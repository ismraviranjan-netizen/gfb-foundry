"""Gradient boosting in numpy: small regression trees, each one fitted to what the trees before it
got wrong. Two losses:
  squared   each tree fits the residual y - F; leaves hold the mean residual
  quantile  each tree fits the pinball gradient (tau - 1[y < F]); leaves hold the tau-quantile of
            the residuals in the leaf (the leaf value that minimises the pinball loss there)
This is the algorithm inside LightGBM and XGBoost with the engineering taken out (histograms, sparse
features, leaf-wise growth, regularisation); on a few hundred rows it is enough to see what they do.
"""
from __future__ import annotations

import numpy as np


class Tree:
    def __init__(self, depth: int = 3, min_leaf: int = 10):
        self.depth, self.min_leaf = depth, min_leaf
        self.nodes: list[dict] = []
        self.gain_by_feature: dict[int, float] = {}

    def _best_split(self, X, r, idx):
        best = None
        n = len(idx)
        rs_all = r[idx]
        tot = rs_all.sum()
        base = tot * tot / n
        for j in range(X.shape[1]):
            x = X[idx, j]
            order = np.argsort(x, kind="mergesort")
            xs, rs = x[order], rs_all[order]
            cs = np.cumsum(rs)[:-1]
            nl = np.arange(1, n)
            nr = n - nl
            ok = (xs[1:] > xs[:-1]) & (nl >= self.min_leaf) & (nr >= self.min_leaf)
            if not ok.any():
                continue
            gain = cs * cs / nl + (tot - cs) ** 2 / nr - base
            gain = np.where(ok, gain, -np.inf)
            k = int(np.argmax(gain))
            if best is None or gain[k] > best[0]:
                best = (float(gain[k]), j, float((xs[k] + xs[k + 1]) / 2.0))
        return best

    def fit(self, X, r, leaf_value):
        """r: what the tree splits on (residual or gradient); leaf_value(idx) -> the leaf's number."""
        self.nodes = []

        def grow(idx, d):
            node = {"leaf": True, "value": 0.0, "n": len(idx)}
            self.nodes.append(node)
            split = self._best_split(X, r, idx) if d < self.depth and len(idx) >= 2 * self.min_leaf else None
            if split is None or split[0] <= 1e-12:
                node["value"] = float(leaf_value(idx))
                return node
            gain, j, thr = split
            self.gain_by_feature[j] = self.gain_by_feature.get(j, 0.0) + gain
            left, right = idx[X[idx, j] <= thr], idx[X[idx, j] > thr]
            node.update({"leaf": False, "feature": j, "threshold": thr})
            node["left"] = grow(left, d + 1)
            node["right"] = grow(right, d + 1)
            return node

        self.root = grow(np.arange(len(r)), 0)
        return self

    def predict(self, X):
        out = np.empty(len(X))
        for i, x in enumerate(X):
            node = self.root
            while not node["leaf"]:
                node = node["left"] if x[node["feature"]] <= node["threshold"] else node["right"]
            out[i] = node["value"]
        return out


class GBM:
    def __init__(self, loss: str = "squared", tau: float = 0.5, n_trees: int = 200, lr: float = 0.05,
                 depth: int = 3, min_leaf: int = 10, subsample: float = 0.8, seed: int = 0):
        assert loss in ("squared", "quantile")
        self.loss, self.tau, self.n_trees, self.lr = loss, tau, n_trees, lr
        self.depth, self.min_leaf, self.subsample, self.seed = depth, min_leaf, subsample, seed
        self.trees: list[Tree] = []
        self.history: list[float] = []

    def _loss(self, y, f):
        if self.loss == "squared":
            return float(np.mean((y - f) ** 2))
        u = y - f
        return float(np.mean(np.where(u >= 0, self.tau * u, (self.tau - 1.0) * u)))

    def fit(self, X, y, X_val=None, y_val=None):
        X, y = np.asarray(X, float), np.asarray(y, float)
        rng = np.random.default_rng(self.seed)
        self.f0 = float(np.mean(y)) if self.loss == "squared" else float(np.quantile(y, self.tau))
        f = np.full(len(y), self.f0)
        fv = np.full(len(y_val), self.f0) if X_val is not None else None
        self.trees, self.history, self.val_history = [], [], []
        for _ in range(self.n_trees):
            take = rng.random(len(y)) < self.subsample
            idx = np.where(take)[0]
            Xs, ys, fs = X[idx], y[idx], f[idx]
            if self.loss == "squared":
                r = ys - fs
                leaf = lambda ii, r=r: r[ii].mean()
            else:
                r = np.where(ys > fs, self.tau, self.tau - 1.0)
                res = ys - fs
                leaf = lambda ii, res=res: np.quantile(res[ii], self.tau)
            t = Tree(self.depth, self.min_leaf).fit(Xs, r, leaf)
            self.trees.append(t)
            f = f + self.lr * t.predict(X)
            self.history.append(self._loss(y, f))
            if fv is not None:
                fv = fv + self.lr * t.predict(np.asarray(X_val, float))
                self.val_history.append(self._loss(np.asarray(y_val, float), fv))
        return self

    def predict(self, X):
        X = np.asarray(X, float)
        f = np.full(len(X), self.f0)
        for t in self.trees:
            f = f + self.lr * t.predict(X)
        return f

    def importance(self, names: list[str]) -> dict[str, float]:
        tot: dict[int, float] = {}
        for t in self.trees:
            for j, g in t.gain_by_feature.items():
                tot[j] = tot.get(j, 0.0) + g
        s = sum(tot.values()) or 1.0
        return {names[j]: round(tot.get(j, 0.0) / s, 3) for j in range(len(names))}
