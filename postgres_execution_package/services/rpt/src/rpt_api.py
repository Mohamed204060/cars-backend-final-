"""
rpt_api.py — طبقة REST API لتقارير الإدارة (Batch 3A Slice 2 + Member/Store 360°
+ Data Quality — Corrective Batch)

GET /reports/executive-dashboard وأغلب المسارات: للإداري (SYSTEM_ADMIN_ROLES)
حصرًا — نفس نمط aud_api.py/ana_api.py حرفيًا. لا Endpoint للكتابة إطلاقًا
(Read-Only Domain).

Member 360°/Store 360° — فصل صريح بين طبقتين (Reports Catalog §36-37: امتلاك
صلاحية Dashboard العامة لا يساوي صلاحية بيانات حساسة):
  - GET /member-360/{id}: SYSTEM_ADMIN_ROLES (admin+super_admin) — حساب،
    متجر، اشتراك، مخزون، طلبات/عروض، تذاكر دعم. Aggregate/Admin-safe.
  - GET /member-360/{id}/sensitive: super_admin حصرًا — جلسات الدخول،
    عدد المحادثات (Metadata فقط، لا محتوى أبدًا)، عدد أحداث Audit كـFaعل.
لا صلاحية "Sensitive Report" مخصَّصة موجودة في الـ9 قيم الحالية لـ
primary_role (IAM الحالي) — تقييد super_admin هو أقل حل متوافق دون اختراع
Role/Permission جديد، موثَّق هنا صراحة كقرار حوكمة، لا افتراضًا صامتًا.
Store 360° بلا حقول حساسة مكافئة أصلًا (لا جلسات/رسائل مباشرة للمتجر
نفسه) — Endpoint واحد فقط، SYSTEM_ADMIN_ROLES.
"""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel

from auth_api import error, get_correlation_id, get_current_session
from pct_api import SYSTEM_ADMIN_ROLES, get_auth_repository_for_role_check
from session_service import Session
from rpt_service import (
    InvalidDateRangeError,
    InvalidWindowError,
    get_data_quality_dashboard_via_repository,
    get_executive_dashboard_via_repository,
    get_inventory_catalog_analytics_via_repository,
    get_marketplace_intelligence_via_repository,
    get_member_360_sensitive_via_repository,
    get_member_360_via_repository,
    get_missing_parts_report_via_repository,
    get_purchase_request_offer_analytics_via_repository,
    get_search_analytics_via_repository,
    get_seller_store_analytics_via_repository,
    get_store_360_via_repository,
    get_trending_parts_via_repository,
    get_user_analytics_via_repository,
)

# IAM الحالي (001_iam.sql) لا يملك صلاحية "Sensitive Report" مخصَّصة ضمن
# primary_role التسعة — super_admin هو أعلى دور موجود فعليًا، فاستُخدم كأقل
# حل متوافق مع البنية القائمة (لا اختراع Role/Permission جديد، وفق التوجيه
# الصريح). عند اعتماد صلاحية مخصَّصة مستقبلًا (IAM Extension) تُستبدَل هذه
# الثوابت بها دون تغيير معماري إضافي.
SENSITIVE_REPORT_ROLES = {"super_admin"}


router = APIRouter(prefix="/api/v1/reports", tags=["reports"])


class ExecutiveDashboardResponse(BaseModel):
    generated_at_utc: str
    date_from: Optional[str] = None
    date_to: Optional[str] = None

    users_total: int
    users_new: int
    users_by_status: dict[str, int]
    sellers_total: int

    stores_total: int
    stores_by_status: dict[str, int]

    inventory_items_total: int
    inventory_items_by_status: dict[str, int]

    catalog_parts_total: int
    catalog_parts_by_status: dict[str, int]

    purchase_requests_total: int
    purchase_requests_by_status: dict[str, int]
    purchase_requests_without_offers: int

    offers_total: int
    offers_by_status: dict[str, int]

    request_to_offer_rate: float
    request_to_accepted_offer_rate: float
    avg_offers_per_request: float

    subscriptions_active_total: int
    subscriptions_by_plan: dict[str, int]


