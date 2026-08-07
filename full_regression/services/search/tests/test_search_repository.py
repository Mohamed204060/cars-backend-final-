"""
test_search_repository.py — اختبارات وحدة لتنسيق البحث عبر طبقة Repository
تُشغَّل عبر: python3 -m unittest discover -s tests -v
تستخدم InMemorySearchRepository فقط (بلا قاعدة بيانات حقيقية) للتحقق من
صحة التنسيق بين search_service.py وSearchRepository (حقن الاعتمادية).
"""

import sys
import os
import unittest
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from search_service import InventoryItemView, execute_search_via_repository  # noqa: E402
from search_repository import InMemorySearchRepository  # noqa: E402


def make_item(id_, business_code, country="SA", city="SA-RUH", condition="new",
              price=None, verified=False, rating=None, store_ref_id="store-1"):
    return InventoryItemView(
        id=id_, business_code=business_code, part_name=f"Part {id_}", store_name=f"Store {id_}",
        store_ref_id=store_ref_id, country_code=country, city_code=city, condition_code=condition,
        price_amount=price, is_verified_seller=verified, seller_rating=rating,
        created_at=datetime(2026, 1, 1),
    )


class TestExecuteSearchViaRepository(unittest.TestCase):

    def setUp(self):
        self.items = [
            make_item("1", "INV-001", country="SA", price=100, verified=True, store_ref_id="store-A"),
            make_item("2", "INV-002", country="AE", price=None, verified=False, store_ref_id="store-B"),
            make_item("3", "INV-003", country="SA", price=200, verified=True, store_ref_id="store-A"),
        ]
        self.repo = InMemorySearchRepository(self.items)

    def test_repository_orchestration_applies_country_detection(self):
        result = execute_search_via_repository(self.repo, account_country_code="SA")
        self.assertEqual(result["effective_country_code"], "SA")
        self.assertEqual(result["effective_country_source"], "account")
        self.assertEqual(len(result["results"]), 2)

    def test_repository_orchestration_no_detection_returns_all(self):
        result = execute_search_via_repository(self.repo)
        self.assertEqual(result["effective_country_source"], "none")
        self.assertEqual(len(result["results"]), 3)

    def test_repository_orchestration_store_context_reuse(self):
        # يتحقق من سياق إعادة استخدام صفحة المتجر عبر الاستعلام المفهرَس نفسه
        result = execute_search_via_repository(self.repo, store_ref_id="store-A")
        self.assertEqual(len(result["results"]), 2)
        self.assertTrue(all(i.store_ref_id == "store-A" for i in result["results"]))

    def test_repository_orchestration_price_filter_applied_after_fetch(self):
        result = execute_search_via_repository(self.repo, price_filter="priced_only")
        self.assertEqual(len(result["results"]), 2)
        self.assertTrue(all(i.price_amount is not None for i in result["results"]))

    def test_repository_orchestration_pagination(self):
        result = execute_search_via_repository(self.repo, page=1, page_size=1)
        self.assertEqual(len(result["results"]), 1)
        self.assertEqual(result["pagination"]["total_items"], 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
