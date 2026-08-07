"""
test_store_service.py — اختبارات وحدة لخدمة المتاجر
تُشغَّل عبر: python3 -m unittest discover -s tests -v
"""

import sys
import os
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from store_service import (  # noqa: E402
    Store, create_store, transition_store_status, transfer_ownership,
    send_correction_request, resolve_correction_by_seller, moderator_direct_edit,
    build_administrative_audit_event,
    InvalidStatusTransitionError, UnauthorizedOwnershipTransferError, CorrectionWindowStillOpenError,
)


class TestCreateStore(unittest.TestCase):
    """REQ-STR-001"""

    def test_create_store_ssot_stores_only_reference_id(self):
        store = create_store(owner_user_ref_id="user-42")
        self.assertEqual(store.owner_user_ref_id, "user-42")
        self.assertEqual(store.status, "active")
        # SSOT: لا حقول أخرى تخص المستخدم في الكيان سوى المعرّف المرجعي
        self.assertNotIn("owner_name", store.__dict__)
        self.assertNotIn("owner_email", store.__dict__)


class TestStoreStatusLifecycle(unittest.TestCase):
    """REQ-STR-004"""

    def test_creating_to_active_allowed(self):
        store = Store(id="s1", owner_user_ref_id="user-1", status="creating")
        transition_store_status(store, "active")
        self.assertEqual(store.status, "active")

    def test_active_to_suspended_allowed(self):
        store = Store(id="s1", owner_user_ref_id="user-1", status="active")
        transition_store_status(store, "suspended")
        self.assertEqual(store.status, "suspended")

    def test_suspended_to_active_allowed(self):
        store = Store(id="s1", owner_user_ref_id="user-1", status="suspended")
        transition_store_status(store, "active")
        self.assertEqual(store.status, "active")

    def test_any_to_archived_allowed(self):
        store = Store(id="s1", owner_user_ref_id="user-1", status="active")
        transition_store_status(store, "archived")
        self.assertEqual(store.status, "archived")

    def test_archived_is_terminal_no_transitions_allowed(self):
        store = Store(id="s1", owner_user_ref_id="user-1", status="archived")
        with self.assertRaises(InvalidStatusTransitionError):
            transition_store_status(store, "active")

    def test_creating_to_suspended_rejected(self):
        # لا يجوز تخطي "active" مباشرة إلى "suspended" من "creating"
        store = Store(id="s1", owner_user_ref_id="user-1", status="creating")
        with self.assertRaises(InvalidStatusTransitionError):
            transition_store_status(store, "suspended")

    def test_unknown_status_rejected(self):
        store = Store(id="s1", owner_user_ref_id="user-1", status="active")
        with self.assertRaises(ValueError):
            transition_store_status(store, "deleted")


class TestOwnershipTransfer(unittest.TestCase):
    """REQ-STR-006"""

    def test_admin_can_transfer_ownership(self):
        store = Store(id="s1", owner_user_ref_id="user-1", status="active")
        transfer_ownership(store, "user-2", actor_role="admin")
        self.assertEqual(store.owner_user_ref_id, "user-2")

    def test_non_admin_cannot_transfer_ownership(self):
        store = Store(id="s1", owner_user_ref_id="user-1", status="active")
        with self.assertRaises(UnauthorizedOwnershipTransferError):
            transfer_ownership(store, "user-2", actor_role="moderator")
        # التأكد من عدم تغيّر المالك عند الرفض
        self.assertEqual(store.owner_user_ref_id, "user-1")

    def test_seller_cannot_transfer_ownership(self):
        store = Store(id="s1", owner_user_ref_id="user-1", status="active")
        with self.assertRaises(UnauthorizedOwnershipTransferError):
            transfer_ownership(store, "user-2", actor_role="individual_seller")


class TestCorrectionRequestWorkflow(unittest.TestCase):
    """REQ-STR-007, 008"""

    def test_seller_resolves_before_deadline(self):
        store = Store(id="s1", owner_user_ref_id="user-1", status="active")
        deadline = datetime(2026, 1, 10)
        send_correction_request(store, deadline)
        self.assertTrue(store.has_pending_correction)

        resolve_correction_by_seller(store)
        self.assertFalse(store.has_pending_correction)
        self.assertIsNone(store.correction_deadline)

    def test_moderator_direct_edit_blocked_before_deadline(self):
        store = Store(id="s1", owner_user_ref_id="user-1", status="active")
        deadline = datetime(2026, 1, 10)
        send_correction_request(store, deadline)

        with self.assertRaises(CorrectionWindowStillOpenError):
            moderator_direct_edit(store, actor_role="moderator", current_time=datetime(2026, 1, 5))

    def test_moderator_direct_edit_allowed_after_deadline(self):
        store = Store(id="s1", owner_user_ref_id="user-1", status="active")
        deadline = datetime(2026, 1, 10)
        send_correction_request(store, deadline)

        moderator_direct_edit(store, actor_role="moderator", current_time=datetime(2026, 1, 11))
        self.assertFalse(store.has_pending_correction)

    def test_non_moderator_cannot_direct_edit(self):
        store = Store(id="s1", owner_user_ref_id="user-1", status="active")
        send_correction_request(store, datetime(2026, 1, 10))
        with self.assertRaises(PermissionError):
            moderator_direct_edit(store, actor_role="admin", current_time=datetime(2026, 1, 11))

    def test_direct_edit_without_pending_correction_raises(self):
        store = Store(id="s1", owner_user_ref_id="user-1", status="active")
        with self.assertRaises(ValueError):
            moderator_direct_edit(store, actor_role="moderator", current_time=datetime(2026, 1, 11))


class TestAdministrativeAuditEventBuilder(unittest.TestCase):

    def test_build_event_for_known_action(self):
        event = build_administrative_audit_event("store_suspended", "mod-1", "store-1", reason="مخالفة متكررة")
        self.assertEqual(event["log_type"], "administrative")
        self.assertEqual(event["metadata"]["reason"], "مخالفة متكررة")

    def test_build_event_rejects_unknown_action(self):
        with self.assertRaises(ValueError):
            build_administrative_audit_event("unknown_action", "mod-1", "store-1")


if __name__ == "__main__":
    unittest.main(verbosity=2)
