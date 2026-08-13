"""
test_scheduler_api.py — اختبارات وحدة لطبقة REST API الإدارية للمُجدوِل
"""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth_api import router as auth_router
from auth_repository import InMemoryAuthRepository
from auth_service import IdentityProvider, UserIdentity
from session_repository import InMemorySessionRepository
from scheduler_api import router as scheduler_router
from scheduler_repository import InMemorySchedulerRepository


@pytest.fixture
def app_and_client():
    app = FastAPI()
    app.include_router(auth_router)
    app.include_router(scheduler_router)

    providers = [IdentityProvider(code="email_password", display_name="كلمة المرور", category="password", is_enabled=True)]
    app.state.auth_repository = InMemoryAuthRepository(providers=providers, identities=[])
    app.state.session_repository = InMemorySessionRepository()
    app.state.scheduler_repository = InMemorySchedulerRepository()

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


def _future_iso() -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()


class TestAdminOnlyAccess:

    def test_regular_user_forbidden(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "buyer@example.com", role="individual_buyer")
        resp = client.post("/api/v1/admin/scheduled-jobs", json={
            "job_type": "pur_expiration_check", "target_ref_id": "pr-1", "scheduled_at": _future_iso(),
        })
        assert resp.status_code == 403

    def test_admin_can_create_job(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "admin@example.com", role="admin")
        resp = client.post("/api/v1/admin/scheduled-jobs", json={
            "job_type": "pur_expiration_check", "target_ref_id": "pr-1", "scheduled_at": _future_iso(),
        })
        assert resp.status_code == 201
        assert resp.json()["status"] == "pending"


class TestGetAndCancelJob:

    def test_get_nonexistent_job_404(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "admin2@example.com", role="admin")
        resp = client.get("/api/v1/admin/scheduled-jobs/ghost")
        assert resp.status_code == 404

    def test_cancel_job_success(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "admin3@example.com", role="admin")
        job_id = client.post("/api/v1/admin/scheduled-jobs", json={
            "job_type": "ntf_campaign_dispatch", "target_ref_id": "campaign-1", "scheduled_at": _future_iso(),
        }).json()["id"]

        resp = client.post(f"/api/v1/admin/scheduled-jobs/{job_id}/cancel")
        assert resp.status_code == 200
        assert resp.json()["status"] == "cancelled"

    def test_invalid_recurrence_rule_rejected(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "admin4@example.com", role="admin")
        resp = client.post("/api/v1/admin/scheduled-jobs", json={
            "job_type": "x", "target_ref_id": "y", "scheduled_at": _future_iso(), "recurrence_rule": "hourly",
        })
        assert resp.status_code == 400
