"""
test_order_repository.py — اختبارات وحدة لتنسيق خدمة الطلبات عبر Repository
تُشغَّل عبر: python3 -m unittest discover -s tests -v
"""

import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from order_service import (  # noqa: E402
    create_purchase_request_via_repository, submit_offer_via_repository,
    accept_offer_via_repository, withdraw_offer_via_repository,
    PurchaseRequestClosedError, OfferNotWithdrawableError,
)
from order_repository import InMemoryOrderRepository  # noqa: E402


class TestOrderRepositoryOrchestration(unittest.TestCase):

    def setUp(self):
        self.repo = InMemoryOrderRepository()

    def test_create_pr_via_repository_assigns_id(self):
        pr = create_purchase_request_via_repository(self.repo, "buyer-1", "part-1", "trim-1")
        self.assertTrue(pr.id.startswith("pr-"))
        self.assertEqual(pr.status, "open")

    def test_submit_offer_via_repository_persists_and_updates_pr(self):
        pr = create_purchase_request_via_repository(self.repo, "buyer-1", "part-1", "trim-1")
        offer = submit_offer_via_repository(self.repo, pr.id, "store-1", 100, "SAR", provides_shipping=False)

        fetched_pr = self.repo.get_purchase_request_by_id(pr.id)
        self.assertEqual(fetched_pr.status, "under_review")
        self.assertTrue(fetched_pr.has_received_offer)
        self.assertTrue(offer.id.startswith("offer-"))

    def test_accept_offer_via_repository_rejects_others_correctly(self):
        """
        هذا الاختبار تحديدًا يغطي الخلل الحقيقي الذي اكتُشف واصلح في منطق
        الأعمال (المقارنة بمعرّف فارغ بدل الهوية الكائنية): يتأكد أن الرفض
        التلقائي يعمل بصورة صحيحة عبر طبقة Repository حيث تصبح المعرّفات
        الفعلية غير فارغة بعد الإسناد.
        """
        pr = create_purchase_request_via_repository(self.repo, "buyer-1", "part-1", "trim-1")
        offer1 = submit_offer_via_repository(self.repo, pr.id, "store-1", 100, "SAR", provides_shipping=False)
        offer2 = submit_offer_via_repository(self.repo, pr.id, "store-2", 90, "SAR", provides_shipping=True)
        offer3 = submit_offer_via_repository(self.repo, pr.id, "store-3", 95, "SAR", provides_shipping=False)

        accept_offer_via_repository(self.repo, pr.id, offer2.id)

        all_offers = {o.id: o for o in self.repo.get_offers_for_purchase_request(pr.id)}
        self.assertEqual(all_offers[offer2.id].status, "accepted")
        self.assertEqual(all_offers[offer1.id].status, "rejected")
        self.assertEqual(all_offers[offer3.id].status, "rejected")

        fetched_pr = self.repo.get_purchase_request_by_id(pr.id)
        self.assertEqual(fetched_pr.status, "fulfilled")

    def test_no_offers_after_fulfilled_via_repository(self):
        pr = create_purchase_request_via_repository(self.repo, "buyer-1", "part-1", "trim-1")
        offer1 = submit_offer_via_repository(self.repo, pr.id, "store-1", 100, "SAR", provides_shipping=False)
        accept_offer_via_repository(self.repo, pr.id, offer1.id)

        with self.assertRaises(PurchaseRequestClosedError):
            submit_offer_via_repository(self.repo, pr.id, "store-4", 80, "SAR", provides_shipping=False)

    def test_withdraw_offer_via_repository(self):
        pr = create_purchase_request_via_repository(self.repo, "buyer-1", "part-1", "trim-1")
        offer = submit_offer_via_repository(self.repo, pr.id, "store-1", 100, "SAR", provides_shipping=False)

        withdraw_offer_via_repository(self.repo, offer.id)

        fetched = self.repo.get_offer_by_id(offer.id)
        self.assertEqual(fetched.status, "withdrawn")

    def test_cannot_withdraw_accepted_offer_via_repository(self):
        pr = create_purchase_request_via_repository(self.repo, "buyer-1", "part-1", "trim-1")
        offer = submit_offer_via_repository(self.repo, pr.id, "store-1", 100, "SAR", provides_shipping=False)
        accept_offer_via_repository(self.repo, pr.id, offer.id)

        with self.assertRaises(OfferNotWithdrawableError):
            withdraw_offer_via_repository(self.repo, offer.id)


if __name__ == "__main__":
    unittest.main(verbosity=2)
