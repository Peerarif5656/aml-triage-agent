"""Generate the synthetic dataset used by the AML alert triage agent.

Everything produced here is fictional: customers, counterparties, the sanctions
list and the higher-risk jurisdictions. The higher-risk jurisdictions use the
ISO 3166 user-assigned codes QK, QM and QV so that no real country is labelled
high risk. Output is deterministic (seeded), so the evaluation is reproducible.

Run from the repo root:
    python scripts/generate_data.py
"""
from __future__ import annotations

import json
import random
from datetime import date, datetime, timedelta
from pathlib import Path

SEED = 42
START = date(2026, 4, 1)
AS_OF = date(2026, 10, 15)
DATA_DIR = Path(__file__).resolve().parents[1] / "data"

rng = random.Random(SEED)
_rows: list[dict] = []
MINUTES = [2, 9, 14, 21, 27, 33, 38, 46, 51, 57]
MERCHANTS = [
    "Grocery store", "Fuel station", "Online marketplace", "Restaurant",
    "Pharmacy", "Clothing retailer", "Rail operator", "Coffee shop",
]


# --------------------------------------------------------------------------- helpers
def _ts(when) -> datetime:
    if isinstance(when, datetime):
        return when
    return datetime(when.year, when.month, when.day, rng.randint(8, 18), rng.choice(MINUTES))


def add(cid, when, direction, amount, channel, cp, *, country="GB", cp_type="business",
        ref="", alert=None):
    _rows.append({
        "customer_id": cid,
        "timestamp": _ts(when).isoformat(timespec="minutes"),
        "direction": direction,
        "amount": round(float(amount), 2),
        "currency": "GBP",
        "channel": channel,
        "counterparty": cp,
        "counterparty_country": country,
        "counterparty_type": cp_type,
        "reference": ref,
        "_alert": alert,
    })


