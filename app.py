"""Streamlit UI for the AML Alert Triage Agent.

Run locally:   streamlit run app.py
Deploy:        Streamlit Community Cloud, with GROQ_API_KEY in the app's secrets.

Design: a Level 1 analyst's case workbench. Quiet ink-navy and cool grey surfaces; the one loud element is
the disposition stamp on the case file. Theme colours and fonts live in .streamlit/config.toml.
"""
from __future__ import annotations

import html
import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import streamlit as st

from aml_agent import config as C
from aml_agent.agent import CaseResult, TriageAgent, record_analyst_decision
from aml_agent.data_store import DataStore, public_txn
from aml_agent.llm import GroqClient, LLMError, list_groq_models, usable_models
from aml_agent.report import render_case_report
from eval.run_eval import RESULTS_DIR, evaluate, to_markdown

REPO_URL = "https://github.com/Peerarif5656/aml-triage-agent"
DEMO_CASES = [  # a three-case walkthrough: a real escalation, a cleared false positive and a hard control
    ("ALT-1005", "Student account used as a money mule", "Escalate"),
    ("ALT-1002", "£287k credit explained by a documented house sale", "Close"),
    ("ALT-1003", "Payment to an entity on the sanctions list", "Escalate, with the sanctions control"),
]
DISPLAY = {"ESCALATE": "Escalate", "RFI": "Request info", "CLOSE": "Close"}
ACTOR_LABEL = {"SYSTEM": "System", "TOOL": "Tool", "AGENT": "Agent", "CONTROL": "Control", "ANALYST": "Analyst"}

st.set_page_config(page_title="AML Alert Triage Agent", page_icon="🛡️", layout="wide",
                   menu_items={"Get help": REPO_URL, "Report a bug": REPO_URL + "/issues",
                               "About": "AML Alert Triage Agent by Peer Arif. An agentic AI investigator for bank "
                                        "AML and sanctions alerts. All data is synthetic."})

