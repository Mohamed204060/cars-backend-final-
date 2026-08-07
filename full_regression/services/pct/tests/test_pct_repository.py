"""
test_pct_repository.py — اختبارات وحدة لتنسيق خدمة الكتالوج عبر Repository
تُشغَّل عبر: python3 -m unittest discover -s tests -v
"""

import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from pct_service import (  # noqa: E402
    propose_catalog_part_via_repository, approve_catalog_part_via_repository,
    add_oem_number_via_repository, is_approved, DuplicateOemNumberError,
)
from pct_repository import InMemoryPctRepository  # noqa: E402


class TestPctRepositoryOrchestration(unittest.TestCase):

    def setUp(self):
        self.repo = InMemoryPctRepository()

    def test_propose_via_repository_assigns_id(self):
        part = propose_catalog_part_via_repository(self.repo, category_id="cat-1")
        self.assertTrue(part.id.startswith("part-"))
        self.assertFalse(is_approved(part))

    def test_approve_via_repository_persists_status(self):
        part = propose_catalog_part_via_repository(self.repo, category_id="cat-1")
        approve_catalog_part_via_repository(self.repo, part.id)

        fetched = self.repo.get_part_by_id(part.id)
        self.assertTrue(is_approved(fetched))

    def test_is_part_approved_reflects_true_state(self):
        part = propose_catalog_part_via_repository(self.repo, category_id="cat-1")
        self.assertFalse(self.repo.is_part_approved(part.id))
        approve_catalog_part_via_repository(self.repo, part.id)
        self.assertTrue(self.repo.is_part_approved(part.id))

    def test_add_oem_number_via_repository_checks_cross_part_duplicates(self):
        part1 = propose_catalog_part_via_repository(self.repo, category_id="cat-1")
        part2 = propose_catalog_part_via_repository(self.repo, category_id="cat-1")
        add_oem_number_via_repository(self.repo, part1.id, "manufacturer-1", "OEM-999")

        with self.assertRaises(DuplicateOemNumberError):
            add_oem_number_via_repository(self.repo, part2.id, "manufacturer-1", "OEM-999")

    def test_approve_unknown_part_raises(self):
        with self.assertRaises(ValueError):
            approve_catalog_part_via_repository(self.repo, "nonexistent")


class TestSsotIntegrationWithInventoryService(unittest.TestCase):
    """
    مبدأ SSOT المعتمَد للتو: لا يجوز إنشاء/اعتماد عنصر مخزون إلا لقطعة معتمدة،
    عبر تكامل صريح مع is_part_approved من PCT، لا استعلامًا مباشرًا لبيانات PCT
    من داخل خدمة المخزون.
    """

    def setUp(self):
        self.pct_repo = InMemoryPctRepository()

    def test_approved_part_passes_the_shared_checker(self):
        part = propose_catalog_part_via_repository(self.pct_repo, category_id="cat-1")
        approve_catalog_part_via_repository(self.pct_repo, part.id)
        # هذا هو الاستدعاء الذي ستحقنه خدمة المخزون كـ catalog_part_checker
        self.assertTrue(self.pct_repo.is_part_approved(part.id))

    def test_proposed_unapproved_part_fails_the_shared_checker(self):
        part = propose_catalog_part_via_repository(self.pct_repo, category_id="cat-1")
        self.assertFalse(self.pct_repo.is_part_approved(part.id))


if __name__ == "__main__":
    unittest.main(verbosity=2)
