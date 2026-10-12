---
doc_id: epf-akaun-sejahtera
title: EPF Akaun Sejahtera and housing withdrawals
category: EPF
version: "2026-07"
source_uri: https://www.kwsp.gov.my/
---

## The 2024 three-account structure

In May 2024 the Employees Provident Fund (EPF, or KWSP) restructured every member's savings into three accounts. Akaun Persaraan holds the retirement savings and receives 75% of each monthly contribution. Akaun Sejahtera receives 15% and supports life-cycle needs such as housing, education and health. Akaun Fleksibel receives 10% and can be withdrawn for any purpose.

Before the restructure, members had two accounts. The former Account 1 received 70% of contributions and the former Account 2 received 30%. Housing withdrawals were drawn from Account 2.

## Where housing withdrawals come from now

Under the 2024 structure, housing withdrawals draw on Akaun Sejahtera. Because Akaun Sejahtera receives 15% of contributions where the former Account 2 received 30%, a member's housing pot now grows at roughly half the old pace for the same salary. The balances that existed in Account 2 at the time of the restructure were carried across, but every contribution since then splits under the new shares.

LifeNest AI reads the Akaun Sejahtera balance from the member's EPF statement and treats that balance as the amount available for a housing withdrawal. It does not count Akaun Persaraan, and it does not count Akaun Fleksibel towards the housing withdrawal, because the fleksibel balance is not reserved for housing and may already be committed elsewhere.

## What a housing withdrawal may be used for

EPF housing withdrawals are generally available to help buy or build a house, to reduce or settle an outstanding housing loan, and to help pay monthly housing loan instalments. Eligibility depends on conditions such as the property type, the relationship of the member to the property owner, and how often the member has already withdrawn for housing. The exact withdrawal limit for a purchase is set by KWSP and is capped by the Akaun Sejahtera balance.

Withdrawals for a purchase are usually paid against the difference between the purchase price and the financing, so they help with the down payment. Many of the other upfront costs, such as stamp duty and legal fees, still need to be paid in cash.

## Why pre-2024 numbers overestimate the down payment

Many buyers still plan with figures from articles, calculators or family advice written before May 2024. Those sources assume 30% of contributions flow into the housing account. A buyer who projects their housing pot forward on that basis will expect roughly twice the monthly growth they will actually see.

The effect compounds. A buyer planning to wait two or three years to build a down payment from EPF alone may find the pot is far smaller than expected on the day they sign. That gap then has to come from cash savings, which pushes the purchase date back or reduces the price they can safely commit to.

## How LifeNest AI uses this

The `epf_withdrawal` tool returns the Akaun Sejahtera balance, the amount LifeNest treats as eligible, and a note about the 2024 structure. The upfront-cost calculation subtracts that eligible amount from the total cash needed and shows any shortfall that must come from savings. Future EPF contributions are not assumed in any plan, so the plan is never optimistic about EPF.

*Indicative guidance. Verify current terms with the relevant authority. Not a lending decision.*
