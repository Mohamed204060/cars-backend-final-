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
from aud_repository import InMemoryAudRepository


@pytest.fixture
def app_and_client():
    app = FastAPI()
    app.include_router(auth_router)
    app.include_router(rpt_router)

    providers = [IdentityProvider(code="email_password", display_name="كلمة المرور", category="password", is_enabled=True)]
    app.state.auth_repository = InMemoryAuthRepository(providers=providers, identities=[])
    app.state.session_repository = InMemorySessionRepository()
    app.state.aud_repository = InMemoryAudRepository()
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


class TestUserAnalytics:

    def test_requires_authentication(self, app_and_client):
        app, client = app_and_client
        assert client.get("/api/v1/reports/user-analytics").status_code == 401

    def test_forbidden_for_non_admin(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "buyer@example.com", role="individual_buyer")
        assert client.get("/api/v1/reports/user-analytics").status_code == 403

    def test_empty_dataset_no_errors(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "admin@example.com", role="admin")
        resp = client.get("/api/v1/reports/user-analytics")
        assert resp.status_code == 200
        body = resp.json()
        assert body["users_by_role"] == {}
        assert body["verified_sellers_count"] == 0
        assert body["registrations_by_day"] == []

    def test_role_account_type_and_verified_seller_breakdown(self, app_and_client):
        app, client = app_and_client
        now = datetime.utcnow()
        repo = app.state.rpt_repository
        repo.users = [
            {"primary_role": "individual_buyer", "account_type": "individual", "is_verified_seller": False, "created_at": now},
            {"primary_role": "individual_seller", "account_type": "individual", "is_verified_seller": True, "created_at": now},
            {"primary_role": "business_seller", "account_type": "business", "is_verified_seller": True, "created_at": now},
        ]
        _login_as(app, client, "admin@example.com", role="admin")
        resp = client.get("/api/v1/reports/user-analytics")
        body = resp.json()
        assert body["users_by_role"] == {"individual_buyer": 1, "individual_seller": 1, "business_seller": 1}
        assert body["users_by_account_type"] == {"individual": 2, "business": 1}
        assert body["verified_sellers_count"] == 2

    def test_registrations_by_day_requires_date_range(self, app_and_client):
        """Date Semantics: بلا مدى زمني، registrations_by_day فارغة — نفس مبدأ
        users_new في Executive Dashboard (لا معنى لـ'اتجاه يومي' بلا نافذة)."""
        app, client = app_and_client
        now = datetime.utcnow()
        repo = app.state.rpt_repository
        repo.users = [
            {"primary_role": "individual_buyer", "account_type": "individual", "is_verified_seller": False, "created_at": now},
            {"primary_role": "individual_buyer", "account_type": "individual", "is_verified_seller": False, "created_at": now},
        ]
        _login_as(app, client, "admin@example.com", role="admin")

        no_range = client.get("/api/v1/reports/user-analytics")
        assert no_range.json()["registrations_by_day"] == []

        with_range = client.get("/api/v1/reports/user-analytics", params={
            "date_from": (now - timedelta(days=7)).isoformat(), "date_to": now.isoformat(),
        })
        days = with_range.json()["registrations_by_day"]
        assert len(days) == 1
        assert days[0]["count"] == 2

    def test_invalid_date_range_returns_400(self, app_and_client):
        app, client = app_and_client
        now = datetime.utcnow()
        _login_as(app, client, "admin@example.com", role="admin")
        resp = client.get("/api/v1/reports/user-analytics", params={
            "date_from": now.isoformat(), "date_to": (now - timedelta(days=1)).isoformat(),
        })
        assert resp.status_code == 400
        assert resp.json()["detail"]["error_code"] == "INVALID_DATE_RANGE"

    def test_date_only_range_is_inclusive_of_full_end_day(self, app_and_client):
        """Root-Cause Fix (مراجعة ما قبل الرفع): date_to بصيغة تاريخ بلا وقت
        (مثل ما يُرسِله <input type="date">، الحالة الفعلية 'YYYY-MM-DD') يجب أن
        يشمل اليوم بأكمله — لا يُستبعَد سجل في منتصف/نهاية ذلك اليوم. سجل اليوم
        التالي مباشرة يجب ألا يُحتسَب."""
        app, client = app_and_client
        repo = app.state.rpt_repository
        repo.users = [
            {"primary_role": "individual_buyer", "account_type": "individual", "is_verified_seller": False,
             "created_at": datetime(2026, 8, 1, 0, 0, 0)},  # بداية النطاق تمامًا
            {"primary_role": "individual_buyer", "account_type": "individual", "is_verified_seller": False,
             "created_at": datetime(2026, 8, 20, 15, 30, 0)},  # منتصف يوم النهاية — كان يُستبعَد قبل الإصلاح
            {"primary_role": "individual_buyer", "account_type": "individual", "is_verified_seller": False,
             "created_at": datetime(2026, 8, 20, 23, 59, 0)},  # نهاية يوم النهاية تمامًا
            {"primary_role": "individual_buyer", "account_type": "individual", "is_verified_seller": False,
             "created_at": datetime(2026, 8, 21, 0, 0, 1)},  # اليوم التالي — يجب ألا يُحتسَب
        ]
        _login_as(app, client, "admin@example.com", role="admin")

        # نفس الصيغة الفعلية التي يُرسِلها <input type="date"> — تاريخ بلا وقت إطلاقًا
        resp = client.get("/api/v1/reports/user-analytics", params={"date_from": "2026-08-01", "date_to": "2026-08-20"})
        assert resp.status_code == 200, resp.text
        total = sum(d["count"] for d in resp.json()["registrations_by_day"])
        assert total == 3, f"expected 3 (excludes only the next-day record), got {total}"

        day20 = next((d for d in resp.json()["registrations_by_day"] if d["date"] == "2026-08-20"), None)
        assert day20 is not None and day20["count"] == 2, "both end-of-range-day records must be counted"

    def test_no_country_city_language_fields_present(self, app_and_client):
        """قرار حاكم صريح: لا اختراع بيانات جغرافية/لغوية غير متوفرة فعليًا
        في iam.users — لا حقل من هذا النوع في الاستجابة إطلاقًا."""
        app, client = app_and_client
        _login_as(app, client, "admin@example.com", role="admin")
        body = client.get("/api/v1/reports/user-analytics").json()
        forbidden_keys = {"country", "city", "language", "country_ref_id", "city_ref_id"}
        assert forbidden_keys.isdisjoint(set(k.lower() for k in body.keys()))


