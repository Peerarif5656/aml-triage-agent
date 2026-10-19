"""Deterministic typology detectors.

Each detector looks for one money laundering or fraud pattern in the customer's
activity, returns the transactions that evidence it, and checks whether
documented information on file (expected events, EDD-verified counterparties)
mitigates it. These are the agent's "eyes": the LLM reasons over their output,
and the rules engine scores it independently as a second line of control.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from statistics import median

from . import config as C
from .screening import name_similarity

KNOWN_MATCH = 0.92
CONTACT_CHANGE_KEYWORDS = ("mobile", "email", "payee", "password", "address", "phone")


@dataclass
class Finding:
    typology: str
    title: str
    description: str
    evidence: list[str]
    metrics: dict = field(default_factory=dict)
    mitigated: bool = False
    mitigation: str | None = None
    mitigation_evidence: list[str] = field(default_factory=list)
    policy_ref: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


class CaseContext:
    """Everything a detector needs about one alert, anchored at the alert date."""

    def __init__(self, store, alert: dict):
        self.store = store
        self.alert = alert
        self.customer = store.get_customer(alert["customer_id"])
        d = date.fromisoformat(alert["alert_date"])
        self.as_of = datetime(d.year, d.month, d.day, 23, 59)
        self.history = store.transactions(self.customer["customer_id"], None, self.as_of)

    def window(self, days: int, end_days_ago: int = 0) -> list[dict]:
        start = self.as_of - timedelta(days=days)
        end = self.as_of - timedelta(days=end_days_ago)
        return [t for t in self.history if start <= t["_dt"] <= end]

    @property
    def is_individual(self) -> bool:
        return self.customer["customer_type"] == "individual"

    def known(self, counterparty: str) -> dict | None:
        best, best_kc = 0.0, None
        for kc in self.customer.get("known_counterparties", []):
            score = name_similarity(counterparty, kc["name"])
            if score > best:
                best, best_kc = score, kc
        return best_kc if best >= KNOWN_MATCH else None

    def expected_event(self, txn: dict) -> dict | None:
        for ev in self.customer.get("expected_events", []):
            if name_similarity(txn["counterparty"], ev["counterparty"]) < KNOWN_MATCH:
                continue
            if ev["type"] == "INCOME_CHANGE":
                return ev
            approx = ev.get("approx_amount") or 0
            if approx and abs(txn["amount"] - approx) / approx <= 0.15:
                return ev
        return None

    def edd_current(self) -> bool:
        last = self.customer.get("edd_last_review")
        if not last:
            return False
        return (self.as_of.date() - date.fromisoformat(last)).days <= C.EDD_VALID_DAYS

    def account_age_days(self) -> int:
        return (self.as_of.date() - date.fromisoformat(self.customer["account_opened"])).days


def _gbp(x: float) -> str:
    return f"£{x:,.0f}"


def _ids(rows) -> list[str]:
    return [r["txn_id"] for r in rows]


# ----------------------------------------------------------------------------- detectors
def detect_structuring(ctx: CaseContext) -> Finding | None:
    lo, hi = C.STRUCTURING_BAND
    cash = [t for t in ctx.window(C.LOOKBACK_DAYS)
            if t["channel"] == "CASH" and t["direction"] == "IN" and lo <= t["amount"] <= hi]
    best: list[dict] = []
    for i, first in enumerate(cash):
        group = [t for t in cash[i:] if t["_dt"] - first["_dt"] <= timedelta(days=C.STRUCTURING_WINDOW_DAYS)]
        if len(group) > len(best):
            best = group
    if len(best) < C.STRUCTURING_MIN_DEPOSITS:
        return None
    total = sum(t["amount"] for t in best)
    branches = sorted({t["counterparty"] for t in best})
    normal = [t["amount"] for t in ctx.window(C.LOOKBACK_DAYS)
              if t["channel"] == "CASH" and t["direction"] == "IN" and t["amount"] < lo]
    onward = [t for t in ctx.history if t["direction"] == "OUT" and t["counterparty_type"] != "own_account"
              and best[-1]["_dt"] <= t["_dt"] <= best[-1]["_dt"] + timedelta(days=5)
              and t["amount"] >= 0.5 * total]
    desc = (f"{len(best)} cash deposits between {_gbp(lo)} and £9,999 within "
            f"{(best[-1]['_dt'] - best[0]['_dt']).days + 1} days, totalling {_gbp(total)}, "
            f"across {len(branches)} branch(es).")
    if normal:
        desc += f" The customer's other cash deposits average {_gbp(sum(normal) / len(normal))}."
    if onward:
        desc += f" Funds were then moved on in a {_gbp(onward[0]['amount'])} payment to {onward[0]['counterparty']}."
    return Finding("STRUCTURING", "Cash structuring below the £10,000 review threshold", desc,
                   _ids(best) + _ids(onward),
                   {"deposits": len(best), "total": round(total, 2), "branches": branches,
                    "typical_cash_deposit": round(sum(normal) / len(normal), 2) if normal else None,
                    "onward_payment": _ids(onward)},
                   policy_ref="POL-CASH-01")


def detect_velocity(ctx: CaseContext) -> Finding | None:
    w = [t for t in ctx.window(C.VELOCITY_WINDOW_DAYS)
         if t["direction"] == "IN" and t["counterparty_type"] != "own_account"]
    total = sum(t["amount"] for t in w)
    expected = max(ctx.customer.get("expected_monthly_inflow") or 0, 250)
    ratio = total / expected
    if ratio < C.VELOCITY_RATIO_MEDIUM:
        return None
    explained = [t for t in w if ctx.expected_event(t)]
    explained_total = sum(t["amount"] for t in explained)
    adjusted = (total - explained_total) / expected
    top = sorted(w, key=lambda t: -t["amount"])[:6]
    f = Finding("VELOCITY_SPIKE", "Credit turnover far above expected profile",
                f"Credits in the last {C.VELOCITY_WINDOW_DAYS} days total {_gbp(total)}, "
                f"{ratio:.1f}x the expected monthly inflow of {_gbp(expected)}.",
                _ids(top),
                {"credits_30d": round(total, 2), "expected_monthly": expected, "ratio": round(ratio, 2),
                 "ratio_excluding_documented": round(adjusted, 2)},
                policy_ref="POL-VEL-01")
    if explained and adjusted < C.VELOCITY_RATIO_MEDIUM:
        evs = {ctx.expected_event(t)["evidence"] for t in explained}
        f.mitigated = True
        f.mitigation = (f"{_gbp(explained_total)} of the credits match documented expected events; "
                        f"excluding them the ratio is {adjusted:.1f}x.")
        f.mitigation_evidence = sorted(evs) + _ids(explained)
    return f


def detect_large_inflow(ctx: CaseContext) -> Finding | None:
    income = ctx.customer.get("declared_annual_income") or 0
    if not income:
        return None
    share = 0.5 if ctx.is_individual else 0.25
    big = [t for t in ctx.window(C.VELOCITY_WINDOW_DAYS)
           if t["direction"] == "IN" and t["counterparty_type"] != "own_account" and t["amount"] >= share * income]
    if not big:
        return None
    f = Finding("LARGE_INFLOW_VS_PROFILE", "Single credit large relative to declared income",
                "; ".join(f"{_gbp(t['amount'])} from {t['counterparty']} ({t['amount'] / income:.0%} of "
                          f"declared annual {'income' if ctx.is_individual else 'turnover'})" for t in big) + ".",
                _ids(big), {"declared_annual": income, "threshold_share": share}, policy_ref="POL-VEL-01")
    events = [ctx.expected_event(t) for t in big]
    if all(events):
        f.mitigated = True
        f.mitigation = "; ".join(ev["note"] for ev in events)
        f.mitigation_evidence = sorted({ev["evidence"] for ev in events})
    return f


def detect_high_risk_jurisdiction(ctx: CaseContext) -> Finding | None:
    rows = [t for t in ctx.window(C.LOOKBACK_DAYS) if t["counterparty_country"] in C.HIGH_RISK_JURISDICTIONS]
    if not rows:
        return None
    by_cp: dict[str, list[dict]] = {}
    for t in rows:
        by_cp.setdefault(t["counterparty"], []).append(t)
    unverified, verified = [], []
    for cp, txns in by_cp.items():
        (verified if ctx.known(cp) and ctx.edd_current() else unverified).append(cp)
    countries = sorted({t["counterparty_country"] for t in rows})
    total = sum(t["amount"] for t in rows)
    f = Finding("HIGH_RISK_JURISDICTION", "Activity with higher-risk jurisdiction",
                f"{len(rows)} transaction(s) totalling {_gbp(total)} with counterparties in "
                f"{', '.join(f'{c} ({C.HIGH_RISK_JURISDICTIONS[c]})' for c in countries)}. "
                f"Unverified counterparties: {', '.join(unverified) or 'none'}.",
                _ids(rows[-6:]),
                {"countries": countries, "counterparties": sorted(by_cp), "unverified": unverified,
                 "edd_last_review": ctx.customer.get("edd_last_review"), "total": round(total, 2)},
                policy_ref="POL-JUR-01")
    if not unverified:
        f.mitigated = True
        f.mitigation = (f"All counterparties ({', '.join(verified)}) are verified in the EDD review dated "
                        f"{ctx.customer.get('edd_last_review')}, which is within 12 months.")
        f.mitigation_evidence = [d.split(" ")[0] for d in ctx.customer.get("documents_on_file", [])
                                 if "EDD" in d]
    return f


def detect_rapid_pass_through(ctx: CaseContext) -> Finding | None:
    w = ctx.window(C.VELOCITY_WINDOW_DAYS)
    inbound = [t for t in w if t["direction"] == "IN" and t["counterparty_type"] == "individual"
               and not ctx.known(t["counterparty"])]
    if not inbound:
        return None
    outs = [dict(t, _left=t["amount"]) for t in w if t["direction"] == "OUT"
            and t["counterparty_type"] not in {"own_account", "payroll", "government", "merchant"}]
    moved_total, used, per_inbound, exit_hours = 0.0, [], [], []
    for t in inbound:
        need = t["amount"]
        for o in outs:
            if need <= 0:
                break
            gap = (o["_dt"] - t["_dt"]).total_seconds() / 3600
            if 0 <= gap <= C.PASS_THROUGH_HOURS and o["_left"] > 0:
                take = min(need, o["_left"])
                o["_left"] -= take
                need -= take
                if o["txn_id"] not in used:
                    used.append(o["txn_id"])
                exit_hours.append(gap)
        moved = t["amount"] - need
        moved_total += moved
        per_inbound.append(moved / t["amount"])
    total_in = sum(t["amount"] for t in inbound)
    ratio = moved_total / total_in
    big_single = any(t["amount"] >= 10_000 and p >= 0.8 for t, p in zip(inbound, per_inbound))
    if not ((len(inbound) >= 3 and total_in >= 3_000 and ratio >= C.PASS_THROUGH_RATIO) or big_single):
        return None
    destinations = sorted({o["counterparty_type"] for o in outs if o["txn_id"] in used})
    return Finding("RAPID_PASS_THROUGH", "Third-party funds moved on within 48 hours",
                   f"{len(inbound)} credit(s) from unrelated individuals totalling {_gbp(total_in)}; "
                   f"{ratio:.0%} left the account within {C.PASS_THROUGH_HOURS} hours "
                   f"(median {median(exit_hours):.1f}h) to: {', '.join(destinations)}.",
                   _ids(inbound) + used,
                   {"payers": len(inbound), "total_in": round(total_in, 2), "moved_out": round(moved_total, 2),
                    "ratio": round(ratio, 2), "median_hours_to_exit": round(median(exit_hours), 1),
                    "destination_types": destinations},
                   policy_ref="POL-MULE-01")


def detect_funnel(ctx: CaseContext) -> Finding | None:
    def payers(rows):
        return {t["counterparty"] for t in rows if t["direction"] == "IN"
                and t["counterparty_type"] == "individual" and not ctx.known(t["counterparty"])}
    recent_rows = ctx.window(C.VELOCITY_WINDOW_DAYS)
    recent = payers(recent_rows)
    baseline = payers(ctx.window(C.LOOKBACK_DAYS, end_days_ago=C.VELOCITY_WINDOW_DAYS + 1))
    new_account = ctx.account_age_days() < C.NEW_ACCOUNT_DAYS
    if len(recent) < C.FUNNEL_MIN_NEW_PAYERS or (len(baseline) > 2 and not new_account):
        return None
    rows = [t for t in recent_rows if t["direction"] == "IN" and t["counterparty"] in recent]
    total = sum(t["amount"] for t in rows)
    desc = (f"{len(recent)} different individuals paid in {_gbp(total)} in {C.VELOCITY_WINDOW_DAYS} days "
            f"(previous 60 days: {len(baseline)} such payers).")
    if new_account:
        desc += f" Account is {ctx.account_age_days()} days old."
    return Finding("FUNNEL_ACCOUNT", "Many unrelated individuals paying into one account", desc,
                   _ids(rows[:10]),
                   {"distinct_payers_30d": len(recent), "baseline_payers": len(baseline), "total": round(total, 2),
                    "account_age_days": ctx.account_age_days()},
                   policy_ref="POL-FUN-01")


def detect_circular_flows(ctx: CaseContext) -> Finding | None:
    w = ctx.window(C.LOOKBACK_DAYS)
    outs = [t for t in w if t["direction"] == "OUT" and t["amount"] >= 10_000]
    ins = [t for t in w if t["direction"] == "IN"]
    pairs, used_in = [], set()
    for o in outs:
        for i in ins:
            if i["txn_id"] in used_in or i["counterparty"] != o["counterparty"]:
                continue
            days = (i["_dt"] - o["_dt"]).days
            if 0 <= days <= C.CIRCULAR_RETURN_DAYS and abs(i["amount"] - o["amount"]) / o["amount"] <= C.CIRCULAR_AMOUNT_TOLERANCE:
                pairs.append((o, i, days))
                used_in.add(i["txn_id"])
                break
    if len(pairs) < 2:
        return None
    cps = sorted({o["counterparty"] for o, _, _ in pairs})
    related = [cp for cp in cps if cp in ctx.customer.get("related_parties", [])]
    rounds = sum(1 for o, _, _ in pairs if o["amount"] % 1000 == 0)
    refs = sorted({t["reference"] for o, i, _ in pairs for t in (o, i)})
    return Finding("CIRCULAR_FLOWS", "Funds sent out and returned by the same party",
                   f"{len(pairs)} payments to {', '.join(cps)} returned within "
                   f"{max(d for _, _, d in pairs)} days at 95-100% of the amount sent; {rounds} were round amounts. "
                   f"References used: {', '.join(refs)}." + (" Counterparty is a related party." if related else ""),
                   [x for o, i, _ in pairs for x in (o["txn_id"], i["txn_id"])],
                   {"pairs": len(pairs), "round_amounts": rounds, "related_party": bool(related),
                    "total_out": round(sum(o["amount"] for o, _, _ in pairs), 2)},
                   policy_ref="POL-CIRC-01")


def detect_dormant_reactivation(ctx: CaseContext) -> Finding | None:
    recent = ctx.window(C.VELOCITY_WINDOW_DAYS)
    earlier = [t for t in ctx.history if t["_dt"] < ctx.as_of - timedelta(days=C.VELOCITY_WINDOW_DAYS)]
    if not recent or not earlier:
        return None
    gap = (recent[0]["_dt"] - earlier[-1]["_dt"]).days
    credits = [t for t in recent if t["direction"] == "IN" and t["amount"] >= 5_000]
    if gap < C.DORMANCY_DAYS or not credits:
        return None
    return Finding("DORMANT_REACTIVATION", "Dormant account suddenly reactivated",
                   f"No activity for {gap} days (last on {earlier[-1]['_dt'].date()}), then "
                   f"{_gbp(sum(t['amount'] for t in credits))} credited.",
                   _ids(credits) + [earlier[-1]["txn_id"]],
                   {"days_dormant": gap, "last_activity": earlier[-1]["_dt"].date().isoformat()},
                   policy_ref="POL-DOR-01")


def detect_account_takeover(ctx: CaseContext) -> Finding | None:
    changes = [c for c in ctx.customer.get("recent_profile_changes", [])
               if 0 <= (ctx.as_of.date() - date.fromisoformat(c["date"])).days <= 14
               and any(k in c["change"].lower() for k in CONTACT_CHANGE_KEYWORDS)]
    if not changes:
        return None
    seen_before = {t["counterparty"] for t in ctx.history if t["_dt"] < ctx.as_of - timedelta(days=14)}
    new_payee_outs = [t for t in ctx.window(14) if t["direction"] == "OUT" and t["amount"] >= 1_000
                      and t["counterparty"] not in seen_before and t["counterparty_type"] != "merchant"]
    if not new_payee_outs:
        return None
    return Finding("ACCOUNT_TAKEOVER_SIGNALS", "Profile changes shortly before payments to a new payee",
                   "Recent changes: " + "; ".join(f"{c['date']}: {c['change']}" for c in changes) +
                   f". Then {_gbp(sum(t['amount'] for t in new_payee_outs))} paid to new payee(s) "
                   f"{', '.join(sorted({t['counterparty'] for t in new_payee_outs}))}.",
                   _ids(new_payee_outs), {"changes": changes}, policy_ref="POL-DOR-01")


def detect_pep_funds(ctx: CaseContext) -> Finding | None:
    if not ctx.customer.get("pep"):
        return None
    big = [t for t in ctx.window(C.VELOCITY_WINDOW_DAYS) if t["direction"] == "IN" and t["amount"] >= 25_000
           and not ctx.known(t["counterparty"])]
    if not big:
        return None
    f = Finding("PEP_UNEXPLAINED_FUNDS", "PEP received large credit from an unknown source",
                f"Customer is a PEP ({ctx.customer.get('pep_details')}). Received "
                + "; ".join(f"{_gbp(t['amount'])} from {t['counterparty']} ({t['counterparty_country']}), "
                            f"reference '{t['reference']}'" for t in big)
                + ". Documented source of wealth does not include this.",
                _ids(big), {"pep_details": ctx.customer.get("pep_details")}, policy_ref="POL-PEP-01")
    events = [ctx.expected_event(t) for t in big]
    if all(events):
        f.mitigated = True
        f.mitigation = "; ".join(ev["note"] for ev in events)
        f.mitigation_evidence = sorted({ev["evidence"] for ev in events})
    return f


def detect_unapproved_beneficiary(ctx: CaseContext) -> Finding | None:
    if ctx.is_individual:
        return None
    rows = [t for t in ctx.window(C.LOOKBACK_DAYS) if t["direction"] == "OUT" and t["amount"] >= 5_000
            and t["counterparty_type"] == "individual" and not ctx.known(t["counterparty"])]
    if not rows:
        return None
    return Finding("UNAPPROVED_BENEFICIARY", "Payment to an individual outside known/approved counterparties",
                   "; ".join(f"{_gbp(t['amount'])} to {t['counterparty']} (reference '{t['reference']}')"
                             for t in rows) + ". The recipient is not on the customer's known or approved list.",
                   _ids(rows), {"recipients": sorted({t["counterparty"] for t in rows})},
                   policy_ref="POL-NPO-01" if ctx.customer["customer_type"] == "charity" else "POL-VEL-01")


def detect_income_shift(ctx: CaseContext) -> Finding | None:
    salary = [t for t in ctx.window(210) if t["direction"] == "IN" and "SALARY" in t["reference"].upper()]
    if len(salary) < 3:
        return None
    latest, previous = salary[-1], salary[:-1][-6:]
    avg = sum(t["amount"] for t in previous) / len(previous)
    ratio = latest["amount"] / avg
    if ratio < C.INCOME_SHIFT_RATIO:
        return None
    related = [t for t in ctx.window(C.VELOCITY_WINDOW_DAYS) if t["direction"] == "IN"
               and t["counterparty"] == latest["counterparty"] and t["txn_id"] != latest["txn_id"]]
    f = Finding("INCOME_SHIFT", "Salary credits increased sharply",
                f"Latest salary {_gbp(latest['amount'])} from {latest['counterparty']} is {ratio:.1f}x the "
                f"previous average of {_gbp(avg)}"
                + (f"; plus {len(related)} other credit(s) from the same payer totalling "
                   f"{_gbp(sum(t['amount'] for t in related))}." if related else "."),
                [latest["txn_id"]] + _ids(related), {"ratio": round(ratio, 2), "previous_average": round(avg, 2)},
                policy_ref="POL-VEL-01")
    ev = ctx.expected_event(latest)
    if ev:
        f.mitigated = True
        f.mitigation = ev["note"]
        f.mitigation_evidence = [ev["evidence"]]
    return f


DETECTORS = [
    detect_structuring, detect_rapid_pass_through, detect_circular_flows, detect_dormant_reactivation,
    detect_account_takeover, detect_pep_funds, detect_velocity, detect_large_inflow,
    detect_high_risk_jurisdiction, detect_unapproved_beneficiary, detect_funnel, detect_income_shift,
]


def run_detectors(ctx: CaseContext) -> list[Finding]:
    return [f for f in (d(ctx) for d in DETECTORS) if f is not None]
