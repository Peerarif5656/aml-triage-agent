"""Streamlit UI for the AML Alert Triage Agent.

Run locally:   streamlit run app.py
Deploy:        Streamlit Community Cloud, with GROQ_API_KEY in the app's secrets.
"""
from __future__ import annotations

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
DEMO_CASES = [  # a three-case walkthrough that shows escalation, a cleared false positive and a hard control
    ("ALT-1005", "Student account used as a money mule", "ESCALATE"),
    ("ALT-1002", "£287k credit explained by a documented house sale", "CLOSE"),
    ("ALT-1003", "Payment to a sanctioned entity", "ESCALATE + sanctions control"),
]

st.set_page_config(page_title="AML Alert Triage Agent", page_icon="🛡️", layout="wide",
                   menu_items={"Get help": REPO_URL, "Report a bug": REPO_URL + "/issues",
                               "About": "AML Alert Triage Agent by Peer Arif. An agentic AI investigator for bank "
                                        "AML and sanctions alerts. All data is synthetic."})

STYLE = {"ESCALATE": ("#b42318", "#fef3f2"), "RFI": ("#b54708", "#fffaeb"), "CLOSE": ("#067647", "#ecfdf3")}
ACTOR_ICON = {"SYSTEM": "⚙️", "TOOL": "🔧", "AGENT": "🤖", "CONTROL": "🛡️", "ANALYST": "👤"}

st.markdown("""
<style>
.badge {display:inline-block;padding:0.35rem 0.9rem;border-radius:999px;font-weight:700;font-size:1.05rem;
        letter-spacing:0.02em;border:1px solid currentColor;}
.small-label {font-size:0.8rem;opacity:0.7;margin-bottom:0.25rem;}
.evidence code {font-size:0.78rem;}
.kpis {display:flex;flex-wrap:wrap;gap:0.75rem 1.75rem;align-items:flex-end;margin:0.25rem 0 0.5rem;}
.kpi .k {font-size:0.75rem;opacity:0.7;text-transform:uppercase;letter-spacing:0.06em;}
.kpi .v {font-size:1.15rem;font-weight:700;font-variant-numeric:tabular-nums;white-space:nowrap;}
.kpi .v small {font-weight:500;opacity:0.7;}
</style>
""", unsafe_allow_html=True)


def kpis(items: list[tuple[str, str]]) -> str:
    """Compact label/value row that wraps instead of truncating (st.metric truncates in narrow columns)."""
    cells = "".join(f"<div class='kpi'><div class='k'>{k}</div><div class='v'>{v}</div></div>" for k, v in items)
    return f"<div class='kpis'>{cells}</div>"


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


def badge(disposition: str) -> str:
    fg, bg = STYLE[disposition]
    return f"<span class='badge' style='color:{fg};background:{bg}'>{disposition}</span>"


def evidence(ids: list[str]) -> str:
    return " ".join(f"`{i}`" for i in ids) if ids else "_no evidence cited_"


def format_event(e: dict) -> str:
    icon = ACTOR_ICON.get(e["actor"], "•")
    d = e["detail"]
    if e["actor"] == "TOOL":
        return f"{icon} `{e['action']}` → {d['summary']}"
    if e["actor"] == "CONTROL" and isinstance(d, dict) and "message" in d:
        return f"{icon} **{e['action']}**: {d['message']}"
    if e["actor"] == "CONTROL" and isinstance(d, dict) and "score" in d:
        return f"{icon} {e['action']}: score {d['score']} → {d['disposition']}"
    if e["actor"] == "AGENT" and isinstance(d, dict) and d.get("calls"):
        calls = ", ".join(c["tool"] for c in d["calls"])
        return f"{icon} {e['action']}: {calls}"
    return f"{icon} {e['action']}"


store = get_store()
ss = st.session_state
ss.setdefault("results", {})
ss.setdefault("evals", {})

