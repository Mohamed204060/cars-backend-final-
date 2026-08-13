"""
cmp_api.py — طبقة REST API لخدمة CMP (التوافق)
المرجع: CMP Contract Extension & Implementation Plan؛ REQ-CMP-001..003؛
        Approved VCT Design Baseline §10-17 (Batch 1: Year-specific Compatibility)

نمط SSOT كما صمَّمه cmp_service.py أصلاً: لا استعلام مباشر لبيانات PCT أو
VCT هنا؛ يُحقَن is_part_approved (من PctRepository) وis_trim_valid (من
VctRepository) كدالتين فقط، دون أي ترابط مباشر بين الخدمتين أنفسهما.

REQ-CMP-001: مدير النظام حصريًا لإنشاء سجل توافق. REQ-CMP-003: مدير النظام
حصريًا للأرشفة. نفس فحص SYSTEM_ADMIN_ROLES المعتمَد في PCT/VCT.

Batch 1: body يقبل الآن trim_ref_id (General) أو trim_model_year_ref_id
(Year-specific) — واحد بالضبط (§10). الطلبات القديمة (trim_ref_id فقط)
تبقى تعمل بلا أي تغيير في السلوك — General Compatibility، تمامًا كسابقًا.

ملاحظة نطاق قائمة مسبقًا (لا اكتشاف جديد): fitment_type/compatibility_notes/
source لا أعمدة فعلية لها حتى بعد Migration 030 — خارج نطاق هذه الدفعة عمدًا.
"""

from typing import Optional

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel

from auth_api import error, get_correlation_id, get_current_session
from pct_api import SYSTEM_ADMIN_ROLES, get_auth_repository_for_role_check
from session_service import Session
from cmp_service import (
    CompatibilityLevelConflictError,
    CompatibilityRecordNotFoundError,
    DuplicateCompatibilityRecordError,
    InvalidCompatibilityStatusError,
    InvalidCompatibilityTargetError,
    PartNotApprovedForCompatibilityError,
    TrimModelYearNotFoundError,
    TrimNotValidForCompatibilityError,
    archive_compatibility_record_via_repository,
    create_compatibility_record_via_repository,
)

router = APIRouter(prefix="/api/v1/cmp", tags=["cmp"])


class CompatibilityRecordCreateRequest(BaseModel):
    catalog_part_ref_id: str
    trim_ref_id: Optional[str] = None            # General — Batch 1: اختياري الآن (كان إلزاميًا)
    trim_model_year_ref_id: Optional[str] = None  # Year-specific — Batch 1: جديد


class CompatibilityRecordResponse(BaseModel):
    id: str
    catalog_part_ref_id: str
    trim_ref_id: Optional[str] = None
    trim_model_year_ref_id: Optional[str] = None
    status: str


def get_cmp_repository(request: Request):
    return request.app.state.cmp_repository


def get_pct_repository(request: Request):
    return request.app.state.pct_repository


def get_vct_repository(request: Request):
    return request.app.state.vct_repository


def _to_response(record) -> CompatibilityRecordResponse:
    return CompatibilityRecordResponse(
        id=record.id, catalog_part_ref_id=record.catalog_part_ref_id,
        trim_ref_id=record.trim_ref_id, trim_model_year_ref_id=record.trim_model_year_ref_id,
        status=record.status,
    )


@router.post("/records", response_model=CompatibilityRecordResponse, status_code=status.HTTP_201_CREATED)
def create_record(
    body: CompatibilityRecordCreateRequest,
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    cmp_repo=Depends(get_cmp_repository),
    pct_repo=Depends(get_pct_repository),
    vct_repo=Depends(get_vct_repository),
    auth_repo=Depends(get_auth_repository_for_role_check),
):
    # REQ-CMP-001: مدير النظام حصريًا
    role = auth_repo.get_user_role(current_session.user_id)
    if role not in SYSTEM_ADMIN_ROLES:
        raise error(correlation_id, status.HTTP_403_FORBIDDEN, "FORBIDDEN", "هذه العملية تتطلب صلاحية مدير النظام.")

    try:
        record = create_compatibility_record_via_repository(
            cmp_repo, catalog_part_ref_id=body.catalog_part_ref_id,
            trim_ref_id=body.trim_ref_id, trim_model_year_ref_id=body.trim_model_year_ref_id,
            is_part_approved_checker=pct_repo.is_part_approved,
            is_trim_valid_checker=vct_repo.is_trim_valid,
        )
    except InvalidCompatibilityTargetError as exc:
        raise error(correlation_id, status.HTTP_400_BAD_REQUEST, "INVALID_COMPATIBILITY_TARGET", str(exc))
    except TrimModelYearNotFoundError as exc:
        raise error(correlation_id, status.HTTP_404_NOT_FOUND, "TRIM_MODEL_YEAR_NOT_FOUND", str(exc))
    except PartNotApprovedForCompatibilityError as exc:
        raise error(correlation_id, status.HTTP_409_CONFLICT, "PART_NOT_APPROVED", str(exc))
    except TrimNotValidForCompatibilityError as exc:
        raise error(correlation_id, status.HTTP_409_CONFLICT, "TRIM_NOT_VALID", str(exc))
    except CompatibilityLevelConflictError as exc:
        raise error(correlation_id, status.HTTP_409_CONFLICT, "COMPATIBILITY_LEVEL_CONFLICT", str(exc))
    except DuplicateCompatibilityRecordError as exc:
        raise error(correlation_id, status.HTTP_409_CONFLICT, "DUPLICATE_COMPATIBILITY_RECORD", str(exc))
    return _to_response(record)


@router.get("/records/{record_id}", response_model=CompatibilityRecordResponse)
def get_record(
    record_id: str,
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    cmp_repo=Depends(get_cmp_repository),
):
    record = cmp_repo.get_record_by_id(record_id)
    if record is None:
        raise error(correlation_id, status.HTTP_404_NOT_FOUND, "RECORD_NOT_FOUND", "سجل التوافق غير موجود.")
    return _to_response(record)


@router.get("/parts/{part_id}/records", response_model=list[CompatibilityRecordResponse])
def list_records_for_part(
    part_id: str,
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    cmp_repo=Depends(get_cmp_repository),
):
    records = cmp_repo.get_records_for_part(part_id)
    return [_to_response(r) for r in records]


@router.post("/records/{record_id}/archive", response_model=CompatibilityRecordResponse)
def archive_record(
    record_id: str,
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    cmp_repo=Depends(get_cmp_repository),
    auth_repo=Depends(get_auth_repository_for_role_check),
):
    # REQ-CMP-003: مدير النظام حصريًا
    role = auth_repo.get_user_role(current_session.user_id)
    if role not in SYSTEM_ADMIN_ROLES:
        raise error(correlation_id, status.HTTP_403_FORBIDDEN, "FORBIDDEN", "هذه العملية تتطلب صلاحية مدير النظام.")

    try:
        record = archive_compatibility_record_via_repository(cmp_repo, record_id=record_id)
    except CompatibilityRecordNotFoundError as exc:
        raise error(correlation_id, status.HTTP_404_NOT_FOUND, "RECORD_NOT_FOUND", str(exc))
    except InvalidCompatibilityStatusError as exc:
        raise error(correlation_id, status.HTTP_409_CONFLICT, "INVALID_STATUS_TRANSITION", str(exc))
    return _to_response(record)
