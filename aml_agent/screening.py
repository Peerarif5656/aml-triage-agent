"""Sanctions name screening with secondary-identifier disambiguation.

Name matching uses Jaro-Winkler similarity on normalised tokens (legal suffixes
and common abbreviations handled), the same family of fuzzy matching used by
commercial screening engines. A name match on its own is never treated as a
confirmed hit: date of birth, nationality and country decide how strong it is.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

from .config import LOOKBACK_DAYS, SCREEN_MATCH_THRESHOLD

LEGAL_SUFFIXES = {"LTD", "LIMITED", "PLC", "LLC", "LLP", "FZE", "FZCO", "INC", "BV", "AS", "GMBH", "SA",
                  "CO", "COMPANY", "GROUP"}
ABBREVIATIONS = {"SHPG": "SHIPPING", "TRDG": "TRADING", "INTL": "INTERNATIONAL", "SVCS": "SERVICES",
                 "MGMT": "MANAGEMENT", "HLDGS": "HOLDINGS"}
STOPWORDS = {"AND", "THE", "OF"}
# Common business words carry little identifying weight; matching on them alone
# ("X Holdings" vs "Y Holdings") is a classic source of screening false positives.
GENERIC_WORDS = {"HOLDINGS", "MARINE", "MARITIME", "SHIPPING", "TRADING", "SERVICES", "INTERNATIONAL",
                 "MANAGEMENT", "PARTS", "SUPPLIES", "SUPPLY", "EXPORTS", "IMPORTS", "LOGISTICS", "CONSULTING",
                 "BANK", "COMMERCIAL", "EXCHANGE", "METALS", "CAPITAL", "GLOBAL", "INVESTMENTS", "PARTNERSHIP",
                 "SOLUTIONS", "SYSTEMS", "TECHNOLOGIES", "DEFENCE", "PETROCHEMICAL", "BROKERAGE"}


def normalize(name: str) -> list[str]:
    s = name.upper().replace("&", " AND ")
    s = re.sub(r"\(.*?\)", " ", s)
    s = re.sub(r"[^A-Z0-9 ]", " ", s)
    tokens = [ABBREVIATIONS.get(t, t) for t in s.split()]
    return [t for t in tokens if t not in LEGAL_SUFFIXES and t not in STOPWORDS]


def jaro_winkler(a: str, b: str, prefix_scale: float = 0.1) -> float:
    if a == b:
        return 1.0
    if not a or not b:
        return 0.0
    match_dist = max(max(len(a), len(b)) // 2 - 1, 0)
    a_flags = [False] * len(a)
    b_flags = [False] * len(b)
    matches = 0
    for i, ch in enumerate(a):
        lo, hi = max(0, i - match_dist), min(i + match_dist + 1, len(b))
        for j in range(lo, hi):
            if not b_flags[j] and b[j] == ch:
                a_flags[i] = b_flags[j] = True
                matches += 1
                break
    if matches == 0:
        return 0.0
    a_m = [a[i] for i in range(len(a)) if a_flags[i]]
    b_m = [b[j] for j in range(len(b)) if b_flags[j]]
    transpositions = sum(x != y for x, y in zip(a_m, b_m)) / 2
    jaro = (matches / len(a) + matches / len(b) + (matches - transpositions) / matches) / 3
    prefix = 0
    for x, y in zip(a[:4], b[:4]):
        if x != y:
            break
        prefix += 1
    return jaro + prefix * prefix_scale * (1 - jaro)


def name_similarity(a: str, b: str) -> float:
    """Similarity between two names in [0, 1], robust to word order and legal suffixes."""
    ta, tb = normalize(a), normalize(b)
    if not ta or not tb:
        return 0.0
    da = [t for t in ta if t not in GENERIC_WORDS]
    db = [t for t in tb if t not in GENERIC_WORDS]
    if da and db:  # compare on distinctive tokens when both names have them
        ta, tb = da, db
    whole = jaro_winkler(" ".join(sorted(ta)), " ".join(sorted(tb)))
    short, long_ = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    token_avg = sum(max(jaro_winkler(s, l) for l in long_) for s in short) / len(short)
    coverage = (len(short) / len(long_)) ** 0.15  # tolerate a missing middle name
    return round(max(whole, token_avg * coverage), 4)


def _compare(expected: str | None, observed: str | None) -> str:
    if not expected or not observed:
        return "UNKNOWN"
    return "MATCH" if expected == observed else "MISMATCH"


def _strength(secondary: dict) -> str:
    values = list(secondary.values())
    if "MATCH" in values:
        return "STRONG"
    if "MISMATCH" in values:
        return "WEAK"
    return "POSSIBLE"


def screen_name(name: str, sanctions: list[dict], *, dob: str | None = None,
                nationality: str | None = None, country: str | None = None,
                threshold: float = SCREEN_MATCH_THRESHOLD) -> list[dict]:
    hits = []
    for entry in sanctions:
        best, best_name = 0.0, entry["name"]
        for candidate in [entry["name"], *entry.get("aliases", [])]:
            score = name_similarity(name, candidate)
            if score > best:
                best, best_name = score, candidate
        if best < threshold:
            continue
        if entry["type"] == "individual":
            secondary = {
                "date_of_birth_year": _compare(entry.get("dob", "")[:4] if entry.get("dob") else None,
                                               dob[:4] if dob else None),
                "nationality": _compare(entry.get("nationality"), nationality),
            }
        else:
            secondary = {"country": _compare(entry.get("country"), country)}
        hits.append({
            "sanctions_id": entry["sanctions_id"],
            "listed_name": entry["name"],
            "matched_alias": best_name,
            "name_score": best,
            "listed_type": entry["type"],
            "listed_dob": entry.get("dob"),
            "listed_nationality": entry.get("nationality"),
            "listed_country": entry.get("country"),
            "programme": entry.get("programme"),
            "secondary_identifiers": secondary,
            "strength": _strength(secondary),
        })
    return sorted(hits, key=lambda h: -h["name_score"])


def screen_case(store, customer_id: str, as_of: datetime, lookback_days: int = LOOKBACK_DAYS) -> dict:
    """Screen the customer and every counterparty they dealt with in the lookback window."""
    customer = store.get_customer(customer_id)
    results = []
    is_individual = customer["customer_type"] == "individual"
    for hit in screen_name(customer["name"], store.sanctions,
                           dob=customer.get("date_of_birth") if is_individual else None,
                           nationality=customer.get("nationality") if is_individual else None,
                           country=customer.get("country_of_incorporation", customer.get("nationality"))):
        results.append({**hit, "party_role": "customer", "screened_name": customer["name"], "txn_ids": []})

    window = store.transactions(customer_id, as_of - timedelta(days=lookback_days), as_of)
    by_cp: dict[tuple[str, str], list[str]] = {}
    for t in window:
        if t["counterparty_type"] in {"merchant", "cash", "payroll", "acquirer", "own_account"}:
            continue
        by_cp.setdefault((t["counterparty"], t["counterparty_country"]), []).append(t["txn_id"])
    for (cp, cp_country), txn_ids in by_cp.items():
        for hit in screen_name(cp, store.sanctions, country=cp_country, nationality=None):
            if hit["listed_type"] == "individual":
                # Only country is known for a counterparty, so compare it with nationality.
                hit["secondary_identifiers"] = {"date_of_birth_year": "UNKNOWN",
                                                "nationality": _compare(hit["listed_nationality"], cp_country)}
                hit["strength"] = _strength(hit["secondary_identifiers"])
            results.append({**hit, "party_role": "counterparty", "screened_name": cp, "txn_ids": txn_ids})

    potential = [r for r in results if r["strength"] in {"STRONG", "POSSIBLE"}]
    return {
        "customer_id": customer_id,
        "parties_screened": 1 + len(by_cp),
        "hits": results,
        "potential_true_match": bool(potential),
        "summary": (
            f"{len(results)} hit(s); {len(potential)} potential true match(es) requiring sanctions team review"
            if results else "No sanctions hits for the customer or any counterparty in the lookback window"
        ),
    }
