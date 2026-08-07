"""
search_api.py — طبقة REST API لخدمة البحث (SRC)
المرجع: api_spec/openapi.yaml — GET /search/parts (موثَّق بالكامل أصلاً ضمن
        الشريحة الأولى المعتمَدة؛ لا حاجة لامتداد عقد جديد هنا، التنفيذ فقط).

ملاحظتا نطاق (لا تُخفيان، موثَّقتان صراحة):
1. معاملَا q (نص حر) وsort (ترتيب مخصَّص) موجودان في العقد لكن غير مُنفَّذين
   في search_service.py على الإطلاق؛ يُقبَلان في الطلب دون أي أثر حاليًا.
2. account_country_code/geolocation_country_code/ip_country_code (REQ-SRC-006-C)
   تتطلب مصادر بيانات غير متوفرة بعد (حساب المستخدم، تحديد موقع، قاعدة IP)؛
   يُستخدَم country_ref_id المُرسَل من العميل مباشرة كـmanual_country_code
   فقط (المصدر الوحيد الفعلي المتاح حاليًا عبر العقد).
3. image_url ليس له أي تخزين أو منطق في الكود؛ يُعاد null دائمًا (لا نظام
   صور مبني بعد). price_display_text يُشتَق محليًا هنا (REQ-STR-014).
"""

from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel

from auth_api import get_correlation_id
from search_service import execute_search_via_repository

router = APIRouter(prefix="/api/v1", tags=["search"])


class SearchResultItem(BaseModel):
    inventory_item_id: str
    business_code: str
    part_name: str
    store_name: str
    image_url: Optional[str] = None
    price_amount: Optional[float] = None
    price_display_text: str


class PaginationMeta(BaseModel):
    page: int
    page_size: int
    total_items: int


class SearchResponse(BaseModel):
    results: list[SearchResultItem]
    effective_country_code: Optional[str] = None
    effective_country_source: str
    pagination: PaginationMeta


def get_search_repository(request: Request):
    return request.app.state.search_repository


def _price_display_text(price_amount: Optional[float]) -> str:
    """REQ-STR-014: نص بديل عند غياب السعر."""
    if price_amount is None:
        return "تواصل مع البائع للسعر"
    return f"{price_amount:.2f}"


@router.get("/search/parts", response_model=SearchResponse)
def search_parts(
    q: Optional[str] = Query(default=None),
    trim_ref_id: Optional[str] = Query(default=None),
    price_filter: str = Query(default="all"),
    condition_ref_id: Optional[str] = Query(default=None),
    verified_sellers_only: bool = Query(default=False),
    country_ref_id: Optional[str] = Query(default=None),
    city_ref_id: Optional[str] = Query(default=None),
    store_ref_id: Optional[str] = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1),
    sort: Optional[str] = Query(default=None),
    correlation_id: str = Depends(get_correlation_id),
    search_repo=Depends(get_search_repository),
):
    result = execute_search_via_repository(
        search_repo,
        manual_country_code=country_ref_id,
        trim_ref_id=trim_ref_id,
        city_code=city_ref_id,
        price_filter=price_filter,
        condition_code=condition_ref_id,
        verified_sellers_only=verified_sellers_only,
        store_ref_id=store_ref_id,
        page=page,
        page_size=page_size,
    )

    items = [
        SearchResultItem(
            inventory_item_id=i.id, business_code=i.business_code, part_name=i.part_name,
            store_name=i.store_name, image_url=None,
            price_amount=i.price_amount, price_display_text=_price_display_text(i.price_amount),
        )
        for i in result["results"]
    ]
    return SearchResponse(
        results=items,
        effective_country_code=result["effective_country_code"],
        effective_country_source=result["effective_country_source"],
        pagination=PaginationMeta(**result["pagination"]),
    )
