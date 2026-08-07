"""
order_service.py — منطق خدمة طلبات الشراء والعروض (PUR)
المرجع: REQ-PUR-001..018 (يشمل CR-002: تعديل عرض السعر قبل القبول فقط)

SSOT: يشير الطلب لقطعة الكتالوج وفئة السيارة بمعرّفات مرجعية فقط
(catalog_part_ref_id، trim_ref_id)، وللبائع بمعرّف متجر مرجعي فقط
(seller_store_ref_id)؛ لا نسخ بيانات من أي خدمة أخرى.

مبدأ عدم الحذف الفعلي (دليل الحوكمة 6.6): طلبات الشراء والعروض بيانات
مجال أعمال (سجلات تجارية تاريخية)؛ لا حذف فعلي لأي منهما — الإزالة عبر
تغيير الحالة فقط (cancelled/expired/rejected/withdrawn).
"""

from dataclasses import dataclass
from typing import Optional, List


PR_VALID_STATUSES = {"open", "under_review", "fulfilled", "expired", "cancelled"}
PR_CLOSED_STATUSES = {"fulfilled", "expired", "cancelled"}  # REQ-PUR-017: لا عروض جديدة بعدها

OFFER_VALID_STATUSES = {"submitted", "accepted", "rejected", "withdrawn", "expired"}


@dataclass
class PurchaseRequest:
    id: str
    buyer_user_ref_id: str    # SSOT: إشارة مرجعية فقط لخدمة IAM
    catalog_part_ref_id: str  # SSOT: إشارة مرجعية فقط لخدمة PCT
    trim_ref_id: str          # SSOT: إشارة مرجعية فقط لخدمة VCT
    status: str = "open"
    has_received_offer: bool = False  # CR-002: يقيِّد تعديل الحقول بعد أول عرض
    business_code: Optional[str] = None  # REQ-PUR-015: معرّف أعمال ظاهر؛ يُسنَد عبر Repository


@dataclass
class Offer:
    id: str
    purchase_request_id: str
    seller_store_ref_id: str  # SSOT: إشارة مرجعية فقط لخدمة STR
    amount: float
    currency: str
    provides_shipping: bool
    notes: Optional[str] = None
    status: str = "submitted"
    business_code: Optional[str] = None  # REQ-PUR-015: معرّف أعمال ظاهر؛ يُسنَد عبر Repository


class InvalidPurchaseRequestStatusError(Exception):
    """REQ-PUR-005: انتقال حالة غير مسموح به لطلب الشراء."""


class PurchaseRequestClosedError(Exception):
    """REQ-PUR-017: لا عروض جديدة على طلب مغلَق."""


class PurchaseRequestFieldsLockedError(Exception):
    """CR-002: لا تعديل على حقول الطلب بعد استقبال أول عرض."""


class DuplicateActiveOfferError(Exception):
    """منع أكثر من عرض نشط واحد لنفس البائع على نفس الطلب."""


class OfferNotWithdrawableError(Exception):
    """REQ-PUR-018: سحب العرض مسموح فقط قبل القبول."""


PR_ALLOWED_TRANSITIONS = {
    "open": {"under_review", "cancelled", "expired"},
    "under_review": {"fulfilled", "cancelled", "expired"},
    "fulfilled": set(),
    "expired": set(),
    "cancelled": set(),
}


# ---------------------------------------------------------------------------
# REQ-PUR-001, 002: إنشاء طلب الشراء
# ---------------------------------------------------------------------------

