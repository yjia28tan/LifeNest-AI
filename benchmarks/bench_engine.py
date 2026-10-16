"""Calculation-correctness benchmark: the rules engine vs. an independent reference implementation.

The reference below is written straight from the published formulas and reads config/rules_*.yaml with plain
yaml.safe_load. It shares no code with app/engine (plain floats, its own tier and annuity maths), so agreement means
two separate implementations of the same rules produce the same figures.

Also checks the worked figures published in the README (Aina, Mei) and writes every case to a CSV, so a sample can
be re-checked by hand in a spreadsheet.

Usage:
    python -m benchmarks.bench_engine                 # 50 random cases + published anchors
    python -m benchmarks.bench_engine --cases 100 --seed 1
"""

from __future__ import annotations

import argparse
import csv
import math
import random
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

from app.engine import (
    Commitment,
    EPFBalances,
    Income,
    Person,
    Profile,
    compute_dsr,
    load_rules,
    loan_capacity,
    upfront_costs,
)
from benchmarks.common import RESULTS_DIR, ROOT, banner, write_result

RULES_PATH = ROOT / "config" / "rules_my_2026_07.yaml"
MONEY_TOL = 0.01  # RM0.01: results are reported to the sen


# --- independent reference ------------------------------------------------------------------------------------------

class Reference:
    def __init__(self, path: Path) -> None:
        self.r = yaml.safe_load(path.read_text(encoding="utf-8"))

    def net(self, c: dict) -> float:
        return c["gross"] - c["statutory"] - c["variable"] * self.r["dsr"]["variable_income_haircut_pct"] / 100

    def max_dsr(self, net: float) -> float:
        for band in self.r["dsr"]["max_dsr_pct_by_income_band"]:
            if band["up_to_net_monthly"] is None or net <= band["up_to_net_monthly"]:
                return float(band["max_dsr_pct"])
        raise ValueError("no open-ended band")

    def rate(self) -> float:
        return self.r["rates"]["opr_pct"] + self.r["rates"]["spread_standard_applicant_pct"]

    def mof(self, existing: int) -> float:
        m = self.r["margin_of_finance"]
        return m["first_property_pct"] if existing <= 0 else (
            m["second_property_pct"] if existing == 1 else m["third_and_subsequent_pct"])

    def tenure(self, age: int) -> tuple[int, bool]:
        wanted = self.r["tenure"]["max_years"]
        cap = max(self.r["tenure"]["max_age_at_maturity"] - age, 0)
        return min(wanted, cap), cap < wanted

    @staticmethod
    def tiered(amount: float, tiers: list[dict]) -> float:
        total, lower = 0.0, 0.0
        for t in tiers:
            upper = amount if t["up_to"] is None else min(amount, t["up_to"])
            if upper > lower:
                total += (upper - lower) * t["rate_pct"] / 100
            if t["up_to"] is None or amount <= t["up_to"]:
                break
            lower = t["up_to"]
        return total

    def upfront(self, price: float, mof: float) -> dict[str, float]:
        loan = price * mof / 100
        parts = {
            "down_payment": price - loan,
            "stamp_duty_mot": self.tiered(price, self.r["stamp_duty_mot_tiers"]),
            "stamp_duty_loan": loan * self.r["stamp_duty_loan_pct"] / 100,
            "legal_fees": self.tiered(price, self.r["legal_fees_tiers"]),
            "valuation": price * self.r["other_upfront"]["valuation_pct"] / 100,
            "disbursements": float(self.r["other_upfront"]["disbursements_flat"]),
        }
        # The engine rounds each component to the sen before totalling.
        parts = {k: round(v + 1e-9, 2) for k, v in parts.items()}
        parts["total_cash_needed"] = round(sum(parts.values()), 2)
        parts["loan_amount"] = round(loan, 2)
        return parts

    @staticmethod
    def pv_annuity(payment: float, rate_pct: float, years: int) -> float:
        n, i = years * 12, rate_pct / 100 / 12
        if n == 0:
            return 0.0
        return payment * n if i == 0 else payment * (1 - (1 + i) ** -n) / i

    @staticmethod
    def payment(principal: float, rate_pct: float, years: int) -> float:
        n, i = years * 12, rate_pct / 100 / 12
        if n == 0:
            return 0.0
        return principal / n if i == 0 else principal * i / (1 - (1 + i) ** -n)

    def capacity(self, c: dict) -> dict[str, Any]:
        net = self.net(c)
        commitments = sum(m for _, m in c["commitments"])
        max_dsr = self.max_dsr(net)
        avail = max(net * max_dsr / 100 - commitments, 0.0)
        rate, mof = self.rate(), self.mof(c["existing_housing_loans"])
        years, capped = self.tenure(c["age"])
        raw_dsr_price = self.pv_annuity(avail, rate, years) / (mof / 100)

        cash_price = None
        if c["savings"] is not None:
            cash = c["savings"] + c["sejahtera"]
            # Largest whole RM1,000 whose cash requirement fits (cash needed rises with price, so scan by bisection).
            lo, hi = 0, 1
            if self.upfront(0, mof)["total_cash_needed"] > cash:
                cash_price = 0.0
            else:
                while self._cash(hi * 1000, mof) <= cash:
                    lo, hi = hi, hi * 2
                while hi - lo > 1:
                    mid = (lo + hi) // 2
                    lo, hi = (mid, hi) if self._cash(mid * 1000, mof) <= cash else (lo, mid)
                cash_price = lo * 1000.0

        if cash_price is not None and cash_price < raw_dsr_price:
            raw, factor = cash_price, ("MARGIN_OF_FINANCE" if mof < self.mof(0) else "CASH_UPFRONT")
        else:
            raw, factor = raw_dsr_price, ("TENURE_AGE" if capped else "DSR")
        price = math.floor(raw / 1000) * 1000 if raw > 0 else 0.0
        loan = round(price * mof / 100, 2)
        return {
            "net_income": round(net, 2), "dsr_pct": round(commitments / net * 100, 2) if net > 0 else None,
            "max_dsr_pct": max_dsr, "available_monthly": round(avail, 2), "tenure_years": years,
            "tenure_capped_by_age": capped, "effective_rate_pct": round(rate, 2),
            "max_property_price": float(price), "max_loan": loan,
            "monthly_instalment": round(self.payment(loan, rate, years), 2), "limiting_factor": factor,
            "raw_price": raw,
        }

    def _cash(self, price: float, mof: float) -> float:
        p = self.upfront(price, mof)
        return p["total_cash_needed"]


