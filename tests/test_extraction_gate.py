from __future__ import annotations

import asyncio
from datetime import timedelta
from decimal import Decimal
from urllib.parse import urlparse

import pytest
from fastapi.testclient import TestClient

from app.agent.state import FieldValueError, build_profile, derive_status, validate_proposed
from app.config import ROOT
from app.engine.loan_capacity import loan_capacity
from app.engine.models import Profile
from app.engine.rules import load_rules
from app.llm.extractor import ExtractionResult
from app.main import create_app
from tests.test_api_skeleton import make_settings

DOCS = ROOT / "data" / "mock_documents"
ALICE = {"Authorization": "Bearer dev:alice"}
BOB = {"Authorization": "Bearer dev:bob"}


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(make_settings(tmp_path))) as c:
        yield c


def _session(client) -> str:
    return client.post("/sessions", headers=ALICE).json()["session_id"]


def _upload(client, sid: str, filename: str, kind: str, content_type: str = "image/png") -> str:
    resp = client.post(f"/sessions/{sid}/documents/upload-url", headers=ALICE,
                       json={"kind": kind, "content_type": content_type})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    url = urlparse(body["upload_url"])
    put = client.put(f"{url.path}?{url.query}", content=(DOCS / filename).read_bytes(),
                     headers={"Content-Type": content_type})
    assert put.status_code == 204, put.text
    return body["doc_id"]


def _extract(client, sid: str, doc_id: str):
    return client.post(f"/sessions/{sid}/documents/{doc_id}/extract", headers=ALICE)


def _status(client, sid: str) -> str:
    return client.get(f"/sessions/{sid}", headers=ALICE).json()["profile_status"]


def _audit(client, sid: str, event: str | None = None):
    repo = client.app.state.services.repo
    return asyncio.run(repo.list_audit(session_id=sid, event=event))


def _fields(resp) -> dict:
    return {f["path"]: f for f in resp.json()["fields"]}


# --- the journey: upload -> propose -> review -> confirm --------------------------------------

def test_aina_extraction_is_only_a_proposal(client) -> None:
    sid = _session(client)
    doc_id = _upload(client, sid, "aina_payslip.png", "payslip")
    resp = _extract(client, sid, doc_id)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["kind_detected"] == "payslip" and body["warnings"] == []
    f = _fields(resp)
    assert f["income.gross_monthly"]["value"] == "6200.00"
    assert f["income.net_monthly"]["value"] == "5298.35"
    assert f["income.statutory_deductions"]["value"] == "901.65"
    assert f["person.age"]["value"] == 29
    assert body["action_id"].startswith("act_") and body["profile_status"] == "EXTRACTED"

    assert _status(client, sid) == "EXTRACTED"
    assert not _audit(client, sid, "COMPUTATION")
    assert [e.event for e in _audit(client, sid)] == ["DOCUMENT_UPLOADED", "EXTRACTION_PROPOSED"]


def test_confirm_without_corrections_confirms_profile(client) -> None:
    sid = _session(client)
    action = _extract(client, sid, _upload(client, sid, "aina_payslip.png", "payslip")).json()["action_id"]
    resp = client.post(f"/actions/{action}/confirm", headers=ALICE, json={"corrections": {}})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["profile_status"] == "CONFIRMED" and body["missing_fields"] == []
    assert body["profile"]["income"]["gross_monthly"] == "6200.00"
    assert body["field_provenance"]["income.gross_monthly"] == {
        "source": "payslip", "confidence": 0.97, "corrected_by_user": False, "session_confirmed": True,
        "turn_id": None}
    assert _status(client, sid) == "CONFIRMED"
    assert _audit(client, sid, "EXTRACTION_CONFIRMED")