class TestSellerStoreAnalytics:

    def test_requires_authentication(self, app_and_client):
        app, client = app_and_client
        assert client.get("/api/v1/reports/seller-store-analytics").status_code == 401

    def test_forbidden_for_non_admin(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "seller@example.com", role="individual_seller")
        assert client.get("/api/v1/reports/seller-store-analytics").status_code == 403

    def test_empty_dataset_no_errors(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "admin@example.com", role="admin")
        resp = client.get("/api/v1/reports/seller-store-analytics")
        assert resp.status_code == 200
        body = resp.json()
        assert body["stores_by_status"] == {}
        assert body["sellers_without_store_count"] == 0
        assert body["top_stores_by_offer_count"] == []

    def test_seller_without_store_and_stale_active_store_detected(self, app_and_client):
        app, client = app_and_client
        repo = app.state.rpt_repository
        repo.users = [{"id": "u1", "primary_role": "individual_seller"}, {"id": "u2", "primary_role": "individual_buyer"}]
        repo.stores = [{"id": "s1", "status": "active"}]  # لا owner_user_ref_id مطابق لـu1
        _login_as(app, client, "admin@example.com", role="admin")
        body = client.get("/api/v1/reports/seller-store-analytics").json()
        assert body["sellers_without_store_count"] == 1
        assert body["active_stores_without_inventory_count"] == 1

    def test_top_stores_ranked_by_offer_count(self, app_and_client):
        app, client = app_and_client
        repo = app.state.rpt_repository
        repo.offers = [
            {"purchase_request_id": "pr1", "status": "submitted", "seller_store_ref_id": "s1"},
            {"purchase_request_id": "pr2", "status": "submitted", "seller_store_ref_id": "s1"},
            {"purchase_request_id": "pr3", "status": "submitted", "seller_store_ref_id": "s2"},
        ]
        _login_as(app, client, "admin@example.com", role="admin")
        body = client.get("/api/v1/reports/seller-store-analytics").json()
        assert body["top_stores_by_offer_count"][0] == {"store_id": "s1", "offer_count": 2}


