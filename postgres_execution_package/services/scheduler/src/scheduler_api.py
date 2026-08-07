"""
scheduler_api.py — طبقة REST API إدارية للمُجدوِل المشترك (Scheduler)
المرجع: Final Backend Batch Contract Extension؛ ADR-035

نطاق إداري بحت: مدير النظام حصريًا (SYSTEM_ADMIN_ROLES نفسها من PCT/VCT/
CMP/Store) — هذه أداة تشغيلية داخلية، لا يستهلكها المستخدم النهائي مباشرة.
التنفيذ الدوري الفعلي (process_due_jobs_via_repository) يبقى خارج REST
تمامًا، يُستدعى بواسطة عملية دورية (Cron/Worker) لا طلب HTTP.
"""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel

from auth_api import error, get_correlation_id, get_current_session
from pct_api import SYSTEM_ADMIN_ROLES, get_auth_repository_for_role_check
from session_service import Session
from scheduler_service import InvalidJobStatusError, InvalidRecurrenceRuleError, cancel_job_via_repository, schedule_job_via_repository

router = APIRouter(prefix="/api/v1/admin/scheduled-jobs", tags=["admin-scheduler"])


class ScheduledJobCreateRequest(BaseModel):
    job_type: str
    target_ref_id: str
    scheduled_at: datetime
    recurrence_rule: Optional[str] = None


class ScheduledJobResponse(BaseModel):
    id: str
    job_type: str
    target_ref_id: str
    scheduled_at: datetime
    recurrence_rule: Optional[str] = None
    status: str
    last_run_at: Optional[datetime] = None


def get_scheduler_repository(request: Request):
    return request.app.state.scheduler_repository


def _to_response(job) -> ScheduledJobResponse:
    return ScheduledJobResponse(id=job.id, job_type=job.job_type, target_ref_id=job.target_ref_id,
                                 scheduled_at=job.scheduled_at, recurrence_rule=job.recurrence_rule,
                                 status=job.status, last_run_at=job.last_run_at)


def _ensure_admin(correlation_id, auth_repo, user_id):
    role = auth_repo.get_user_role(user_id)
    if role not in SYSTEM_ADMIN_ROLES:
        raise error(correlation_id, status.HTTP_403_FORBIDDEN, "FORBIDDEN", "هذه العملية مقصورة على مدير النظام.")


@router.post("", response_model=ScheduledJobResponse, status_code=status.HTTP_201_CREATED)
def create_scheduled_job(
    body: ScheduledJobCreateRequest,
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    scheduler_repo=Depends(get_scheduler_repository),
    auth_repo=Depends(get_auth_repository_for_role_check),
):
    _ensure_admin(correlation_id, auth_repo, current_session.user_id)
    try:
        job = schedule_job_via_repository(
            scheduler_repo, job_type=body.job_type, target_ref_id=body.target_ref_id,
            scheduled_at=body.scheduled_at, recurrence_rule=body.recurrence_rule,
        )
    except InvalidRecurrenceRuleError as exc:
        raise error(correlation_id, status.HTTP_400_BAD_REQUEST, "INVALID_RECURRENCE_RULE", str(exc))
    except ValueError as exc:
        raise error(correlation_id, status.HTTP_400_BAD_REQUEST, "INVALID_JOB_TYPE", str(exc))
    return _to_response(job)


@router.get("/{job_id}", response_model=ScheduledJobResponse)
def get_scheduled_job(
    job_id: str,
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    scheduler_repo=Depends(get_scheduler_repository),
    auth_repo=Depends(get_auth_repository_for_role_check),
):
    _ensure_admin(correlation_id, auth_repo, current_session.user_id)
    job = scheduler_repo.get_job_by_id(job_id)
    if job is None:
        raise error(correlation_id, status.HTTP_404_NOT_FOUND, "JOB_NOT_FOUND", "المهمة المجدوَلة غير موجودة.")
    return _to_response(job)


@router.post("/{job_id}/cancel", response_model=ScheduledJobResponse)
def cancel_scheduled_job(
    job_id: str,
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    scheduler_repo=Depends(get_scheduler_repository),
    auth_repo=Depends(get_auth_repository_for_role_check),
):
    _ensure_admin(correlation_id, auth_repo, current_session.user_id)
    try:
        job = cancel_job_via_repository(scheduler_repo, job_id=job_id)
    except ValueError as exc:
        raise error(correlation_id, status.HTTP_404_NOT_FOUND, "JOB_NOT_FOUND", str(exc))
    except InvalidJobStatusError as exc:
        raise error(correlation_id, status.HTTP_409_CONFLICT, "INVALID_STATUS_TRANSITION", str(exc))
    return _to_response(job)
