"""
test_pur_distribution_service.py — اختبارات وحدة وتكامل حقيقي لتوسعة PUR (CR-009)
تُشغَّل عبر: python3 -m unittest discover -s tests -v

يتضمَّن اختبار تكامل حقيقي يستدعي scheduler_service الفعلية (لا محاكاة)
لإثبات أن PUR تستخدم المُجدوِل المشترك فعليًا عبر DI، بنفس أسلوب اختبار
CMP↔PCT↔VCT السابق.
"""

import sys
import os
import unittest
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "svc_scheduler", "src"))

from pur_distribution_service import (  # noqa: E402
    SellerProfile, DistributionCriteria,
    is_seller_eligible_for_distribution, distribute_purchase_request,
    schedule_purchase_request_expiration, execute_expiration_check,
    send_seller_reminder, send_buyer_reminder,
    InvalidDistributionCriteriaError,
)
from order_service import create_purchase_request, transition_purchase_request_status  # noqa: E402

# استيراد المُجدوِل المشترك الفعلي (لا Mock) لإثبات التكامل الحقيقي
from scheduler_service import schedule_job_via_repository, process_due_jobs_via_repository  # noqa: E402
from scheduler_repository import InMemorySchedulerRepository  # noqa: E402


def make_seller(store_ref_id, city="riyadh", specialties=None, activity="active",
                 status="approved", ready=True):
    return SellerProfile(store_ref_id=store_ref_id, city_ref_id=city,
                          specialty_ref_ids=specialties or ["engine_parts"],
                          activity_type=activity, status=status, is_ready_to_receive=ready)


class TestRuleBasedDistribution(unittest.TestCase):
    """REQ-PUR-019: قواعد صريحة فقط، بلا ذكاء اصطناعي."""

    def test_matching_seller_is_eligible(self):
        seller = make_seller("store-1")
        criteria = DistributionCriteria(city_ref_id="riyadh", specialty_ref_id="engine_parts")
        self.assertTrue(is_seller_eligible_for_distribution(seller, criteria))

    def test_city_mismatch_rejected(self):
        seller = make_seller("store-1", city="jeddah")
        criteria = DistributionCriteria(city_ref_id="riyadh", specialty_ref_id="engine_parts")
        self.assertFalse(is_seller_eligible_for_distribution(seller, criteria))

    def test_specialty_mismatch_rejected(self):
        seller = make_seller("store-1", specialties=["brake_parts"])
        criteria = DistributionCriteria(city_ref_id="riyadh", specialty_ref_id="engine_parts")
        self.assertFalse(is_seller_eligible_for_distribution(seller, criteria))

    def test_inactive_seller_rejected(self):
        seller = make_seller("store-1", activity="inactive")
        criteria = DistributionCriteria(city_ref_id="riyadh", specialty_ref_id="engine_parts")
        self.assertFalse(is_seller_eligible_for_distribution(seller, criteria))

    def test_unapproved_status_rejected(self):
        seller = make_seller("store-1", status="pending")
        criteria = DistributionCriteria(city_ref_id="riyadh", specialty_ref_id="engine_parts")
        self.assertFalse(is_seller_eligible_for_distribution(seller, criteria))

    def test_not_ready_seller_rejected(self):
        seller = make_seller("store-1", ready=False)
        criteria = DistributionCriteria(city_ref_id="riyadh", specialty_ref_id="engine_parts")
        self.assertFalse(is_seller_eligible_for_distribution(seller, criteria))

    def test_distribute_returns_only_eligible_stores(self):
        sellers = [
            make_seller("store-1"),                                    # مؤهَّل
            make_seller("store-2", city="jeddah"),                     # مدينة مختلفة
            make_seller("store-3", specialties=["brake_parts"]),       # تخصص مختلف
            make_seller("store-4", activity="inactive"),               # غير نشط
        ]
        criteria = DistributionCriteria(city_ref_id="riyadh", specialty_ref_id="engine_parts")
        result = distribute_purchase_request(criteria, sellers)
        self.assertEqual(result, ["store-1"])

    def test_invalid_criteria_raises(self):
        with self.assertRaises(InvalidDistributionCriteriaError):
            distribute_purchase_request(DistributionCriteria(city_ref_id="", specialty_ref_id=""), [])


class TestRealSchedulerIntegration(unittest.TestCase):
    """
    اختبار تكامل حقيقي فعلي: يستخدم scheduler_service.py الحقيقية (لا Mock)
    لإثبات أن PUR تعتمد فعليًا على المُجدوِل المشترك عبر DI، لا آلية داخلية
    خاصة بها.
    """

    def setUp(self):
        self.scheduler_repo = InMemorySchedulerRepository()

    def test_schedule_expiration_uses_real_shared_scheduler(self):
        def schedule_fn(job_type, target_ref_id, scheduled_at):
            return schedule_job_via_repository(self.scheduler_repo, job_type, target_ref_id, scheduled_at)

        job = schedule_purchase_request_expiration("pr-1", datetime(2026, 2, 1), schedule_fn)
        self.assertEqual(job.job_type, "pur_expiration_check")
        self.assertEqual(job.target_ref_id, "pr-1")
        self.assertTrue(job.id.startswith("job-"))  # معرّف فعلي صادر من المُجدوِل الحقيقي

    def test_end_to_end_expiration_via_real_scheduler_cycle(self):
        pr = create_purchase_request("buyer-1", "part-1", "trim-1")
        pr.id = "pr-real-1"
        transition_purchase_request_status(pr, "under_review")

        def schedule_fn(job_type, target_ref_id, scheduled_at):
            return schedule_job_via_repository(self.scheduler_repo, job_type, target_ref_id, scheduled_at)
        schedule_purchase_request_expiration(pr.id, datetime(2026, 1, 1), schedule_fn)

        def pur_executor(job):
            # PUR توفِّر منطق التنفيذ الخاص بها؛ المُجدوِل لا يعرف محتواه
            return execute_expiration_check(pr, transition_purchase_request_status)

        # المُجدوِل الحقيقي (لا محاكاة) ينفِّذ الدورة فعليًا
        results = process_due_jobs_via_repository(self.scheduler_repo, current_time=datetime(2026, 1, 2),
                                                     execute_job_fn=pur_executor)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].status, "completed")
        self.assertEqual(pr.status, "expired")  # الأثر الفعلي على طلب الشراء تحقَّق فعليًا


class TestReminders(unittest.TestCase):
    """REQ-PUR-021: PUR تبادر عبر DI، لا اشتراك أحداث."""

    def test_send_seller_reminder_calls_injected_function_with_correct_context(self):
        calls = []

        def fake_ntf_create_notification(target_ref_id, notification_type, context):
            calls.append((target_ref_id, notification_type, context))

        send_seller_reminder("pr-1", "store-1", fake_ntf_create_notification)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "store-1")
        self.assertEqual(calls[0][1], "pur_seller_reminder")
        self.assertEqual(calls[0][2]["purchase_request_id"], "pr-1")

    def test_send_buyer_reminder_calls_injected_function(self):
        calls = []

        def fake_ntf_create_notification(target_ref_id, notification_type, context):
            calls.append((target_ref_id, notification_type))

        send_buyer_reminder("pr-1", "buyer-1", fake_ntf_create_notification)
        self.assertEqual(calls, [("buyer-1", "pur_buyer_reminder")])


if __name__ == "__main__":
    unittest.main(verbosity=2)
