"""
scheduler_service.py — منطق خدمة المُجدوِل المشترك (Platform Scheduler)
المرجع: ADR-035 (Blueprint)؛ REQ-PUR-020 (أول مستهلِك فعلي)

مبدأ جوهري: هذه الخدمة عامة تمامًا، لا تعرف شيئًا عن PUR أو NTF أو أي مجال
أعمال بعينه. job_type مجرَّد نص حر يحدِّده المستهلِك (كـ"pur_expiration_check"
أو "ntf_campaign_dispatch")؛ منطق التنفيذ الفعلي لكل نوع مهمة يبقى بالكامل
خارج هذا الملف، في المجال المستهلِك نفسه — الجدولة تخبر "متى"، لا "ماذا تفعل".

مبدأ عدم الحذف الفعلي: المهام سجل تشغيلي (Operational Record)؛ الإزالة عبر
تغيير الحالة إلى cancelled فقط، لا حذف فعلي أبدًا.
"""

from dataclasses import dataclass
from typing import Optional, List
from datetime import datetime, timedelta


JOB_STATUSES = {"pending", "executing", "completed", "cancelled", "failed"}
JOB_ALLOWED_TRANSITIONS = {
    "pending": {"executing", "cancelled"},
    "executing": {"completed", "failed"},
    "completed": set(),
    "cancelled": set(),
    "failed": {"pending"},  # إعادة جدولة بعد فشل، لا إعادة تنفيذ مباشرة
}

VALID_RECURRENCE_RULES = {None, "daily", "weekly", "monthly"}


@dataclass
class ScheduledJob:
    id: str
    job_type: str            # نص حر يحدِّده المستهلِك؛ لا معرفة بمجال بعينه هنا
    target_ref_id: str        # SSOT: إشارة مرجعية فقط لكيان المستهلِك (كمعرّف طلب شراء أو حملة)
    scheduled_at: datetime
    recurrence_rule: Optional[str] = None
    status: str = "pending"
    last_run_at: Optional[datetime] = None


class InvalidJobStatusError(Exception):
    """انتقال حالة غير مسموح به لمهمة مجدوَلة."""


class InvalidRecurrenceRuleError(Exception):
    """قاعدة تكرار غير معروفة."""


# ---------------------------------------------------------------------------
# إنشاء مهمة مجدوَلة (عامة تمامًا، لا تخصيص لأي مجال)
# ---------------------------------------------------------------------------

def schedule_job(job_type: str, target_ref_id: str, scheduled_at: datetime,
                  recurrence_rule: Optional[str] = None) -> ScheduledJob:
    if not job_type or not job_type.strip():
        raise ValueError("نوع المهمة (job_type) يجب ألا يكون فارغًا.")
    if recurrence_rule not in VALID_RECURRENCE_RULES:
        raise InvalidRecurrenceRuleError(f"قاعدة تكرار غير معروفة: {recurrence_rule}")
    return ScheduledJob(id="", job_type=job_type, target_ref_id=target_ref_id,
                         scheduled_at=scheduled_at, recurrence_rule=recurrence_rule)


def cancel_job(job: ScheduledJob) -> ScheduledJob:
    return transition_job_status(job, "cancelled")


def transition_job_status(job: ScheduledJob, new_status: str) -> ScheduledJob:
    if new_status not in JOB_STATUSES:
        raise ValueError(f"حالة مهمة غير معروفة: {new_status}")
    allowed = JOB_ALLOWED_TRANSITIONS.get(job.status, set())
    if new_status not in allowed:
        raise InvalidJobStatusError(
            f"الانتقال من '{job.status}' إلى '{new_status}' غير مسموح به."
        )
    job.status = new_status
    return job


# ---------------------------------------------------------------------------
# استحقاق التنفيذ (Due Jobs) — استعلام عام لا يعرف شيئًا عن نوع المهمة
# ---------------------------------------------------------------------------

def get_due_jobs(jobs: List[ScheduledJob], current_time: datetime) -> List[ScheduledJob]:
    return [j for j in jobs if j.status == "pending" and j.scheduled_at <= current_time]