def get_rpt_repository(request: Request):
    return request.app.state.rpt_repository


class SearchAnalyticsResponse(BaseModel):
    generated_at_utc: str
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    search_volume: int
    zero_result_count: int
    zero_result_rate: float
    top_zero_result_vehicles: list[dict]
    top_missing_search_terms: list[dict]


class MissingPartsReportResponse(BaseModel):
    generated_at_utc: str
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    zero_result_search_count: int
    purchase_requests_without_offers_count: int
    top_unmet_demand_parts: list[dict]
    top_missing_search_terms: list[dict]


class MarketplaceIntelligenceResponse(BaseModel):
    generated_at_utc: str
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    demand_hotspot_vehicles: list[dict]
    unmet_demand_parts: list[dict]
    catalog_parts_with_no_active_supply: int
    sellers_to_active_stores_ratio: float
    request_to_offer_rate: float


class TrendingPartsResponse(BaseModel):
    generated_at_utc: str
    window_days: int
    current_period_from: str
    current_period_to: str
    previous_period_from: str
    previous_period_to: str
    top_growing_parts: list[dict]


class UserAnalyticsResponse(BaseModel):
    generated_at_utc: str
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    registrations_by_day: list[dict]
    users_by_role: dict[str, int]
    users_by_account_type: dict[str, int]
    verified_sellers_count: int


class SellerStoreAnalyticsResponse(BaseModel):
    generated_at_utc: str
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    stores_by_status: dict[str, int]
    sellers_without_store_count: int
    active_stores_without_inventory_count: int
    top_stores_by_offer_count: list[dict]
    new_stores_count: int


class InventoryCatalogAnalyticsResponse(BaseModel):
    generated_at_utc: str
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    inventory_items_by_status: dict[str, int]
    inventory_items_by_pricing_mode: dict[str, int]
    stale_active_inventory_items_count: int
    catalog_parts_by_status: dict[str, int]
    manufacturers_by_status: dict[str, int]
    models_total: int
    generations_total: int
    trims_total: int


class PurchaseRequestOfferAnalyticsResponse(BaseModel):
    generated_at_utc: str
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    offers_by_status: dict[str, int]
    withdrawn_offers_count: int
    avg_hours_to_first_offer: Optional[float] = None
    avg_hours_to_accepted_offer: Optional[float] = None


def _require_admin(correlation_id: str, current_session: Session, auth_repo) -> None:
    role = auth_repo.get_user_role(current_session.user_id)
    if role not in SYSTEM_ADMIN_ROLES:
        raise error(correlation_id, status.HTTP_403_FORBIDDEN, "FORBIDDEN", "هذه العملية تتطلب صلاحية مدير النظام.")


def _require_sensitive_report_access(correlation_id: str, current_session: Session, auth_repo) -> None:
    """§36-37: بيانات حساسة (جلسات دخول، Metadata رسائل، Audit) — super_admin
    حصرًا، أضيق من SYSTEM_ADMIN_ROLES العامة (admin+super_admin) المستخدَمة
    لبقية التقارير. راجع التوثيق أعلى الملف لسبب هذا القرار."""
    role = auth_repo.get_user_role(current_session.user_id)
    if role not in SENSITIVE_REPORT_ROLES:
        raise error(correlation_id, status.HTTP_403_FORBIDDEN, "FORBIDDEN",
                    "هذه البيانات حساسة وتتطلب صلاحية super_admin تحديدًا.")


