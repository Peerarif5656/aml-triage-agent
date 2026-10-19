"""Tools the investigator agent can call, with JSON schemas for LLM function calling.

Design points worth noting:
- Case scoping: tools only return data for the customer under investigation
  (data minimisation; the model cannot browse other customers).
- Every call is written to the audit trail with its arguments and output.
- Every identifier the tools return is collected, so the controls layer can
  check that the model only cites evidence it actually saw (grounding check).
"""
from __future__ import annotations

import json
import re
import time
from collections import Counter
from datetime import timedelta

from .audit import AuditTrail
from .data_store import DataStore, public_txn
from .retrieval import PolicyIndex
from .screening import screen_case
from .typologies import DETECTORS, CaseContext, run_detectors

ID_PATTERN = re.compile(r"\b(?:TX-\d{3}-\d{4}|SAN-\d{4}|DOC-\d{4}|POL-[A-Z]+-\d{2}|ALT-\d{4})\b")


def _fn(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": properties, "required": required}}}


TOOL_SCHEMAS = [
    _fn("get_alert", "Get the alert under investigation: rule triggered, date, summary and triggering transaction IDs.",
        {"alert_id": {"type": "string", "description": "Alert ID, e.g. ALT-1001"}}, ["alert_id"]),
    _fn("get_customer_profile",
        "Get the KYC profile: identity, occupation or business activity, declared income/turnover, expected "
        "monthly inflow, risk rating, PEP status, EDD date, known counterparties, expected events, recent profile "
        "changes and documents on file.",
        {"customer_id": {"type": "string", "description": "Customer ID, e.g. C101"}}, ["customer_id"]),
    _fn("get_transactions",
        "List the customer's transactions up to the alert date with a summary. Filter to keep results focused.",
        {"customer_id": {"type": "string"},
         "days": {"type": "integer", "description": "Look-back window in days (1-365). Default 30."},
         "direction": {"type": "string", "enum": ["IN", "OUT"], "description": "Optional: only credits or debits."},
         "min_amount": {"type": "number", "description": "Optional: only transactions at or above this GBP amount."},
         "limit": {"type": "integer", "description": "Max rows returned (1-25). Default 15; largest amounts kept."}},
        ["customer_id"]),
    _fn("detect_typologies",
        "Run the bank's deterministic AML pattern detectors (structuring, pass-through, circular flows, dormancy, "
        "account takeover, PEP funds, velocity, large inflows, higher-risk jurisdictions, unapproved beneficiaries, "
        "funnel accounts, income shifts). Returns findings with evidence IDs and whether documented information "
        "mitigates each one.",
        {"customer_id": {"type": "string"}}, ["customer_id"]),
    _fn("screen_sanctions",
        "Screen the customer and every counterparty in the last 90 days against the sanctions list. Each hit has "
        "a name score, secondary identifier comparison and strength: STRONG, POSSIBLE or WEAK.",
        {"customer_id": {"type": "string"}}, ["customer_id"]),
    _fn("search_policy",
        "Search the bank's AML and sanctions policy. Returns the most relevant sections with their POL-... IDs.",
        {"query": {"type": "string", "description": "What you need guidance on, e.g. 'cash structuring threshold'"},
         "k": {"type": "integer", "description": "Number of sections to return (1-5). Default 2."}},
        ["query"]),
]

# Compact tool set for models that call one tool per turn (e.g. gpt-oss on Groq). Each turn re-sends the
# whole conversation, so bundling the case file into one call cuts round trips and tokens by more than half,
# which keeps an investigation inside a free tier's tokens-per-minute limit.
OPEN_CASE_SCHEMA = _fn(
    "open_case",
    "Open the case file in one call: the alert (rule, date, summary, triggering transaction IDs), the customer's "
    "KYC profile (expected activity, PEP status, EDD date, known counterparties, expected events, documents), the "
    "bank's typology detector findings (with evidence IDs and any documented mitigation) and sanctions screening "
    "of the customer and all counterparties (hits graded STRONG, POSSIBLE or WEAK). Call this first.",
    {"alert_id": {"type": "string", "description": "Alert ID, e.g. ALT-1001"}}, ["alert_id"])
_BY_NAME = {t["function"]["name"]: t for t in TOOL_SCHEMAS}
COMPACT_TOOL_SCHEMAS = [OPEN_CASE_SCHEMA, _BY_NAME["get_transactions"], _BY_NAME["search_policy"]]
TOOL_NAMES = [t["function"]["name"] for t in TOOL_SCHEMAS] + ["open_case"]


def compact_txn(t: dict) -> dict:
    p = public_txn(t)
    return {"txn_id": p["txn_id"], "timestamp": p["timestamp"].replace("T", " "), "direction": p["direction"],
            "amount": p["amount"], "channel": p["channel"], "counterparty": p["counterparty"],
            "country": p["counterparty_country"], "cp_type": p["counterparty_type"], "reference": p["reference"]}


class ScopeError(Exception):
    pass


class ToolKit:
    def __init__(self, store: DataStore, policy: PolicyIndex, audit: AuditTrail):
        self.store = store
        self.policy = policy
        self.audit = audit
        self.ctx: CaseContext | None = None
        self.seen_ids: set[str] = set()
        self.calls = 0
        self._registry = {
            "get_alert": self.get_alert,
            "get_customer_profile": self.get_customer_profile,
            "get_transactions": self.get_transactions,
            "detect_typologies": self.detect_typologies,
            "screen_sanctions": self.screen_sanctions,
            "search_policy": self.search_policy,
            "open_case": self.open_case,
        }

    # ------------------------------------------------------------------ plumbing
    def begin_case(self, alert: dict) -> None:
        self.ctx = CaseContext(self.store, alert)

    def _scope(self, customer_id: str) -> None:
        if customer_id != self.ctx.customer["customer_id"]:
            raise ScopeError(f"Access denied: customer {customer_id} is outside the scope of this case "
                             f"({self.ctx.customer['customer_id']}).")

    def execute(self, name: str, arguments) -> dict:
        t0 = time.perf_counter()
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments or "{}")
            except json.JSONDecodeError as e:
                arguments = {}
                result = {"error": f"Arguments were not valid JSON: {e}"}
                return self._record(name, arguments, result, t0)
        fn = self._registry.get(name)
        if fn is None:
            result = {"error": f"Unknown tool '{name}'. Available: {', '.join(TOOL_NAMES)}"}
        else:
            try:
                result = fn(**(arguments or {}))
            except ScopeError as e:
                result = {"error": str(e)}
            except TypeError as e:
                result = {"error": f"Invalid arguments for {name}: {e}"}
        return self._record(name, arguments, result, t0)

    def _record(self, name, arguments, result, t0) -> dict:
        self.calls += 1
        text = json.dumps(result, default=str)
        self.seen_ids.update(ID_PATTERN.findall(text))
        self.audit.log("TOOL", name, {"arguments": arguments, "summary": self._summary(name, result),
                                      "output": result}, (time.perf_counter() - t0) * 1000)
        return result

    @staticmethod
    def _summary(name: str, r: dict) -> str:
        if "error" in r:
            return f"ERROR: {r['error']}"
        if name == "open_case":
            found = ", ".join(f"{f['typology']}{' (mitigated)' if f['mitigated'] else ''}"
                              for f in r["detector_findings"]) or "no typologies"
            return (f"{r['alert']['rule_id']} · {r['customer_profile']['name']} · findings: {found} · "
                    f"sanctions: {len(r['sanctions_screening']['hits'])} hit(s)")
        if name == "get_alert":
            return f"{r['rule_id']}: {r['rule_name']}"
        if name == "get_customer_profile":
            return f"{r['name']} ({r['customer_type']}), risk {r['risk_rating']}, PEP={r['pep']}"
        if name == "get_transactions":
            return f"{r['summary']['count']} transactions, showing {len(r['transactions'])}"
        if name == "detect_typologies":
            return ", ".join(f"{f['typology']}{' (mitigated)' if f['mitigated'] else ''}" for f in r["findings"]) \
                or "no typologies detected"
        if name == "screen_sanctions":
            return r["summary"]
        if name == "search_policy":
            return ", ".join(s["policy_id"] for s in r["results"]) or "no results"
        return "ok"

    # ------------------------------------------------------------------ tools
    def get_alert(self, alert_id: str) -> dict:
        alert = self.store.get_alert(alert_id)
        if alert is None:
            return {"error": f"Alert {alert_id} not found"}
        if alert_id != self.ctx.alert["alert_id"]:
            raise ScopeError(f"Access denied: only alert {self.ctx.alert['alert_id']} is in scope.")
        customer = self.store.get_customer(alert["customer_id"])
        return {**alert, "customer_name": customer["name"]}

    def get_customer_profile(self, customer_id: str) -> dict:
        self._scope(customer_id)
        return dict(self.store.get_customer(customer_id))

    def get_transactions(self, customer_id: str, days: int = 30, direction: str | None = None,
                         min_amount: float = 0, limit: int = 15) -> dict:
        self._scope(customer_id)
        days = max(1, min(int(days), 365))
        limit = max(1, min(int(limit), 25))
        start = self.ctx.as_of - timedelta(days=days)
        rows = self.store.transactions(customer_id, start, self.ctx.as_of)
        if direction in {"IN", "OUT"}:
            rows = [r for r in rows if r["direction"] == direction]
        if min_amount:
            rows = [r for r in rows if r["amount"] >= float(min_amount)]
        shown = sorted(sorted(rows, key=lambda r: -r["amount"])[:limit], key=lambda r: r["_dt"])
        return {
            "customer_id": customer_id,
            "window": {"from": start.date().isoformat(), "to": self.ctx.as_of.date().isoformat()},
            "filters": {"direction": direction, "min_amount": min_amount},
            "summary": {
                "count": len(rows),
                "total_in": round(sum(r["amount"] for r in rows if r["direction"] == "IN"), 2),
                "total_out": round(sum(r["amount"] for r in rows if r["direction"] == "OUT"), 2),
                "by_channel": dict(Counter(r["channel"] for r in rows)),
                "distinct_counterparties": len({r["counterparty"] for r in rows}),
            },
            "note": (f"Showing the {len(shown)} largest of {len(rows)} transactions, in date order."
                     if len(rows) > len(shown) else f"Showing all {len(rows)} transactions."),
            "transactions": [compact_txn(r) for r in shown],
        }

    def detect_typologies(self, customer_id: str) -> dict:
        self._scope(customer_id)
        findings = run_detectors(self.ctx)
        return {"customer_id": customer_id, "as_of": self.ctx.as_of.date().isoformat(),
                "detectors_run": len(DETECTORS),
                "findings": [f.to_dict() for f in findings]}

    def screen_sanctions(self, customer_id: str) -> dict:
        self._scope(customer_id)
        result = screen_case(self.store, customer_id, self.ctx.as_of)
        keep = ["sanctions_id", "listed_name", "matched_alias", "name_score", "listed_type", "listed_dob",
                "listed_nationality", "listed_country", "programme", "party_role", "screened_name",
                "secondary_identifiers", "strength", "txn_ids"]
        result["hits"] = [{k: h[k] for k in keep} for h in result["hits"]]
        return result

    def open_case(self, alert_id: str) -> dict:
        alert = self.get_alert(alert_id)
        if "error" in alert:
            return alert
        cid = alert["customer_id"]
        findings = [{k: f[k] for k in ("typology", "title", "description", "mitigated", "mitigation",
                                       "mitigation_evidence", "policy_ref")} | {"evidence": f["evidence"][:12]}
                    for f in self.detect_typologies(cid)["findings"]]
        screening = self.screen_sanctions(cid)
        hits = [{k: h[k] for k in ("sanctions_id", "listed_name", "screened_name", "party_role", "name_score",
                                   "listed_dob", "listed_nationality", "listed_country", "secondary_identifiers",
                                   "strength", "txn_ids")} for h in screening["hits"]]
        return {
            "alert": {k: alert[k] for k in ("alert_id", "alert_date", "rule_id", "rule_name", "priority",
                                            "trigger_summary", "trigger_txn_ids")},
            "customer_profile": self.get_customer_profile(cid),
            "detector_findings": findings,
            "sanctions_screening": {"summary": screening["summary"], "parties_screened": screening["parties_screened"],
                                    "potential_true_match": screening["potential_true_match"], "hits": hits},
        }

    def search_policy(self, query: str, k: int = 2) -> dict:
        k = max(1, min(int(k), 5))
        return {"query": query, "results": self.policy.search(query, k)}