# ----------------------------------------------------------------------------- sidebar
with st.sidebar:
    st.markdown("### Investigator")
    key_from_secrets = secret_key()
    engine = st.radio("Engine", ["LLM agent (Groq)", "Rules engine (offline)"],
                      index=0 if key_from_secrets else 1,
                      help="The LLM agent decides which tools to call and writes the case. The rules engine "
                           "runs a fixed plan over the same tools; it is also the automatic fallback.")
    use_llm = engine.startswith("LLM")
    model, api_key = C.DEFAULT_MODEL, key_from_secrets
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
            st.success("Groq API key configured on the server.", icon="🔑")
        elif not api_key:
            st.info("Get a free key at console.groq.com, or switch to the offline rules engine.")
    st.divider()
    st.markdown("**About this demo**")
    st.caption("All customers, transactions, sanctions entries and jurisdictions are synthetic. "
               "QK, QM and QV are fictional higher-risk jurisdictions. The policy is illustrative and "
               "modelled on UK practice.")
    st.caption(f"Data version `{store.data_version}` · prompt `{C.PROMPT_VERSION}`")
    st.divider()
    st.markdown(f"Built by **Peer Arif** · [Source code and documentation]({REPO_URL})")


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


def status_for(alert_id: str) -> str:
    r: CaseResult | None = ss.results.get(alert_id)
    if r is None:
        return "Open"
    if r.analyst_decision:
        return f"Decided: {r.analyst_decision['final_disposition']}"
    return f"Recommended: {r.decision['disposition']}"


# ----------------------------------------------------------------------------- header
st.title("🛡️ AML Alert Triage Agent")
st.caption("An agentic AI investigator for bank transaction monitoring and sanctions alerts. It gathers evidence "
           "with tools, checks sanctions and policy, and recommends **CLOSE**, **RFI** or **ESCALATE** for a human "
           "analyst to approve, with guardrails and a full audit trail.")

tab_inv, tab_eval, tab_how = st.tabs(["🔎 Investigate", "📊 Evaluation", "🧭 How it works"])


# ----------------------------------------------------------------------------- result view
def render_result(r: CaseResult) -> None:
    d = r.decision
    st.subheader(f"Recommendation for {r.alert_id}")
    st.markdown(kpis([
        ("Agent recommends", badge(d["disposition"])),
        ("Risk score", f"{d['risk_score']}<small>/100</small>"),
        ("Rules engine (2nd line)", f"{r.rules['disposition']} <small>{r.rules['score']}</small>"),
        ("Grounded citations", f"{r.grounding['grounded']}/{r.grounding['cited']}"),
    ]), unsafe_allow_html=True)
    m = r.metrics
    st.caption(f"Investigator: **{r.mode}** ({r.model}) · confidence {d['confidence']} · {m['tool_calls']} tool calls"
               + (f" · {m['llm_calls']} LLM calls · {m['prompt_tokens'] + m['completion_tokens']:,} tokens"
                  if m.get("llm_calls") else "") + f" · {m['latency_ms'] / 1000:.1f}s · run `{r.run_id}`")

    if r.requires_senior_review:
        st.warning("Senior analyst review required before this case is actioned.", icon="⚠️")
    for c in r.controls:
        text = f"**{c['control']}** · {c['message']}"
        if c["severity"] == "CRITICAL":
            st.error(text, icon="🛑")
        elif c["severity"] in {"WARN", "HIGH"}:
            st.warning(text, icon="🛡️")
        else:
            st.info(text, icon="🛡️")

    st.markdown("#### Key findings")
    if d["key_findings"]:
        for kf in d["key_findings"]:
            st.markdown(f"- {kf['finding']}  \n  <span class='evidence'>Evidence: {evidence(kf['evidence'])}</span>",
                        unsafe_allow_html=True)
    else:
        st.markdown("_None_")
    st.markdown("#### Mitigating factors")
    if d["mitigating_factors"]:
        for mf in d["mitigating_factors"]:
            st.markdown(f"- {mf['factor']}  \n  <span class='evidence'>Evidence: {evidence(mf['evidence'])}</span>",
                        unsafe_allow_html=True)
    else:
        st.markdown("_None_")
    st.markdown("#### Recommended actions")
    for a in d["recommended_actions"]:
        st.markdown(f"- {a}")
    st.caption("Policy references: " + (", ".join(d["policy_refs"]) or "none"))

    # ---- human in the loop
    st.markdown("#### Analyst decision")
    if r.analyst_decision:
        a = r.analyst_decision
        st.success(f"**{a['action']}** by {a['analyst']}: final disposition **{a['final_disposition']}**"
                   + (f". Reason: {a['reason']}" if a.get("reason") else "."), icon="✅")
    else:
        with st.form(key=f"decide_{r.run_id}"):
            narrative = st.text_area("Case narrative (edit before filing if needed)", value=d["narrative"], height=200)
            col_a, col_b = st.columns(2)
            analyst = col_a.text_input("Analyst", value="L1 Analyst")
            choice = col_b.radio("Decision", ["Approve recommendation", "Override"], horizontal=True)
            override_to = st.selectbox("If overriding, final disposition", C.DISPOSITIONS,
                                       index=C.DISPOSITIONS.index(d["disposition"]))
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
    dl1.download_button("⬇️ Case report (.md)", render_case_report(r), file_name=f"{r.alert_id}_case_report.md",
                        mime="text/markdown", width="stretch")
    dl2.download_button("⬇️ Full audit trail (.json)", json.dumps(r.to_dict(), indent=2, default=str),
                        file_name=f"{r.alert_id}_{r.run_id}_audit.json", mime="application/json", width="stretch")

    with st.expander("Investigation trace (every step the agent took)"):
        for e in r.audit.events:
            st.markdown(f"`{e['elapsed_ms']:>8.0f} ms` " + format_event(e))
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
    with st.expander("Rules engine score breakdown"):
        st.dataframe(pd.DataFrame(r.rules["drivers"]), hide_index=True, width="stretch")
        st.caption(f"Thresholds: ESCALATE ≥ {C.ESCALATE_THRESHOLD}, RFI ≥ {C.RFI_THRESHOLD}.")
    if r.mode == "llm" and r.proposed_decision["disposition"] != r.decision["disposition"]:
        st.caption(f"Before controls the model proposed {r.proposed_decision['disposition']}.")


