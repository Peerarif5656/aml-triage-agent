import pytest

from aml_agent.typologies import CaseContext, run_detectors

EXPECTED = {
    # alert: ({typologies that must fire}, {typologies that must be mitigated})
    "ALT-1001": ({"STRUCTURING", "VELOCITY_SPIKE"}, set()),
    "ALT-1002": ({"VELOCITY_SPIKE", "LARGE_INFLOW_VS_PROFILE"}, {"VELOCITY_SPIKE", "LARGE_INFLOW_VS_PROFILE"}),
    "ALT-1003": ({"HIGH_RISK_JURISDICTION"}, set()),
    "ALT-1004": (set(), set()),
    "ALT-1005": ({"RAPID_PASS_THROUGH", "FUNNEL_ACCOUNT", "VELOCITY_SPIKE"}, set()),
    "ALT-1006": ({"HIGH_RISK_JURISDICTION"}, {"HIGH_RISK_JURISDICTION"}),
    "ALT-1007": ({"VELOCITY_SPIKE"}, set()),
    "ALT-1008": ({"PEP_UNEXPLAINED_FUNDS", "HIGH_RISK_JURISDICTION", "LARGE_INFLOW_VS_PROFILE"}, set()),
    "ALT-1009": ({"CIRCULAR_FLOWS"}, set()),
    "ALT-1010": ({"DORMANT_REACTIVATION", "ACCOUNT_TAKEOVER_SIGNALS", "RAPID_PASS_THROUGH"}, set()),
    "ALT-1011": ({"INCOME_SHIFT"}, {"INCOME_SHIFT"}),
    "ALT-1012": ({"UNAPPROVED_BENEFICIARY", "HIGH_RISK_JURISDICTION"}, {"HIGH_RISK_JURISDICTION"}),
    "ALT-1013": ({"FUNNEL_ACCOUNT"}, set()),
}


@pytest.mark.parametrize("alert_id", sorted(EXPECTED))
def test_detectors_fire_as_expected(store, alert_id):
    must_fire, must_be_mitigated = EXPECTED[alert_id]
    findings = {f.typology: f for f in run_detectors(CaseContext(store, store.get_alert(alert_id)))}
    assert must_fire <= set(findings), f"missing: {must_fire - set(findings)}"
    for t in must_be_mitigated:
        assert findings[t].mitigated, f"{t} should be mitigated"
    for t, f in findings.items():
        if t not in must_be_mitigated:
            assert not f.mitigated, f"{t} should not be mitigated"


def test_every_finding_cites_real_transactions(store):
    for alert in store.alert_list():
        for f in run_detectors(CaseContext(store, alert)):
            assert f.evidence, f"{alert['alert_id']} {f.typology} has no evidence"
            for txn_id in f.evidence:
                assert txn_id in store.txn_index


def test_structuring_metrics(store):
    f = next(f for f in run_detectors(CaseContext(store, store.get_alert("ALT-1001"))) if f.typology == "STRUCTURING")
    assert f.metrics["deposits"] == 6
    assert len(f.metrics["branches"]) == 5
    assert f.metrics["onward_payment"], "the £56k onward payment should be linked"


def test_detectors_ignore_activity_after_alert_date(store):
    ctx = CaseContext(store, store.get_alert("ALT-1011"))
    assert all(t["_dt"] <= ctx.as_of for t in ctx.history)
