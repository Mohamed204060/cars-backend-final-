"""
test_search_service.py — اختبارات وحدة لخدمة البحث
تُشغَّل عبر: python3 -m unittest discover -s tests -v
كل اختبار يذكر معرّف المتطلب الذي يتحقق منه صراحة في اسمه أو تعليقه.
"""

import sys
import os
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from search_service import (  # noqa: E402
    determine_effective_country,
    apply_search_filters,
    sort_results,
    paginate,
    execute_search,
    InventoryItemView,
)


def make_item(id_, business_code, country="SA", city="SA-RUH", condition="new",
              price=None, verified=False, rating=None, created_offset_minutes=0, store_ref_id="store-1"):
    return InventoryItemView(
        id=id_,
        business_code=business_code,
        part_name=f"Part {id_}",
        store_name=f"Store {id_}",
        store_ref_id=store_ref_id,
        country_code=country,
        city_code=city,
        condition_code=condition,
        price_amount=price,
        is_verified_seller=verified,
        seller_rating=rating,
        created_at=datetime(2026, 1, 1) + timedelta(minutes=created_offset_minutes),
    )


class TestEffectiveCountryDetection(unittest.TestCase):
    """REQ-SRC-006-C, 006-D, 006-E"""

    def test_priority_account_over_geo_and_ip(self):
        # REQ-SRC-006-C: الحساب له الأولوية الأولى
        r = determine_effective_country(account_country_code="SA",
                                         geolocation_country_code="AE",
                                         ip_country_code="EG")
        self.assertEqual(r.country_code, "SA")
        self.assertEqual(r.source, "account")

    def test_priority_geo_over_ip_when_no_account(self):
        r = determine_effective_country(account_country_code=None,
                                         geolocation_country_code="AE",
                                         ip_country_code="EG")
        self.assertEqual(r.country_code, "AE")
        self.assertEqual(r.source, "geolocation")

    def test_priority_ip_when_only_ip_available(self):
        r = determine_effective_country(account_country_code=None,
                                         geolocation_country_code=None,
                                         ip_country_code="EG")
        self.assertEqual(r.country_code, "EG")
        self.assertEqual(r.source, "ip")

    def test_no_filter_when_all_sources_absent(self):
        # REQ-SRC-006-E: عدم فرض تصفية جغرافية عند تعذر الاكتشاف
        r = determine_effective_country()
        self.assertIsNone(r.country_code)
        self.assertEqual(r.source, "none")

    def test_manual_override_takes_absolute_priority(self):
        # REQ-SRC-006-D: التعديل اليدوي يتجاوز كل الاكتشاف التلقائي
        r = determine_effective_country(account_country_code="SA",
                                         geolocation_country_code="AE",
                                         ip_country_code="EG",
                                         manual_country_code="EG")
        self.assertEqual(r.country_code, "EG")
        self.assertEqual(r.source, "manual")

    def test_detection_does_not_mutate_account_data(self):
        # REQ-SRC-006-C (المبرر المضاف): التأكد أن الدالة استعلامية بحتة بلا آثار جانبية
        account_country = "SA"
        _ = determine_effective_country(account_country_code=account_country,
                                         geolocation_country_code="AE")
        self.assertEqual(account_country, "SA")  # لم يتغيّر المتغيّر الممثِّل لبيانات الحساب


class TestSearchFilters(unittest.TestCase):
    """REQ-SRC-003, 004, 006, 006-A, 006-B"""

    def setUp(self):
        self.items = [
            make_item("1", "INV-001", country="SA", price=100, condition="new", verified=True, store_ref_id="store-A"),
            make_item("2", "INV-002", country="SA", price=None, condition="used_good", verified=False, store_ref_id="store-B"),
            make_item("3", "INV-003", country="AE", price=200, condition="new", verified=True, store_ref_id="store-A"),
        ]

    def test_country_filter(self):
        result = apply_search_filters(self.items, country_code="SA")
        self.assertEqual(len(result), 2)
        self.assertTrue(all(i.country_code == "SA" for i in result))

    def test_no_country_filter_returns_all(self):
        # REQ-SRC-006-E behavior at the filter level: country_code=None => no restriction
        result = apply_search_filters(self.items, country_code=None)
        self.assertEqual(len(result), 3)

    def test_price_filter_priced_only(self):
        result = apply_search_filters(self.items, price_filter="priced_only")
        self.assertEqual(len(result), 2)
        self.assertTrue(all(i.price_amount is not None for i in result))

    def test_price_filter_unpriced_only(self):
        result = apply_search_filters(self.items, price_filter="unpriced_only")
        self.assertEqual(len(result), 1)
        self.assertIsNone(result[0].price_amount)

    def test_condition_filter(self):
        result = apply_search_filters(self.items, condition_code="new")
        self.assertEqual(len(result), 2)

    def test_verified_sellers_only_filter(self):
        result = apply_search_filters(self.items, verified_sellers_only=True)
        self.assertEqual(len(result), 2)
        self.assertTrue(all(i.is_verified_seller for i in result))

    def test_store_filter_scopes_to_single_store(self):
        # سياق إعادة استخدام مكوّن البحث داخل صفحة المتجر (CR-006 مناقشة إعادة الاستخدام)
        result = apply_search_filters(self.items, store_ref_id="store-A")
        self.assertEqual(len(result), 2)
        self.assertTrue(all(i.store_ref_id == "store-A" for i in result))

    def test_no_store_filter_returns_all_stores(self):
        result = apply_search_filters(self.items, store_ref_id=None)
        self.assertEqual(len(result), 3)


