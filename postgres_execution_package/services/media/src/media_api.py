"""
media_api.py — طبقة REST API لخدمة Media Foundation
المرجع: CarsMaint Media Foundation — Approved Baseline v1.0

Unit 1: رفع/معالجة/جلب Asset، وربطه (Bind) بسياق Business.
Unit 2 (هذه الدفعة): Ownership الحقيقي (PR/Offer/Inventory الفعلية)،
Watermark فعلي لـinventory_item عند الربط، ومسار Signed/Public Access
(§9-10) وفق مصفوفة الصلاحيات المعتمَدة.
"""

import io
from typing import Optional

from fastapi import APIRouter, Depends, File, Request, UploadFile, status
from pydantic import BaseModel

from auth_api import error, get_correlation_id, get_current_session
from pct_api import SYSTEM_ADMIN_ROLES, get_auth_repository_for_role_check
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
    apply_watermark,
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


class MediaAccessResponse(BaseModel):
    attachment_id: str
    public: bool
    watermarked: bool
    display_url: str
    thumbnail_url: str


def get_media_repository(request: Request):
    return request.app.state.media_repository


def get_storage_adapter(request: Request):
    return request.app.state.storage_adapter


def get_media_ownership_checker(request: Request):
    """
    §7 (Binding Authorization): (owner_type, owner_ref_id, uploader_user_ref_id)
    -> bool. يجب توصيله صراحةً عند تركيب التطبيق (app.state.media_ownership_checker)
    — نفس نمط SSOT القائم (is_part_approved_checker وغيرها). Batch 2 Unit 2:
    التنفيذ الحقيقي في media_authorization.build_media_ownership_checker،
    يُوصَّل عند تركيب التطبيق. إن لم يُوصَّل شيء صراحةً، الوصول لهذا
    الـDependency يرفع AttributeError واضحًا (Fail Loud لا Fail Silent).
    """
    return request.app.state.media_ownership_checker


