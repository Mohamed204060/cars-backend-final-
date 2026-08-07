"""
trm_service.py — منطق خدمة الثقة والتقييمات (TRM)
المرجع: SRS الحزمة E (REQ-TRM-001..007)؛ CR-009 (تقييم تجربة الشراء)

مبدأ التصميم الموحَّد: بدلاً من ثلاثة كيانات منفصلة تمامًا لثلاثة أنواع
تقييم، يُبنى نموذج واحد (Rating) بمميِّز نوع (target_type)، بحيث تُدرَس
العلاقة بين الأنواع الثلاثة معًا منذ البداية دون تكرار منطق، وتُضاف أنواع
تقييم جديدة مستقبلاً (كتقييم قناة التواصل مثلاً) دون أي إعادة تصميم —
فقط بإضافة قيمة جديدة لمُميِّز النوع.

SSOT: كل تقييم يشير لهدفه (بائع/متجر/تجربة شراء) ولطلب الشراء المصدر
الذي يخوِّل التقييم، عبر معرّفات مرجعية فقط؛ لا نسخ بيانات من IAM أو STR
أو PUR على الإطلاق. التحقق من الأهلية (هل أتمّ المستخدم فعلاً هذه الصفقة؟)
يتم حصرًا عبر دالة محقونة (Dependency Injection)، لا استيرادًا مباشرًا
لأي خدمة أعمال أخرى.

مبدأ عدم الحذف الفعلي: التقييمات سجل ثقة تاريخي؛ الإزالة عبر الأرشفة فقط.
"""

from dataclasses import dataclass
from typing import Optional, List
from datetime import datetime


RATING_TARGET_TYPES = {"seller", "store", "purchase_experience"}
RATING_STATUSES = {"active", "archived"}
MIN_SCORE = 1
MAX_SCORE = 5


@dataclass
class Rating:
    id: str
    rated_by_user_ref_id: str          # SSOT: مرجع IAM فقط (من قام بالتقييم)
    target_type: str                    # seller | store | purchase_experience (قابل للتوسع مستقبلاً)
    target_ref_id: str                  # SSOT: مرجع فقط (user_ref_id للبائع، store_ref_id للمتجر، purchase_request_ref_id لتجربة الشراء)
    source_purchase_request_ref_id: str  # SSOT: مرجع PUR — الصفقة التي تخوِّل هذا التقييم؛ إلزامي دائمًا
    score: int
    comment: Optional[str] = None
    status: str = "active"
    created_at: Optional[datetime] = None


class InvalidTargetTypeError(Exception):
    """نوع هدف تقييم غير معروف."""


class InvalidScoreError(Exception):
    """درجة التقييم يجب أن تكون ضمن المدى المسموح."""


class DuplicateRatingError(Exception):
    """منع التقييم المكرَّر لنفس الهدف من نفس المستخدم عن نفس الصفقة المصدر."""


class RatingIneligibleError(Exception):
    """المستخدم غير مؤهَّل لتقييم هذا الهدف عن هذه الصفقة (فحص عبر دالة محقونة)."""


class RatingArchivedImmutableError(Exception):
    """لا تعديل على تقييم مؤرشَف."""


# ---------------------------------------------------------------------------
# إنشاء تقييم موحَّد (لأي نوع من الأنواع الثلاثة، بنفس المسار والتحقق)
# ---------------------------------------------------------------------------

def create_rating(
    rated_by_user_ref_id: str,
    target_type: str,
    target_ref_id: str,
    source_purchase_request_ref_id: str,
    score: int,
    existing_ratings_for_source: List[Rating],
    is_eligible_checker,  # Callable[[str, str, str], bool] محقونة: (user_ref_id, target_type, purchase_request_ref_id) -> bool
    comment: Optional[str] = None,
) -> Rating:
    if target_type not in RATING_TARGET_TYPES:
        raise InvalidTargetTypeError(f"نوع هدف تقييم غير معروف: {target_type}")
    if not (MIN_SCORE <= score <= MAX_SCORE):
        raise InvalidScoreError(f"الدرجة يجب أن تكون بين {MIN_SCORE} و{MAX_SCORE}.")

    # REQ جديد (CR-009 وامتداداته): التحقق من الأهلية عبر دالة محقونة فقط،
    # لا استيراد مباشر لخدمة PUR أو STR أو IAM من هنا
    if not is_eligible_checker(rated_by_user_ref_id, target_type, source_purchase_request_ref_id):
        raise RatingIneligibleError(
            "المستخدم غير مؤهَّل لتقييم هذا الهدف عن هذه الصفقة تحديدًا."
        )

    # منع التكرار: نفس المُقيِّم + نفس الهدف + نفس الصفقة المصدر
    for existing in existing_ratings_for_source:
        if (existing.rated_by_user_ref_id == rated_by_user_ref_id
                and existing.target_type == target_type
                and existing.target_ref_id == target_ref_id
                and existing.source_purchase_request_ref_id == source_purchase_request_ref_id):
            raise DuplicateRatingError(
                "يوجد بالفعل تقييم لهذا الهدف من هذا المستخدم عن هذه الصفقة."
            )

    return Rating(
        id="", rated_by_user_ref_id=rated_by_user_ref_id, target_type=target_type,
        target_ref_id=target_ref_id, source_purchase_request_ref_id=source_purchase_request_ref_id,
        score=score, comment=comment,
    )


