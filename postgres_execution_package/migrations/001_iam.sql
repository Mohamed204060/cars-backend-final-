-- ============================================================
-- 001_iam.sql — وحدة الهوية والوصول (IAM)
-- المرجع: DD الحزمة 1 (قسم IAM)؛ REQ-IAM-001..009؛ REQ-SEC-006 (كلمة المرور)
-- الاعتماديات: لا شيء (وحدة جذرية)
-- ============================================================

CREATE TABLE iam.users (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    business_code       VARCHAR(32) NOT NULL,
    primary_role        VARCHAR(32) NOT NULL,  -- REQ-IAM-002: دور أساسي واحد
    account_type        VARCHAR(16) NOT NULL,  -- fردي/تجاري REQ-IAM-006
    status              VARCHAR(16) NOT NULL DEFAULT 'active', -- REQ-IAM-005
    is_verified_seller  BOOLEAN NOT NULL DEFAULT false,        -- REQ-IAM-007
    password_hash       TEXT NOT NULL,          -- REQ-SEC-006/009: تمويه أحادي الاتجاه
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT uq_users_business_code UNIQUE (business_code),
    CONSTRAINT chk_users_account_type CHECK (account_type IN ('individual', 'business')),
    CONSTRAINT chk_users_status CHECK (status IN ('active', 'suspended', 'banned', 'archived')),
    CONSTRAINT chk_users_primary_role CHECK (primary_role IN (
        'super_admin', 'admin', 'moderator', 'individual_seller', 'business_seller',
        'individual_buyer', 'business_buyer', 'news_editor', 'support_moderator'
    ))
);
COMMENT ON TABLE iam.users IS 'REQ-IAM-001..009: حساب المستخدم بدوره الأساسي الواحد وحالته';
CREATE INDEX idx_users_status ON iam.users (status);
CREATE INDEX idx_users_primary_role ON iam.users (primary_role);

CREATE TABLE iam.favorites (
    id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id                UUID NOT NULL REFERENCES iam.users(id),
    inventory_item_ref_id  UUID NOT NULL,  -- إشارة مرجعية لعنصر مخزون في str (لا FK فعلي بين الوحدات)
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT uq_favorites_user_item UNIQUE (user_id, inventory_item_ref_id)
);
COMMENT ON TABLE iam.favorites IS 'REQ-IAM-008: قائمة عناصر المخزون المفضَّلة لدى المستخدم';
CREATE INDEX idx_favorites_user_id ON iam.favorites (user_id);
