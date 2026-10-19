# Evaluation report: rules (rules-engine)

Run at 2026-10-19T07:52:27+00:00 | prompt triage-v1.4 | 13 labelled alerts

| Metric | Value |
|---|---|
| Accuracy | 100% |
| Escalation recall | 100% |
| Missed escalations (ESCALATE labelled, CLOSE recommended) | 0 |
| Macro F1 | 1.0 |
| Grounding rate (cited IDs returned by a tool) | 100% |
| Recommendations changed by controls | 0 |
| LLM vs rules engine disagreements | 0 |
| Fallbacks to rules investigator | 0 |
| Avg tool calls / LLM calls / tokens | 7.0 / 0.0 / 0 |
| Avg latency | 25 ms |

## Confusion matrix (rows = label, columns = recommendation)

| | CLOSE | RFI | ESCALATE |
|---|---|---|---|
| **CLOSE** | 4 | 0 | 0 |
| **RFI** | 0 | 3 | 0 |
| **ESCALATE** | 0 | 0 | 6 |

## Per-alert results

| Alert | Customer | Label | Recommended | Rules engine | Score | Controls | Correct |
|---|---|---|---|---|---|---|---|
| ALT-1001 | Gareth Pryce | ESCALATE | ESCALATE | ESCALATE | 95 | - | yes |
| ALT-1002 | Priya Raman | CLOSE | CLOSE | CLOSE | 0 | - | yes |
| ALT-1003 | Brightwater Marine Supplies Ltd | ESCALATE | ESCALATE | ESCALATE | 100 | CTRL-SANCTIONS | yes |
| ALT-1004 | Rebecca Hartmann | CLOSE | CLOSE | CLOSE | 5 | - | yes |
| ALT-1005 | Liam Doherty | ESCALATE | ESCALATE | ESCALATE | 100 | - | yes |
| ALT-1006 | Meridian Spice Imports Ltd | CLOSE | CLOSE | CLOSE | 5 | - | yes |
| ALT-1007 | The Copper Kettle Cafe Ltd | RFI | RFI | RFI | 25 | - | yes |
| ALT-1008 | Jonathan Ellery-Pike | ESCALATE | ESCALATE | ESCALATE | 100 | - | yes |
| ALT-1009 | Northgate Fabrication Ltd | ESCALATE | ESCALATE | ESCALATE | 60 | - | yes |
| ALT-1010 | Harold Benning | ESCALATE | ESCALATE | ESCALATE | 100 | - | yes |
| ALT-1011 | Aisha Karim | CLOSE | CLOSE | CLOSE | 0 | - | yes |
| ALT-1012 | Hope Bridge Relief Foundation | RFI | RFI | RFI | 30 | - | yes |
| ALT-1013 | Lumen Ticketing Ltd | RFI | RFI | RFI | 25 | - | yes |
