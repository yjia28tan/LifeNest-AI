from __future__ import annotations

import os
from datetime import timedelta
from decimal import Decimal

import pytest

from app.config import ROOT
from app.engine.models import Profile
from app.storage.memory import MemoryDocDB
from app.storage.models import AuditEvent, DocumentRecord, MessageRecord, ProfileRecord, utcnow
from app.storage.repository import (
    PROFILES,
    ActionExpiredError,
    ActionNotPendingError,
    AuthzDeniedError,
    NotFoundError,
    Repository,
)
from app.storage.seed_data import load_demo_profiles, load_property_stock

DAY = timedelta(hours=24)


@pytest.fixture(params=["memory", "firestore"])
async def repo(request) -> Repository:
    if request.param == "memory":
        return Repository(MemoryDocDB())
    if not os.environ.get("FIRESTORE_EMULATOR_HOST"):
        pytest.skip("FIRESTORE_EMULATOR_HOST not set")
    from app.storage.firestore import FirestoreDocDB

    db = FirestoreDocDB(project="lifenest-test")
    if not await db.ping():
        pytest.skip("Firestore emulator not reachable")
    return Repository(db)


@pytest.fixture(scope="module")
def demo_profiles():
    return load_demo_profiles(ROOT / "data" / "demo_profiles.json")


def _profile_record(session, demo_profiles) -> ProfileRecord:
    aina = demo_profiles[0]
    return ProfileRecord(session_id=session.session_id, user_id=session.user_id, status="CONFIRMED",
                         confirmed_at=utcnow(), profile=aina.profile, field_provenance=aina.field_provenance)


async def test_session_scoped_by_user(repo: Repository) -> None:
    s = await repo.create_session("user_a", "my-2026-07", DAY)
    assert (await repo.get_session(s.session_id, "user_a")).profile_status == "EMPTY"

    with pytest.raises(AuthzDeniedError):
        await repo.get_session(s.session_id, "user_b")
    denied = await repo.list_audit(session_id=s.session_id, event="AUTHZ_DENIED")
    assert denied and denied[0].user_id == "user_b"

    with pytest.raises(NotFoundError) as missing:
        await repo.get_session("ses_does_not_exist", "user_a")
    assert not isinstance(missing.value, AuthzDeniedError)


async def test_expired_session_is_not_found(repo: Repository) -> None:
    s = await repo.create_session("user_a", "my-2026-07", timedelta(seconds=-1))
    with pytest.raises(NotFoundError):
        await repo.get_session(s.session_id, "user_a")


async def test_update_session(repo: Repository) -> None:
    s = await repo.create_session("user_a", "my-2026-07", DAY)
    updated = await repo.update_session(s, profile_status="CONFIRMED")
    assert (await repo.get_session(s.session_id, "user_a")).profile_status == "CONFIRMED"
    assert updated.updated_at >= s.updated_at


async def test_profile_round_trip_keeps_exact_decimals(repo: Repository, demo_profiles) -> None:
    s = await repo.create_session("user_a", "my-2026-07", DAY)
    rec = _profile_record(s, demo_profiles)
    await repo.save_profile(rec)

    raw = await repo.db.get(PROFILES, s.session_id)
    assert raw["profile"]["income"]["net_monthly"] == "5298.35"  # string, never a float

    loaded = await repo.get_profile(s.session_id, "user_a")
    assert loaded is not None and loaded.profile == rec.profile
    assert loaded.profile.income.statutory_deductions == Decimal("901.65")
    with pytest.raises(AuthzDeniedError):
        await repo.get_profile(s.session_id, "user_b")


async def test_messages_return_latest_in_order(repo: Repository) -> None:
    s = await repo.create_session("user_a", "my-2026-07", DAY)
    base = utcnow()
    for i in range(5):
        await repo.add_message(s.session_id, MessageRecord(
            message_id=f"msg_{i}", role="user" if i % 2 == 0 else "assistant", content=f"m{i}",
            turn_id=f"t{i // 2}", created_at=base + timedelta(seconds=i), request_id=f"req_{i}",
        ))
    latest = await repo.list_messages(s.session_id, limit=3)
    assert [m.content for m in latest] == ["m2", "m3", "m4"]