# ----------------------------------------------------------------------------- investigate tab
with tab_inv:
    alerts = store.alert_list()
    left, right = st.columns([0.4, 0.6], gap="large")
    with left:
        st.subheader("Alert queue")
        st.dataframe(pd.DataFrame([{
            "Alert": a["alert_id"], "Customer": store.get_customer(a["customer_id"])["name"],
            "Priority": a["priority"], "Status": status_for(a["alert_id"])} for a in alerts]),
            hide_index=True, width="stretch", height=300)
        alert_ids = [a["alert_id"] for a in alerts]
        alert_id = st.selectbox("Alert to investigate", alert_ids, index=alert_ids.index(DEMO_CASES[0][0]),
                                format_func=lambda x: f"{x} · {store.get_customer(store.get_alert(x)['customer_id'])['name']}")
        alert = store.get_alert(alert_id)
        customer = store.get_customer(alert["customer_id"])
        with st.container(border=True):
            st.markdown(f"**{alert['rule_id']}** · {alert['rule_name']}")
            st.write(alert["trigger_summary"])
            st.markdown(kpis([("Customer type", customer["customer_type"].title()),
                              ("KYC risk", customer["risk_rating"].title()),
                              ("Priority", alert["priority"].title()),
                              ("Raised", alert["alert_date"])]), unsafe_allow_html=True)
            st.caption(f"{customer['name']} · {customer['occupation']} · customer since "
                       f"{customer['account_opened'][:4]}" + (" · PEP" if customer.get("pep") else ""))
        run = st.button("▶ Run investigation", type="primary", width="stretch")
        with st.expander("Customer transactions (last 90 days)"):
            end =datetime.combine(date.fromisoformat(alert["alert_date"]), datetime.max.time())
            rows = store.transactions(customer["customer_id"], end - timedelta(days=90), end)
            df = pd.DataFrame([public_txn(t) for t in rows])
            if not df.empty:
                df["trigger"] = df["txn_id"].isin(alert["trigger_txn_ids"])
                st.dataframe(df[["txn_id", "timestamp", "direction", "amount", "channel", "counterparty",
                                 "counterparty_country", "reference", "trigger"]].iloc[::-1],
                             hide_index=True, width="stretch", height=280)

    with right:
        if run:
            agent, err = build_agent()
            if err:
                st.error(err)
            else:
                with st.status(f"Investigating {alert_id}…", expanded=True) as status:
                    result = agent.run(alert_id, on_event=lambda e: status.write(format_event(e)))
                    status.update(label=f"Investigation complete · {result.metrics['tool_calls']} tool calls · "
                                        f"{result.metrics['latency_ms'] / 1000:.1f}s",
                                  state="complete", expanded=False)
                ss.results[alert_id] = result
        result = ss.results.get(alert_id)
        if result is None:
            with st.container(border=True):
                st.markdown("#### Suggested walkthrough")
                st.markdown("Choose a case on the left and press **Run investigation**. Each run takes a few seconds.")
                st.markdown("\n".join(f"- **{aid}** · {desc} → expect **{exp}**" for aid, desc, exp in DEMO_CASES))
                st.caption("The agent opens the case file, decides which evidence and policy it needs, and recommends "
                           "CLOSE, RFI or ESCALATE with cited evidence. Controls check the recommendation, and you "
                           "approve or override it as the analyst.")
        else:
            render_result(result)


