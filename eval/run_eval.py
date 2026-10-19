"""Evaluate the triage agent against analyst-labelled alerts.

Usage (from the repo root):
    python -m eval.run_eval                      # offline rules investigator
    python -m eval.run_eval --mode llm           # LLM agent (needs GROQ_API_KEY)
    python -m eval.run_eval --mode llm --model openai/gpt-oss-120b --sleep 20

Metrics reported:
- accuracy and per-class precision / recall against the labelled disposition
- escalation recall: the share of truly suspicious cases the agent escalated
  (the costliest error in AML is closing something that should have been escalated)
- missed escalations: labelled ESCALATE but recommended CLOSE
- grounding rate: share of cited identifiers that a tool actually returned
- control activity: overrides, dual-control disagreements, fallbacks
- cost and speed: tool calls, LLM calls, tokens, latency
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aml_agent import config as C  # noqa: E402
from aml_agent.agent import TriageAgent  # noqa: E402
from aml_agent.data_store import DataStore  # noqa: E402

RESULTS_DIR = Path(__file__).resolve().parent / "results"


def evaluate(agent: TriageAgent, store: DataStore, alert_ids: list[str] | None = None, sleep: float = 0.0,
             progress=None) -> dict:
    alert_ids = alert_ids or [a["alert_id"] for a in store.alert_list()]
    rows = []
    for i, alert_id in enumerate(alert_ids):
        if i and sleep:
            time.sleep(sleep)
        r = agent.run(alert_id)
        rows.append({
            "alert_id": alert_id,
            "customer": r.customer_name,
            "label": r.ground_truth,
            "predicted": r.decision["disposition"],
            "proposed_before_controls": r.proposed_decision["disposition"],
            "rules_engine": r.rules["disposition"],
            "risk_score": r.decision["risk_score"],
            "correct": r.decision["disposition"] == r.ground_truth,
            "mode_used": r.mode,
            "controls": [c["control"] for c in r.controls],
            "senior_review": r.requires_senior_review,
            "grounding_cited": r.grounding["cited"],
            "grounding_ok": r.grounding["grounded"],
            "tool_calls": r.metrics["tool_calls"],
            "llm_calls": r.metrics.get("llm_calls", 0),
            "tokens": r.metrics.get("prompt_tokens", 0) + r.metrics.get("completion_tokens", 0),
            "latency_ms": r.metrics["latency_ms"],
            "fallback_reason": r.fallback_reason,
        })
        if progress:
            progress(i + 1, len(alert_ids), rows[-1])
    return summarize(rows, agent)


def summarize(rows: list[dict], agent: TriageAgent) -> dict:
    n = len(rows)
    labels = C.DISPOSITIONS
    confusion = {t: {p: 0 for p in labels} for t in labels}
    for r in rows:
        confusion[r["label"]][r["predicted"]] += 1
    per_class = {}
    for c in labels:
        tp = confusion[c][c]
        fp = sum(confusion[t][c] for t in labels if t != c)
        fn = sum(confusion[c][p] for p in labels if p != c)
        prec = tp / (tp + fp) if tp + fp else None
        rec = tp / (tp + fn) if tp + fn else None
        f1 = 2 * prec * rec / (prec + rec) if prec and rec else 0.0
        per_class[c] = {"precision": prec, "recall": rec, "f1": round(f1, 3), "support": tp + fn}
    cited = sum(r["grounding_cited"] for r in rows)
    grounded = sum(r["grounding_ok"] for r in rows)
    return {
        "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mode": agent.mode,
        "model": getattr(agent.llm, "model", "rules-engine") if agent.mode == "llm" else "rules-engine",
        "prompt_version": C.PROMPT_VERSION,
        "cases": n,
        "accuracy": round(sum(r["correct"] for r in rows) / n, 3),
        "escalation_recall": per_class["ESCALATE"]["recall"],
        "missed_escalations": confusion["ESCALATE"]["CLOSE"],
        "macro_f1": round(sum(v["f1"] for v in per_class.values()) / len(labels), 3),
        "per_class": per_class,
        "confusion": confusion,
        "grounding_rate": round(grounded / cited, 3) if cited else 1.0,
        "control_overrides": sum(r["proposed_before_controls"] != r["predicted"] for r in rows),
        "dual_control_disagreements": sum("CTRL-DUAL" in r["controls"] for r in rows),
        "fallbacks": sum(r["fallback_reason"] is not None for r in rows),
        "senior_reviews": sum(r["senior_review"] for r in rows),
        "avg_tool_calls": round(sum(r["tool_calls"] for r in rows) / n, 1),
        "avg_llm_calls": round(sum(r["llm_calls"] for r in rows) / n, 1),
        "avg_tokens": round(sum(r["tokens"] for r in rows) / n),
        "avg_latency_ms": round(sum(r["latency_ms"] for r in rows) / n),
        "rows": rows,
    }


def to_markdown(s: dict) -> str:
    def pct(x):
        return "n/a" if x is None else f"{x:.0%}"
    lines = [
        f"# Evaluation report: {s['mode']} ({s['model']})",
        "",
        f"Run at {s['run_at']} | prompt {s['prompt_version']} | {s['cases']} labelled alerts",
        "",
        "| Metric | Value |", "|---|---|",
        f"| Accuracy | {pct(s['accuracy'])} |",
        f"| Escalation recall | {pct(s['escalation_recall'])} |",
        f"| Missed escalations (ESCALATE labelled, CLOSE recommended) | {s['missed_escalations']} |",
        f"| Macro F1 | {s['macro_f1']} |",
        f"| Grounding rate (cited IDs returned by a tool) | {pct(s['grounding_rate'])} |",
        f"| Recommendations changed by controls | {s['control_overrides']} |",
        f"| LLM vs rules engine disagreements | {s['dual_control_disagreements']} |",
        f"| Fallbacks to rules investigator | {s['fallbacks']} |",
        f"| Avg tool calls / LLM calls / tokens | {s['avg_tool_calls']} / {s['avg_llm_calls']} / {s['avg_tokens']} |",
        f"| Avg latency | {s['avg_latency_ms']} ms |",
        "",
        "## Confusion matrix (rows = label, columns = recommendation)",
        "",
        "| | " + " | ".join(C.DISPOSITIONS) + " |",
        "|---|" + "---|" * len(C.DISPOSITIONS),
    ]
    for t in C.DISPOSITIONS:
        lines.append(f"| **{t}** | " + " | ".join(str(s["confusion"][t][p]) for p in C.DISPOSITIONS) + " |")
    lines += ["", "## Per-alert results", "",
              "| Alert | Customer | Label | Recommended | Rules engine | Score | Controls | Correct |",
              "|---|---|---|---|---|---|---|---|"]
    for r in s["rows"]:
        lines.append(f"| {r['alert_id']} | {r['customer']} | {r['label']} | {r['predicted']} | {r['rules_engine']} | "
                     f"{r['risk_score']} | {', '.join(r['controls']) or '-'} | {'yes' if r['correct'] else 'NO'} |")
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mode", choices=["rules", "llm"], default="rules")
    parser.add_argument("--model", default=C.DEFAULT_MODEL)
    parser.add_argument("--sleep", type=float, default=0.0, help="Seconds between cases (free-tier rate limits)")
    parser.add_argument("--alerts", nargs="*", help="Subset of alert IDs")
    args = parser.parse_args()

    store = DataStore()
    llm = None
    if args.mode == "llm":
        from aml_agent.llm import GroqClient
        key = os.getenv("GROQ_API_KEY")
        if not key:
            sys.exit("Set GROQ_API_KEY to evaluate the LLM agent.")
        llm = GroqClient(key, model=args.model)
    agent = TriageAgent(store, llm=llm, mode=args.mode)

    def progress(i, n, row):
        mark = "ok " if row["correct"] else "XX "
        print(f"[{i:>2}/{n}] {mark}{row['alert_id']}  label={row['label']:<8} pred={row['predicted']:<8} "
              f"controls={','.join(row['controls']) or '-'}")

    summary = evaluate(agent, store, args.alerts, args.sleep, progress)
    RESULTS_DIR.mkdir(exist_ok=True)
    stem = f"{args.mode}" + (f"_{args.model.replace('/', '-')}" if args.mode == "llm" else "")
    (RESULTS_DIR / f"{stem}.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (RESULTS_DIR / f"{stem}.md").write_text(to_markdown(summary), encoding="utf-8")
    print(f"\nAccuracy {summary['accuracy']:.0%} | escalation recall {summary['escalation_recall']:.0%} | "
          f"missed escalations {summary['missed_escalations']} | grounding {summary['grounding_rate']:.0%}")
    print(f"Report written to {RESULTS_DIR / (stem + '.md')}")


if __name__ == "__main__":
    main()
