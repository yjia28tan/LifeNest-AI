"""Profile state rules: which fields may be proposed or corrected, how values are validated, and when a profile counts as confirmed.

Extraction output and user corrections pass through the same allowlist, so neither a document nor a chat message
can introduce a field the engine does not expect (e.g. an "approved_amount").
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import ValidationError

from app.engine.models import Profile
from app.storage.models import FieldProvenance, ProfileStatus

MAX_MONEY = Decimal("10000000")
MAX_COMMITMENTS = 20
REQUIRED_FOR_CONFIRMED = ("person.age", "income.gross_monthly", "income.statutory_deductions")
_COMMITMENT_TYPE = re.compile(r"^[a-z][a-z_]{0,39}$")
_NET_TOLERANCE = Decimal("1.00")


class FieldValueError(ValueError):
    def __init__(self, path: str, reason: str) -> None:
        super().__init__(f"{path}: {reason}")
        self.path = path


def _money(path: str, value: Any) -> str:
    if isinstance(value, bool):
        raise FieldValueError(path, "must be an amount in ringgit")
    try:
        amount = Decimal(str(value).replace(",", "").replace("RM", "").strip())
    except InvalidOperation:
        raise FieldValueError(path, "must be an amount in ringgit") from None
    if not amount.is_finite() or amount < 0 or amount > MAX_MONEY:
        raise FieldValueError(path, f"must be between 0 and {MAX_MONEY:,}")
    return str(amount.quantize(Decimal("0.01")))


def _int(lo: int, hi: int) -> Callable[[str, Any], int]:
    def check(path: str, value: Any) -> int:
        if isinstance(value, bool):
            raise FieldValueError(path, "must be a whole number")
        try:
            number = Decimal(str(value).strip())
        except InvalidOperation:
            raise FieldValueError(path, "must be a whole number") from None
        if number != number.to_integral_value() or not lo <= number <= hi:
            raise FieldValueError(path, f"must be a whole number between {lo} and {hi}")
        return int(number)

    return check


def _choice(*options: str) -> Callable[[str, Any], str]:
    def check(path: str, value: Any) -> str:
        if value not in options:
            raise FieldValueError(path, f"must be one of {', '.join(options)}")
        return str(value)

    return check


def _commitments(path: str, value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > MAX_COMMITMENTS:
        raise FieldValueError(path, f"must be a list of up to {MAX_COMMITMENTS} commitments")
    out = []
    for i, item in enumerate(value):
        if not isinstance(item, dict) or not _COMMITMENT_TYPE.match(str(item.get("type", ""))):
            raise FieldValueError(f"{path}[{i}]", "needs a type such as car_loan or study_loan")
        remaining = item.get("remaining_months")
        out.append({
            "type": item["type"],
            "monthly": _money(f"{path}[{i}].monthly", item.get("monthly")),
            "remaining_months": None if remaining is None else _int(0, 600)(f"{path}[{i}].remaining_months", remaining),
        })
    return out


FIELD_VALIDATORS: dict[str, Callable[[str, Any], Any]] = {
    "person.age": _int(16, 100),
    "person.dependants": _int(0, 20),
    "income.gross_monthly": _money,
    "income.statutory_deductions": _money,
    "income.variable_component": _money,
    "income.net_monthly": _money,
    "epf.akaun_sejahtera": _money,
    "employment.type": _choice("permanent", "contract", "self_employed"),
    "employment.months_in_service": _int(0, 720),
    "commitments": _commitments,
    "existing_housing_loans": _int(0, 10),
    "savings": _money,
}


def validate_field(path: str, value: Any) -> Any:
    validator = FIELD_VALIDATORS.get(path)
    if validator is None:
        raise FieldValueError(path, "is not a field LifeNest can use")
    return validator(path, value)


@dataclass(frozen=True)
class ProposedField:
    path: str
    value: Any
    confidence: float
    source_hint: str


def validate_proposed(
    raw_fields: list[dict[str, Any]], min_confidence: float,
) -> tuple[dict[str, ProposedField], list[str]]:
    """Keep only allowlisted, well-formed fields; flag low confidence and inconsistencies as warnings."""
    accepted: dict[str, ProposedField] = {}
    warnings: list[str] = []
    for raw in raw_fields:
        path = str(raw.get("path", ""))
        try:
            value = validate_field(path, raw.get("value"))
        except FieldValueError:
            if path in FIELD_VALIDATORS:
                warnings.append(f"invalid:{path}")
            continue  # unknown paths are dropped silently: document text is data, not schema
        confidence = max(0.0, min(1.0, float(raw.get("confidence") or 0.0)))
        accepted[path] = ProposedField(path, value, confidence, str(raw.get("source_hint") or "")[:80])
        if confidence < min_confidence:
            warnings.append(f"low_confidence:{path}")

    gross, deductions, net = (accepted.get(p) for p in
                              ("income.gross_monthly", "income.statutory_deductions", "income.net_monthly"))
    if gross and deductions and net:
        implied = Decimal(gross.value) - Decimal(deductions.value)
        if abs(implied - Decimal(net.value)) > _NET_TOLERANCE:
            warnings.append("inconsistent:income.net_monthly")
    return accepted, warnings


def apply_confirmation(
    confirmed: dict[str, Any],
    provenance: dict[str, FieldProvenance],
    proposed: dict[str, dict[str, Any]],
    source: str,
    corrections: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, FieldProvenance], list[str]]:
    """Merge a reviewed proposal and the user's corrections. Corrections are validated before anything changes."""
    cleaned = {path: validate_field(path, value) for path, value in corrections.items()}
    fields, prov = dict(confirmed), dict(provenance)
    for path, item in proposed.items():
        fields[path] = item["value"]
        prov[path] = FieldProvenance(source=source, confidence=item["confidence"])
    for path, value in cleaned.items():
        corrected = path in proposed
        fields[path] = value
        prov[path] = FieldProvenance(source="user_corrected" if corrected else "user_stated",
                                     confidence=1.0, corrected_by_user=corrected)
    return fields, prov, sorted(cleaned)


def build_profile(fields: dict[str, Any]) -> tuple[Profile | None, list[str]]:
    missing = [p for p in REQUIRED_FOR_CONFIRMED if p not in fields]
    if missing:
        return None, missing
    nested: dict[str, Any] = {}
    for path, value in fields.items():
        head, _, tail = path.partition(".")
        if tail:
            nested.setdefault(head, {})[tail] = value
        else:
            nested[head] = value
    try:
        return Profile.model_validate(nested), []
    except ValidationError as exc:
        raise FieldValueError("profile", str(exc.errors()[0].get("msg", "invalid"))) from None


def flatten_profile(profile: Profile) -> dict[str, Any]:
    data = profile.model_dump(mode="json")
    fields: dict[str, Any] = {}
    for path in FIELD_VALIDATORS:
        head, _, tail = path.partition(".")
        value = data.get(head, {}).get(tail) if tail else data.get(head)
        if value is not None:
            fields[path] = validate_field(path, value)
    return fields


def derive_status(profile: Profile | None, confirmed_fields: dict[str, Any], pending_reviews: int) -> ProfileStatus:
    """CONFIRMED only when a full profile exists and nothing is awaiting review."""
    if profile is not None and pending_reviews == 0:
        return "CONFIRMED"
    if pending_reviews or confirmed_fields:
        return "EXTRACTED"
    return "EMPTY"
