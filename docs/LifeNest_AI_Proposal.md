# LifeNest AI — Research and Proposal Document

**Google Cloud AI Builder Cup 2026 · JAPAC**
**Theme: BFSI — Intelligent Risk, Fraud & Financial Experiences**
**Built in Malaysia**

> **Verification note.** Every market statistic in Section 2 has been checked against
> published reporting and is cited in Section 12. Every *financial parameter* in
> Section 5 is a policy value that changes — verify each against Bank Negara Malaysia,
> LHDN and KWSP before you submit, and record the date you checked. The engine treats
> all of them as configuration precisely because they move.

---

## 1. Executive summary

Malaysians buy homes at a price they guessed and discover eleven weeks later that no
bank will finance it. The budget is self-declared, never computed. Nobody checks debt
service ratio against real income until the loan application — which is the last step
of the journey, not the first.

LifeNest AI inverts the order. A buyer photographs their payslip and EPF statement in
a chat. Gemini reads the documents multimodally, extracts structured financial facts,
and calls a deterministic Malaysian rules engine that computes what a bank would
actually lend. The system then stress-tests that loan against the next decade of the
household — marriage, a first child, a rate rise, three months of job loss — and
returns a plain-language verdict in Malay, English or Mandarin.

Two things distinguish it from every calculator and portal in this market:

1. **Location is priced as a monthly cost, not shown as a map.** The same RM520,000
   in Semenyih and in Cheras are different loans once commute, tolls and the
   probability of a second car are added to the cashflow. No affordability tool in
   Malaysia does this.
2. **When the answer is no, the system produces a dated plan to yes.** Pay down
   RM18,000 of the car loan, or add a joint applicant, or wait until the study loan
   clears in month nine. A rejection that comes with a route is a product; a
   rejection alone is just bad news delivered faster.

The Gen AI is load-bearing but bounded: Gemini reasons, extracts and explains. It
never states a financial figure it computed itself. Every number in the verdict comes
from a called tool with a recorded input and output, so any advice can be
reconstructed and audited afterwards.

---

## 2. Problem statement and evidence

### 2.1 The failure mode

A first-time buyer sets a budget by browsing listing prices. An agent takes that
number at face value — it is not in the agent's interest to challenge it. The buyer
views units, falls in love with one, pays a booking fee. The loan application is
submitted. Weeks later the bank declines, or approves a materially smaller amount.
The booking fee is at risk, the unit is gone, and the only thing gained is a number
that could have been computed on day one from documents the buyer already had.

### 2.2 Scale of the problem

| Finding | Value | Source |
|---|---|---|
| Developers reporting financing difficulties among buyers, 2H2025 | **72%** of 166 respondents | REHDA Property Industry Survey H2 2025 (released March 2026) |
| Of those, share citing end-financing difficulty specifically | **83%** | REHDA, same survey |
| Loan rejection rate, RM500,001–RM700,000 band | most respondents report **31–45%**; average reported at **38%** | REHDA, same survey |
| New launch take-up rate, 2H2025 vs 1H2025 | **38% → 21%** (17,971 launched, 3,784 sold) | REHDA, same survey |
| Completed unsold residential units, Q1 2026 | **32,801** (RM16.37bn), sixth consecutive quarterly rise | NAPIC Q1 2026 |
| Completed unsold serviced apartments, Q1 2026 | **19,263** (RM16.52bn) | NAPIC Q1 2026 |
| Combined completed unsold stock | **52,000+ homes** | NAPIC Q1 2026 |

Cited reasons for the unsold stock, in developers' own ranking: end-financing loan
rejection first, then high prices, then low demand.

### 2.3 The interpretation that matters

The market reads this as a demand problem. It is not. Buyers want to buy. They are
being allowed to shop, view and commit at price points they were never going to be
financed for, and the whole chain — buyer, agent, developer, bank — discovers it at
the most expensive possible moment.

This is the framing that makes LifeNest a BFSI product rather than a proptech one.
The defect is in the *financial screening sequence*, not in property discovery.

---

## 3. Root cause analysis

**3.1 Budget is self-declared, never computed.**
Buyers name a figure derived from listing prices. Debt service ratio against real net
income and real existing commitments is calculated at the loan application — the last
step. Every input needed to compute it earlier is sitting in the buyer's payslip.

**3.2 The down-payment rules changed and nobody recalculated.**
EPF restructured into three accounts in May 2024. Housing withdrawals now draw on
Akaun Sejahtera, which receives a materially smaller share of contributions than the
old Account 2 did. Buyers are still planning against pre-2024 assumptions and
discovering the shortfall at the point of payment.

**3.3 Affordability is treated as a moment, not a decade.**
A bank asks whether you can service the loan today. A loan approved today is serviced
for up to thirty years, through a child, a job change and several rate moves. Nobody
stress-tests the household. Approval and sustainability are different questions, and
only one of them gets asked.

**3.4 (Added) Nobody prices the location.**
Affordability tools output one number for a purchase price, as if a house had no
address. In practice a longer commute is a permanent monthly deduction from
disposable income — fuel, tolls, vehicle depreciation, sometimes a second car. It is
the single largest uncosted variable in a Malaysian household budget, and it is
computable from an origin, a destination and a distance API.

