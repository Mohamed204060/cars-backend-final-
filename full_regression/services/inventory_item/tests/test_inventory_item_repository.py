"""
test_inventory_item_repository.py — اختبارات وحدة لتنسيق خدمة عنصر المخزون عبر Repository
تُشغَّل عبر: python3 -m unittest discover -s tests -v
"""

import sys
import os
import unittest
import inspect

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from inventory_item_service import (  # noqa: E402
    create_inventory_item_via_repository, update_quantity_via_repository, archive_item_via_repository,
    ItemArchivedImmutableError,
)
import inventory_item_repository as repo_module  # noqa: E402
from inventory_item_repository import InMemoryInventoryItemRepository  # noqa: E402


class TestNoHardDeleteInRepositoryLayer(unittest.TestCase):

    def test_no_delete_named_method_anywhere_in_repository_module(self):
        forbidden_keywords = ["delete", "remove", "destroy", "purge"]
        classes = inspect.getmembers(repo_module, inspect.isclass)
        offending = []
        for cls_name, cls in classes:
            for method_name in dir(cls):
                if any(k in method_name.lower() for k in forbidden_keywords):
                    offending.append(f"{cls_name}.{method_name}")
        self.assertEqual(offending, [], f"عُثر على دوال تشير لحذف فعلي محتمل: {offending}")


class TestInventoryItemRepositoryOrchestration(unittest.TestCase):

    def setUp(self):
        self.repo = InMemoryInventoryItemRepository()

    def test_create_item_via_repository_assigns_id(self):
        item = create_inventory_item_via_repository(
            self.repo, "store-1", "part-1", "cond-1", "contact_for_price", quantity=5
        )
        self.assertTrue(item.id.startswith("item-"))
        self.assertEqual(item.status, "active")

    def test_update_quantity_via_repository_persists_and_transitions_status(self):
        item = create_inventory_item_via_repository(
            self.repo, "store-1", "part-1", "cond-1", "contact_for_price", quantity=5
        )
        update_quantity_via_repository(self.repo, item.id, 0)

        fetched = self.repo.get_item_by_id(item.id)
        self.assertEqual(fetched.quantity, 0)
        self.assertEqual(fetched.status, "out_of_stock")

    def test_archive_via_repository_persists_archived_status(self):
        item = create_inventory_item_via_repository(
            self.repo, "store-1", "part-1", "cond-1", "contact_for_price", quantity=5
        )
        archive_item_via_repository(self.repo, item.id)

        fetched = self.repo.get_item_by_id(item.id)
        self.assertEqual(fetched.status, "archived")

    def test_cannot_update_quantity_after_archive_via_repository(self):
        item = create_inventory_item_via_repository(
            self.repo, "store-1", "part-1", "cond-1", "contact_for_price", quantity=5
        )
        archive_item_via_repository(self.repo, item.id)

        with self.assertRaises(ItemArchivedImmutableError):
            update_quantity_via_repository(self.repo, item.id, 10)

        # التأكد من أن الكمية لم تتغيّر رغم محاولة التحديث المرفوضة
        fetched = self.repo.get_item_by_id(item.id)
        self.assertEqual(fetched.quantity, 5)

    def test_operations_on_unknown_item_raise(self):
        with self.assertRaises(ValueError):
            update_quantity_via_repository(self.repo, "nonexistent", 1)
        with self.assertRaises(ValueError):
            archive_item_via_repository(self.repo, "nonexistent")


if __name__ == "__main__":
    unittest.main(verbosity=2)
