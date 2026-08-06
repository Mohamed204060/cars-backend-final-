"""
test_sup_api.py — اختبارات وحدة لطبقة REST API لخدمة الدعم الفني (SUP)
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth_api import router as auth_router
from auth_repository import InMemoryAuthRepository
from auth_service import IdentityProvider, UserIdentity
from session_repository import InMemorySessionRepository
from sup_api import router as sup_router
from sup_repository import InMemorySupRepository


@pytest.fixture
def app_and_client():
    app = FastAPI()
    app.include_router(auth_router)
    app.include_router(sup_router)

    providers = [IdentityProvider(code="email_password", display_name="كلمة المرور", category="password", is_enabled=True)]
    app.state.auth_repository = InMemoryAuthRepository(providers=providers, identities=[])
    app.state.session_repository = InMemorySessionRepository()
    app.state.sup_repository = InMemorySupRepository()

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


class TestCreateTicket:

    def test_any_user_can_create_ticket(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "user1@example.com")
        resp = client.post("/api/v1/support/tickets", json={"subject": "مشكلة في الدفع"})
        assert resp.status_code == 201
        assert resp.json()["status"] == "open"

    def test_empty_subject_rejected(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "user2@example.com")
        resp = client.post("/api/v1/support/tickets", json={"subject": "  "})
        assert resp.status_code == 400


class TestAssignAndResolve:

    def test_regular_user_cannot_assign(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "user3@example.com")
        ticket_id = client.post("/api/v1/support/tickets", json={"subject": "مشكلة"}).json()["id"]

        resp = client.post(f"/api/v1/support/tickets/{ticket_id}/assign", json={"moderator_ref_id": "mod-1"})
        assert resp.status_code == 403

    def test_moderator_can_assign_and_resolve(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "user4@example.com")
        ticket_id = client.post("/api/v1/support/tickets", json={"subject": "مشكلة"}).json()["id"]

        client.post("/api/v1/auth/logout")
        mod_id = _login_as(app, client, "mod1@example.com", role="support_moderator")
        assign_resp = client.post(f"/api/v1/support/tickets/{ticket_id}/assign", json={"moderator_ref_id": mod_id})
        assert assign_resp.status_code == 200
        assert assign_resp.json()["status"] == "in_progress"

        resolve_resp = client.post(f"/api/v1/support/tickets/{ticket_id}/resolve")
        assert resolve_resp.status_code == 200
        assert resolve_resp.json()["status"] == "resolved"


class TestCloseAndReopen:

    def test_requester_can_close_own_ticket(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "user5@example.com")
        ticket_id = client.post("/api/v1/support/tickets", json={"subject": "مشكلة"}).json()["id"]

        resp = client.post(f"/api/v1/support/tickets/{ticket_id}/close")
        assert resp.status_code == 200
        assert resp.json()["status"] == "closed"
        assert resp.json()["reopen_window_expires_at"] is not None

    def test_requester_can_reopen_within_window(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "user6@example.com")
        ticket_id = client.post("/api/v1/support/tickets", json={"subject": "مشكلة"}).json()["id"]
        client.post(f"/api/v1/support/tickets/{ticket_id}/close")

        resp = client.post(f"/api/v1/support/tickets/{ticket_id}/reopen")
        assert resp.status_code == 200
        assert resp.json()["status"] == "in_progress"

    def test_stranger_cannot_reopen(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "user7@example.com")
        ticket_id = client.post("/api/v1/support/tickets", json={"subject": "مشكلة"}).json()["id"]
        client.post(f"/api/v1/support/tickets/{ticket_id}/close")

        client.post("/api/v1/auth/logout")
        _login_as(app, client, "stranger@example.com")
        resp = client.post(f"/api/v1/support/tickets/{ticket_id}/reopen")
        assert resp.status_code == 403


class TestReplies:

    def test_requester_can_reply_to_own_ticket(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "user8@example.com")
        ticket_id = client.post("/api/v1/support/tickets", json={"subject": "مشكلة"}).json()["id"]

        resp = client.post(f"/api/v1/support/tickets/{ticket_id}/replies", json={"body": "تفاصيل إضافية"})
        assert resp.status_code == 201

    def test_stranger_cannot_view_or_reply(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "user9@example.com")
        ticket_id = client.post("/api/v1/support/tickets", json={"subject": "مشكلة"}).json()["id"]

        client.post("/api/v1/auth/logout")
        _login_as(app, client, "stranger2@example.com")
        reply_resp = client.post(f"/api/v1/support/tickets/{ticket_id}/replies", json={"body": "تدخل غير مصرح"})
        assert reply_resp.status_code == 403
        get_resp = client.get(f"/api/v1/support/tickets/{ticket_id}")
        assert get_resp.status_code == 403

    def test_assigned_moderator_can_reply_and_list(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "user10@example.com")
        ticket_id = client.post("/api/v1/support/tickets", json={"subject": "مشكلة"}).json()["id"]

        client.post("/api/v1/auth/logout")
        mod_id = _login_as(app, client, "mod2@example.com", role="support_moderator")
        client.post(f"/api/v1/support/tickets/{ticket_id}/assign", json={"moderator_ref_id": mod_id})
        reply_resp = client.post(f"/api/v1/support/tickets/{ticket_id}/replies", json={"body": "كيف أساعدك؟"})
        assert reply_resp.status_code == 201

        list_resp = client.get(f"/api/v1/support/tickets/{ticket_id}/replies")
        assert list_resp.status_code == 200
        assert len(list_resp.json()) == 1


class TestMyTickets:

    def test_list_mine_only_own_tickets(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "user11@example.com")
        client.post("/api/v1/support/tickets", json={"subject": "طلبي"})

        client.post("/api/v1/auth/logout")
        _login_as(app, client, "user12@example.com")
        client.post("/api/v1/support/tickets", json={"subject": "طلب آخر"})

        resp = client.get("/api/v1/support/tickets/mine")
        assert resp.status_code == 200
        assert len(resp.json()) == 1
