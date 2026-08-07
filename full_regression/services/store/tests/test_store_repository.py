"""
test_store_repository.py — اختبارات وحدة لتنسيق خدمة المتاجر عبر Repository
تُشغَّل عبر: python3 -m unittest discover -s tests -v
تستخدم InMemoryStoreRepository فقط (بلا قاعدة بيانات حقيقية).
"""

import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from store_service import (  # noqa: E402
    create_store_via_repository, transition_store_status_via_repository,
    transfer_ownership_via_repository,
    InvalidStatusTransitionError, UnauthorizedOwnershipTransferError,
)
from store_repository import InMemoryStoreRepository  # noqa: E402


class TestStoreRepositoryOrchestration(unittest.TestCase):

    def setUp(self):
        self.repo = InMemoryStoreRepository()

    def test_create_store_via_repository_assigns_id(self):
        store = create_store_via_repository(self.repo, owner_user_ref_id="user-1", country_ref_id="SA")
        self.assertTrue(store.id.startswith("store-"))
        self.assertEqual(store.status, "active")

        fetched = self.repo.get_store_by_id(store.id)
        self.assertEqual(fetched.owner_user_ref_id, "user-1")

    def test_transition_status_via_repository_persists_change(self):
        store = create_store_via_repository(self.repo, owner_user_ref_id="user-1")
        transition_store_status_via_repository(self.repo, store.id, "suspended")

        fetched = self.repo.get_store_by_id(store.id)
        self.assertEqual(fetched.status, "suspended")

    def test_invalid_transition_via_repository_rejected_and_not_persisted(self):
        store = create_store_via_repository(self.repo, owner_user_ref_id="user-1")
        transition_store_status_via_repository(self.repo, store.id, "archived")

        with self.assertRaises(InvalidStatusTransitionError):
            transition_store_status_via_repository(self.repo, store.id, "active")

        fetched = self.repo.get_store_by_id(store.id)
        self.assertEqual(fetched.status, "archived")  # لم يتغيّر رغم محاولة الانتقال المرفوضة

    def test_transfer_ownership_via_repository_by_admin(self):
        store = create_store_via_repository(self.repo, owner_user_ref_id="user-1")
        transfer_ownership_via_repository(self.repo, store.id, "user-2", actor_role="admin")

        fetched = self.repo.get_store_by_id(store.id)
        self.assertEqual(fetched.owner_user_ref_id, "user-2")

    def test_transfer_ownership_via_repository_by_non_admin_rejected(self):
        store = create_store_via_repository(self.repo, owner_user_ref_id="user-1")
        with self.assertRaises(UnauthorizedOwnershipTransferError):
            transfer_ownership_via_repository(self.repo, store.id, "user-2", actor_role="moderator")

        fetched = self.repo.get_store_by_id(store.id)
        self.assertEqual(fetched.owner_user_ref_id, "user-1")  # لم يتغيّر

    def test_transition_unknown_store_raises(self):
        with self.assertRaises(ValueError):
            transition_store_status_via_repository(self.repo, "nonexistent", "active")


if __name__ == "__main__":
    unittest.main(verbosity=2)
