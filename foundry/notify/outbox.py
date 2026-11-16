"""The outbox: every message the tower sends, written down before it leaves.

A message is QUEUED, then SENT by the channel. Locally the channel is the file itself (the console
shows it); on AWS, set FOUNDRY_SNS_TOPIC and each email-channel message is also published to the SNS
topic the Terraform creates (subscribers confirm by email). Addresses are made up: gfb.example.
SMS goes only to P1 items and only if the profile lists the channel (tower.notify_channels).
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path


def name_of(approver: str) -> str:
    """'Priya Nambiar (dc_manager)' -> 'Priya Nambiar'."""
    return approver.split(" (")[0].strip()


def address_of(approver: str, channel: str = "email", domain: str = "gfb.example") -> str:
    slug = re.sub(r"[^a-z0-9.]", "", name_of(approver).lower().replace(" ", "."))
    return f"{slug}@{domain}" if channel == "email" else f"sms:{name_of(approver)}"


class Outbox:
    def __init__(self, path: str | Path | None = None, channels: tuple[str, ...] | list[str] = ("email",), domain: str = "gfb.example"):
        self.path = Path(path or os.environ.get("FOUNDRY_OUTBOX", "runs/outbox.jsonl"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.channels = list(channels)
        self.domain = domain
        self.items: list[dict] = []
        self._sns = None

    def send(self, to: str, subject: str, body: str, kind: str, refs: list[str] | None = None, at: float = 0.0,
             priority: str | None = None) -> list[dict]:
        out = []
        for ch in self.channels:
            if ch == "sms" and priority != "P1":
                continue
            msg = {"id": f"MSG-{len(self.items) + 1:04d}", "at": at, "to": name_of(to), "address": address_of(to, ch, self.domain),
                   "channel": ch, "kind": kind, "subject": subject, "body": body if ch == "email" else _sms(subject, refs),
                   "refs": refs or [], "priority": priority, "status": "QUEUED", "ts": time.time()}
            self._deliver(msg)
            self.items.append(msg)
            with open(self.path, "a") as f:
                f.write(json.dumps(msg) + "\n")
            out.append(msg)
        return out

    def _deliver(self, msg: dict) -> None:
        topic = os.environ.get("FOUNDRY_SNS_TOPIC")
        if topic and msg["channel"] == "email":
            try:
                if self._sns is None:
                    import boto3  # only on AWS
                    self._sns = boto3.client("sns")
                self._sns.publish(TopicArn=topic, Subject=msg["subject"][:99], Message=msg["body"],
                                  MessageAttributes={"to": {"DataType": "String", "StringValue": msg["to"]}})
                msg["status"] = "SENT"
            except Exception as e:          # the outbox keeps the message; the console shows it either way
                msg["status"] = f"FAILED: {type(e).__name__}"
        else:
            msg["status"] = "SENT"           # locally the outbox file is the channel

    def messages(self, to: str | None = None, kind: str | None = None) -> list[dict]:
        return [m for m in self.items if (to is None or m["to"] == name_of(to)) and (kind is None or m["kind"] == kind)]


def _sms(subject: str, refs: list[str] | None) -> str:
    return f"{subject[:120]}. Reply APPROVE or REJECT with the plan id: {', '.join(refs or [])}"[:300]
