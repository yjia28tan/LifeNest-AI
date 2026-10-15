"""The agent turn (spec section 6.2): a small explicit async tool-calling loop.

LLM proposes tool calls -> registry executes them concurrently against the engine -> results go back to the LLM ->
repeat until it answers or AGENT_MAX_STEPS. Figures shown to the user come from tool outputs (cards, computations);
the reply text is checked against them before it leaves the server.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.agent.cards import build_cards
from app.agent.grounding import allowed_values, check_grounding, extract_figures, prohibited_claims
from app.agent.prompts import render_system_prompt
from app.agent.tools import ToolContext, ToolOutcome, ToolRegistry
from app.llm.gateway import LLMError
from app.observability import get_logger
from app.storage.models import AuditEvent, MessageRecord, SessionRecord, utcnow
from app.storage.repository import new_id

if TYPE_CHECKING:
    from app.deps import RequestContext, Services

log = get_logger("lifenest.agent")

DISCLAIMER = "Indicative only. Not a lending decision."
HISTORY_TURNS = 10
STEP_LIMIT_REPLY = "I couldn't complete that. Could you rephrase or break it into smaller steps?"
LLM_FAILURE_REPLY = "I'm having trouble reaching the reasoning service right now. Please try again in a moment."
TIMEOUT_REPLY = "That took longer than expected, so I stopped. Please try again."
UNGROUNDED_REPLY = ("I've put the figures I computed in the cards below. I held back my written summary because it "
                    "contained a number I couldn't verify against those results.")
APPROVAL_REPLY = ("I can't approve a loan or promise approval — only a bank can make that decision. The figures in "
                  "the cards below are an indicative estimate of what you can afford, not a lending decision.")


@dataclass
class TurnState:
    reply: str = ""
    outcomes: list[ToolOutcome] = field(default_factory=list)
    llm_error: str | None = None


def _history_messages(history: list[MessageRecord]) -> list[dict[str, Any]]:
    return [{"role": m.role, "content": m.content} for m in history[-HISTORY_TURNS * 2:]]


async def _loop(services: Services, registry: ToolRegistry, system: str, messages: list[dict[str, Any]],
                tctx: ToolContext, state: TurnState) -> None:
    specs = registry.specs()
    max_steps = services.settings.agent_max_steps
    for step in range(max_steps):
        final = step == max_steps - 1
        # On the last step offer no tools, so the model must answer from the results it already has.
        log.info("turn.llm.start", step=step + 1, max_steps=max_steps, messages=len(messages),
                 tools_offered=0 if final else len(specs))
        resp = await services.llm.complete(system, messages, [] if final else specs)
        log.info("turn.llm.done", step=step + 1, stop_reason=resp.stop_reason,
                 tool_calls=[c.name for c in resp.tool_calls], has_text=bool(resp.text))
        if not resp.tool_calls:
            state.reply = (resp.text or "").strip() or STEP_LIMIT_REPLY
            return
        if final:
            break
        messages.append({"role": "assistant", "content": resp.text, "provider_turn": resp.provider_turn,
                         "tool_calls": [c.model_dump() for c in resp.tool_calls]})
        outcomes = await asyncio.gather(*(registry.execute(c, tctx) for c in resp.tool_calls))
        state.outcomes.extend(outcomes)
        messages.extend({"role": "tool", "tool_call_id": o.call.id, "name": o.call.name, "result": o.llm_view}
                        for o in outcomes)
    log.warning("turn.step_limit_reached", max_steps=max_steps)
    state.reply = STEP_LIMIT_REPLY


async def run_turn(services: Services, ctx: RequestContext, session: SessionRecord, message: str) -> dict[str, Any]:
    repo, settings = services.repo, services.settings
    log.info("turn.start", chars=len(message), profile_status=session.profile_status, llm=settings.llm_backend)
    record = await repo.get_profile(session.session_id, ctx.user_id)
    history = await repo.list_messages(session.session_id, limit=HISTORY_TURNS * 2)
    log.info("turn.context.loaded", history_messages=len(history), has_profile=bool(record and record.profile))
    profile_json = record.profile.model_dump(mode="json") if record and record.profile else None

    system = render_system_prompt(timezone=settings.timezone, profile_status=session.profile_status,
                                  profile=profile_json if session.profile_status == "CONFIRMED" else None,
                                  rules_version=services.rules.version)
    messages = [*_history_messages(history), {"role": "user", "content": message}]
    registry = ToolRegistry(timeout_seconds=settings.tool_timeout_seconds)
    tctx = ToolContext(services=services, request=ctx, session=session, record=record)
    state = TurnState()

    audit_base = {"request_id": ctx.request_id, "session_id": session.session_id, "user_id": ctx.user_id}
    try:
        await asyncio.wait_for(_loop(services, registry, system, messages, tctx, state),
                               timeout=settings.turn_timeout_seconds)
    except TimeoutError:
        log.error("turn.timeout", timeout_seconds=settings.turn_timeout_seconds, tools_run=len(state.outcomes))
        state.reply = TIMEOUT_REPLY
        await repo.write_audit(AuditEvent(event="LLM_ERROR", outcome="TURN_TIMEOUT", **audit_base))
    except LLMError as exc:
        log.error("turn.llm_failed", reason=str(exc)[:80], exc_info=exc)
        state.reply = LLM_FAILURE_REPLY
        await repo.write_audit(AuditEvent(event="LLM_ERROR", outcome=str(exc)[:80], **audit_base))
    log.info("turn.loop.done", tools_run=len(state.outcomes),
             tools_failed=sum(1 for o in state.outcomes if not o.ok))

    computations = [o.computation for o in state.outcomes if o.computation]
    sources = [s for o in state.outcomes for s in o.sources]
    user_texts = [m.content for m in history if m.role == "user"] + [message]
    # A user-stated figure recorded during the turn may have changed the profile and its status.
    session, record = tctx.session, tctx.record
    profile_json = record.profile.model_dump(mode="json") if record and record.profile else None

    grounding = check_grounding(state.reply, allowed_values(computations, sources, user_texts, profile_json))
    log.info("turn.grounding.checked", ok=grounding.ok, unmatched=len(grounding.unmatched))
    grounding_status = "ok"
    if not grounding.ok:
        grounding_status = "withheld" if settings.grounding_check == "enforce" else "flagged"
        await repo.write_audit(AuditEvent(event="UNGROUNDED_FIGURE", outcome=grounding_status,
                                          outputs={"figures": list(grounding.unmatched)}, **audit_base))
        log.warning("UNGROUNDED_FIGURE", count=len(grounding.unmatched), mode=settings.grounding_check)
        if settings.grounding_check == "enforce":
            state.reply = UNGROUNDED_REPLY

    claims = prohibited_claims(state.reply)
    if claims:
        # Never configurable: an approval promise is replaced in every mode (spec principle 5).
        grounding_status = "withheld"
        await repo.write_audit(AuditEvent(event="PROHIBITED_CLAIM", outcome="withheld",
                                          outputs={"claims": claims[:5]}, **audit_base))
        log.warning("PROHIBITED_CLAIM", count=len(claims))
        state.reply = APPROVAL_REPLY

    named = [f for f in extract_figures(message) if f.text.upper().startswith("RM")]
    named_price = str(named[-1].value * named[-1].multiplier) if named else None
    cards = build_cards(computations, sources, named_price)
    tool_trace = [o.trace for o in state.outcomes]
    turn_id, now = new_id("turn"), utcnow()
    message_id = new_id("msg")

    await repo.add_message(session.session_id, MessageRecord(
        message_id=new_id("msg"), role="user", content=message, turn_id=turn_id, created_at=now,
        request_id=ctx.request_id))
    await repo.add_message(session.session_id, MessageRecord(
        message_id=message_id, role="assistant", content=state.reply, turn_id=turn_id,
        created_at=utcnow(), request_id=ctx.request_id, tool_trace=tool_trace, computations=computations,
        sources=[{k: s[k] for k in ("doc_id", "title", "section")} for s in sources], cards=cards))
    log.info("turn.done", grounding=grounding_status, cards=len(cards), sources=len(sources),
             profile_status=session.profile_status)

    return {
        "request_id": ctx.request_id,
        "message_id": message_id,
        "reply": state.reply,
        "cards": cards,
        "sources": sources,
        "computations": [{"tool": c["tool"], "inputs_echo": c["inputs_echo"], "outputs": c["outputs"],
                          "rules_version": c["rules_version"]} for c in computations],
        "tool_trace": tool_trace,
        "grounding": {"status": grounding_status, "unmatched": list(grounding.unmatched)},
        "profile_status": session.profile_status,
        "disclaimer": DISCLAIMER,
    }