def create_purchase_request(buyer_user_ref_id: str, catalog_part_ref_id: str, trim_ref_id: str,
                             is_part_approved_checker=None) -> PurchaseRequest:
    """
    catalog-only creation: لا يُنشأ الطلب إلا لقطعة كتالوج (لا نص حر)؛ التحقق
    من اعتمادها عبر دالة محقونة اختيارية من PCT (نفس نمط SSOT في الخدمات
    السابقة)، لا استعلامًا مباشرًا.
    """
    if is_part_approved_checker is not None and not is_part_approved_checker(catalog_part_ref_id):
        raise ValueError(f"قطعة الكتالوج المرجعية '{catalog_part_ref_id}' غير معتمدة أو غير موجودة.")
    return PurchaseRequest(id="", buyer_user_ref_id=buyer_user_ref_id,
                            catalog_part_ref_id=catalog_part_ref_id, trim_ref_id=trim_ref_id)


def transition_purchase_request_status(pr: PurchaseRequest, new_status: str) -> PurchaseRequest:
    if new_status not in PR_VALID_STATUSES:
        raise ValueError(f"حالة غير معروفة: {new_status}")
    allowed = PR_ALLOWED_TRANSITIONS.get(pr.status, set())
    if new_status not in allowed:
        raise InvalidPurchaseRequestStatusError(
            f"الانتقال من '{pr.status}' إلى '{new_status}' غير مسموح به."
        )
    pr.status = new_status
    return pr


def cancel_purchase_request(pr: PurchaseRequest) -> PurchaseRequest:
    """REQ-PUR-009: الإلغاء متاح في أي وقت قبل الإتمام."""
    return transition_purchase_request_status(pr, "cancelled")


# ---------------------------------------------------------------------------
# CR-002: تقييد تعديل حقول الطلب بعد أول عرض
# ---------------------------------------------------------------------------

def update_purchase_request_fields(pr: PurchaseRequest, catalog_part_ref_id: Optional[str] = None,
                                    trim_ref_id: Optional[str] = None) -> PurchaseRequest:
    if pr.has_received_offer:
        raise PurchaseRequestFieldsLockedError(
            "لا يجوز تعديل حقول طلب الشراء بعد استقبال أول عرض عليه (CR-002)."
        )
    if catalog_part_ref_id is not None:
        pr.catalog_part_ref_id = catalog_part_ref_id
    if trim_ref_id is not None:
        pr.trim_ref_id = trim_ref_id
    return pr


# ---------------------------------------------------------------------------
# بناء وصف حدث (لا كتابة فعلية هنا؛ AUD هو المرجع الوحيد لتاريخ الطلب —
# لا "Timeline" منفصلة داخل PUR، حفاظًا على SSOT). يعكس أحداث المجال
# المعتمدة أصلاً في Blueprint الحزمة 2: PurchaseRequestCreated، OfferSubmitted،
# OfferAccepted، PurchaseRequestClosed.
# ---------------------------------------------------------------------------

def build_purchase_request_audit_event(action: str, actor_ref_id: str, pr_id: str,
                                        reason: Optional[str] = None):
    allowed_actions = {
        "purchase_request_created", "offer_submitted", "offer_accepted",
        "offer_rejected", "offer_withdrawn", "purchase_request_cancelled",
        "purchase_request_expired", "purchase_request_fulfilled",
    }
    if action not in allowed_actions:
        raise ValueError(f"نوع حدث غير معروف: {action}")
    return {"log_type": "general", "event_name": action, "actor_ref_id": actor_ref_id,
            "metadata": {"purchase_request_id": pr_id, "reason": reason}}


# ---------------------------------------------------------------------------
# REQ-PUR-011..016: تقديم عرض السعر
# ---------------------------------------------------------------------------

