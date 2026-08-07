"""
test_scheduler_repository.py — اختبارات وحدة لتنسيق المُجدوِل عبر Repository
تُشغَّل عبر: python3 -m unittest discover -s tests -v
"""

import sys
import os
import unittest
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from scheduler_service import (  # noqa: E402
    schedule_job_via_repository, cancel_job_via_repository, process_due_jobs_via_repository,
)
from scheduler_repository import InMemorySchedulerRepository  # noqa: E402


class TestSchedulerRepositoryOrchestration(unittest.TestCase):

    def setUp(self):
        self.repo = InMemorySchedulerRepository()

    def test_schedule_job_via_repository_assigns_id(self):
        job = schedule_job_via_repository(self.repo, "pur_expiration_check", "pr-1", datetime(2026, 2, 1))
        self.assertTrue(job.id.startswith("job-"))

    def test_cancel_job_via_repository(self):
        job = schedule_job_via_repository(self.repo, "pur_expiration_check", "pr-1", datetime(2026, 2, 1))
        cancel_job_via_repository(self.repo, job.id)
        fetched = self.repo.get_job_by_id(job.id)
        self.assertEqual(fetched.status, "cancelled")

    def test_cancel_unknown_job_raises(self):
        with self.assertRaises(ValueError):
            cancel_job_via_repository(self.repo, "nonexistent")


class TestProcessDueJobsViaRepository(unittest.TestCase):
    """
    اختبار محوري: يثبت أن مجالَي استهلاك مختلفين تمامًا (محاكاة PUR ومحاكاة
    NTF) يمكنهما استخدام نفس المُجدوِل المشترك دون أي تعديل عليه، عبر دوال
    تنفيذ محقونة مختلفة تمامًا لكل منهما.
    """

    def setUp(self):
        self.repo = InMemorySchedulerRepository()

    def test_pur_style_job_executes_successfully(self):
        schedule_job_via_repository(self.repo, "pur_expiration_check", "pr-1", datetime(2026, 1, 1))

        executed_targets = []

        def fake_pur_executor(job):
            executed_targets.append(job.target_ref_id)
            return True  # نجاح دائمًا في هذا الاختبار

        results = process_due_jobs_via_repository(self.repo, current_time=datetime(2026, 1, 2),
                                                     execute_job_fn=fake_pur_executor)
        self.assertEqual(executed_targets, ["pr-1"])
        self.assertEqual(results[0].status, "completed")

    def test_ntf_style_recurring_job_creates_next_occurrence(self):
        schedule_job_via_repository(self.repo, "ntf_campaign_dispatch", "campaign-1",
                                     datetime(2026, 1, 1), recurrence_rule="daily")

        def fake_ntf_executor(job):
            return True

        process_due_jobs_via_repository(self.repo, current_time=datetime(2026, 1, 2),
                                          execute_job_fn=fake_ntf_executor)

        all_jobs_of_type = self.repo.get_jobs_by_type("ntf_campaign_dispatch")
        # يجب أن توجد الآن مهمتان: الأصلية المكتملة + التكرار التالي الجديد
        self.assertEqual(len(all_jobs_of_type), 2)
        statuses = sorted(j.status for j in all_jobs_of_type)
        self.assertEqual(statuses, ["completed", "pending"])

    def test_failed_execution_does_not_create_next_occurrence(self):
        schedule_job_via_repository(self.repo, "ntf_campaign_dispatch", "campaign-2",
                                     datetime(2026, 1, 1), recurrence_rule="daily")

        def failing_executor(job):
            return False

        process_due_jobs_via_repository(self.repo, current_time=datetime(2026, 1, 2),
                                          execute_job_fn=failing_executor)

        all_jobs = self.repo.get_jobs_by_type("ntf_campaign_dispatch")
        self.assertEqual(len(all_jobs), 1)  # لا مهمة تالية عند الفشل
        self.assertEqual(all_jobs[0].status, "failed")

    def test_not_yet_due_jobs_are_not_processed(self):
        schedule_job_via_repository(self.repo, "pur_expiration_check", "pr-2", datetime(2026, 6, 1))

        def executor(job):
            return True

        results = process_due_jobs_via_repository(self.repo, current_time=datetime(2026, 1, 1),
                                                     execute_job_fn=executor)
        self.assertEqual(results, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
