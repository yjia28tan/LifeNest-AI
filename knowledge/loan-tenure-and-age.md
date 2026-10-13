---
doc_id: loan-tenure-and-age
title: Loan tenure and the age cap at maturity
category: TENURE
version: "2026-07"
source_uri: https://www.bnm.gov.my/
---

## Maximum tenure

The tenure is the number of years over which a housing loan is repaid. Malaysian banks commonly offer housing loans of up to 35 years. LifeNest AI uses 35 years as the maximum in its indicative rules.

## The cap by age at maturity

Banks also limit how old a borrower may be when the loan ends. A common cap is 70 years old at maturity, although some banks use a lower age and a few allow a higher one. LifeNest AI uses 70.

The tenure a buyer can actually get is the shorter of the two limits. A 29-year-old can take the full 35 years, because they would be 64 at maturity. A 40-year-old is capped at 30 years. A 60-year-old is capped at 10 years. Someone already at the age limit cannot take a new housing loan on their own.

## How a shorter tenure changes the numbers

The monthly instalment on a loan depends on the amount, the interest rate and the tenure. Shortening the tenure spreads the same principal over fewer payments, so each payment rises. Because the debt service ratio caps the instalment a buyer can afford, a shorter tenure also lowers the loan the buyer can reach.

The effect is strong at long tenures and becomes severe at short ones. Losing five years from a 35-year tenure trims capacity noticeably. Losing twenty years cuts it by more than half.

## Choosing a shorter tenure on purpose

Some buyers choose a shorter tenure to pay less interest overall. That is a reasonable goal, but it means a higher instalment and a lower maximum price. LifeNest AI lets the buyer ask for a specific tenure, and `loan_capacity` will use the shorter of the requested tenure and the age-based cap.

## Joint borrowers

Banks often allow an older borrower to extend the tenure by applying jointly with a younger co-borrower, sometimes using the younger borrower's age for the cap. Those arrangements depend on the bank and on both borrowers' circumstances, and LifeNest AI does not model them. Its calculation uses the age on the confirmed profile.

## How LifeNest AI uses this

The `loan_capacity` tool computes the tenure as the minimum of the configured maximum, any tenure the buyer requested, and the years left before the age cap. When the age cap is what shortens the tenure, the tool reports `TENURE_AGE` as the limiting factor so the buyer understands why their capacity is lower than a younger buyer's on the same income.

*Indicative guidance. Verify current terms with the relevant authority. Not a lending decision.*