# --- cases ----------------------------------------------------------------------------------------------------------

@dataclass
class Case:
    name: str
    age: int
    gross: float
    statutory: float
    variable: float
    commitments: list[tuple[str, float]]
    sejahtera: float
    savings: float | None
    existing_housing_loans: int
    test_price: float
    published: dict[str, float] = field(default_factory=dict)  # figures printed in README/tests, checked too

    def as_dict(self) -> dict:
        return self.__dict__

    def profile(self) -> Profile:
        D = lambda x: Decimal(str(x))  # noqa: E731
        return Profile(
            person=Person(age=self.age),
            income=Income(gross_monthly=D(self.gross), statutory_deductions=D(self.statutory),
                          variable_component=D(self.variable)),
            commitments=tuple(Commitment(type=t, monthly=D(m)) for t, m in self.commitments),
            epf=EPFBalances(akaun_sejahtera=D(self.sejahtera)),
            existing_housing_loans=self.existing_housing_loans,
            savings=None if self.savings is None else D(self.savings),
        )


def anchor_cases() -> list[Case]:
    aina = dict(age=29, gross=6200.0, statutory=901.65, variable=0.0,
                commitments=[("car_loan", 780.0), ("study_loan", 310.0)], sejahtera=18400.0,
                existing_housing_loans=0, test_price=750000.0)
    return [
        Case("anchor_aina_no_savings", savings=None, published={"available_monthly": 2618.85}, **aina),
        # README / tests: 54,790 savings + 18,400 EPF is exactly the cash needed at RM520,000.
        Case("anchor_aina_cash_bound_520k", savings=54790.0,
             published={"max_property_price": 520000.0, "max_loan": 468000.0}, **aina),
        Case("anchor_aina_one_ringgit_short", savings=54789.0, published={"max_property_price": 519000.0}, **aina),
        Case("anchor_kumar_tenure_by_age", age=41, gross=11500.0, statutory=2080.0, variable=0.0,
             commitments=[("car_loan", 1100.0)], sejahtera=52000.0, savings=150000.0, existing_housing_loans=0,
             test_price=900000.0),
    ]


def random_cases(n: int, seed: int) -> list[Case]:
    rng = random.Random(seed)
    cases = []
    for i in range(n):
        gross = float(rng.randrange(2500, 30000, 50))
        statutory = round(gross * rng.uniform(0.10, 0.24), 2)
        variable = float(rng.choice([0, 0, 0, rng.randrange(200, 6000, 50)]))
        commitments = [(t, float(rng.randrange(100, 2500, 5)))
                       for t in rng.sample(["car_loan", "study_loan", "personal_loan", "credit_card"],
                                           k=rng.randint(0, 3))]
        cases.append(Case(
            name=f"random_{i + 1:03d}", age=rng.randint(21, 72), gross=gross, statutory=statutory, variable=variable,
            commitments=commitments, sejahtera=float(rng.randrange(0, 150000, 10)),
            savings=rng.choice([None, float(rng.randrange(0, 400000, 100))]),
            existing_housing_loans=rng.choice([0, 0, 0, 1, 2]),
            test_price=float(rng.choice([95000, 300000, 500000, 650000, 1000000, 1450000, rng.randrange(80000,
                                                                                                         2500000,
                                                                                                         1000)])),
        ))
    return cases


