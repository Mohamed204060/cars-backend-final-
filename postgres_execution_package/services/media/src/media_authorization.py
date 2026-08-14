"""
media_authorization.py — Ownership (§7) وView/Signed Access Authorization
(§10) الحقيقيان لكل owner_type — Batch 2 Unit 2
المرجع: CarsMaint Media Foundation — Approved Baseline v1.0

هذا الملف لا يُعرِّف Endpoints ولا يعرف شيئًا عن FastAPI — دوال Builder
خالصة تُنتِج Closures (checkers) تُحقَن في media_api.py عبر app.state، بنفس
نمط is_part_approved_checker القائم في كل المشروع (SSOT، لا استيراد مباشر
لخدمات PR/Offer/Inventory داخل media_api.py نفسها — الاستيراد هنا فقط،
عند التركيب).

Unit 1 استبدلت هذا بـPlaceholder يرفض كل شيء دائمًا (Fail-closed) — Unit 2
تستبدله بالتنفيذ الحقيقي هنا.
"""

from typing import Optional


def build_media_ownership_checker(order_repo, store_repo, inventory_repo):
    """
    §7 (Binding Authorization): المستخدم هو uploader (يُتحقَّق منه في
    media_service.create_attachment مسبقًا) + يملك Business Entity
    المستهدف فعليًا. هذه الدالة تتحقق من الشطر الثاني فقط (الملكية).
    """
    def checker(owner_type: str, owner_ref_id: str, uploader_user_ref_id: str) -> bool:
        if owner_type == "purchase_request":
            pr = order_repo.get_purchase_request_by_id(owner_ref_id)
            return pr is not None and pr.buyer_user_ref_id == uploader_user_ref_id

        if owner_type == "offer":
            offer = order_repo.get_offer_by_id(owner_ref_id)
            if offer is None:
                return False
            store = store_repo.get_store_by_id(offer.seller_store_ref_id)
            return store is not None and store.owner_user_ref_id == uploader_user_ref_id

        if owner_type == "inventory_item":
            item = inventory_repo.get_item_by_id(owner_ref_id)
            if item is None:
                return False
            store = store_repo.get_store_by_id(item.store_id)
            return store is not None and store.owner_user_ref_id == uploader_user_ref_id

        return False

    return checker


def build_media_view_authorization_checker(order_repo, store_repo):
    """
    §10 (Signed Access) — يخص Private فقط (purchase_request/offer). لا
    يُستدعى لـinventory_item إطلاقًا (Public — بلا حاجة لتفويض عرض، §9).

    PR images: Buyer صاحب الطلب، أو Seller لديه Offer فعلي على الطلب
    نفسه (أي حالة عرض، لا "submitted" فقط — مجرد وجود العرض يكفي وفق
    الـBaseline، لا يُقيَّد بحالة معيَّنة)، أو Admin (يُحسَم خارج هذه
    الدالة عبر is_admin).

    Offer images: Seller صاحب العرض، أو Buyer صاحب PR المرتبط، أو Admin.

    مجرد Login/Seller role غير كافٍ — التحقق هنا دائمًا Ownership حقيقي
    عبر البيانات الفعلية، لا الدور وحده.
    """
    def checker(owner_type: str, owner_ref_id: str, requester_user_ref_id: str, is_admin: bool = False) -> bool:
        if is_admin:
            return True

        if owner_type == "purchase_request":
            pr = order_repo.get_purchase_request_by_id(owner_ref_id)
            if pr is None:
                return False
            if pr.buyer_user_ref_id == requester_user_ref_id:
                return True
            offers, _ = order_repo.list_offers_for_purchase_request_paginated(
                owner_ref_id, status=None, page=1, page_size=1000,
            )
            for offer in offers:
                store = store_repo.get_store_by_id(offer.seller_store_ref_id)
                if store is not None and store.owner_user_ref_id == requester_user_ref_id:
                    return True
            return False

        if owner_type == "offer":
            offer = order_repo.get_offer_by_id(owner_ref_id)
            if offer is None:
                return False
            store = store_repo.get_store_by_id(offer.seller_store_ref_id)
            if store is not None and store.owner_user_ref_id == requester_user_ref_id:
                return True
            pr = order_repo.get_purchase_request_by_id(offer.purchase_request_id)
            return pr is not None and pr.buyer_user_ref_id == requester_user_ref_id

        return False

    return checker
