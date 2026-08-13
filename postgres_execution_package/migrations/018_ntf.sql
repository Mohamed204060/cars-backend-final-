-- ============================================================
-- 018_ntf.sql — مجال الإشعارات والبث الجماعي (NTF)
-- المرجع: ADR-034؛ SRS مجال NTF v1.1؛ CR-008
-- الاعتماديات: iam (بالإشارة المرجعية فقط)
-- الحالة: Prepared — لم يُطبَّق على أي قاعدة بيانات فعلية بعد
-- ============================================================

CREATE SCHEMA IF NOT EXISTS ntf;

CREATE TABLE ntf.campaigns (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_by_user_ref_id  UUID NOT NULL,           -- إشارة مرجعية لـ iam.users
    title                   VARCHAR(200) NOT NULL,
    body                    TEXT NOT NULL,
    audience_type           VARCHAR(16) NOT NULL,     -- static | dynamic
    status                  VARCHAR(16) NOT NULL DEFAULT 'draft',
    priority                VARCHAR(16) NOT NULL DEFAULT 'normal',
    campaign_version        INT NOT NULL DEFAULT 1,   -- REQ-NTF-029
    template_version_id     UUID,                     -- إشارة مرجعية اختيارية لـ ntf.template_versions
    scheduled_at            TIMESTAMPTZ,
    expires_at              TIMESTAMPTZ,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT chk_campaigns_audience_type CHECK (audience_type IN ('static', 'dynamic')),
    CONSTRAINT chk_campaigns_status CHECK (status IN ('draft', 'scheduled', 'running', 'completed', 'cancelled', 'paused', 'archived')),
    CONSTRAINT chk_campaigns_priority CHECK (priority IN ('critical', 'high', 'normal', 'low'))
);
COMMENT ON TABLE ntf.campaigns IS 'REQ-NTF-001..004, 029: الحملة، مع رقم إصدار (Campaign Versioning)';
CREATE INDEX idx_campaigns_status ON ntf.campaigns (status);
CREATE INDEX idx_campaigns_scheduled_at ON ntf.campaigns (scheduled_at);

CREATE TABLE ntf.deliveries (
    id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    campaign_id                 UUID NOT NULL REFERENCES ntf.campaigns(id),
    campaign_version_snapshot   INT NOT NULL,
    correlation_id              UUID NOT NULL,          -- يُمرَّر عبر كل الطبقات (Logs/AUD/Queue/Workers/Providers/RPT)
    execution_status            VARCHAR(16) NOT NULL DEFAULT 'running',
    started_at                  TIMESTAMPTZ,
    completed_at                 TIMESTAMPTZ,
    total_recipients             INT NOT NULL DEFAULT 0,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT chk_deliveries_status CHECK (execution_status IN ('running', 'paused', 'resumed', 'completed', 'cancelled', 'failed'))
);
COMMENT ON TABLE ntf.deliveries IS 'REQ-NTF-020, 022: تنفيذ فعلي واحد لحملة؛ يثبِّت إصدار الحملة وقت التنفيذ';
CREATE INDEX idx_deliveries_campaign_id ON ntf.deliveries (campaign_id);
CREATE INDEX idx_deliveries_execution_status ON ntf.deliveries (execution_status);

CREATE TABLE ntf.recipients (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    delivery_id             UUID NOT NULL REFERENCES ntf.deliveries(id),
    user_ref_id             UUID NOT NULL,           -- إشارة مرجعية لـ iam.users
    channel_provider_code   VARCHAR(32) NOT NULL,    -- إشارة مرجعية لـ ntf.channel_providers
    status                  VARCHAR(16) NOT NULL DEFAULT 'pending',
    sent_at                 TIMESTAMPTZ,
    delivered_at            TIMESTAMPTZ,
    read_at                 TIMESTAMPTZ,
    failure_reason_code     VARCHAR(32),
    retry_count             INT NOT NULL DEFAULT 0,

    CONSTRAINT chk_recipients_status CHECK (status IN ('pending', 'queued', 'sent', 'delivered', 'read', 'failed', 'cancelled')),
    -- REQ-NTF-012: الضامن الفعلي لمنع تكرار المستلِم على مستوى قاعدة البيانات نفسها
    CONSTRAINT uq_recipients_delivery_user UNIQUE (delivery_id, user_ref_id)
);
COMMENT ON TABLE ntf.recipients IS 'REQ-NTF-012, 021: سجل مستلِم واحد فقط لكل مستخدم لكل Delivery (Dedup مضمونة بقيد DB)';
CREATE INDEX idx_recipients_user_ref_id ON ntf.recipients (user_ref_id);
CREATE INDEX idx_recipients_status ON ntf.recipients (status);

