"""Transparent rules engine that scores a case independently of the LLM.

It serves two purposes:
1. Offline mode: the agent can triage without an LLM (useful for demos and as a fallback).
2. Dual control: when the LLM runs, its disposition is compared with this score and
   any disagreement is routed to a senior analyst.
"""
from __future__ import annotations

from . import config as C
from .typologies import Finding


def disposition_for(score: int) -> str:
    if score >= C.ESCALATE_THRESHOLD:
        return "ESCALATE"
    if score >= C.RFI_THRESHOLD:
        return "RFI"
    return "CLOSE"


def score_case(findings: list[Finding], screening: dict) -> dict:
    drivers, total = [], 0
    for f in findings:
        w = C.TYPOLOGY_WEIGHTS[f.typology]
        points = w["mitigated_points"] if f.mitigated else w["points"]
        if (f.typology == "VELOCITY_SPIKE" and not f.mitigated
                and f.metrics.get("ratio", 0) >= C.VELOCITY_RATIO_HIGH):
            points = C.VELOCITY_HIGH_POINTS
        drivers.append({"typology": f.typology, "points": points, "mitigated": f.mitigated,
                        "policy_ref": f.policy_ref})
        total += points

    forced = False
    for hit in screening.get("hits", []):
        if hit["strength"] in {"STRONG", "POSSIBLE"}:
            forced = True
            drivers.append({"typology": f"SANCTIONS_{hit['strength']}_MATCH", "points": 100,
                            "mitigated": False, "policy_ref": "POL-SAN-02"})
        else:
            drivers.append({"typology": "SANCTIONS_WEAK_MATCH", "points": C.SANCTIONS_WEAK_POINTS,
                            "mitigated": True, "policy_ref": "POL-SAN-01"})
            total += C.SANCTIONS_WEAK_POINTS

    score = 100 if forced else min(total, 100)
    disposition = "ESCALATE" if forced else disposition_for(score)
    nearest = min(abs(score - C.ESCALATE_THRESHOLD), abs(score - C.RFI_THRESHOLD))
    confidence = "HIGH" if forced or nearest >= 15 else ("MEDIUM" if nearest >= 5 else "LOW")
    return {
        "score": score,
        "disposition": disposition,
        "confidence": confidence,
        "sanctions_override": forced,
        "drivers": sorted(drivers, key=lambda d: -d["points"]),
        "thresholds": {"ESCALATE": C.ESCALATE_THRESHOLD, "RFI": C.RFI_THRESHOLD},
    }