# ----------------------------------------------------------------------------- evaluation tab
def render_eval(name: str, s: dict) -> None:
    st.markdown(f"##### {name}")
    e1, e2, e3, e4, e5 = st.columns(5)
    e1.metric("Accuracy", f"{s['accuracy']:.0%}")
    e2.metric("Escalation recall", f"{s['escalation_recall']:.0%}" if s["escalation_recall"] is not None else "n/a")
    e3.metric("Missed escalations", s["missed_escalations"])
    e4.metric("Grounding rate", f"{s['grounding_rate']:.0%}")
    e5.metric("Avg latency", f"{s['avg_latency_ms'] / 1000:.1f}s")
    cm = pd.DataFrame(s["confusion"]).T[list(C.DISPOSITIONS)]
    cm.index.name = "label ↓ / recommended →"
    a, b = st.columns([0.35, 0.65])
    a.dataframe(cm, width="stretch")
    b.dataframe(pd.DataFrame(s["rows"])[["alert_id", "label", "predicted", "rules_engine", "risk_score",
                                         "controls", "correct"]], hide_index=True, width="stretch")
    st.caption(f"Controls changed {s['control_overrides']} recommendation(s) · LLM/rules disagreements "
               f"{s['dual_control_disagreements']} · fallbacks {s['fallbacks']} · avg {s['avg_tool_calls']} tool calls"
               + (f", {s['avg_llm_calls']} LLM calls, {s['avg_tokens']:,} tokens per case" if s["avg_llm_calls"] else ""))
    st.download_button("⬇️ Report (.md)", to_markdown(s), file_name=f"eval_{name.replace(' ', '_')}.md",
                       key=f"dl_{name}")
    with st.expander("Raw results (JSON)"):
        st.code(json.dumps(s, indent=1, default=str), language="json")


def saved_run_label(stem: str, s: dict) -> str:
    if s.get("mode") == "llm":
        return f"LLM agent · {s.get('model')} · saved run ({s.get('run_at', '')[:10]})"
    return "Offline rules engine · saved run"


with tab_eval:
    st.subheader("Evaluation against analyst-labelled alerts")
    st.write("Each of the 13 alerts has a disposition set by a senior analyst. The evaluation runs the agent on "
             "all of them and measures accuracy, **escalation recall** (the costliest AML error is closing "
             "something that should have been escalated), **grounding** (every cited ID must come from a tool) "
             "and how often the controls had to step in.")
    st.caption("The rules engine was calibrated on this synthetic dataset, so its score is a sanity check; the LLM "
               "agent's score is the informative one. In the saved LLM run, both errors disagreed with the rules "
               "engine and were routed to a senior analyst by CTRL-DUAL. Batch latency includes waits for Groq's "
               "free-tier rate limit; a single case takes 1-3 seconds.")
    # Saved runs: inserted rules-first so the LLM run is shown first (newest entries render at the top).
    for path in sorted(RESULTS_DIR.glob("*.json"), key=lambda p: p.stem.startswith("llm")) if RESULTS_DIR.exists() else []:
        saved = json.loads(path.read_text())
        ss.evals.setdefault(saved_run_label(path.stem, saved), saved)
    b1, b2 = st.columns(2)
    if b1.button("Run evaluation · rules engine", width="stretch"):
        with st.spinner("Running 13 investigations…"):
            ss.evals["rules engine (this session)"] = evaluate(TriageAgent(store, mode="rules"), store)
    if b2.button("Run evaluation · LLM agent", width="stretch", disabled=not (use_llm and api_key),
                 help="Runs all 13 alerts through the LLM agent. On Groq's free tier this takes a few minutes and uses "
                      "about a third of the daily token allowance, so avoid running it just before a live demo."):
        agent, err = build_agent()
        if err:
            st.error(err)
        else:
            bar = st.progress(0.0, text="Starting…")
            ss.evals[f"LLM {model} (this session)"] = evaluate(
                agent, store, progress=lambda i, n, row: bar.progress(i / n, text=f"{row['alert_id']}: "
                                                                                 f"{row['predicted']} ({i}/{n})"))
            bar.empty()
    for name, s in reversed(list(ss.evals.items())):
        with st.container(border=True):
            render_eval(name, s)