-- Transactional Outbox Pattern (مطلوب صراحة من المالك — انظر مراجعة جاهزية التنفيذ NTF v1.1)
CREATE TABLE ntf.outbox (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    delivery_id     UUID NOT NULL REFERENCES ntf.deliveries(id),
    recipient_id    UUID NOT NULL REFERENCES ntf.recipients(id),
    correlation_id  UUID NOT NULL,
    dispatched      BOOLEAN NOT NULL DEFAULT false,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE ntf.outbox IS 'نمط Transactional Outbox: تُكتَب ضمن نفس معاملة إنشاء Recipient؛ Outbox Worker يستطلعها لاحقًا';
CREATE INDEX idx_outbox_pending ON ntf.outbox (created_at) WHERE dispatched = false;

CREATE TABLE ntf.templates (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    code                  VARCHAR(64) NOT NULL UNIQUE,
    status                VARCHAR(16) NOT NULL DEFAULT 'active',
    current_version_number INT NOT NULL DEFAULT 1,

    CONSTRAINT chk_templates_status CHECK (status IN ('active', 'archived'))
);
COMMENT ON TABLE ntf.templates IS 'BR-NTF-006: لا حذف فعلي؛ الأرشفة فقط';

CREATE TABLE ntf.template_versions (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    template_id       UUID NOT NULL REFERENCES ntf.templates(id),
    version_number    INT NOT NULL,
    title             VARCHAR(200) NOT NULL,
    body              TEXT NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT uq_template_versions_template_version UNIQUE (template_id, version_number)
);
COMMENT ON TABLE ntf.template_versions IS 'BR-NTF-006: جدول Append-Only بالكامل؛ لا UPDATE ولا DELETE على صف قائم أبدًا';
CREATE INDEX idx_template_versions_template_id ON ntf.template_versions (template_id);
-- ملاحظة تصميمية: لا صلاحيات UPDATE أو DELETE يجب أن تُمنَح لهذا الجدول على مستوى قاعدة البيانات
-- (REVOKE UPDATE, DELETE ON ntf.template_versions) — يُطبَّق فعليًا عند التنفيذ الحي، غير مُفعَّل هنا.

CREATE TABLE ntf.channel_providers (
    code             VARCHAR(32) PRIMARY KEY,
    display_name     VARCHAR(64) NOT NULL,
    health_status    VARCHAR(16) NOT NULL DEFAULT 'healthy',
    last_success_at  TIMESTAMPTZ,
    last_failure_at  TIMESTAMPTZ,
    success_rate_pct NUMERIC(5,2),
    is_enabled       BOOLEAN NOT NULL DEFAULT true,

    CONSTRAINT chk_channel_providers_health CHECK (health_status IN ('healthy', 'degraded', 'offline'))
);
COMMENT ON TABLE ntf.channel_providers IS 'REQ-NTF-018, 044: سجل صحة كل مزوِّد قناة';
CREATE INDEX idx_channel_providers_health ON ntf.channel_providers (health_status);

CREATE TABLE ntf.notification_preferences (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_ref_id             UUID NOT NULL,          -- إشارة مرجعية لـ iam.users
    channel_provider_code   VARCHAR(32) NOT NULL REFERENCES ntf.channel_providers(code),
    notification_type       VARCHAR(32) NOT NULL,
    is_enabled              BOOLEAN NOT NULL DEFAULT true,

    CONSTRAINT uq_notification_preferences UNIQUE (user_ref_id, channel_provider_code, notification_type)
);
COMMENT ON TABLE ntf.notification_preferences IS 'REQ-NTF-033, 034: تفضيلات لكل مستخدم ولكل قناة ولكل نوع إشعار';
CREATE INDEX idx_notification_preferences_user ON ntf.notification_preferences (user_ref_id);

CREATE TABLE ntf.notification_center_entries (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    recipient_id            UUID NOT NULL REFERENCES ntf.recipients(id),
    user_ref_id             UUID NOT NULL,          -- إشارة مرجعية لـ iam.users (مُكرَّرة قصدًا لتسريع الاستعلام حسب المستخدم)
    is_read                 BOOLEAN NOT NULL DEFAULT false,
    is_archived_by_user     BOOLEAN NOT NULL DEFAULT false,
    is_deleted_by_user      BOOLEAN NOT NULL DEFAULT false,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE ntf.notification_center_entries IS 'REQ-NTF-036: حذف نسبي بالمستخدم فقط؛ لا حذف فعلي';
CREATE INDEX idx_notification_center_user_ref_id ON ntf.notification_center_entries (user_ref_id);
CREATE INDEX idx_notification_center_is_read ON ntf.notification_center_entries (is_read);
