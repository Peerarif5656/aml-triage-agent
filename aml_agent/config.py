"""Central configuration: thresholds, scoring weights and model settings.

Weights and thresholds are deliberately kept in one place so that a model risk
reviewer can see exactly how the rules engine reaches a score.
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"

# ----------------------------------------------------------------- LLM settings
GROQ_BASE_URL = "https://api.groq.com/openai/v1"
# llama-3.3-70b-versatile supports parallel tool calls (fewer round trips, which matters on the
# free tier's tokens-per-minute limit). gpt-oss-120b reasons more carefully but calls tools one at a time.
DEFAULT_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
AVAILABLE_MODELS = [
    "llama-3.3-70b-versatile",
    "openai/gpt-oss-120b",
    "openai/gpt-oss-20b",
]
MAX_AGENT_STEPS = 10          # maximum LLM turns per investigation
LLM_TEMPERATURE = 0.0

# ----------------------------------------------------------------- dispositions
DISPOSITIONS = ("CLOSE", "RFI", "ESCALATE")
ESCALATE_THRESHOLD = 60       # rules score >= 60  -> ESCALATE
RFI_THRESHOLD = 20            # rules score >= 20  -> RFI, otherwise CLOSE

# ----------------------------------------------------------------- jurisdictions
# Fictional higher-risk jurisdictions (ISO 3166 user-assigned codes).
HIGH_RISK_JURISDICTIONS = {
    "QK": "Kestrel Islands (fictional)",
    "QM": "Marazan (fictional)",
    "QV": "Veldora (fictional)",
}

# ----------------------------------------------------------------- detector thresholds
LOOKBACK_DAYS = 90
VELOCITY_WINDOW_DAYS = 30
CASH_REVIEW_THRESHOLD = 10_000
STRUCTURING_BAND = (8_000, 9_999.99)
STRUCTURING_MIN_DEPOSITS = 3
STRUCTURING_WINDOW_DAYS = 10
VELOCITY_RATIO_MEDIUM = 2.5
VELOCITY_RATIO_HIGH = 5.0
PASS_THROUGH_HOURS = 48
PASS_THROUGH_RATIO = 0.70
DORMANCY_DAYS = 180
CIRCULAR_RETURN_DAYS = 14
CIRCULAR_AMOUNT_TOLERANCE = 0.05
EDD_VALID_DAYS = 365
NEW_ACCOUNT_DAYS = 90
FUNNEL_MIN_NEW_PAYERS = 5
INCOME_SHIFT_RATIO = 1.75

# ----------------------------------------------------------------- sanctions screening
SCREEN_MATCH_THRESHOLD = 0.88

# ----------------------------------------------------------------- scoring weights
# points: severity weight if the typology is present and NOT mitigated
# mitigated_points: residual weight if documented evidence explains it
TYPOLOGY_WEIGHTS = {
    "STRUCTURING":               {"points": 60, "mitigated_points": 10, "policy": "POL-CASH-01"},
    "RAPID_PASS_THROUGH":        {"points": 60, "mitigated_points": 10, "policy": "POL-MULE-01"},
    "CIRCULAR_FLOWS":            {"points": 60, "mitigated_points": 10, "policy": "POL-CIRC-01"},
    "DORMANT_REACTIVATION":      {"points": 40, "mitigated_points": 5,  "policy": "POL-DOR-01"},
    "ACCOUNT_TAKEOVER_SIGNALS":  {"points": 20, "mitigated_points": 0,  "policy": "POL-DOR-01"},
    "PEP_UNEXPLAINED_FUNDS":     {"points": 30, "mitigated_points": 5,  "policy": "POL-PEP-01"},
    "VELOCITY_SPIKE":            {"points": 25, "mitigated_points": 0,  "policy": "POL-VEL-01"},
    "LARGE_INFLOW_VS_PROFILE":   {"points": 15, "mitigated_points": 0,  "policy": "POL-VEL-01"},
    "HIGH_RISK_JURISDICTION":    {"points": 20, "mitigated_points": 5,  "policy": "POL-JUR-01"},
    "UNAPPROVED_BENEFICIARY":    {"points": 25, "mitigated_points": 0,  "policy": "POL-NPO-01"},
    "FUNNEL_ACCOUNT":            {"points": 25, "mitigated_points": 5,  "policy": "POL-FUN-01"},
    "INCOME_SHIFT":              {"points": 15, "mitigated_points": 0,  "policy": "POL-VEL-01"},
}
VELOCITY_HIGH_POINTS = 35
SANCTIONS_WEAK_POINTS = 5

PROMPT_VERSION = "triage-v1.3"