def _to_response(d) -> ExecutiveDashboardResponse:
    return ExecutiveDashboardResponse(
        generated_at_utc=d.generated_at_utc.isoformat(),
        date_from=d.date_from.isoformat() if d.date_from else None,
        date_to=d.date_to.isoformat() if d.date_to else None,
        users_total=d.users_total, users_new=d.users_new, users_by_status=d.users_by_status,
        sellers_total=d.sellers_total,
        stores_total=d.stores_total, stores_by_status=d.stores_by_status,
        inventory_items_total=d.inventory_items_total, inventory_items_by_status=d.inventory_items_by_status,
        catalog_parts_total=d.catalog_parts_total, catalog_parts_by_status=d.catalog_parts_by_status,
        purchase_requests_total=d.purchase_requests_total, purchase_requests_by_status=d.purchase_requests_by_status,
        purchase_requests_without_offers=d.purchase_requests_without_offers,
        offers_total=d.offers_total, offers_by_status=d.offers_by_status,
        request_to_offer_rate=d.request_to_offer_rate,
        request_to_accepted_offer_rate=d.request_to_accepted_offer_rate,
        avg_offers_per_request=d.avg_offers_per_request,
        subscriptions_active_total=d.subscriptions_active_total, subscriptions_by_plan=d.subscriptions_by_plan,
    )


@router.get("/executive-dashboard", response_model=ExecutiveDashboardResponse)
def get_executive_dashboard(
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    rpt_repo=Depends(get_rpt_repository),
    auth_repo=Depends(get_auth_repository_for_role_check),
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
):
    _require_admin(correlation_id, current_session, auth_repo)
    try:
        dashboard = get_executive_dashboard_via_repository(rpt_repo, date_from=date_from, date_to=date_to)
    except InvalidDateRangeError:
        raise error(correlation_id, status.HTTP_400_BAD_REQUEST, "INVALID_DATE_RANGE", "date_from يجب ألا يكون بعد date_to.")

    return _to_response(dashboard)


@router.get("/search-analytics", response_model=SearchAnalyticsResponse)
def get_search_analytics(
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    rpt_repo=Depends(get_rpt_repository),
    auth_repo=Depends(get_auth_repository_for_role_check),
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
):
    _require_admin(correlation_id, current_session, auth_repo)
    try:
        sa = get_search_analytics_via_repository(rpt_repo, date_from=date_from, date_to=date_to)
    except InvalidDateRangeError:
        raise error(correlation_id, status.HTTP_400_BAD_REQUEST, "INVALID_DATE_RANGE", "date_from يجب ألا يكون بعد date_to.")

    return SearchAnalyticsResponse(
        generated_at_utc=sa.generated_at_utc.isoformat(),
        date_from=sa.date_from.isoformat() if sa.date_from else None,
        date_to=sa.date_to.isoformat() if sa.date_to else None,
        search_volume=sa.search_volume, zero_result_count=sa.zero_result_count,
        zero_result_rate=sa.zero_result_rate, top_zero_result_vehicles=sa.top_zero_result_vehicles,
        top_missing_search_terms=sa.top_missing_search_terms,
    )


@router.get("/missing-parts", response_model=MissingPartsReportResponse)
def get_missing_parts_report(
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    rpt_repo=Depends(get_rpt_repository),
    auth_repo=Depends(get_auth_repository_for_role_check),
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
):
    _require_admin(correlation_id, current_session, auth_repo)
    try:
        mp = get_missing_parts_report_via_repository(rpt_repo, date_from=date_from, date_to=date_to)
    except InvalidDateRangeError:
        raise error(correlation_id, status.HTTP_400_BAD_REQUEST, "INVALID_DATE_RANGE", "date_from يجب ألا يكون بعد date_to.")

    return MissingPartsReportResponse(
        generated_at_utc=mp.generated_at_utc.isoformat(),
        date_from=mp.date_from.isoformat() if mp.date_from else None,
        date_to=mp.date_to.isoformat() if mp.date_to else None,
        zero_result_search_count=mp.zero_result_search_count,
        purchase_requests_without_offers_count=mp.purchase_requests_without_offers_count,
        top_unmet_demand_parts=mp.top_unmet_demand_parts,
        top_missing_search_terms=mp.top_missing_search_terms,
    )


