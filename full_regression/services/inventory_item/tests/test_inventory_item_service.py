"""
test_inventory_item_service.py — اختبارات وحدة لخدمة عنصر مخزون البائع
تُشغَّل عبر: python3 -m unittest discover -s tests -v
"""

import sys
import os
import unittest
import inspect

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import inventory_item_service as svc  # noqa: E402
from inventory_item_service import (  # noqa: E402
    InventoryItem, create_inventory_item, update_quantity, update_pricing,
    hide_item, unhide_item, archive_item, ensure_modifiable,
    InvalidPricingError, ItemArchivedImmutableError, InvalidQuantityError,
    CatalogPartNotApprovedError,
)


class TestNoHardDeleteExists(unittest.TestCase):
    """
    مبدأ معتمَد صراحة: لا حذف فعلي لعناصر المخزون؛ هذا الاختبار يفحص وحدة
    الكود نفسها للتأكد من عدم وجود أي دالة تحمل اسمًا يوحي بالحذف الفعلي،
    لا فقط الاعتماد على عدم استدعائها.
    """

    def test_no_delete_function_defined_in_module(self):
        function_names = [name for name, obj in inspect.getmembers(svc, inspect.isfunction)]
        forbidden_keywords = ["delete", "remove", "destroy", "purge"]
        offending = [
            name for name in function_names
            if any(keyword in name.lower() for keyword in forbidden_keywords)
        ]
        self.assertEqual(offending, [], f"عُثر على دوال تشير لحذف فعلي محتمل: {offending}")

    def test_only_archive_changes_status_to_archived(self):
        # التأكد من أن "archive_item" هي الطريقة الوحيدة الموثَّقة للوصول لحالة archived
        item = create_inventory_item("store-1", "part-1", "cond-1", "fixed_price", quantity=5,
                                      price_amount=100, price_currency="SAR")
        archive_item(item)
        self.assertEqual(item.status, "archived")


class TestPricingValidation(unittest.TestCase):
    """REQ-STR-012"""

    def test_fixed_price_requires_amount_and_currency(self):
        with self.assertRaises(InvalidPricingError):
            create_inventory_item("store-1", "part-1", "cond-1", "fixed_price", quantity=1)

    def test_contact_for_price_rejects_amount(self):
        with self.assertRaises(InvalidPricingError):
            create_inventory_item("store-1", "part-1", "cond-1", "contact_for_price",
                                   quantity=1, price_amount=50)

    def test_fixed_price_rejects_negative_amount(self):
        with self.assertRaises(InvalidPricingError):
            create_inventory_item("store-1", "part-1", "cond-1", "fixed_price",
                                   quantity=1, price_amount=-10, price_currency="SAR")

    def test_valid_fixed_price_succeeds(self):
        item = create_inventory_item("store-1", "part-1", "cond-1", "fixed_price",
                                      quantity=1, price_amount=100, price_currency="SAR")
        self.assertEqual(item.price_amount, 100)

    def test_valid_contact_for_price_succeeds(self):
        item = create_inventory_item("store-1", "part-1", "cond-1", "contact_for_price", quantity=1)
        self.assertIsNone(item.price_amount)


class TestQuantityAndStatusTransitions(unittest.TestCase):
    """REQ-STR-017"""

    def test_creation_with_zero_quantity_is_out_of_stock(self):
        item = create_inventory_item("store-1", "part-1", "cond-1", "contact_for_price", quantity=0)
        self.assertEqual(item.status, "out_of_stock")

    def test_creation_with_positive_quantity_is_active(self):
        item = create_inventory_item("store-1", "part-1", "cond-1", "contact_for_price", quantity=3)
        self.assertEqual(item.status, "active")

    def test_update_quantity_to_zero_auto_transitions_out_of_stock(self):
        item = create_inventory_item("store-1", "part-1", "cond-1", "contact_for_price", quantity=3)
        update_quantity(item, 0)
        self.assertEqual(item.status, "out_of_stock")

    def test_update_quantity_from_zero_back_to_active(self):
        item = create_inventory_item("store-1", "part-1", "cond-1", "contact_for_price", quantity=0)
        update_quantity(item, 5)
        self.assertEqual(item.status, "active")

    def test_negative_quantity_rejected(self):
        item = create_inventory_item("store-1", "part-1", "cond-1", "contact_for_price", quantity=1)
        with self.assertRaises(InvalidQuantityError):
            update_quantity(item, -1)

    def test_quantity_update_does_not_unhide_hidden_item(self):
        item = create_inventory_item("store-1", "part-1", "cond-1", "contact_for_price", quantity=5)
        hide_item(item)
        update_quantity(item, 10)
        self.assertEqual(item.status, "hidden")  # لا يُعاد للحالة النشطة تلقائيًا


