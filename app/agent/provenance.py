"""Provenance of every input a computation uses, and the questions that must be answered before computing."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.agent.state import HOME_LOAN_TYPES
from app.engine.models import InputUsed, Profile, Provenance
from app.engine.rules import Rules
from app.engine.statutory import income_breakdown
from app.storage.models import FieldProvenance, ProfileRecord

LABELS = {
    "person.age": "age", "person.dependants": "number of dependants",
    "income.gross_monthly": "gross monthly salary", "income.net_monthly": "net monthly income",
    "income.statutory_deductions": "monthly statutory deductions", "income.variable_component": "variable income",
    "epf.akaun_sejahtera": "EPF Akaun Sejahtera balance", "savings": "savings",
    "existing_housing_loans": "number of existing housing loans", "employment.type": "employment type",
    "employment.months_in_service": "months in service", "commitments": "monthly commitments",
}
_SOURCES = {"payslip": "your payslip", "epf_statement": "your EPF statement", "loan_statement": "your loan statement",
            "user_stated": "what you told me", "user_corrected": "your correction", "demo": "the demo profile",
            "inferred": "an unconfirmed guess"}
_MONEY_PATHS = {"income.gross_monthly", "income.net_monthly", "income.statutory_deductions",
                "income.variable_component", "epf.akaun_sejahtera", "savings"}


def _rm(value: Any) -> str:
    return f"RM{Decimal(str(value)):,.2f}"


def _display(path: str, value: Any) -> str:
    if path == "commitments":
        return "; ".join(f"{c['type'].replace('_', ' ')} {_rm(c['monthly'])}/month" for c in value) or "none"
    return _rm(value) if path in _MONEY_PATHS else str(value)


def classify(prov: FieldProvenance | None, turn_id: str | None) -> Provenance:
    if prov is None or prov.source == "inferred":
        return "inferred"
    if prov.source == "user_stated":
        return "stated_this_turn" if turn_id is not None and prov.turn_id == turn_id else "stated_earlier"
    return "confirmed_from_profile"


def inputs_used(record: ProfileRecord, profile: Profile, rules: Rules, turn_id: str | None) -> list[InputUsed]:
    """Every field the engine reads, with where it came from. The UI footer and the narrator both use this list."""
    out: list[InputUsed] = []
    for path, value in sorted(record.confirmed_fields.items()):
        prov = record.field_provenance.get(path)
        kind, source = classify(prov, turn_id), prov.source if prov else "unknown"
        label = LABELS.get(path, path)
        if path == "commitments":
            out += [InputUsed(field=f"commitments[{i}]", label="commitment", value=_display(path, [c]),
                              provenance=kind, source=source) for i, c in enumerate(value)]
        else:
            out.append(InputUsed(field=path, label=label, value=_display(path, value), provenance=kind,
                                 source=source))
    breakdown = income_breakdown(profile, rules)
    if breakdown.basis == "derived_from_gross":
        out.append(InputUsed(field="income.statutory_deductions", label=LABELS["income.statutory_deductions"],
                             value=_rm(breakdown.statutory_deductions), provenance="default",
                             source="rules: EPF + SOCSO + EIS + PCB estimate"))
        out.append(InputUsed(field="income.net_monthly", label=LABELS["income.net_monthly"],
                             value=_rm(breakdown.net_monthly), provenance="default",
                             source="derived from gross salary"))
    if "existing_housing_loans" not in record.confirmed_fields:
        out.append(InputUsed(field="existing_housing_loans", label=LABELS["existing_housing_loans"], value="0",
                             provenance="default", source="default: no existing housing loan"))
    return out


def needs_confirmation(record: ProfileRecord) -> list[dict[str, Any]]:
    """Questions to ask before any computation. Empty means every input is stated, confirmed or a default."""
    items: list[dict[str, Any]] = []
    fields = record.confirmed_fields
    for path, value in sorted(fields.items()):
        prov = record.field_provenance.get(path)
        label, shown = LABELS.get(path, path), _display(path, value)
        if prov is None or prov.source == "inferred":
            items.append({"field": path, "label": label, "reason": "inferred", "value": shown,
                          "question": f"I don't have a {label} that you confirmed. What is it?"})
        elif not prov.session_confirmed:
            items.append({"field": path, "label": label, "reason": "not_confirmed_this_session", "value": shown,
                          "question": f"I have your {label} as {shown} (from {_SOURCES[prov.source]}). "
                                      "Is that still correct?"})
    homes = [c for c in fields.get("commitments", []) if HOME_LOAN_TYPES.search(c["type"])]
    count_prov = record.field_provenance.get("existing_housing_loans")
    if homes and (count_prov is None or not count_prov.session_confirmed):
        shown = _display("commitments", homes)
        items.append({"field": "existing_housing_loans", "reason": "property_count", "value": shown,
                      "question": f"You have an existing home loan ({shown}). Would this home be your 2nd property, "
                                  "or your 3rd or later? It sets the margin of finance and whether first-home "
                                  "incentives apply."})
    return items


def missing_questions(missing: list[str], rules: Rules) -> list[dict[str, Any]]:
    questions = {
        "person.age": f"How old are you? A home loan must end by age {rules.tenure.max_age_at_maturity}, "
                      "so your age sets the longest tenure.",
        "income.gross_monthly": "What is your monthly salary? (Gross, before EPF and tax, unless you say net.)",
    }
    return [{"field": p, "reason": "missing", "question": questions.get(p, f"What is your {LABELS.get(p, p)}?")}
            for p in missing]