@router.get("/marketplace-intelligence", response_model=MarketplaceIntelligenceResponse)
def get_marketplace_intelligence(
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    rpt_repo=Depends(get_rpt_repository),
    auth_repo=Depends(get_auth_repository_for_role_check),
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
):
    _require_admin(correlation_id, current_session, auth_repo)
    try:
        mi = get_marketplace_intelligence_via_repository(rpt_repo, date_from=date_from, date_to=date_to)
    except InvalidDateRangeError:
        raise error(correlation_id, status.HTTP_400_BAD_REQUEST, "INVALID_DATE_RANGE", "date_from يجب ألا يكون بعد date_to.")

    return MarketplaceIntelligenceResponse(
        generated_at_utc=mi.generated_at_utc.isoformat(),
        date_from=mi.date_from.isoformat() if mi.date_from else None,
        date_to=mi.date_to.isoformat() if mi.date_to else None,
        demand_hotspot_vehicles=mi.demand_hotspot_vehicles,
        unmet_demand_parts=mi.unmet_demand_parts,
        catalog_parts_with_no_active_supply=mi.catalog_parts_with_no_active_supply,
        sellers_to_active_stores_ratio=mi.sellers_to_active_stores_ratio,
        request_to_offer_rate=mi.request_to_offer_rate,
    )


@router.get("/trending-parts", response_model=TrendingPartsResponse)
def get_trending_parts(
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    rpt_repo=Depends(get_rpt_repository),
    auth_repo=Depends(get_auth_repository_for_role_check),
    # Pre-Gate Corrective #5: window_days: int (بلا Query(ge=,le=)) عمدًا —
    # قيمة غير رقمية (مثل "abc") تُرفَض تلقائيًا بـ422 من FastAPI/Pydantic
    # (تحويل النوع الفاشل، سلوك مدمَج، بلا كود إضافي)؛ قيمة رقمية خارج
    # المدى (0، سالبة، >365) تُرفَض صراحة أدناه بـ400 INVALID_WINDOW (رسالة
    # عمل واضحة، بخلاف 422 الصامت). لا حالة منها تتحول بصمت لـ30 الافتراضية
    # — الافتراضي 30 يُستخدَم فقط عند غياب المعامل تمامًا من الطلب.
    window_days: int = 30,
):
    _require_admin(correlation_id, current_session, auth_repo)
    try:
        tp = get_trending_parts_via_repository(rpt_repo, window_days=window_days)
    except InvalidWindowError:
        raise error(correlation_id, status.HTTP_400_BAD_REQUEST, "INVALID_WINDOW", "window_days يجب أن يكون بين 1 و365.")

    return TrendingPartsResponse(
        generated_at_utc=tp.generated_at_utc.isoformat(), window_days=tp.window_days,
        current_period_from=tp.current_period_from.isoformat(), current_period_to=tp.current_period_to.isoformat(),
        previous_period_from=tp.previous_period_from.isoformat(), previous_period_to=tp.previous_period_to.isoformat(),
        top_growing_parts=tp.top_growing_parts,
    )


@router.get("/user-analytics", response_model=UserAnalyticsResponse)
def get_user_analytics(
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    rpt_repo=Depends(get_rpt_repository),
    auth_repo=Depends(get_auth_repository_for_role_check),
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
):
    _require_admin(correlation_id, current_session, auth_repo)
    try:
        ua = get_user_analytics_via_repository(rpt_repo, date_from=date_from, date_to=date_to)
    except InvalidDateRangeError:
        raise error(correlation_id, status.HTTP_400_BAD_REQUEST, "INVALID_DATE_RANGE", "date_from يجب ألا يكون بعد date_to.")

    return UserAnalyticsResponse(
        generated_at_utc=ua.generated_at_utc.isoformat(),
        date_from=ua.date_from.isoformat() if ua.date_from else None,
        date_to=ua.date_to.isoformat() if ua.date_to else None,
        registrations_by_day=ua.registrations_by_day, users_by_role=ua.users_by_role,
        users_by_account_type=ua.users_by_account_type, verified_sellers_count=ua.verified_sellers_count,
    )


