"""
media_service.py — منطق خدمة Media Foundation
المرجع: CarsMaint Media Foundation — Approved Baseline v1.0 (الحاكمة)

هذه الخدمة SSOT لدورة حياة الملفات (media.assets) وربطها بسياق الأعمال
(media.attachments). لا تعرف شيئًا عن S3/filesystem (عبر StorageAdapter
المحقون من services/shared/src/storage.py) ولا عن تفاصيل PR/Offer/
Inventory الداخلية (owner_ref_id مرجع Polymorphic مجرد فقط).
"""

import hashlib
import io
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List, Tuple

from PIL import Image, ImageOps


# ---------------------------------------------------------------------------
# §12: Image Policy — القيم المعتمَدة حرفيًا، لا قيم مُخترَعة
# ---------------------------------------------------------------------------
MAX_UPLOAD_SIZE_BYTES = 10 * 1024 * 1024       # 10MB
MAX_INPUT_DIMENSION_PX = 8000                   # 8000px input max (عرض أو ارتفاع)
DISPLAY_MAX_DIMENSION_PX = 1600                 # 1600px display
THUMBNAIL_MAX_DIMENSION_PX = 300                # 300px thumbnail
JPEG_QUALITY = 80

VALID_ASSET_STATUSES = {"pending", "processing", "ready", "failed", "archived"}
ASSET_STATUS_TRANSITIONS = {
    "pending": {"processing", "failed"},
    "processing": {"ready", "failed"},
    "ready": {"archived"},
    "failed": {"archived"},  # مسموح أرشفة أصل فاشل (تنظيف)، لا إعادة معالجة تلقائية
    "archived": set(),
}

VALID_OWNER_TYPES = {"purchase_request", "offer", "inventory_item", "article"}
VALID_ATTACHMENT_STATUSES = {"active", "archived"}

# §6: حدود الصور النشطة لكل نوع Owner. inventory_item: لا حد جديد معتمَد (None = بلا حد يفرضه هذا الإصدار)
# article (CMS — Master Handoff §8): صورة بارزة واحدة فقط — لا معرض صور لمقال.
MAX_ACTIVE_ATTACHMENTS_PER_OWNER = {
    "purchase_request": 5,
    "offer": 5,
    "inventory_item": None,
    "article": 1,
}

# §9: Visibility/Watermark تُشتَق من owner_type وقت القراءة — لا تُخزَّن أبدًا
# article: عام (مقال منشور محتوى تحريري عام) وبلا Watermark (ليست صورة
# منتج سوقي تحتاج حماية ملكية — نفس منطق عدم Watermark لأي محتوى تحريري).
OWNER_TYPE_VISIBILITY_POLICY = {
    "purchase_request": {"public": False, "watermark": False},
    "offer": {"public": False, "watermark": False},
    "inventory_item": {"public": True, "watermark": True},
    "article": {"public": True, "watermark": False},
}


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class UnsupportedImageFormatError(Exception):
    """الصيغة غير مدعومة (بما فيها HEIC/HEIF — §12: بلا اعتماد حتى إثبات Decoder حقيقي عبر CI)."""


class UploadTooLargeError(Exception):
    """تجاوز MAX_UPLOAD_SIZE_BYTES."""


class ImageDimensionsTooLargeError(Exception):
    """تجاوز MAX_INPUT_DIMENSION_PX."""


class ImageDecodeError(Exception):
    """فشل Decode فعلي — Magic Bytes قد تكون صحيحة لكن المحتوى تالف (§12: Decode failure => failed)."""


class InvalidAssetStatusTransitionError(Exception):
    pass


class AssetNotReadyForBindingError(Exception):
    """§7/§14: لا Binding قبل status == ready."""


class BindingAuthorizationError(Exception):
    """§7: uploader فقط، ويملك Business Entity المستهدف فعليًا."""


class AttachmentLimitExceededError(Exception):
    """§6: حد 5 صور active لـPR/Offer."""


class InvalidOwnerTypeError(Exception):
    pass


# ---------------------------------------------------------------------------
# Dataclasses — media.assets / media.attachments حرفيًا
# ---------------------------------------------------------------------------