class TestInventoryCatalogAnalytics:

    def test_requires_authentication(self, app_and_client):
        app, client = app_and_client
        assert client.get("/api/v1/reports/inventory-catalog-analytics").status_code == 401

    def test_forbidden_for_non_admin(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "seller@example.com", role="individual_seller")
        assert client.get("/api/v1/reports/inventory-catalog-analytics").status_code == 403

    def test_no_image_related_field_present(self, app_and_client):
        """قرار حاكم صريح: primary_photo_id عمود يتيم — لا مؤشر 'بلا صور' مُخترَع."""
        app, client = app_and_client
        _login_as(app, client, "admin@example.com", role="admin")
        body = client.get("/api/v1/reports/inventory-catalog-analytics").json()
        assert not any("image" in k.lower() or "photo" in k.lower() for k in body.keys())

    def test_stale_active_items_detected_by_updated_at(self, app_and_client):
        app, client = app_and_client
        now = datetime.utcnow()
        repo = app.state.rpt_repository
        repo.inventory_items = [
            {"status": "active", "pricing_mode": "fixed_price", "updated_at": now - timedelta(days=40)},
            {"status": "active", "pricing_mode": "fixed_price", "updated_at": now - timedelta(days=1)},
        ]
        _login_as(app, client, "admin@example.com", role="admin")
        body = client.get("/api/v1/reports/inventory-catalog-analytics").json()
        assert body["stale_active_inventory_items_count"] == 1
        assert body["inventory_items_by_pricing_mode"] == {"fixed_price": 2}

    def test_empty_dataset_no_errors(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "admin@example.com", role="admin")
        resp = client.get("/api/v1/reports/inventory-catalog-analytics")
        assert resp.status_code == 200
        assert resp.json()["models_total"] == 0


class TestPurchaseRequestOfferAnalytics:

    def test_requires_authentication(self, app_and_client):
        app, client = app_and_client
        assert client.get("/api/v1/reports/purchase-request-offer-analytics").status_code == 401

    def test_forbidden_for_non_admin(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "seller@example.com", role="individual_seller")
        assert client.get("/api/v1/reports/purchase-request-offer-analytics").status_code == 403

    def test_empty_dataset_returns_null_averages_not_zero(self, app_and_client):
        """Null handling: None (لا صفر مضلِّل) بلا بيانات كافية."""
        app, client = app_and_client
        _login_as(app, client, "admin@example.com", role="admin")
        body = client.get("/api/v1/reports/purchase-request-offer-analytics").json()
        assert body["avg_hours_to_first_offer"] is None
        assert body["avg_hours_to_accepted_offer"] is None
        assert body["withdrawn_offers_count"] == 0

    def test_avg_hours_to_first_and_accepted_offer_computed_correctly(self, app_and_client):
        app, client = app_and_client
        now = datetime.utcnow()
        repo = app.state.rpt_repository
        repo.purchase_requests = [{"id": "pr1", "status": "fulfilled", "created_at": now - timedelta(hours=10)}]
        repo.offers = [
            {"purchase_request_id": "pr1", "status": "accepted", "seller_store_ref_id": "s1",
             "created_at": now - timedelta(hours=8), "updated_at": now - timedelta(hours=2)},
            {"purchase_request_id": "pr1", "status": "withdrawn", "seller_store_ref_id": "s2",
             "created_at": now - timedelta(hours=9), "updated_at": now},
        ]
        _login_as(app, client, "admin@example.com", role="admin")
        body = client.get("/api/v1/reports/purchase-request-offer-analytics").json()
        assert body["withdrawn_offers_count"] == 1
        assert abs(body["avg_hours_to_first_offer"] - 1.0) < 0.01  # أول عرض عند الساعة 9 من أصل الطلب في الساعة 10
        assert abs(body["avg_hours_to_accepted_offer"] - 8.0) < 0.01  # العرض المقبول تحدَّث عند الساعة 2 من أصل الطلب في الساعة 10


