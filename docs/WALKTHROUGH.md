# Walkthrough and design FAQ

## Five-minute walkthrough

Open the [live app](https://aml-triage-agent-peerarif.streamlit.app) with the **LLM agent** engine selected.

1. **ALT-1005 · student account used as a money mule.** Run the investigation and open the trace. The agent calls
   `open_case`, decides whether it needs `get_transactions` or `search_policy`, and returns **ESCALATE**: seven
   unrelated payers, 99% of funds out within 48 hours to a crypto exchange and cash. Every finding cites
   transaction IDs, and the independent rules engine agrees.
2. **ALT-1002 · £287k credit to a nurse.** It looks alarming against a £41k salary, but the agent finds the
   documented house sale (completion statement `DOC-5531`) and recommends **CLOSE**, often in a single tool call.
   Clearing false positives with evidence is where most Level 1 analyst time goes.
3. **ALT-1003 · payment to a sanctioned entity.** The beneficiary matches `SAN-0041` on name and country.
   `CTRL-SANCTIONS` guarantees ESCALATE and a payment hold whatever the model says. Compare **ALT-1004**: the
   customer's name is similar to a listed person, but date of birth and nationality differ, so it is a false positive.
4. **Human in the loop.** Override a recommendation with a reason and download the case report and the audit trail.
5. **Evaluation tab.** Accuracy, escalation recall, missed escalations and grounding against analyst labels, for both
   the LLM agent and the rules engine.

## Design FAQ

**Why not let the LLM do everything?**
Detection and sanctions matching must be deterministic, testable and explainable to model risk and audit. Code does
those; the LLM weighs mixed evidence and drafts the case, and controls check its output.

**How are hallucinations contained?**
Tools are the only source of facts. `CTRL-GROUNDING` removes any identifier that no tool returned in the case, the
analyst sees the evidence behind every finding, and invalid output falls back to the rules investigator.

**What happens when the model and the rules engine disagree?**
`CTRL-DUAL` routes the case to a senior analyst. The two are independent (neither sees the other's answer), so
disagreement is a real signal.

**Why does the Groq agent use one `open_case` tool instead of many small ones?**
Cost and latency. gpt-oss calls one tool per turn and each turn re-sends the conversation. With seven small tools a
case used about 24,000 tokens and took 140 seconds on the free tier; bundling the case file cut that to about 6,000
tokens and 2–3 seconds, with the same decisions. The granular tools remain for models that call tools in parallel.

**Why Groq and gpt-oss-120b?**
Fast inference with OpenAI-compatible tool calling, and prompt caching on the gpt-oss models. Groq retired the
previous default (Llama 3.3 70B) for free accounts in August 2026, so the app reads the account's available models
and switches automatically if one is retired. The agent depends on a small response type, so moving to Azure OpenAI,
Amazon Bedrock or a private deployment is a client change, not an agent change.

**How would this go to production in a bank?**
See [GOVERNANCE.md](GOVERNANCE.md): model risk validation under PRA SS1/23, a private model deployment with data
minimisation, integration with the case management system, monitoring of override and disagreement rates, red-teaming
for prompt injection through payment references, and back-testing on historical alert outcomes.

**What would come next?**
Claim-level evidence checks, hybrid retrieval over the full policy library, a state machine for multi-step Level 2
investigations, network analysis across linked customers, and an evaluation set built from real historical dispositions.

## Glossary

- **L1 / L2:** first-line alert review; deeper investigation team.
- **MLRO:** Money Laundering Reporting Officer (the nominated officer who decides on SARs).
- **SAR:** Suspicious Activity Report, submitted to the National Crime Agency. **DAML:** Defence Against Money
  Laundering, consent requested from the NCA before proceeding with a suspected transaction.
- **Tipping off:** telling a customer that a SAR or investigation is in progress; an offence under POCA s.333A.
- **KYC / CDD / EDD:** know your customer; customer due diligence; enhanced due diligence for higher-risk customers.
- **PEP:** politically exposed person. **OFSI:** Office of Financial Sanctions Implementation (HM Treasury).
- **RFI:** request for information from the customer or relationship manager.
- **Typologies:** recognised laundering patterns such as structuring, money mules, round-tripping and funnel accounts.
