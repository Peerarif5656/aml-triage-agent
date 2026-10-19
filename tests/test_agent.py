import pytest

from aml_agent.agent import TriageAgent, record_analyst_decision
from aml_agent.report import render_case_report

from .mock_llm import FailingLLM, ScriptedLLM


@pytest.fixture(scope="module")
def rules_agent(store):
    return TriageAgent(store, mode="rules")


@pytest.mark.parametrize("alert_id", [f"ALT-{n}" for n in range(1001, 1014)])
def test_rules_investigator_matches_labels(store, rules_agent, alert_id):
    result = rules_agent.run(alert_id)
    assert result.decision["disposition"] == store.label(alert_id)
    assert result.grounding["rate"] == 1.0
    assert result.status == "PENDING_ANALYST_REVIEW"


def test_audit_trail_records_every_tool_call(rules_agent):
    r = rules_agent.run("ALT-1001")
    tool_events = [e for e in r.audit.events if e["actor"] == "TOOL"]
    assert len(tool_events) == r.metrics["tool_calls"] >= 5
    assert {"get_alert", "get_customer_profile", "detect_typologies", "screen_sanctions"} <= {e["action"] for e in tool_events}
    assert r.audit.data_version and r.audit.prompt_version


def _llm_script(final):
    return [
        [("get_alert", {"alert_id": "ALT-1003"})],
        [("get_customer_profile", {"customer_id": "C103"}), ("screen_sanctions", {"customer_id": "C103"})],
        [("search_policy", {"query": "potential true sanctions match"})],
        final,
    ]


def test_llm_loop_runs_tools_and_parses_decision(store):
    final = {"disposition": "ESCALATE", "risk_score": 95, "confidence": "HIGH",
             "typologies": ["SANCTIONS"],
             "key_findings": [{"finding": "Beneficiary matches SAN-0041", "evidence": ["SAN-0041", "TX-103-0055"]}],
             "mitigating_factors": [], "policy_refs": ["POL-SAN-02"],
             "narrative": "Payment TX-103-0055 to a designated entity SAN-0041.",
             "recommended_actions": ["Hold payment"]}
    llm = ScriptedLLM(_llm_script(final))
    r = TriageAgent(store, llm=llm, mode="llm").run("ALT-1003")
    assert r.mode == "llm" and r.fallback_reason is None
    assert r.decision["disposition"] == "ESCALATE"
    assert r.metrics["tool_calls"] == 4 and r.metrics["llm_calls"] == 4
    assert r.grounding["rate"] == 1.0
    # tool results were fed back to the model with matching tool_call_ids
    last_messages = llm.calls[-1]["messages"]
    tool_msgs = [m for m in last_messages if m["role"] == "tool"]
    assert len(tool_msgs) == 4 and all(m["tool_call_id"].startswith("call_") for m in tool_msgs)


def test_sanctions_control_overrides_an_unsafe_closure(store):
    final = {"disposition": "CLOSE", "risk_score": 10, "confidence": "HIGH", "key_findings": [],
             "mitigating_factors": [{"factor": "Regular supplier", "evidence": ["TX-103-0055"]}],
             "policy_refs": [], "narrative": "Looks fine.", "recommended_actions": []}
    r = TriageAgent(store, llm=ScriptedLLM(_llm_script(final)), mode="llm").run("ALT-1003")
    assert r.proposed_decision["disposition"] == "CLOSE"
    assert r.decision["disposition"] == "ESCALATE"
    controls = {c["control"] for c in r.controls}
    assert "CTRL-SANCTIONS" in controls
    assert r.requires_senior_review


def test_grounding_control_removes_invented_evidence(store):
    final = {"disposition": "ESCALATE", "risk_score": 90, "confidence": "HIGH",
             "key_findings": [{"finding": "Match", "evidence": ["SAN-0041", "TX-999-9999", "DOC-0001"]}],
             "mitigating_factors": [], "policy_refs": ["POL-SAN-02", "POL-XYZ-99"],
             "narrative": "See TX-999-9999.", "recommended_actions": []}
    r = TriageAgent(store, llm=ScriptedLLM(_llm_script(final)), mode="llm").run("ALT-1003")
    assert r.decision["key_findings"][0]["evidence"] == ["SAN-0041"]
    assert "POL-XYZ-99" not in r.decision["policy_refs"]
    assert "TX-999-9999" not in r.decision["narrative"]
    assert any(c["control"] == "CTRL-GROUNDING" for c in r.controls)
    assert r.grounding["rate"] < 1.0


def test_dual_control_flags_disagreement(store):
    script = [[("get_alert", {"alert_id": "ALT-1007"}), ("detect_typologies", {"customer_id": "C107"})],
              {"disposition": "CLOSE", "risk_score": 10, "confidence": "LOW", "key_findings": [],
               "mitigating_factors": [], "policy_refs": [], "narrative": "Busy season.", "recommended_actions": []}]
    r = TriageAgent(store, llm=ScriptedLLM(script), mode="llm").run("ALT-1007")
    controls = {c["control"] for c in r.controls}
    assert {"CTRL-DUAL", "CTRL-EVIDENCE"} <= controls
    assert r.requires_senior_review


def test_scope_control_blocks_other_customers(store):
    script = [[("get_customer_profile", {"customer_id": "C999"}), ("get_transactions", {"customer_id": "C101"})],
              {"disposition": "RFI", "risk_score": 30, "key_findings": [], "narrative": "x"}]
    r = TriageAgent(store, llm=ScriptedLLM(script), mode="llm").run("ALT-1007")
    outputs = [e["detail"]["output"] for e in r.audit.events if e["actor"] == "TOOL"]
    assert all("Access denied" in o["error"] for o in outputs)


def test_invalid_reply_is_repaired_then_falls_back(store):
    script = [[("get_alert", {"alert_id": "ALT-1001"})], "I think it is suspicious.", "Still not JSON"]
    r = TriageAgent(store, llm=ScriptedLLM(script), mode="llm").run("ALT-1001")
    assert r.mode == "rules" and "valid decision" in r.fallback_reason
    assert r.decision["disposition"] == "ESCALATE"
    assert r.controls[0]["control"] == "CTRL-FALLBACK"


def test_json_inside_code_fence_is_accepted(store):
    fenced = '```json\n{"disposition": "close", "risk_score": "12", "key_findings": [], "narrative": "ok"}\n```'
    script = [[("get_alert", {"alert_id": "ALT-1011"})], fenced]
    r = TriageAgent(store, llm=ScriptedLLM(script), mode="llm").run("ALT-1011")
    assert r.mode == "llm" and r.proposed_decision["disposition"] == "CLOSE" and r.proposed_decision["risk_score"] == 12


def test_llm_outage_falls_back_to_rules(store):
    r = TriageAgent(store, llm=FailingLLM(), mode="llm").run("ALT-1005")
    assert r.mode == "rules" and r.requested_mode == "llm"
    assert r.decision["disposition"] == "ESCALATE"
    assert "simulated outage" in r.fallback_reason


def test_analyst_decision_and_report(rules_agent):
    r = rules_agent.run("ALT-1007")
    with pytest.raises(ValueError):
        record_analyst_decision(r, "A. Analyst", approved=False, disposition="CLOSE", reason="")
    record_analyst_decision(r, "A. Analyst", approved=False, disposition="CLOSE",
                            reason="Customer confirmed a new catering contract; invoices received.")
    assert r.status == "DECIDED_CLOSE"
    assert r.audit.events[-1]["actor"] == "ANALYST"
    report = render_case_report(r)
    assert "OVERRIDDEN" in report and "ALT-1007" in report and "| # |" in report
