"""
scheduler_repository.py — طبقة الوصول للبيانات للمُجدوِل المشترك (Repository Pattern)
المرجع: ADR-035؛ دليل حوكمة التنفيذ v1.7

لا دالة حذف فعلي؛ الإزالة عبر تغيير الحالة إلى cancelled فقط.
"""

from abc import ABC, abstractmethod
from typing import Optional, List

from scheduler_service import ScheduledJob


class SchedulerRepository(ABC):
    """العقد الوحيد الذي تعتمد عليه scheduler_service.py. لا دالة حذف عمدًا."""

    @abstractmethod
    def insert_job(self, job: ScheduledJob) -> ScheduledJob: raise NotImplementedError

    @abstractmethod
    def get_job_by_id(self, job_id: str) -> Optional[ScheduledJob]: raise NotImplementedError

    @abstractmethod
    def update_job(self, job: ScheduledJob) -> ScheduledJob: raise NotImplementedError

    @abstractmethod
    def get_pending_jobs_due_before(self, current_time) -> List[ScheduledJob]:
        """يُستدعى دوريًا من أي مجال مستهلِك؛ لا معرفة بنوع المهمة هنا سوى الفلترة الزمنية والحالة."""
        raise NotImplementedError

    @abstractmethod
    def get_jobs_by_type(self, job_type: str) -> List[ScheduledJob]: raise NotImplementedError

    @abstractmethod
    def list_jobs(self, status: Optional[str], page: int, page_size: int) -> "tuple[List[ScheduledJob], int]":
        """CR-015: مدير النظام حصريًا (يُتحقَّق في طبقة الـAPI)."""
        raise NotImplementedError


class PostgresSchedulerRepository(SchedulerRepository):
    """تنفيذ فعلي عبر PostgreSQL؛ جدول sys.scheduled_jobs عام لكل المجالات. غير مختبَر على اتصال حي."""

    def __init__(self, connection):
        self._connection = connection

    def insert_job(self, job: ScheduledJob) -> ScheduledJob:
        with self._connection.cursor() as cur:
            cur.execute(
                "INSERT INTO sys.scheduled_jobs (job_type, target_ref_id, scheduled_at, "
                "recurrence_rule, status) VALUES (%(jt)s, %(t)s, %(sa)s, %(rr)s, %(s)s) RETURNING id",
                {"jt": job.job_type, "t": job.target_ref_id, "sa": job.scheduled_at,
                 "rr": job.recurrence_rule, "s": job.status},
            )
            job.id = cur.fetchone()["id"]
        return job

    def get_job_by_id(self, job_id: str) -> Optional[ScheduledJob]:
        with self._connection.cursor() as cur:
            cur.execute("SELECT * FROM sys.scheduled_jobs WHERE id = %(id)s", {"id": job_id})
            row = cur.fetchone()
        return self._row_to_job(row) if row else None

    def update_job(self, job: ScheduledJob) -> ScheduledJob:
        with self._connection:
            with self._connection.cursor() as cur:
                cur.execute(
                    "UPDATE sys.scheduled_jobs SET status = %(s)s, last_run_at = %(lra)s WHERE id = %(id)s",
                    {"s": job.status, "lra": job.last_run_at, "id": job.id},
                )
        return job

    def get_pending_jobs_due_before(self, current_time) -> List[ScheduledJob]:
        # يعتمد على idx_scheduled_jobs_status_scheduled_at (فهرس مركَّب)
        with self._connection.cursor() as cur:
            cur.execute(
                "SELECT * FROM sys.scheduled_jobs WHERE status = 'pending' AND scheduled_at <= %(t)s",
                {"t": current_time},
            )
            rows = cur.fetchall()
        return [self._row_to_job(r) for r in rows]

    def get_jobs_by_type(self, job_type: str) -> List[ScheduledJob]:
        with self._connection.cursor() as cur:
            cur.execute("SELECT * FROM sys.scheduled_jobs WHERE job_type = %(jt)s", {"jt": job_type})
            rows = cur.fetchall()
        return [self._row_to_job(r) for r in rows]

    def list_jobs(self, status: Optional[str], page: int, page_size: int):
        offset = (page - 1) * page_size
        where_clause = ""
        params = {"limit": page_size, "offset": offset}
        if status is not None:
            where_clause = "WHERE status = %(status)s"
            params["status"] = status
        with self._connection.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) AS total FROM sys.scheduled_jobs {where_clause}", params)
            total = cur.fetchone()["total"]
            cur.execute(
                f"SELECT * FROM sys.scheduled_jobs {where_clause} "
                f"ORDER BY created_at DESC LIMIT %(limit)s OFFSET %(offset)s",
                params,
            )
            rows = cur.fetchall()
        return [self._row_to_job(r) for r in rows], total

    @staticmethod
    def _row_to_job(row) -> ScheduledJob:
        return ScheduledJob(id=row["id"], job_type=row["job_type"], target_ref_id=row["target_ref_id"],
                             scheduled_at=row["scheduled_at"], recurrence_rule=row["recurrence_rule"],
                             status=row["status"], last_run_at=row["last_run_at"])


class InMemorySchedulerRepository(SchedulerRepository):
    """تنفيذ وهمي للاختبار فقط. لا دالة حذف هنا أيضًا، عمدًا."""

    def __init__(self):
        self._jobs = {}
        self._next_seq = 1

    def insert_job(self, job: ScheduledJob) -> ScheduledJob:
        job.id = f"job-{self._next_seq}"
        self._next_seq += 1
        self._jobs[job.id] = job
        return job

    def get_job_by_id(self, job_id: str) -> Optional[ScheduledJob]:
        return self._jobs.get(job_id)

    def update_job(self, job: ScheduledJob) -> ScheduledJob:
        self._jobs[job.id] = job
        return job

    def get_pending_jobs_due_before(self, current_time) -> List[ScheduledJob]:
        return [j for j in self._jobs.values() if j.status == "pending" and j.scheduled_at <= current_time]

    def get_jobs_by_type(self, job_type: str) -> List[ScheduledJob]:
        return [j for j in self._jobs.values() if j.job_type == job_type]

    def list_jobs(self, status: Optional[str], page: int, page_size: int):
        items = list(reversed(list(self._jobs.values())))
        if status is not None:
            items = [j for j in items if j.status == status]
        total = len(items)
        start = (page - 1) * page_size
        return items[start:start + page_size], total