# ----------------------------------------------------------------------------- how it works tab
with tab_how:
    st.subheader("Architecture")
    st.graphviz_chart("""
digraph {
  rankdir=LR; bgcolor="transparent";
  node [shape=box, style="rounded,filled", fillcolor="#f2f4f7", color="#98a2b3", fontname="Helvetica", fontsize=11];
  edge [color="#667085", fontname="Helvetica", fontsize=9];
  alert [label="Monitoring / screening\\nalert"];
  agent [label="LLM investigator\\n(Groq tool-calling loop)", fillcolor="#e0eaff"];
  tools [label="Tools\\nopen_case: alert · KYC profile ·\\ntypology detectors · sanctions screening\\nget_transactions · search_policy (BM25 RAG)"];
  rules [label="Independent rules engine\\n(second line)"];
  controls [label="Controls\\ngrounding · sanctions hard stop\\ndual control · evidence checks", fillcolor="#fef0c7"];
  analyst [label="Human analyst\\napprove / override", fillcolor="#d1fadf"];
  audit [label="Audit trail", shape=cylinder];
  alert -> agent; agent -> tools [dir=both, label="function calls"]; alert -> rules;
  agent -> controls [label="proposed decision"]; rules -> controls [label="score"];
  controls -> analyst; analyst -> audit; tools -> audit [style=dashed]; controls -> audit [style=dashed];
}
""", width="stretch")
    st.markdown("""
**How an investigation runs**

1. The LLM receives the alert ID and a versioned system prompt describing the procedure, dispositions and rules.
2. It calls `open_case`, which returns the alert, KYC profile, typology detector findings and sanctions screening.
3. It decides what else it needs: `get_transactions` to inspect specific activity, `search_policy` to find the
   policy sections to cite. A clear-cut case may need neither.
4. Tool results go back into the conversation; the loop repeats until the model returns a JSON decision.
5. Separately, a deterministic rules engine scores the same case without seeing the model's answer.
6. Controls check the proposal before any human sees it, then an analyst approves or overrides it. Every step is logged.

**Why one `open_case` call instead of many small tools?** The model calls one tool per turn and each turn re-sends
the conversation. Bundling the case file cut a typical investigation from about 24,000 tokens and 140 seconds to
about 6,000 tokens and 2–3 seconds on Groq's free tier, with the same decisions.

**Controls**

| Control | What it does |
|---|---|
| `CTRL-SCHEMA` | Invalid or unparseable model output is repaired once, then falls back to the rules investigator |
| `CTRL-GROUNDING` | Every cited ID (TX, SAN, DOC, POL) must have been returned by a tool in this case; invented ones are removed and flagged |
| `CTRL-SANCTIONS` | A potential true sanctions match can never be closed or sent for RFI by the agent |
| `CTRL-DUAL` | If the LLM and the rules engine disagree, the case goes to a senior analyst |
| `CTRL-EVIDENCE` | Escalations need cited evidence; closures need mitigating evidence when the risk score is elevated |
| `CTRL-TIPOFF` | An escalation that suggests contacting the customer is flagged for MLRO approval (tipping-off risk) |
| `CTRL-HITL` | Nothing is actioned automatically; overrides need a reason |
| Scope | Tools only return data for the customer on the alert (data minimisation) |
| `CTRL-FALLBACK` | If Groq is unavailable or rate-limited, the rules investigator completes the case and says so |

**What this is not:** a production system. Data is synthetic, the policy is illustrative, and BM25 stands in for
a production retrieval stack. The design choices (tool scoping, grounding, dual control, human approval, audit)
are the ones a bank's model risk and compliance teams would expect to see.
""")