class TestHideUnhide(unittest.TestCase):

    def test_hide_then_unhide_restores_active_if_stocked(self):
        item = create_inventory_item("store-1", "part-1", "cond-1", "contact_for_price", quantity=2)
        hide_item(item)
        self.assertEqual(item.status, "hidden")
        unhide_item(item)
        self.assertEqual(item.status, "active")

    def test_unhide_restores_out_of_stock_if_zero_quantity(self):
        item = create_inventory_item("store-1", "part-1", "cond-1", "contact_for_price", quantity=0)
        hide_item(item)
        unhide_item(item)
        self.assertEqual(item.status, "out_of_stock")

    def test_unhide_non_hidden_item_raises(self):
        item = create_inventory_item("store-1", "part-1", "cond-1", "contact_for_price", quantity=1)
        with self.assertRaises(ValueError):
            unhide_item(item)


class TestArchivedImmutability(unittest.TestCase):
    """REQ-STR-019"""

    def setUp(self):
        self.item = create_inventory_item("store-1", "part-1", "cond-1", "contact_for_price", quantity=1)
        archive_item(self.item)

    def test_cannot_update_quantity_after_archive(self):
        with self.assertRaises(ItemArchivedImmutableError):
            update_quantity(self.item, 5)

    def test_cannot_update_pricing_after_archive(self):
        with self.assertRaises(ItemArchivedImmutableError):
            update_pricing(self.item, "contact_for_price")

    def test_cannot_hide_after_archive(self):
        with self.assertRaises(ItemArchivedImmutableError):
            hide_item(self.item)

    def test_cannot_re_archive_already_archived_item(self):
        with self.assertRaises(ItemArchivedImmutableError):
            archive_item(self.item)


class TestSsotCatalogPartApprovalCheck(unittest.TestCase):
    """
    مبدأ SSOT المعتمَد صراحة: لا إنشاء لعنصر مخزون إلا بالإشارة لقطعة كتالوج
    معتمدة، عبر دالة فحص محقونة من خدمة PCT (Dependency Injection)، لا
    استعلامًا مباشرًا لبيانات PCT من داخل هذه الخدمة.
    """

    def test_creation_rejected_when_checker_says_not_approved(self):
        checker = lambda part_ref_id: False  # noqa: E731
        with self.assertRaises(CatalogPartNotApprovedError):
            create_inventory_item("store-1", "part-unapproved", "cond-1", "contact_for_price",
                                   quantity=1, is_part_approved_checker=checker)

    def test_creation_succeeds_when_checker_says_approved(self):
        checker = lambda part_ref_id: part_ref_id == "part-approved"  # noqa: E731
        item = create_inventory_item("store-1", "part-approved", "cond-1", "contact_for_price",
                                      quantity=1, is_part_approved_checker=checker)
        self.assertEqual(item.catalog_part_ref_id, "part-approved")

    def test_creation_without_checker_does_not_block(self):
        # عند غياب الدالة المحقونة، لا يُفترَض الاعتماد ضمنًا كخطأ؛ يُترَك
        # التحقق لطبقة تنسيق أعلى إن لم تُمرَّر هنا (سلوك موثَّق صراحة)
        item = create_inventory_item("store-1", "part-1", "cond-1", "contact_for_price", quantity=1)
        self.assertEqual(item.catalog_part_ref_id, "part-1")


if __name__ == "__main__":
    unittest.main(verbosity=2)