class TestMember360:

    def _seed_member(self, repo, user_id="member-1", **overrides):
        base = {
            "id": user_id, "business_code": "USR-0001", "primary_role": "individual_seller",
            "account_type": "individual", "status": "active", "created_at": datetime.utcnow(),
            "is_verified_seller": True,
        }
        base.update(overrides)
        repo.users = [base]

    def test_requires_authentication(self, app_and_client):
        _, client = app_and_client
        resp = client.get("/api/v1/reports/member-360/member-1")
        assert resp.status_code == 401

    def test_forbidden_for_non_admin(self, app_and_client):
        app, client = app_and_client
        self._seed_member(app.state.rpt_repository)
        _login_as(app, client, "buyer1@example.com", role="individual_buyer")
        resp = client.get("/api/v1/reports/member-360/member-1")
        assert resp.status_code == 403

    def test_allowed_for_admin(self, app_and_client):
        app, client = app_and_client
        self._seed_member(app.state.rpt_repository)
        _login_as(app, client, "admin1@example.com", role="admin")
        resp = client.get("/api/v1/reports/member-360/member-1")
        assert resp.status_code == 200
        body = resp.json()
        assert body["user_id"] == "member-1"
        assert body["primary_role"] == "individual_seller"
        # طبقة حساسة غير موجودة إطلاقًا في هذا الـEndpoint — لا تسريب
        assert "login_sessions_total" not in body
        assert "conversations_count" not in body

    def test_404_for_nonexistent_user(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "admin2@example.com", role="admin")
        resp = client.get("/api/v1/reports/member-360/does-not-exist")
        assert resp.status_code == 404
        assert resp.json()["detail"]["error_code"] == "USER_NOT_FOUND"

    def test_aggregates_store_inventory_offers(self, app_and_client):
        app, client = app_and_client
        repo = app.state.rpt_repository
        self._seed_member(repo)
        repo.stores = [{"id": "store-1", "owner_user_ref_id": "member-1", "status": "active"}]
        repo.inventory_items = [
            {"status": "active", "store_id": "store-1"},
            {"status": "hidden", "store_id": "store-1"},
            {"status": "active", "store_id": "other-store"},  # لا يجب احتسابه
        ]
        repo.offers = [
            {"status": "accepted", "seller_store_ref_id": "store-1"},
            {"status": "submitted", "seller_store_ref_id": "store-1"},
        ]
        _login_as(app, client, "admin3@example.com", role="admin")
        body = client.get("/api/v1/reports/member-360/member-1").json()
        assert body["store_ids"] == ["store-1"]
        assert body["inventory_items_total"] == 2
        assert body["inventory_items_by_status"] == {"active": 1, "hidden": 1}
        assert body["offers_total"] == 2


