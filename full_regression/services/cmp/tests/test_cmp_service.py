"""
test_cmp_service.py — اختبارات وحدة لخدمة التوافق (Part Fitment)
تُشغَّل عبر: python3 -m unittest discover -s tests -v

تتضمن هذه الوحدة اختبار تكامل حقيقي عبر ثلاث خدمات فعليًا (لا محاكاة نصية):
CMP تستهلك PCT وVCT الحقيقيتين عبر دوال محقونة، بلا أي استعلام مباشر.
"""

import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "svc_pct", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "svc_vct", "src"))

from cmp_service import (  # noqa: E402
    create_compatibility_record, transition_compatibility_status,
    PartNotApprovedForCompatibilityError, TrimNotValidForCompatibilityError,
    DuplicateCompatibilityRecordError, InvalidCompatibilityStatusError,
)

# استيراد خدمتَي PCT وVCT الفعليتين لاختبار تكامل حقيقي، لا Mock مصطنع بالكامل
from pct_service import propose_catalog_part_via_repository, approve_catalog_part_via_repository  # noqa: E402
from pct_repository import InMemoryPctRepository  # noqa: E402
from vct_service import propose_manufacturer_via_repository, create_full_trim_via_repository  # noqa: E402
from vct_repository import InMemoryVctRepository  # noqa: E402


def always_true(_):
    return True


def always_false(_):
    return False


class TestCompatibilityCreationWithSimpleCheckers(unittest.TestCase):
    """اختبارات منطقية بحتة بدوال تحقق مبسَّطة (بلا استدعاء PCT/VCT فعليًا)"""

    def test_create_record_success(self):
        record = create_compatibility_record(
            "part-1", "trim-1", existing_records_for_pair=[],
            is_part_approved_checker=always_true, is_trim_valid_checker=always_true,
        )
        self.assertEqual(record.catalog_part_ref_id, "part-1")
        self.assertEqual(record.status, "active")

    def test_unapproved_part_rejected(self):
        with self.assertRaises(PartNotApprovedForCompatibilityError):
            create_compatibility_record(
                "part-1", "trim-1", existing_records_for_pair=[],
                is_part_approved_checker=always_false, is_trim_valid_checker=always_true,
            )

    def test_invalid_trim_rejected(self):
        with self.assertRaises(TrimNotValidForCompatibilityError):
            create_compatibility_record(
                "part-1", "trim-1", existing_records_for_pair=[],
                is_part_approved_checker=always_true, is_trim_valid_checker=always_false,
            )

    def test_duplicate_pair_rejected(self):
        existing = [create_compatibility_record(
            "part-1", "trim-1", [], always_true, always_true,
        )]
        with self.assertRaises(DuplicateCompatibilityRecordError):
            create_compatibility_record(
                "part-1", "trim-1", existing_records_for_pair=existing,
                is_part_approved_checker=always_true, is_trim_valid_checker=always_true,
            )

    def test_same_part_different_trim_allowed(self):
        # REQ-CMP: نفس القطعة يمكن أن تتوافق مع أكثر من فئة سيارة (N:M)
        existing = [create_compatibility_record("part-1", "trim-1", [], always_true, always_true)]
        record2 = create_compatibility_record(
            "part-1", "trim-2", existing_records_for_pair=existing,
            is_part_approved_checker=always_true, is_trim_valid_checker=always_true,
        )
        self.assertEqual(record2.trim_ref_id, "trim-2")


class TestCompatibilityStatusTransitions(unittest.TestCase):
    """REQ-CMP-003"""

    def test_active_to_archived_allowed(self):
        record = create_compatibility_record("part-1", "trim-1", [], always_true, always_true)
        transition_compatibility_status(record, "archived")
        self.assertEqual(record.status, "archived")

    def test_archived_is_terminal(self):
        record = create_compatibility_record("part-1", "trim-1", [], always_true, always_true)
        transition_compatibility_status(record, "archived")
        with self.assertRaises(InvalidCompatibilityStatusError):
            transition_compatibility_status(record, "active")