# ---------------------------------------------------------------------------
# تعديل وأرشفة (لا حذف فعلي أبدًا)
# ---------------------------------------------------------------------------

def ensure_rating_modifiable(rating: Rating) -> None:
    if rating.status == "archived":
        raise RatingArchivedImmutableError("لا يجوز تعديل تقييم مؤرشَف.")


def update_rating_score_and_comment(rating: Rating, new_score: int, new_comment: Optional[str] = None) -> Rating:
    ensure_rating_modifiable(rating)
    if not (MIN_SCORE <= new_score <= MAX_SCORE):
        raise InvalidScoreError(f"الدرجة يجب أن تكون بين {MIN_SCORE} و{MAX_SCORE}.")
    rating.score = new_score
    if new_comment is not None:
        rating.comment = new_comment
    return rating


def archive_rating(rating: Rating) -> Rating:
    """الإزالة الوحيدة المسموحة لتقييم؛ لا حذف فعلي مطلقًا."""
    ensure_rating_modifiable(rating)
    rating.status = "archived"
    return rating


# ---------------------------------------------------------------------------
# حساب متوسط تقييم لهدف معيَّن (Read Model بسيط، لا تخزين منفصل له)
# ---------------------------------------------------------------------------

def compute_average_score(ratings_for_target: List[Rating]) -> Optional[float]:
    active_ratings = [r for r in ratings_for_target if r.status == "active"]
    if not active_ratings:
        return None
    return sum(r.score for r in active_ratings) / len(active_ratings)


# ---------------------------------------------------------------------------
# الإبلاغ عن تقييم مخالف (تكامل مع مفهوم البلاغات القائم لا آلية جديدة)
# ---------------------------------------------------------------------------

def build_rating_report_event(rating_id: str, reported_by_user_ref_id: str, reason: str) -> dict:
    """
    يبني وصف بلاغ جاهزًا للتكامل مع آلية البلاغات القائمة أصلاً (REQ-TRM-005)؛
    لا يُنشئ آلية إبلاغ مستقلة جديدة لتقييمات TRM.
    """
    if not reason or not reason.strip():
        raise ValueError("سبب البلاغ يجب ألا يكون فارغًا.")
    return {"report_target_type": "rating", "report_target_ref_id": rating_id,
            "reported_by_user_ref_id": reported_by_user_ref_id, "reason": reason}


# ---------------------------------------------------------------------------
# سجل التدقيق (Audit Trail) لكل عملية حساسة
# ---------------------------------------------------------------------------

def build_administrative_audit_event(action: str, actor_ref_id: str, rating_id: str,
                                      reason: Optional[str] = None) -> dict:
    allowed_actions = {"rating_created", "rating_updated", "rating_archived", "rating_reported"}
    if action not in allowed_actions:
        raise ValueError(f"نوع حدث غير معروف: {action}")
    return {"log_type": "general", "event_name": action, "actor_ref_id": actor_ref_id,
            "metadata": {"rating_id": rating_id, "reason": reason}}


# ---------------------------------------------------------------------------
# نقاط تجميع تعتمد على طبقة Repository (دليل حوكمة التنفيذ v1.3/1.4/1.7)
# ---------------------------------------------------------------------------

def create_rating_via_repository(
    repository, rated_by_user_ref_id: str, target_type: str, target_ref_id: str,
    source_purchase_request_ref_id: str, score: int, is_eligible_checker, comment=None,
) -> Rating:
    existing = repository.get_ratings_for_source_purchase_request(source_purchase_request_ref_id)
    rating = create_rating(rated_by_user_ref_id, target_type, target_ref_id,
                            source_purchase_request_ref_id, score, existing, is_eligible_checker, comment)
    return repository.insert_rating(rating)


def archive_rating_via_repository(repository, rating_id: str) -> Rating:
    rating = repository.get_rating_by_id(rating_id)
    if rating is None:
        raise ValueError(f"لا يوجد تقييم بالمعرّف: {rating_id}")
    archive_rating(rating)
    return repository.update_rating(rating)


def get_average_score_via_repository(repository, target_type: str, target_ref_id: str) -> Optional[float]:
    ratings = repository.get_ratings_for_target(target_type, target_ref_id)
    return compute_average_score(ratings)
