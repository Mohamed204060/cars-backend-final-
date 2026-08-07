"""
test_trm_service.py — اختبارات وحدة لخدمة الثقة والتقييمات (TRM)
تُشغَّل عبر: python3 -m unittest discover -s tests -v
"""

import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from trm_service import (  # noqa: E402
    Rating, create_rating, update_rating_score_and_comment, archive_rating,
    compute_average_score, build_rating_report_event, build_administrative_audit_event,
    InvalidTargetTypeError, InvalidScoreError, DuplicateRatingError,
    RatingIneligibleError, RatingArchivedImmutableError,
)


def always_eligible(user_ref_id, target_type, purchase_request_ref_id):
    return True


def never_eligible(user_ref_id, target_type, purchase_request_ref_id):
    return False


class TestCreateRatingBasics(unittest.TestCase):

    def test_create_seller_rating_success(self):
        rating = create_rating("buyer-1", "seller", "seller-user-1", "pr-1", 5, [], always_eligible)
        self.assertEqual(rating.target_type, "seller")
        self.assertEqual(rating.status, "active")

    def test_create_store_rating_success(self):
        rating = create_rating("buyer-1", "store", "store-1", "pr-1", 4, [], always_eligible)
        self.assertEqual(rating.target_type, "store")

    def test_create_purchase_experience_rating_success(self):
        rating = create_rating("buyer-1", "purchase_experience", "pr-1", "pr-1", 3, [], always_eligible)
        self.assertEqual(rating.target_type, "purchase_experience")

    def test_invalid_target_type_rejected(self):
        with self.assertRaises(InvalidTargetTypeError):
            create_rating("buyer-1", "conversation", "x-1", "pr-1", 5, [], always_eligible)

    def test_score_out_of_range_rejected(self):
        with self.assertRaises(InvalidScoreError):
            create_rating("buyer-1", "seller", "seller-1", "pr-1", 6, [], always_eligible)

    def test_score_zero_rejected(self):
        with self.assertRaises(InvalidScoreError):
            create_rating("buyer-1", "seller", "seller-1", "pr-1", 0, [], always_eligible)


class TestEligibilityViaDependencyInjection(unittest.TestCase):
    """يثبت أن التحقق من الأهلية يتم حصرًا عبر الدالة المحقونة، لا استيرادًا مباشرًا."""

    def test_ineligible_user_rejected(self):
        with self.assertRaises(RatingIneligibleError):
            create_rating("buyer-1", "seller", "seller-1", "pr-1", 5, [], never_eligible)

    def test_eligibility_checker_receives_correct_arguments(self):
        received_args = []

        def spy_checker(user_ref_id, target_type, purchase_request_ref_id):
            received_args.append((user_ref_id, target_type, purchase_request_ref_id))
            return True

        create_rating("buyer-1", "seller", "seller-1", "pr-42", 5, [], spy_checker)
        self.assertEqual(received_args, [("buyer-1", "seller", "pr-42")])


class TestDuplicatePrevention(unittest.TestCase):
    """منع التقييم المكرَّر لنفس الهدف من نفس المستخدم عن نفس الصفقة المصدر فقط."""

    def test_duplicate_same_target_same_source_rejected(self):
        existing = [create_rating("buyer-1", "seller", "seller-1", "pr-1", 5, [], always_eligible)]
        with self.assertRaises(DuplicateRatingError):
            create_rating("buyer-1", "seller", "seller-1", "pr-1", 3, existing, always_eligible)

    def test_same_target_different_source_allowed(self):
        # نفس البائع لكن عن صفقة مختلفة تمامًا: مسموح
        existing = [create_rating("buyer-1", "seller", "seller-1", "pr-1", 5, [], always_eligible)]
        rating2 = create_rating("buyer-1", "seller", "seller-1", "pr-2", 4, existing, always_eligible)
        self.assertEqual(rating2.source_purchase_request_ref_id, "pr-2")

    def test_different_target_type_same_source_allowed(self):
        # نفس الصفقة، لكن تقييم البائع والمتجر وتجربة الشراء ثلاثتها مستقلة
        existing = [create_rating("buyer-1", "seller", "seller-1", "pr-1", 5, [], always_eligible)]
        store_rating = create_rating("buyer-1", "store", "store-1", "pr-1", 4, existing, always_eligible)
        self.assertEqual(store_rating.target_type, "store")

    def test_different_rater_same_target_same_source_allowed(self):
        # هذا غير واقعي عمليًا (صفقة واحدة لمشترٍ واحد) لكنه يثبت أن القيد مرتبط بالمُقيِّم لا الهدف وحده
        existing = [create_rating("buyer-1", "seller", "seller-1", "pr-1", 5, [], always_eligible)]
        rating2 = create_rating("buyer-2", "seller", "seller-1", "pr-1", 3, existing, always_eligible)
        self.assertEqual(rating2.rated_by_user_ref_id, "buyer-2")


