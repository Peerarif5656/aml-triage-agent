"""The triage agent: runs an investigation for one alert and returns a recommendation.

Two investigators share the same tools, controls and output format:
- LLM investigator: a tool-calling loop on Groq. The model decides which tools to
  call, reads the results and writes the decision and narrative.
- Rules investigator: a fixed plan over the same tools, scored by the rules engine.
  Used offline, and as the automatic fallback if the LLM is unavailable.

Whatever the investigator, the rules engine also scores every case independently
and the controls layer checks the recommendation before an analyst sees it.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from . import config as C
from .audit import AuditTrail
from .controls import apply_controls, normalize_decision
from .data_store import DataStore
from .llm import LLMError, extract_json
from .narrative import POLICY_QUERIES, build_rules_decision
from .prompts import FINALISE_REQUEST, REPAIR_REQUEST, SYSTEM_PROMPT, SYSTEM_PROMPT_COMPACT, investigation_request
from .retrieval import PolicyIndex
from .rules import score_case
from .screening import screen_case
from .tools import COMPACT_TOOL_SCHEMAS, TOOL_SCHEMAS, ToolKit
from .typologies import CaseContext, run_detectors


@dataclass
class CaseResult:
    run_id: str
    alert_id: str
    customer_id: str
    customer_name: str
    mode: str                     # mode actually used: "llm" or "rules"
    requested_mode: str
    model: str
    decision: dict                # final recommendation after controls
    proposed_decision: dict       # what the investigator proposed before controls
    rules: dict                   # independent rules engine assessment
    findings: list[dict]
    screening: dict
    controls: list[dict]
    requires_senior_review: bool
    grounding: dict
    metrics: dict
    audit: AuditTrail
    status: str = "PENDING_ANALYST_REVIEW"
    fallback_reason: str | None = None
    analyst_decision: dict | None = None
    ground_truth: str | None = field(default=None)

    def to_dict(self) -> dict:
        out = {k: v for k, v in self.__dict__.items() if k != "audit"}
        out["audit"] = self.audit.to_dict()
        return out


class TriageAgent:
    def __init__(self, store: DataStore, llm=None, mode: str = "rules", max_steps: int = C.MAX_AGENT_STEPS,
                 toolset: str = "compact"):
        if mode not in {"llm", "rules"}:
            raise ValueError("mode must be 'llm' or 'rules'")
        if mode == "llm" and llm is None:
            raise ValueError("LLM mode needs an LLM client")
        if toolset not in {"compact", "granular"}:
            raise ValueError("toolset must be 'compact' or 'granular'")
        self.store = store
        self.llm = llm
        self.mode = mode
        self.max_steps = max_steps
        # compact: open_case + get_transactions + search_policy (fewer round trips; default for Groq)
        # granular: six separate tools (suits models that call several tools per turn)
        self.tool_schemas = COMPACT_TOOL_SCHEMAS if toolset == "compact" else TOOL_SCHEMAS
        self.system_prompt = SYSTEM_PROMPT_COMPACT if toolset == "compact" else SYSTEM_PROMPT
        self.policy = PolicyIndex(store.policy_text)

    # ------------------------------------------------------------------ public
    def run(self, alert_id: str, on_event=None) -> CaseResult:
        alert = self.store.get_alert(alert_id)
        if alert is None:
            raise KeyError(f"Unknown alert {alert_id}")
        model = getattr(self.llm, "model", "llm") if self.mode == "llm" else "rules-engine"
        audit = AuditTrail(alert_id, self.mode, model, C.PROMPT_VERSION, self.store.data_version, on_event)
        customer = self.store.get_customer(alert["customer_id"])
        audit.log("SYSTEM", "Investigation started",
                  {"alert_id": alert_id, "rule": alert["rule_id"], "customer": customer["name"], "mode": self.mode,
                   "model": model})
        t0 = time.perf_counter()
        toolkit = ToolKit(self.store, self.policy, audit)
        toolkit.begin_case(alert)

        # Independent second line: deterministic detectors, screening and rules score.
        ctx = CaseContext(self.store, alert)
        findings = run_detectors(ctx)
        screening = screen_case(self.store, customer["customer_id"], ctx.as_of)
        rules = score_case(findings, screening)
        audit.log("CONTROL", "Independent rules engine assessment",
                  {"score": rules["score"], "disposition": rules["disposition"],
                   "drivers": [f"{d['typology']} (+{d['points']})" for d in rules["drivers"]]})

        usage = {"prompt_tokens": 0, "completion_tokens": 0, "llm_calls": 0}
        used_mode, fallback, proposed = self.mode, None, None
        if self.mode == "llm":
            switches_before = len(getattr(self.llm, "model_switches", []))
            if hasattr(self.llm, "on_retry"):  # make provider waits visible in the live trace
                self.llm.on_retry = lambda wait, reason: audit.log(
                    "SYSTEM", f"Waiting {wait:.0f}s for the LLM provider ({reason})", {"wait_s": round(wait, 1)})
            try:
                proposed = self._investigate_with_llm(alert_id, toolkit, audit, usage)
                if proposed is None:
                    fallback = "The LLM did not return a valid decision object (CTRL-SCHEMA)."
            except LLMError as e:
                fallback = f"LLM unavailable: {e}"
            finally:
                if hasattr(self.llm, "on_retry"):
                    self.llm.on_retry = None
            for sw in getattr(self.llm, "model_switches", [])[switches_before:]:
                audit.log("SYSTEM", f"Model {sw['from']} is not available; switched to {sw['to']}", sw)
            model = getattr(self.llm, "model", model)
            audit.model = model
            if fallback:
                audit.log("SYSTEM", "Falling back to rules investigator", fallback)
                used_mode = "rules"
        if proposed is None:
            proposed = self._investigate_with_rules(ctx, toolkit, findings, screening, rules)
            audit.log("AGENT", "Rules investigator proposed a decision",
                      {"disposition": proposed["disposition"], "risk_score": proposed["risk_score"]})

        final, flags, senior, grounding = apply_controls(proposed, rules, toolkit.seen_ids, used_mode)
        if fallback:
            flags.insert(0, {"control": "CTRL-FALLBACK", "severity": "WARN",
                             "message": f"{fallback} The rules investigator produced this recommendation."})
        for f in flags:
            audit.log("CONTROL", f["control"], f)
        audit.log("AGENT", "Recommendation ready for analyst review",
                  {"disposition": final["disposition"], "risk_score": final["risk_score"],
                   "senior_review": senior})

        return CaseResult(
            run_id=audit.run_id, alert_id=alert_id, customer_id=customer["customer_id"],
            customer_name=customer["name"], mode=used_mode, requested_mode=self.mode, model=model,
            decision=final, proposed_decision=proposed, rules=rules,
            findings=[f.to_dict() for f in findings], screening=screening, controls=flags,
            requires_senior_review=senior, grounding=grounding,
            metrics={"tool_calls": toolkit.calls, "latency_ms": round((time.perf_counter() - t0) * 1000),
                     **usage},
            audit=audit, fallback_reason=fallback, ground_truth=self.store.label(alert_id),
        )

    # ------------------------------------------------------------------ investigators
    def _investigate_with_llm(self, alert_id: str, toolkit: ToolKit, audit: AuditTrail, usage: dict) -> dict | None:
        messages = [{"role": "system", "content": self.system_prompt},
                    {"role": "user", "content": investigation_request(alert_id)}]
        repaired, finalising = False, False
        for step in range(1, self.max_steps + 1):
            t0 = time.perf_counter()
            resp = self.llm.chat(messages, tools=self.tool_schemas, tool_choice="none" if finalising else "auto")
            usage["llm_calls"] += 1
            usage["prompt_tokens"] += resp.usage.get("prompt_tokens", 0)
            usage["completion_tokens"] += resp.usage.get("completion_tokens", 0)
            llm_ms = (time.perf_counter() - t0) * 1000

            if resp.tool_calls and not finalising:
                audit.log("AGENT", f"Step {step}: model requested {len(resp.tool_calls)} tool call(s)",
                          {"calls": [{"tool": tc.name, "arguments": tc.arguments} for tc in resp.tool_calls],
                           "note": (resp.content or "").strip()[:500] or None}, llm_ms)
                messages.append(resp.as_message())
                for tc in resp.tool_calls:
                    result = toolkit.execute(tc.name, tc.arguments)
                    messages.append({"role": "tool", "tool_call_id": tc.id,
                                     "content": json.dumps(result, default=str, separators=(",", ":"))})
                if step >= self.max_steps - 1:
                    messages.append({"role": "user", "content": FINALISE_REQUEST})
                    finalising = True
                continue

            decision = normalize_decision(extract_json(resp.content))
            if decision:
                audit.log("AGENT", f"Step {step}: model proposed a decision",
                          {"disposition": decision["disposition"], "risk_score": decision["risk_score"]}, llm_ms)
                return decision
            audit.log("AGENT", f"Step {step}: reply was not a valid decision object",
                      {"reply": (resp.content or "")[:500]}, llm_ms)
            if repaired:
                return None
            repaired = True
            messages.append(resp.as_message())
            messages.append({"role": "user", "content": REPAIR_REQUEST})
        return None

    def _investigate_with_rules(self, ctx: CaseContext, toolkit: ToolKit, findings, screening, rules) -> dict:
        cid = ctx.customer["customer_id"]
        toolkit.execute("get_alert", {"alert_id": ctx.alert["alert_id"]})
        toolkit.execute("get_customer_profile", {"customer_id": cid})
        toolkit.execute("detect_typologies", {"customer_id": cid})
        toolkit.execute("get_transactions", {"customer_id": cid, "days": 30, "limit": 15})
        toolkit.execute("screen_sanctions", {"customer_id": cid})
        needed = ["POL-GOV-01"] + [d["policy_ref"] for d in rules["drivers"]]
        if any(f.mitigated for f in findings) or any(h["strength"] == "WEAK" for h in screening["hits"]):
            needed.append("POL-MIT-01")
        if rules["disposition"] == "ESCALATE":
            needed.append("POL-SAR-01")
        for pid in dict.fromkeys(needed):
            if pid not in toolkit.seen_ids:
                toolkit.execute("search_policy", {"query": POLICY_QUERIES[pid], "k": 2})
        return build_rules_decision(ctx, findings, screening, rules, toolkit.seen_ids)


def record_analyst_decision(result: CaseResult, analyst: str, approved: bool, disposition: str,
                            reason: str = "", narrative: str | None = None) -> dict:
    """Record the human decision (CTRL-HITL). Overrides require a reason."""
    if disposition not in C.DISPOSITIONS:
        raise ValueError("Invalid disposition")
    if not approved and not reason.strip():
        raise ValueError("An override needs a reason")
    entry = {
        "analyst": analyst or "analyst",
        "action": "APPROVED" if approved else "OVERRIDDEN",
        "agent_recommendation": result.decision["disposition"],
        "final_disposition": disposition,
        "reason": reason.strip() or None,
        "narrative_edited": narrative is not None and narrative != result.decision["narrative"],
        "decided_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    if narrative is not None:
        entry["final_narrative"] = narrative
    result.analyst_decision = entry
    result.status = f"DECIDED_{disposition}"
    result.audit.log("ANALYST", f"Analyst {entry['action'].lower()} recommendation", entry)
    return entry