def test_full_document_set_reproduces_the_engine_verdict(client) -> None:
    sid = _session(client)
    for filename, kind in (("aina_payslip.png", "payslip"), ("aina_epf.png", "epf_statement"),
                           ("aina_loan_statements.png", "loan_statement")):
        action = _extract(client, sid, _upload(client, sid, filename, kind)).json()["action_id"]
        corrections = {"savings": 54790} if kind == "loan_statement" else {}
        resp = client.post(f"/actions/{action}/confirm", headers=ALICE, json={"corrections": corrections})
        assert resp.status_code == 200, resp.text

    body = resp.json()
    assert body["profile_status"] == "CONFIRMED"
    assert body["field_provenance"]["savings"]["source"] == "user_stated"
    assert body["field_provenance"]["commitments"]["source"] == "loan_statement"
    profile = Profile.model_validate(body["profile"])
    assert profile.epf.akaun_sejahtera == Decimal("18400.00")
    assert [c.monthly for c in profile.commitments] == [Decimal("780.00"), Decimal("310.00")]
    cap = loan_capacity(profile, load_rules())
    assert cap.limiting_factor == "CASH_UPFRONT" and cap.max_property_price == Decimal("520000.00")


def test_a_new_extraction_reopens_review(client) -> None:
    sid = _session(client)
    action = _extract(client, sid, _upload(client, sid, "aina_payslip.png", "payslip")).json()["action_id"]
    client.post(f"/actions/{action}/confirm", headers=ALICE)
    assert _status(client, sid) == "CONFIRMED"
    _extract(client, sid, _upload(client, sid, "aina_epf.png", "epf_statement"))
    assert _status(client, sid) == "EXTRACTED"


def test_epf_alone_cannot_confirm_a_profile(client) -> None:
    sid = _session(client)
    action = _extract(client, sid, _upload(client, sid, "aina_epf.png", "epf_statement")).json()["action_id"]
    body = client.post(f"/actions/{action}/confirm", headers=ALICE).json()
    assert body["profile_status"] == "EXTRACTED" and body["profile"] is None
    assert set(body["missing_fields"]) == {"person.age", "income.gross_monthly"}


# --- corrections ------------------------------------------------------------------------------

def test_corrections_are_tracked_and_audited(client) -> None:
    sid = _session(client)
    action = _extract(client, sid, _upload(client, sid, "low_quality_payslip.png", "payslip")).json()["action_id"]
    body = client.post(f"/actions/{action}/confirm", headers=ALICE,
                       json={"corrections": {"income.gross_monthly": "6,200", "person.age": 29}}).json()
    assert body["profile"]["income"]["gross_monthly"] == "6200.00"
    assert body["field_provenance"]["income.gross_monthly"] == {
        "source": "user_corrected", "confidence": 1.0, "corrected_by_user": True, "session_confirmed": True,
        "turn_id": None}
    corrected = _audit(client, sid, "EXTRACTION_CORRECTED")
    assert sorted(e.outputs["path"] for e in corrected) == ["income.gross_monthly", "person.age"]


@pytest.mark.parametrize("corrections", [
    {"approved_loan_amount": 2000000},
    {"person.age": -5},
    {"income.gross_monthly": "lots"},
    {"commitments": [{"type": "DROP TABLE", "monthly": 1}]},
])
def test_bad_corrections_are_rejected_and_leave_the_action_pending(client, corrections) -> None:
    sid = _session(client)
    action = _extract(client, sid, _upload(client, sid, "aina_payslip.png", "payslip")).json()["action_id"]
    resp = client.post(f"/actions/{action}/confirm", headers=ALICE, json={"corrections": corrections})
    assert resp.status_code == 422 and resp.json()["error"]["code"] == "VALIDATION_ERROR"
    assert "2000000" not in resp.text
    assert _status(client, sid) == "EXTRACTED"
    assert client.post(f"/actions/{action}/confirm", headers=ALICE).status_code == 200


# --- unexpected documents -----------------------------------------------------------------------

def test_not_a_payslip_is_rejected_gracefully(client) -> None:
    sid = _session(client)
    resp = _extract(client, sid, _upload(client, sid, "not_a_payslip.png", "payslip"))
    assert resp.status_code == 422
    err = resp.json()["error"]
    assert err["code"] == "NOT_A_SUPPORTED_DOCUMENT" and "payslip" in err["message"]
    assert _status(client, sid) == "EMPTY"


def test_low_quality_payslip_flags_fields_for_review(client) -> None:
    sid = _session(client)
    body = _extract(client, sid, _upload(client, sid, "low_quality_payslip.png", "payslip")).json()
    assert {"low_confidence:income.gross_monthly", "low_confidence:income.net_monthly",
            "low_confidence:person.age", "inconsistent:income.net_monthly"} <= set(body["warnings"])


