# Illustrative AML & Sanctions Alert Handling Policy

This policy is written for a demonstration project. It is modelled on the general shape of UK
financial-crime controls (Proceeds of Crime Act 2002, Money Laundering Regulations 2017, sanctions
regimes administered by OFSI) but it is simplified and is not legal or compliance advice. Each section
has an ID so the agent can cite it.

## [POL-GOV-01] Alert triage workflow and dispositions

Level 1 (L1) analysts review every transaction monitoring and screening alert and choose one of three
dispositions. CLOSE means the activity is explained by information already held and no further action
is needed; the rationale must name the evidence relied on. RFI (request for information) means the
activity is not clearly suspicious but cannot be explained from information on file, so the analyst
requests an explanation or documents from the customer or relationship manager and reassesses within
14 days. ESCALATE means there are reasonable grounds to suspect money laundering, terrorist financing,
sanctions exposure or fraud, and the case is referred to the Level 2 investigations team or the MLRO.
An automated assistant may recommend a disposition, but a human analyst must approve every outcome.

## [POL-GOV-02] Evidence and record keeping

Every disposition must be supported by evidence held on file: transaction identifiers, screening
results, customer due diligence records and documents. Statements that cannot be traced to a record
must not be relied on. The full investigation trail, including automated steps, must be retained for
at least five years after the end of the business relationship.

## [POL-CASH-01] Cash deposits and structuring

Structuring is splitting cash into amounts that stay below internal reporting or review thresholds.
The bank reviews cash deposits that total £10,000 or more within ten days. Indicators include several
deposits between £8,000 and £9,999 in a short period, use of multiple branches, deposits out of line
with the customer's normal takings, and cash quickly consolidated into a single large payment.
Structuring indicators with no credible explanation should be escalated.

## [POL-MULE-01] Money mule and pass-through activity

Mule accounts receive funds from third parties and move them on quickly, often to cryptocurrency
exchanges, other personal accounts or cash. Indicators include several new, unrelated payers in a
short period, most of each credit leaving the account within 48 hours, references that do not fit
the customer's profile, and young or financially vulnerable customers such as students. Confirmed
pass-through patterns should be escalated and the account considered for restriction.

## [POL-SAN-01] Sanctions screening

Customers and payment counterparties are screened against sanctions lists. A name similarity alone
does not confirm a match. Analysts compare secondary identifiers: date of birth and nationality for
individuals, country and address for entities. Where secondary identifiers clearly differ, the alert
can be closed as a false positive with the differing identifiers recorded.

## [POL-SAN-02] Potential true sanctions matches

Where the name matches and at least one secondary identifier matches, or where no secondary
identifiers are available to rule the match out, the case is a potential true match. L1 analysts must
not close it. The payment must be held and the case referred immediately to the sanctions team, which
decides whether to freeze funds and report to OFSI.

## [POL-JUR-01] Higher-risk jurisdictions

Transactions involving higher-risk jurisdictions require enhanced due diligence (EDD). Activity is
acceptable when the counterparty and purpose were verified in an EDD review completed within the last
12 months and the payment is consistent with that review in amount, frequency and counterparty. New or
unverified counterparties in higher-risk jurisdictions should be investigated further.

## [POL-PEP-01] Politically exposed persons

PEPs are subject to EDD, including documented source of wealth and source of funds. Large or unusual
credits that are not consistent with the documented source of wealth, especially from offshore
companies or with vague references such as "advisory services", must be escalated for senior review
and SAR consideration.

## [POL-VEL-01] Activity inconsistent with the customer profile

Analysts compare activity with the expected activity recorded at onboarding or the last review. A
sustained increase in turnover above 250% of expected, or single credits that are large relative to
declared income, require an explanation. If the reason is not on file and the activity is not
otherwise suspicious, raise an RFI. If the change is documented, close with reference to the evidence.

## [POL-DOR-01] Dormant account reactivation

Accounts with no customer-initiated activity for more than 180 days are dormant. Sudden reactivation
with large credits, especially after recent changes to contact details or new payees, can indicate
account takeover or that the account is being used as a mule. Such cases should be escalated and the
account protected.

## [POL-CIRC-01] Round-tripping and circular flows

Round-tripping is moving funds out and back through the same or related parties to create the
appearance of legitimate business or to layer funds. Indicators include repeated round-amount payments
to a related company, similar amounts returned within days, and changing payment references such as
"loan", "investment" and "capital" without supporting agreements. Unexplained circular flows should be
escalated.

## [POL-NPO-01] Charities and non-profit organisations

Charities operating in or near conflict areas can be abused for terrorist financing. Payments should go
to approved beneficiaries and implementing partners recorded in the charity's EDD file. Payments to
private individuals or unlisted parties require supporting documents such as a contract and trustee
approval. Without them, raise an RFI; escalate if the explanation is inadequate.

## [POL-FUN-01] Funnel accounts and new business accounts

Funnel activity is many payers sending funds into one account. For new business accounts, many small
credits from individuals can be legitimate trade, but they should match how the business said it would
be paid. If the pattern differs from the declared business model, raise an RFI for an explanation and
records. Combined with rapid onward movement, treat it as mule activity.

## [POL-MIT-01] Mitigating evidence

Mitigating evidence includes documented expected events (for example a property sale with a completion
statement on file), verified changes of employment, established counterparties confirmed in EDD, and
supporting documents such as invoices and contracts. Mitigation applies only when the evidence matches
the activity in counterparty, amount and timing.

## [POL-SAR-01] Escalation narratives and tipping off

Escalation narratives should explain who is involved, what happened, when, where the funds came from
and went to, and why the activity is suspicious, citing transaction identifiers. The customer must not
be told that a referral or SAR is being considered (tipping off is an offence). The MLRO decides
whether to submit a SAR to the National Crime Agency.