# ----------------------------------------------------------------------------- styles
st.markdown("""
<style>
:root {
  --ink: #12284B; --ink-2: #2A3F63; --paper: #F4F6F9; --surface: #FFFFFF; --rule: #D9DFE7; --rule-2: #E8ECF1;
  --text: #16233A; --muted: #5B6676; --accent: #0E6B66; --accent-bg: #E3F1EF;
  --esc: #B42318; --esc-bg: #FDEEEC; --rfi: #94560A; --rfi-bg: #FDF3E1; --close: #0B7344; --close-bg: #E5F3EB;
  --mono: "IBM Plex Mono", ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}
.block-container { padding-top: 4.4rem; padding-bottom: 3rem; max-width: 1360px; }
h1, h2, h3, h4 { letter-spacing: -0.01em; }

/* masthead */
.mast { background: var(--ink); color: #fff; border-radius: 10px; padding: 20px 24px; margin-bottom: 4px;
        display: flex; flex-wrap: wrap; gap: 14px 32px; align-items: flex-end; justify-content: space-between; }
.mast .name { font-size: 1.6rem; font-weight: 600; line-height: 1.2; letter-spacing: -0.015em; }
.mast .lede { color: #C7D1E2; margin-top: 6px; font-size: 0.97rem; max-width: 68ch; line-height: 1.5; }
.mast .facts { display: flex; flex-wrap: wrap; gap: 8px; }
.mast .fact { border: 1px solid rgba(255,255,255,.25); color: #E4EAF3; border-radius: 999px; padding: 4px 11px;
              font-size: 0.8rem; white-space: nowrap; }

/* tabs */
.stTabs [data-baseweb="tab-list"] { gap: 6px; border-bottom: 1px solid var(--rule); }
.stTabs [data-baseweb="tab"] { font-weight: 600; padding: 10px 14px; }

/* queue */
.queue { width: 100%; border-collapse: collapse; font-size: 0.88rem; background: var(--surface);
         border: 1px solid var(--rule); border-radius: 8px; overflow: hidden; }
.queue th { text-align: left; font-weight: 600; color: var(--muted); font-size: 0.78rem; padding: 8px 10px;
            background: #EEF1F5; border-bottom: 1px solid var(--rule); }
.queue td { padding: 7px 10px; border-bottom: 1px solid var(--rule-2); color: var(--text); vertical-align: middle; }
.queue tr:last-child td { border-bottom: 0; }
.queue tr.sel td { background: var(--accent-bg); }
.queue tr.sel td:first-child { box-shadow: inset 3px 0 0 var(--accent); }
.qwrap { max-height: 372px; overflow-y: auto; border-radius: 8px; }
.ref { font-family: var(--mono); font-size: 0.82rem; color: var(--ink-2); white-space: nowrap; }
.pill { display: inline-block; font-size: 0.74rem; font-weight: 600; padding: 2px 8px; border-radius: 999px;
        white-space: nowrap; background: #EEF1F5; color: var(--muted); }
.pill.HIGH { background: var(--esc-bg); color: var(--esc); }
.pill.MEDIUM { background: var(--rfi-bg); color: var(--rfi); }
.pill.LOW { background: #EEF1F5; color: var(--muted); }
.pill.ESCALATE { background: var(--esc-bg); color: var(--esc); }
.pill.RFI { background: var(--rfi-bg); color: var(--rfi); }
.pill.CLOSE { background: var(--close-bg); color: var(--close); }

/* case card */
.case { background: var(--surface); border: 1px solid var(--rule); border-radius: 10px; padding: 16px 18px; }
.case .top { display: flex; justify-content: space-between; align-items: center; gap: 8px; flex-wrap: wrap; }
.case .who { font-size: 1.15rem; font-weight: 600; color: var(--ink); margin-top: 8px; }
.case .occ { color: var(--muted); font-size: 0.88rem; margin-top: 2px; }
.case .rule { margin-top: 12px; font-weight: 600; font-size: 0.92rem; color: var(--text); }
.case .trig { margin-top: 4px; color: var(--text); font-size: 0.92rem; line-height: 1.5; }
.facts-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(120px, 1fr)); gap: 10px 16px;
              margin: 14px 0 0; padding-top: 12px; border-top: 1px solid var(--rule-2); }
.facts-grid dt { font-size: 0.76rem; color: var(--muted); }
.facts-grid dd { margin: 2px 0 0; font-weight: 600; font-size: 0.92rem; color: var(--text); }

/* empty state */
.start h3 { margin: 0 0 4px; font-size: 1.15rem; color: var(--ink); }
.start p { color: var(--muted); margin: 0 0 12px; font-size: 0.93rem; }
.startcase { display: flex; flex-direction: column; gap: 2px; }
.startcase .t { font-weight: 600; color: var(--text); }
.startcase .e { color: var(--muted); font-size: 0.86rem; }

/* verdict */
.verdict { display: flex; flex-wrap: wrap; gap: 18px 30px; align-items: center; background: var(--surface);
           border: 1px solid var(--rule); border-radius: 10px; padding: 20px 22px; }
.stamp { font-size: 1.7rem; font-weight: 700; letter-spacing: 0.06em; text-transform: uppercase; line-height: 1;
         padding: 12px 18px 10px; border: 3px solid currentColor; border-radius: 6px; transform: rotate(-3deg);
         box-shadow: inset 0 0 0 2px var(--surface), inset 0 0 0 4px currentColor; white-space: nowrap; }
.stamp.ESCALATE { color: var(--esc); background: var(--esc-bg); }
.stamp.RFI { color: var(--rfi); background: var(--rfi-bg); }
.stamp.CLOSE { color: var(--close); background: var(--close-bg); }
.vbody { flex: 1; min-width: 260px; }
.vhead { display: flex; align-items: baseline; gap: 6px; flex-wrap: wrap; }
.vhead .score { font-size: 2rem; font-weight: 600; color: var(--ink); font-variant-numeric: tabular-nums; }
.vhead .of { color: var(--muted); }
.vhead .lbl { color: var(--muted); font-size: 0.88rem; margin-left: 4px; }
.meter { position: relative; height: 10px; border-radius: 5px; margin: 10px 0 6px;
         background: linear-gradient(90deg, var(--close-bg) 0 20%, var(--rfi-bg) 20% 60%, var(--esc-bg) 60% 100%);
         border: 1px solid var(--rule); }
.meter .tick { position: absolute; top: -4px; bottom: -4px; width: 1px; background: #9AA5B5; }
.meter .mk { position: absolute; top: 50%; width: 14px; height: 14px; border-radius: 50%; transform: translate(-50%, -50%);
             background: var(--ink); border: 2px solid var(--surface); box-shadow: 0 0 0 1px var(--ink); }
.meter .mk.rules { width: 10px; height: 10px; border-radius: 2px; background: var(--accent); top: calc(50% + 13px);
                   transform: translate(-50%, -50%) rotate(45deg); box-shadow: none; }
.scale { display: flex; font-size: 0.72rem; color: var(--muted); margin-top: 16px; }
.scale span { text-align: center; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.vnote { color: var(--muted); font-size: 0.86rem; margin-top: 6px; }
.vnote b { color: var(--text); }
.stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: 12px 18px; margin: 14px 0 4px; }
.stat .k { font-size: 0.78rem; color: var(--muted); }
.stat .v { font-size: 1rem; font-weight: 600; color: var(--text); font-variant-numeric: tabular-nums; }

/* control flags */
.flag { display: flex; gap: 12px; align-items: flex-start; padding: 10px 14px; border-radius: 8px; margin: 8px 0;
        background: #EEF1F5; color: var(--text); font-size: 0.9rem; line-height: 1.45; }
.flag .code { font-family: var(--mono); font-size: 0.76rem; font-weight: 600; padding: 2px 7px; border-radius: 4px;
              background: var(--surface); white-space: nowrap; color: var(--ink-2); }
.flag.CRITICAL { background: var(--esc-bg); } .flag.CRITICAL .code { color: var(--esc); }
.flag.WARN, .flag.HIGH { background: var(--rfi-bg); } .flag.WARN .code, .flag.HIGH .code { color: var(--rfi); }
.flag.INFO { background: var(--accent-bg); } .flag.INFO .code { color: var(--accent); }
.flag.REVIEW { background: var(--rfi-bg); font-weight: 600; }

/* findings */
.sect { margin: 22px 0 6px; font-weight: 600; font-size: 1.02rem; color: var(--ink); }
.items { list-style: none; margin: 0; padding: 0; }
.items li { padding: 10px 0 10px 16px; border-bottom: 1px solid var(--rule-2); position: relative; line-height: 1.5; }
.items li:last-child { border-bottom: 0; }
.items li::before { content: ""; position: absolute; left: 0; top: 17px; width: 7px; height: 7px; border-radius: 50%;
                    background: var(--ink-2); }
.items.mit li::before { background: var(--close); }
.items.act li::before { border-radius: 1px; background: var(--accent); }
.chips { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 6px; }
.chip { font-family: var(--mono); font-size: 0.74rem; padding: 1px 6px; border-radius: 4px; color: var(--ink-2);
        background: #EEF1F5; border: 1px solid var(--rule); }
.chip.more { font-family: inherit; color: var(--accent); background: var(--accent-bg); border-color: transparent; cursor: help; }
.none { color: var(--muted); font-style: italic; }

/* trace */
.trace { display: flex; flex-direction: column; }
.tr { display: grid; grid-template-columns: 70px 74px minmax(0, 1fr); gap: 10px; padding: 7px 0;
      border-bottom: 1px dashed var(--rule); font-size: 0.86rem; line-height: 1.45; align-items: baseline; }
.tr:last-child { border-bottom: 0; }
.tr .t { font-family: var(--mono); font-size: 0.75rem; color: var(--muted); text-align: right; }
.tr .a { font-size: 0.72rem; font-weight: 600; text-align: center; border-radius: 4px; padding: 1px 6px;
         background: #EEF1F5; color: var(--muted); }
.tr .a.TOOL { background: var(--accent-bg); color: var(--accent); }
.tr .a.AGENT { background: #E7ECF6; color: var(--ink-2); }
.tr .a.CONTROL { background: var(--rfi-bg); color: var(--rfi); }
.tr .a.ANALYST { background: var(--close-bg); color: var(--close); }
.tr .w { min-width: 0; overflow-wrap: anywhere; color: var(--text); }
.tr .w code { font-family: var(--mono); font-size: 0.8rem; background: none; color: var(--ink); padding: 0; }
.tr .w .s { color: var(--muted); }

/* evaluation */
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; margin: 6px 0 14px; }
.tile { background: var(--surface); border: 1px solid var(--rule); border-radius: 8px; padding: 12px 14px; }
.tile .v { font-size: 1.7rem; font-weight: 600; color: var(--ink); font-variant-numeric: tabular-nums; line-height: 1.2; }
.tile .k { font-size: 0.86rem; font-weight: 600; color: var(--text); }
.tile .d { font-size: 0.78rem; color: var(--muted); margin-top: 2px; }
.cm { border-collapse: separate; border-spacing: 4px; font-variant-numeric: tabular-nums; }
.cm th { font-size: 0.78rem; color: var(--muted); font-weight: 600; padding: 4px 8px; text-align: center; }
.cm th.row { text-align: right; }
.cm td { width: 64px; height: 44px; text-align: center; font-weight: 600; border-radius: 6px; background: #EEF1F5; }
.cm td.diag { background: var(--close-bg); color: var(--close); }
.cm td.miss { background: var(--esc-bg); color: var(--esc); }
.runhead { display: flex; flex-wrap: wrap; gap: 6px 14px; align-items: baseline; margin-bottom: 4px; }
.runhead .n { font-weight: 600; font-size: 1.05rem; color: var(--ink); }
.runhead .m { color: var(--muted); font-size: 0.86rem; }

/* sidebar */
section[data-testid="stSidebar"] .side-name { font-weight: 600; color: var(--ink); font-size: 1.02rem; }
.footer-note { color: var(--muted); font-size: 0.82rem; }
@media (max-width: 640px) { .tr { grid-template-columns: 58px minmax(0, 1fr); } .tr .a { grid-column: 2; justify-self: start; }
  .tr .w { grid-column: 1 / -1; } .stamp { font-size: 1.35rem; } }
</style>
""", unsafe_allow_html=True)


