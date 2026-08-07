"""
test_vct_service.py — اختبارات وحدة لخدمة كتالوج السيارات
تُشغَّل عبر: python3 -m unittest discover -s tests -v
"""

import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from vct_service import (  # noqa: E402
    propose_manufacturer, transition_manufacturer_status, is_manufacturer_approved,
    propose_model, transition_model_status, is_model_approved,
    create_generation, create_trim, is_trim_valid_for_compatibility,
    InvalidVctStatusError,
)


class TestManufacturerLifecycle(unittest.TestCase):
    """REQ-VCT-001, 002"""

    def test_propose_creates_proposed_status(self):
        m = propose_manufacturer()
        self.assertFalse(is_manufacturer_approved(m))

    def test_proposed_to_approved_allowed(self):
        m = propose_manufacturer()
        transition_manufacturer_status(m, "approved")
        self.assertTrue(is_manufacturer_approved(m))

    def test_archived_is_terminal(self):
        m = propose_manufacturer()
        transition_manufacturer_status(m, "archived")
        with self.assertRaises(InvalidVctStatusError):
            transition_manufacturer_status(m, "approved")

    def test_unknown_status_rejected(self):
        m = propose_manufacturer()
        with self.assertRaises(ValueError):
            transition_manufacturer_status(m, "banned")


class TestModelLifecycle(unittest.TestCase):
    """REQ-VCT-002"""

    def test_propose_model_linked_to_manufacturer(self):
        model = propose_model(manufacturer_id="manu-1")
        self.assertEqual(model.manufacturer_id, "manu-1")
        self.assertFalse(is_model_approved(model))

    def test_model_approval_lifecycle(self):
        model = propose_model(manufacturer_id="manu-1")
        transition_model_status(model, "approved")
        self.assertTrue(is_model_approved(model))

    def test_model_archived_then_approved_rejected(self):
        model = propose_model(manufacturer_id="manu-1")
        transition_model_status(model, "archived")
        with self.assertRaises(InvalidVctStatusError):
            transition_model_status(model, "approved")


class TestGenerationAndTrim(unittest.TestCase):
    """REQ-VCT-003, 004"""

    def test_create_generation_linked_to_model(self):
        gen = create_generation(model_id="model-1")
        self.assertEqual(gen.model_id, "model-1")

    def test_create_trim_requires_fuel_and_transmission(self):
        with self.assertRaises(ValueError):
            create_trim(generation_id="gen-1", fuel_type_ref_id="", transmission_type_ref_id="auto")

    def test_create_trim_success(self):
        trim = create_trim(generation_id="gen-1", fuel_type_ref_id="petrol", transmission_type_ref_id="automatic")
        self.assertEqual(trim.generation_id, "gen-1")


class TestTrimValidityCheck(unittest.TestCase):
    """نقطة التحقق المرجعية المستهلَكة من CMP"""

    def test_existing_trim_is_valid(self):
        trim = create_trim(generation_id="gen-1", fuel_type_ref_id="petrol", transmission_type_ref_id="automatic")
        self.assertTrue(is_trim_valid_for_compatibility(trim))

    def test_none_trim_is_invalid(self):
        self.assertFalse(is_trim_valid_for_compatibility(None))


if __name__ == "__main__":
    unittest.main(verbosity=2)
