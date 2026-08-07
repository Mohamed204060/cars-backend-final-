"""
test_order_service.py — اختبارات وحدة لخدمة الطلبات (PUR)
تُشغَّل عبر: python3 -m unittest discover -s tests -v
"""

import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from order_service import (  # noqa: E402
    PurchaseRequest, Offer, create_purchase_request, transition_purchase_request_status,
    cancel_purchase_request, update_purchase_request_fields, submit_offer, withdraw_offer, accept_offer,
    build_purchase_request_audit_event,
    InvalidPurchaseRequestStatusError, PurchaseRequestClosedError, PurchaseRequestFieldsLockedError,
    DuplicateActiveOfferError, OfferNotWithdrawableError,
)


class TestCreatePurchaseRequest(unittest.TestCase):
    """REQ-PUR-001, 002"""

    def test_create_success(self):
        pr = create_purchase_request("buyer-1", "part-1", "trim-1")
        self.assertEqual(pr.status, "open")
        self.assertFalse(pr.has_received_offer)

    def test_create_rejected_for_unapproved_part(self):
        checker = lambda part_id: False  # noqa: E731
        with self.assertRaises(ValueError):
            create_purchase_request("buyer-1", "part-1", "trim-1", is_part_approved_checker=checker)

    def test_create_succeeds_with_approved_checker(self):
        checker = lambda part_id: True  # noqa: E731
        pr = create_purchase_request("buyer-1", "part-1", "trim-1", is_part_approved_checker=checker)
        self.assertEqual(pr.catalog_part_ref_id, "part-1")


class TestPurchaseRequestLifecycle(unittest.TestCase):
    """REQ-PUR-005"""

    def test_open_to_under_review_allowed(self):
        pr = create_purchase_request("buyer-1", "part-1", "trim-1")
        transition_purchase_request_status(pr, "under_review")
        self.assertEqual(pr.status, "under_review")

    def test_fulfilled_is_terminal(self):
        pr = create_purchase_request("buyer-1", "part-1", "trim-1")
        transition_purchase_request_status(pr, "under_review")
        transition_purchase_request_status(pr, "fulfilled")
        with self.assertRaises(InvalidPurchaseRequestStatusError):
            transition_purchase_request_status(pr, "open")

    def test_cancel_from_open(self):
        pr = create_purchase_request("buyer-1", "part-1", "trim-1")
        cancel_purchase_request(pr)
        self.assertEqual(pr.status, "cancelled")

    def test_cannot_cancel_fulfilled_request(self):
        pr = create_purchase_request("buyer-1", "part-1", "trim-1")
        transition_purchase_request_status(pr, "under_review")
        transition_purchase_request_status(pr, "fulfilled")
        with self.assertRaises(InvalidPurchaseRequestStatusError):
            cancel_purchase_request(pr)


class TestFieldLockAfterFirstOffer(unittest.TestCase):
    """CR-002"""

    def test_update_allowed_before_any_offer(self):
        pr = create_purchase_request("buyer-1", "part-1", "trim-1")
        update_purchase_request_fields(pr, trim_ref_id="trim-2")
        self.assertEqual(pr.trim_ref_id, "trim-2")

    def test_update_blocked_after_first_offer(self):
        pr = create_purchase_request("buyer-1", "part-1", "trim-1")
        submit_offer(pr, [], "store-1", 100, "SAR", provides_shipping=False)
        with self.assertRaises(PurchaseRequestFieldsLockedError):
            update_purchase_request_fields(pr, trim_ref_id="trim-2")


