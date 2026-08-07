"""
test_scheduler_service.py — اختبارات وحدة للمُجدوِل المشترك (Platform Scheduler)
تُشغَّل عبر: python3 -m unittest discover -s tests -v
"""

import sys
import os
import unittest
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from scheduler_service import (  # noqa: E402
    ScheduledJob, schedule_job, cancel_job, transition_job_status,
    get_due_jobs, mark_job_executed, compute_next_occurrence, schedule_next_occurrence,
    InvalidJobStatusError, InvalidRecurrenceRuleError,
)


class TestSchedulerIsGenericAndDomainAgnostic(unittest.TestCase):
    """يثبت أن المُجدوِل لا يفترض أي معرفة بمجال أعمال بعينه."""

    def test_can_schedule_any_job_type_string(self):
        job1 = schedule_job("pur_expiration_check", "pr-1", datetime(2026, 2, 1))
        job2 = schedule_job("ntf_campaign_dispatch", "campaign-1", datetime(2026, 2, 1))
        job3 = schedule_job("cms_content_publish", "post-1", datetime(2026, 2, 1))
        self.assertEqual(job1.job_type, "pur_expiration_check")
        self.assertEqual(job2.job_type, "ntf_campaign_dispatch")
        self.assertEqual(job3.job_type, "cms_content_publish")

    def test_empty_job_type_rejected(self):
        with self.assertRaises(ValueError):
            schedule_job("   ", "x-1", datetime(2026, 2, 1))


class TestJobLifecycle(unittest.TestCase):

    def test_new_job_is_pending(self):
        job = schedule_job("pur_expiration_check", "pr-1", datetime(2026, 2, 1))
        self.assertEqual(job.status, "pending")

    def test_cancel_from_pending(self):
        job = schedule_job("pur_expiration_check", "pr-1", datetime(2026, 2, 1))
        cancel_job(job)
        self.assertEqual(job.status, "cancelled")

    def test_completed_is_terminal(self):
        job = schedule_job("x", "y", datetime(2026, 2, 1))
        transition_job_status(job, "executing")
        transition_job_status(job, "completed")
        with self.assertRaises(InvalidJobStatusError):
            transition_job_status(job, "executing")

    def test_failed_can_be_rescheduled_to_pending(self):
        job = schedule_job("x", "y", datetime(2026, 2, 1))
        transition_job_status(job, "executing")
        transition_job_status(job, "failed")
        transition_job_status(job, "pending")
        self.assertEqual(job.status, "pending")

    def test_invalid_recurrence_rule_rejected(self):
        with self.assertRaises(InvalidRecurrenceRuleError):
            schedule_job("x", "y", datetime(2026, 2, 1), recurrence_rule="hourly")


class TestDueJobsQuery(unittest.TestCase):

    def test_only_due_pending_jobs_returned(self):
        past_job = schedule_job("x", "y", datetime(2026, 1, 1))
        future_job = schedule_job("x", "z", datetime(2026, 6, 1))
        cancelled_job = schedule_job("x", "w", datetime(2026, 1, 1))
        cancel_job(cancelled_job)

        due = get_due_jobs([past_job, future_job, cancelled_job], current_time=datetime(2026, 2, 1))
        self.assertEqual(due, [past_job])

    def test_no_due_jobs_returns_empty(self):
        job = schedule_job("x", "y", datetime(2026, 6, 1))
        due = get_due_jobs([job], current_time=datetime(2026, 1, 1))
        self.assertEqual(due, [])


class TestJobExecutionAndRecurrence(unittest.TestCase):

    def test_successful_one_time_job_completes(self):
        job = schedule_job("pur_expiration_check", "pr-1", datetime(2026, 1, 1))
        result = mark_job_executed(job, occurred_at=datetime(2026, 1, 1, 10), succeeded=True)
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.last_run_at, datetime(2026, 1, 1, 10))

    def test_failed_job_marked_failed(self):
        job = schedule_job("x", "y", datetime(2026, 1, 1))
        result = mark_job_executed(job, occurred_at=datetime(2026, 1, 1, 10), succeeded=False)
        self.assertEqual(result.status, "failed")

    def test_recurring_job_creates_new_next_occurrence_without_mutating_completed_one(self):
        job = schedule_job("ntf_campaign_dispatch", "campaign-1", datetime(2026, 1, 1), recurrence_rule="daily")
        next_job = mark_job_executed(job, occurred_at=datetime(2026, 1, 1, 10), succeeded=True)

        # المهمة الأصلية تبقى مكتملة، سجلاً تاريخيًا ثابتًا
        self.assertEqual(job.status, "completed")
        # المهمة الجديدة مستقلة تمامًا، بحالة pending وموعد اليوم التالي
        self.assertEqual(next_job.status, "pending")
        self.assertEqual(next_job.scheduled_at, datetime(2026, 1, 2))
        self.assertIsNot(next_job, job)

    def test_compute_next_occurrence_weekly(self):
        next_time = compute_next_occurrence(datetime(2026, 1, 1), "weekly")
        self.assertEqual(next_time, datetime(2026, 1, 8))

    def test_compute_next_occurrence_invalid_rule(self):
        with self.assertRaises(InvalidRecurrenceRuleError):
            compute_next_occurrence(datetime(2026, 1, 1), "yearly")


if __name__ == "__main__":
    unittest.main(verbosity=2)
