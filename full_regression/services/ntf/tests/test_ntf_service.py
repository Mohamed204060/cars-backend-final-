"""
test_ntf_service.py — اختبارات وحدة لخدمة NTF
تُشغَّل عبر: python3 -m unittest discover -s tests -v
"""

import sys
import os
import unittest
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from ntf_service import (  # noqa: E402
    Campaign, Delivery, Recipient, Template, NotificationPreference, NotificationCenterEntry,
    ChannelProviderInfo,
    create_campaign, transition_campaign_status, validate_scheduling,
    create_delivery_for_campaign, add_recipient, transition_recipient_status,
    is_send_allowed_by_preferences, ensure_send_allowed,
    create_template, create_new_template_version,
    mark_notification_read, archive_notification_for_user, delete_notification_for_user,
    update_channel_health, build_administrative_audit_event, generate_correlation_id,
    InvalidCampaignStatusError, InvalidDeliveryStatusError, DuplicateRecipientError,
    PreferenceBlockedError, SchedulingValidationError, TemplateArchivedImmutableError,
)


class TestCampaignLifecycle(unittest.TestCase):

    def test_create_campaign_defaults(self):
        c = create_campaign("admin-1", "عرض الصيف", "خصم 20%", "static")
        self.assertEqual(c.status, "draft")
        self.assertEqual(c.priority, "normal")

    def test_draft_to_scheduled_allowed(self):
        c = create_campaign("admin-1", "t", "b", "static")
        transition_campaign_status(c, "scheduled")
        self.assertEqual(c.status, "scheduled")

    def test_archived_is_terminal(self):
        c = create_campaign("admin-1", "t", "b", "static")
        transition_campaign_status(c, "cancelled")
        transition_campaign_status(c, "archived")
        with self.assertRaises(InvalidCampaignStatusError):
            transition_campaign_status(c, "running")

    def test_invalid_audience_type_rejected(self):
        with self.assertRaises(ValueError):
            create_campaign("admin-1", "t", "b", "everyone")

    def test_invalid_priority_rejected(self):
        with self.assertRaises(ValueError):
            create_campaign("admin-1", "t", "b", "static", priority="urgent")


class TestSchedulingValidation(unittest.TestCase):

    def test_past_date_rejected(self):
        c = create_campaign("admin-1", "t", "b", "static")
        with self.assertRaises(SchedulingValidationError):
            validate_scheduling(c, datetime(2020, 1, 1), datetime(2026, 1, 1), True, True, True)

    def test_no_recipients_rejected(self):
        c = create_campaign("admin-1", "t", "b", "static")
        with self.assertRaises(SchedulingValidationError):
            validate_scheduling(c, datetime(2026, 2, 1), datetime(2026, 1, 1), False, True, True)

    def test_no_channel_rejected(self):
        c = create_campaign("admin-1", "t", "b", "static")
        with self.assertRaises(SchedulingValidationError):
            validate_scheduling(c, datetime(2026, 2, 1), datetime(2026, 1, 1), True, False, True)

    def test_invalid_template_rejected(self):
        c = create_campaign("admin-1", "t", "b", "static")
        with self.assertRaises(SchedulingValidationError):
            validate_scheduling(c, datetime(2026, 2, 1), datetime(2026, 1, 1), True, True, False)

    def test_valid_scheduling_passes(self):
        c = create_campaign("admin-1", "t", "b", "static")
        validate_scheduling(c, datetime(2026, 2, 1), datetime(2026, 1, 1), True, True, True)  # لا استثناء


class TestDeliveryAndRecipientDedup(unittest.TestCase):
    """REQ-NTF-012, 020, 021"""

    def test_create_delivery_carries_correlation_id_and_version_snapshot(self):
        c = create_campaign("admin-1", "t", "b", "static")
        c.id = "camp-1"
        c.campaign_version = 3
        delivery = create_delivery_for_campaign(c)
        self.assertEqual(delivery.campaign_version_snapshot, 3)
        self.assertTrue(len(delivery.correlation_id) > 0)

    def test_duplicate_recipient_rejected(self):
        delivery = Delivery(id="d1", campaign_id="c1", campaign_version_snapshot=1, correlation_id="corr-1")
        existing = [add_recipient(delivery, [], "user-1", "in_app")]
        with self.assertRaises(DuplicateRecipientError):
            add_recipient(delivery, existing, "user-1", "in_app")

    def test_different_users_allowed(self):
        delivery = Delivery(id="d1", campaign_id="c1", campaign_version_snapshot=1, correlation_id="corr-1")
        existing = [add_recipient(delivery, [], "user-1", "in_app")]
        r2 = add_recipient(delivery, existing, "user-2", "in_app")
        self.assertEqual(r2.user_ref_id, "user-2")

    def test_recipient_status_transitions_with_timestamps(self):
        recipient = Recipient(id="r1", delivery_id="d1", user_ref_id="user-1", channel_provider_code="in_app")
        now = datetime(2026, 1, 1, 10, 0, 0)
        transition_recipient_status(recipient, "queued", now)
        transition_recipient_status(recipient, "sent", now)
        self.assertEqual(recipient.sent_at, now)
        transition_recipient_status(recipient, "delivered", now)
        self.assertEqual(recipient.delivered_at, now)
        transition_recipient_status(recipient, "read", now)
        self.assertEqual(recipient.read_at, now)

    def test_failed_transition_records_reason(self):
        recipient = Recipient(id="r1", delivery_id="d1", user_ref_id="user-1", channel_provider_code="in_app")
        transition_recipient_status(recipient, "queued", datetime.now())
        transition_recipient_status(recipient, "failed", datetime.now(), failure_reason_code="rate_limit")
        self.assertEqual(recipient.failure_reason_code, "rate_limit")

    def test_read_is_terminal(self):
        recipient = Recipient(id="r1", delivery_id="d1", user_ref_id="user-1",
                               channel_provider_code="in_app", status="read")
        with self.assertRaises(InvalidDeliveryStatusError):
            transition_recipient_status(recipient, "sent", datetime.now())


