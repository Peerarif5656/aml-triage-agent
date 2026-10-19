"""Build the self-contained web demo (demo/index.html) from demo/template.html.

The web demo has no Python backend, so this script runs the Python engine once and
embeds its outputs: the dataset, the deterministic tool outputs (KYC profile,
typology detectors, sanctions screening), the rules engine assessment, the offline
investigator's full run, and the evaluation summary. The browser then runs
get_transactions, BM25 policy search and the controls itself, and in the Claude
viewer it can run a live tool-calling investigation on Claude over the same tools.

Run from the repo root:
    python scripts/build_demo.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from aml_agent import config as C  # noqa: E402
from aml_agent.agent import TriageAgent  # noqa: E402
from aml_agent.audit import AuditTrail  # noqa: E402
from aml_agent.data_store import DataStore, public_txn  # noqa: E402
from aml_agent.prompts import SYSTEM_PROMPT, investigation_request  # noqa: E402
from aml_agent.retrieval import PolicyIndex  # noqa: E402
from aml_agent.tools import TOOL_SCHEMAS, ToolKit  # noqa: E402
from eval.run_eval import evaluate  # noqa: E402

TEMPLATE = ROOT / "demo" / "template.html"
OUT = ROOT / "demo" / "index.html"
MARKER = "/*__DEMO_DATA__*/null"


def trace_line(e: dict) -> dict:
    d = e["detail"]
    if isinstance(d, dict):
        summary = d.get("summary") or d.get("message")
        if summary is None and "score" in d:
            summary = f"score {d['score']} → {d['disposition']}"
        if summary is None and "disposition" in d:
            summary = f"{d['disposition']} · risk {d.get('risk_score', '')}"
        if summary is None and "customer" in d:
            summary = f"{d['customer']} · {d.get('rule', '')}"
    else:
        summary = d
    return {"ms": e["elapsed_ms"], "actor": e["actor"], "action": e["action"], "summary": summary or ""}


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Build demo/index.html")
    parser.add_argument("--repo-url", default="", help="GitHub URL shown in the demo footer")
    args = parser.parse_args()
    store = DataStore()
    policy = PolicyIndex(store.policy_text)
    agent = TriageAgent(store, mode="rules")
    cases = {}
    for alert in store.alert_list():
        aid, cid = alert["alert_id"], alert["customer_id"]
        tk = ToolKit(store, policy, AuditTrail(aid, "export", None, C.PROMPT_VERSION, store.data_version))
        tk.begin_case(alert)
        outputs = {
            "get_alert": tk.execute("get_alert", {"alert_id": aid}),
            "get_customer_profile": tk.execute("get_customer_profile", {"customer_id": cid}),
            "detect_typologies": tk.execute("detect_typologies", {"customer_id": cid}),
            "screen_sanctions": tk.execute("screen_sanctions", {"customer_id": cid}),
        }
        r = agent.run(aid)
        cases[aid] = {
            "tool_outputs": outputs,
            "rules": r.rules,
            "offline": {
                "decision": r.decision, "controls": r.controls, "grounding": r.grounding, "metrics": r.metrics,
                "run_id": r.run_id, "trace": [trace_line(e) for e in r.audit.events],
            },
        }

    txns: dict[str, list] = {}
    for t in store.txn_index.values():
        p = public_txn(t)
        txns.setdefault(p["customer_id"], []).append(
            [p["txn_id"], p["timestamp"], p["direction"], p["amount"], p["channel"], p["counterparty"],
             p["counterparty_country"], p["counterparty_type"], p["reference"]])
    for rows in txns.values():
        rows.sort(key=lambda r: r[1])

    ev = evaluate(TriageAgent(store, mode="rules"), store)
    data = {
        "meta": {"data_version": store.data_version, "prompt_version": C.PROMPT_VERSION,
                 "jurisdictions": C.HIGH_RISK_JURISDICTIONS,
                 "thresholds": {"ESCALATE": C.ESCALATE_THRESHOLD, "RFI": C.RFI_THRESHOLD},
                 "repo_url": args.repo_url},
        "alerts": [{**a, "customer_name": store.get_customer(a["customer_id"])["name"]} for a in store.alert_list()],
        "customers": store.customers,
        "labels": store.labels,
        "txns": txns,
        "policy": policy.sections,
        "cases": cases,
        "eval": {k: v for k, v in ev.items() if k != "run_at"},
        "prompt": SYSTEM_PROMPT,
        "request_template": investigation_request("{ALERT_ID}"),
        "tool_schemas": TOOL_SCHEMAS,
    }
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    html = TEMPLATE.read_text(encoding="utf-8")
    if MARKER not in html:
        sys.exit("Template is missing the data marker")
    OUT.write_text(html.replace(MARKER, payload), encoding="utf-8")
    print(f"Wrote {OUT} ({OUT.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