def esc(x) -> str:
    """Escape any dynamic text before it goes into HTML (model output included)."""
    return html.escape(str(x), quote=True)


def plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def chips(ids: list[str], limit: int = 8) -> str:
    """Evidence reference chips; long lists show the first few and a '+N more' chip listing the rest on hover."""
    if not ids:
        return "<div class='chips'><span class='none'>No evidence cited</span></div>"
    shown = "".join(f"<span class='chip'>{esc(i)}</span>" for i in ids[:limit])
    rest = ids[limit:]
    if rest:
        shown += f"<span class='chip more' title='{esc(', '.join(rest))}'>+{len(rest)} more</span>"
    return f"<div class='chips'>{shown}</div>"


def trace_row(e: dict) -> str:
    d, actor = e["detail"], e["actor"]
    if actor == "TOOL":
        what = f"<code>{esc(e['action'])}</code> <span class='s'>returned {esc(d['summary'])}</span>"
    elif actor == "CONTROL" and isinstance(d, dict) and "message" in d:
        what = f"<b>{esc(e['action'])}</b> <span class='s'>{esc(d['message'])}</span>"
    elif actor == "CONTROL" and isinstance(d, dict) and "score" in d:
        what = f"{esc(e['action'])} <span class='s'>scored {esc(d['score'])}, {esc(DISPLAY[d['disposition']])}</span>"
    elif actor == "AGENT" and isinstance(d, dict) and d.get("calls"):
        calls = ", ".join(f"<code>{esc(c['tool'])}</code>" for c in d["calls"])
        what = f"{esc(e['action'].split(':')[0])}: called {calls}"
    elif actor == "AGENT" and isinstance(d, dict) and "disposition" in d:
        what = f"{esc(e['action'])} <span class='s'>{esc(DISPLAY.get(d['disposition'], d['disposition']))}, " \
               f"risk {esc(d.get('risk_score', ''))}</span>"
    else:
        what = esc(e["action"])
    return (f"<div class='tr'><span class='t'>{e['elapsed_ms']:,.0f} ms</span>"
            f"<span class='a {actor}'>{ACTOR_LABEL.get(actor, actor)}</span><span class='w'>{what}</span></div>")


