---
doc_id: margin-of-finance
title: Margin of finance for housing loans
category: LENDING
version: "2026-07"
source_uri: https://www.bnm.gov.my/
---

## What margin of finance means

The margin of finance is the share of the property price a bank is willing to lend. If the margin is 90%, the bank lends up to 90% of the price (or of the valuation, if lower) and the buyer funds the remaining 10% as a down payment. The margin is sometimes called the loan-to-value ratio.

The margin sets the minimum down payment, so it directly shapes the cash a buyer needs on the day of purchase. A lower margin means a larger down payment for the same property.

## First and second housing loans

For a buyer's first and second housing loans, Malaysian banks commonly offer a margin of up to 90%. LifeNest AI uses 90% for both in its indicative rules. Some government-backed or bank-specific first-home programmes can offer a higher margin, but those depend on the scheme's eligibility conditions and are not assumed in the calculation.

## Third and subsequent housing loans

Bank Negara Malaysia introduced a maximum margin of 70% for a borrower's third and subsequent outstanding housing loans. The aim was to cool speculative buying and keep household debt sustainable. LifeNest AI applies 70% whenever the confirmed profile shows two or more existing housing loans.

At 70%, the buyer must fund 30% of the price in cash, three times the down payment on a 90% loan. Stamp duty and other costs come on top. In practice this cash requirement, rather than income, is usually what limits a third purchase.

## What counts as a prior housing loan

A prior housing loan generally means any outstanding loan or financing facility secured on residential property in the borrower's name, including joint loans. A loan that has been fully settled usually no longer counts. Banks check the borrower's credit report to identify existing housing facilities, so a buyer cannot avoid the tighter margin by leaving a loan off their application.

Loans taken on commercial property, or personal loans used for renovation, are typically treated differently, but each bank applies its own definitions.

## How LifeNest AI uses this

The margin is chosen from the number of existing housing loans in the confirmed profile. The `loan_capacity` tool converts the maximum loan into a maximum property price by dividing by the margin. The `upfront_costs` tool uses the same margin to compute the down payment and the loan amount on which loan stamp duty is charged. When a reduced margin is what makes the cash requirement bind, LifeNest reports the limiting factor as `MARGIN_OF_FINANCE` so the buyer can see the reason.

*Indicative guidance. Verify current terms with the relevant authority. Not a lending decision.*