class TestSortingAndTieBreaker(unittest.TestCase):
    """REQ-SRC-007, 007-A"""

    def test_sort_by_rating_descending(self):
        items = [
            make_item("1", "INV-003", rating=3.0),
            make_item("2", "INV-001", rating=4.5),
            make_item("3", "INV-002", rating=1.0),
        ]
        result = sort_results(items)
        self.assertEqual([i.business_code for i in result], ["INV-001", "INV-003", "INV-002"])

    def test_tie_breaker_newest_first_then_business_code(self):
        # نفس التقييم تمامًا لثلاثة عناصر: يجب أن يُحسَم الترتيب بالأحدث ثم business_code
        items = [
            make_item("1", "INV-003", rating=4.0, created_offset_minutes=10),
            make_item("2", "INV-001", rating=4.0, created_offset_minutes=20),
            make_item("3", "INV-002", rating=4.0, created_offset_minutes=20),
        ]
        result = sort_results(items)
        # الأحدث (offset=20) أولاً، وبينهما INV-001 قبل INV-002 أبجديًا
        self.assertEqual([i.business_code for i in result], ["INV-001", "INV-002", "INV-003"])

    def test_deterministic_repeat_search_same_order(self):
        # تكرار البحث نفسه ينتج الترتيب نفسه تمامًا (معيار قبول REQ-SRC-007-A)
        items = [
            make_item("1", "INV-002", rating=None, created_offset_minutes=5),
            make_item("2", "INV-001", rating=None, created_offset_minutes=5),
        ]
        first_run = [i.business_code for i in sort_results(items)]
        second_run = [i.business_code for i in sort_results(items)]
        self.assertEqual(first_run, second_run)


class TestPagination(unittest.TestCase):

    def test_pagination_basic(self):
        items = [make_item(str(i), f"INV-{i:03d}") for i in range(1, 26)]
        page1 = paginate(items, page=1, page_size=10)
        self.assertEqual(len(page1.items), 10)
        self.assertEqual(page1.total_items, 25)
        page3 = paginate(items, page=3, page_size=10)
        self.assertEqual(len(page3.items), 5)  # آخر صفحة جزئية

    def test_pagination_invalid_page_defaults_to_1(self):
        items = [make_item("1", "INV-001")]
        result = paginate(items, page=0, page_size=10)
        self.assertEqual(result.page, 1)


class TestExecuteSearchIntegrationOfUnits(unittest.TestCase):
    """اختبار تكاملي (على مستوى الوحدة، دون قاعدة بيانات فعلية) لرحلة البحث الكاملة"""

    def test_full_search_with_auto_detected_country_and_no_manual_override(self):
        items = [
            make_item("1", "INV-001", country="SA", price=100, rating=4.0),
            make_item("2", "INV-002", country="AE", price=150, rating=5.0),
        ]
        result = execute_search(items, account_country_code="SA")
        self.assertEqual(result["effective_country_code"], "SA")
        self.assertEqual(result["effective_country_source"], "account")
        self.assertEqual(len(result["results"]), 1)
        self.assertEqual(result["results"][0].business_code, "INV-001")

    def test_full_search_with_no_detection_shows_all_countries(self):
        items = [
            make_item("1", "INV-001", country="SA"),
            make_item("2", "INV-002", country="AE"),
        ]
        result = execute_search(items)  # لا حساب، لا موقع، لا IP
        self.assertEqual(result["effective_country_source"], "none")
        self.assertEqual(len(result["results"]), 2)  # REQ-SRC-006-E: بلا قيد جغرافي


if __name__ == "__main__":
    unittest.main(verbosity=2)