class TestRealCrossServiceIntegration(unittest.TestCase):
    """
    اختبار تكامل حقيقي فعلي: يُنشئ قطعة كتالوج حقيقية عبر PCT وفئة سيارة
    حقيقية عبر VCT (كلاهما بمستودعَيهما الوهميين InMemory الفعليين)، ثم
    يمرِّر دالتَي التحقق الحقيقيتين لهما (لا Mock) إلى CMP للتأكد من أن
    التكامل الفعلي بين الخدمات الثلاث يعمل كما هو متوقَّع من طرف لطرف.
    """

    def setUp(self):
        self.pct_repo = InMemoryPctRepository()
        self.vct_repo = InMemoryVctRepository()

    def test_compatibility_creation_fails_for_unapproved_real_part(self):
        part = propose_catalog_part_via_repository(self.pct_repo, category_id="cat-1")  # لم يُعتمَد بعد
        manufacturer = propose_manufacturer_via_repository(self.vct_repo)
        trim = create_full_trim_via_repository(self.vct_repo, manufacturer.id, "petrol", "automatic")

        with self.assertRaises(PartNotApprovedForCompatibilityError):
            create_compatibility_record(
                part.id, trim.id, existing_records_for_pair=[],
                is_part_approved_checker=self.pct_repo.is_part_approved,
                is_trim_valid_checker=self.vct_repo.is_trim_valid,
            )

    def test_compatibility_creation_succeeds_for_approved_real_part_and_real_trim(self):
        part = propose_catalog_part_via_repository(self.pct_repo, category_id="cat-1")
        approve_catalog_part_via_repository(self.pct_repo, part.id)  # يُعتمَد فعليًا عبر PCT الحقيقية
        manufacturer = propose_manufacturer_via_repository(self.vct_repo)
        trim = create_full_trim_via_repository(self.vct_repo, manufacturer.id, "petrol", "automatic")

        record = create_compatibility_record(
            part.id, trim.id, existing_records_for_pair=[],
            is_part_approved_checker=self.pct_repo.is_part_approved,
            is_trim_valid_checker=self.vct_repo.is_trim_valid,
        )
        self.assertEqual(record.catalog_part_ref_id, part.id)
        self.assertEqual(record.trim_ref_id, trim.id)

    def test_compatibility_creation_fails_for_nonexistent_trim(self):
        part = propose_catalog_part_via_repository(self.pct_repo, category_id="cat-1")
        approve_catalog_part_via_repository(self.pct_repo, part.id)

        with self.assertRaises(TrimNotValidForCompatibilityError):
            create_compatibility_record(
                part.id, "trim-does-not-exist", existing_records_for_pair=[],
                is_part_approved_checker=self.pct_repo.is_part_approved,
                is_trim_valid_checker=self.vct_repo.is_trim_valid,
            )


class TestExtendedFields(unittest.TestCase):
    """مقترحات المالك: fitment_type، compatibility_notes، source"""

    def test_default_fitment_type_and_source(self):
        record = create_compatibility_record("part-1", "trim-1", [], always_true, always_true)
        self.assertEqual(record.fitment_type, "unknown")
        self.assertEqual(record.source, "catalog_admin")
        self.assertIsNone(record.compatibility_notes)

    def test_explicit_fitment_type_and_notes(self):
        record = create_compatibility_record(
            "part-1", "trim-1", [], always_true, always_true,
            fitment_type="requires_modification", compatibility_notes="يتطلب تعديل الحامل",
            source="merchant_proposal",
        )
        self.assertEqual(record.fitment_type, "requires_modification")
        self.assertEqual(record.compatibility_notes, "يتطلب تعديل الحامل")
        self.assertEqual(record.source, "merchant_proposal")

    def test_unknown_fitment_type_rejected(self):
        with self.assertRaises(ValueError):
            create_compatibility_record("part-1", "trim-1", [], always_true, always_true,
                                         fitment_type="perfect_match")

    def test_unknown_source_rejected(self):
        with self.assertRaises(ValueError):
            create_compatibility_record("part-1", "trim-1", [], always_true, always_true,
                                         source="random_guess")


class TestAdministrativeAuditEventBuilder(unittest.TestCase):

    def test_build_event_for_known_action(self):
        from cmp_service import build_administrative_audit_event
        event = build_administrative_audit_event("compatibility_created", "admin-1", "cmp-1")
        self.assertEqual(event["log_type"], "administrative")

    def test_build_event_rejects_unknown_action(self):
        from cmp_service import build_administrative_audit_event
        with self.assertRaises(ValueError):
            build_administrative_audit_event("unknown_action", "admin-1", "cmp-1")


if __name__ == "__main__":
    unittest.main(verbosity=2)
