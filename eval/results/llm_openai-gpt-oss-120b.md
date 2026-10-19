# Evaluation report: llm (openai/gpt-oss-120b)

Run at 2026-10-19T08:25:46+00:00 | prompt triage-v1.5 | 13 labelled alerts

| Metric | Value |
|---|---|
| Accuracy | 85% |
| Escalation recall | 83% |
| Missed escalations (ESCALATE labelled, CLOSE recommended) | 0 |
| Macro F1 | 0.822 |
| Grounding rate (cited IDs returned by a tool) | 99% |
| Recommendations changed by controls | 0 |
| LLM vs rules engine disagreements | 2 |
| Fallbacks to rules investigator | 0 |
| Avg tool calls / LLM calls / tokens | 1.5 / 2.5 / 4493 |
| Avg latency | 24682 ms |

## Confusion matrix (rows = label, columns = recommendation)

| | CLOSE | RFI | ESCALATE |
|---|---|---|---|
| **CLOSE** | 4 | 0 | 0 |
| **RFI** | 1 | 2 | 0 |
| **ESCALATE** | 0 | 1 | 5 |

## Per-alert results

| Alert | Customer | Label | Recommended | Rules engine | Score | Controls | Correct |
|---|---|---|---|---|---|---|---|
| ALT-1001 | Gareth Pryce | ESCALATE | ESCALATE | ESCALATE | 80 | CTRL-TIPOFF | yes |
| ALT-1002 | Priya Raman | CLOSE | CLOSE | CLOSE | 10 | - | yes |
| ALT-1003 | Brightwater Marine Supplies Ltd | ESCALATE | ESCALATE | ESCALATE | 85 | CTRL-GROUNDING, CTRL-SANCTIONS, CTRL-TIPOFF | yes |
| ALT-1004 | Rebecca Hartmann | CLOSE | CLOSE | CLOSE | 10 | - | yes |
| ALT-1005 | Liam Doherty | ESCALATE | ESCALATE | ESCALATE | 85 | - | yes |
| ALT-1006 | Meridian Spice Imports Ltd | CLOSE | CLOSE | CLOSE | 30 | - | yes |
| ALT-1007 | The Copper Kettle Cafe Ltd | RFI | CLOSE | RFI | 15 | CTRL-DUAL | NO |
| ALT-1008 | Jonathan Ellery-Pike | ESCALATE | ESCALATE | ESCALATE | 85 | - | yes |
| ALT-1009 | Northgate Fabrication Ltd | ESCALATE | RFI | ESCALATE | 40 | CTRL-DUAL | NO |
| ALT-1010 | Harold Benning | ESCALATE | ESCALATE | ESCALATE | 85 | CTRL-TIPOFF | yes |
| ALT-1011 | Aisha Karim | CLOSE | CLOSE | CLOSE | 20 | - | yes |
| ALT-1012 | Hope Bridge Relief Foundation | RFI | RFI | RFI | 65 | - | yes |
| ALT-1013 | Lumen Ticketing Ltd | RFI | RFI | RFI | 45 | - | yes |