def get_media_view_authorization_checker(request: Request):
    """
    §10 (Signed Access): (owner_type, owner_ref_id, requester_user_ref_id,
    is_admin) -> bool. يخص Private فقط (purchase_request/offer) — لا
    يُستدعى لـinventory_item (Public، §9). التنفيذ الحقيقي في
    media_authorization.build_media_view_authorization_checker.
    """
    return request.app.state.media_view_authorization_checker


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

    Watermark (§9) **لا** يُطبَّق هنا — owner_type غير معروف بعد وقت
    الرفع (Upload وBind منفصلان عمدًا، §14)؛ يُطبَّق عند Bind فقط إذا
    كان الهدف inventory_item (انظر create_attachment أدناه).
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
    storage=Depends(get_storage_adapter),
    ownership_checker=Depends(get_media_ownership_checker),
):
    """
    §6/§7/§15: التحقق (uploader/ownership/status=ready/الحد) + العدّ +
    sort_order + الإدراج، كلها ضمن Advisory Transaction Lock واحد داخل
    media_repo.insert_attachment_with_lock — لا منطق مكرَّر هنا.

    §9 (Watermark، Batch 2 Unit 2): إن كان owner_type == inventory_item،
    يُعاد ترميز Display/Thumbnail بعلامة مائية فعليًا **بعد** نجاح الربط
    (خارج نطاق القفل — عملية Storage/CPU لا علاقة لها بذرّية القفل، ولا
    تُبطئ تسلسل الطلبات المتزامنة الأخرى على نفس Owner). يُستبدَل نفس
    storage_key_display/_thumbnail الأصليَّين (لا مفتاح جديد، لا Migration).
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

    policy = resolve_visibility_policy(body.owner_type)
    if policy["watermark"]:
        try:
            display_bytes = storage.read(asset.storage_key_display)
            thumb_bytes = storage.read(asset.storage_key_thumbnail)
        except Exception:
            display_bytes = thumb_bytes = None
        if display_bytes is not None and thumb_bytes is not None:
            watermarked_display = apply_watermark(display_bytes, asset.mime_type)
            watermarked_thumb = apply_watermark(thumb_bytes, asset.mime_type)
            storage.put(asset.storage_key_display, io.BytesIO(watermarked_display), content_type=asset.mime_type)
            storage.put(asset.storage_key_thumbnail, io.BytesIO(watermarked_thumb), content_type=asset.mime_type)
            media_repo.update_asset_display_variants(asset.id, asset.storage_key_display, asset.storage_key_thumbnail)

    return _attachment_response(attachment)


@router.get("/attachments", response_model=list[MediaAttachmentResponse])
def list_attachments(
    owner_type: str, owner_ref_id: str,
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    media_repo=Depends(get_media_repository),
):
    """§9: هذا المسار يُعيد Metadata فقط (لا URLs) — لحل الرؤية/التوقيع
    الفعلي استخدموا GET /attachments/{id}/access."""
    try:
        items = media_repo.list_attachments_for_owner(owner_type, owner_ref_id)
    except InvalidOwnerTypeError as exc:
        raise error(correlation_id, status.HTTP_400_BAD_REQUEST, "INVALID_OWNER_TYPE", str(exc))
    return [_attachment_response(a) for a in items]


@router.get("/attachments/{attachment_id}/access", response_model=MediaAccessResponse)
def get_attachment_access(
    attachment_id: str,
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    media_repo=Depends(get_media_repository),
    storage=Depends(get_storage_adapter),
    auth_repo=Depends(get_auth_repository_for_role_check),
    view_checker=Depends(get_media_view_authorization_checker),
):
    """
    §9-10 (Batch 2 Unit 2): يحل الرؤية الفعلية:
    - inventory_item: Public دائمًا (بلا فحص تفويض إضافي — أي مستخدم
      مسجَّل يراه؛ Watermark مُطبَّق مسبقًا على Display/Thumbnail وقت
      الربط) — رابط عام دائم، بلا TTL.
    - purchase_request/offer: Private — يتطلب اجتياز مصفوفة §10 (صاحب
      الطلب/العرض المرتبط/بائع لديه عرض فعلي/Admin) عبر view_checker،
      وإلا 403. رابط موقَّع (TTL) عبر MEDIA_SIGNED_URL_TTL_SECONDS.
    """
    import os
    attachment = media_repo.get_attachment_by_id(attachment_id)
    if attachment is None:
        raise error(correlation_id, status.HTTP_404_NOT_FOUND, "ATTACHMENT_NOT_FOUND", "المرفق غير موجود.")

    asset = media_repo.get_asset_by_id(attachment.asset_ref_id)
    if asset is None:
        raise error(correlation_id, status.HTTP_404_NOT_FOUND, "ASSET_NOT_FOUND", "الملف غير موجود.")

    policy = resolve_visibility_policy(attachment.owner_type)

    if not policy["public"]:
        role = auth_repo.get_user_role(current_session.user_id)
        is_admin = role in SYSTEM_ADMIN_ROLES
        authorized = view_checker(attachment.owner_type, attachment.owner_ref_id, current_session.user_id, is_admin)
        if not authorized:
            raise error(correlation_id, status.HTTP_403_FORBIDDEN, "ACCESS_FORBIDDEN",
                        "لا صلاحية لعرض هذا المرفق — مجرد تسجيل الدخول أو الدور لا يكفي (§10).")
        ttl = int(os.environ.get("MEDIA_SIGNED_URL_TTL_SECONDS", "900"))
        display_url = storage.get_private_access_url(asset.storage_key_display, ttl)
        thumbnail_url = storage.get_private_access_url(asset.storage_key_thumbnail, ttl)
    else:
        display_url = storage.get_public_url(asset.storage_key_display)
        thumbnail_url = storage.get_public_url(asset.storage_key_thumbnail)

    return MediaAccessResponse(
        attachment_id=attachment.id, public=policy["public"], watermarked=policy["watermark"],
        display_url=display_url, thumbnail_url=thumbnail_url,
    )


@router.post("/attachments/{attachment_id}/archive", response_model=MediaAttachmentResponse)
def archive_attachment(
    attachment_id: str,
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    media_repo=Depends(get_media_repository),
    ownership_checker=Depends(get_media_ownership_checker),
):
    """
    §7 (Batch 2 Unit 2): قبل Submit فقط — يتطلب نفس تفويض الربط (المستخدم
    يملك Business Entity المستهدف فعليًا)؛ لا فحص حالة "قبل Submit" هنا
    تحديدًا (لا حقل حالة إرسال نهائي في media.attachments نفسها — الحالة
    الفعلية لـPR/Offer مسؤولية تلك الخدمات، لا Media). Archive منطقي فقط
    — الملفات تبقى (Retention/Audit)، purged_at يبقى NULL حتى Purge مستقل
    لاحق (§11) — لا حذف صامت هنا إطلاقًا.
    """
    attachment = media_repo.get_attachment_by_id(attachment_id)
    if attachment is None:
        raise error(correlation_id, status.HTTP_404_NOT_FOUND, "ATTACHMENT_NOT_FOUND", "المرفق غير موجود.")

    if not ownership_checker(attachment.owner_type, attachment.owner_ref_id, current_session.user_id):
        raise error(correlation_id, status.HTTP_403_FORBIDDEN, "ARCHIVE_FORBIDDEN",
                    "لا صلاحية لأرشفة هذا المرفق — يلزم امتلاك Business Entity المستهدف فعليًا (§7).")

    updated = media_repo.archive_attachment(attachment_id)
    return _attachment_response(updated)
