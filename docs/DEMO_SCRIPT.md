# Demo script and interview prep

## 5-minute walkthrough

1. **The problem (30s).** "Banks raise thousands of monitoring alerts. Most are false positives, but each needs an
   L1 analyst to gather evidence and write a rationale. I built an agent that does the investigation and drafts the
   case, with controls so a human stays in charge."
2. **Run ALT-1005, the student mule (60s).** Point at the live trace: the model pulls the profile, runs the detectors and
   screening (often in parallel), then inspects transactions and searches policy. Show the result: ESCALATE, evidence IDs on every finding, policy
   references, recommended actions, and the rules engine agreeing.
3. **Run ALT-1002, the house sale (45s).** A £287k credit to a nurse looks alarming, but the agent finds the documented
   sale and closes it, citing DOC-5531. "This is where most analyst time goes: proving the false positives."
4. **Run ALT-1003, the sanctions hit (45s).** Explain CTRL-SANCTIONS: even if the model said close, the control forces
   escalation. Then ALT-1004 for the contrast: name match, but DOB and nationality differ, so it's a false positive.
5. **Human in the loop (30s).** Override a recommendation with a reason, download the case report and audit trail.
6. **Evaluation tab (30s).** Accuracy, escalation recall, grounding rate. "Escalation recall is the metric I'd
   optimise first, because missing a suspicious case is the expensive error."
7. **Close (30s).** "The same pattern applies to KYC refresh, trade surveillance alerts, reconciliation breaks and
   payment investigations in capital markets."

## Questions you will likely get

**Why not let the LLM do everything?**
Detection and sanctions matching need to be deterministic, testable and explainable to model risk. The LLM is used
for judgement over mixed evidence and for writing, and it's checked by controls.

**How do you stop hallucinations?**
Three layers: tools are the only source of facts, the grounding control removes any ID no tool returned, and the
analyst sees the evidence for every finding. Invalid output falls back to the rules investigator.

**What happens if the model and the rules disagree?**
CTRL-DUAL routes the case to a senior analyst. The two are independent, so disagreement is informative.

**How would this go to production in a bank?**
Model risk validation (independent testing, documented limitations), PII redaction or a private model deployment,
integration with the case management system, monitoring of override rates and drift, red-teaming for prompt
injection (for example malicious text in a payment reference), and back-testing on historical alert outcomes.

**Why Groq and Llama?**
Fast, cheap inference with OpenAI-compatible tool calling. The agent only depends on a small response type, so
switching to Azure OpenAI, Bedrock or an on-premise model is a client change, not an agent change.

**What would you add next?**
Hybrid retrieval over the full policy library, a LangGraph-style state machine for multi-step L2 investigations,
network analysis across linked customers, and an evaluation set built from real historical dispositions.

## Banking terms to be comfortable with

- **L1 / L2 / MLRO**: first-line alert review, deeper investigation team, Money Laundering Reporting Officer.
- **SAR**: Suspicious Activity Report, submitted to the National Crime Agency in the UK. **Tipping off**: telling the
  customer about a SAR, which is an offence.
- **KYC / CDD / EDD**: know your customer, customer due diligence, enhanced due diligence for higher-risk customers.
- **PEP**: politically exposed person. **OFSI**: UK Office of Financial Sanctions Implementation.
- **Typologies**: structuring, mule accounts, round-tripping, funnel accounts, trade-based laundering.
