"""
test_search_analytics_instrumentation.py — اختبارات تسجيل الأحداث التحليلية
المُضافة لـ GET /search/parts في Batch 3A Slice 2 (Search Analytics).

ملف منفصل عمدًا عن test_search_api.py (مغلَق، Batch 1 — بلا أي تعديل عليه)؛
هذا الملف يختبر فقط السلوك الإضافي (تسجيل ana events)، لا سلوك البحث نفسه
(ذلك مُغطًّى بالكامل في test_search_api.py القائم).
"""

from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth_api import router as auth_router
from auth_repository import InMemoryAuthRepository
from auth_service import IdentityProvider, UserIdentity
from session_repository import InMemorySessionRepository
from search_api import router as search_router
from search_repository import InMemorySearchRepository
from search_service import InventoryItemView, normalize_arabic_search_text
from ana_repository import InMemoryAnaRepository
from aud_repository import InMemoryAudRepository


def _item(**overrides) -> InventoryItemView:
    defaults = dict(
        id="item-1", business_code="IT-0001", part_name="فلتر زيت", store_name="متجر تجريبي",
        store_ref_id="store-1", country_code="SA", city_code="RUH", condition_code="new",
        price_amount=150.0, is_verified_seller=True, seller_rating=4.5,
        created_at=datetime.now(timezone.utc),
    )
    defaults.update(overrides)
    return InventoryItemView(**defaults)


class TestSearchAnalyticsWhenAnaWired:

    @pytest.fixture
    def app_and_client(self):
        app = FastAPI()
        app.include_router(search_router)
        app.state.search_repository = InMemorySearchRepository(items=[_item()])
        app.state.ana_repository = InMemoryAnaRepository()
        client = TestClient(app, base_url="https://testserver")
        return app, client

    def test_search_with_results_records_search_performed_only(self, app_and_client):
        app, client = app_and_client
        resp = client.get("/api/v1/search/parts", params={"q": "فلتر"})
        assert resp.status_code == 200

        ana_repo = app.state.ana_repository
        performed, total_p = ana_repo.list_events("search_performed", None, None, None, None, None, 1, 20)
        zero, total_z = ana_repo.list_events("search_zero_results", None, None, None, None, None, 1, 20)
        assert total_p == 1
        assert total_z == 0
        assert performed[0].context_type == "search"
        assert performed[0].metadata["results_count"] == 1
        assert performed[0].metadata["has_query_text"] is True

    def test_search_with_no_results_records_both_events(self, app_and_client):
        app, client = app_and_client
        resp = client.get("/api/v1/search/parts", params={"q": "قطعة-غير-موجودة-إطلاقًا"})
        assert resp.status_code == 200
        assert resp.json()["results"] == []

        ana_repo = app.state.ana_repository
        _, total_p = ana_repo.list_events("search_performed", None, None, None, None, None, 1, 20)
        zero, total_z = ana_repo.list_events("search_zero_results", None, None, None, None, None, 1, 20)
        assert total_p == 1
        assert total_z == 1
        assert zero[0].metadata["results_count"] == 0

    def test_no_raw_query_text_stored_in_metadata(self, app_and_client):
        """Data Minimization: النص الحر الأصلي لا يُخزَّن حرفيًا — فقط طوله
        ووجوده والنسخة المُطبَّعة (normalized_query_term، ليست raw q)."""
        app, client = app_and_client
        client.get("/api/v1/search/parts", params={"q": "نص حساس محتمل"})
        ana_repo = app.state.ana_repository
        items, _ = ana_repo.list_events("search_performed", None, None, None, None, None, 1, 20)
        assert "q" not in items[0].metadata
        assert "query" not in items[0].metadata
        assert items[0].metadata["query_length"] == len("نص حساس محتمل")

    def test_normalized_query_term_recorded_matches_normalize_function(self, app_and_client):
        """Pre-Gate Corrective #3: normalized_query_term مُسجَّل فعليًا، ويطابق
        بالضبط ناتج normalize_arabic_search_text (نفس الدالة النقية المستخدَمة
        فعليًا في search_service.py للمطابقة — لا منطق تطبيع مستقل مُخترَع)."""
        app, client = app_and_client
        raw_query = "فِلْتَر  زيت"
        client.get("/api/v1/search/parts", params={"q": raw_query})
        ana_repo = app.state.ana_repository
        items, _ = ana_repo.list_events("search_performed", None, None, None, None, None, 1, 20)
        assert items[0].metadata["normalized_query_term"] == normalize_arabic_search_text(raw_query)

    def test_normalized_query_term_absent_for_vehicle_only_search(self, app_and_client):
        """بحث بالمركبة فقط (بلا q) يجب ألا يُسجِّل normalized_query_term."""
        app, client = app_and_client
        client.get("/api/v1/search/parts", params={"trim_ref_id": "trim-1"})
        ana_repo = app.state.ana_repository
        items, _ = ana_repo.list_events("search_performed", None, None, None, None, None, 1, 20)
        assert items[0].metadata["normalized_query_term"] is None

    def test_normalized_query_term_length_capped(self, app_and_client):
        """حد دفاعي إضافي (100 حرف) فوق التطبيع نفسه — Data Minimization."""
        app, client = app_and_client
        long_query = "أ" * 500
        client.get("/api/v1/search/parts", params={"q": long_query})
        ana_repo = app.state.ana_repository
        items, _ = ana_repo.list_events("search_performed", None, None, None, None, None, 1, 20)
        assert len(items[0].metadata["normalized_query_term"]) <= 100

    def test_actor_ref_id_none_when_no_session_wired(self, app_and_client):
        """توثيق دقيق: actor_ref_id = None هنا تحديدًا لأن هذا الـFixture لا
        يُضمِّن auth_router/session_repository إطلاقًا (بيئة اختبار بلا جلسات)
        — وليس لأن العقد يفرض None دائمًا. راجع الاختبار التالي للسلوك الفعلي
        عند وجود جلسة حقيقية صالحة."""
        app, client = app_and_client
        client.get("/api/v1/search/parts")
        ana_repo = app.state.ana_repository
        items, _ = ana_repo.list_events("search_performed", None, None, None, None, None, 1, 20)
        assert items[0].actor_ref_id is None