@router.get("/seller-store-analytics", response_model=SellerStoreAnalyticsResponse)
def get_seller_store_analytics(
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    rpt_repo=Depends(get_rpt_repository),
    auth_repo=Depends(get_auth_repository_for_role_check),
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
):
    _require_admin(correlation_id, current_session, auth_repo)
    try:
        d = get_seller_store_analytics_via_repository(rpt_repo, date_from=date_from, date_to=date_to)
    except InvalidDateRangeError:
        raise error(correlation_id, status.HTTP_400_BAD_REQUEST, "INVALID_DATE_RANGE", "date_from يجب ألا يكون بعد date_to.")

    return SellerStoreAnalyticsResponse(
        generated_at_utc=d.generated_at_utc.isoformat(),
        date_from=d.date_from.isoformat() if d.date_from else None,
        date_to=d.date_to.isoformat() if d.date_to else None,
        stores_by_status=d.stores_by_status, sellers_without_store_count=d.sellers_without_store_count,
        active_stores_without_inventory_count=d.active_stores_without_inventory_count,
        top_stores_by_offer_count=d.top_stores_by_offer_count, new_stores_count=d.new_stores_count,
    )


@router.get("/inventory-catalog-analytics", response_model=InventoryCatalogAnalyticsResponse)
def get_inventory_catalog_analytics(
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    rpt_repo=Depends(get_rpt_repository),
    auth_repo=Depends(get_auth_repository_for_role_check),
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
):
    _require_admin(correlation_id, current_session, auth_repo)
    try:
        d = get_inventory_catalog_analytics_via_repository(rpt_repo, date_from=date_from, date_to=date_to)
    except InvalidDateRangeError:
        raise error(correlation_id, status.HTTP_400_BAD_REQUEST, "INVALID_DATE_RANGE", "date_from يجب ألا يكون بعد date_to.")

    return InventoryCatalogAnalyticsResponse(
        generated_at_utc=d.generated_at_utc.isoformat(),
        date_from=d.date_from.isoformat() if d.date_from else None,
        date_to=d.date_to.isoformat() if d.date_to else None,
        inventory_items_by_status=d.inventory_items_by_status,
        inventory_items_by_pricing_mode=d.inventory_items_by_pricing_mode,
        stale_active_inventory_items_count=d.stale_active_inventory_items_count,
        catalog_parts_by_status=d.catalog_parts_by_status, manufacturers_by_status=d.manufacturers_by_status,
        models_total=d.models_total, generations_total=d.generations_total, trims_total=d.trims_total,
    )


@router.get("/purchase-request-offer-analytics", response_model=PurchaseRequestOfferAnalyticsResponse)
def get_purchase_request_offer_analytics(
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    rpt_repo=Depends(get_rpt_repository),
    auth_repo=Depends(get_auth_repository_for_role_check),
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
):
    _require_admin(correlation_id, current_session, auth_repo)
    try:
        d = get_purchase_request_offer_analytics_via_repository(rpt_repo, date_from=date_from, date_to=date_to)
    except InvalidDateRangeError:
        raise error(correlation_id, status.HTTP_400_BAD_REQUEST, "INVALID_DATE_RANGE", "date_from يجب ألا يكون بعد date_to.")

    return PurchaseRequestOfferAnalyticsResponse(
        generated_at_utc=d.generated_at_utc.isoformat(),
        date_from=d.date_from.isoformat() if d.date_from else None,
        date_to=d.date_to.isoformat() if d.date_to else None,
        offers_by_status=d.offers_by_status, withdrawn_offers_count=d.withdrawn_offers_count,
        avg_hours_to_first_offer=d.avg_hours_to_first_offer,
        avg_hours_to_accepted_offer=d.avg_hours_to_accepted_offer,
    )


# ===========================================================================
# Member 360° (Reports Catalog §6) — طبقتان صريحتان (راجع التوثيق أعلى الملف)
# ===========================================================================

