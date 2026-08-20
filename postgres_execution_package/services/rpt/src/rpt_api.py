"""
rpt_api.py — طبقة REST API لتقارير الإدارة (Batch 3A Slice 2)

GET /reports/executive-dashboard: للإداري (SYSTEM_ADMIN_ROLES) حصرًا — نفس
نمط aud_api.py/ana_api.py حرفيًا. لا Endpoint للكتابة إطلاقًا (Read-Only Domain).
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
    get_executive_dashboard_via_repository,
    get_marketplace_intelligence_via_repository,
    get_missing_parts_report_via_repository,
    get_search_analytics_via_repository,
    get_trending_parts_via_repository,
)

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


def _require_admin(correlation_id: str, current_session: Session, auth_repo) -> None:
    role = auth_repo.get_user_role(current_session.user_id)
    if role not in SYSTEM_ADMIN_ROLES:
        raise error(correlation_id, status.HTTP_403_FORBIDDEN, "FORBIDDEN", "هذه العملية تتطلب صلاحية مدير النظام.")


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
