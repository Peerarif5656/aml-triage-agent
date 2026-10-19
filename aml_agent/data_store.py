"""Read-only access to the case data (customers, transactions, alerts, sanctions, policy).

In production these would be calls to the core banking system, the transaction
monitoring platform, the KYC system and the screening engine. Keeping them behind
one interface means the agent code does not change when the data source does.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

from .config import DATA_DIR


class DataStore:
    def __init__(self, data_dir: Path | str = DATA_DIR):
        self.data_dir = Path(data_dir)
        self.customers = {c["customer_id"]: c for c in self._load("customers.json")}
        self.alerts = {a["alert_id"]: a for a in self._load("alerts.json")}
        self.sanctions = self._load("sanctions_list.json")
        self.labels = self._load("labels.json")
        self.meta = self._load("meta.json")
        self.policy_text = (self.data_dir / "policies" / "aml_policy.md").read_text(encoding="utf-8")

        self._txns_by_customer: dict[str, list[dict]] = {}
        for t in self._load("transactions.json"):
            t = dict(t)
            t["_dt"] = datetime.fromisoformat(t["timestamp"])
            self._txns_by_customer.setdefault(t["customer_id"], []).append(t)
        for rows in self._txns_by_customer.values():
            rows.sort(key=lambda r: r["_dt"])
        self.txn_index = {t["txn_id"]: t for rows in self._txns_by_customer.values() for t in rows}

        digest = hashlib.sha256()
        for name in ["customers.json", "transactions.json", "alerts.json", "sanctions_list.json"]:
            digest.update((self.data_dir / name).read_bytes())
        self.data_version = digest.hexdigest()[:12]

    def _load(self, name: str):
        return json.loads((self.data_dir / name).read_text(encoding="utf-8"))

    # ------------------------------------------------------------------ queries
    def get_alert(self, alert_id: str) -> dict | None:
        return self.alerts.get(alert_id)

    def get_customer(self, customer_id: str) -> dict | None:
        return self.customers.get(customer_id)

    def transactions(self, customer_id: str, start: datetime | None = None,
                     end: datetime | None = None) -> list[dict]:
        rows = self._txns_by_customer.get(customer_id, [])
        return [r for r in rows if (start is None or r["_dt"] >= start) and (end is None or r["_dt"] <= end)]

    def label(self, alert_id: str) -> str | None:
        entry = self.labels.get(alert_id)
        return entry["disposition"] if entry else None

    def alert_list(self) -> list[dict]:
        return sorted(self.alerts.values(), key=lambda a: a["alert_id"])


def public_txn(t: dict) -> dict:
    """Strip internal fields before a transaction leaves the data layer."""
    return {k: v for k, v in t.items() if not k.startswith("_")}
