"""Builds the structured decision for the offline (rules engine) investigator.

The LLM writes its own narrative; this module produces an equivalent decision
object from deterministic findings, so offline mode and fallback mode produce
the same shape of output the UI, controls and evaluation expect.
"""
from __future__ import annotations

from .typologies import CaseContext, Finding

POLICY_QUERIES = {
    "POL-GOV-01": "alert triage dispositions close RFI escalate",
    "POL-CASH-01": "cash deposits structuring below threshold multiple branches",
    "POL-MULE-01": "money mule pass-through funds moved on quickly crypto exchange",
    "POL-CIRC-01": "round-tripping circular flows related company",
    "POL-DOR-01": "dormant account reactivation contact details changed",
    "POL-PEP-01": "politically exposed person source of wealth",
    "POL-VEL-01": "activity inconsistent with customer profile turnover increase",
    "POL-JUR-01": "higher-risk jurisdiction enhanced due diligence",
    "POL-NPO-01": "charity payments to private individuals approved beneficiaries",
    "POL-FUN-01": "funnel account many payers new business account",
    "POL-SAN-01": "sanctions screening name similarity secondary identifiers false positive",
    "POL-SAN-02": "potential true sanctions match hold payment sanctions team OFSI",
    "POL-MIT-01": "mitigating evidence documented expected events",
    "POL-SAR-01": "escalation narrative tipping off SAR",
}

ACTIONS = {
    "STRUCTURING": "Review all cash deposits for the last 12 months, including branch CCTV/teller notes, as part of L2 investigation.",
    "RAPID_PASS_THROUGH": "Consider restricting outbound payments while L2 investigates; identify and notify the paying banks via the fraud channel.",
    "CIRCULAR_FLOWS": "L2 to obtain intercompany agreements and review the related party's accounts and filed accounts.",
    "DORMANT_REACTIVATION": "Protect the account: block online banking, revert recent contact changes and contact the customer via verified details on file.",
    "ACCOUNT_TAKEOVER_SIGNALS": "Raise a fraud case and recall the outbound payment to the new payee if possible.",
    "PEP_UNEXPLAINED_FUNDS": "Senior management review under PEP EDD; obtain source of funds evidence for the credit.",
    "HIGH_RISK_JURISDICTION": "Complete EDD on the unverified counterparty before any further payments.",
}

RFI_ASKS = {
    "VELOCITY_SPIKE": "an explanation for the increase in turnover with supporting evidence (contracts, invoices or bookings)",
    "LARGE_INFLOW_VS_PROFILE": "evidence of the source of the large credit",
    "UNAPPROVED_BENEFICIARY": "the contract and trustee approval for the payment to {recipients}",
    "FUNNEL_ACCOUNT": "an explanation of the payments received from many individuals and sales records that tie them to trade",
    "HIGH_RISK_JURISDICTION": "the purpose of the payments and documents verifying the counterparty",
    "INCOME_SHIFT": "evidence of the change in employment",
}


def _descriptor(c: dict) -> str:
    since = c["account_opened"][:4]
    kind = {"individual": "personal customer", "business": "business customer",
            "charity": "charity customer"}[c["customer_type"]]
    return f"{c['name']} ({c['customer_id']}), {kind} since {since}: {c['occupation']}; risk rating {c['risk_rating']}"


def _hit_text(h: dict) -> str:
    sec = ", ".join(f"{k.replace('_', ' ')} {v.lower()}" for k, v in h["secondary_identifiers"].items())
    return (f"{h['party_role'].capitalize()} '{h['screened_name']}' vs listed '{h['listed_name']}' "
            f"({h['sanctions_id']}, {h['programme']}): name score {h['name_score']:.2f}; {sec}")