class TestOfferSubmission(unittest.TestCase):
    """REQ-PUR-011..017"""

    def test_submit_offer_success_transitions_pr_to_under_review(self):
        pr = create_purchase_request("buyer-1", "part-1", "trim-1")
        offer = submit_offer(pr, [], "store-1", 150, "SAR", provides_shipping=True, notes="جديدة تمامًا")
        self.assertEqual(offer.status, "submitted")
        self.assertEqual(pr.status, "under_review")
        self.assertTrue(pr.has_received_offer)

    def test_submit_offer_rejected_on_closed_request(self):
        pr = create_purchase_request("buyer-1", "part-1", "trim-1")
        transition_purchase_request_status(pr, "under_review")
        transition_purchase_request_status(pr, "cancelled")
        with self.assertRaises(PurchaseRequestClosedError):
            submit_offer(pr, [], "store-1", 100, "SAR", provides_shipping=False)

    def test_duplicate_active_offer_from_same_seller_rejected(self):
        pr = create_purchase_request("buyer-1", "part-1", "trim-1")
        existing = [submit_offer(pr, [], "store-1", 100, "SAR", provides_shipping=False)]
        with self.assertRaises(DuplicateActiveOfferError):
            submit_offer(pr, existing, "store-1", 120, "SAR", provides_shipping=False)

    def test_shipping_is_seller_indicated_not_assumed(self):
        # REQ-PUR-016: الشحن اختياري صراحة، لا افتراض ضمني
        pr = create_purchase_request("buyer-1", "part-1", "trim-1")
        offer_no_shipping = submit_offer(pr, [], "store-1", 100, "SAR", provides_shipping=False)
        self.assertFalse(offer_no_shipping.provides_shipping)


class TestOfferWithdrawal(unittest.TestCase):
    """REQ-PUR-018"""

    def test_withdraw_submitted_offer_success(self):
        pr = create_purchase_request("buyer-1", "part-1", "trim-1")
        offer = submit_offer(pr, [], "store-1", 100, "SAR", provides_shipping=False)
        withdraw_offer(offer)
        self.assertEqual(offer.status, "withdrawn")

    def test_cannot_withdraw_accepted_offer(self):
        pr = create_purchase_request("buyer-1", "part-1", "trim-1")
        offer = submit_offer(pr, [], "store-1", 100, "SAR", provides_shipping=False)
        accept_offer(pr, offer, [offer])
        with self.assertRaises(OfferNotWithdrawableError):
            withdraw_offer(offer)


class TestAcceptOfferAndAutoRejectOthers(unittest.TestCase):
    """REQ-PUR-013, 014"""

    def test_accept_one_offer_rejects_others_and_closes_request(self):
        pr = create_purchase_request("buyer-1", "part-1", "trim-1")
        offer1 = submit_offer(pr, [], "store-1", 100, "SAR", provides_shipping=False)
        offer2 = submit_offer(pr, [offer1], "store-2", 90, "SAR", provides_shipping=True)
        offer3 = submit_offer(pr, [offer1, offer2], "store-3", 95, "SAR", provides_shipping=False)

        accept_offer(pr, offer2, [offer1, offer2, offer3])

        self.assertEqual(offer2.status, "accepted")
        self.assertEqual(offer1.status, "rejected")
        self.assertEqual(offer3.status, "rejected")
        self.assertEqual(pr.status, "fulfilled")

    def test_cannot_accept_already_rejected_offer(self):
        pr = create_purchase_request("buyer-1", "part-1", "trim-1")
        offer1 = submit_offer(pr, [], "store-1", 100, "SAR", provides_shipping=False)
        offer2 = submit_offer(pr, [offer1], "store-2", 90, "SAR", provides_shipping=False)
        accept_offer(pr, offer1, [offer1, offer2])

        with self.assertRaises(ValueError):
            accept_offer(pr, offer2, [offer1, offer2])

    def test_no_new_offers_after_fulfilled(self):
        # REQ-PUR-017 مطبَّق فعليًا بعد الإتمام لا فقط الإلغاء/انتهاء الصلاحية
        pr = create_purchase_request("buyer-1", "part-1", "trim-1")
        offer1 = submit_offer(pr, [], "store-1", 100, "SAR", provides_shipping=False)
        accept_offer(pr, offer1, [offer1])

        with self.assertRaises(PurchaseRequestClosedError):
            submit_offer(pr, [offer1], "store-4", 80, "SAR", provides_shipping=False)


class TestAuditEventBuilder(unittest.TestCase):

    def test_build_event_for_known_action(self):
        event = build_purchase_request_audit_event("purchase_request_created", "buyer-1", "pr-1")
        self.assertEqual(event["log_type"], "general")

    def test_build_event_rejects_unknown_action(self):
        with self.assertRaises(ValueError):
            build_purchase_request_audit_event("unknown_action", "buyer-1", "pr-1")


if __name__ == "__main__":
    unittest.main(verbosity=2)
