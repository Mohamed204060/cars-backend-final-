"""
test_vct_repository.py — اختبارات وحدة لتنسيق خدمة كتالوج السيارات عبر Repository
تُشغَّل عبر: python3 -m unittest discover -s tests -v
"""

import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from vct_service import (  # noqa: E402
    propose_manufacturer_via_repository, approve_manufacturer_via_repository,
    create_full_trim_via_repository, is_manufacturer_approved,
)
from vct_repository import InMemoryVctRepository  # noqa: E402


class TestVctRepositoryOrchestration(unittest.TestCase):

    def setUp(self):
        self.repo = InMemoryVctRepository()

    def test_propose_and_approve_manufacturer(self):
        manufacturer = propose_manufacturer_via_repository(self.repo)
        self.assertFalse(is_manufacturer_approved(manufacturer))

        approve_manufacturer_via_repository(self.repo, manufacturer.id)
        fetched = self.repo.get_manufacturer_by_id(manufacturer.id)
        self.assertTrue(is_manufacturer_approved(fetched))

    def test_create_full_trim_chain(self):
        manufacturer = propose_manufacturer_via_repository(self.repo)
        trim = create_full_trim_via_repository(self.repo, manufacturer.id, "petrol", "automatic")

        self.assertTrue(trim.id.startswith("trim-"))
        self.assertTrue(self.repo.is_trim_valid(trim.id))

    def test_is_trim_valid_false_for_unknown_trim(self):
        self.assertFalse(self.repo.is_trim_valid("nonexistent"))

    def test_approve_unknown_manufacturer_raises(self):
        with self.assertRaises(ValueError):
            approve_manufacturer_via_repository(self.repo, "nonexistent")


if __name__ == "__main__":
    unittest.main(verbosity=2)