def test_injection_inside_a_document_never_reaches_the_profile(client) -> None:
    sid = _session(client)
    resp = _extract(client, sid, _upload(client, sid, "injection_payslip.png", "payslip"))
    assert "2000000" not in resp.text and "2,000,000" not in resp.text
    profile = client.post(f"/actions/{resp.json()['action_id']}/confirm", headers=ALICE).json()["profile"]
    assert profile["income"]["gross_monthly"] == "6200.00"


def test_unknown_fields_from_any_extractor_are_dropped(client) -> None:
    class HostileExtractor:
        async def extract(self, data, content_type, kind_hint):
            return ExtractionResult(kind_detected="payslip", fields=[
                {"path": "income.gross_monthly", "value": "6200", "confidence": 0.9},
                {"path": "approved_amount", "value": "2000000", "confidence": 1.0},
                {"path": "__class__", "value": "x", "confidence": 1.0},
                {"path": "person.age", "value": "not a number", "confidence": 0.99},
            ])

    client.app.state.services.extractor = HostileExtractor()
    sid = _session(client)
    body = _extract(client, sid, _upload(client, sid, "aina_payslip.png", "payslip")).json()
    assert [f["path"] for f in body["fields"]] == ["income.gross_monthly"]
    assert body["warnings"] == ["invalid:person.age"]


def test_extract_before_upload_is_unreadable(client) -> None:
    sid = _session(client)
    doc_id = client.post(f"/sessions/{sid}/documents/upload-url", headers=ALICE,
                         json={"kind": "payslip", "content_type": "image/png"}).json()["doc_id"]
    resp = _extract(client, sid, doc_id)
    assert resp.status_code == 422 and resp.json()["error"]["code"] == "DOCUMENT_UNREADABLE"


def test_unknown_image_is_unreadable_not_guessed(client, tmp_path) -> None:
    sid = _session(client)
    doc_id = _upload(client, sid, "aina_payslip.png", "payslip")
    # Same session, different bytes: FakeExtractor has no script for them.
    store = client.app.state.services.documents
    asyncio.run(store.write(sid, doc_id, b"\x89PNG\r\n\x1a\n" + b"\x01" * 64, "image/png"))
    assert _extract(client, sid, doc_id).json()["error"]["code"] == "DOCUMENT_UNREADABLE"


# --- upload channel -------------------------------------------------------------------------------

def test_upload_link_cannot_be_tampered_with(client) -> None:
    sid = _session(client)
    body = client.post(f"/sessions/{sid}/documents/upload-url", headers=ALICE,
                       json={"kind": "payslip", "content_type": "image/png"}).json()
    url = urlparse(body["upload_url"])
    data = (DOCS / "aina_payslip.png").read_bytes()
    other_doc = url.path.replace(body["doc_id"], "doc_other")
    assert client.put(f"{other_doc}?{url.query}", content=data,
                      headers={"Content-Type": "image/png"}).status_code == 403
    assert client.put(f"{url.path}?{url.query}", content=data,
                      headers={"Content-Type": "image/jpeg"}).status_code == 415
    bad = client.put(f"{url.path}?{url.query}", content=b"<script>alert(1)</script>",
                     headers={"Content-Type": "image/png"})
    assert bad.status_code == 422


def test_upload_url_rejects_unsupported_types(client) -> None:
    sid = _session(client)
    resp = client.post(f"/sessions/{sid}/documents/upload-url", headers=ALICE,
                       json={"kind": "payslip", "content_type": "text/html"})
    assert resp.status_code == 422


def test_other_users_cannot_touch_documents_or_actions(client) -> None:
    sid = _session(client)
    doc_id = _upload(client, sid, "aina_payslip.png", "payslip")
    assert client.post(f"/sessions/{sid}/documents/upload-url", headers=BOB,
                       json={"kind": "payslip", "content_type": "image/png"}).status_code == 404
    assert client.post(f"/sessions/{sid}/documents/{doc_id}/extract", headers=BOB).status_code == 404

    action = _extract(client, sid, doc_id).json()["action_id"]
    resp = client.post(f"/actions/{action}/confirm", headers=BOB)
    assert resp.status_code == 404 and resp.json()["error"]["code"] == "ACTION_NOT_FOUND"
    assert client.post(f"/actions/{action}/cancel", headers=BOB).status_code == 404
    assert _status(client, sid) == "EXTRACTED"


