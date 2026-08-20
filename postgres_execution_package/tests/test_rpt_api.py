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


class TestSearchAnalyticsAuthorization:

    def test_requires_authentication(self, app_and_client):
        app, client = app_and_client
        resp = client.get("/api/v1/reports/search-analytics")
        assert resp.status_code == 401

    def test_forbidden_for_non_admin(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "buyer@example.com", role="individual_buyer")
        resp = client.get("/api/v1/reports/search-analytics")
        assert resp.status_code == 403

    def test_allowed_for_admin(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "admin@example.com", role="admin")
        resp = client.get("/api/v1/reports/search-analytics")
        assert resp.status_code == 200

    def test_no_write_endpoint_exists(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "admin@example.com", role="admin")
        assert client.post("/api/v1/reports/search-analytics", json={}).status_code == 405


class TestSearchAnalyticsFormulas:

    def test_empty_dataset_returns_zeros_not_errors(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "admin@example.com", role="admin")
        resp = client.get("/api/v1/reports/search-analytics")
        assert resp.status_code == 200
        body = resp.json()
        assert body["search_volume"] == 0
        assert body["zero_result_count"] == 0
        assert body["zero_result_rate"] == 0.0
        assert body["top_zero_result_vehicles"] == []
        assert body["top_missing_search_terms"] == []

    def test_volume_and_zero_result_rate_computed_correctly(self, app_and_client):
        app, client = app_and_client
        now = datetime.utcnow()
        repo = app.state.rpt_repository
        repo.ana_events = [
            {"event_type": "search_performed", "occurred_at_utc": now, "metadata": {}},
            {"event_type": "search_performed", "occurred_at_utc": now, "metadata": {}},
            {"event_type": "search_performed", "occurred_at_utc": now, "metadata": {}},
            {"event_type": "search_performed", "occurred_at_utc": now, "metadata": {}},
            {"event_type": "search_zero_results", "occurred_at_utc": now, "metadata": {"trim_ref_id": "trim-A"}},
        ]
        _login_as(app, client, "admin@example.com", role="admin")
        resp = client.get("/api/v1/reports/search-analytics")
        body = resp.json()
        assert body["search_volume"] == 4
        assert body["zero_result_count"] == 1
        assert abs(body["zero_result_rate"] - 0.25) < 1e-9
        assert body["top_zero_result_vehicles"] == [{"trim_ref_id": "trim-A", "count": 1}]

    def test_missing_search_terms_identified_not_just_counted(self, app_and_client):
        """Pre-Gate Corrective #3: يثبت أن التقرير يستطيع الإجابة "ما القطعة التي
        يبحث عنها المستخدمون ولا يجدونها؟" — لا عدّ إجمالي فقط."""
        app, client = app_and_client
        now = datetime.utcnow()
        repo = app.state.rpt_repository
        repo.ana_events = [
            {"event_type": "search_zero_results", "occurred_at_utc": now, "metadata": {"normalized_query_term": "فلتر زيت نادر"}},
            {"event_type": "search_zero_results", "occurred_at_utc": now, "metadata": {"normalized_query_term": "فلتر زيت نادر"}},
            {"event_type": "search_zero_results", "occurred_at_utc": now, "metadata": {"trim_ref_id": "trim-B"}},  # بحث مركبة بلا نص
        ]
        _login_as(app, client, "admin@example.com", role="admin")
        resp = client.get("/api/v1/reports/search-analytics")
        body = resp.json()
        assert body["top_missing_search_terms"] == [{"normalized_query_term": "فلتر زيت نادر", "count": 2}]

    def test_invalid_date_range_returns_400(self, app_and_client):
        app, client = app_and_client
        now = datetime.utcnow()
        _login_as(app, client, "admin@example.com", role="admin")
        resp = client.get("/api/v1/reports/search-analytics", params={
            "date_from": now.isoformat(), "date_to": (now - timedelta(days=1)).isoformat(),
        })
        assert resp.status_code == 400
        assert resp.json()["detail"]["error_code"] == "INVALID_DATE_RANGE"


class TestMissingPartsAuthorization:

    def test_requires_authentication(self, app_and_client):
        app, client = app_and_client
        resp = client.get("/api/v1/reports/missing-parts")
        assert resp.status_code == 401

    def test_forbidden_for_non_admin(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "seller@example.com", role="individual_seller")
        resp = client.get("/api/v1/reports/missing-parts")
        assert resp.status_code == 403

    def test_allowed_for_admin(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "admin@example.com", role="admin")
        resp = client.get("/api/v1/reports/missing-parts")
        assert resp.status_code == 200


