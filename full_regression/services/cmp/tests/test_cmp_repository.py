"""
test_cmp_repository.py — اختبار وحدة لتنسيق خدمة التوافق عبر Repository
تُشغَّل عبر: python3 -m unittest discover -s tests -v
"""

import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from cmp_service import create_compatibility_record_via_repository, DuplicateCompatibilityRecordError  # noqa: E402
from cmp_repository import InMemoryCmpRepository  # noqa: E402


def always_true(_):
    return True


class TestCmpRepositoryOrchestration(unittest.TestCase):

    def setUp(self):
        self.repo = InMemoryCmpRepository()

    def test_create_via_repository_assigns_id_and_persists(self):
        record = create_compatibility_record_via_repository(
            self.repo, "part-1", "trim-1", always_true, always_true
        )
        self.assertTrue(record.id.startswith("cmp-"))
        self.assertEqual(len(self.repo.get_records_for_part("part-1")), 1)

    def test_duplicate_via_repository_rejected(self):
        create_compatibility_record_via_repository(self.repo, "part-1", "trim-1", always_true, always_true)
        with self.assertRaises(DuplicateCompatibilityRecordError):
            create_compatibility_record_via_repository(self.repo, "part-1", "trim-1", always_true, always_true)


if __name__ == "__main__":
    unittest.main(verbosity=2)