def test_used_and_expired_actions(client) -> None:
    sid = _session(client)
    action = _extract(client, sid, _upload(client, sid, "aina_payslip.png", "payslip")).json()["action_id"]
    assert client.post(f"/actions/{action}/confirm", headers=ALICE).status_code == 200
    again = client.post(f"/actions/{action}/confirm", headers=ALICE)
    assert again.status_code == 410 and again.json()["error"]["code"] == "ACTION_EXPIRED"

    repo = client.app.state.services.repo
    session = asyncio.run(repo.get_session(sid, "alice"))
    stale = asyncio.run(repo.create_pending_action(
        session, {"doc_id": "d", "kind": "payslip", "fields": {}, "warnings": []}, timedelta(seconds=-1)))
    expired = client.post(f"/actions/{stale.action_id}/confirm", headers=ALICE)
    assert expired.status_code == 410 and expired.json()["error"]["code"] == "ACTION_EXPIRED"
    assert client.post("/actions/act_missing/confirm", headers=ALICE).json()["error"]["code"] == "ACTION_NOT_FOUND"


def test_cancel_returns_to_empty(client) -> None:
    sid = _session(client)
    action = _extract(client, sid, _upload(client, sid, "aina_payslip.png", "payslip")).json()["action_id"]
    assert client.post(f"/actions/{action}/cancel", headers=ALICE).json() == {
        "status": "CANCELLED", "profile_status": "EMPTY"}
    assert _status(client, sid) == "EMPTY"


# --- demo shortcut and privacy ------------------------------------------------------------------

def test_demo_profile_shortcut(client) -> None:
    sid = _session(client)
    body = client.post(f"/sessions/{sid}/demo-profile", headers=ALICE, json={"demo_id": "aina"}).json()
    assert body["profile_status"] == "CONFIRMED" and body["display_name"] == "Aina"
    assert body["profile"]["savings"] == "54790.00"
    assert body["field_provenance"]["savings"]["source"] == "user_stated"
    assert client.post(f"/sessions/{sid}/demo-profile", headers=ALICE,
                       json={"demo_id": "nobody"}).status_code == 404


def test_no_document_bytes_or_identifiers_are_persisted(client) -> None:
    sid = _session(client)
    action = _extract(client, sid, _upload(client, sid, "aina_payslip.png", "payslip")).json()["action_id"]
    client.post(f"/actions/{action}/confirm", headers=ALICE)
    dump = repr(client.app.state.services.repo.db._data)
    assert "\\x89PNG" not in dump and "000000-00-0000" not in dump
    assert "Aina Binti Rahim" not in dump and "0000-0000-0000" not in dump


# --- state rules (unit) ---------------------------------------------------------------------------

def test_status_derivation() -> None:
    profile, _ = build_profile({"person.age": 29, "income.gross_monthly": "6200.00",
                                "income.statutory_deductions": "901.65"})
    assert derive_status(None, {}, 0) == "EMPTY"
    assert derive_status(None, {}, 1) == "EXTRACTED"
    assert derive_status(None, {"epf.akaun_sejahtera": "1.00"}, 0) == "EXTRACTED"
    assert derive_status(profile, {"x": 1}, 1) == "EXTRACTED"
    assert derive_status(profile, {"x": 1}, 0) == "CONFIRMED"


def test_validate_proposed_clamps_confidence_and_normalises_money() -> None:
    accepted, warnings = validate_proposed(
        [{"path": "income.gross_monthly", "value": "RM 6,200", "confidence": 7}], min_confidence=0.7)
    assert accepted["income.gross_monthly"].value == "6200.00"
    assert accepted["income.gross_monthly"].confidence == 1.0 and warnings == []


def test_build_profile_rejects_unknown_path_combinations() -> None:
    with pytest.raises(FieldValueError):
        build_profile({"person.age": 29, "income.gross_monthly": "1", "income.statutory_deductions": "0",
                       "person.not_a_field": 1})