class Member360Response(BaseModel):
    generated_at_utc: str
    user_id: str
    business_code: str
    primary_role: str
    account_type: str
    status: str
    created_at: str
    is_verified_seller: bool

    store_ids: list[str]

    subscription_plan_code: Optional[str] = None
    subscription_status: Optional[str] = None
    subscription_expires_at: Optional[str] = None

    inventory_items_total: int
    inventory_items_by_status: dict[str, int]

    purchase_requests_total: int
    purchase_requests_by_status: dict[str, int]

    offers_total: int
    offers_by_status: dict[str, int]

    support_tickets_total: int
    support_tickets_by_status: dict[str, int]


class Member360SensitiveResponse(BaseModel):
    """§36-37: super_admin حصرًا. جلسات الدخول (بلا IP — غير مسجَّل في
    iam.sessions إطلاقًا، Blocker منفصل)، عدد محادثات (Metadata فقط، لا
    محتوى)، عدد أحداث Audit كفاعل (actor) فقط — aud.events بلا عمود
    Target/Subject، فلا يمكن عرض "إجراءات اتُّخذت على الحساب" بثقة هنا."""
    generated_at_utc: str
    user_id: str
    login_sessions_total: int
    last_login_at: Optional[str] = None
    last_logout_at: Optional[str] = None
    conversations_count: int
    audit_events_as_actor_total: int


def _to_member_360_response(m) -> Member360Response:
    return Member360Response(
        generated_at_utc=m.generated_at_utc.isoformat(), user_id=m.user_id, business_code=m.business_code,
        primary_role=m.primary_role, account_type=m.account_type, status=m.status,
        created_at=m.created_at.isoformat(), is_verified_seller=m.is_verified_seller, store_ids=m.store_ids,
        subscription_plan_code=m.subscription_plan_code, subscription_status=m.subscription_status,
        subscription_expires_at=m.subscription_expires_at.isoformat() if m.subscription_expires_at else None,
        inventory_items_total=m.inventory_items_total, inventory_items_by_status=m.inventory_items_by_status,
        purchase_requests_total=m.purchase_requests_total,
        purchase_requests_by_status=m.purchase_requests_by_status,
        offers_total=m.offers_total, offers_by_status=m.offers_by_status,
        support_tickets_total=m.support_tickets_total, support_tickets_by_status=m.support_tickets_by_status,
    )


@router.get("/member-360/{user_id}", response_model=Member360Response)
def get_member_360(
    user_id: str,
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    rpt_repo=Depends(get_rpt_repository),
    auth_repo=Depends(get_auth_repository_for_role_check),
):
    _require_admin(correlation_id, current_session, auth_repo)
    m = get_member_360_via_repository(rpt_repo, user_id)
    if m is None:
        raise error(correlation_id, status.HTTP_404_NOT_FOUND, "USER_NOT_FOUND", "المستخدم غير موجود.")
    return _to_member_360_response(m)


@router.get("/member-360/{user_id}/sensitive", response_model=Member360SensitiveResponse)
def get_member_360_sensitive(
    user_id: str,
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    rpt_repo=Depends(get_rpt_repository),
    auth_repo=Depends(get_auth_repository_for_role_check),
):
    # Corrective: كان هذا Endpoint يستدعي get_member_360_via_repository
    # (Admin-safe) خطأً — bug في التوصيل فقط، رغم أن طبقة Data Access نفسها
    # (get_member_360_sensitive في Repository) كانت مفصولة بالفعل ولا تُستدعى
    # هنا إطلاقًا سابقًا. الآن يُستدعى المسار الحساس المعزول فعليًا.
    _require_sensitive_report_access(correlation_id, current_session, auth_repo)
    m = get_member_360_sensitive_via_repository(rpt_repo, user_id)
    if m is None:
        raise error(correlation_id, status.HTTP_404_NOT_FOUND, "USER_NOT_FOUND", "المستخدم غير موجود.")
    return Member360SensitiveResponse(
        generated_at_utc=m.generated_at_utc.isoformat(), user_id=m.user_id,
        login_sessions_total=m.login_sessions_total,
        last_login_at=m.last_login_at.isoformat() if m.last_login_at else None,
        last_logout_at=m.last_logout_at.isoformat() if m.last_logout_at else None,
        conversations_count=m.conversations_count, audit_events_as_actor_total=m.audit_events_as_actor_total,
    )