def submit_offer(pr: PurchaseRequest, existing_offers_for_pr: List[Offer],
                  seller_store_ref_id: str, amount: float, currency: str,
                  provides_shipping: bool, notes: Optional[str] = None) -> Offer:
    if pr.status in PR_CLOSED_STATUSES:
        raise PurchaseRequestClosedError(
            f"لا يمكن تقديم عرض على طلب بحالة '{pr.status}' (REQ-PUR-017)."
        )
    for existing in existing_offers_for_pr:
        if existing.seller_store_ref_id == seller_store_ref_id and existing.status == "submitted":
            raise DuplicateActiveOfferError(
                "يوجد بالفعل عرض نشط لنفس البائع على هذا الطلب."
            )

    offer = Offer(id="", purchase_request_id=pr.id, seller_store_ref_id=seller_store_ref_id,
                   amount=amount, currency=currency, provides_shipping=provides_shipping, notes=notes)

    # الأثر الجانبي على الطلب: أول عرض يقفل تعديل الحقول وينقل الحالة لقيد المراجعة
    pr.has_received_offer = True
    if pr.status == "open":
        transition_purchase_request_status(pr, "under_review")

    return offer


def withdraw_offer(offer: Offer) -> Offer:
    """REQ-PUR-018: السحب مسموح فقط قبل القبول."""
    if offer.status != "submitted":
        raise OfferNotWithdrawableError(
            f"لا يمكن سحب عرض بحالة '{offer.status}'؛ السحب متاح فقط للعروض المقدَّمة لم تُقبَل أو تُرفَض بعد."
        )
    offer.status = "withdrawn"
    return offer


# ---------------------------------------------------------------------------
# REQ-PUR-013, 014: قبول عرض واحد ورفض الباقي تلقائيًا
# ---------------------------------------------------------------------------

def accept_offer(pr: PurchaseRequest, offer_to_accept: Offer, all_offers_for_pr: List[Offer]) -> PurchaseRequest:
    if offer_to_accept.status != "submitted":
        raise ValueError(f"لا يمكن قبول عرض بحالة '{offer_to_accept.status}'.")
    if offer_to_accept.purchase_request_id != pr.id:
        raise ValueError("العرض لا ينتمي لطلب الشراء المحدَّد.")

    offer_to_accept.status = "accepted"
    for other in all_offers_for_pr:
        # ملاحظة تصحيح مهمة (اكتُشفت أثناء الاختبار الفعلي): المقارنة بالهوية
        # الكائنية (is) لا بمعرّف id، لأن id يبقى فارغًا في هذه الطبقة النقية
        # قبل إسناده فعليًا عبر Repository؛ المقارنة بـid وحدها كانت تفشل
        # بصمت عند تساوي كل المعرّفات الفارغة، فتُبقي عروضًا أخرى دون رفض.
        if other is not offer_to_accept and other.status == "submitted":
            other.status = "rejected"  # REQ-PUR-014: رفض تلقائي لبقية العروض

    transition_purchase_request_status(pr, "fulfilled")
    return pr


# ---------------------------------------------------------------------------
# نقاط تجميع تعتمد على طبقة Repository (دليل حوكمة التنفيذ v1.3/1.4/1.7)
# ---------------------------------------------------------------------------

def create_purchase_request_via_repository(repository, buyer_user_ref_id: str,
                                            catalog_part_ref_id: str, trim_ref_id: str,
                                            is_part_approved_checker=None) -> PurchaseRequest:
    pr = create_purchase_request(buyer_user_ref_id, catalog_part_ref_id, trim_ref_id, is_part_approved_checker)
    return repository.insert_purchase_request(pr)


def submit_offer_via_repository(repository, pr_id: str, seller_store_ref_id: str,
                                 amount: float, currency: str, provides_shipping: bool,
                                 notes=None) -> Offer:
    pr = repository.get_purchase_request_by_id(pr_id)
    if pr is None:
        raise ValueError(f"لا يوجد طلب شراء بالمعرّف: {pr_id}")
    existing_offers = repository.get_offers_for_purchase_request(pr_id)

    offer = submit_offer(pr, existing_offers, seller_store_ref_id, amount, currency, provides_shipping, notes)
    saved_offer = repository.insert_offer(offer)
    repository.update_purchase_request(pr)  # لأن submit_offer قد يغيّر has_received_offer وstatus
    return saved_offer