class TestNotificationPreferences(unittest.TestCase):
    """REQ-NTF-034, 035"""

    def test_no_preference_means_allowed(self):
        self.assertTrue(is_send_allowed_by_preferences([], "email", "marketing", "normal"))

    def test_disabled_preference_blocks_normal_priority(self):
        prefs = [NotificationPreference(id="p1", user_ref_id="user-1",
                                         channel_provider_code="email", notification_type="marketing", is_enabled=False)]
        self.assertFalse(is_send_allowed_by_preferences(prefs, "email", "marketing", "normal"))

    def test_critical_priority_overrides_disabled_preference(self):
        prefs = [NotificationPreference(id="p1", user_ref_id="user-1",
                                         channel_provider_code="email", notification_type="marketing", is_enabled=False)]
        self.assertTrue(is_send_allowed_by_preferences(prefs, "email", "marketing", "critical"))

    def test_ensure_send_allowed_raises_when_blocked(self):
        prefs = [NotificationPreference(id="p1", user_ref_id="user-1",
                                         channel_provider_code="sms", notification_type="marketing", is_enabled=False)]
        with self.assertRaises(PreferenceBlockedError):
            ensure_send_allowed(prefs, "sms", "marketing", "normal")


class TestTemplateVersioning(unittest.TestCase):
    """BR-NTF-006"""

    def test_create_new_version_increments_number(self):
        template, v1 = create_template("welcome", "مرحبًا", "أهلاً بك")
        template.id = "tmpl-1"
        v2 = create_new_template_version(template, "مرحبًا مجددًا", "أهلاً من جديد")
        self.assertEqual(v2.version_number, 2)
        self.assertEqual(template.current_version_number, 2)

    def test_cannot_version_archived_template(self):
        template, _ = create_template("welcome", "t", "b")
        template.status = "archived"
        with self.assertRaises(TemplateArchivedImmutableError):
            create_new_template_version(template, "t2", "b2")

    def test_old_version_content_unchanged_by_new_version(self):
        # يثبت عدم التعديل بأثر رجعي: الإصدار القديم يبقى كما هو ككائن منفصل
        template, v1 = create_template("welcome", "نص أصلي", "محتوى أصلي")
        create_new_template_version(template, "نص جديد", "محتوى جديد")
        self.assertEqual(v1.title, "نص أصلي")  # لم يتغيَّر الكائن الأصلي


class TestNotificationCenter(unittest.TestCase):
    """REQ-NTF-036"""

    def test_mark_read(self):
        entry = NotificationCenterEntry(id="e1", recipient_id="r1", user_ref_id="user-1")
        mark_notification_read(entry)
        self.assertTrue(entry.is_read)

    def test_archive_does_not_mark_read(self):
        entry = NotificationCenterEntry(id="e1", recipient_id="r1", user_ref_id="user-1")
        archive_notification_for_user(entry)
        self.assertTrue(entry.is_archived_by_user)
        self.assertFalse(entry.is_read)

    def test_delete_is_soft_not_destructive(self):
        entry = NotificationCenterEntry(id="e1", recipient_id="r1", user_ref_id="user-1")
        delete_notification_for_user(entry)
        self.assertTrue(entry.is_deleted_by_user)
        self.assertEqual(entry.id, "e1")  # الكائن نفسه لم يُدمَّر


class TestChannelProviderHealth(unittest.TestCase):

    def test_update_health_status(self):
        provider = ChannelProviderInfo(code="email", display_name="Email")
        update_channel_health(provider, "degraded")
        self.assertEqual(provider.health_status, "degraded")

    def test_invalid_health_status_rejected(self):
        provider = ChannelProviderInfo(code="email", display_name="Email")
        with self.assertRaises(ValueError):
            update_channel_health(provider, "unknown")


class TestAuditEventAndCorrelationId(unittest.TestCase):

    def test_build_event_for_known_action(self):
        corr = generate_correlation_id()
        event = build_administrative_audit_event("campaign_created", "admin-1", "camp-1", corr)
        self.assertEqual(event["metadata"]["correlation_id"], corr)

    def test_build_event_rejects_unknown_action(self):
        with self.assertRaises(ValueError):
            build_administrative_audit_event("unknown_action", "admin-1", "camp-1", "corr-1")

    def test_correlation_ids_are_unique(self):
        ids = {generate_correlation_id() for _ in range(100)}
        self.assertEqual(len(ids), 100)


if __name__ == "__main__":
    unittest.main(verbosity=2)