# ===========================================================================
# Store 360° (Reports Catalog §8) — Endpoint واحد؛ بلا حقول حساسة مكافئة
# (لا جلسات/رسائل مباشرة للمتجر نفسه) — SYSTEM_ADMIN_ROLES فقط.
# ===========================================================================

class Store360Response(BaseModel):
    generated_at_utc: str
    store_id: str
    owner_user_ref_id: str
    status: str
    created_at: str

    subscription_plan_code: Optional[str] = None
    subscription_status: Optional[str] = None
    subscription_expires_at: Optional[str] = None

    inventory_items_total: int
    inventory_items_by_status: dict[str, int]

    offers_total: int
    offers_by_status: dict[str, int]
    accepted_offers_total: int
    accepted_offer_rate: float

    avg_hours_to_offer_response: Optional[float] = None

    media_active_images_total: int


@router.get("/store-360/{store_id}", response_model=Store360Response)
def get_store_360(
    store_id: str,
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    rpt_repo=Depends(get_rpt_repository),
    auth_repo=Depends(get_auth_repository_for_role_check),
):
    _require_admin(correlation_id, current_session, auth_repo)
    s = get_store_360_via_repository(rpt_repo, store_id)
    if s is None:
        raise error(correlation_id, status.HTTP_404_NOT_FOUND, "STORE_NOT_FOUND", "المتجر غير موجود.")
    return Store360Response(
        generated_at_utc=s.generated_at_utc.isoformat(), store_id=s.store_id,
        owner_user_ref_id=s.owner_user_ref_id, status=s.status, created_at=s.created_at.isoformat(),
        subscription_plan_code=s.subscription_plan_code, subscription_status=s.subscription_status,
        subscription_expires_at=s.subscription_expires_at.isoformat() if s.subscription_expires_at else None,
        inventory_items_total=s.inventory_items_total, inventory_items_by_status=s.inventory_items_by_status,
        offers_total=s.offers_total, offers_by_status=s.offers_by_status,
        accepted_offers_total=s.accepted_offers_total, accepted_offer_rate=s.accepted_offer_rate,
        avg_hours_to_offer_response=s.avg_hours_to_offer_response,
        media_active_images_total=s.media_active_images_total,
    )


# ===========================================================================
# Data Quality Dashboard (Reports Catalog §25) — بلا حقول حساسة، SYSTEM_ADMIN_ROLES.
# ===========================================================================

class DataQualityDashboardResponse(BaseModel):
    generated_at_utc: str
    inventory_items_without_price: int
    inventory_items_without_images: int
    inventory_items_total: int
    catalog_parts_proposed_pending_review: int
    catalog_parts_not_linked_to_vehicle: int
    catalog_parts_total: int


@router.get("/data-quality", response_model=DataQualityDashboardResponse)
def get_data_quality_dashboard(
    correlation_id: str = Depends(get_correlation_id),
    current_session: Session = Depends(get_current_session),
    rpt_repo=Depends(get_rpt_repository),
    auth_repo=Depends(get_auth_repository_for_role_check),
):
    _require_admin(correlation_id, current_session, auth_repo)
    d = get_data_quality_dashboard_via_repository(rpt_repo)
    return DataQualityDashboardResponse(
        generated_at_utc=d.generated_at_utc.isoformat(),
        inventory_items_without_price=d.inventory_items_without_price,
        inventory_items_without_images=d.inventory_items_without_images,
        inventory_items_total=d.inventory_items_total,
        catalog_parts_proposed_pending_review=d.catalog_parts_proposed_pending_review,
        catalog_parts_not_linked_to_vehicle=d.catalog_parts_not_linked_to_vehicle,
        catalog_parts_total=d.catalog_parts_total,
    )
