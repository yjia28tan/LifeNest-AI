---
doc_id: sbr-and-rate-pricing
title: Standardised Base Rate and housing loan pricing
category: RATES
version: "2026-07"
source_uri: https://www.bnm.gov.my/
---

## The Overnight Policy Rate

The Overnight Policy Rate (OPR) is the interest rate set by Bank Negara Malaysia's Monetary Policy Committee. It is the main tool the central bank uses to steer borrowing costs across the economy. Changes to the OPR are announced after scheduled committee meetings.

## The Standardised Base Rate

Since 1 August 2022, new retail floating-rate loans in Malaysia reference the Standardised Base Rate (SBR). The SBR is tied to the OPR: when the OPR moves, the SBR moves by the same amount, and it changes for no other reason. This makes it easy for borrowers to see that a change in their loan rate came from monetary policy rather than from a bank's own decision.

Older loans may still reference a bank's Base Rate or Base Lending Rate, which can also move for bank-specific reasons.

## The spread and the effective rate

A floating-rate housing loan is priced as the reference rate plus a spread. The spread reflects the bank's view of the borrower and the loan, its funding costs and its profit margin. The spread is fixed in the loan agreement, although some loans step the spread up after an initial period.

The effective lending rate is the reference rate plus the spread. In its indicative rules LifeNest AI takes the OPR as the reference, because the SBR tracks it exactly, and adds a standard-applicant spread of 1.65 percentage points. With the OPR at 2.75% that gives an effective rate of 4.40%. A stronger applicant may obtain a narrower spread, but LifeNest uses the standard spread so that its figures are not optimistic.

## What happens when the OPR moves

When the OPR rises, the SBR rises with it, and so does the effective rate on every floating-rate loan that references it. The bank then either raises the monthly instalment or keeps it the same and extends the time needed to repay, depending on the loan terms. On a long-tenure loan, a one-percentage-point rise can add a meaningful amount to each instalment.

For a buyer who has borrowed close to their DSR limit, that increase comes straight out of their monthly buffer.

## How LifeNest AI uses this

All rates live in one versioned rules file. Changing the OPR is a single-line edit, and every calculation then uses the new effective rate. The `loan_capacity` tool reports the effective rate it used. The `rate_rise` scenario in `simulate_scenario` adds a configured increase to the rate from a chosen month, recomputes the instalment on the outstanding balance, and shows whether the household's cashflow still holds.

*Indicative guidance. Verify current terms with the relevant authority. Not a lending decision.*