def mark_job_executed(job: ScheduledJob, occurred_at: datetime, succeeded: bool) -> ScheduledJob:
    """
    يُستدعى بعد أن يُنفِّذ المستهلِك (كـPUR أو NTF) منطقه الخاص فعليًا؛ هذه
    الدالة تُحدِّث حالة المهمة فقط، ولا تنفِّذ أي منطق أعمال بعينه.
    """
    transition_job_status(job, "executing")
    job.last_run_at = occurred_at
    if succeeded:
        transition_job_status(job, "completed")
        if job.recurrence_rule is not None:
            return schedule_next_occurrence(job, occurred_at)
    else:
        transition_job_status(job, "failed")
    return job


# ---------------------------------------------------------------------------
# التكرار الدوري (يُنشئ مهمة تالية جديدة، لا يُعدِّل المهمة المكتملة بأثر رجعي)
# ---------------------------------------------------------------------------

def compute_next_occurrence(current_scheduled_at: datetime, recurrence_rule: str) -> datetime:
    if recurrence_rule == "daily":
        return current_scheduled_at + timedelta(days=1)
    if recurrence_rule == "weekly":
        return current_scheduled_at + timedelta(weeks=1)
    if recurrence_rule == "monthly":
        return current_scheduled_at + timedelta(days=30)  # تقريب مبسَّط للإصدار الأول
    raise InvalidRecurrenceRuleError(f"قاعدة تكرار غير معروفة: {recurrence_rule}")


def schedule_next_occurrence(completed_job: ScheduledJob, occurred_at: datetime) -> ScheduledJob:
    """
    يُنشئ مهمة جديدة مستقلة للتكرار التالي؛ المهمة المكتملة تبقى سجلاً
    تاريخيًا ثابتًا (لا حذف، لا تعديل)، اتساقًا مع مبدأ عدم الحذف الفعلي.
    """
    next_time = compute_next_occurrence(completed_job.scheduled_at, completed_job.recurrence_rule)
    return schedule_job(completed_job.job_type, completed_job.target_ref_id, next_time,
                         completed_job.recurrence_rule)


# ---------------------------------------------------------------------------
# نقاط تجميع تعتمد على طبقة Repository (دليل حوكمة التنفيذ v1.3/1.4/1.7)
# ---------------------------------------------------------------------------

def schedule_job_via_repository(repository, job_type: str, target_ref_id: str,
                                 scheduled_at: datetime, recurrence_rule: Optional[str] = None) -> ScheduledJob:
    job = schedule_job(job_type, target_ref_id, scheduled_at, recurrence_rule)
    return repository.insert_job(job)


def cancel_job_via_repository(repository, job_id: str) -> ScheduledJob:
    job = repository.get_job_by_id(job_id)
    if job is None:
        raise ValueError(f"لا توجد مهمة مجدوَلة بالمعرّف: {job_id}")
    cancel_job(job)
    return repository.update_job(job)


def process_due_jobs_via_repository(repository, current_time: datetime, execute_job_fn):
    """
    نقطة الدخول الفعلية التي يستدعيها أي مجال مستهلِك (PUR أو NTF) دوريًا؛
    execute_job_fn دالة محقونة يوفِّرها المستهلِك نفسه، تحتوي منطق التنفيذ
    الفعلي الخاص بنوع مهمته (لا معرفة للمُجدوِل بمحتواها)، وتُعيد True/False
    للنجاح/الفشل.
    """
    due_jobs = repository.get_pending_jobs_due_before(current_time)
    results = []
    for job in due_jobs:
        succeeded = execute_job_fn(job)
        updated_job = mark_job_executed(job, occurred_at=current_time, succeeded=succeeded)
        repository.update_job(job)
        if succeeded and job.recurrence_rule is not None:
            next_job = updated_job  # mark_job_executed أعاد مهمة التكرار التالية عند النجاح
            repository.insert_job(next_job)
        results.append(updated_job)
    return results