class TestMissingPartsFormulas:

    def test_empty_dataset_returns_zeros(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "admin@example.com", role="admin")
        resp = client.get("/api/v1/reports/missing-parts")
        body = resp.json()
        assert body["zero_result_search_count"] == 0
        assert body["purchase_requests_without_offers_count"] == 0
        assert body["top_unmet_demand_parts"] == []
        assert body["top_missing_search_terms"] == []

    def test_combines_zero_result_searches_and_unmet_requests(self, app_and_client):
        """Root directive: لا يعتمد التقرير على search_zero_results فقط —
        يدمج purchase_requests بلا عروض أيضًا (مصدر ثانٍ مستقل)."""
        app, client = app_and_client
        now = datetime.utcnow()
        repo = app.state.rpt_repository
        repo.ana_events = [
            {"event_type": "search_zero_results", "occurred_at_utc": now, "metadata": {"trim_ref_id": "trim-A"}},
            {"event_type": "search_zero_results", "occurred_at_utc": now, "metadata": {"trim_ref_id": "trim-A"}},
        ]
        repo.purchase_requests = [
            {"id": "pr1", "status": "open", "catalog_part_ref_id": "part-X"},
            {"id": "pr2", "status": "open", "catalog_part_ref_id": "part-X"},
            {"id": "pr3", "status": "fulfilled", "catalog_part_ref_id": "part-Y"},
        ]
        repo.offers = [{"purchase_request_id": "pr3", "status": "accepted"}]

        _login_as(app, client, "admin@example.com", role="admin")
        resp = client.get("/api/v1/reports/missing-parts")
        body = resp.json()
        assert body["zero_result_search_count"] == 2
        assert body["purchase_requests_without_offers_count"] == 2
        assert body["top_unmet_demand_parts"] == [{"catalog_part_ref_id": "part-X", "requests_without_offers": 2}]
        # part-Y له عرض مقبول — لا يجب أن يظهر ضمن الطلب غير المُلبَّى
        assert all(p["catalog_part_ref_id"] != "part-Y" for p in body["top_unmet_demand_parts"])


class TestMarketplaceIntelligence:

    def test_requires_authentication(self, app_and_client):
        app, client = app_and_client
        assert client.get("/api/v1/reports/marketplace-intelligence").status_code == 401

    def test_forbidden_for_non_admin(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "buyer@example.com", role="individual_buyer")
        assert client.get("/api/v1/reports/marketplace-intelligence").status_code == 403

    def test_composes_existing_metrics_no_double_calculation(self, app_and_client):
        """يتحقق أن القيم المرجَّعة مطابقة تمامًا لنفس القيم من Search Analytics/
        Missing Parts/Executive Dashboard — لا صيغة موازية مستقلة."""
        app, client = app_and_client
        now = datetime.utcnow()
        repo = app.state.rpt_repository
        repo.catalog_parts = [{"status": "approved", "id": "part-A"}, {"status": "approved", "id": "part-B"}]
        repo.inventory_items = [{"status": "active", "catalog_part_ref_id": "part-A"}]
        repo.users = [{"status": "active", "primary_role": "individual_seller", "created_at": now}]
        repo.stores = [{"status": "active"}]

        _login_as(app, client, "admin@example.com", role="admin")
        mi = client.get("/api/v1/reports/marketplace-intelligence").json()
        dashboard = client.get("/api/v1/reports/executive-dashboard").json()

        assert mi["catalog_parts_with_no_active_supply"] == 1
        assert mi["sellers_to_active_stores_ratio"] == 1.0
        assert mi["request_to_offer_rate"] == dashboard["request_to_offer_rate"]

    def test_out_of_stock_only_counts_as_no_active_supply(self, app_and_client):
        """Pre-Gate Corrective #2: قطعة بمخزون out_of_stock فقط (بلا active) يجب
        أن تُحتسَب ضمن 'بلا Supply نشط' — لا شيء منها قابل للشراء الآن فعليًا،
        رغم وجود سطر مخزون. يختلف هذا عمدًا عن تعريف search_repository.py
        (الذي يُظهِر out_of_stock في نتائج البحث للتصفح فقط)."""
        app, client = app_and_client
        repo = app.state.rpt_repository
        repo.catalog_parts = [{"status": "approved", "id": "part-A"}]
        repo.inventory_items = [{"status": "out_of_stock", "catalog_part_ref_id": "part-A"}]

        _login_as(app, client, "admin@example.com", role="admin")
        mi = client.get("/api/v1/reports/marketplace-intelligence").json()
        assert mi["catalog_parts_with_no_active_supply"] == 1

    def test_suspended_and_archived_stores_excluded_from_ratio_denominator(self, app_and_client):
        """Pre-Gate Corrective #1: المقام active stores حصرًا — متجر مُعلَّق/مؤرشَف
        لا يمثّل تغطية عرض فعلية رغم وجود صفّه في الجدول."""
        app, client = app_and_client
        repo = app.state.rpt_repository
        repo.stores = [{"status": "active"}, {"status": "suspended"}, {"status": "archived"}, {"status": "creating"}]
        repo.users = [{"status": "active", "primary_role": "individual_seller", "created_at": datetime.utcnow()}]

        _login_as(app, client, "admin@example.com", role="admin")
        mi = client.get("/api/v1/reports/marketplace-intelligence").json()
        # متجر نشط واحد فقط من أصل 4 — النسبة يجب أن تُحسَب على أساس 1، لا 4
        assert mi["sellers_to_active_stores_ratio"] == 1.0

    def test_empty_dataset_no_errors(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "admin@example.com", role="admin")
        resp = client.get("/api/v1/reports/marketplace-intelligence")
        assert resp.status_code == 200
        assert resp.json()["sellers_to_active_stores_ratio"] == 0.0