# --- comparison -----------------------------------------------------------------------------------------------------

def close(a: Any, b: Any, tol: float = MONEY_TOL) -> bool:
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, bool | str) or isinstance(b, bool | str):
        return a == b
    return abs(float(a) - float(b)) <= tol + 1e-9


def check_case(case: Case, ref: Reference, rules: Any) -> dict[str, Any]:
    profile = case.profile()
    dsr, cap = compute_dsr(profile, rules), loan_capacity(profile, rules)
    up = upfront_costs(Decimal(str(case.test_price)), profile, rules)

    expected = ref.capacity(case.as_dict())
    expected_up = ref.upfront(case.test_price, ref.mof(case.existing_housing_loans))
    engine = {
        "net_income": dsr.net_income, "dsr_pct": dsr.dsr_pct, "max_dsr_pct": dsr.max_dsr_pct,
        "available_monthly": cap.available_monthly, "tenure_years": cap.tenure_years,
        "tenure_capped_by_age": cap.tenure_capped_by_age, "effective_rate_pct": cap.effective_rate_pct,
        "max_property_price": cap.max_property_price, "max_loan": cap.max_loan,
        "monthly_instalment": cap.monthly_instalment, "limiting_factor": cap.limiting_factor,
        **{f"upfront.{k}": getattr(up, k) for k in expected_up},
    }
    expected.update({f"upfront.{k}": v for k, v in expected_up.items()})

    checks, failures = 0, []
    for key, want in expected.items():
        if key == "raw_price":
            continue
        got = engine[key]
        ok = close(got, want)
        if not ok and key in {"max_property_price", "max_loan", "monthly_instalment"}:
            # Only a raw price within a sen of a RM1,000 boundary can floor differently in float vs Decimal.
            ok = abs(expected["raw_price"] - round(expected["raw_price"] / 1000) * 1000) < 0.01
        checks += 1
        if not ok:
            failures.append({"field": key, "engine": str(got), "reference": str(want)})
    for key, want in case.published.items():
        checks += 1
        if not close(engine[key], want):
            failures.append({"field": f"published.{key}", "engine": str(engine[key]), "reference": str(want)})
    return {"case": case.name, "checks": checks, "failures": failures,
            "engine": {k: str(v) for k, v in engine.items()}, "reference": expected}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cases", type=int, default=50, help="random cases on top of the published anchors")
    parser.add_argument("--seed", type=int, default=2026)
    args = parser.parse_args()

    rules, ref = load_rules(RULES_PATH), Reference(RULES_PATH)
    cases = anchor_cases() + random_cases(args.cases, args.seed)
    results = [check_case(c, ref, rules) for c in cases]

    passed = [r for r in results if not r["failures"]]
    checks = sum(r["checks"] for r in results)
    failed_checks = sum(len(r["failures"]) for r in results)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    csv_path = RESULTS_DIR / "engine_cases.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["case", "age", "gross", "statutory", "variable", "commitments_total", "akaun_sejahtera",
                    "savings", "existing_housing_loans", "engine_max_price", "reference_max_price",
                    "engine_max_loan", "engine_instalment", "limiting_factor", "test_price",
                    "engine_cash_needed", "reference_cash_needed", "match"])
        for c, r in zip(cases, results, strict=True):
            w.writerow([c.name, c.age, c.gross, c.statutory, c.variable, sum(m for _, m in c.commitments),
                        c.sejahtera, c.savings, c.existing_housing_loans, r["engine"]["max_property_price"],
                        r["reference"]["max_property_price"], r["engine"]["max_loan"],
                        r["engine"]["monthly_instalment"], r["engine"]["limiting_factor"], c.test_price,
                        r["engine"]["upfront.total_cash_needed"], r["reference"]["upfront.total_cash_needed"],
                        "yes" if not r["failures"] else "NO"])

    banner("Calculation correctness - engine vs independent reference")
    print(f"rules version : {rules.version}")
    print(f"cases         : {len(cases)} ({len(anchor_cases())} published anchors + {args.cases} random)")
    print(f"field checks  : {checks - failed_checks} / {checks} agree (tolerance RM{MONEY_TOL:.2f})")
    print(f"cases matched : {len(passed)} / {len(cases)}")
    for r in results:
        for f in r["failures"]:
            print(f"  MISMATCH {r['case']}: {f['field']} engine={f['engine']} reference={f['reference']}")
    path = write_result("engine", {"rules_version": rules.version, "cases": len(cases), "cases_matched": len(passed),
                                   "checks": checks, "checks_failed": failed_checks, "results": results})
    print(f"\nSlide 9 -> Calculation correctness: {len(passed)} / {len(cases)} cases match")
    print(f"details: {path}\nspreadsheet: {csv_path}")


if __name__ == "__main__":
    main()