@dataclass
class Asset:
    id: str
    original_file_name: str
    uploaded_by_user_ref_id: str
    status: str = "pending"
    storage_key: Optional[str] = None
    storage_key_display: Optional[str] = None
    storage_key_thumbnail: Optional[str] = None
    mime_type: Optional[str] = None
    size_bytes: Optional[int] = None
    checksum: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    created_at: Optional[datetime] = None
    archived_at: Optional[datetime] = None
    purged_at: Optional[datetime] = None


@dataclass
class Attachment:
    id: str
    asset_ref_id: str
    owner_type: str
    owner_ref_id: str
    sort_order: int
    status: str = "active"
    created_at: Optional[datetime] = None


@dataclass
class ProcessedImage:
    """نتيجة خط المعالجة الكامل (§4) — جاهزة للكتابة عبر StorageAdapter."""
    master_bytes: bytes
    display_bytes: bytes
    thumbnail_bytes: bytes
    width: int
    height: int
    mime_type: str
    checksum_sha256_hex: str


# ---------------------------------------------------------------------------
# §12: Magic Bytes — تحديد الصيغة الحقيقية من محتوى الملف، لا من الامتداد
# أو MIME المُعلَن (§13: "MIME المعلن غير حاكم")
# ---------------------------------------------------------------------------

