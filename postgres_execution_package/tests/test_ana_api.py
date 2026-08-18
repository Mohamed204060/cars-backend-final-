"""
test_ana_api.py — اختبارات وحدة لطبقة REST API لـAnalytics Event Foundation
Batch 3A Slice 1 (+ Corrective Pass: UUID validation، metadata byte-size،
actor_ref_id filter، pagination bounds)
"""

import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth_api import router as auth_router
from auth_repository import InMemoryAuthRepository
from auth_service import IdentityProvider, UserIdentity
from session_repository import InMemorySessionRepository
from ana_api import router as ana_router
from ana_repository import InMemoryAnaRepository
from ana_service import record_analytics_event_via_repository


@pytest.fixture
def app_and_client():
    app = FastAPI()
    app.include_router(auth_router)
    app.include_router(ana_router)

    providers = [IdentityProvider(code="email_password", display_name="كلمة المرور", category="password", is_enabled=True)]
    app.state.auth_repository = InMemoryAuthRepository(providers=providers, identities=[])
    app.state.session_repository = InMemorySessionRepository()
    app.state.ana_repository = InMemoryAnaRepository()

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


class TestRecordEvent:

    def test_records_event_without_session_anonymous(self, app_and_client):
        app, client = app_and_client
        resp = client.post("/api/v1/analytics/events", json={"event_type": "search_performed"})
        assert resp.status_code == 201
        body = resp.json()
        assert body["event_type"] == "search_performed"
        assert body["actor_ref_id"] is None

    def test_records_event_with_session_sets_actor(self, app_and_client):
        app, client = app_and_client
        user_id = _login_as(app, client, "buyer@example.com")
        item_id = str(uuid.uuid4())
        resp = client.post("/api/v1/analytics/events", json={"event_type": "inventory_item_viewed",
                                                               "context_type": "inventory_item",
                                                               "context_ref_id": item_id})
        assert resp.status_code == 201
        body = resp.json()
        assert body["actor_ref_id"] == user_id
        assert body["context_ref_id"] == item_id

    def test_unknown_event_type_returns_400(self, app_and_client):
        app, client = app_and_client
        resp = client.post("/api/v1/analytics/events", json={"event_type": "totally_made_up_event"})
        assert resp.status_code == 400
        assert resp.json()["detail"]["error_code"] == "INVALID_EVENT_TYPE"

    def test_metadata_too_many_keys_returns_400(self, app_and_client):
        app, client = app_and_client
        big_metadata = {f"key_{i}": i for i in range(25)}
        resp = client.post("/api/v1/analytics/events", json={"event_type": "search_performed", "metadata": big_metadata})
        assert resp.status_code == 400
        assert resp.json()["detail"]["error_code"] == "METADATA_TOO_LARGE"

    def test_metadata_single_huge_value_returns_400(self, app_and_client):
        """Corrective Pass: قيمة واحدة ضخمة (حجم فعلي، لا عدد مفاتيح) يجب أن تُرفَض أيضًا."""
        app, client = app_and_client
        huge_metadata = {"note": "x" * 20000}  # مفتاح واحد فقط، لكن حجمه serialized يتجاوز 8KB
        resp = client.post("/api/v1/analytics/events", json={"event_type": "search_performed", "metadata": huge_metadata})
        assert resp.status_code == 400
        assert resp.json()["detail"]["error_code"] == "METADATA_TOO_LARGE"

    def test_metadata_within_bounds_accepted(self, app_and_client):
        app, client = app_and_client
        resp = client.post("/api/v1/analytics/events", json={"event_type": "search_performed",
                                                               "metadata": {"query": "مصباح خلفي", "results_count": 5}})
        assert resp.status_code == 201

    def test_invalid_context_ref_id_returns_400(self, app_and_client):
        """Corrective Pass: context_ref_id عمود UUID فعليًا — قيمة غير صالحة تُرفَض قبل الوصول لـDB (400 لا 500)."""
        app, client = app_and_client
        resp = client.post("/api/v1/analytics/events", json={
            "event_type": "inventory_item_viewed", "context_type": "inventory_item", "context_ref_id": "item-1",
        })
        assert resp.status_code == 400
        assert resp.json()["detail"]["error_code"] == "INVALID_REF_ID"

    def test_all_catalog_v32_event_types_accepted(self, app_and_client):
        app, client = app_and_client
        event_types = [
            "search_performed", "search_zero_results", "search_result_impression",
            "search_result_clicked", "inventory_item_viewed", "purchase_request_created",
            "offer_submitted", "offer_accepted",
        ]
        for et in event_types:
            resp = client.post("/api/v1/analytics/events", json={"event_type": et})
            assert resp.status_code == 201, f"failed for {et}"


class TestListEventsAuthorization:

    def test_requires_authentication(self, app_and_client):
        app, client = app_and_client
        resp = client.get("/api/v1/analytics/events")
        assert resp.status_code == 401

    def test_forbidden_for_non_admin(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "buyer@example.com", role="individual_buyer")
        resp = client.get("/api/v1/analytics/events")
        assert resp.status_code == 403

    def test_allowed_for_admin(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "admin@example.com", role="admin")
        resp = client.get("/api/v1/analytics/events")
        assert resp.status_code == 200


