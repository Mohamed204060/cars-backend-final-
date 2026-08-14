"""
media_api.py — طبقة REST API لخدمة Media Foundation
المرجع: CarsMaint Media Foundation — Approved Baseline v1.0

Unit 1 (Backend Foundation) فقط: رفع/معالجة/جلب Asset، وربطه (Bind) بسياق
Business عبر media.attachments. **لا** تكامل خاص بـPR/Offer/Inventory هنا
(Unit 2) — is_uploader_owner_checker دالة محقونة عامة يُموِّنها المُستدعي
(main.py عند التركيب الفعلي)، نفس نمط SSOT القائم في كل المشروع.

Visibility/Watermark/Signed Access (§9-10): هذه الوحدة تُعيد storage_key
الخام فقط عبر get_asset/list_attachments؛ حل الـURL الفعلي (موقَّع أو
عام، مع/بلا Watermark) مسؤولية طبقة عرض لاحقة تستهلك StorageAdapter +
resolve_visibility_policy — غير مُنفَّذة في Unit 1 (سيُستكمَل مع Unit 2
عند وجود سياق Owner فعلي للتحقق من صلاحية الوصول Signed).
"""

from typing import Optional

from fastapi import APIRouter, Depends, File, Request, UploadFile, status
from pydantic import BaseModel

from auth_api import error, get_correlation_id, get_current_session
from session_service import Session
from media_service import (
    Asset,
    AssetNotReadyForBindingError,
    AttachmentLimitExceededError,
    BindingAuthorizationError,
    ImageDecodeError,
    ImageDimensionsTooLargeError,
    InvalidOwnerTypeError,
    UnsupportedImageFormatError,
    UploadTooLargeError,
    make_storage_key,
    process_image,
    resolve_visibility_policy,
)

router = APIRouter(prefix="/api/v1/media", tags=["media"])


class MediaAssetResponse(BaseModel):
    id: str
    original_file_name: str
    status: str
    mime_type: Optional[str] = None
    size_bytes: Optional[int] = None
    width: Optional[int] = None
    height: Optional[int] = None


class MediaAttachmentCreateRequest(BaseModel):
    asset_ref_id: str
    owner_type: str
    owner_ref_id: str


class MediaAttachmentResponse(BaseModel):
    id: str
    asset_ref_id: str
    owner_type: str
    owner_ref_id: str
    sort_order: int
    status: str


def get_media_repository(request: Request):
    return request.app.state.media_repository


def get_storage_adapter(request: Request):
    return request.app.state.storage_adapter


def get_media_ownership_checker(request: Request):
    """
    §7: (owner_type, owner_ref_id, uploader_user_ref_id) -> bool. يجب
    توصيله صراحةً عند تركيب التطبيق (app.state.media_ownership_checker)
    — نفس نمط SSOT القائم (is_part_approved_checker وغيرها). **Unit 1
    لا تُموِّن تنفيذًا حقيقيًا** (لا Repository لـPR/Offer/Inventory هنا
    بعد) — Unit 2 يحقن الدالة الحقيقية عند التكامل. إن لم يُوصَّل شيء
    صراحةً، الوصول لهذا الـDependency يرفع AttributeError واضحًا (Fail
    Loud لا Fail Silent) بدل افتراض سلوك صامت خاطئ.
    """
    return request.app.state.media_ownership_checker


def _asset_response(asset: Asset) -> MediaAssetResponse:
    return MediaAssetResponse(id=asset.id, original_file_name=asset.original_file_name, status=asset.status,
                               mime_type=asset.mime_type, size_bytes=asset.size_bytes, width=asset.width, height=asset.height)


def _attachment_response(att) -> MediaAttachmentResponse:
    return MediaAttachmentResponse(id=att.id, asset_ref_id=att.asset_ref_id, owner_type=att.owner_type,
                                    owner_ref_id=att.owner_ref_id, sort_order=att.sort_order, status=att.status)


@router.post("/assets", response_model=MediaAssetResponse, status_code=status.HTTP_201_CREATED)
def upload_asset(
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    media_repo=Depends(get_media_repository),
    storage=Depends(get_storage_adapter),
    file: UploadFile = File(...),
):
    """
    §4/§14: Upload منفصل عن Bind عمدًا. المعالجة (§4 الخط الكامل) تُنفَّذ
    Synchronous ضمن نفس الطلب في v1 — لا بنية Async Job مضافة هنا (خارج
    نطاق ما نصَّ عليه Baseline صراحةً؛ لا اختراع بنية غير مطلوبة).
    """
    raw_bytes = file.file.read()

    asset = Asset(id="", original_file_name=file.filename or "unnamed",
                  uploaded_by_user_ref_id=current_session.user_id, status="pending")
    asset = media_repo.insert_asset(asset)

    try:
        result = process_image(raw_bytes)
    except UploadTooLargeError as exc:
        media_repo.update_asset_processing_result(asset.id, "failed")
        raise error(correlation_id, status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "UPLOAD_TOO_LARGE", str(exc))
    except (UnsupportedImageFormatError, ImageDimensionsTooLargeError, ImageDecodeError) as exc:
        media_repo.update_asset_processing_result(asset.id, "failed")
        raise error(correlation_id, status.HTTP_422_UNPROCESSABLE_ENTITY, "IMAGE_PROCESSING_FAILED", str(exc))

    master_key = make_storage_key(asset.id, "master", result.mime_type)
    display_key = make_storage_key(asset.id, "display", result.mime_type)
    thumbnail_key = make_storage_key(asset.id, "thumbnail", result.mime_type)

    import io
    storage.put(master_key, io.BytesIO(result.master_bytes), content_type=result.mime_type)
    storage.put(display_key, io.BytesIO(result.display_bytes), content_type=result.mime_type)
    storage.put(thumbnail_key, io.BytesIO(result.thumbnail_bytes), content_type=result.mime_type)

    asset = media_repo.update_asset_processing_result(
        asset.id, "ready",
        storage_key=master_key, storage_key_display=display_key, storage_key_thumbnail=thumbnail_key,
        mime_type=result.mime_type, size_bytes=len(result.master_bytes), checksum=result.checksum_sha256_hex,
        width=result.width, height=result.height,
    )
    return _asset_response(asset)


