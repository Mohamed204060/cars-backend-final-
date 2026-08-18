"""
test_rpt_api.py — اختبارات وحدة لطبقة REST API لتقارير الإدارة
Batch 3A Slice 2 — Executive Dashboard
"""

from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth_api import router as auth_router
from auth_repository import InMemoryAuthRepository
from auth_service import IdentityProvider, UserIdentity
from session_repository import InMemorySessionRepository
from rpt_api import router as rpt_router
from rpt_repository import InMemoryRptRepository


@pytest.fixture
def app_and_client():
    app = FastAPI()
    app.include_router(auth_router)
    app.include_router(rpt_router)

    providers = [IdentityProvider(code="email_password", display_name="كلمة المرور", category="password", is_enabled=True)]
    app.state.auth_repository = InMemoryAuthRepository(providers=providers, identities=[])
    app.state.session_repository = InMemorySessionRepository()
    app.state.rpt_repository = InMemoryRptRepository()

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


class TestExecutiveDashboardAuthorization:

    def test_requires_authentication(self, app_and_client):
        app, client = app_and_client
        resp = client.get("/api/v1/reports/executive-dashboard")
        assert resp.status_code == 401

    def test_forbidden_for_non_admin(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "buyer@example.com", role="individual_buyer")
        resp = client.get("/api/v1/reports/executive-dashboard")
        assert resp.status_code == 403

    def test_forbidden_for_seller(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "seller@example.com", role="individual_seller")
        resp = client.get("/api/v1/reports/executive-dashboard")
        assert resp.status_code == 403

    def test_forbidden_for_moderator(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "mod@example.com", role="moderator")
        resp = client.get("/api/v1/reports/executive-dashboard")
        assert resp.status_code == 403

    def test_allowed_for_admin(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "admin@example.com", role="admin")
        resp = client.get("/api/v1/reports/executive-dashboard")
        assert resp.status_code == 200

    def test_allowed_for_super_admin(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "root@example.com", role="super_admin")
        resp = client.get("/api/v1/reports/executive-dashboard")
        assert resp.status_code == 200

    def test_no_write_endpoint_exists(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "admin2@example.com", role="admin")
        assert client.post("/api/v1/reports/executive-dashboard", json={}).status_code == 405
        assert client.put("/api/v1/reports/executive-dashboard", json={}).status_code == 405
        assert client.delete("/api/v1/reports/executive-dashboard").status_code == 405


class TestExecutiveDashboardEmptyDataset:

    def test_empty_dataset_returns_zeros_not_errors(self, app_and_client):
        """No-data scenario + Division-by-zero guard."""
        app, client = app_and_client
        _login_as(app, client, "admin@example.com", role="admin")
        resp = client.get("/api/v1/reports/executive-dashboard")
        assert resp.status_code == 200
        body = resp.json()
        assert body["users_total"] == 0
        assert body["purchase_requests_total"] == 0
        assert body["request_to_offer_rate"] == 0.0
        assert body["request_to_accepted_offer_rate"] == 0.0
        assert body["avg_offers_per_request"] == 0.0
        assert body["users_by_status"] == {}
        assert body["subscriptions_by_plan"] == {}


class TestExecutiveDashboardFormulas:

    def _seed(self, repo, now):
        repo.users = [
            {"status": "active", "primary_role": "individual_buyer", "created_at": now - timedelta(days=1)},
            {"status": "active", "primary_role": "individual_seller", "created_at": now - timedelta(days=40)},
            {"status": "suspended", "primary_role": "business_seller", "created_at": now - timedelta(days=2)},
        ]
        repo.stores = [{"status": "active"}, {"status": "creating"}]
        repo.inventory_items = [{"status": "active"}, {"status": "hidden"}, {"status": "active"}]
        repo.catalog_parts = [{"status": "approved"}, {"status": "proposed"}]
        repo.purchase_requests = [
            {"id": "pr1", "status": "fulfilled"}, {"id": "pr2", "status": "open"}, {"id": "pr3", "status": "cancelled"},
        ]
        repo.offers = [
            {"purchase_request_id": "pr1", "status": "accepted"}, {"purchase_request_id": "pr1", "status": "rejected"},
        ]
        repo.subscriptions = [
            {"status": "active", "plan_code": "free"}, {"status": "active", "plan_code": "gold"},
            {"status": "expired", "plan_code": "silver"},
        ]

    def test_full_dashboard_formulas(self, app_and_client):
        app, client = app_and_client
        now = datetime.utcnow()
        self._seed(app.state.rpt_repository, now)
        _login_as(app, client, "admin@example.com", role="admin")

        resp = client.get("/api/v1/reports/executive-dashboard")
        assert resp.status_code == 200
        body = resp.json()

        assert body["users_total"] == 3
        assert body["sellers_total"] == 2
        assert body["stores_total"] == 2
        assert body["purchase_requests_total"] == 3
        assert body["purchase_requests_without_offers"] == 2
        assert abs(body["request_to_offer_rate"] - (1 / 3)) < 1e-9
        assert abs(body["request_to_accepted_offer_rate"] - (1 / 3)) < 1e-9
        assert abs(body["avg_offers_per_request"] - (2 / 3)) < 1e-9
        # الاشتراك المنتهي (silver/expired) يجب ألا يُحتسَب
        assert body["subscriptions_by_plan"] == {"free": 1, "gold": 1}
        assert body["subscriptions_active_total"] == 2

    def test_users_new_requires_date_range(self, app_and_client):
        app, client = app_and_client
        now = datetime.utcnow()
        self._seed(app.state.rpt_repository, now)
        _login_as(app, client, "admin@example.com", role="admin")

        no_range = client.get("/api/v1/reports/executive-dashboard")
        assert no_range.json()["users_new"] == 0

        with_range = client.get("/api/v1/reports/executive-dashboard", params={
            "date_from": (now - timedelta(days=7)).isoformat(), "date_to": now.isoformat(),
        })
        assert with_range.json()["users_new"] == 2

    def test_invalid_date_range_returns_400(self, app_and_client):
        app, client = app_and_client
        now = datetime.utcnow()
        _login_as(app, client, "admin@example.com", role="admin")
        resp = client.get("/api/v1/reports/executive-dashboard", params={
            "date_from": now.isoformat(), "date_to": (now - timedelta(days=1)).isoformat(),
        })
        assert resp.status_code == 400
        assert resp.json()["detail"]["error_code"] == "INVALID_DATE_RANGE"

    def test_no_financial_fields_present(self, app_and_client):
        """قرار حاكم صريح: لا Revenue/Profit/GMV/Commission/Refunds في الاستجابة."""
        app, client = app_and_client
        _login_as(app, client, "admin@example.com", role="admin")
        resp = client.get("/api/v1/reports/executive-dashboard")
        body = resp.json()
        forbidden_keys = {"revenue", "profit", "gmv", "commission", "refunds", "financial_sales"}
        assert forbidden_keys.isdisjoint(set(k.lower() for k in body.keys()))