class TestSearchAnalyticsActorAttributionWithRealSession:
    """Root directive: لا نجعل غياب Actor قرارًا دائمًا فقط لتفادي تعديل
    اختبارات مغلقة — يثبت هذا الملف أن actor_ref_id يُشتَق فعليًا من جلسة
    حقيقية صالحة عند توفرها، بلا كسر Anonymous Search."""

    @pytest.fixture
    def app_and_client(self):
        app = FastAPI()
        app.include_router(auth_router)
        app.include_router(search_router)
        providers = [IdentityProvider(code="email_password", display_name="كلمة المرور", category="password", is_enabled=True)]
        app.state.auth_repository = InMemoryAuthRepository(providers=providers, identities=[])
        app.state.session_repository = InMemorySessionRepository()
        app.state.aud_repository = InMemoryAudRepository()
        app.state.search_repository = InMemorySearchRepository(items=[_item()])
        app.state.ana_repository = InMemoryAnaRepository()
        client = TestClient(app, base_url="https://testserver")
        return app, client

    def test_actor_ref_id_populated_from_real_session_when_available(self, app_and_client):
        app, client = app_and_client
        repo = app.state.auth_repository
        user_id = repo.create_user()
        identity = UserIdentity(id="", user_id=user_id, provider_code="email_password",
                                 external_identifier="buyer@example.com", is_verified=True, is_primary=True)
        repo.insert_identity(identity, raw_password="Str0ngPass1!")
        login_resp = client.post("/api/v1/auth/login", json={"login_identifier": "buyer@example.com", "password": "Str0ngPass1!"})
        assert login_resp.status_code == 200

        client.get("/api/v1/search/parts", params={"q": "فلتر"})
        ana_repo = app.state.ana_repository
        items, _ = ana_repo.list_events("search_performed", None, None, None, None, None, 1, 20)
        assert items[0].actor_ref_id == user_id

    def test_search_still_anonymous_safe_without_login(self, app_and_client):
        """حتى مع auth_router/session_repository موصولَين بالكامل، البحث بلا
        تسجيل دخول يبقى يعمل طبيعيًا (لا 401، لا كسر Authorization الأصلي)."""
        app, client = app_and_client
        resp = client.get("/api/v1/search/parts", params={"q": "فلتر"})
        assert resp.status_code == 200


class TestSearchAnalyticsWhenAnaNotWired:
    """الأهم: يثبت أن البحث يعمل طبيعيًا تمامًا حتى بلا ana_repository إطلاقًا —
    بالضبط حالة test_search_api.py/test_postgres_search_api_integration.py
    الحاليتين المغلقتين (لا تعديل عليهما، وهذا يثبت أنهما لن تنكسرا)."""

    @pytest.fixture
    def app_and_client(self):
        app = FastAPI()
        app.include_router(search_router)
        app.state.search_repository = InMemorySearchRepository(items=[_item()])
        # عمدًا: لا app.state.ana_repository إطلاقًا هنا
        client = TestClient(app, base_url="https://testserver")
        return app, client

    def test_search_succeeds_without_ana_repository_wired(self, app_and_client):
        app, client = app_and_client
        resp = client.get("/api/v1/search/parts", params={"q": "فلتر"})
        assert resp.status_code == 200
        assert len(resp.json()["results"]) == 1

    def test_zero_result_search_succeeds_without_ana_repository_wired(self, app_and_client):
        app, client = app_and_client
        resp = client.get("/api/v1/search/parts", params={"q": "غير موجود"})
        assert resp.status_code == 200
        assert resp.json()["results"] == []

    def test_response_contract_unchanged_regardless_of_ana_wiring(self, app_and_client):
        """Regression Sweep: نفس مجموعة الحقول تمامًا في الاستجابة، بغضّ النظر
        عن توصيل ana_repository من عدمه — لا حقل مسرَّب من التحليلات للعميل."""
        app, client = app_and_client
        resp = client.get("/api/v1/search/parts", params={"q": "فلتر"})
        assert set(resp.json().keys()) == {"results", "effective_country_code", "effective_country_source", "pagination"}