def detect_magic_bytes(data: bytes) -> Optional[str]:
    """يُعيد 'jpeg' | 'png' | 'webp' | 'heic' | None (غير معروف)."""
    if len(data) < 12:
        return None
    if data[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    # HEIC/HEIF: ISO Base Media (ftyp box) بأنواع علامات heic/heix/hevc/mif1/msf1 عند offset 4
    if data[4:8] == b"ftyp" and data[8:12] in (b"heic", b"heix", b"hevc", b"hevx", b"mif1", b"msf1"):
        return "heic"
    return None


SUPPORTED_FORMATS_FOR_PROCESSING = {"jpeg", "png", "webp"}  # §12: HEIC/HEIF مرفوضة صراحةً في v1


def validate_upload_size(size_bytes: int) -> None:
    if size_bytes > MAX_UPLOAD_SIZE_BYTES:
        raise UploadTooLargeError(
            f"حجم الملف {size_bytes} بايت يتجاوز الحد الأقصى {MAX_UPLOAD_SIZE_BYTES} بايت (10MB)."
        )


def _resize_within(img: "Image.Image", max_dimension: int) -> "Image.Image":
    """تصغير متناسب (Aspect Ratio محفوظ) بحيث لا يتجاوز أي بُعد max_dimension. لا تكبير أبدًا."""
    w, h = img.size
    if w <= max_dimension and h <= max_dimension:
        return img.copy()
    ratio = min(max_dimension / w, max_dimension / h)
    new_size = (max(1, round(w * ratio)), max(1, round(h * ratio)))
    return img.resize(new_size, Image.LANCZOS)


def _encode(img: "Image.Image", fmt: str) -> bytes:
    buf = io.BytesIO()
    if fmt == "PNG":
        img.save(buf, format="PNG", optimize=True)
    else:
        # JPEG لا يدعم Alpha — يُحوَّل لخلفية بيضاء إن لزم (حالة نادرة: PNG بلا Alpha حقيقي أُعيد ترميزه JPEG)
        if img.mode in ("RGBA", "LA", "P"):
            img = img.convert("RGB")
        img.save(buf, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    return buf.getvalue()


def _has_real_alpha(img: "Image.Image") -> bool:
    """§12: PNG مع Alpha حقيقي (قناة شفافية فعلية، لا قناة كاملة الصلابة) يبقى PNG."""
    if img.mode not in ("RGBA", "LA", "PA"):
        if img.mode == "P" and "transparency" in img.info:
            return True
        return False
    alpha = img.convert("RGBA").split()[-1]
    return alpha.getextrema()[0] < 255  # أي بكسل بشفافية فعلية (ليس كله 255=معتم تمامًا)


def process_image(raw_bytes: bytes) -> ProcessedImage:
    """
    خط المعالجة الكامل (§4): Magic Bytes → dimensions/size → Decode → EXIF
    orientation → Strip EXIF → Re-encode → Sanitized Master → Display →
    Thumbnail. يرفع استثناءً محدَّدًا عند أي فشل (§12: Decode failure => failed
    — يُترجَم لاحقًا في طبقة الخدمة/API إلى status='failed'، لا استثناء عام).
    """
    validate_upload_size(len(raw_bytes))

    fmt = detect_magic_bytes(raw_bytes)
    if fmt is None or fmt not in SUPPORTED_FORMATS_FOR_PROCESSING:
        raise UnsupportedImageFormatError(
            f"صيغة غير مدعومة أو غير معروفة (Magic Bytes: {fmt!r}). "
            f"المدعوم في v1: JPEG/PNG/WebP فقط — لا HEIC/HEIF حتى إثبات Decoder حقيقي عبر CI (§12)."
        )

    try:
        img = Image.open(io.BytesIO(raw_bytes))
        img.load()  # فرض Decode فعلي الآن، لا كسول — يكشف الملفات التالفة فورًا
    except Exception as exc:
        raise ImageDecodeError(f"فشل Decode فعلي رغم صحة Magic Bytes الظاهرية: {exc}") from exc

    if img.width > MAX_INPUT_DIMENSION_PX or img.height > MAX_INPUT_DIMENSION_PX:
        raise ImageDimensionsTooLargeError(
            f"أبعاد الصورة {img.width}x{img.height} تتجاوز الحد الأقصى {MAX_INPUT_DIMENSION_PX}px."
        )

    # EXIF orientation: تصحيح الدوران الفعلي حسب بيانات EXIF قبل حذفها (§4)
    img = ImageOps.exif_transpose(img)

    keep_png = fmt == "png" and _has_real_alpha(img)
    target_format = "PNG" if keep_png else "JPEG"
    target_mime = "image/png" if keep_png else "image/jpeg"

    # Strip EXIF + Re-encode: الحفظ عبر _encode لا يمرر exif إطلاقًا (Strip كامل ضمنيًا،
    # نُنشئ Buffer جديدًا تمامًا من بيانات البكسل فقط، لا من الملف الأصلي)
    master_img = img.copy()
    master_bytes = _encode(master_img, target_format)

    display_img = _resize_within(img, DISPLAY_MAX_DIMENSION_PX)
    display_bytes = _encode(display_img, target_format)

    thumb_img = _resize_within(img, THUMBNAIL_MAX_DIMENSION_PX)
    thumbnail_bytes = _encode(thumb_img, target_format)

    checksum = hashlib.sha256(master_bytes).hexdigest()

    return ProcessedImage(
        master_bytes=master_bytes, display_bytes=display_bytes, thumbnail_bytes=thumbnail_bytes,
        width=img.width, height=img.height, mime_type=target_mime, checksum_sha256_hex=checksum,
    )


def make_storage_key(asset_id: str, variant: str, mime_type: str) -> str:
    """
    §4: Storage keys داخلية UUID/random بالكامل؛ لا علاقة بالاسم الأصلي.
    نستخدم asset_id (UUID مُولَّد أصلًا من قاعدة البيانات) + variant، لا
    اسمًا عشوائيًا إضافيًا — يكفي لضمان التفرُّد ولمنع التخمين.
    """
    ext = "png" if mime_type == "image/png" else "jpg"
    return f"assets/{asset_id}/{variant}.{ext}"


# ---------------------------------------------------------------------------
# §9 (Watermark) — Batch 2 Unit 2. يُطبَّق حصرًا على Derived Display لـ
# inventory_item فقط (Public)، أبدًا على Master (Private دائمًا)، أبدًا على
# PR/Offer (Private + no watermark). التوقيت: عند Bind فعليًا (owner_type
# يُعرَف فقط حينها، لا وقت Upload) — يُعاد ترميز display/thumbnail بعلامة
# مائية ويُستبدَل نفس storage_key الأصلي عبر Storage.put (لا Migration
# جديدة، لا عمود إضافي — نفس المفتاح، محتوى جديد).
# ---------------------------------------------------------------------------

WATERMARK_TEXT = "CarsMaint"
WATERMARK_OPACITY = 96  # من 255 — شفافية جزئية، لا يحجب الصورة


def apply_watermark(image_bytes: bytes, mime_type: str) -> bytes:
    """
    يرسم علامة مائية نصية متكررة قطريًا بشفافية جزئية فوق الصورة. يُعيد
    ترميزًا كاملًا جديدًا (نفس صيغة الإدخال: JPEG يبقى JPEG بلا Alpha؛ PNG
    يبقى PNG محتفظًا بأي Alpha أصلي).
    """
    from PIL import ImageDraw, ImageFont

    img = Image.open(io.BytesIO(image_bytes))
    img = img.convert("RGBA") if img.mode != "RGBA" else img.copy()

    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    try:
        font = ImageFont.load_default()
    except Exception:
        font = None

    step_x, step_y = 180, 120
    for y in range(0, img.height + step_y, step_y):
        for x in range(0, img.width + step_x, step_x):
            draw.text((x, y), WATERMARK_TEXT, fill=(255, 255, 255, WATERMARK_OPACITY), font=font)

    watermarked = Image.alpha_composite(img, overlay)

    if mime_type == "image/png":
        return _encode(watermarked, "PNG")
    return _encode(watermarked.convert("RGB"), "JPEG")


# ---------------------------------------------------------------------------
# دورة الحياة (§3) — تحقق انتقالات الحالة فقط، بلا أي وصول لقاعدة بيانات
# ---------------------------------------------------------------------------

def validate_status_transition(current: str, new: str) -> None:
    if new not in ASSET_STATUS_TRANSITIONS.get(current, set()):
        raise InvalidAssetStatusTransitionError(f"انتقال حالة غير مسموح: {current} → {new}")


# ---------------------------------------------------------------------------
# §7: Binding Authorization + §6 Limits — منطق أعمال خالص (SSOT عبر دوال
# محقونة، بنفس نمط is_part_approved_checker القائم في كل المشروع). القفل
# والمعاملة الفعليان (§6/§15) في media_repository.py، لا هنا.
# ---------------------------------------------------------------------------

def validate_owner_type(owner_type: str) -> None:
    if owner_type not in VALID_OWNER_TYPES:
        raise InvalidOwnerTypeError(f"owner_type غير معروف: {owner_type!r}. المسموح: {sorted(VALID_OWNER_TYPES)}")


def create_attachment(
    asset: Asset, owner_type: str, owner_ref_id: str,
    is_uploader_owner_checker,  # (owner_type, owner_ref_id, uploader_user_ref_id) -> bool — يتحقق ownership فعليًا عبر PR/Offer/Inventory
    current_active_count: int,  # يُمرَّر من Repository (COUNT فعلي تحت القفل، §6) — لا استعلام هنا
    sort_order: int,            # MAX(sort_order)+1 محسوب من Repository تحت نفس القفل
) -> Attachment:
    """
    §7: uploader فقط + يملك Business Entity المستهدف فعليًا + asset.status
    == ready. §6: الحد يُتحقَّق منه هنا كمنطق أعمال خالص، لكن count نفسه
    يجب أن يكون قد حُسِب بالفعل تحت القفل في الطبقة الأدنى (Repository) —
    هذه الدالة لا تفتح أي قفل أو معاملة بنفسها.
    """
    validate_owner_type(owner_type)

    if asset.status != "ready":
        raise AssetNotReadyForBindingError(
            f"الأصل '{asset.id}' بحالة '{asset.status}' — لا يمكن الربط قبل الوصول لحالة 'ready' (§7/§14)."
        )

    if not is_uploader_owner_checker(owner_type, owner_ref_id, asset.uploaded_by_user_ref_id):
        raise BindingAuthorizationError(
            "الربط مرفوض: المستخدم يجب أن يكون هو من رفع الملف وأن يملك الكيان المستهدف فعليًا (§7)."
        )

    limit = MAX_ACTIVE_ATTACHMENTS_PER_OWNER.get(owner_type)
    if limit is not None and current_active_count >= limit:
        raise AttachmentLimitExceededError(
            f"تجاوز الحد الأقصى للصور النشطة ({limit}) لـ{owner_type} '{owner_ref_id}' (§6)."
        )

    return Attachment(id="", asset_ref_id=asset.id, owner_type=owner_type,
                       owner_ref_id=owner_ref_id, sort_order=sort_order)


def resolve_visibility_policy(owner_type: str) -> dict:
    """§9: يُشتَق من owner_type وقت القراءة فقط — لا يُخزَّن أبدًا في أي جدول."""
    validate_owner_type(owner_type)
    return dict(OWNER_TYPE_VISIBILITY_POLICY[owner_type])
