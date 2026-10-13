---
doc_id: stamp-duty-and-legal-fees
title: Stamp duty, legal fees and other upfront costs
category: COSTS
version: "2026-07"
source_uri: https://www.hasil.gov.my/
---

## Stamp duty on the memorandum of transfer

When ownership of a property passes to the buyer, the memorandum of transfer (MOT) is stamped and duty is charged on the property price or market value, whichever is higher. The duty is tiered, and each rate applies only to the slice of the price that falls inside its tier:

- 1% on the first RM100,000
- 2% on the portion from RM100,001 to RM500,000
- 3% on the portion from RM500,001 to RM1,000,000
- 4% on the portion above RM1,000,000

On a RM500,000 property the MOT duty is therefore RM1,000 plus RM8,000, a total of RM9,000.

## Stamp duty on the loan agreement

The loan or financing agreement is also stamped, at 0.5% of the loan amount. On a loan of RM450,000 that is RM2,250.

First-time buyers may qualify for full or partial exemptions on both duties under government first-home initiatives, subject to a price ceiling and a deadline. LifeNest AI does not apply any exemption automatically, because eligibility must be confirmed by the buyer's lawyer. Its figures are therefore the cost before any exemption.

## Legal fees

Lawyers are paid for the sale and purchase agreement and for the loan documentation, and their fees follow a regulated scale that is tiered by price. LifeNest AI uses an indicative tiered scale on the property price: 1.25% on the first RM500,000, 1.00% on the portion up to RM1,000,000, and 0.70% above that. Actual quotations vary with the transaction and may be charged separately for the purchase and the loan.

## Valuation and disbursements

The bank requires a valuation report, and its fee is roughly proportional to the property value. LifeNest uses 0.25% of the price as an indicative valuation cost. Disbursements cover searches, registration, courier, printing and similar out-of-pocket items; LifeNest uses a flat RM1,500.

## Which costs need cash

The housing loan finances only part of the price, up to the margin of finance. Everything else is paid upfront: the down payment, both stamp duties, legal fees, valuation and disbursements. Some banks allow certain fees to be added to the loan, but this is not assumed. An EPF housing withdrawal can help with the down payment, but the remaining costs usually need cash.

## How LifeNest AI uses this

The `upfront_costs` tool returns each of these items for a given price, the total cash needed, the eligible EPF amount applied against it, and the shortfall after EPF. If the buyer has declared savings, the shortfall after savings is shown too. When cash runs out before the DSR does, `loan_capacity` reports `CASH_UPFRONT` as the limiting factor.

*Indicative guidance. Verify current terms with the relevant authority. Not a lending decision.*