---

## 4. Solution design

### 4.1 User journey

| Step | What happens | Who does it |
|---|---|---|
| 1 · Snap | Buyer photographs payslip and EPF statement in chat | Buyer |
| 2 · Extract | Documents read multimodally into structured fields | Gemini |
| 3 · Compute | DSR, capacity, EPF withdrawal, location cost | Deterministic tools |
| 4 · Simulate | Life scenarios re-run against household cashflow | Deterministic tools |
| 5 · Verdict | Plain-language risk report, with a plan if the answer is no | Gemini, narrating tool output |

Target: under ninety seconds end to end. No forms. No manual income entry. No
requirement that the buyer knows what DSR, SBR or Akaun Sejahtera mean.

### 4.2 The tool surface

| Tool | Responsibility |
|---|---|
| `compute_dsr()` | Debt service ratio on net income after statutory deductions, including all existing commitments |
| `loan_capacity()` | Maximum loan and purchase price given DSR headroom, tenure, margin of finance, reference rate |
| `epf_withdrawal()` | Available Akaun Sejahtera balance against down-payment and upfront-cost requirement |
| `location_cost()` | Monthly commute cost for a candidate location — distance, fuel, tolls, second-vehicle probability — returned as a cashflow deduction |
| `simulate_scenario()` | Re-runs the full cashflow under marriage, first child, rate rise, job loss; returns the month the buffer breaks |
| `plan_path_to_approval()` | If the verdict is no: the specific deltas required (debt paydown, joint applicant, wait period) with dates |
| `match_affordable_stock()` | Property matched to the computed figure — runs last, and only after a verdict |

### 4.3 The guardrail

Gemini never states a financial figure it computed itself. Every number that reaches
the user came from a tool call with a logged input and output. The interface exposes
a "show the working" panel listing each call, so a user, an agent or a regulator can
reconstruct the advice. Output is framed as indicative throughout, never as a lending
decision.

---

## 5. The Malaysian rules engine

Each parameter below is stored as configuration, not hard-coded logic. When Bank
Negara moves the OPR, the engine updates; the model is not retrained.

### 5.1 Parameter register

| Parameter | Basis | Where it comes from |
|---|---|---|
| Reference rate | SBR, pegged to OPR; effective housing rates sit above it by a bank-specific spread | Bank Negara Malaysia; individual bank rate boards |
| Debt service ratio ceiling | Bank-specific and income-band dependent; the engine models a conservative band rather than a single figure | Bank published policies; BNM responsible lending guidelines |
| Margin of finance | Higher for first and second housing loans; tighter for the third and subsequent | BNM policy on property financing |
| Tenure | Capped by both an absolute maximum and age at maturity — materially reduces capacity for older applicants | Bank policy |
| EPF down-payment capacity | Akaun Sejahtera balance under the May 2024 three-account structure | KWSP |
| Stamp duty on transfer (MOT) | Tiered ad valorem scale on purchase price | LHDN |
| Stamp duty on loan agreement | Percentage of loan amount | LHDN |
| Legal, disbursement and valuation fees | Scale-based on price and loan size | Solicitors' remuneration order; valuation scale |
| Commute cost inputs | Fuel price, toll tariffs, distance and drive time between home and workplace | Retail fuel price, toll operator tariffs, Routes API |

**Before submission, re-verify every row above and put the check date on the slide.**
A judge who knows this market will test one of them, and a stale figure is more
damaging than a missing one.

### 5.2 The location cost model

```
monthly_location_cost =
      (2 × one_way_distance_km × working_days_per_month × fuel_cost_per_km)
    + (2 × toll_cost_per_trip × working_days_per_month)
    + vehicle_wear_allowance(distance)
    + second_vehicle_amortisation × P(second_vehicle_required)
```

`P(second_vehicle_required)` is driven by public transport availability at the
origin — an area with rail access and a household of two adults has a very different
profile from one without. Keep this simple and defensible for the prototype: a
transparent, tunable coefficient beats a black box you cannot explain in Q&A.

The output subtracts directly from monthly surplus, which is the same surplus that
`simulate_scenario()` drives negative. This is what makes location an input to risk
rather than a browsing feature.

### 5.3 Secondary location signals (optional, cheap, high-impact)

- **Overhang concentration by state**, from NAPIC. Buying into a state carrying heavy
  completed unsold stock is a resale-liquidity risk worth flagging on the verdict.
- **Flood exposure**, where data is available. It feeds insurance premium and
  maintenance cost, both of which are cashflow lines.

Present both as annotations on the risk report. Never as filters on a listing page.

---

## 6. Gen AI implementation

**Where generative AI is essential and cannot be substituted:**

- **Multimodal document intake.** Malaysian payslips have no standard format. A
  photographed payslip from a GLC, an SME and a statutory body look nothing alike.
  Reading them into a consistent schema without templates is exactly what a
  multimodal model is for, and it is the reason the product needs no forms.
