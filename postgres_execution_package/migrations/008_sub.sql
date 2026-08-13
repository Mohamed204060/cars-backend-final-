-- ============================================================
-- 008_sub.sql — وحدة الاشتراكات (SUB)
-- المرجع: DD الحزمة 1 (قسم SUB)؛ REQ-SUB-001..008
-- الاعتماديات: ref (نوع الاشتراك)، iam (هوية البائع) — بالإشارة المرجعية
-- ============================================================

CREATE TABLE sub.plans (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    plan_type_ref_id  UUID NOT NULL,  -- إشارة مرجعية لـ ref.ref_values (ref_type = subscription_type)
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE sub.plans IS 'REQ-SUB-001: خطط الاشتراك المعرَّفة من الإدارة';

CREATE TABLE sub.seller_subscriptions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    seller_ref_id   UUID NOT NULL,   -- إشارة مرجعية لـ iam.users
    plan_id         UUID NOT NULL REFERENCES sub.plans(id),
    status          VARCHAR(16) NOT NULL DEFAULT 'active',  -- REQ-SUB-004
    expires_at      TIMESTAMPTZ NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT chk_seller_subscriptions_status CHECK (status IN ('active', 'expired'))
);
COMMENT ON TABLE sub.seller_subscriptions IS 'REQ-SUB-002..005: اشتراك البائع في خطة، مع انتهاء تلقائي';
CREATE INDEX idx_seller_subscriptions_seller ON sub.seller_subscriptions (seller_ref_id);
CREATE INDEX idx_seller_subscriptions_status ON sub.seller_subscriptions (status);