# ----------------------------------------------------------------------------- setup
@st.cache_resource
def get_store() -> DataStore:
    return DataStore()


@st.cache_data(ttl=3600, show_spinner=False)
def groq_models(api_key: str) -> list[str] | None:
    """Models this key can use, refreshed hourly, so a model Groq retires drops out of the menu."""
    return list_groq_models(api_key)


def secret_key() -> str | None:
    try:
        key = st.secrets.get("GROQ_API_KEY")
    except Exception:  # no secrets file configured
        key = None
    return key or os.getenv("GROQ_API_KEY")


store = get_store()
ss = st.session_state
ss.setdefault("results", {})
ss.setdefault("evals", {})
ss.setdefault("alert_select", DEMO_CASES[0][0])

# ----------------------------------------------------------------------------- sidebar
with st.sidebar:
    st.markdown("<div class='side-name'>Investigator settings</div>", unsafe_allow_html=True)
    key_from_secrets = secret_key()
    engine = st.radio("Engine", ["LLM agent (Groq)", "Rules engine (offline)"],
                      index=0 if key_from_secrets else 1,
                      help="The LLM agent decides which tools to call and writes the case. The rules engine "
                           "runs a fixed plan over the same tools; it is also the automatic fallback.")
    use_llm = engine.startswith("LLM")
    model, api_key, options = C.DEFAULT_MODEL, key_from_secrets, list(C.AVAILABLE_MODELS)
    if use_llm:
        if not key_from_secrets:
            api_key = st.text_input("Groq API key", type="password",
                                    help="Used only in this browser session; never stored.")
        options = usable_models(groq_models(api_key)) if api_key else list(C.AVAILABLE_MODELS)
        model = st.selectbox("Model", options,
                             help="Only models your Groq account can use are listed. gpt-oss models use Groq's "
                                  "prompt caching, which keeps a multi-step investigation within the free tier's "
                                  "per-minute token limit.")
        if key_from_secrets:
            st.caption("Groq API key is configured on the server.")
        elif not api_key:
            st.info("Get a free key at console.groq.com, or switch to the offline rules engine.")
    st.divider()
    st.markdown("**About this demo**")
    st.caption("All customers, transactions, sanctions entries and jurisdictions are synthetic. QK, QM and QV are "
               "fictional higher-risk jurisdictions. The policy is illustrative and modelled on UK practice.")
    st.caption(f"Prompt `{C.PROMPT_VERSION}`, data `{store.data_version}`")
    st.divider()
    st.markdown(f"Built by **Peer Arif**  \n[Source code and documentation]({REPO_URL})")


def build_agent() -> tuple[TriageAgent | None, str | None]:
    if use_llm:
        if not api_key:
            return None, "Add a Groq API key in the sidebar, or switch to the offline rules engine."
        try:
            client = GroqClient(api_key, model=model, fallback_models=[m for m in options if m != model])
            return TriageAgent(store, llm=client, mode="llm"), None
        except LLMError as e:
            return None, str(e)
    return TriageAgent(store, mode="rules"), None


def status_pill(alert_id: str) -> str:
    r: CaseResult | None = ss.results.get(alert_id)
    if r is None:
        return "<span class='pill'>Open</span>"
    if r.analyst_decision:
        final = r.analyst_decision["final_disposition"]
        return f"<span class='pill {final}'>Decided: {DISPLAY[final]}</span>"
    rec = r.decision["disposition"]
    return f"<span class='pill {rec}'>{DISPLAY[rec]}</span>"


def investigate(alert_id: str) -> None:
    """Select a case and run it on the next render (used by the walkthrough buttons)."""
    ss.alert_select = alert_id
    ss.autorun = True


# ----------------------------------------------------------------------------- masthead
engine_fact = f"Agent: {model}" if use_llm else "Engine: offline rules"
st.markdown(f"""
<div class="mast">
  <div>
    <div class="name">AML Alert Triage Agent</div>
    <div class="lede">An AI investigator for bank anti-money laundering and sanctions alerts. It opens the case file,
    decides what evidence and policy it needs, and recommends Close, Request info or Escalate, for an analyst
    to approve.</div>
  </div>
  <div class="facts">
    <span class="fact">{esc(engine_fact)}</span>
    <span class="fact">Human approval on every case</span>
    <span class="fact">Synthetic data</span>
  </div>
</div>
""", unsafe_allow_html=True)

tab_inv, tab_eval, tab_how = st.tabs(["Investigate", "Evaluation", "How it works"])


