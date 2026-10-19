# Governance, model risk and UK regulatory alignment

This note explains how the AML Alert Triage Agent is designed to be governable in a UK bank. It maps design
choices to the regulatory obligations and model risk expectations they support. The project is a demonstration on
synthetic data, and this mapping is illustrative, not legal or compliance advice.

## Where the agent sits in the financial crime operating model

| Line | Role | In this project |
|---|---|---|
| Transaction monitoring and screening | Systems raise alerts | 13 synthetic alerts from monitoring rules (`TM-...`) and screening (`SCR-...`) |
| Level 1 alert review | Gather evidence, decide CLOSE / RFI / ESCALATE, document the rationale | **The agent drafts this work; the analyst approves or overrides it** |
| Level 2 investigations | Deeper investigation of escalated cases | Receives ESCALATE recommendations with a cited case narrative |
| MLRO / Nominated Officer | Decides whether to submit a SAR to the NCA; DAML requests | Referenced in recommended actions; customer contact on escalations needs MLRO approval |
| Sanctions team | Potential true matches, freezing, OFSI reporting | Potential true matches can only be escalated (`CTRL-SANCTIONS`) |

The agent never closes, files or contacts anyone. Every case ends in `PENDING_ANALYST_REVIEW`.

## UK obligations the design supports

| Obligation | Source | How the design supports it |
|---|---|---|
| Report knowledge or suspicion of money laundering (regulated sector) | Proceeds of Crime Act 2002, s.330 | Typology detectors and escalation rules raise suspicious activity; the narrative states who, what, when, where funds came from and went to, and why |
| No tipping off | POCA 2002, s.333A | Prompt forbids suggesting customer contact on escalations; `CTRL-TIPOFF` flags any action that would contact the customer during an escalation for MLRO approval |
| Defence Against Money Laundering consent | POCA 2002, s.335 and s.338 | Policy POL-SAR-01 covers holding a transaction while a DAML request is considered |
| Customer due diligence and enhanced due diligence | Money Laundering Regulations 2017, regs 28 and 33 | KYC profile, expected activity and EDD dates are evidence; higher-risk jurisdiction activity is mitigated only by an EDD review less than 12 months old |
| Politically exposed persons | MLR 2017, reg 35 | PEP status, source of wealth and unexplained funds detector; escalation for senior review |
| Record keeping (at least five years) | MLR 2017, reg 40 | Append-only audit trail of every tool call, control and analyst decision, exportable as JSON; case report as Markdown |
| Financial sanctions: freeze, do not make funds available, report | Sanctions and Anti-Money Laundering Act 2018 and the regulations made under it; OFSI guidance | Fuzzy name screening with secondary identifiers; a potential true match forces ESCALATE and a payment hold |
| Industry guidance on typologies and controls | JMLSG Guidance; FCA Financial Crime Guide | Detector set covers structuring, mules, round-tripping, dormancy and account takeover, PEPs, higher-risk jurisdictions, charities and funnel accounts |
| Data minimisation; human involvement in significant decisions | UK GDPR, Articles 5(1)(c) and 22 | Tools are scoped to the customer on the alert; no decision takes effect without an analyst |

## Model risk management (PRA SS1/23)

SS1/23 sets out five principles for model risk management in banks. An LLM agent that drafts financial crime
decisions falls within scope as a model, and the design maps to each principle as follows.

| SS1/23 principle | Design response |
|---|---|
| 1. Model identification and risk classification | Every run records the model ID, prompt version (`triage-v1.5`) and data version hash, so each decision traces to a specific configuration. The agent supports L1 decisions in a regulated process, so it is high materiality. |
| 2. Governance | Human approval of every case; overrides need a reason and are logged; senior review is triggered by control flags. |
| 3. Development, implementation and use | Deterministic detectors and screening are unit tested (74 tests in CI); the LLM is limited to judgement and drafting; a versioned prompt; graceful fallback when the provider is unavailable. |
| 4. Independent validation | An evaluation harness scores the agent against analyst-labelled alerts (accuracy, escalation recall, missed escalations, grounding). An independent rules engine scores every case without seeing the model's answer (`CTRL-DUAL`). |
| 5. Model risk mitigants | Controls run before any human sees a recommendation: grounding, sanctions hard stop, evidence checks, tipping-off flag and case scoping. Fallback to the rules investigator is visible in the trace. |

## Known limitations and what production would add

- **Data:** synthetic and small. Production needs back-testing on historical alerts with known outcomes, and
  thresholds tuned on that data, not on the evaluation set.
- **Grounding checks existence, not meaning:** a cited transaction must exist in the case, but the control does not
  yet check that it supports the specific sentence. Next step: claim-level evidence checks.
- **Prompt injection:** transaction references and counterparty names are customer-controlled text that reaches
  the model. Production would treat tool output as data (delimiting, sanitising) and red-team this route.
- **Personal data and hosting:** a bank would run the model in an approved environment (private deployment or an
  enterprise endpoint with data processing terms) and redact data the decision does not need.
- **Monitoring:** override rates, CTRL-DUAL disagreement rates, fallback rates and drift in disposition mix, with
  thresholds that trigger revalidation.
- **Retrieval:** BM25 over one illustrative policy; production would use hybrid retrieval over the full policy and
  procedure library, with document versioning.
