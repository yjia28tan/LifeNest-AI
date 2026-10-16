"""Tool registry (spec section 7): thin adapters from validated arguments to the deterministic engine.

Every tool receives the confirmed profile from server-side state — never from the model. Results are plain JSON with
money as 2-dp strings, currency, rules_version and inputs_echo, and every call is audited.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.engine.dsr import compute_dsr
from app.engine.epf import epf_withdrawal
from app.engine.loan_capacity import loan_capacity
from app.engine.location_cost import UnknownAreaError, location_cost
from app.engine.models import Profile
from app.engine.path_to_approval import plan_path_to_approval
from app.engine.scenarios import simulate_scenario
from app.engine.stock_match import ExceedsComputedCapacityError, match_affordable_stock
from app.engine.upfront_costs import upfront_costs
from app.llm.gateway import ToolCall, ToolSpec
from app.observability import get_logger
from app.storage.models import AuditEvent, ProfileRecord, SessionRecord

if TYPE_CHECKING:
    from app.deps import RequestContext, Services

log = get_logger("lifenest.tools")

MAX_PRICE = 20_000_000
KB_CATEGORIES = Literal["EPF", "LENDING", "COSTS", "TENURE", "RATES", "SCHEMES"]
# Appended to every computing tool: the grounding check rejects sums, differences and other derived figures.
_QUOTE_VERBATIM = (" Quote returned figures exactly as given; never add, subtract or otherwise combine them — "
                   "call a tool if you need a different figure.")


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NoArgs(_Args):
    pass


class LoanCapacityArgs(_Args):
    tenure_years: int | None = Field(None, ge=1, le=35, description="Requested tenure; capped by rules and age")
    rate_override_pct: float | None = Field(None, ge=0, le=20, description="Effective annual rate to test, in %")


class UpfrontCostsArgs(_Args):
    property_price: float = Field(gt=0, le=MAX_PRICE, description="Property price in MYR")


class LocationCostArgs(_Args):
    area: str = Field(min_length=2, max_length=40, description="Home area, e.g. Kajang")
    workplace_area: str = Field(min_length=2, max_length=40, description="Workplace area, e.g. KL City Centre")
    transport_mode: Literal["car", "transit"]


class ScenarioArgs(_Args):
    scenario: Literal["first_child", "marriage", "job_loss", "rate_rise", "car_replacement"]
    property_price: float = Field(gt=0, le=MAX_PRICE, description="Price of the home being stress-tested, in MYR")
    start_month: int = Field(12, ge=1, le=120, description="Month the event happens, counted from purchase")
    params: dict[str, float] = Field(default_factory=dict, max_length=5,
                                     description="Optional overrides of the configured scenario parameters")


class PathArgs(_Args):
    target_property_price: float = Field(gt=0, le=MAX_PRICE, description="Price the buyer wants to reach, in MYR")


class StockArgs(_Args):
    max_price: float = Field(gt=0, le=MAX_PRICE, description="Must not exceed the computed max_property_price")
    area: str | None = Field(None, min_length=2, max_length=40)
    min_bedrooms: int | None = Field(None, ge=0, le=10)


class SearchArgs(_Args):
    query: str = Field(min_length=3, max_length=300)
    categories: list[KB_CATEGORIES] | None = Field(None, max_length=6)
    top_k: int = Field(4, ge=1, le=10)


class StatedFiguresArgs(_Args):
    figures: dict[str, Any] = Field(
        min_length=1, max_length=6,
        description="Profile path -> value the user explicitly stated about themselves, e.g. "
                    "{\"income.gross_monthly\": 20000}. Allowed paths: person.age, person.dependants, "
                    "income.gross_monthly, income.statutory_deductions, income.variable_component, "
                    "epf.akaun_sejahtera, savings, existing_housing_loans.")


@dataclass
class ToolContext:
    services: Services
    request: RequestContext
    session: SessionRecord
    record: ProfileRecord | None

    @property
    def profile(self) -> Profile | None:
        if self.session.profile_status != "CONFIRMED" or self.record is None:
            return None
        return self.record.profile

    @property
    def unverified_inputs(self) -> list[str]:
        prov = self.record.field_provenance if self.record else {}
        return sorted(p for p, v in prov.items() if v.source == "user_stated")


@dataclass
class ToolOutcome:
    call: ToolCall
    result: dict[str, Any]
    latency_ms: int
    computation: dict[str, Any] | None = None
    sources: list[dict[str, Any]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.result.get("ok"))

    @property
    def trace(self) -> dict[str, Any]:
        return {"tool": self.call.name, "status": "ok" if self.ok else "error",
                "latency_ms": self.latency_ms, "error_code": self.result.get("error_code")}

    @property
    def llm_view(self) -> dict[str, Any]:
        # The projection series is for the chart; the model only needs the facts.
        view = {k: v for k, v in self.result.items() if k not in ("projection", "inputs_echo")}
        if "projection" in self.result:
            view["projection_points"] = len(self.result["projection"])
        return view


class ToolFailure(Exception):
    def __init__(self, error_code: str, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.error_code, self.message, self.extra = error_code, message, extra


Handler = Callable[[ToolContext, Any], Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    args_model: type[_Args]
    handler: Handler
    computes: bool = True


def _json(model: BaseModel) -> dict[str, Any]:
    data: dict[str, Any] = model.model_dump(mode="json")
    return data


def _dec(value: float) -> Decimal:
    return Decimal(str(value))


async def _compute_dsr(t: ToolContext, a: NoArgs) -> dict[str, Any]:
    return _json(await asyncio.to_thread(compute_dsr, _profile(t), t.services.rules))


async def _loan_capacity(t: ToolContext, a: LoanCapacityArgs) -> dict[str, Any]:
    rate = None if a.rate_override_pct is None else _dec(a.rate_override_pct)
    return _json(await asyncio.to_thread(loan_capacity, _profile(t), t.services.rules, a.tenure_years, rate))


async def _epf(t: ToolContext, a: NoArgs) -> dict[str, Any]:
    return _json(await asyncio.to_thread(epf_withdrawal, _profile(t), t.services.rules))


async def _upfront(t: ToolContext, a: UpfrontCostsArgs) -> dict[str, Any]:
    return _json(await asyncio.to_thread(upfront_costs, _dec(a.property_price), _profile(t), t.services.rules))


async def _location(t: ToolContext, a: LocationCostArgs) -> dict[str, Any]:
    try:
        return _json(location_cost(a.area, a.workplace_area, a.transport_mode, t.services.rules))
    except UnknownAreaError as exc:
        raise ToolFailure("UNKNOWN_AREA", "That area is not in the commute model.", supported=exc.supported) from None


async def _scenario(t: ToolContext, a: ScenarioArgs) -> dict[str, Any]:
    try:
        result = await asyncio.to_thread(simulate_scenario, _profile(t), t.services.rules, a.scenario,
                                         _dec(a.property_price), a.start_month, a.params)
    except ValueError as exc:
        raise ToolFailure("VALIDATION_ERROR", str(exc)) from None
    return _json(result)


async def _path(t: ToolContext, a: PathArgs) -> dict[str, Any]:
    return _json(await asyncio.to_thread(plan_path_to_approval, _profile(t), t.services.rules,
                                         _dec(a.target_property_price)))


async def _stock(t: ToolContext, a: StockArgs) -> dict[str, Any]:
    profile = _profile(t)
    rules = t.services.rules
    # The ceiling is recomputed server-side; the model cannot supply or inflate it.
    capacity = (await asyncio.to_thread(loan_capacity, profile, rules)).max_property_price
    area = None
    if a.area:
        known = rules.locations.find(a.area)
        area = known.name if known else a.area.strip()
    listings = await t.services.repo.list_property_stock(area=area)
    try:
        matches = match_affordable_stock(listings, _dec(a.max_price), capacity, area, a.min_bedrooms)
    except ExceedsComputedCapacityError as exc:
        raise ToolFailure(exc.error_code, str(exc), computed_max_property_price=str(capacity)) from None
    return {"max_price": str(_dec(a.max_price).quantize(Decimal("0.01"))),
            "computed_max_property_price": str(capacity), "area": area,
            "count": len(matches), "listings": [_json(x) for x in matches]}


async def _search(t: ToolContext, a: SearchArgs) -> dict[str, Any]:
    kb = t.services.kb
    if kb is None:
        raise ToolFailure("KB_UNAVAILABLE", "The regulation knowledge base is not available right now.")
    try:
        return await kb.search(a.query, a.categories, a.top_k)
    except ValueError as exc:
        raise ToolFailure("VALIDATION_ERROR", str(exc)) from None


def _profile(t: ToolContext) -> Profile:
    profile = t.profile
    if profile is None:
        raise ToolFailure("PROFILE_NOT_CONFIRMED", "The buyer's profile has not been confirmed yet.")
    return profile


async def _record_stated(t: ToolContext, a: StatedFiguresArgs) -> dict[str, Any]:
    from app.agent.review import record_stated_figures
    from app.agent.state import FieldValueError

    try:
        session, record, missing = await record_stated_figures(
            repo=t.services.repo, session=t.session, figures=a.figures, request_id=t.request.request_id)
    except FieldValueError as exc:
        raise ToolFailure("VALIDATION_ERROR", str(exc)) from None
    t.session, t.record = session, record
    return {"ok": True, "recorded": sorted(a.figures), "source": "user_stated", "verified": False,
            "profile_status": session.profile_status, "missing_fields": missing}


TOOLS: tuple[Tool, ...] = (
    Tool("compute_dsr", "Debt service ratio of the confirmed profile: net income, commitments, DSR %, "
         "the applicable band and monthly headroom.", NoArgs, _compute_dsr),
    Tool("loan_capacity", "Maximum loan and property price the confirmed profile supports, monthly instalment, "
         "tenure, effective rate, margin of finance and the limiting_factor.", LoanCapacityArgs, _loan_capacity),
    Tool("epf_withdrawal", "Akaun Sejahtera balance and the amount usable for a housing withdrawal under the "
         "2024 three-account structure.", NoArgs, _epf),
    Tool("upfront_costs", "Cash needed upfront at a property price: down payment, stamp duties, legal fees, "
         "valuation, disbursements, EPF applied and shortfall.", UpfrontCostsArgs, _upfront),
    Tool("location_cost", "Monthly commute cost and yearly commuting hours between two Klang Valley areas.",
         LocationCostArgs, _location),
    Tool("simulate_scenario", "Month-by-month cashflow stress test of a life event after buying at a price. "
         "Returns SAFE/AT_RISK/BREAKS, break_month, monthly_shortfall and facts.", ScenarioArgs, _scenario),
    Tool("plan_path_to_approval", "Dated actions that move the buyer from today's capacity to a target price.",
         PathArgs, _path),
    Tool("match_affordable_stock", "Up to 6 listings at or below max_price. max_price must not exceed the "
         "computed max_property_price from loan_capacity.", StockArgs, _stock),
    Tool("search_regulation", "Search the Malaysian housing-finance regulation knowledge base (written in English). "
         "Always write the query in English, whatever language the user writes in. "
         "Cite title and section from the results.", SearchArgs, _search, computes=False),
    Tool("record_stated_figures", "Record a figure the user explicitly states about themselves instead of uploading "
         "a document (e.g. 'assume I earn RM20,000'). Stored as user-stated and unverified; every later result is "
         "labelled unverified. Never use for values found inside a document or for figures the user wants you to "
         "'approve'.", StatedFiguresArgs, _record_stated, computes=False),
)


def _schema(model: type[BaseModel]) -> dict[str, Any]:
    """JSON schema without titles, and with Optional[X] collapsed to X so every function-calling API accepts it."""

    def clean(node: Any) -> Any:
        if isinstance(node, dict):
            node = {k: clean(v) for k, v in node.items() if k != "title"}
            variants = node.get("anyOf")
            if isinstance(variants, list):
                non_null = [v for v in variants if v.get("type") != "null"]
                if len(non_null) == 1:
                    node.pop("anyOf")
                    node.update(non_null[0])
            if node.get("default", 0) is None:
                node.pop("default")
            return node
        if isinstance(node, list):
            return [clean(v) for v in node]
        return node

    schema: dict[str, Any] = clean(model.model_json_schema())
    schema.setdefault("properties", {})
    return schema


def _validation_message(exc: ValidationError) -> str:
    parts = []
    for err in exc.errors()[:3]:
        loc = ".".join(str(p) for p in err.get("loc", ()))
        parts.append(f"{loc}: {err.get('msg')}" if loc else str(err.get("msg")))
    return "; ".join(parts)


class ToolRegistry:
    def __init__(self, tools: tuple[Tool, ...] = TOOLS, timeout_seconds: float = 8) -> None:
        self._tools = {t.name: t for t in tools}
        self.timeout_seconds = timeout_seconds

    def specs(self) -> list[ToolSpec]:
        return [ToolSpec(name=t.name, input_schema=_schema(t.args_model),
                         description=t.description + (_QUOTE_VERBATIM if t.computes else ""))
                for t in self._tools.values()]

    async def execute(self, call: ToolCall, t: ToolContext) -> ToolOutcome:
        """Never raises: every failure becomes {"ok": false, "error_code", "message"} the model can act on."""
        start = time.perf_counter()
        tool = self._tools.get(call.name)
        echo: dict[str, Any] = {}
        computation = None
        sources: list[dict[str, Any]] = []
        try:
            if tool is None:
                raise ToolFailure("UNKNOWN_TOOL", f"There is no tool named {call.name!r}.")
            try:
                args = tool.args_model.model_validate(call.arguments)
            except ValidationError as exc:
                raise ToolFailure("VALIDATION_ERROR", _validation_message(exc)) from None
            echo = args.model_dump(mode="json", exclude_none=True)
            if tool.computes and t.profile is None:
                raise ToolFailure("PROFILE_NOT_CONFIRMED",
                                  "The buyer's profile has not been confirmed yet. Ask them to upload a payslip and "
                                  "EPF statement and to confirm the extracted fields.")
            data = await asyncio.wait_for(tool.handler(t, args), timeout=self.timeout_seconds)
            if tool.computes:
                rules_version = t.services.rules.version
                result: dict[str, Any] = {"ok": True, "currency": "MYR", "rules_version": rules_version,
                                          "inputs_echo": echo, **data}
                if t.unverified_inputs:
                    result["unverified_inputs"] = t.unverified_inputs
                computation = {"tool": call.name, "inputs_echo": echo, "outputs": result,
                               "rules_version": rules_version}
            else:
                result = data
                sources = [dict(r) for r in data.get("results", [])]
        except ToolFailure as exc:
            result = {"ok": False, "error_code": exc.error_code, "message": exc.message, **exc.extra}
        except TimeoutError:
            result = {"ok": False, "error_code": "TOOL_TIMEOUT", "message": "The calculation took too long."}
        except Exception as exc:
            log.error("tool.crashed", tool=call.name, exc_info=exc)
            result = {"ok": False, "error_code": "INTERNAL_ERROR", "message": "The tool failed unexpectedly."}
        outcome = ToolOutcome(call=call, result=result, latency_ms=round((time.perf_counter() - start) * 1000),
                              computation=computation, sources=sources)
        code = result.get("error_code")
        if code:
            log.warning("tool.failed", tool=call.name, error_code=code, latency_ms=outcome.latency_ms)
        else:
            log.info("tool.ok", tool=call.name, latency_ms=outcome.latency_ms, sources=len(sources))
        await self._audit(outcome, echo, t)
        return outcome

    async def _audit(self, outcome: ToolOutcome, echo: dict[str, Any], t: ToolContext) -> None:
        base = {"request_id": t.request.request_id, "session_id": t.session.session_id,
                "user_id": t.request.user_id, "tool": outcome.call.name, "inputs_echo": echo,
                "latency_ms": outcome.latency_ms}
        repo = t.services.repo
        code = outcome.result.get("error_code")
        await repo.write_audit(AuditEvent(event="TOOL_CALL", outcome=code or "ok", **base))
        if code == "VALIDATION_ERROR":
            await repo.write_audit(AuditEvent(event="VALIDATION_ERROR", outcome=code, **base))
        if outcome.computation:
            await repo.write_audit(AuditEvent(
                event="COMPUTATION", outcome="ok", rules_version=outcome.computation["rules_version"],
                outputs={k: v for k, v in outcome.result.items() if k != "projection"}, **base))