def build_rules_decision(ctx: CaseContext, findings: list[Finding], screening: dict, rules: dict,
                         seen_policy_ids: set[str]) -> dict:
    alert, customer = ctx.alert, ctx.customer
    disposition = rules["disposition"]
    open_findings = [f for f in findings if not f.mitigated]
    mitigated = [f for f in findings if f.mitigated]
    potential_hits = [h for h in screening["hits"] if h["strength"] in {"STRONG", "POSSIBLE"}]
    weak_hits = [h for h in screening["hits"] if h["strength"] == "WEAK"]
    id_docs = [d.split(" ")[0] for d in customer.get("documents_on_file", []) if "passport" in d.lower()]

    key_findings = [{"finding": f"Potential sanctions match ({h['strength']}): {_hit_text(h)}.",
                     "evidence": [h["sanctions_id"], *h["txn_ids"]]} for h in potential_hits]
    key_findings += [{"finding": f"{f.title}. {f.description}", "evidence": f.evidence[:10]} for f in open_findings]

    mitigating = [{"factor": f"{f.title}: explained. {f.mitigation}", "evidence": f.mitigation_evidence[:6]}
                  for f in mitigated]
    mitigating += [{"factor": f"Sanctions name similarity discounted as a false positive. {_hit_text(h)}.",
                    "evidence": [h["sanctions_id"], *(id_docs if h["party_role"] == "customer" else [])]}
                   for h in weak_hits]

    refs = ["POL-GOV-01"] + [d["policy_ref"] for d in rules["drivers"]]
    if mitigating:
        refs.append("POL-MIT-01")
    if disposition == "ESCALATE":
        refs.append("POL-SAR-01")
    policy_refs = [r for r in dict.fromkeys(refs) if r in seen_policy_ids]

    # ------------------------------------------------------------- narrative
    intro = (f"Alert {alert['alert_id']} ({alert['rule_id']}: {alert['rule_name']}) was raised on "
             f"{alert['alert_date']} for {_descriptor(customer)}.")
    parts = [intro]
    if potential_hits:
        parts.append("Sanctions screening returned a potential true match: " +
                     "; ".join(_hit_text(h) for h in potential_hits) + ".")
    for f in open_findings[:3]:
        parts.append(f.description)
    if mitigating:
        parts.append("Mitigating information reviewed: " +
                     " ".join(m["factor"] for m in mitigating[:2]))
    if disposition == "ESCALATE":
        parts.append("These indicators are not explained by information held on file, giving reasonable grounds "
                     "for suspicion. Recommend escalation to L2 investigations and the MLRO for SAR consideration. "
                     "The customer must not be informed of this referral.")
    elif disposition == "RFI":
        asks = [RFI_ASKS.get(f.typology, "an explanation with supporting documents") for f in open_findings]
        recipients = ", ".join(sorted({r for f in open_findings for r in f.metrics.get("recipients", [])}))
        asks = [a.format(recipients=recipients or "the recipient") for a in dict.fromkeys(asks)]
        parts.append("The activity cannot be explained from information on file but is not clearly suspicious. "
                     "Recommend a request for information seeking " + "; and ".join(asks) +
                     ", then reassessment within 14 days.")
    else:
        parts.append("No unexplained indicators remain after reviewing the customer profile and documents. "
                     "Recommend closure with no further action.")

    # ------------------------------------------------------------- actions
    actions = []
    if potential_hits:
        actions.append("Hold the payment and refer immediately to the sanctions team; L1 must not close (POL-SAN-02).")
    if disposition == "ESCALATE":
        actions.append("Refer to L2 investigations / MLRO for SAR consideration (POL-SAR-01).")
        actions += [ACTIONS[f.typology] for f in open_findings if f.typology in ACTIONS]
        actions.append("Do not inform the customer (tipping off).")
    elif disposition == "RFI":
        actions.append("Send an RFI via the relationship manager or customer contact team; set a 14-day reassessment date.")
    else:
        actions.append("Close the alert with no further action; record the rationale and evidence relied on.")

    return {
        "disposition": disposition,
        "risk_score": rules["score"],
        "confidence": rules["confidence"],
        "typologies": [f.typology for f in open_findings] + [f"SANCTIONS_{h['strength']}_MATCH" for h in potential_hits],
        "key_findings": key_findings,
        "mitigating_factors": mitigating,
        "policy_refs": policy_refs,
        "narrative": " ".join(parts),
        "recommended_actions": list(dict.fromkeys(actions)),
    }
