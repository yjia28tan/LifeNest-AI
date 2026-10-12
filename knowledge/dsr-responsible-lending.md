---
doc_id: dsr-responsible-lending
title: Debt service ratio and responsible lending
category: LENDING
version: "2026-07"
source_uri: https://www.bnm.gov.my/
---

## What the debt service ratio measures

The debt service ratio (DSR) compares a borrower's monthly debt repayments with their monthly income. It answers a simple question: of every ringgit that reaches the borrower each month, how much is already promised to lenders? Banks in Malaysia are expected under Bank Negara Malaysia's responsible lending guidance to assess whether a borrower can repay without undue hardship, and the DSR is the main measure they use to do it.

## Net income, not gross

Banks compute the DSR on net income: the salary after statutory deductions. Those deductions are the employee EPF contribution, SOCSO, EIS and monthly tax deductions (PCB). A buyer who quotes their gross salary will always overstate what the bank counts.

Variable income such as commission, overtime and bonuses is treated cautiously. Many lenders discount it or average it over a long period because it may not recur. LifeNest AI applies a haircut to variable income before computing net income; the size of the haircut is set in the versioned rules file.

## All existing commitments count

Every existing monthly repayment is added to the numerator: car loans, personal loans, study loans, other housing loans, and credit card obligations. The new housing loan instalment is then added on top. A car loan taken out a year before applying for a mortgage can reduce the property price a buyer can reach by much more than the car itself cost, because each ringgit of monthly commitment removes many ringgit of borrowing capacity over a long tenure.

## Typical acceptance bands

Each bank sets its own internal DSR limits, and they are not published as a single national rule. Limits commonly rise with income, because a higher-income household has more left over after essential spending. LifeNest AI uses an indicative banded table: up to 60% for net income up to RM3,000 a month, 65% up to RM5,000, 70% up to RM10,000, and 75% above that. These bands are configuration, not law, and a particular bank may apply stricter or more generous limits.

## Why self-declared budgets diverge from computed capacity

Buyers often set a budget from a figure they can comfortably imagine paying each month, or from a property they have already seen. Neither starts from net income and existing commitments. The result is a budget that can sit far above what any bank would lend, and buyers discover this only after paying for a booking, a valuation or legal advice.

LifeNest AI starts from the other end. It computes the DSR from the confirmed payslip and commitments, works out the monthly instalment the DSR still allows, and converts that instalment into a maximum loan and price using the configured rate and tenure. The `compute_dsr` tool reports net income, total commitments, the DSR, the applicable band and the monthly headroom. The `loan_capacity` tool then reports the maximum loan and price and names the factor that limits them.

A DSR inside the band does not mean a loan will be approved. Banks also consider credit history, employment stability, the property itself and their own risk appetite.

*Indicative guidance. Verify current terms with the relevant authority. Not a lending decision.*
