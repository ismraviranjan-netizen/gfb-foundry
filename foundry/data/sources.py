"""Where the tables come from (Part 6). One function, three kinds of place:

    canon:                       the generated incident week (demos, tests, the fake brain)
    file:///path or /path        CSVs on disk, plus network.yaml beside them if the company is not GFB
    s3://bucket/prefix/          the same CSVs in S3, copied to /tmp on a cold start

The handler reads FOUNDRY_DATA_URI; the CLI takes --data. Nothing else in the repo knows where
the data lives, which is the point.
"""
from __future__ import annotations

import os
from pathlib import Path

from foundry.network import GFB, Network
from foundry.store import TABLES, Store


def load_store(uri: str | None = None, net: Network | None = None) -> Store:
    uri = uri or os.environ.get("FOUNDRY_DATA_URI", "canon:")
    if uri.startswith("canon:"):
        from foundry.data.generate import canonical_store, sensing_history
        return sensing_history(net=net) if uri == "canon:sensing" else canonical_store()
    if uri.startswith("s3://"):
        folder = _fetch_s3(uri)
    else:
        folder = Path(uri.replace("file://", ""))
    net = net or _network_beside(folder)
    return Store.from_csv(folder, net=net)


def _network_beside(folder: Path) -> Network:
    y = folder / "network.yaml"
    return Network.from_yaml(y) if y.exists() else GFB


def _fetch_s3(uri: str) -> Path:
    import boto3  # lazy: tests never need it
    bucket, _, prefix = uri[5:].partition("/")
    prefix = prefix.rstrip("/")
    local = Path(os.environ.get("FOUNDRY_TMP", "/tmp")) / "foundry-data" / bucket / prefix.replace("/", "_")
    local.mkdir(parents=True, exist_ok=True)
    s3 = boto3.client("s3")
    names = [f"{t}.csv" for t in TABLES] + ["network.yaml"]
    listing = s3.list_objects_v2(Bucket=bucket, Prefix=prefix + "/")
    for obj in listing.get("Contents", []):
        name = obj["Key"].rsplit("/", 1)[-1]
        if name in names or name.endswith(".csv"):
            s3.download_file(bucket, obj["Key"], str(local / name))
    return local
