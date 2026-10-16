"""UI cards (spec section 13). Every figure on a card is copied from a tool output, never parsed from reply text."""

from __future__ import annotations

from typing import Any


def _latest(computations: list[dict[str, Any]], tool: str) -> dict[str, Any] | None:
    found = [c["outputs"] for c in computations if c["tool"] == tool]
    return found[-1] if found else None


def build_cards(computations: list[dict[str, Any]], sources: list[dict[str, Any]],
                named_price: str | None) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    cap = _latest(computations, "loan_capacity")
    if cap:
        dsr = _latest(computations, "compute_dsr") or {}
        cards.append({"type": "affordability_verdict", "data": {
            "max_property_price": cap["max_property_price"], "max_loan": cap["max_loan"],
            "monthly_instalment": cap["monthly_instalment"], "tenure_years": cap["tenure_years"],
            "effective_rate_pct": cap["effective_rate_pct"], "limiting_factor": cap["limiting_factor"],
            "dsr_pct": dsr.get("dsr_pct"), "max_dsr_pct": dsr.get("max_dsr_pct"),
            "income": dsr.get("income"), "tenure_reason": cap.get("tenure_reason"),
            "margin_of_finance_pct": cap.get("margin_of_finance_pct"),
            "named_price": named_price, "unverified_inputs": cap.get("unverified_inputs", []),
            "inputs_used": cap.get("inputs_used", []),
            "rules_version": cap["rules_version"],
        }})
    for c in computations:
        out = c["outputs"]
        if c["tool"] == "upfront_costs":
            cards.append({"type": "cost_breakdown", "data": out})
        elif c["tool"] == "affordability_timeline":
            cards.append({"type": "affordability_timeline", "data": out})
        elif c["tool"] == "simulate_scenario":
            cards.append({"type": "scenario_result", "data": {
                k: out[k] for k in ("scenario", "property_price", "status", "break_month", "monthly_shortfall",
                                    "buffer_months", "projection", "rules_version")}})
        elif c["tool"] == "plan_path_to_approval":
            cards.append({"type": "path_to_approval", "data": out})
        elif c["tool"] == "match_affordable_stock":
            cards.append({"type": "property_matches", "data": out})
    seen: set[tuple[str, str]] = set()
    for s in sources:
        key = (s["doc_id"], s["section"])
        if key not in seen:
            seen.add(key)
            cards.append({"type": "regulation_citation", "data": {"doc_id": s["doc_id"], "title": s["title"],
                                                                  "section": s["section"]}})
    return cards