class TestRatingModificationAndArchival(unittest.TestCase):
    """لا حذف فعلي؛ الأرشفة فقط."""

    def test_update_score_and_comment(self):
        rating = create_rating("buyer-1", "seller", "seller-1", "pr-1", 3, [], always_eligible)
        update_rating_score_and_comment(rating, 5, "تحسَّنت التجربة لاحقًا")
        self.assertEqual(rating.score, 5)
        self.assertEqual(rating.comment, "تحسَّنت التجربة لاحقًا")

    def test_archive_rating(self):
        rating = create_rating("buyer-1", "seller", "seller-1", "pr-1", 3, [], always_eligible)
        archive_rating(rating)
        self.assertEqual(rating.status, "archived")

    def test_cannot_update_archived_rating(self):
        rating = create_rating("buyer-1", "seller", "seller-1", "pr-1", 3, [], always_eligible)
        archive_rating(rating)
        with self.assertRaises(RatingArchivedImmutableError):
            update_rating_score_and_comment(rating, 5)

    def test_cannot_re_archive(self):
        rating = create_rating("buyer-1", "seller", "seller-1", "pr-1", 3, [], always_eligible)
        archive_rating(rating)
        with self.assertRaises(RatingArchivedImmutableError):
            archive_rating(rating)

    def test_archived_rating_object_still_intact(self):
        # يثبت أن الأرشفة لا تحذف أي بيانات فعليًا من الكائن نفسه
        rating = create_rating("buyer-1", "seller", "seller-1", "pr-1", 3, [], always_eligible, comment="جيد")
        archive_rating(rating)
        self.assertEqual(rating.score, 3)
        self.assertEqual(rating.comment, "جيد")


class TestAverageScoreComputation(unittest.TestCase):

    def test_average_of_active_ratings_only(self):
        r1 = create_rating("buyer-1", "seller", "seller-1", "pr-1", 5, [], always_eligible)
        r2 = create_rating("buyer-2", "seller", "seller-1", "pr-2", 3, [], always_eligible)
        r3 = create_rating("buyer-3", "seller", "seller-1", "pr-3", 1, [], always_eligible)
        archive_rating(r3)  # يُستبعَد من المتوسط

        avg = compute_average_score([r1, r2, r3])
        self.assertEqual(avg, 4.0)  # (5+3)/2، لا (5+3+1)/3

    def test_average_with_no_active_ratings_is_none(self):
        r1 = create_rating("buyer-1", "seller", "seller-1", "pr-1", 5, [], always_eligible)
        archive_rating(r1)
        self.assertIsNone(compute_average_score([r1]))

    def test_average_with_no_ratings_at_all_is_none(self):
        self.assertIsNone(compute_average_score([]))


class TestReportingAndAudit(unittest.TestCase):

    def test_build_report_event(self):
        event = build_rating_report_event("rating-1", "user-2", "محتوى مسيء")
        self.assertEqual(event["report_target_ref_id"], "rating-1")

    def test_empty_report_reason_rejected(self):
        with self.assertRaises(ValueError):
            build_rating_report_event("rating-1", "user-2", "   ")

    def test_build_audit_event_for_known_action(self):
        event = build_administrative_audit_event("rating_created", "buyer-1", "rating-1")
        self.assertEqual(event["event_name"], "rating_created")

    def test_build_audit_event_rejects_unknown_action(self):
        with self.assertRaises(ValueError):
            build_administrative_audit_event("unknown_action", "buyer-1", "rating-1")


if __name__ == "__main__":
    unittest.main(verbosity=2)