- **Function-calling orchestration.** Gemini decides which tools to call, in what
  order, and with what arguments, from an unstructured conversation.
- **Multilingual explanation.** Turning a DSR figure and a break month into an
  explanation a 29-year-old first-time buyer understands, in the language they think
  in.

**Where generative AI is deliberately excluded:** every arithmetic result. This
boundary is the technical argument of the submission and should be stated as a design
choice, not a limitation.

---

## 7. Innovation and differentiation

| | What it does | Why it fails the buyer |
|---|---|---|
| Loan calculators | One generic formula on numbers you type in | Asks for inputs the buyer does not know; ignores real commitments |
| Property portals | Show more property at higher prices | The incentive is structurally opposed to the buyer's |
| Bank pre-approval | Accurate | Arrives after viewing, after the booking fee, after the emotional commitment |
| **LifeNest AI** | Reads real documents, computes what a bank would lend, prices the location, stress-tests a decade, and says no with a route to yes | — |

The incentive argument is the strongest differentiator and belongs in Q&A: every
other product in this market is paid to say yes. A tool whose value is telling you no
has to be built by someone not taking a commission on the sale.

---

## 8. Impact

**The buyer** avoids a rejection eleven weeks in, a forfeited booking fee, and a
mortgage that breaks when the first child arrives.

**The agency** spends viewings on buyers who can complete. Agent time stops being
consumed by applications destined to fail.

**The developer** protects take-up rate. Bookings convert to sales rather than
collapsing at the financing stage — directly relevant given the 38% to 21% fall.

**The lender** receives better-prepared applicants and wastes fewer assessments.

**Systemically**, household over-indebtedness is a macroprudential concern, not only
a personal misfortune. Screening it at the point of decision is cheaper than
resolving it at the point of default.

---

## 9. Compliance, privacy and positioning

- **PDPA.** Uploaded payslips are processed in-session and purged. Nothing is
  retained for training. All demo documents in the public repository are synthetic.
  Malaysia's amended PDPA carries substantial penalties for principle breaches; a
  financial advisor that leaks payslips is not a product.
- **Not licensed financial advice.** LifeNest produces indicative estimates and
  education. It does not carry on a regulated financial advisory business, does not
  recommend specific financial products, and does not make lending decisions. State
  this in the product, in the repo and on the slide.
- **Auditability as a compliance feature.** The tool-call log is not only a technical
  claim; it is what makes the output reviewable by a compliance function if this is
  ever deployed inside an agency or a lender.

---

## 10. Evaluation methodology

Almost no hackathon team ships an eval harness. Doing so is the cheapest available
evidence for "technically sound" under the 40% criterion.

**Extraction accuracy.** A fixed set of synthetic payslips and EPF statements across
at least four visual layouts, with hand-labelled ground truth for every field. Report
field-level accuracy.

**Computation correctness.** Twenty to thirty synthetic borrower profiles spanning
income bands, existing commitments, ages and applicant counts. DSR and capacity
computed by hand for each. Report pass rate against tolerance.

**Guardrail adherence.** An automated check that no financial figure appears in a
generated verdict without a matching tool-call record. Report violation count —
target zero.

**Robustness.** Adversarial inputs: a blurred payslip, a payslip in Malay, a
deliberately contradictory income claim in chat. Record graceful-degradation
behaviour rather than pretending it does not happen.

Put the pass rates on a slide. Numbers about your own system read as rigour.

---

## 11. Risks and limitations

| Risk | Mitigation |
|---|---|
| Bank DSR policies are not public and vary | Model a conservative band, state the assumption on screen, frame output as indicative |
| Extraction fails on an unusual payslip | Confirmation step: show extracted fields and let the user correct them before computing |
| Rates and rules go stale | Parameters as config, refreshed on a schedule; display the "rates as at" date in the UI |
| Demo depends on synthetic data | Label it explicitly on the slide and in the video; volunteer it before a judge asks |
| Perceived as a property portal | Every surface leads with the verdict; listings appear only after, and only filtered by the computed figure |

---

## 12. References

1. REHDA Malaysia, *Property Industry Survey H2 2025*, 166 developers across
   Peninsular Malaysia, conducted December 2025 – February 2026, released 13 March
   2026. Findings on financing difficulty (72%), end-financing share (83%), rejection
   rates in the RM500k–700k band (31–45%, averaging 38%), and take-up rate (38% → 21%).
2. NAPIC, *Property Market Report* / Q1 2026 data: 32,801 completed unsold
   residential units valued at RM16.37bn; 19,263 completed unsold serviced apartments
   valued at RM16.52bn; state distribution led by Perak, Johor, Selangor and Kuala
   Lumpur.
3. Bank Negara Malaysia — OPR and Standardised Base Rate framework; responsible
   financing guidance.
4. KWSP (EPF) — three-account structure effective May 2024 and housing withdrawal
   rules under Akaun Sejahtera.
5. LHDN — stamp duty scales on instruments of transfer and loan agreements.

Obtain and archive the primary PDFs for items 1–5. Cite the primary document in the
deck, not a news write-up of it.
