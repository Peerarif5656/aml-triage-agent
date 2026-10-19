"""Guardrails applied to every recommendation before a human sees it.

CTRL-SCHEMA     the model's answer must be a valid decision object (else fall back)
CTRL-GROUNDING  every cited identifier must come from a tool result or the agent's instructions in this case
CTRL-SANCTIONS  a potential true sanctions match can never be closed or sent for RFI
CTRL-DUAL       if the LLM and the independent rules engine disagree, a senior analyst reviews
CTRL-EVIDENCE   escalations need evidence; closures need mitigation when the rules score is elevated
CTRL-TIPOFF     an LLM-written escalation that suggests contacting the customer is flagged for MLRO approval
CTRL-HITL       nothing is actioned automatically: every case waits for an analyst decision
"""
from __future__ import annotations

import copy
import re

from . import config as C
from .tools import ID_PATTERN

CUSTOMER_CONTACT = re.compile(
    r"\b(rfi|source[- ]of[- ](?:funds|wealth)|customer (?:contact|outreach)|"
    r"(?:contact|ask|inform|notify|tell|call|email|write to|reach out to|request\b.{0,40}\bfrom) (?:the )?customer)\b",
    re.I)
NEGATED = re.compile(r"\b(do not|don't|never|must not|avoid|without)\b", re.I)


def customer_contact_actions(actions: list[str]) -> list[str]:
    """Recommended actions that would involve contacting the customer (ignoring 'do not ...' warnings)."""
    return [a for a in actions if CUSTOMER_CONTACT.search(a) and not NEGATED.search(a)]


def normalize_decision(obj) -> dict | None:
    """Validate and normalise a decision object. Returns None if it is unusable (CTRL-SCHEMA)."""
    if not isinstance(obj, dict):
        return None
    disposition = str(obj.get("disposition", "")).strip().upper()
    if disposition not in C.DISPOSITIONS:
        return None
    try:
        score = int(round(float(obj.get("risk_score", 0))))
    except (TypeError, ValueError):
        score = 0
    confidence = str(obj.get("confidence", "MEDIUM")).upper()
    if confidence not in {"LOW", "MEDIUM", "HIGH"}:
        confidence = "MEDIUM"

    def items(key: str, text_key: str) -> list[dict]:
        out = []
        for entry in obj.get(key) or []:
            if isinstance(entry, str):
                entry = {text_key: entry, "evidence": ID_PATTERN.findall(entry)}
            if not isinstance(entry, dict):
                continue
            ev = entry.get("evidence") or []
            if isinstance(ev, str):
                ev = ID_PATTERN.findall(ev) or [ev]
            out.append({text_key: str(entry.get(text_key) or entry.get("text") or ""), "evidence": [str(e) for e in ev]})
        return out

    def strings(key: str) -> list[str]:
        val = obj.get(key) or []
        return [str(v) for v in (val if isinstance(val, list) else [val])]

    return {
        "disposition": disposition,
        "risk_score": max(0, min(100, score)),
        "confidence": confidence,
        "typologies": strings("typologies"),
        "key_findings": items("key_findings", "finding"),
        "mitigating_factors": items("mitigating_factors", "factor"),
        "policy_refs": strings("policy_refs"),
        "narrative": str(obj.get("narrative") or ""),
        "recommended_actions": strings("recommended_actions"),
    }


def _cited_ids(d: dict) -> set[str]:
    ids = set()
    for kf in d["key_findings"]:
        ids.update(kf["evidence"])
    for mf in d["mitigating_factors"]:
        ids.update(mf["evidence"])
    ids.update(d["policy_refs"])
    ids.update(ID_PATTERN.findall(d["narrative"]))
    return {i for i in ids if ID_PATTERN.fullmatch(i)}


def apply_controls(proposed: dict, rules: dict, seen_ids: set[str], mode: str) -> tuple[dict, list[dict], bool, dict]:
    d = copy.deepcopy(proposed)
    flags: list[dict] = []
    senior = False

    # CTRL-GROUNDING ----------------------------------------------------------
    cited = _cited_ids(d)
    ungrounded = sorted(cited - seen_ids)
    if ungrounded:
        bad = set(ungrounded)
        for kf in d["key_findings"]:
            kf["evidence"] = [e for e in kf["evidence"] if e not in bad]
        for mf in d["mitigating_factors"]:
            mf["evidence"] = [e for e in mf["evidence"] if e not in bad]
        d["policy_refs"] = [p for p in d["policy_refs"] if p not in bad]
        d["narrative"] = re.sub("|".join(map(re.escape, ungrounded)), "[unverified reference removed]", d["narrative"])
        flags.append({"control": "CTRL-GROUNDING", "severity": "HIGH",
                      "message": f"Removed {len(ungrounded)} citation(s) that no tool returned in this case: "
                                 f"{', '.join(ungrounded)}."})
        senior = True
    grounding = {"cited": len(cited), "grounded": len(cited) - len(ungrounded),
                 "rate": round((len(cited) - len(ungrounded)) / len(cited), 3) if cited else 1.0}

    # CTRL-SANCTIONS ----------------------------------------------------------
    if rules["sanctions_override"]:
        if d["disposition"] != "ESCALATE":
            flags.append({"control": "CTRL-SANCTIONS", "severity": "CRITICAL",
                          "message": f"Recommendation changed from {d['disposition']} to ESCALATE: potential true "
                                     f"sanctions match cannot be closed at L1 (POL-SAN-02)."})
            d["disposition"] = "ESCALATE"
            d["risk_score"] = max(d["risk_score"], 90)
            senior = True
        else:
            flags.append({"control": "CTRL-SANCTIONS", "severity": "INFO",
                          "message": "Potential true sanctions match: hold the payment and refer to the sanctions team."})

    # CTRL-TIPOFF -------------------------------------------------------------
    if mode == "llm" and d["disposition"] == "ESCALATE":
        contact = customer_contact_actions(d["recommended_actions"])
        if contact:
            flags.append({"control": "CTRL-TIPOFF", "severity": "WARN",
                          "message": f"{len(contact)} recommended action(s) would contact the customer while the case "
                                     f"is being escalated: \"{contact[0]}\". Get MLRO approval before any customer "
                                     f"contact (tipping-off risk, POL-SAR-01)."})

    # CTRL-DUAL ---------------------------------------------------------------
    if mode == "llm" and d["disposition"] != rules["disposition"]:
        flags.append({"control": "CTRL-DUAL", "severity": "WARN",
                      "message": f"LLM recommends {d['disposition']}; independent rules engine scores "
                                 f"{rules['score']} ({rules['disposition']}). Senior analyst review required."})
        senior = True

    # CTRL-EVIDENCE -----------------------------------------------------------
    if d["disposition"] in {"ESCALATE", "RFI"} and not any(kf["evidence"] for kf in d["key_findings"]):
        flags.append({"control": "CTRL-EVIDENCE", "severity": "WARN",
                      "message": f"{d['disposition']} recommended without any cited evidence."})
        senior = True
    if (d["disposition"] == "CLOSE" and rules["score"] >= C.RFI_THRESHOLD
            and not any(mf["evidence"] for mf in d["mitigating_factors"])):
        flags.append({"control": "CTRL-EVIDENCE", "severity": "WARN",
                      "message": f"Closure recommended without cited mitigating evidence while the rules score is "
                                 f"{rules['score']}."})
        senior = True

    return d, flags, senior, grounding
