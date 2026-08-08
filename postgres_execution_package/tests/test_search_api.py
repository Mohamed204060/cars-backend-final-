"""
test_search_api.py — اختبارات وحدة لطبقة REST API لخدمة البحث
تستخدم InMemorySearchRepository (لا اتصال قاعدة بيانات؛ لا مصادقة مطلوبة —
GET /search/parts عام، security: [] في العقد).
"""

from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from search_api import router as search_router
from search_repository import InMemorySearchRepository
from search_service import InventoryItemView


def _item(**overrides) -> InventoryItemView:
    defaults = dict(
        id="item-1", business_code="IT-0001", part_name="فلتر زيت", store_name="متجر تجريبي",
        store_ref_id="store-1", country_code="SA", city_code="RUH", condition_code="new",
        price_amount=150.0, is_verified_seller=True, seller_rating=4.5,
        created_at=datetime.now(timezone.utc),
    )
    defaults.update(overrides)
    return InventoryItemView(**defaults)


@pytest.fixture
def app_and_client():
    app = FastAPI()
    app.include_router(search_router)
    app.state.search_repository = InMemorySearchRepository(items=[])
    client = TestClient(app, base_url="https://testserver")
    return app, client


class TestSearchNoAuthRequired:

    def test_search_works_without_any_session(self, app_and_client):
        _, client = app_and_client
        resp = client.get("/api/v1/search/parts")
        assert resp.status_code == 200


class TestSearchFiltersAndPagination:

    def test_empty_results_when_no_items(self, app_and_client):
        _, client = app_and_client
        resp = client.get("/api/v1/search/parts")
        assert resp.json()["results"] == []
        assert resp.json()["pagination"]["total_items"] == 0

    def test_filters_by_condition(self, app_and_client):
        app, client = app_and_client
        app.state.search_repository = InMemorySearchRepository(items=[
            _item(id="a", condition_code="new"), _item(id="b", condition_code="used"),
        ])
        resp = client.get("/api/v1/search/parts", params={"condition_ref_id": "used"})
        results = resp.json()["results"]
        assert len(results) == 1
        assert results[0]["inventory_item_id"] == "b"

    def test_filters_by_store(self, app_and_client):
        app, client = app_and_client
        app.state.search_repository = InMemorySearchRepository(items=[
            _item(id="a", store_ref_id="store-A"), _item(id="b", store_ref_id="store-B"),
        ])
        resp = client.get("/api/v1/search/parts", params={"store_ref_id": "store-A"})
        results = resp.json()["results"]
        assert len(results) == 1
        assert results[0]["inventory_item_id"] == "a"

    def test_price_filter_priced_only(self, app_and_client):
        app, client = app_and_client
        app.state.search_repository = InMemorySearchRepository(items=[
            _item(id="a", price_amount=100.0), _item(id="b", price_amount=None),
        ])
        resp = client.get("/api/v1/search/parts", params={"price_filter": "priced_only"})
        results = resp.json()["results"]
        assert len(results) == 1
        assert results[0]["inventory_item_id"] == "a"

    def test_pagination_respects_page_size(self, app_and_client):
        app, client = app_and_client
        items = [_item(id=f"item-{i}") for i in range(5)]
        app.state.search_repository = InMemorySearchRepository(items=items)
        resp = client.get("/api/v1/search/parts", params={"page": 1, "page_size": 2})
        body = resp.json()
        assert len(body["results"]) == 2
        assert body["pagination"]["total_items"] == 5
        assert body["pagination"]["page"] == 1
        assert body["pagination"]["page_size"] == 2


class TestPriceDisplayText:
    """REQ-STR-014: نص بديل عند غياب السعر."""

    def test_priced_item_shows_formatted_amount(self, app_and_client):
        app, client = app_and_client
        app.state.search_repository = InMemorySearchRepository(items=[_item(price_amount=99.5)])
        resp = client.get("/api/v1/search/parts")
        assert resp.json()["results"][0]["price_display_text"] == "99.50"

    def test_unpriced_item_shows_fallback_text(self, app_and_client):
        app, client = app_and_client
        app.state.search_repository = InMemorySearchRepository(items=[_item(price_amount=None)])
        resp = client.get("/api/v1/search/parts")
        assert resp.json()["results"][0]["price_display_text"] == "تواصل مع البائع للسعر"
        assert resp.json()["results"][0]["price_amount"] is None


class TestEffectiveCountry:

    def test_manual_country_from_query_param_is_reflected(self, app_and_client):
        app, client = app_and_client
        app.state.search_repository = InMemorySearchRepository(items=[_item(country_code="SA")])
        resp = client.get("/api/v1/search/parts", params={"country_ref_id": "SA"})
        body = resp.json()
        assert body["effective_country_code"] == "SA"
        assert body["effective_country_source"] == "manual"

    def test_no_country_param_means_no_source(self, app_and_client):
        _, client = app_and_client
        resp = client.get("/api/v1/search/parts")
        assert resp.json()["effective_country_source"] == "none"


class TestCR019StoreIdInSearchResults:
    """CR-019: store_id حقيقي من InventoryItemView.store_ref_id — لا store_name وهمي جديد."""

    def test_store_id_present_and_correct(self, app_and_client):
        app, client = app_and_client
        app.state.search_repository = InMemorySearchRepository(items=[_item(store_ref_id="store-xyz")])
        resp = client.get("/api/v1/search/parts")
        assert resp.status_code == 200
        body = resp.json()
        assert body["results"][0]["store_id"] == "store-xyz"

    def test_no_new_fake_store_name_or_image(self, app_and_client):
        """يتحقق أن store_name يبقى كما يأتي من المستودع (لا تحسين وهمي)،
        وأن image_url يبقى null دائمًا (GAP-B غير مُنفَّذة)."""
        app, client = app_and_client
        app.state.search_repository = InMemorySearchRepository(items=[_item(store_name="")])
        resp = client.get("/api/v1/search/parts")
        body = resp.json()
        assert body["results"][0]["store_name"] == ""
        assert body["results"][0]["image_url"] is None
