"""
test_trm_repository.py — اختبارات Repository لخدمة TRM
تُشغَّل عبر: python3 -m unittest discover -s tests -v
"""

import sys
import os
import unittest
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from trm_service import (  # noqa: E402
    create_rating_via_repository, archive_rating_via_repository, get_average_score_via_repository,
    DuplicateRatingError,
)
from trm_repository import InMemoryTrmRepository  # noqa: E402


def always_eligible(user_ref_id, target_type, purchase_request_ref_id):
    return True


class TestTrmRepositoryOrchestration(unittest.TestCase):

    def setUp(self):
        self.repo = InMemoryTrmRepository()

    def test_create_rating_via_repository_assigns_id(self):
        rating = create_rating_via_repository(self.repo, "buyer-1", "seller", "seller-1", "pr-1", 5, always_eligible)
        self.assertTrue(rating.id.startswith("rating-"))

    def test_duplicate_via_repository_rejected(self):
        create_rating_via_repository(self.repo, "buyer-1", "seller", "seller-1", "pr-1", 5, always_eligible)
        with self.assertRaises(DuplicateRatingError):
            create_rating_via_repository(self.repo, "buyer-1", "seller", "seller-1", "pr-1", 3, always_eligible)

    def test_archive_via_repository_persists(self):
        rating = create_rating_via_repository(self.repo, "buyer-1", "seller", "seller-1", "pr-1", 5, always_eligible)
        archive_rating_via_repository(self.repo, rating.id)
        fetched = self.repo.get_rating_by_id(rating.id)
        self.assertEqual(fetched.status, "archived")

    def test_average_score_via_repository(self):
        create_rating_via_repository(self.repo, "buyer-1", "seller", "seller-1", "pr-1", 5, always_eligible)
        create_rating_via_repository(self.repo, "buyer-2", "seller", "seller-1", "pr-2", 3, always_eligible)
        avg = get_average_score_via_repository(self.repo, "seller", "seller-1")
        self.assertEqual(avg, 4.0)


class TestConcurrentRatingCreation(unittest.TestCase):
    """
    اختبار تزامن حقيقي (بنفس منهجية AuthRepository وNTF): طلبان متزامنان
    يحاولان إنشاء تقييم لنفس (المُقيِّم، الهدف، الصفقة المصدر). يجب أن ينجح
    واحد فقط، دون أي سجل مكرَّر في المستودع.
    """

    def test_two_concurrent_requests_only_one_succeeds(self):
        repo = InMemoryTrmRepository()
        results = {"success": 0, "failure": 0}
        lock = threading.Lock()

        def attempt(score):
            try:
                create_rating_via_repository(repo, "buyer-1", "seller", "seller-1", "pr-1", score, always_eligible)
                with lock:
                    results["success"] += 1
            except DuplicateRatingError:
                with lock:
                    results["failure"] += 1

        t1 = threading.Thread(target=attempt, args=(5,))
        t2 = threading.Thread(target=attempt, args=(3,))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        self.assertEqual(results["success"], 1)
        self.assertEqual(results["failure"], 1)

        all_for_pair = repo.get_ratings_for_source_purchase_request("pr-1")
        self.assertEqual(len(all_for_pair), 1)  # لا سجل مكرَّر فعليًا في المستودع


if __name__ == "__main__":
    unittest.main(verbosity=2)
