"""
test_ntf_api.py — اختبارات وحدة لطبقة REST API لمركز الإشعارات
لا Endpoint لإنشاء إشعار (إدارة الحملات مؤجَّلة)؛ يُزرَع مباشرة عبر
المستودع، بنفس أسلوب البيانات التمهيدية المستخدَم لبيانات PCT/Store سابقًا.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth_api import router as auth_router
from auth_repository import InMemoryAuthRepository
from auth_service import IdentityProvider, UserIdentity
from session_repository import InMemorySessionRepository
from ntf_api import router as ntf_router
from ntf_repository import InMemoryNtfRepository
from ntf_service import NotificationCenterEntry
from aud_repository import InMemoryAudRepository


@pytest.fixture
def app_and_client():
    app = FastAPI()
    app.include_router(auth_router)
    app.include_router(ntf_router)

    providers = [IdentityProvider(code="email_password", display_name="كلمة المرور", category="password", is_enabled=True)]
    app.state.auth_repository = InMemoryAuthRepository(providers=providers, identities=[])
    app.state.session_repository = InMemorySessionRepository()
    app.state.aud_repository = InMemoryAudRepository()
    app.state.ntf_repository = InMemoryNtfRepository()

    client = TestClient(app, base_url="https://testserver")
    return app, client


def _login_as(app, client, email: str) -> str:
    repo = app.state.auth_repository
    user_id = repo.create_user()
    identity = UserIdentity(id="", user_id=user_id, provider_code="email_password",
                             external_identifier=email, is_verified=True, is_primary=True)
    repo.insert_identity(identity, raw_password="Str0ngPass1!")
    resp = client.post("/api/v1/auth/login", json={"login_identifier": email, "password": "Str0ngPass1!"})
    assert resp.status_code == 200
    return user_id


def _seed_notification(app, user_id: str) -> str:
    entry = app.state.ntf_repository.insert_notification_center_entry(
        NotificationCenterEntry(id="", recipient_id="recipient-1", user_ref_id=user_id)
    )
    return entry.id


class TestListNotifications:

    def test_list_only_own_notifications(self, app_and_client):
        app, client = app_and_client
        user_id = _login_as(app, client, "user1@example.com")
        _seed_notification(app, user_id)
        _seed_notification(app, "someone-else")

        resp = client.get("/api/v1/notifications/")
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    def test_empty_list_for_new_user(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "user2@example.com")
        resp = client.get("/api/v1/notifications/")
        assert resp.status_code == 200
        assert resp.json() == []


class TestMarkReadAndArchive:

    def test_mark_read_success(self, app_and_client):
        app, client = app_and_client
        user_id = _login_as(app, client, "user3@example.com")
        entry_id = _seed_notification(app, user_id)

        resp = client.post(f"/api/v1/notifications/{entry_id}/read")
        assert resp.status_code == 200
        assert resp.json()["is_read"] is True

    def test_cannot_mark_read_someone_elses_notification(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "user4@example.com")
        other_entry_id = _seed_notification(app, "someone-else-2")

        resp = client.post(f"/api/v1/notifications/{other_entry_id}/read")
        assert resp.status_code == 404
        assert resp.json()["detail"]["error_code"] == "NOTIFICATION_NOT_FOUND"

    def test_archive_success(self, app_and_client):
        app, client = app_and_client
        user_id = _login_as(app, client, "user5@example.com")
        entry_id = _seed_notification(app, user_id)

        resp = client.post(f"/api/v1/notifications/{entry_id}/archive")
        assert resp.status_code == 200
        assert resp.json()["is_archived_by_user"] is True