# ----------------------------------------------------------------------------- result view
def verdict_html(r: CaseResult) -> str:
    d, rules, m = r.decision, r.rules, r.metrics
    agrees = d["disposition"] == rules["disposition"]
    t = {"RFI": C.RFI_THRESHOLD, "ESC": C.ESCALATE_THRESHOLD}
    investigator = f"LLM agent, {r.model}" if r.mode == "llm" else "Offline rules engine"
    tokens = f"{m['prompt_tokens'] + m['completion_tokens']:,}" if m.get("llm_calls") else "none"
    return f"""
<div class="verdict">
  <div class="stamp {d['disposition']}">{DISPLAY[d['disposition']]}</div>
  <div class="vbody">
    <div class="vhead"><span class="score">{d['risk_score']}</span><span class="of">/100</span>
      <span class="lbl">risk score, confidence {esc(d['confidence'].lower())}</span></div>
    <div class="meter" role="img" aria-label="Risk score {d['risk_score']} of 100; rules engine {rules['score']}">
      <span class="tick" style="left:{t['RFI']}%"></span><span class="tick" style="left:{t['ESC']}%"></span>
      <span class="mk" style="left:{min(max(d['risk_score'], 2), 98)}%"></span><span class="mk rules" style="left:{min(max(rules['score'], 2), 98)}%"></span>
    </div>
    <div class="scale"><span style="width:{t['RFI']}%">Close</span><span style="width:{t['ESC'] - t['RFI']}%">Request info</span>
      <span style="width:{100 - t['ESC']}%">Escalate</span></div>
    <div class="vnote">Circle: agent. Diamond: independent rules engine, which scored <b>{rules['score']}</b>
      ({DISPLAY[rules['disposition']]}) and <b>{'agrees' if agrees else 'disagrees'}</b>.</div>
  </div>
</div>
<div class="stats">
  <div class="stat"><div class="k">Investigator</div><div class="v">{esc(investigator)}</div></div>
  <div class="stat"><div class="k">Grounded citations</div><div class="v">{r.grounding['grounded']} of {r.grounding['cited']}</div></div>
  <div class="stat"><div class="k">Tool calls</div><div class="v">{m['tool_calls']}</div></div>
  <div class="stat"><div class="k">Tokens</div><div class="v">{tokens}</div></div>
  <div class="stat"><div class="k">Time</div><div class="v">{m['latency_ms'] / 1000:.1f} s</div></div>
</div>"""


def render_result(r: CaseResult) -> None:
    d = r.decision
    customer = store.get_customer(r.customer_id)
    st.markdown(f"<div class='sect' style='margin-top:4px'>Recommendation for {esc(r.alert_id)}, "
                f"{esc(customer['name'])}</div>", unsafe_allow_html=True)
    st.markdown(verdict_html(r), unsafe_allow_html=True)

    flags = ""
    if r.requires_senior_review:
        flags += ("<div class='flag REVIEW'><span class='code'>REVIEW</span><span>Senior analyst review required before "
                  "this case is actioned.</span></div>")
    for c in r.controls:
        flags += (f"<div class='flag {esc(c['severity'])}'><span class='code'>{esc(c['control'])}</span>"
                  f"<span>{esc(c['message'])}</span></div>")
    if r.mode == "llm" and r.proposed_decision["disposition"] != d["disposition"]:
        flags += (f"<div class='flag INFO'><span class='code'>CONTROLS</span><span>Before controls the agent proposed "
                  f"{DISPLAY[r.proposed_decision['disposition']]}.</span></div>")
    if flags:
        st.markdown(flags, unsafe_allow_html=True)

    def items(rows, key, cls):
        if not rows:
            return "<p class='none'>None</p>"
        return f"<ul class='items {cls}'>" + "".join(
            f"<li>{esc(x[key])}{chips(x['evidence'])}</li>" for x in rows) + "</ul>"

    actions = ("<ul class='items act'>" + "".join(f"<li>{esc(a)}</li>" for a in d["recommended_actions"]) + "</ul>"
               if d["recommended_actions"] else "<p class='none'>None</p>")
    policy = chips(d["policy_refs"]) if d["policy_refs"] else "<p class='none'>None cited</p>"
    st.markdown(
        "<div class='sect'>Key findings</div>" + items(d["key_findings"], "finding", "")
        + "<div class='sect'>Mitigating factors</div>" + items(d["mitigating_factors"], "factor", "mit")
        + "<div class='sect'>Recommended actions</div>" + actions
        + "<div class='sect'>Policy cited</div>" + policy, unsafe_allow_html=True)

    # ---- human in the loop
    st.markdown("<div class='sect'>Analyst decision</div>", unsafe_allow_html=True)
    if r.analyst_decision:
        a = r.analyst_decision
        st.success(f"{a['action'].title()} by {a['analyst']}. Final disposition: {DISPLAY[a['final_disposition']]}"
                   + (f". Reason: {a['reason']}" if a.get("reason") else "."))
    else:
        with st.form(key=f"decide_{r.run_id}", border=True):
            narrative = st.text_area("Case narrative (edit before filing if needed)", value=d["narrative"], height=190)
            col_a, col_b = st.columns(2)
            analyst = col_a.text_input("Analyst", value="L1 Analyst")
            choice = col_b.radio("Decision", ["Approve recommendation", "Override"], horizontal=True)
            override_to = st.selectbox("If overriding, final disposition", C.DISPOSITIONS,
                                       index=C.DISPOSITIONS.index(d["disposition"]), format_func=DISPLAY.get)
            reason = st.text_input("Reason (required when overriding)")
            if st.form_submit_button("Submit decision", type="primary"):
                approved = choice.startswith("Approve")
                try:
                    record_analyst_decision(r, analyst, approved, d["disposition"] if approved else override_to,
                                            reason, narrative)
                    st.rerun()
                except ValueError as e:
                    st.error(str(e))

    dl1, dl2 = st.columns(2)
    dl1.download_button("Download case report", render_case_report(r), file_name=f"{r.alert_id}_case_report.md",
                        mime="text/markdown", width="stretch")
    dl2.download_button("Download audit trail (JSON)", json.dumps(r.to_dict(), indent=2, default=str),
                        file_name=f"{r.alert_id}_{r.run_id}_audit.json", mime="application/json", width="stretch")

    with st.expander(f"Investigation trace: {len(r.audit.events)} steps, run {r.run_id}"):
        st.markdown("<div class='trace'>" + "".join(trace_row(e) for e in r.audit.events) + "</div>",
                    unsafe_allow_html=True)
    with st.expander("Detector findings"):
        if r.findings:
            st.dataframe(pd.DataFrame([{"Typology": f["typology"], "Mitigated": f["mitigated"],
                                        "Description": f["description"], "Evidence": ", ".join(f["evidence"][:6]),
                                        "Mitigation": f["mitigation"] or ""} for f in r.findings]),
                         hide_index=True, width="stretch")
        else:
            st.write("No typologies detected.")
    with st.expander("Sanctions screening"):
        st.write(r.screening["summary"])
        if r.screening["hits"]:
            st.dataframe(pd.DataFrame([{"List entry": h["sanctions_id"], "Listed name": h["listed_name"],
                                        "Screened": h["screened_name"], "Role": h["party_role"],
                                        "Name score": h["name_score"], "Strength": h["strength"],
                                        "Secondary identifiers": ", ".join(f"{k}: {v}" for k, v in
                                                                           h["secondary_identifiers"].items())}
                                       for h in r.screening["hits"]]), hide_index=True, width="stretch")
    with st.expander("Rules engine scoring"):
        st.dataframe(pd.DataFrame(r.rules["drivers"]), hide_index=True, width="stretch")
        st.caption(f"Thresholds: Escalate at {C.ESCALATE_THRESHOLD} or more, Request info at {C.RFI_THRESHOLD} or more.")