def monthly(day, start=START, end=AS_OF):
    y, m = start.year, start.month
    while True:
        first = date(y, m, 1)
        if first > end:
            break
        nxt = date(y + (m // 12), m % 12 + 1, 1)
        last = (nxt - timedelta(days=1)).day
        d = date(y, m, min(day, last))
        if start <= d <= end:
            yield d
        y, m = nxt.year, nxt.month


def weekly(weekday, start=START, end=AS_OF):
    d = start + timedelta(days=(weekday - start.weekday()) % 7)
    while d <= end:
        yield d
        d += timedelta(days=7)


def quarterly(dates):
    for d in dates:
        if START <= d <= AS_OF:
            yield d


def jitter(x, pct=0.06):
    return x * rng.uniform(1 - pct, 1 + pct)


def card_spend(cid, per_month=(3, 6), lo=12, hi=140, start=START, end=AS_OF):
    for d in monthly(1, start, end):
        for _ in range(rng.randint(*per_month)):
            day = d + timedelta(days=rng.randint(0, 27))
            if day > end:
                continue
            add(cid, day, "OUT", rng.uniform(lo, hi), "CARD", rng.choice(MERCHANTS),
                cp_type="merchant", ref="CARD PURCHASE")


def kc(name, relationship):
    return {"name": name, "relationship": relationship}


customers: list[dict] = []
alerts: list[dict] = []
labels: dict[str, dict] = {}


def alert(alert_id, alert_date, rule_id, rule_name, customer_id, priority, summary,
          disposition, rationale):
    alerts.append({
        "alert_id": alert_id,
        "alert_date": alert_date.isoformat(),
        "rule_id": rule_id,
        "rule_name": rule_name,
        "customer_id": customer_id,
        "priority": priority,
        "trigger_summary": summary,
        "trigger_txn_ids": [],
        "status": "OPEN",
    })
    labels[alert_id] = {"disposition": disposition, "rationale": rationale}


# =========================================================================== C101
# Structuring: cash deposits just below the £10,000 internal threshold.
customers.append({
    "customer_id": "C101", "name": "Gareth Pryce", "customer_type": "individual",
    "date_of_birth": "1979-05-21", "nationality": "GB",
    "occupation": "Self-employed landscape gardener (sole trader)",
    "declared_annual_income": 38000, "expected_monthly_inflow": 3200,
    "account_opened": "2017-03-10", "account_type": "Personal current account",
    "risk_rating": "MEDIUM", "pep": False, "pep_details": None, "edd_last_review": None,
    "known_counterparties": [kc("Hillcrest Housing Association", "grounds maintenance client"),
                             kc("Severn Building Society", "mortgage lender")],
    "related_parties": [], "expected_events": [], "recent_profile_changes": [],
    "documents_on_file": ["DOC-2210 (2025 self-assessment tax return)"],
    "kyc_notes": "Cash-intensive sole trader. Normally deposits weekly takings of £300-£900 "
                 "at his home branch (Cardiff Queen St).",
})
for d in weekly(4):
    add("C101", d, "IN", rng.uniform(300, 900), "CASH", "Cash deposit - Cardiff Queen St branch",
        cp_type="cash", ref="CASH TAKINGS")
for d in monthly(12):
    add("C101", d, "IN", jitter(650), "BACS", "Hillcrest Housing Association", ref="GROUNDS MAINT")
for d in monthly(1):
    add("C101", d, "OUT", 1150, "DD", "Severn Building Society", ref="MORTGAGE")
for d in monthly(15):
    add("C101", d, "OUT", 168, "DD", "Cardiff Council", cp_type="government", ref="COUNCIL TAX")
card_spend("C101")
for d, branch, amt in [
    (datetime(2026, 10, 1, 9, 14), "Cardiff Queen St", 9400),
    (datetime(2026, 10, 2, 12, 31), "Newport", 9750),
    (datetime(2026, 10, 3, 10, 2), "Bridgend", 9200),
    (datetime(2026, 10, 6, 15, 46), "Cardiff Queen St", 9900),
    (datetime(2026, 10, 7, 11, 9), "Pontypridd", 9600),
    (datetime(2026, 10, 8, 14, 27), "Caerphilly", 9450),
]:
    add("C101", d, "IN", amt, "CASH", f"Cash deposit - {branch} branch", cp_type="cash",
        ref="CASH", alert="ALT-1001")
add("C101", datetime(2026, 10, 9, 10, 38), "OUT", 56000, "CHAPS", "Halden Property Holdings Ltd",
    ref="DEPOSIT UNIT 4", alert="ALT-1001")
alert("ALT-1001", date(2026, 10, 9), "TM-CASH-07",
      "Multiple cash deposits just below £10,000 within 10 days", "C101", "HIGH",
      "6 cash deposits of £9,200-£9,900 at 5 branches over 8 days, followed by a £56,000 CHAPS payment.",
      "ESCALATE",
      "Classic structuring pattern across multiple branches, far above the customer's normal takings, "
      "with funds consolidated and moved on. Refer to MLRO for SAR consideration.")

# =========================================================================== C102
# Large inflow that is fully explained by documented property sale.
customers.append({
    "customer_id": "C102", "name": "Priya Raman", "customer_type": "individual",
    "date_of_birth": "1985-11-02", "nationality": "GB",
    "occupation": "Staff nurse, NHS hospital trust",
    "declared_annual_income": 41000, "expected_monthly_inflow": 2700,
    "account_opened": "2012-06-18", "account_type": "Personal current account",
    "risk_rating": "LOW", "pep": False, "pep_details": None, "edd_last_review": None,
    "known_counterparties": [kc("University Hospitals NHS Trust", "employer"),
                             kc("P Raman - Cash ISA (own account)", "own account"),
                             kc("Midshire Building Society", "mortgage lender")],
    "related_parties": [],
    "expected_events": [{
        "type": "LARGE_INFLOW", "counterparty": "Whitcombe & Lyle Solicitors LLP",
        "approx_amount": 285000, "evidence": "DOC-5531",
        "note": "Sale of 14 Elm Road, Leicester. Customer informed branch on 2026-08-20; "
                "completion statement DOC-5531 on file.",
    }],
    "recent_profile_changes": [],
    "documents_on_file": ["DOC-5531 (property sale completion statement)", "DOC-1874 (payslip)"],
    "kyc_notes": "Long-standing low-risk customer. Salary paid monthly by NHS trust.",
})
for d in monthly(25):
    add("C102", d, "IN", jitter(2650, 0.02), "BACS", "University Hospitals NHS Trust", ref="SALARY")
for d in monthly(2, end=date(2026, 9, 30)):
    add("C102", d, "OUT", 890, "DD", "Midshire Building Society", ref="MORTGAGE")
for d in monthly(1):
    add("C102", d, "OUT", 152, "DD", "Leicester City Council", cp_type="government", ref="COUNCIL TAX")
card_spend("C102")
add("C102", datetime(2026, 10, 6, 13, 5), "IN", 287450, "CHAPS", "Whitcombe & Lyle Solicitors LLP",
    ref="COMPLETION 14 ELM RD RAMAN", alert="ALT-1002")
add("C102", datetime(2026, 10, 7, 9, 22), "OUT", 250000, "CHAPS", "P Raman - Cash ISA (own account)",
    cp_type="own_account", ref="TRANSFER TO ISA", alert="ALT-1002")
alert("ALT-1002", date(2026, 10, 7), "TM-INFLOW-02",
      "Single credit exceeds 50% of declared annual income", "C102", "MEDIUM",
      "£287,450 CHAPS credit from a solicitors' firm against declared income of £41,000.",
      "CLOSE",
      "Credit matches the documented property sale (completion statement DOC-5531, amount and "
      "solicitor match). Funds moved to the customer's own ISA. No further action.")

# =========================================================================== C103
# Payment to a designated (sanctioned) entity: true sanctions match.
customers.append({
    "customer_id": "C103", "name": "Brightwater Marine Supplies Ltd", "customer_type": "business",
    "incorporation_date": "2009-06-15", "country_of_incorporation": "GB",
    "occupation": "Ship chandlery and marine equipment supply, Southampton",
    "declared_annual_income": 4200000, "expected_monthly_inflow": 350000,
    "account_opened": "2009-07-01", "account_type": "Business current account",
    "risk_rating": "MEDIUM", "pep": False, "pep_details": None, "edd_last_review": "2026-01-20",
    "directors": ["Alan Whitcombe", "Joanna Reyes"],
    "known_counterparties": [kc("Solent Ferry Services Ltd", "customer"),
                             kc("North Sea Tenders BV", "customer"),
                             kc("Fjordline Supply AS", "customer"),
                             kc("Itchen Harbour Pilots Ltd", "customer"),
                             kc("Rotterdam Marine Parts BV", "supplier"),
                             kc("Hampshire Rope & Rigging Ltd", "supplier"),
                             kc("Bergen Deck Equipment AS", "supplier")],
    "related_parties": [], "expected_events": [], "recent_profile_changes": [],
    "documents_on_file": ["DOC-3102 (2025 filed accounts)"],
    "kyc_notes": "Established trading business. Suppliers in NL, NO and GB. No previous "
                 "payments to higher-risk jurisdictions.",
})
for d in weekly(0):
    add("C103", d, "IN", jitter(80000, 0.25), "BACS",
        rng.choice(["Solent Ferry Services Ltd", "North Sea Tenders BV", "Fjordline Supply AS",
                    "Itchen Harbour Pilots Ltd"]), ref=f"INV BWM-{rng.randint(4000, 4999)}")
for d in monthly(5):
    add("C103", d, "OUT", jitter(65000, 0.2), "SWIFT", "Rotterdam Marine Parts BV", country="NL",
        ref="SUPPLIER PAYMENT")
for d in monthly(12):
    add("C103", d, "OUT", jitter(38000, 0.1), "CHAPS", "Hampshire Rope & Rigging Ltd", ref="SUPPLIES")
for d in monthly(18):
    add("C103", d, "OUT", jitter(52000, 0.1), "SWIFT", "Bergen Deck Equipment AS", country="NO",
        ref="DECK EQUIPMENT")
for d in monthly(28):
    add("C103", d, "OUT", jitter(96000, 0.03), "BACS", "Payroll run (BACS bureau)", cp_type="payroll",
        ref="PAYROLL")
for d in quarterly([date(2026, 5, 7), date(2026, 8, 7)]):
    add("C103", d, "OUT", jitter(41000, 0.1), "FPS", "HMRC VAT", cp_type="government", ref="VAT")
add("C103", datetime(2026, 10, 10, 11, 52), "OUT", 148000, "SWIFT", "Zarovel Shipping and Trading FZE",
    country="QK", ref="PO 88213 BUNKER FUEL", alert="ALT-1003")
alert("ALT-1003", date(2026, 10, 10), "SCR-PAY-01", "Payment screening hit on beneficiary name",
      "C103", "HIGH",
      "Outbound SWIFT payment of £148,000 to 'Zarovel Shipping and Trading FZE' (QK) hit the sanctions screening filter.",
      "ESCALATE",
      "Beneficiary name and country match designated entity SAN-0041. Potential true sanctions match: "
      "hold payment and refer to the sanctions team; L1 closure not permitted.")

# =========================================================================== C104
# Name-screening false positive: secondary identifiers do not match.
customers.append({
    "customer_id": "C104", "name": "Rebecca Hartmann", "customer_type": "individual",
    "date_of_birth": "1992-07-19", "nationality": "GB", "occupation": "Pharmacist",
    "declared_annual_income": 52000, "expected_monthly_inflow": 3100,
    "account_opened": "2015-09-01", "account_type": "Personal current account",
    "risk_rating": "LOW", "pep": False, "pep_details": None, "edd_last_review": None,
    "known_counterparties": [kc("Northern Pharmacy Group Ltd", "employer"),
                             kc("Pennine Building Society", "mortgage lender")],
    "related_parties": [], "expected_events": [], "recent_profile_changes": [],
    "documents_on_file": ["DOC-0918 (UK passport, verified at onboarding)"],
    "kyc_notes": "Low-risk salaried customer. Identity verified with UK passport.",
})
for d in monthly(28):
    add("C104", d, "IN", jitter(3050, 0.02), "BACS", "Northern Pharmacy Group Ltd", ref="SALARY")
for d in monthly(1):
    add("C104", d, "OUT", 1240, "DD", "Pennine Building Society", ref="MORTGAGE")
for d in monthly(9):
    add("C104", d, "OUT", jitter(85, 0.2), "DD", "Yorkshire Energy Ltd", ref="ENERGY")
card_spend("C104")
alert("ALT-1004", date(2026, 10, 12), "SCR-CUST-03", "Periodic customer re-screening: name match",
      "C104", "MEDIUM",
      "Customer name 'Rebecca Hartmann' returned a similarity hit against a sanctions list entry during periodic re-screening.",
      "CLOSE",
      "Name similarity only. Listed person SAN-0107 was born in 1949 with QM nationality; customer was born "
      "in 1992, is a GB national verified by passport. False positive; close and record rationale.")

# =========================================================================== C105
# Money mule: rapid pass-through of third-party funds to a crypto exchange.
customers.append({
    "customer_id": "C105", "name": "Liam Doherty", "customer_type": "individual",
    "date_of_birth": "2005-02-14", "nationality": "GB",
    "occupation": "Full-time student with part-time retail job",
    "declared_annual_income": 6500, "expected_monthly_inflow": 900,
    "account_opened": "2023-09-05", "account_type": "Student current account",
    "risk_rating": "LOW", "pep": False, "pep_details": None, "edd_last_review": None,
    "known_counterparties": [kc("Brightmart Retail Ltd", "employer"),
                             kc("Student Loans Company", "maintenance loan"),
                             kc("S Doherty", "mother"),
                             kc("Castlegate Student Living Ltd", "landlord")],
    "related_parties": [], "expected_events": [], "recent_profile_changes": [],
    "documents_on_file": ["DOC-6620 (student enrolment letter)"],
    "kyc_notes": "Student account. Income from part-time job, maintenance loan and occasional "
                 "transfers from mother.",
})
for d in monthly(14):
    add("C105", d, "IN", jitter(540, 0.15), "BACS", "Brightmart Retail Ltd", ref="PAY")
for d in [date(2026, 4, 21), date(2026, 9, 22)]:
    add("C105", d, "IN", 2950, "BACS", "Student Loans Company", cp_type="government", ref="MAINTENANCE LOAN")
for d in monthly(8):
    add("C105", d, "IN", 100, "FPS", "S Doherty", cp_type="individual", ref="LOVE MUM")
for d in monthly(1):
    add("C105", d, "OUT", 640, "FPS", "Castlegate Student Living Ltd", ref="RENT")
card_spend("C105", lo=5, hi=60)
for when, name, amt in [
    (datetime(2026, 10, 3, 10, 12), "K Adeyemi", 950),
    (datetime(2026, 10, 3, 16, 40), "M Kowalski", 1450),
    (datetime(2026, 10, 4, 11, 5), "J Turner", 480),
    (datetime(2026, 10, 5, 9, 48), "A Rossi", 1950),
    (datetime(2026, 10, 6, 13, 22), "T Nguyen", 720),
    (datetime(2026, 10, 7, 10, 3), "R Patel", 1200),
    (datetime(2026, 10, 8, 8, 55), "D Murphy", 1100),
]:
    add("C105", when, "IN", amt, "FPS", name, cp_type="individual",
        ref=rng.choice(["RENT", "THANKS", "PAYBACK", "TICKETS", "DINNER"]), alert="ALT-1005")
    add("C105", when + timedelta(minutes=rng.randint(45, 170)), "OUT", round(amt * 0.85, -1), "FPS",
        "CoinVault Exchange Ltd", cp_type="exchange", ref="WALLET TOP UP", alert="ALT-1005")
    add("C105", when + timedelta(minutes=rng.randint(180, 300)), "OUT", rng.choice([100, 150, 200]),
        "ATM", "ATM withdrawal - Leeds city centre", cp_type="cash", ref="ATM", alert="ALT-1005")
alert("ALT-1005", date(2026, 10, 8), "TM-MULE-04", "Rapid pass-through of third-party funds",
      "C105", "HIGH",
      "7 incoming payments from unrelated individuals in 6 days, each moved on within hours to a crypto exchange or withdrawn as cash.",
      "ESCALATE",
      "Strong money mule indicators: new unrelated payers, near-total onward movement within hours to a "
      "crypto exchange and ATM. Refer to MLRO; consider account restriction.")

# =========================================================================== C106
# Higher-risk jurisdiction payment that is consistent with an EDD-reviewed supply chain.
customers.append({
    "customer_id": "C106", "name": "Meridian Spice Imports Ltd", "customer_type": "business",
    "incorporation_date": "2014-04-02", "country_of_incorporation": "GB",
    "occupation": "Import and wholesale of spices and dried goods, Coventry",
    "declared_annual_income": 1100000, "expected_monthly_inflow": 92000,
    "account_opened": "2014-05-12", "account_type": "Business current account",
    "risk_rating": "HIGH", "pep": False, "pep_details": None, "edd_last_review": "2026-03-18",
    "directors": ["Farid Qureshi", "Helen Marsh"],
    "known_counterparties": [kc("Saffron Valley Agro Exports", "primary supplier (QM) since 2016; EDD verified"),
                             kc("Felixstowe Freight Services Ltd", "logistics"),
                             kc("Northfield Cash & Carry Ltd", "customer"),
                             kc("Spice Route Restaurants Ltd", "customer"),
                             kc("Midlands Foodservice Ltd", "customer"),
                             kc("Harbour Deli Group Ltd", "customer")],
    "related_parties": [], "expected_events": [], "recent_profile_changes": [],
    "documents_on_file": ["DOC-4417 (EDD review March 2026: supplier site visit report and contracts)",
                          "DOC-4420 (supplier invoices Jan-Mar 2026)"],
    "kyc_notes": "High risk due to supply chain in a higher-risk jurisdiction. EDD completed March 2026: "
                 "supplier verified, payments expected monthly at £20k-£30k against invoices.",
})
for d in weekly(1):
    add("C106", d, "IN", jitter(21500, 0.2), "BACS",
        rng.choice(["Northfield Cash & Carry Ltd", "Spice Route Restaurants Ltd",
                    "Midlands Foodservice Ltd", "Harbour Deli Group Ltd"]),
        ref=f"INV MSI-{rng.randint(1000, 1999)}")
for d in monthly(8, end=date(2026, 9, 30)):
    add("C106", d, "OUT", jitter(24000, 0.12), "SWIFT", "Saffron Valley Agro Exports", country="QM",
        ref=f"INV-SVA-{d.strftime('%y%m')}")
for d in monthly(15):
    add("C106", d, "OUT", jitter(6800, 0.1), "BACS", "Felixstowe Freight Services Ltd", ref="FREIGHT")
for d in monthly(28):
    add("C106", d, "OUT", jitter(18500, 0.03), "BACS", "Payroll run (BACS bureau)", cp_type="payroll", ref="PAYROLL")
for d in monthly(1):
    add("C106", d, "OUT", 4200, "DD", "Coventry Trade Park Ltd", ref="WAREHOUSE RENT")
add("C106", datetime(2026, 10, 8, 10, 14), "OUT", 24800, "SWIFT", "Saffron Valley Agro Exports",
    country="QM", ref="INV-SVA-2610", alert="ALT-1006")
alert("ALT-1006", date(2026, 10, 9), "TM-JUR-02",
      "Payments to higher-risk jurisdiction exceed £20,000 in a month", "C106", "MEDIUM",
      "£24,800 SWIFT payment to a supplier in QM (higher-risk jurisdiction).",
      "CLOSE",
      "Payment is to the long-standing supplier verified in the March 2026 EDD review (DOC-4417), "
      "invoice-referenced and in line with 6 months of history. No further action.")

# =========================================================================== C107
# Café turnover spike with no explanation on file: needs information from the customer.
customers.append({
    "customer_id": "C107", "name": "The Copper Kettle Cafe Ltd", "customer_type": "business",
    "incorporation_date": "2019-05-01", "country_of_incorporation": "GB",
    "occupation": "Independent cafe, York",
    "declared_annual_income": 180000, "expected_monthly_inflow": 15000,
    "account_opened": "2019-05-20", "account_type": "Business current account",
    "risk_rating": "LOW", "pep": False, "pep_details": None, "edd_last_review": None,
    "directors": ["Sophie Blake"],
    "known_counterparties": [kc("Card acquirer settlement", "card takings"),
                             kc("Yorkshire Bakery Supplies Ltd", "supplier"),
                             kc("Dales Dairy Ltd", "supplier"),
                             kc("Stonegate Estates Ltd", "landlord")],
    "related_parties": [], "expected_events": [], "recent_profile_changes": [],
    "documents_on_file": ["DOC-2789 (2025 filed accounts)"],
    "kyc_notes": "Single-site cafe. Card takings settled weekly; small monthly cash deposits.",
})
for d in weekly(0, end=date(2026, 9, 14)):
    add("C107", d, "IN", jitter(3500, 0.12), "BACS", "Card acquirer settlement", cp_type="acquirer",
        ref="CARD SETTLEMENT")
for d, amt in zip(weekly(0, start=date(2026, 9, 15), end=date(2026, 10, 13)), [11800, 12450, 13100, 12900]):
    add("C107", d, "IN", amt, "BACS", "Card acquirer settlement", cp_type="acquirer",
        ref="CARD SETTLEMENT", alert="ALT-1007")
for d in monthly(20, end=date(2026, 8, 31)):
    add("C107", d, "IN", jitter(400, 0.2), "CASH", "Cash deposit - York branch", cp_type="cash", ref="CASH")
for d, amt in zip(weekly(4, start=date(2026, 9, 15), end=date(2026, 10, 13)), [1800, 2400, 2100, 2600]):
    add("C107", d, "IN", amt, "CASH", "Cash deposit - York branch", cp_type="cash", ref="CASH",
        alert="ALT-1007")
for d in monthly(10):
    add("C107", d, "OUT", jitter(1900, 0.1), "BACS", "Yorkshire Bakery Supplies Ltd", ref="SUPPLIES")
for d in monthly(12):
    add("C107", d, "OUT", jitter(850, 0.1), "BACS", "Dales Dairy Ltd", ref="SUPPLIES")
for d in monthly(28):
    add("C107", d, "OUT", jitter(6200, 0.03), "BACS", "Payroll run (BACS bureau)", cp_type="payroll", ref="PAYROLL")
for d in monthly(1):
    add("C107", d, "OUT", 2100, "DD", "Stonegate Estates Ltd", ref="RENT")
alert("ALT-1007", date(2026, 10, 14), "TM-VEL-01", "30-day credit turnover above 250% of expected",
      "C107", "MEDIUM",
      "30-day credits of about £59,000 against expected monthly turnover of £15,000.",
      "RFI",
      "Turnover roughly quadrupled with no explanation on file. Not clearly suspicious for a cafe "
      "(could be a new contract or event), so request an explanation and evidence from the customer.")

# =========================================================================== C108
# PEP receiving a large unexplained offshore payment.
customers.append({
    "customer_id": "C108", "name": "Jonathan Ellery-Pike", "customer_type": "individual",
    "date_of_birth": "1968-09-03", "nationality": "GB", "occupation": "Member of Parliament",
    "declared_annual_income": 93000, "expected_monthly_inflow": 5600,
    "account_opened": "2008-02-11", "account_type": "Premier current account",
    "risk_rating": "HIGH", "pep": True, "pep_details": "Domestic PEP: sitting Member of Parliament since 2019",
    "edd_last_review": "2026-02-02",
    "known_counterparties": [kc("House of Commons (members' pay)", "salary"),
                             kc("Westminster Mutual Building Society", "mortgage lender")],
    "related_parties": [], "expected_events": [], "recent_profile_changes": [],
    "documents_on_file": ["DOC-3390 (PEP EDD review Feb 2026: source of wealth = salary and savings)"],
    "kyc_notes": "Domestic PEP. Source of wealth documented as parliamentary salary and savings. "
                 "No declared external business interests.",
})
for d in monthly(27):
    add("C108", d, "IN", 5600, "BACS", "House of Commons (members' pay)", cp_type="government", ref="SALARY")
for d in monthly(1):
    add("C108", d, "OUT", 2300, "DD", "Westminster Mutual Building Society", ref="MORTGAGE")
card_spend("C108", hi=220)
add("C108", datetime(2026, 10, 1, 15, 33), "IN", 95000, "SWIFT", "Orbis Strategic Consulting Ltd",
    country="QK", ref="ADVISORY SERVICES Q3", alert="ALT-1008")
add("C108", datetime(2026, 10, 3, 10, 51), "OUT", 60000, "CHAPS", "Ellery-Pike Family Trust",
    ref="TRANSFER", alert="ALT-1008")
alert("ALT-1008", date(2026, 10, 3), "TM-PEP-01", "PEP receives international credit above £25,000",
      "C108", "HIGH",
      "£95,000 SWIFT credit from a consultancy in QK referenced 'advisory services', £60,000 moved to a family trust 2 days later.",
      "ESCALATE",
      "Large offshore payment to a PEP, inconsistent with the documented source of wealth and with no "
      "declared business interests; funds layered to a trust. Escalate for EDD and SAR consideration.")

# =========================================================================== C109
# Round-tripping between related companies.
customers.append({
    "customer_id": "C109", "name": "Northgate Fabrication Ltd", "customer_type": "business",
    "incorporation_date": "2011-03-22", "country_of_incorporation": "GB",
    "occupation": "Structural steel fabrication, Sheffield",
    "declared_annual_income": 2400000, "expected_monthly_inflow": 200000,
    "account_opened": "2011-04-04", "account_type": "Business current account",
    "risk_rating": "MEDIUM", "pep": False, "pep_details": None, "edd_last_review": None,
    "directors": ["Paul Hendry", "Simone Hendry"],
    "known_counterparties": [kc("NGF Holdings Ltd", "related company: common director Paul Hendry"),
                             kc("Ardent Rail Components Ltd", "customer"),
                             kc("Pennine Construction Group Ltd", "customer"),
                             kc("Humber Offshore Structures Ltd", "customer"),
                             kc("Trent Valley Plant Hire Ltd", "customer"),
                             kc("Sheffield Steel Stockholders Ltd", "supplier"),
                             kc("Don Valley Coatings Ltd", "supplier")],
    "related_parties": ["NGF Holdings Ltd"], "expected_events": [], "recent_profile_changes": [],
    "documents_on_file": ["DOC-2954 (2025 filed accounts)"],
    "kyc_notes": "Established manufacturer. No intercompany loan arrangements disclosed to the bank.",
})
for d in weekly(3):
    add("C109", d, "IN", jitter(45000, 0.2), "BACS",
        rng.choice(["Ardent Rail Components Ltd", "Pennine Construction Group Ltd",
                    "Humber Offshore Structures Ltd", "Trent Valley Plant Hire Ltd"]),
        ref=f"INV NGF-{rng.randint(7000, 7999)}")
for d in monthly(6):
    add("C109", d, "OUT", jitter(88000, 0.1), "CHAPS", "Sheffield Steel Stockholders Ltd", ref="STEEL")
for d in monthly(16):
    add("C109", d, "OUT", jitter(24000, 0.1), "BACS", "Don Valley Coatings Ltd", ref="COATINGS")
for d in monthly(27):
    add("C109", d, "OUT", jitter(61000, 0.03), "BACS", "Payroll run (BACS bureau)", cp_type="payroll", ref="PAYROLL")
for d in monthly(1):
    add("C109", d, "OUT", 7500, "DD", "Attercliffe Industrial Estates Ltd", ref="RENT")
for when, direction, amt, ref in [
    (datetime(2026, 9, 18, 10, 2), "OUT", 50000, "LOAN"),
    (datetime(2026, 9, 22, 14, 21), "IN", 49500, "INVESTMENT"),
    (datetime(2026, 9, 26, 9, 46), "OUT", 75000, "LOAN 2"),
    (datetime(2026, 9, 30, 16, 9), "IN", 74000, "INV RETURN"),
    (datetime(2026, 10, 5, 11, 27), "OUT", 50000, "LOAN 3"),
    (datetime(2026, 10, 9, 15, 38), "IN", 49800, "CAPITAL"),
]:
    add("C109", when, direction, amt, "CHAPS", "NGF Holdings Ltd", ref=ref, alert="ALT-1009")
alert("ALT-1009", date(2026, 10, 10), "TM-CIRC-01",
      "Repeated round-amount transfers returned by the same counterparty", "C109", "MEDIUM",
      "3 round-amount payments (£50k, £75k, £50k) to a related company, each returned within 4 days less 1-2%.",
      "ESCALATE",
      "Round-tripping with a related party: round amounts out, near-identical amounts back with changing "
      "references, no disclosed business rationale. Escalate for investigation.")

# =========================================================================== C110
# Dormant account reactivated after contact details changed: account takeover / mule.
customers.append({
    "customer_id": "C110", "name": "Harold Benning", "customer_type": "individual",
    "date_of_birth": "1947-01-11", "nationality": "GB", "occupation": "Retired",
    "declared_annual_income": 21000, "expected_monthly_inflow": 300,
    "account_opened": "1994-08-30", "account_type": "Personal current account (secondary)",
    "risk_rating": "LOW", "pep": False, "pep_details": None, "edd_last_review": None,
    "known_counterparties": [kc("State Pension (DWP)", "pension (moved to another bank Sept 2025)"),
                             kc("H Benning - Coastal Mutual (own account)", "own account")],
    "related_parties": [], "expected_events": [],
    "recent_profile_changes": [
        {"date": "2026-10-02", "change": "Mobile number and email address changed via online banking"},
        {"date": "2026-10-03", "change": "New payee added: R Mensah"},
    ],
    "documents_on_file": [],
    "kyc_notes": "Elderly customer. Pension moved to another provider in Sept 2025; this account has "
                 "been inactive since.",
})
for d in monthly(3, start=date(2025, 5, 1), end=date(2025, 8, 31)):
    add("C110", d, "IN", 880, "BACS", "State Pension (DWP)", cp_type="government", ref="STATE PENSION")
card_spend("C110", per_month=(2, 4), start=date(2025, 5, 1), end=date(2025, 8, 31))
add("C110", datetime(2025, 9, 14, 10, 30), "OUT", 1200, "FPS", "H Benning - Coastal Mutual (own account)",
    cp_type="own_account", ref="MOVING ACCOUNT")
add("C110", datetime(2026, 10, 4, 9, 41), "IN", 18500, "FPS", "J Smith", cp_type="individual",
    ref="CAR SALE", alert="ALT-1010")
add("C110", datetime(2026, 10, 4, 11, 15), "OUT", 15000, "FPS", "R Mensah", cp_type="individual",
    ref="INVOICE 2231", alert="ALT-1010")
for when in [datetime(2026, 10, 4, 14, 2), datetime(2026, 10, 5, 10, 20), datetime(2026, 10, 6, 9, 12)]:
    add("C110", when, "OUT", 500, "ATM", "ATM withdrawal - Croydon", cp_type="cash", ref="ATM", alert="ALT-1010")
add("C110", datetime(2026, 10, 5, 22, 48), "OUT", 1180, "CARD", "Online electronics retailer",
    cp_type="merchant", ref="CARD PURCHASE", alert="ALT-1010")
alert("ALT-1010", date(2026, 10, 6), "TM-DORM-01", "Dormant account (>180 days) receives credit above £5,000",
      "C110", "HIGH",
      "Account inactive for over a year receives £18,500 from an unknown individual; £15,000 sent to a new payee within 2 hours, plus cash withdrawals.",
      "ESCALATE",
      "Contact details changed and a new payee added days before reactivation, then rapid outflow: "
      "indicates account takeover or the account being used as a mule. Escalate and restrict account.")

# =========================================================================== C111
# Salary increase from a verified new employer.
customers.append({
    "customer_id": "C111", "name": "Aisha Karim", "customer_type": "individual",
    "date_of_birth": "1994-04-27", "nationality": "GB", "occupation": "Senior software engineer",
    "declared_annual_income": 112000, "expected_monthly_inflow": 6600,
    "account_opened": "2016-10-03", "account_type": "Personal current account",
    "risk_rating": "LOW", "pep": False, "pep_details": None, "edd_last_review": None,
    "known_counterparties": [kc("Ledgerline Technologies Ltd", "employer from Sept 2026"),
                             kc("Corvel Systems Ltd", "previous employer"),
                             kc("Canal Quarter Lettings Ltd", "landlord")],
    "related_parties": [],
    "expected_events": [{
        "type": "INCOME_CHANGE", "counterparty": "Ledgerline Technologies Ltd",
        "approx_amount": 6450, "evidence": "DOC-7781",
        "note": "New role from Sept 2026. Offer letter and payslip DOC-7781 verified; KYC refreshed 2026-08-14.",
    }],
    "recent_profile_changes": [{"date": "2026-08-14", "change": "Employment and income updated (KYC refresh)"}],
    "documents_on_file": ["DOC-7781 (offer letter and first payslip, Ledgerline Technologies)"],
    "kyc_notes": "Changed employer in Sept 2026; declared income updated from £58,000 to £112,000.",
})
for d in monthly(25, end=date(2026, 8, 31)):
    add("C111", d, "IN", 3520, "BACS", "Corvel Systems Ltd", ref="SALARY")
add("C111", datetime(2026, 9, 25, 6, 2), "IN", 6450, "BACS", "Ledgerline Technologies Ltd", ref="SALARY",
    alert="ALT-1011")
add("C111", datetime(2026, 10, 1, 6, 2), "IN", 4000, "BACS", "Ledgerline Technologies Ltd",
    ref="SIGN-ON BONUS", alert="ALT-1011")
for d in monthly(1):
    add("C111", d, "OUT", 1450, "DD", "Canal Quarter Lettings Ltd", ref="RENT")
card_spend("C111", hi=180)
alert("ALT-1011", date(2026, 10, 2), "TM-INC-02", "Salary credits increased by more than 75% vs 6-month average",
      "C111", "LOW",
      "Salary credits rose from about £3,520 to £6,450 per month, plus a £4,000 one-off credit from the same employer.",
      "CLOSE",
      "Income change documented and verified (DOC-7781, KYC refresh Aug 2026). One-off credit is a "
      "sign-on bonus from the same employer. No further action.")

# =========================================================================== C112
# Charity paying an individual who is not an approved beneficiary.
customers.append({
    "customer_id": "C112", "name": "Hope Bridge Relief Foundation", "customer_type": "charity",
    "incorporation_date": "2017-02-09", "country_of_incorporation": "GB",
    "occupation": "Registered charity: water and education grants in QV",
    "declared_annual_income": 1600000, "expected_monthly_inflow": 130000,
    "account_opened": "2017-03-01", "account_type": "Charity current account",
    "risk_rating": "HIGH", "pep": False, "pep_details": None, "edd_last_review": "2026-05-10",
    "directors": ["Ruth Okoye (chair of trustees)", "Martin Fell (treasurer)"],
    "known_counterparties": [kc("Hope Bridge Field Office", "own programme office in QV; EDD verified"),
                             kc("Clearwater Wells Partnership", "approved implementing partner in QV"),
                             kc("GiveRight Donations Platform", "online donations"),
                             kc("HMRC Gift Aid", "tax reclaim"),
                             kc("Bristol Harbourside Offices Ltd", "landlord")],
    "related_parties": [], "expected_events": [], "recent_profile_changes": [],
    "documents_on_file": ["DOC-5120 (EDD review May 2026: approved beneficiary list and grant agreements)"],
    "kyc_notes": "Charity operating in a higher-risk jurisdiction. Approved beneficiaries: Hope Bridge "
                 "Field Office and Clearwater Wells Partnership only. Payments to individuals are not expected.",
})
for d in weekly(4):
    add("C112", d, "IN", jitter(24000, 0.2), "BACS", "GiveRight Donations Platform", ref="DONATIONS PAYOUT")
for d in monthly(20):
    add("C112", d, "IN", jitter(16000, 0.15), "BACS", "HMRC Gift Aid", cp_type="government", ref="GIFT AID")
for d in monthly(12):
    add("C112", d, "OUT", jitter(41000, 0.1), "SWIFT", "Hope Bridge Field Office", country="QV",
        ref=f"GRANT TRANCHE {d.strftime('%b').upper()}")
for d in monthly(19):
    add("C112", d, "OUT", jitter(17500, 0.1), "SWIFT", "Clearwater Wells Partnership", country="QV",
        ref="WELLS PROGRAMME")
for d in monthly(27):
    add("C112", d, "OUT", jitter(22000, 0.03), "BACS", "Payroll run (BACS bureau)", cp_type="payroll", ref="PAYROLL")
for d in monthly(1):
    add("C112", d, "OUT", 3100, "DD", "Bristol Harbourside Offices Ltd", ref="RENT")
add("C112", datetime(2026, 10, 7, 16, 18), "OUT", 12000, "FPS", "Daniel Kerr", cp_type="individual",
    ref="CONSULTANCY SEPT", alert="ALT-1012")
alert("ALT-1012", date(2026, 10, 8), "TM-NPO-03", "Charity payment to individual not on approved beneficiary list",
      "C112", "MEDIUM",
      "£12,000 Faster Payment to a private individual referenced 'consultancy', outside the approved beneficiary list.",
      "RFI",
      "Grant payments to QV partners are consistent with EDD. The £12,000 payment to an individual is not "
      "covered by the approved list; request the consultancy contract and trustee approval before deciding.")

# =========================================================================== C113
# New business receiving many small payments from individuals.
customers.append({
    "customer_id": "C113", "name": "Lumen Ticketing Ltd", "customer_type": "business",
    "incorporation_date": "2026-07-08", "country_of_incorporation": "GB",
    "occupation": "Online ticket sales for local music events, Manchester",
    "declared_annual_income": 60000, "expected_monthly_inflow": 5000,
    "account_opened": "2026-08-01", "account_type": "Business current account",
    "risk_rating": "MEDIUM", "pep": False, "pep_details": None, "edd_last_review": None,
    "directors": ["Kieran Walsh"],
    "known_counterparties": [kc("Kieran Walsh", "sole director"),
                             kc("Hostwell Web Services Ltd", "website hosting"),
                             kc("Northern Quarter Live Ltd", "venue")],
    "related_parties": [], "expected_events": [], "recent_profile_changes": [],
    "documents_on_file": ["DOC-8012 (certificate of incorporation)"],
    "kyc_notes": "New business. Declared that ticket sales are taken through the company website "
                 "via a card payment processor.",
})
add("C113", datetime(2026, 8, 2, 12, 4), "IN", 2000, "FPS", "Kieran Walsh", cp_type="individual", ref="CAPITAL INTRO")
for d in monthly(5, start=date(2026, 8, 1)):
    add("C113", d, "OUT", 60, "DD", "Hostwell Web Services Ltd", ref="HOSTING")
add("C113", datetime(2026, 9, 30, 11, 40), "OUT", 1800, "FPS", "Northern Quarter Live Ltd", ref="VENUE HIRE")
first = ["Amy", "Ben", "Chloe", "Dan", "Ella", "Finn", "Grace", "Harry", "Isla", "Jack", "Katie", "Leo",
         "Megan", "Nathan", "Olivia", "Paddy", "Rosie", "Sam", "Tara", "Will", "Zoe", "Callum", "Freya",
         "Josh", "Lily", "Ryan", "Sophie", "Tom", "Abby", "Connor", "Erin", "George", "Holly", "Owen"]
last = ["Barker", "Clarke", "Dawson", "Ellis", "Foster", "Gibson", "Hughes", "Irwin", "Jones", "Kelly",
        "Lowe", "Mason", "Norris", "Owens", "Parker", "Quinn", "Reid", "Shaw", "Taylor", "Usher", "Vaughan",
        "Wood", "Young", "Bell", "Cooke", "Doyle", "Fry", "Hale", "Lamb", "Moss", "Nash", "Pike", "Rowe", "Stone"]
for i in range(34):
    d = date(2026, 9, 14) + timedelta(days=i * 28 // 34)
    add("C113", d, "IN", rng.choice([45, 60, 75, 90, 120, 150, 180, 240, 320]), "FPS",
        f"{first[i]} {last[i]}", cp_type="individual", ref="LUMEN TICKETS", alert="ALT-1013")
add("C113", datetime(2026, 10, 13, 9, 15), "OUT", 5000, "FPS", "Kieran Walsh", cp_type="individual",
    ref="DIRECTOR LOAN", alert="ALT-1013")
alert("ALT-1013", date(2026, 10, 13), "TM-FUNNEL-02",
      "New account receiving credits from more than 20 distinct individuals in 30 days", "C113", "LOW",
      "34 Faster Payments from different individuals (£45-£320) in a 2-month-old business account.",
      "RFI",
      "Consistent with ticket sales in value, but the customer declared sales go through a card processor, "
      "not direct bank transfers. Request an explanation and sales records before deciding.")

# --------------------------------------------------------------------------- sanctions
sanctions = [
    {"sanctions_id": "SAN-0012", "name": "VORANTH, Kesimir Dalovic", "type": "individual", "dob": "1963-04-11",
     "nationality": "QV", "country": "QV", "aliases": ["Kasimir Voranth"],
     "programme": "Illustrative regime: QV conflict", "listed_on": "2023-05-02"},
    {"sanctions_id": "SAN-0019", "name": "Ostrel Bank", "type": "entity", "dob": None, "nationality": None,
     "country": "QM", "aliases": ["Ostrel Commercial Bank"], "programme": "Illustrative regime: QM financial",
     "listed_on": "2022-11-19"},
    {"sanctions_id": "SAN-0023", "name": "MERRADINE, Tovic", "type": "individual", "dob": "1971-12-01",
     "nationality": "QK", "country": "QK", "aliases": [], "programme": "Illustrative regime: QK maritime",
     "listed_on": "2024-03-08"},
    {"sanctions_id": "SAN-0031", "name": "Kalvaro Holdings Ltd", "type": "entity", "dob": None,
     "nationality": None, "country": "QK", "aliases": ["Kalvaro Group"],
     "programme": "Illustrative regime: QK maritime", "listed_on": "2024-03-08"},
    {"sanctions_id": "SAN-0041", "name": "ZAROVEL SHIPPING AND TRADING FZE", "type": "entity", "dob": None,
     "nationality": None, "country": "QK", "aliases": ["Zarovel Shpg & Trdg", "Zarovel Marine FZE"],
     "programme": "Illustrative regime: QK maritime", "listed_on": "2025-02-11"},
    {"sanctions_id": "SAN-0055", "name": "IRSKAYA, Luda Fenn", "type": "individual", "dob": "1980-06-23",
     "nationality": "QM", "country": "QM", "aliases": [], "programme": "Illustrative regime: QM financial",
     "listed_on": "2023-09-30"},
    {"sanctions_id": "SAN-0062", "name": "Pellucid Arms Brokerage", "type": "entity", "dob": None,
     "nationality": None, "country": "QV", "aliases": ["Pellucid Defence Trading"],
     "programme": "Illustrative regime: QV conflict", "listed_on": "2023-05-02"},
    {"sanctions_id": "SAN-0078", "name": "DRESSEL, Anton Moritz", "type": "individual", "dob": "1958-02-17",
     "nationality": "QK", "country": "QK", "aliases": [], "programme": "Illustrative regime: QK maritime",
     "listed_on": "2025-02-11"},
    {"sanctions_id": "SAN-0091", "name": "Tessaly Petrochemical Trading", "type": "entity", "dob": None,
     "nationality": None, "country": "QK", "aliases": [], "programme": "Illustrative regime: QK maritime",
     "listed_on": "2024-07-15"},
    {"sanctions_id": "SAN-0107", "name": "HARTMAN, Rebekka", "type": "individual", "dob": "1949-01-30",
     "nationality": "QM", "country": "QM", "aliases": ["Rebeka Hartmanova"],
     "programme": "Illustrative regime: QM financial", "listed_on": "2022-11-19"},
    {"sanctions_id": "SAN-0113", "name": "OKONDRA, Selim Varr", "type": "individual", "dob": "1975-10-09",
     "nationality": "QV", "country": "QV", "aliases": [], "programme": "Illustrative regime: QV conflict",
     "listed_on": "2023-05-02"},
    {"sanctions_id": "SAN-0120", "name": "Greyfen Maritime Services", "type": "entity", "dob": None,
     "nationality": None, "country": "QK", "aliases": ["Greyfen Marine"],
     "programme": "Illustrative regime: QK maritime", "listed_on": "2025-02-11"},
    {"sanctions_id": "SAN-0126", "name": "Novarre Metals Exchange", "type": "entity", "dob": None,
     "nationality": None, "country": "QM", "aliases": [], "programme": "Illustrative regime: QM financial",
     "listed_on": "2024-01-22"},
]


def main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    rows = sorted(_rows, key=lambda r: (r["customer_id"], r["timestamp"]))
    seq: dict[str, int] = {}
    alert_txns: dict[str, list[str]] = {}
    for r in rows:
        num = r["customer_id"][1:]
        seq[num] = seq.get(num, 0) + 1
        r["txn_id"] = f"TX-{num}-{seq[num]:04d}"
        tag = r.pop("_alert")
        if tag:
            alert_txns.setdefault(tag, []).append(r["txn_id"])
    ordered = [{k: r[k] for k in ["txn_id", "customer_id", "timestamp", "direction", "amount", "currency",
                                  "channel", "counterparty", "counterparty_country", "counterparty_type",
                                  "reference"]} for r in rows]
    for a in alerts:
        a["trigger_txn_ids"] = alert_txns.get(a["alert_id"], [])

    meta = {
        "dataset": "Synthetic AML alert triage dataset",
        "version": "1.0",
        "seed": SEED,
        "as_of": AS_OF.isoformat(),
        "currency": "GBP",
        "higher_risk_jurisdictions": {
            "QK": "Kestrel Islands (fictional)",
            "QM": "Marazan (fictional)",
            "QV": "Veldora (fictional)",
        },
        "notice": "All people, companies, sanctions entries and jurisdictions are fictional.",
    }
    files = {
        "customers.json": customers,
        "transactions.json": ordered,
        "alerts.json": alerts,
        "sanctions_list.json": sanctions,
        "labels.json": labels,
        "meta.json": meta,
    }
    for name, obj in files.items():
        (DATA_DIR / name).write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {len(customers)} customers, {len(ordered)} transactions, {len(alerts)} alerts, "
          f"{len(sanctions)} sanctions entries to {DATA_DIR}")


if __name__ == "__main__":
    main()