class TestMember360Sensitive:

    def _seed_member(self, repo, user_id="member-2"):
        repo.users = [{
            "id": user_id, "business_code": "USR-0002", "primary_role": "individual_buyer",
            "account_type": "individual", "status": "active", "created_at": datetime.utcnow(),
            "is_verified_seller": False,
        }]

    def test_forbidden_for_regular_admin(self, app_and_client):
        """§36-37: SYSTEM_ADMIN_ROLES العامة لا تكفي — admin عادي يُرفَض."""
        app, client = app_and_client
        self._seed_member(app.state.rpt_repository)
        _login_as(app, client, "admin4@example.com", role="admin")
        resp = client.get("/api/v1/reports/member-360/member-2/sensitive")
        assert resp.status_code == 403

    def test_allowed_for_super_admin_only(self, app_and_client):
        app, client = app_and_client
        self._seed_member(app.state.rpt_repository)
        _login_as(app, client, "root1@example.com", role="super_admin")
        resp = client.get("/api/v1/reports/member-360/member-2/sensitive")
        assert resp.status_code == 200
        body = resp.json()
        assert "login_sessions_total" in body
        assert "conversations_count" in body
        # لا محتوى رسائل إطلاقًا — Metadata فقط
        assert "message" not in body
        assert "body" not in body

    def test_login_sessions_from_iam_sessions(self, app_and_client):
        app, client = app_and_client
        repo = app.state.rpt_repository
        self._seed_member(repo)
        now = datetime.utcnow()
        repo.sessions = [
            {"user_id": "member-2", "created_at": now - timedelta(days=2), "revoked_at": now - timedelta(days=1), "revoked_reason": "logout"},
            {"user_id": "member-2", "created_at": now - timedelta(hours=1), "revoked_at": None, "revoked_reason": None},
        ]
        _login_as(app, client, "root2@example.com", role="super_admin")
        body = client.get("/api/v1/reports/member-360/member-2/sensitive").json()
        assert body["login_sessions_total"] == 2
        assert body["last_login_at"] is not None

    def test_conversations_count_is_metadata_only(self, app_and_client):
        app, client = app_and_client
        repo = app.state.rpt_repository
        self._seed_member(repo)
        repo.conversation_participants = [
            {"conversation_id": "conv-1", "user_ref_id": "member-2"},
            {"conversation_id": "conv-2", "user_ref_id": "member-2"},
            {"conversation_id": "conv-1", "user_ref_id": "member-2"},  # نفس المحادثة — لا يُحتسب مرتين
        ]
        _login_as(app, client, "root3@example.com", role="super_admin")
        body = client.get("/api/v1/reports/member-360/member-2/sensitive").json()
        assert body["conversations_count"] == 2

    def test_admin_safe_endpoint_never_calls_sensitive_repository_method(self, app_and_client):
        """Corrective — إثبات معماري مباشر (ليس فقط اختبار سلوك HTTP): يُغلِّف
        كائن الـRepository الفعلي بمُتتبِّع استدعاءات، ثم يتحقق أن استدعاء
        /member-360/{id} العام لا يستدعي get_member_360_sensitive إطلاقًا،
        وأن /member-360/{id}/sensitive لا يستدعي get_member_360 إطلاقًا. هذا
        يمنع تكرار Bug التوصيل (Endpoint حساس يستدعي طريقة Admin-safe أو
        العكس) حتى لو تطابقت الاستجابة HTTP ظاهريًا."""
        app, client = app_and_client
        self._seed_member(app.state.rpt_repository)

        calls = {"get_member_360": 0, "get_member_360_sensitive": 0}
        real_repo = app.state.rpt_repository
        original_admin_safe = real_repo.get_member_360
        original_sensitive = real_repo.get_member_360_sensitive

        def tracked_admin_safe(user_id):
            calls["get_member_360"] += 1
            return original_admin_safe(user_id)

        def tracked_sensitive(user_id):
            calls["get_member_360_sensitive"] += 1
            return original_sensitive(user_id)

        real_repo.get_member_360 = tracked_admin_safe
        real_repo.get_member_360_sensitive = tracked_sensitive

        _login_as(app, client, "root5@example.com", role="super_admin")

        # المسار العام أولًا
        resp1 = client.get("/api/v1/reports/member-360/member-2")
        assert resp1.status_code == 200
        assert calls == {"get_member_360": 1, "get_member_360_sensitive": 0}, \
            "المسار العام استدعى get_member_360_sensitive — تسريب Data Access حساسة"

        # ثم المسار الحساس
        resp2 = client.get("/api/v1/reports/member-360/member-2/sensitive")
        assert resp2.status_code == 200
        assert calls == {"get_member_360": 1, "get_member_360_sensitive": 1}, \
            "المسار الحساس لم يستدعِ get_member_360_sensitive (أو استدعى get_member_360 خطأً)"


