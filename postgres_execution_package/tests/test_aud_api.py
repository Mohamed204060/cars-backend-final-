"""
test_aud_api.py — اختبارات وحدة لطبقة REST API لخدمة AUD (سجل التدقيق)
Batch 3A Slice 1
"""

import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth_api import router as auth_router
from auth_repository import InMemoryAuthRepository
from auth_service import IdentityProvider, UserIdentity
from session_repository import InMemorySessionRepository
from aud_api import router as aud_router
from aud_repository import AuditEvent, InMemoryAudRepository
from aud_service import record_audit_event_via_repository


@pytest.fixture
def app_and_client():
    app = FastAPI()
    app.include_router(auth_router)
    app.include_router(aud_router)

    providers = [IdentityProvider(code="email_password", display_name="كلمة المرور", category="password", is_enabled=True)]
    app.state.auth_repository = InMemoryAuthRepository(providers=providers, identities=[])
    app.state.session_repository = InMemorySessionRepository()
    app.state.aud_repository = InMemoryAudRepository()

    client = TestClient(app, base_url="https://testserver")
    return app, client


def _login_as(app, client, email: str, role: str = "individual_buyer") -> str:
    repo = app.state.auth_repository
    user_id = repo.create_user()
    repo.set_user_role(user_id, role)
    identity = UserIdentity(id="", user_id=user_id, provider_code="email_password",
                             external_identifier=email, is_verified=True, is_primary=True)
    repo.insert_identity(identity, raw_password="Str0ngPass1!")
    resp = client.post("/api/v1/auth/login", json={"login_identifier": email, "password": "Str0ngPass1!"})
    assert resp.status_code == 200
    return user_id


class TestListAuditEventsAuthorization:

    def test_requires_authentication(self, app_and_client):
        app, client = app_and_client
        resp = client.get("/api/v1/audit/events")
        assert resp.status_code == 401

    def test_forbidden_for_non_admin(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "buyer@example.com", role="individual_buyer")
        resp = client.get("/api/v1/audit/events")
        assert resp.status_code == 403

    def test_forbidden_for_seller(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "seller@example.com", role="individual_seller")
        resp = client.get("/api/v1/audit/events")
        assert resp.status_code == 403

    def test_forbidden_for_moderator(self, app_and_client):
        """moderator ليس ضمن SYSTEM_ADMIN_ROLES = {super_admin, admin} — سجل التدقيق أضيق نطاقًا من الإشراف العام."""
        app, client = app_and_client
        _login_as(app, client, "mod@example.com", role="moderator")
        resp = client.get("/api/v1/audit/events")
        assert resp.status_code == 403

    def test_allowed_for_admin(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "admin@example.com", role="admin")
        resp = client.get("/api/v1/audit/events")
        assert resp.status_code == 200

    def test_allowed_for_super_admin(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "root@example.com", role="super_admin")
        resp = client.get("/api/v1/audit/events")
        assert resp.status_code == 200

    def test_no_write_endpoint_exists(self, app_and_client):
        """قرار تصميمي مقصود: لا POST/PUT/DELETE على /audit/events إطلاقًا."""
        app, client = app_and_client
        _login_as(app, client, "admin2@example.com", role="admin")
        assert client.post("/api/v1/audit/events", json={}).status_code == 405
        assert client.put("/api/v1/audit/events/some-id", json={}).status_code == 405
        assert client.delete("/api/v1/audit/events/some-id").status_code == 405


class TestListAuditEventsFiltersAndPagination:

    def _seed(self, app, u1: str, u2: str):
        repo = app.state.aud_repository
        record_audit_event_via_repository(repo, log_type="security", event_name="identity_added", actor_ref_id=u1)
        record_audit_event_via_repository(repo, log_type="administrative", event_name="store_suspended", actor_ref_id=u2,
                                           reason="انتهاك سياسة")
        record_audit_event_via_repository(repo, log_type="general", event_name="misc_event", actor_ref_id=u1)

    def test_lists_all_events_for_admin(self, app_and_client):
        app, client = app_and_client
        self._seed(app, str(uuid.uuid4()), str(uuid.uuid4()))
        _login_as(app, client, "admin@example.com", role="admin")
        resp = client.get("/api/v1/audit/events")
        assert resp.status_code == 200
        assert resp.json()["pagination"]["total_items"] == 3

    def test_filter_by_log_type(self, app_and_client):
        app, client = app_and_client
        self._seed(app, str(uuid.uuid4()), str(uuid.uuid4()))
        _login_as(app, client, "admin@example.com", role="admin")
        resp = client.get("/api/v1/audit/events", params={"log_type": "security"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["pagination"]["total_items"] == 1
        assert body["items"][0]["event_name"] == "identity_added"

    def test_filter_by_actor_ref_id(self, app_and_client):
        app, client = app_and_client
        u1 = str(uuid.uuid4())
        self._seed(app, u1, str(uuid.uuid4()))
        _login_as(app, client, "admin@example.com", role="admin")
        resp = client.get("/api/v1/audit/events", params={"actor_ref_id": u1})
        assert resp.status_code == 200
        assert resp.json()["pagination"]["total_items"] == 2

    def test_invalid_log_type_returns_400(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "admin@example.com", role="admin")
        resp = client.get("/api/v1/audit/events", params={"log_type": "not_a_real_type"})
        assert resp.status_code == 400

    def test_invalid_actor_ref_id_returns_400(self, app_and_client):
        """Corrective Pass: actor_ref_id عمود UUID فعليًا في aud.events — قيمة غير صالحة تُرفَض قبل DB (400 لا 500)."""
        app, client = app_and_client
        _login_as(app, client, "admin@example.com", role="admin")
        resp = client.get("/api/v1/audit/events", params={"actor_ref_id": "u1"})
        assert resp.status_code == 400
        assert resp.json()["error_code"] == "INVALID_REF_ID"

    def test_events_include_administrative_reason_field(self, app_and_client):
        app, client = app_and_client
        self._seed(app, str(uuid.uuid4()), str(uuid.uuid4()))
        _login_as(app, client, "admin@example.com", role="admin")
        resp = client.get("/api/v1/audit/events", params={"log_type": "administrative"})
        assert resp.json()["items"][0]["reason"] == "انتهاك سياسة"

    def test_pagination_page_size(self, app_and_client):
        app, client = app_and_client
        self._seed(app, str(uuid.uuid4()), str(uuid.uuid4()))
        _login_as(app, client, "admin@example.com", role="admin")
        resp = client.get("/api/v1/audit/events", params={"page": 1, "page_size": 2})
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["items"]) == 2
        assert body["pagination"]["total_items"] == 3


class TestAuditEventsPaginationBounds:

    def test_page_zero_rejected(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "admin@example.com", role="admin")
        resp = client.get("/api/v1/audit/events", params={"page": 0})
        assert resp.status_code == 422

    def test_negative_page_rejected(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "admin@example.com", role="admin")
        resp = client.get("/api/v1/audit/events", params={"page": -1})
        assert resp.status_code == 422

    def test_page_size_zero_rejected(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "admin@example.com", role="admin")
        resp = client.get("/api/v1/audit/events", params={"page_size": 0})
        assert resp.status_code == 422

    def test_page_size_over_max_rejected(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "admin@example.com", role="admin")
        resp = client.get("/api/v1/audit/events", params={"page_size": 1000})
        assert resp.status_code == 422

    def test_page_size_at_max_accepted(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "admin@example.com", role="admin")
        resp = client.get("/api/v1/audit/events", params={"page_size": 100})
        assert resp.status_code == 200