def queue_html(alerts: list[dict], selected: str) -> str:
    rows = "".join(
        f"<tr class='{'sel' if a['alert_id'] == selected else ''}'><td><span class='ref'>{esc(a['alert_id'])}</span></td>"
        f"<td>{esc(store.get_customer(a['customer_id'])['name'])}</td>"
        f"<td><span class='pill {a['priority']}'>{a['priority'].title()}</span></td>"
        f"<td>{status_pill(a['alert_id'])}</td></tr>" for a in alerts)
    return ("<div class='qwrap'><table class='queue'><thead><tr><th>Alert</th><th>Customer</th><th>Priority</th>"
            f"<th>Status</th></tr></thead><tbody>{rows}</tbody></table></div>")


def case_html(alert: dict, customer: dict) -> str:
    kind = customer["customer_type"].title()
    income_label = "Declared income" if customer["customer_type"] == "individual" else "Declared turnover"
    facts = [("Customer type", kind), ("KYC risk", customer["risk_rating"].title() + (", PEP" if customer.get("pep") else "")),
             ("Customer since", customer["account_opened"][:4]), ("Raised", alert["alert_date"]),
             (income_label, f"£{customer['declared_annual_income']:,} a year"),
             ("Expected inflow", f"£{customer['expected_monthly_inflow']:,} a month")]
    grid = "".join(f"<div><dt>{esc(k)}</dt><dd>{esc(v)}</dd></div>" for k, v in facts)
    return f"""
<div class="case">
  <div class="top"><span class="ref">{esc(alert['alert_id'])} / {esc(alert['rule_id'])}</span>
    <span class="pill {alert['priority']}">{alert['priority'].title()} priority</span></div>
  <div class="who">{esc(customer['name'])}</div>
  <div class="occ">{esc(customer['occupation'])}</div>
  <div class="rule">{esc(alert['rule_name'])}</div>
  <div class="trig">{esc(alert['trigger_summary'])}</div>
  <dl class="facts-grid">{grid}</dl>
</div>"""


# ----------------------------------------------------------------------------- investigate tab
with tab_inv:
    alerts = store.alert_list()
    alert_ids = [a["alert_id"] for a in alerts]
    left, right = st.columns([0.42, 0.58], gap="large")
    with left:
        alert_id = st.selectbox("Case to investigate", alert_ids, key="alert_select",
                                format_func=lambda x: f"{x}  {store.get_customer(store.get_alert(x)['customer_id'])['name']}")
        alert = store.get_alert(alert_id)
        customer = store.get_customer(alert["customer_id"])
        st.markdown(case_html(alert, customer), unsafe_allow_html=True)
        run = st.button("Run investigation", type="primary", width="stretch") or ss.pop("autorun", False)
        st.markdown("<div class='sect'>Alert queue</div>", unsafe_allow_html=True)
        st.markdown(queue_html(alerts, alert_id), unsafe_allow_html=True)
        with st.expander("Customer transactions, last 90 days"):
            end = datetime.combine(date.fromisoformat(alert["alert_date"]), datetime.max.time())
            rows = store.transactions(customer["customer_id"], end - timedelta(days=90), end)
            df = pd.DataFrame([public_txn(t) for t in rows])
            if not df.empty:
                df["trigger"] = df["txn_id"].isin(alert["trigger_txn_ids"])
                st.dataframe(df[["txn_id", "timestamp", "direction", "amount", "channel", "counterparty",
                                 "counterparty_country", "reference", "trigger"]].iloc[::-1],
                             hide_index=True, width="stretch", height=300,
                             column_config={"amount": st.column_config.NumberColumn("amount", format="£%.2f")})

    with right:
        if run:
            agent, err = build_agent()
            if err:
                st.error(err)
            else:
                with st.status(f"Investigating {alert_id}", expanded=True) as status:
                    result = agent.run(alert_id, on_event=lambda e: status.markdown(trace_row(e), unsafe_allow_html=True))
                    status.update(label=f"Investigation complete: {plural(result.metrics['tool_calls'], 'tool call')} in "
                                        f"{result.metrics['latency_ms'] / 1000:.1f} s", state="complete", expanded=False)
                ss.results[alert_id] = result
        result = ss.results.get(alert_id)
        if result is None:
            with st.container(border=True):
                st.markdown("<div class='start'><h3>Start with one of these cases</h3><p>Each investigation takes a "
                            "few seconds. The agent opens the case file, decides what else it needs, and recommends a "
                            "decision with cited evidence for you to approve or override.</p></div>",
                            unsafe_allow_html=True)
                for aid, desc, expect in DEMO_CASES:
                    c1, c2 = st.columns([0.68, 0.32], vertical_alignment="center")
                    c1.markdown(f"<div class='startcase'><span class='t'><span class='ref'>{aid}</span> {esc(desc)}</span>"
                                f"<span class='e'>Expected: {esc(expect)}</span></div>", unsafe_allow_html=True)
                    c2.button(f"Investigate {aid}", key=f"go_{aid}", on_click=investigate, args=(aid,), width="stretch")
        else:
            render_result(result)