class TestStore360:

    def _seed_store(self, repo, store_id="store-x"):
        repo.stores = [{"id": store_id, "owner_user_ref_id": "owner-1", "status": "active", "created_at": datetime.utcnow()}]

    def test_requires_authentication(self, app_and_client):
        _, client = app_and_client
        resp = client.get("/api/v1/reports/store-360/store-x")
        assert resp.status_code == 401

    def test_forbidden_for_non_admin(self, app_and_client):
        app, client = app_and_client
        self._seed_store(app.state.rpt_repository)
        _login_as(app, client, "seller1@example.com", role="individual_seller")
        resp = client.get("/api/v1/reports/store-360/store-x")
        assert resp.status_code == 403

    def test_allowed_for_admin(self, app_and_client):
        app, client = app_and_client
        self._seed_store(app.state.rpt_repository)
        _login_as(app, client, "admin5@example.com", role="admin")
        resp = client.get("/api/v1/reports/store-360/store-x")
        assert resp.status_code == 200
        assert resp.json()["store_id"] == "store-x"

    def test_404_for_nonexistent_store(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "admin6@example.com", role="admin")
        resp = client.get("/api/v1/reports/store-360/ghost-store")
        assert resp.status_code == 404
        assert resp.json()["detail"]["error_code"] == "STORE_NOT_FOUND"

    def test_accepted_offer_rate_calculation(self, app_and_client):
        app, client = app_and_client
        repo = app.state.rpt_repository
        self._seed_store(repo)
        repo.offers = [
            {"status": "accepted", "seller_store_ref_id": "store-x"},
            {"status": "accepted", "seller_store_ref_id": "store-x"},
            {"status": "rejected", "seller_store_ref_id": "store-x"},
            {"status": "submitted", "seller_store_ref_id": "other-store"},
        ]
        _login_as(app, client, "admin7@example.com", role="admin")
        body = client.get("/api/v1/reports/store-360/store-x").json()
        assert body["offers_total"] == 3
        assert body["accepted_offers_total"] == 2
        assert abs(body["accepted_offer_rate"] - (2 / 3)) < 0.001

    def test_no_sensitive_endpoint_exists_for_store(self, app_and_client):
        """Store 360 بلا طبقة حساسة — لا Endpoint /sensitive له إطلاقًا."""
        app, client = app_and_client
        self._seed_store(app.state.rpt_repository)
        _login_as(app, client, "root4@example.com", role="super_admin")
        resp = client.get("/api/v1/reports/store-360/store-x/sensitive")
        assert resp.status_code == 404


class TestDataQualityDashboard:

    def test_requires_authentication(self, app_and_client):
        _, client = app_and_client
        resp = client.get("/api/v1/reports/data-quality")
        assert resp.status_code == 401

    def test_forbidden_for_non_admin(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "buyer2@example.com", role="individual_buyer")
        resp = client.get("/api/v1/reports/data-quality")
        assert resp.status_code == 403

    def test_allowed_for_admin(self, app_and_client):
        app, client = app_and_client
        _login_as(app, client, "admin8@example.com", role="admin")
        resp = client.get("/api/v1/reports/data-quality")
        assert resp.status_code == 200

    def test_price_upon_contact_excluded_from_without_price(self, app_and_client):
        """§9/§25: pricing_mode='contact_for_price' حالة صحيحة، ليست نقصًا."""
        app, client = app_and_client
        repo = app.state.rpt_repository
        repo.inventory_items = [
            {"id": "i1", "pricing_mode": "fixed_price", "price_amount": None},   # نقص حقيقي
            {"id": "i2", "pricing_mode": "contact_for_price", "price_amount": None},  # صحيح، مستثنى
            {"id": "i3", "pricing_mode": "fixed_price", "price_amount": 100},
        ]
        _login_as(app, client, "admin9@example.com", role="admin")
        body = client.get("/api/v1/reports/data-quality").json()
        assert body["inventory_items_without_price"] == 1
        assert body["inventory_items_total"] == 3

    def test_items_without_images(self, app_and_client):
        app, client = app_and_client
        repo = app.state.rpt_repository
        repo.inventory_items = [{"id": "i1"}, {"id": "i2"}]
        repo.media_attachments = [{"owner_type": "inventory_item", "owner_ref_id": "i1", "status": "active"}]
        _login_as(app, client, "admin10@example.com", role="admin")
        body = client.get("/api/v1/reports/data-quality").json()
        assert body["inventory_items_without_images"] == 1

    def test_catalog_parts_not_linked_to_vehicle(self, app_and_client):
        app, client = app_and_client
        repo = app.state.rpt_repository
        repo.catalog_parts = [{"id": "p1", "status": "approved"}, {"id": "p2", "status": "proposed"}]
        repo.compatibility_records = [{"catalog_part_ref_id": "p1"}]
        _login_as(app, client, "admin11@example.com", role="admin")
        body = client.get("/api/v1/reports/data-quality").json()
        assert body["catalog_parts_not_linked_to_vehicle"] == 1
        assert body["catalog_parts_proposed_pending_review"] == 1
