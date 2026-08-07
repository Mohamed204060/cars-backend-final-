"""
ref_api.py — طبقة REST API لخدمة البيانات المرجعية (REF)
المرجع: openapi.yaml الأصلي (POST /reference-data/{type}/bulk-import/preview
        معتمَد سلفًا ضمن الشريحة الأولى) + Final Backend Batch Contract Extension.

REQ-REF-001/002: إضافة/أرشفة قيمة مرجعية — مدير النظام حصريًا.
صيغة ملف xlsx المفترَضة: صف عناوين أول، ثم عمود واحد باسم "code" (راجع
ref_service.py للتفصيل والمبرر).
"""

import io
from typing import Optional

import openpyxl
from fastapi import APIRouter, Depends, File, Request, UploadFile, status
from pydantic import BaseModel

from auth_api import error, get_correlation_id, get_current_session
from pct_api import SYSTEM_ADMIN_ROLES, get_auth_repository_for_role_check
from session_service import Session
from ref_service import (
    DuplicateRefValueError,
    InvalidRefTypeError,
    RefValueArchivedImmutableError,
    archive_ref_value_via_repository,
    create_ref_value_via_repository,
    preview_bulk_import_via_repository,
)

router = APIRouter(prefix="/api/v1/reference-data", tags=["reference-data"])


class RefValueCreateRequest(BaseModel):
    ref_type: str
    code: str


class RefValueResponse(BaseModel):
    id: str
    ref_type: str
    code: str
    status: str


class RejectedRow(BaseModel):
    row_number: int
    reason: str


class BulkImportPreviewResponse(BaseModel):
    job_id: str
    new_count: int
    updated_count: int
    rejected_count: int
    rejected_rows: list[RejectedRow]


def get_ref_repository(request: Request):
    return request.app.state.ref_repository


def _to_response(value) -> RefValueResponse:
    return RefValueResponse(id=value.id, ref_type=value.ref_type, code=value.code, status=value.status)


def _parse_xlsx_rows(file_bytes: bytes) -> list[dict]:
    """
    يقرأ الصف الأول كعناوين أعمدة، وكل صف تالٍ كسجل {عمود: قيمة}. يتوقَّع
    عمود "code" على الأقل (راجع ref_service.classify_import_rows للتحقق).
    """
    workbook = openpyxl.load_workbook(io.BytesIO(file_bytes), read_only=True, data_only=True)
    sheet = workbook.active
    rows_iter = sheet.iter_rows(values_only=True)
    try:
        headers = [str(h).strip() if h is not None else "" for h in next(rows_iter)]
    except StopIteration:
        return []
    parsed = []
    for raw_row in rows_iter:
        if raw_row is None or all(v is None for v in raw_row):
            continue
        row_dict = {headers[i]: (str(raw_row[i]).strip() if i < len(raw_row) and raw_row[i] is not None else "")
                    for i in range(len(headers))}
        parsed.append(row_dict)
    return parsed


@router.get("/{ref_type}", response_model=list[RefValueResponse])
def list_ref_values(
    ref_type: str,
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    ref_repo=Depends(get_ref_repository),
):
    values = ref_repo.get_values_for_type(ref_type, include_archived=False)
    return [_to_response(v) for v in values]


@router.post("", response_model=RefValueResponse, status_code=status.HTTP_201_CREATED)
def create_ref_value(
    body: RefValueCreateRequest,
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    ref_repo=Depends(get_ref_repository),
    auth_repo=Depends(get_auth_repository_for_role_check),
):
    # REQ-REF-001: مدير النظام حصريًا
    role = auth_repo.get_user_role(current_session.user_id)
    if role not in SYSTEM_ADMIN_ROLES:
        raise error(correlation_id, status.HTTP_403_FORBIDDEN, "FORBIDDEN", "إضافة قيمة مرجعية مقصورة على مدير النظام.")

    try:
        value = create_ref_value_via_repository(ref_repo, ref_type=body.ref_type, code=body.code)
    except InvalidRefTypeError as exc:
        raise error(correlation_id, status.HTTP_400_BAD_REQUEST, "INVALID_REF_TYPE", str(exc))
    except ValueError as exc:
        raise error(correlation_id, status.HTTP_400_BAD_REQUEST, "INVALID_CODE", str(exc))
    except DuplicateRefValueError as exc:
        raise error(correlation_id, status.HTTP_409_CONFLICT, "DUPLICATE_REF_VALUE", str(exc))
    return _to_response(value)


@router.post("/{value_id}/archive", response_model=RefValueResponse)
def archive_ref_value(
    value_id: str,
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    ref_repo=Depends(get_ref_repository),
    auth_repo=Depends(get_auth_repository_for_role_check),
):
    # REQ-REF-002: مدير النظام حصريًا
    role = auth_repo.get_user_role(current_session.user_id)
    if role not in SYSTEM_ADMIN_ROLES:
        raise error(correlation_id, status.HTTP_403_FORBIDDEN, "FORBIDDEN", "أرشفة قيمة مرجعية مقصورة على مدير النظام.")

    try:
        value = archive_ref_value_via_repository(ref_repo, value_id=value_id)
    except ValueError as exc:
        raise error(correlation_id, status.HTTP_404_NOT_FOUND, "REF_VALUE_NOT_FOUND", str(exc))
    except RefValueArchivedImmutableError as exc:
        raise error(correlation_id, status.HTTP_409_CONFLICT, "ALREADY_ARCHIVED", str(exc))
    return _to_response(value)


@router.post("/{ref_type}/bulk-import/preview", response_model=BulkImportPreviewResponse)
def preview_bulk_import(
    ref_type: str,
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    ref_repo=Depends(get_ref_repository),
    auth_repo=Depends(get_auth_repository_for_role_check),
    file: UploadFile = File(...),
):
    # صلاحية غير كافية (REQ-REF-004: مدير النظام أو مفوَّض مماثل)؛ SYSTEM_ADMIN_ROLES هنا فقط
    role = auth_repo.get_user_role(current_session.user_id)
    if role not in SYSTEM_ADMIN_ROLES:
        raise error(correlation_id, status.HTTP_403_FORBIDDEN, "FORBIDDEN", "الاستيراد الجماعي مقصور على مدير النظام.")

    if not file.filename.lower().endswith(".xlsx"):
        raise error(correlation_id, status.HTTP_422_UNPROCESSABLE_ENTITY, "UNSUPPORTED_FILE_FORMAT", "صيغة الملف يجب أن تكون .xlsx فقط.")

    try:
        parsed_rows = _parse_xlsx_rows(file.file.read())
    except Exception as exc:
        raise error(correlation_id, status.HTTP_422_UNPROCESSABLE_ENTITY, "UNSUPPORTED_FILE_FORMAT", f"تعذَّرت قراءة الملف: {exc}")

    try:
        job = preview_bulk_import_via_repository(
            ref_repo, ref_type=ref_type, file_name=file.filename,
            imported_by_ref_id=current_session.user_id, parsed_rows=parsed_rows,
        )
    except InvalidRefTypeError as exc:
        raise error(correlation_id, status.HTTP_422_UNPROCESSABLE_ENTITY, "UNSUPPORTED_REF_TYPE", str(exc))

    rejected = [RejectedRow(row_number=r.row_number, reason=r.rejection_reason or "")
                for r in job.rows if r.outcome == "rejected"]
    return BulkImportPreviewResponse(
        job_id=job.id, new_count=job.new_count, updated_count=job.updated_count,
        rejected_count=job.rejected_count, rejected_rows=rejected,
    )