@router.get("/assets/{asset_id}", response_model=MediaAssetResponse)
def get_asset(
    asset_id: str,
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    media_repo=Depends(get_media_repository),
):
    asset = media_repo.get_asset_by_id(asset_id)
    if asset is None:
        raise error(correlation_id, status.HTTP_404_NOT_FOUND, "ASSET_NOT_FOUND", "الملف غير موجود.")
    return _asset_response(asset)


@router.post("/attachments", response_model=MediaAttachmentResponse, status_code=status.HTTP_201_CREATED)
def create_attachment(
    body: MediaAttachmentCreateRequest,
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    media_repo=Depends(get_media_repository),
    ownership_checker=Depends(get_media_ownership_checker),
):
    """
    §6/§7/§15: التحقق (uploader/ownership/status=ready/الحد) + العدّ +
    sort_order + الإدراج، كلها ضمن Advisory Transaction Lock واحد داخل
    media_repo.insert_attachment_with_lock — لا منطق مكرَّر هنا.

    ownership_checker مُحقَن عبر get_media_ownership_checker (SSOT، نفس
    نمط is_part_approved_checker القائم) — Unit 1 لا تُموِّن تنفيذًا
    حقيقيًا (لا Repository لـPR/Offer/Inventory هنا)؛ Unit 2 يحقن الدالة
    الفعلية عند التكامل الحقيقي مع تلك الخدمات.
    """
    asset = media_repo.get_asset_by_id(body.asset_ref_id)
    if asset is None:
        raise error(correlation_id, status.HTTP_404_NOT_FOUND, "ASSET_NOT_FOUND", "الملف غير موجود.")

    try:
        attachment = media_repo.insert_attachment_with_lock(
            asset, body.owner_type, body.owner_ref_id, ownership_checker,
        )
    except InvalidOwnerTypeError as exc:
        raise error(correlation_id, status.HTTP_400_BAD_REQUEST, "INVALID_OWNER_TYPE", str(exc))
    except AssetNotReadyForBindingError as exc:
        raise error(correlation_id, status.HTTP_409_CONFLICT, "ASSET_NOT_READY", str(exc))
    except BindingAuthorizationError as exc:
        raise error(correlation_id, status.HTTP_403_FORBIDDEN, "BINDING_FORBIDDEN", str(exc))
    except AttachmentLimitExceededError as exc:
        raise error(correlation_id, status.HTTP_409_CONFLICT, "ATTACHMENT_LIMIT_EXCEEDED", str(exc))
    return _attachment_response(attachment)


@router.get("/attachments", response_model=list[MediaAttachmentResponse])
def list_attachments(
    owner_type: str, owner_ref_id: str,
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    media_repo=Depends(get_media_repository),
):
    """§9: هذا المسار يُعيد Metadata فقط (لا URLs) — حل الرؤية/التوقيع
    الفعلي يستوجب سياق Scoping من Unit 2؛ خارج نطاق Unit 1 عمدًا."""
    try:
        items = media_repo.list_attachments_for_owner(owner_type, owner_ref_id)
    except InvalidOwnerTypeError as exc:
        raise error(correlation_id, status.HTTP_400_BAD_REQUEST, "INVALID_OWNER_TYPE", str(exc))
    return [_attachment_response(a) for a in items]


@router.post("/attachments/{attachment_id}/archive", response_model=MediaAttachmentResponse)
def archive_attachment(
    attachment_id: str,
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    media_repo=Depends(get_media_repository),
):
    """§7: قبل Submit فقط (Fail-closed مؤقَّتًا لغياب فحص حالة الطلب/العرض
    الأب في Unit 1 — يُستكمَل في Unit 2)."""
    attachment = media_repo.get_attachment_by_id(attachment_id)
    if attachment is None:
        raise error(correlation_id, status.HTTP_404_NOT_FOUND, "ATTACHMENT_NOT_FOUND", "المرفق غير موجود.")
    updated = media_repo.archive_attachment(attachment_id)
    return _attachment_response(updated)