async def test_audit_never_stores_forbidden_fields(repo: Repository) -> None:
    await repo.write_audit(AuditEvent(
        event="EXTRACTION_PROPOSED", session_id="ses_scrub",
        inputs_echo={"ic_number": "000000-00-0000", "kind": "payslip"},
        outputs={"fields": [{"path": "income.gross_monthly", "raw_text": "GAJI 6200"}], "Bank_Account": "0000"},
    ))
    [event] = await repo.list_audit(session_id="ses_scrub")
    assert event.inputs_echo == {"kind": "payslip"}
    assert event.outputs == {"fields": [{"path": "income.gross_monthly"}]}


async def test_pending_action_lifecycle(repo: Repository) -> None:
    s = await repo.create_session("user_a", "my-2026-07", DAY)
    action = await repo.create_pending_action(s, {"income.gross_monthly": "6200.00"}, timedelta(minutes=15))

    with pytest.raises(AuthzDeniedError):
        await repo.get_pending_action(action.action_id, "user_b")
    fetched = await repo.get_pending_action(action.action_id, "user_a")
    assert fetched.proposed_fields == {"income.gross_monthly": "6200.00"}

    await repo.set_action_status(fetched, "APPLIED")
    with pytest.raises(ActionNotPendingError):
        await repo.get_pending_action(action.action_id, "user_a")

    stale = await repo.create_pending_action(s, {}, timedelta(seconds=-1))
    with pytest.raises(ActionExpiredError):
        await repo.get_pending_action(stale.action_id, "user_a")


async def test_purge_removes_everything_and_is_audited(repo: Repository, demo_profiles) -> None:
    s = await repo.create_session("user_a", "my-2026-07", DAY)
    await repo.save_profile(_profile_record(s, demo_profiles))
    await repo.add_message(s.session_id, MessageRecord(
        message_id="msg_1", role="user", content="hi", turn_id="t1", created_at=utcnow(), request_id="req_1"))
    for doc_id, kind in (("doc_pay", "payslip"), ("doc_epf", "epf_statement")):
        await repo.save_document(DocumentRecord(
            doc_id=doc_id, session_id=s.session_id, kind=kind, content_type="image/png",
            gcs_uri=f"local://{s.session_id}/{doc_id}", expires_at=utcnow() + DAY))
    await repo.create_pending_action(s, {}, timedelta(minutes=15))

    counts = await repo.purge_session(s, request_id="req_purge")
    assert counts == {"documents": 2, "messages": 1, "profile": 1, "pending_actions": 1}

    with pytest.raises(NotFoundError):
        await repo.get_session(s.session_id, "user_a")
    assert await repo.get_profile(s.session_id, "user_a") is None
    assert await repo.list_messages(s.session_id) == []
    assert await repo.list_documents(s.session_id) == []
    purged = await repo.list_audit(session_id=s.session_id, event="DOCUMENT_PURGED")
    assert {e.outputs["doc_id"] for e in purged} == {"doc_pay", "doc_epf"}
    assert await repo.list_audit(session_id=s.session_id, event="SESSION_PURGED")


async def test_property_stock_seed_is_idempotent_and_queryable(repo: Repository) -> None:
    listings = load_property_stock(ROOT / "data" / "property_stock.json")
    assert await repo.replace_property_stock(listings) == len(listings)
    assert await repo.replace_property_stock(listings) == len(listings)
    assert len(await repo.list_property_stock()) == len(listings)

    kajang = await repo.list_property_stock(area="Kajang", max_price=Decimal("450000"))
    assert kajang and all(x.area == "Kajang" and x.price <= 450000 for x in kajang)
    assert all(isinstance(x.price, Decimal) for x in kajang)
    assert {x.listing_id for x in kajang} == {x.listing_id for x in listings
                                              if x.area == "Kajang" and x.price <= 450000}


async def test_demo_profiles_seed(repo: Repository, demo_profiles) -> None:
    assert await repo.replace_demo_profiles(demo_profiles) == 3
    aina = await repo.get_demo_profile("aina")
    assert isinstance(aina.profile, Profile)
    assert aina == demo_profiles[0]
    assert [p.demo_id for p in await repo.list_demo_profiles()] == ["aina", "kumar", "mei"]