# ----------------------------------------------------------------------------- evaluation tab
def pct(x) -> str:
    return "n/a" if x is None else f"{x:.0%}"


def render_eval(name: str, s: dict) -> None:
    errors = [r for r in s["rows"] if not r["correct"]]
    caught = [r for r in errors if r.get("senior_review") or "CTRL-DUAL" in r["controls"]]
    correct = sum(r["correct"] for r in s["rows"])
    tiles = [
        ("Accuracy", f"{correct} of {s['cases']}", f"{pct(s['accuracy'])} of labelled alerts"),
        ("Escalation recall", pct(s["escalation_recall"]), "Suspicious cases escalated"),
        ("Missed escalations", str(s["missed_escalations"]), "Should escalate, recommended close"),
        ("Grounding", pct(s["grounding_rate"]), "Cited IDs returned by a tool"),
        ("Errors sent for review", f"{len(caught)} of {len(errors)}" if errors else "No errors",
         "Flagged by the controls"),
    ]
    tiles_html = "".join(f"<div class='tile'><div class='k'>{k}</div><div class='v'>{v}</div><div class='d'>{dsc}</div></div>"
                         for k, v, dsc in tiles)
    cm = s["confusion"]
    head = "".join(f"<th>{DISPLAY[c]}</th>" for c in C.DISPOSITIONS)
    body = "".join(
        f"<tr><th class='row'>{DISPLAY[t]}</th>" + "".join(
            f"<td class='{'diag' if t == p else ('miss' if t == 'ESCALATE' and p == 'CLOSE' and cm[t][p] else '')}'>"
            f"{cm[t][p]}</td>" for p in C.DISPOSITIONS) + "</tr>" for t in C.DISPOSITIONS)
    meta = (f"{s['cases']} alerts, prompt {s['prompt_version']}, avg {s['avg_tool_calls']} tool calls"
            + (f" and {s['avg_tokens']:,} tokens per case" if s.get("avg_llm_calls") else ""))
    st.markdown(f"<div class='runhead'><span class='n'>{esc(name)}</span><span class='m'>{esc(meta)}</span></div>"
                f"<div class='tiles'>{tiles_html}</div>", unsafe_allow_html=True)
    a, b = st.columns([0.34, 0.66], gap="medium")
    a.markdown(f"<table class='cm'><tr><th class='row'>Label / agent</th>{head}</tr>{body}</table>",
               unsafe_allow_html=True)
    table = pd.DataFrame(s["rows"])[["alert_id", "customer", "label", "predicted", "rules_engine", "controls", "correct"]]
    b.dataframe(table.rename(columns={"alert_id": "Alert", "customer": "Customer", "label": "Label",
                                      "predicted": "Recommended", "rules_engine": "Rules engine",
                                      "controls": "Controls", "correct": "Correct"}),
                hide_index=True, width="stretch", height=260)
    c1, c2 = st.columns([0.3, 0.7])
    c1.download_button("Download report", to_markdown(s), file_name=f"eval_{name.split(' ')[0].lower()}.md",
                       key=f"dl_{name}", width="stretch")
    with c2.expander("Raw results (JSON)"):
        st.code(json.dumps(s, indent=1, default=str), language="json")


def saved_run_label(stem: str, s: dict) -> str:
    if s.get("mode") == "llm":
        return f"LLM agent, {s.get('model')}, saved run of {s.get('run_at', '')[:10]}"
    return "Offline rules engine, saved run"