class TestListEventsFilters:

    def _seed(self, repo, pr_id: str):
        record_analytics_event_via_repository(repo, event_type="search_performed", context_type="search")
        record_analytics_event_via_repository(repo, event_type="offer_submitted", context_type="purchase_request", context_ref_id=pr_id)
        record_analytics_event_via_repository(repo, event_type="offer_accepted", context_type="purchase_request", context_ref_id=pr_id)

    def test_lists_all_for_admin(self, app_and_client):
        app, client = app_and_client
        self._seed(app.state.ana_repository, str(uuid.uuid4()))
        _login_as(app, client, "admin@example.com", role="admin")
        resp = client.get("/api/v1/analytics/events")
        assert resp.json()["pagination"]["total_items"] == 3

    def test_filter_by_event_type(self, app_and_client):
        app, client = app_and_client
        self._seed(app.state.ana_repository, str(uuid.uuid4()))
        _login_as(app, client, "admin@example.com", role="admin")
        resp = client.get("/api/v1/analytics/events", params={"event_type": "offer_accepted"})
        body = resp.json()
        assert body["pagination"]["total_items"] == 1
        assert body["items"][0]["event_type"] == "offer_accepted"

    def test_filter_by_context(self, app_and_client):
        app, client = app_and_client
        pr_id = str(uuid.uuid4())
        self._seed(app.state.ana_repository, pr_id)
        _login_as(app, client, "admin@example.com", role="admin")
        resp = client.get("/api/v1/analytics/events", params={"context_type": "purchase_request", "context_ref_id": pr_id})
        assert resp.json()["pagination"]["total_items"] == 2

    def test_invalid_event_type_filter_returns_400(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "admin@example.com", role="admin")
        resp = client.get("/api/v1/analytics/events", params={"event_type": "not_real"})
        assert resp.status_code == 400

    def test_invalid_context_ref_id_filter_returns_400(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "admin@example.com", role="admin")
        resp = client.get("/api/v1/analytics/events", params={"context_ref_id": "not-a-uuid"})
        assert resp.status_code == 400
        assert resp.json()["detail"]["error_code"] == "INVALID_REF_ID"

    def test_filter_by_actor_ref_id(self, app_and_client):
        """Corrective Pass: actor_ref_id أصبح فلترًا فعليًا (لا Query Param متجاهَل).
        ملاحظة بيانات اختبار: InMemoryAuthRepository.create_user() يُعيد مُعرِّفات
        بصيغة 'user-N' (وليس UUID) — قصور في مُولِّد المعرِّفات الوهمي فقط، لا في
        العقد الفعلي (iam.users.id عمود UUID حقيقي في PostgreSQL، ونفس النمط
        مُثبَت في test_postgres_ana_api_integration.py). لذلك هذا الاختبار يُدرِج
        الحدث مباشرة عبر Repository بـUUID صريح صالح (بنفس نمط _seed أعلاه)
        بدل الاعتماد على معرّف الجلسة الوهمي — لا نخفف UUID Validation، ولا
        نغيّر مُولِّد InMemoryAuthRepository (يخص وحدات اختبار أخرى مغلقة)."""
        app, client = app_and_client
        actor_id = str(uuid.uuid4())
        record_analytics_event_via_repository(app.state.ana_repository, event_type="search_performed",
                                                actor_ref_id=actor_id)
        record_analytics_event_via_repository(app.state.ana_repository, event_type="offer_submitted",
                                                actor_ref_id=str(uuid.uuid4()))

        _login_as(app, client, "admin@example.com", role="admin")
        resp = client.get("/api/v1/analytics/events", params={"actor_ref_id": actor_id})
        assert resp.status_code == 200
        body = resp.json()
        assert body["pagination"]["total_items"] == 1
        assert body["items"][0]["actor_ref_id"] == actor_id


class TestPaginationBounds:

    def test_page_zero_rejected(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "admin@example.com", role="admin")
        resp = client.get("/api/v1/analytics/events", params={"page": 0})
        assert resp.status_code == 422

    def test_negative_page_rejected(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "admin@example.com", role="admin")
        resp = client.get("/api/v1/analytics/events", params={"page": -1})
        assert resp.status_code == 422

    def test_page_size_zero_rejected(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "admin@example.com", role="admin")
        resp = client.get("/api/v1/analytics/events", params={"page_size": 0})
        assert resp.status_code == 422

    def test_page_size_over_max_rejected(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "admin@example.com", role="admin")
        resp = client.get("/api/v1/analytics/events", params={"page_size": 1000})
        assert resp.status_code == 422

    def test_page_size_at_max_accepted(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "admin@example.com", role="admin")
        resp = client.get("/api/v1/analytics/events", params={"page_size": 100})
        assert resp.status_code == 200