def accept_offer_via_repository(repository, pr_id: str, offer_id: str) -> PurchaseRequest:
    """
    ملاحظة تصحيح مهمة: تُجلَب كل العروض مرة واحدة فقط، ويُحدَّد العرض
    المستهدَف ضمن نفس القائمة بالضبط (لا استعلام منفصل)، لضمان استخدام
    accept_offer لمنطق المقارنة بالهوية الكائنية (is) بصورة صحيحة تمامًا
    كما في الاختبارات النقية؛ جلب العرض المستهدَف باستعلام منفصل كان سيُنتج
    كائنًا مختلفًا يكسر تلك المقارنة رغم تطابق المعرّف.
    """
    pr = repository.get_purchase_request_by_id(pr_id)
    if pr is None:
        raise ValueError(f"لا يوجد طلب شراء بالمعرّف: {pr_id}")

    all_offers = repository.get_offers_for_purchase_request(pr_id)
    target_offer = next((o for o in all_offers if o.id == offer_id), None)
    if target_offer is None:
        raise ValueError(f"لا يوجد عرض بالمعرّف: {offer_id}")

    accept_offer(pr, target_offer, all_offers)

    for o in all_offers:
        repository.update_offer(o)
    repository.update_purchase_request(pr)
    return pr


def withdraw_offer_via_repository(repository, offer_id: str) -> Offer:
    offer = repository.get_offer_by_id(offer_id)
    if offer is None:
        raise ValueError(f"لا يوجد عرض بالمعرّف: {offer_id}")
    withdraw_offer(offer)  # عملية على كائن واحد فقط؛ لا مشكلة هوية كائنية هنا (بخلاف accept_offer)
    return repository.update_offer(offer)


def cancel_purchase_request_via_repository(repository, pr_id: str) -> PurchaseRequest:
    """امتداد Orders/Messaging/Notifications Contract Extension: كان الغلاف مفقودًا."""
    pr = repository.get_purchase_request_by_id(pr_id)
    if pr is None:
        raise ValueError(f"لا يوجد طلب شراء بالمعرّف: {pr_id}")
    cancel_purchase_request(pr)
    return repository.update_purchase_request(pr)


# ---------------------------------------------------------------------------
# CR-015: قوائم Frontend Enablement — بلا تعديل على أي دالة أعلاه
# ---------------------------------------------------------------------------

def list_my_purchase_requests_via_repository(repository, buyer_user_ref_id: str,
                                              status: Optional[str], page: int, page_size: int):
    return repository.list_purchase_requests_by_buyer(buyer_user_ref_id, status, page, page_size)


def list_purchase_request_offers_via_repository(repository, pr_id: str, requester_user_ref_id: str,
                                                 requester_store_id: Optional[str],
                                                 status: Optional[str], page: int, page_size: int):
    """
    نطاق النتيجة حسب صلاحية الطالب (Scoping، لا اسم دور):
    - صاحب طلب الشراء (buyer_user_ref_id مطابق): يرى كل العروض.
    - بائع (يملك متجرًا، requester_store_id غير فارغ): يرى عرضه هو فقط
      (التصفية على مستوى الاستعلام نفسه، لا بعد الترقيم — لتجنّب صفحات
      غير صحيحة عند وجود أكثر من عرض تاريخي لنفس البائع).
    - غير ذلك: PermissionError (403 عند طبقة الـAPI).
    """
    pr = repository.get_purchase_request_by_id(pr_id)
    if pr is None:
        raise ValueError(f"لا يوجد طلب شراء بالمعرّف: {pr_id}")

    is_buyer = pr.buyer_user_ref_id == requester_user_ref_id
    is_seller_with_store = requester_store_id is not None

    if not is_buyer and not is_seller_with_store:
        raise PermissionError("لا صلة للطالب بطلب الشراء هذا لا كصاحب ولا كبائع.")

    seller_scope = None if is_buyer else requester_store_id
    return repository.list_offers_for_purchase_request_paginated(pr_id, status, page, page_size, seller_scope)