with tab_eval:
    st.markdown("<div class='sect' style='font-size:1.25rem;margin-top:8px'>Evaluation against analyst-labelled "
                "alerts</div>", unsafe_allow_html=True)
    st.write("Each of the 13 alerts carries a disposition set by a senior analyst. The headline measures are "
             "**escalation recall** (closing a case that should have been escalated is the costliest error in AML) "
             "and **grounding** (every cited identifier must come from a tool).")
    st.caption("The rules engine was calibrated on this synthetic set, so its score is a sanity check; the LLM agent's "
               "score is the informative one. Batch latency includes waits for Groq's free-tier rate limit; a "
               "single case takes 1 to 3 seconds.")
    # Saved runs: inserted rules-first so the LLM run is shown first (newest entries render at the top).
    for path in sorted(RESULTS_DIR.glob("*.json"), key=lambda p: p.stem.startswith("llm")) if RESULTS_DIR.exists() else []:
        saved = json.loads(path.read_text())
        ss.evals.setdefault(saved_run_label(path.stem, saved), saved)
    for name, s in reversed(list(ss.evals.items())):
        with st.container(border=True):
            render_eval(name, s)
    with st.expander("Run the evaluation again"):
        st.caption("Runs all 13 alerts. The LLM run takes a few minutes on Groq's free tier and uses about a third of "
                   "the daily token allowance, so avoid it just before a live demo.")
        b1, b2 = st.columns(2)
        if b1.button("Run with the rules engine", width="stretch"):
            with st.spinner("Running 13 investigations"):
                ss.evals["Rules engine, this session"] = evaluate(TriageAgent(store, mode="rules"), store)
                st.rerun()
        if b2.button("Run with the LLM agent", width="stretch", disabled=not (use_llm and api_key)):
            agent, err = build_agent()
            if err:
                st.error(err)
            else:
                bar = st.progress(0.0, text="Starting")
                ss.evals[f"LLM agent, {model}, this session"] = evaluate(
                    agent, store, progress=lambda i, n, row: bar.progress(i / n, text=f"{row['alert_id']}: "
                                                                                     f"{DISPLAY[row['predicted']]} ({i} of {n})"))
                bar.empty()
                st.rerun()


# ----------------------------------------------------------------------------- how it works tab
with tab_how:
    st.markdown("<div class='sect' style='font-size:1.25rem;margin-top:8px'>How an investigation runs</div>",
                unsafe_allow_html=True)
    st.graphviz_chart("""
digraph {
  rankdir=LR; bgcolor="transparent"; nodesep=0.35; ranksep=0.45;
  node [shape=box, style="rounded,filled", fillcolor="#FFFFFF", color="#C9D1DC", fontname="Helvetica", fontsize=11,
        fontcolor="#16233A", margin="0.18,0.1"];
  edge [color="#8A96A8", fontname="Helvetica", fontsize=9, fontcolor="#5B6676"];
  alert [label="Monitoring or\\nscreening alert"];
  agent [label="LLM investigator\\nGroq tool-calling loop", fillcolor="#12284B", fontcolor="#FFFFFF", color="#12284B"];
  tools [label="Tools\\nopen_case: alert, KYC, detectors, sanctions\\nget_transactions\\nsearch_policy (BM25 RAG)"];
  rules [label="Independent rules engine\\nsecond line"];
  controls [label="Controls\\ngrounding, sanctions hard stop,\\ndual control, evidence, tipping off", fillcolor="#FDF3E1", color="#E8C88F"];
  analyst [label="Analyst\\napprove or override", fillcolor="#E5F3EB", color="#A9D3BC"];
  audit [label="Audit trail", shape=cylinder];
  alert -> agent; agent -> tools [dir=both, label="function calls"]; alert -> rules;
  agent -> controls [label="proposed decision"]; rules -> controls [label="score"];
  controls -> analyst; analyst -> audit; tools -> audit [style=dashed]; controls -> audit [style=dashed];
}
""", width="stretch")
    st.markdown("""
1. The LLM receives the alert ID and a versioned system prompt describing the procedure, dispositions and rules.
2. It calls `open_case`, which returns the alert, KYC profile, typology detector findings and sanctions screening.
3. It decides what else it needs: `get_transactions` to inspect specific activity, `search_policy` to find the
   policy sections to cite. A clear-cut case may need neither.
4. Tool results go back into the conversation; the loop repeats until the model returns a JSON decision.
5. Separately, a deterministic rules engine scores the same case without seeing the model's answer.
6. Controls check the proposal before any human sees it, then an analyst approves or overrides it. Every step is logged.

**Why one `open_case` call instead of many small tools?** The model calls one tool per turn and each turn re-sends
the conversation. Bundling the case file cut a typical investigation from about 24,000 tokens and 140 seconds to
about 6,000 tokens and 2 to 3 seconds on Groq's free tier, with the same decisions.

#### Controls

| Control | What it does |
|---|---|
| `CTRL-SCHEMA` | Invalid or unparseable model output is repaired once, then falls back to the rules investigator |
| `CTRL-GROUNDING` | Every cited ID (TX, SAN, DOC, POL) must have been returned by a tool in this case; invented ones are removed and flagged |
| `CTRL-SANCTIONS` | A potential true sanctions match can never be closed or sent for more information by the agent |
| `CTRL-DUAL` | If the agent and the rules engine disagree, the case goes to a senior analyst |
| `CTRL-EVIDENCE` | Escalations need cited evidence; closures need mitigating evidence when the risk score is elevated |
| `CTRL-TIPOFF` | An escalation that suggests contacting the customer is flagged for MLRO approval (POCA s.333A) |
| `CTRL-HITL` | Nothing is actioned automatically; overrides need a reason |
| Scope | Tools only return data for the customer on the alert (data minimisation) |
| `CTRL-FALLBACK` | If Groq is unavailable or rate limited, the rules investigator completes the case and says so |

**What this is not:** a production system. Data is synthetic, the policy is illustrative, and BM25 stands in for a
production retrieval stack. The design choices (tool scoping, grounding, dual control, human approval, audit) are
the ones a bank's model risk and compliance teams expect. See the
[governance note](""" + REPO_URL + """/blob/main/docs/GOVERNANCE.md) for the UK regulatory mapping.
""")
