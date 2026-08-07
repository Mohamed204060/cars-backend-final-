"""
test_pct_service.py — اختبارات وحدة لخدمة كتالوج قطع الغيار
تُشغَّل عبر: python3 -m unittest discover -s tests -v
"""

import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from pct_service import (  # noqa: E402
    CatalogPart, propose_catalog_part, transition_part_status, is_approved,
    add_localized_name, add_oem_number,
    InvalidCatalogPartStatusError, DuplicateOemNumberError,
)


class TestPartLifecycle(unittest.TestCase):
    """REQ-PCT-001, 002"""

    def test_propose_creates_part_with_proposed_status(self):
        part = propose_catalog_part(category_id="cat-1")
        self.assertEqual(part.status, "proposed")
        self.assertFalse(is_approved(part))

    def test_proposed_to_approved_allowed(self):
        part = propose_catalog_part(category_id="cat-1")
        transition_part_status(part, "approved")
        self.assertTrue(is_approved(part))

    def test_proposed_to_archived_allowed(self):
        part = propose_catalog_part(category_id="cat-1")
        transition_part_status(part, "archived")
        self.assertEqual(part.status, "archived")

    def test_approved_to_archived_allowed(self):
        part = propose_catalog_part(category_id="cat-1")
        transition_part_status(part, "approved")
        transition_part_status(part, "archived")
        self.assertEqual(part.status, "archived")

    def test_archived_is_terminal(self):
        part = propose_catalog_part(category_id="cat-1")
        transition_part_status(part, "archived")
        with self.assertRaises(InvalidCatalogPartStatusError):
            transition_part_status(part, "approved")

    def test_approved_cannot_go_back_to_proposed(self):
        part = propose_catalog_part(category_id="cat-1")
        transition_part_status(part, "approved")
        with self.assertRaises(InvalidCatalogPartStatusError):
            transition_part_status(part, "proposed")

    def test_unknown_status_rejected(self):
        part = propose_catalog_part(category_id="cat-1")
        with self.assertRaises(ValueError):
            transition_part_status(part, "rejected")


class TestLocalizedNames(unittest.TestCase):
    """REQ-PCT-003"""

    def setUp(self):
        self.part = propose_catalog_part(category_id="cat-1")
        self.part.id = "part-1"

    def test_add_canonical_name(self):
        name = add_localized_name(self.part, "Brake Pad Set", "canonical")
        self.assertEqual(name.name_kind, "canonical")

    def test_add_synonym_name(self):
        name = add_localized_name(self.part, "تيل فرامل", "synonym", locale="ar")
        self.assertEqual(name.locale, "ar")

    def test_unknown_name_kind_rejected(self):
        with self.assertRaises(ValueError):
            add_localized_name(self.part, "Something", "nickname")

    def test_empty_name_value_rejected(self):
        with self.assertRaises(ValueError):
            add_localized_name(self.part, "   ", "canonical")


class TestOemNumbers(unittest.TestCase):
    """REQ-PCT-004, 005"""

    def setUp(self):
        self.part = propose_catalog_part(category_id="cat-1")
        self.part.id = "part-1"

    def test_add_oem_number_success(self):
        oem = add_oem_number(self.part, "manufacturer-1", "12345-ABC", existing_oem_numbers=[])
        self.assertEqual(oem.oem_number, "12345-ABC")

    def test_duplicate_oem_for_same_manufacturer_rejected(self):
        existing = [add_oem_number(self.part, "manufacturer-1", "12345-ABC", [])]
        with self.assertRaises(DuplicateOemNumberError):
            add_oem_number(self.part, "manufacturer-1", "12345-ABC", existing)

    def test_same_oem_number_allowed_for_different_manufacturer(self):
        existing = [add_oem_number(self.part, "manufacturer-1", "12345-ABC", [])]
        # نفس الرقم لكن لشركة مصنّعة مختلفة تمامًا: مسموح
        oem2 = add_oem_number(self.part, "manufacturer-2", "12345-ABC", existing)
        self.assertEqual(oem2.manufacturer_ref_id, "manufacturer-2")

    def test_duplicate_check_case_insensitive(self):
        existing = [add_oem_number(self.part, "manufacturer-1", "ABC-123", [])]
        with self.assertRaises(DuplicateOemNumberError):
            add_oem_number(self.part, "manufacturer-1", "abc-123", existing)


if __name__ == "__main__":
    unittest.main(verbosity=2)
