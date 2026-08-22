"""
test_sub_api.py — اختبارات وحدة لطبقة REST API لخدمة الاشتراكات (SUB)
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth_api import router as auth_router
from auth_repository import InMemoryAuthRepository
from auth_service import IdentityProvider, UserIdentity
from session_repository import InMemorySessionRepository
from sub_api import router as sub_router
from sub_repository import InMemorySubRepository
from aud_repository import InMemoryAudRepository


@pytest.fixture
def app_and_client():
    app = FastAPI()
    app.include_router(auth_router)
    app.include_router(sub_router)

    providers = [IdentityProvider(code="email_password", display_name="كلمة المرور", category="password", is_enabled=True)]
    app.state.auth_repository = InMemoryAuthRepository(providers=providers, identities=[])
    app.state.session_repository = InMemorySessionRepository()
    app.state.aud_repository = InMemoryAudRepository()
    app.state.sub_repository = InMemorySubRepository()

    client = TestClient(app, base_url="https://testserver")
    return app, client


def _login_as(app, client, email: str, role: str = "individual_seller") -> str:
    repo = app.state.auth_repository
    user_id = repo.create_user()
    repo.set_user_role(user_id, role)
    identity = UserIdentity(id="", user_id=user_id, provider_code="email_password",
                             external_identifier=email, is_verified=True, is_primary=True)
    repo.insert_identity(identity, raw_password="Str0ngPass1!")
    resp = client.post("/api/v1/auth/login", json={"login_identifier": email, "password": "Str0ngPass1!"})
    assert resp.status_code == 200
    return user_id


class TestCreatePlan:

    def test_regular_user_forbidden(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "seller@example.com")
        resp = client.post("/api/v1/subscriptions/plans", json={"plan_type_ref_id": "gold"})
        assert resp.status_code == 403

    def test_admin_can_create_plan(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "admin@example.com", role="admin")
        resp = client.post("/api/v1/subscriptions/plans", json={"plan_type_ref_id": "gold"})
        assert resp.status_code == 201


class TestSubscribeAndChangePlan:

    def _create_plan(self, app, client) -> str:
        _login_as(app, client, "admin-setup@example.com", role="admin")
        plan_id = client.post("/api/v1/subscriptions/plans", json={"plan_type_ref_id": "gold"}).json()["id"]
        client.post("/api/v1/auth/logout")
        return plan_id

    def test_subscribe_success(self, app_and_client):
        app, client = app_and_client
        plan_id = self._create_plan(app, client)
        _login_as(app, client, "seller1@example.com")

        resp = client.post("/api/v1/subscriptions", json={"plan_id": plan_id, "duration_days": 30})
        assert resp.status_code == 201
        assert resp.json()["status"] == "active"

    def test_duplicate_active_subscription_rejected(self, app_and_client):
        app, client = app_and_client
        plan_id = self._create_plan(app, client)
        _login_as(app, client, "seller2@example.com")
        client.post("/api/v1/subscriptions", json={"plan_id": plan_id, "duration_days": 30})

        second = client.post("/api/v1/subscriptions", json={"plan_id": plan_id, "duration_days": 30})
        assert second.status_code == 409
        assert second.json()["detail"]["error_code"] == "ALREADY_SUBSCRIBED"

    def test_subscribing_to_paid_plan_while_on_free_is_allowed(self, app_and_client):
        """CR-014: كل بائع على خطة Free افتراضيًا؛ الاشتراك في خطة مدفوعة
        أول مرة هو ترقية مسموحة دومًا، وليس تعارضًا."""
        app, client = app_and_client
        plan_id = self._create_plan(app, client)
        _login_as(app, client, "seller-free-upgrade@example.com")

        # يملك أصلًا اشتراك Free تلقائيًا (لم يشترك يدويًا بعد)
        mine = client.get("/api/v1/subscriptions/mine")
        assert mine.status_code == 200
        assert mine.json() is not None
        assert mine.json()["status"] == "active"

        resp = client.post("/api/v1/subscriptions", json={"plan_id": plan_id, "duration_days": 30})
        assert resp.status_code == 201
        assert resp.json()["plan_id"] == plan_id

    def test_invalid_duration_rejected(self, app_and_client):
        app, client = app_and_client
        plan_id = self._create_plan(app, client)
        _login_as(app, client, "seller3@example.com")
        resp = client.post("/api/v1/subscriptions", json={"plan_id": plan_id, "duration_days": 0})
        assert resp.status_code == 400

    def test_get_my_subscription(self, app_and_client):
        app, client = app_and_client
        plan_id = self._create_plan(app, client)
        _login_as(app, client, "seller4@example.com")
        client.post("/api/v1/subscriptions", json={"plan_id": plan_id, "duration_days": 30})

        resp = client.get("/api/v1/subscriptions/mine")
        assert resp.status_code == 200
        assert resp.json()["plan_id"] == plan_id

    def test_get_my_subscription_auto_provisions_free_when_never_subscribed(self, app_and_client):
        """CR-014: لا وجود لحالة 'بلا اشتراك' من منظور الواجهة؛ أول استعلام
        لبائع جديد يُنشئ له اشتراك Free تلقائيًا بدل إعادة null."""
        app, client = app_and_client
        _login_as(app, client, "seller5@example.com")
        resp = client.get("/api/v1/subscriptions/mine")
        assert resp.status_code == 200
        body = resp.json()
        assert body is not None
        assert body["status"] == "active"
        assert body["expires_at"] is None

    def test_expired_paid_plan_reverts_to_free_not_blocked(self, app_and_client):
        """CR-014: انتهاء خطة مدفوعة يعيد البائع تلقائيًا لخطة Free (نشط)،
        لا إلى حالة مسدودة."""
        app, client = app_and_client
        plan_id = self._create_plan(app, client)
        _login_as(app, client, "seller-expiry@example.com")
        sub_resp = client.post("/api/v1/subscriptions", json={"plan_id": plan_id, "duration_days": 1})
        assert sub_resp.status_code == 201

        import sub_api as sub_api_module
        from datetime import timedelta as _timedelta
        real_datetime = sub_api_module.datetime

        class _FutureDatetime(real_datetime):
            @classmethod
            def now(cls, tz=None):
                return real_datetime.now(tz) + _timedelta(days=2)

        sub_api_module.datetime = _FutureDatetime
        try:
            mine = client.get("/api/v1/subscriptions/mine")
        finally:
            sub_api_module.datetime = real_datetime

        assert mine.status_code == 200
        body = mine.json()
        assert body["status"] == "active"
        assert body["plan_id"] != plan_id
        assert body["expires_at"] is None

    def test_non_owner_cannot_change_plan(self, app_and_client):
        app, client = app_and_client
        plan_id = self._create_plan(app, client)
        _login_as(app, client, "seller6@example.com")
        sub_id = client.post("/api/v1/subscriptions", json={"plan_id": plan_id, "duration_days": 30}).json()["id"]

        client.post("/api/v1/auth/logout")
        _login_as(app, client, "stranger@example.com")
        resp = client.post(f"/api/v1/subscriptions/{sub_id}/change-plan", json={"new_plan_id": "other-plan"})
        assert resp.status_code == 403

    def test_owner_can_change_plan(self, app_and_client):
        app, client = app_and_client
        plan_id = self._create_plan(app, client)
        _login_as(app, client, "seller7@example.com")
        sub_id = client.post("/api/v1/subscriptions", json={"plan_id": plan_id, "duration_days": 30}).json()["id"]

        resp = client.post(f"/api/v1/subscriptions/{sub_id}/change-plan", json={"new_plan_id": "other-plan"})
        assert resp.status_code == 200
        assert resp.json()["plan_id"] == "other-plan"
