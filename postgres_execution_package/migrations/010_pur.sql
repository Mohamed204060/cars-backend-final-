-- ============================================================
-- 010_pur.sql — وحدة طلبات الشراء والعروض (PUR)
-- المرجع: DD الحزمة 1 (قسم PUR)؛ REQ-PUR-001..018 (يشمل CR-002: تعديل عرض السعر قبل القبول)
-- الاعتماديات: str، pct، vct، iam — بالإشارة المرجعية
-- ============================================================

CREATE TABLE pur.purchase_requests (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    business_code       VARCHAR(32) NOT NULL,          -- REQ-PUR-015
    buyer_user_ref_id   UUID NOT NULL,                 -- إشارة مرجعية لـ iam.users
    catalog_part_ref_id UUID NOT NULL,                 -- إشارة مرجعية لـ pct.catalog_parts (REQ-PUR-002)
    trim_ref_id         UUID NOT NULL,                 -- إشارة مرجعية لـ vct.trims
    status              VARCHAR(24) NOT NULL DEFAULT 'open', -- REQ-PUR-005
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT uq_purchase_requests_business_code UNIQUE (business_code),
    CONSTRAINT chk_purchase_requests_status CHECK (status IN (
        'open', 'under_review', 'fulfilled', 'expired', 'cancelled'
    ))
);
COMMENT ON TABLE pur.purchase_requests IS 'REQ-PUR-001..010: طلب الشراء بدورة حياته الخماسية';
CREATE INDEX idx_purchase_requests_buyer ON pur.purchase_requests (buyer_user_ref_id);
CREATE INDEX idx_purchase_requests_status ON pur.purchase_requests (status);

CREATE TABLE pur.offers (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    business_code         VARCHAR(32) NOT NULL,
    purchase_request_id   UUID NOT NULL REFERENCES pur.purchase_requests(id),
    seller_store_ref_id   UUID NOT NULL,   -- إشارة مرجعية لـ str.stores
    amount                NUMERIC(12,2) NOT NULL,
    currency              CHAR(3) NOT NULL,
    provides_shipping     BOOLEAN NOT NULL,  -- REQ-PUR-016: الشحن اختياري، لا يُفترَض
    notes                 TEXT,
    status                VARCHAR(16) NOT NULL DEFAULT 'submitted', -- REQ-PUR-011..018
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT uq_offers_business_code UNIQUE (business_code),
    CONSTRAINT chk_offers_status CHECK (status IN (
        'submitted', 'accepted', 'rejected', 'withdrawn', 'expired'
    ))
);
COMMENT ON TABLE pur.offers IS 'REQ-PUR-011..018: عرض السعر، قابل للتعديل قبل القبول فقط (CR-002)';
CREATE INDEX idx_offers_purchase_request_id ON pur.offers (purchase_request_id);
CREATE INDEX idx_offers_status ON pur.offers (status);
-- REQ-SUB-004-B/PUR-004: منع أكثر من عرض نشط واحد لنفس البائع على نفس الطلب
CREATE UNIQUE INDEX uq_offers_one_active_per_seller
    ON pur.offers (purchase_request_id, seller_store_ref_id)
    WHERE status = 'submitted';