class TestTrendingParts:

    def test_requires_authentication(self, app_and_client):
        app, client = app_and_client
        assert client.get("/api/v1/reports/trending-parts").status_code == 401

    def test_forbidden_for_non_admin(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "buyer@example.com", role="individual_buyer")
        assert client.get("/api/v1/reports/trending-parts").status_code == 403

    def test_growth_calculated_correctly(self, app_and_client):
        app, client = app_and_client
        now = datetime.utcnow()
        repo = app.state.rpt_repository
        repo.purchase_requests = [
            {"id": "pr1", "status": "open", "catalog_part_ref_id": "part-X", "created_at": now - timedelta(days=1)},
            {"id": "pr2", "status": "open", "catalog_part_ref_id": "part-X", "created_at": now - timedelta(days=2)},
            {"id": "pr3", "status": "open", "catalog_part_ref_id": "part-X", "created_at": now - timedelta(days=40)},
        ]
        _login_as(app, client, "admin@example.com", role="admin")
        resp = client.get("/api/v1/reports/trending-parts", params={"window_days": 30})
        assert resp.status_code == 200
        rows = resp.json()["top_growing_parts"]
        assert rows[0] == {"catalog_part_ref_id": "part-X", "current_count": 2, "previous_count": 1, "growth": 1}

    def test_invalid_window_rejected(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "admin@example.com", role="admin")
        resp = client.get("/api/v1/reports/trending-parts", params={"window_days": 400})
        assert resp.status_code == 400
        assert resp.json()["detail"]["error_code"] == "INVALID_WINDOW"

    def test_zero_window_rejected_not_silently_defaulted(self, app_and_client):
        """Pre-Gate Corrective #5: 0 يجب ألا يتحول بصمت لـ30 الافتراضية."""
        app, client = app_and_client
        _login_as(app, client, "admin@example.com", role="admin")
        resp = client.get("/api/v1/reports/trending-parts", params={"window_days": 0})
        assert resp.status_code == 400
        assert resp.json()["detail"]["error_code"] == "INVALID_WINDOW"

    def test_negative_window_rejected_not_silently_defaulted(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "admin@example.com", role="admin")
        resp = client.get("/api/v1/reports/trending-parts", params={"window_days": -5})
        assert resp.status_code == 400
        assert resp.json()["detail"]["error_code"] == "INVALID_WINDOW"

    def test_non_numeric_window_rejected_with_422(self, app_and_client):
        """Pre-Gate Corrective #5: قيمة غير رقمية تُرفَض تلقائيًا (FastAPI/Pydantic
        Type Coercion) — لا تتحول بصمت لأي Default."""
        app, client = app_and_client
        _login_as(app, client, "admin@example.com", role="admin")
        resp = client.get("/api/v1/reports/trending-parts", params={"window_days": "abc"})
        assert resp.status_code == 422

    def test_default_window_used_only_when_param_absent(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "admin@example.com", role="admin")
        resp = client.get("/api/v1/reports/trending-parts")
        assert resp.status_code == 200
        assert resp.json()["window_days"] == 30

    def test_empty_dataset_returns_empty_list(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "admin@example.com", role="admin")
        resp = client.get("/api/v1/reports/trending-parts")
        assert resp.status_code == 200
        assert resp.json()["top_growing_parts"] == []
