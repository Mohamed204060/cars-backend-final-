-- ============================================================
-- 032_media_foundation.sql — Batch 2 Unit 1
-- المرجع: CarsMaint Media Foundation — Approved Baseline v1.0 (الحاكمة،
-- تُلغي أي صياغة أقدم متعارضة معها صراحةً)
-- الاعتماديات: لا شيء (نطاق مستقل بالكامل؛ owner_ref_id في attachments
-- إشارة Polymorphic بلا FK لأي Domain آخر، بنفس مبدأ SSOT القائم في كل
-- المشروع لحقول *_ref_id العابرة للنطاق).
--
-- Boundary (§1): media.assets = الملف ودورة حياته التقنية فقط؛ لا
-- owner_type/owner_ref_id هنا إطلاقًا. media.attachments = الربط بسياق
-- Business فقط.
-- ============================================================

CREATE SCHEMA IF NOT EXISTS media;

-- ---------------------------------------------------------------
-- §2-3: media.assets — الشكل المعتمد حرفيًا. لا عمود visibility (§9:
-- الـSanitized Master دائمًا Private؛ الرؤية تُشتَق من Binding/Policy،
-- لا تُخزَّن هنا).
-- ---------------------------------------------------------------
CREATE TABLE media.assets (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    storage_key             VARCHAR(512) NULL,  -- الـSanitized Master فقط؛ NULL حتى status='ready' (§2)
    storage_key_display     VARCHAR(512) NULL,  -- NULL حتى ready
    storage_key_thumbnail   VARCHAR(512) NULL,  -- NULL حتى ready
    original_file_name      VARCHAR(255) NOT NULL,  -- Metadata فقط (§4)؛ لا يُستخدَم في أي storage_key (كلها UUID/random داخليًا)
    mime_type                VARCHAR(64) NULL,   -- يُحسَم فعليًا بعد Magic Bytes+Decode (§12)، لا من امتداد/MIME معلَن
    size_bytes               BIGINT NULL,        -- حجم Sanitized Master بعد المعالجة؛ NULL حتى ready
    checksum                 CHAR(64) NULL,       -- SHA-256 hex للـSanitized Master فقط (§2)؛ NULL حتى ready
    width                    INT NULL,
    height                   INT NULL,
    status                   VARCHAR(16) NOT NULL DEFAULT 'pending',
    uploaded_by_user_ref_id  UUID NOT NULL,  -- إشارة مرجعية لـ iam.users؛ بلا FK فعلي (SSOT، نفس نمط المشروع)
    created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    archived_at              TIMESTAMPTZ NULL,  -- §3: Soft Archive (Retention/Audit)، مستقل عن purged_at
    purged_at                TIMESTAMPTZ NULL,  -- §3: حذف ملفات Storage فعليًا؛ Metadata (هذا الصف) يبقى

    CONSTRAINT chk_media_assets_status
        CHECK (status IN ('pending', 'processing', 'ready', 'failed', 'archived'))
);
COMMENT ON TABLE media.assets IS
    'Media Foundation Approved Baseline v1.0 §2-3: الملف ودورة حياته التقنية '
    'فقط. Lifecycle: pending → processing → ready|failed → archived. لا '
    'Binding قبل ready. لا عمود visibility — الرؤية تُشتَق من '
    'media.attachments.owner_type وقت القراءة (§9)، لا تُخزَّن هنا.';
COMMENT ON COLUMN media.assets.storage_key IS 'مفتاح Sanitized Master فقط (Private دائمًا). NULL حتى تكتمل المعالجة.';
COMMENT ON COLUMN media.assets.checksum IS 'SHA-256 hex للـSanitized Master فقط، بعد Strip EXIF وRe-encode — لا للملف الخام المرفوع.';
COMMENT ON COLUMN media.assets.archived_at IS 'Soft Archive: الملفات لا تزال موجودة في Storage (Retention/Audit). مستقل عن purged_at (§3/§11).';
COMMENT ON COLUMN media.assets.purged_at IS 'حذف فعلي لملفات Storage. هذا الصف (Metadata) يبقى بعد Purge للتدقيق (§11).';

CREATE INDEX idx_media_assets_status ON media.assets (status);
CREATE INDEX idx_media_assets_uploaded_by ON media.assets (uploaded_by_user_ref_id);

-- ---------------------------------------------------------------
-- §5: media.attachments — ربط Asset بسياق Business. asset_ref_id FK
-- داخلي حقيقي (نفس نطاق media، ليس عابرًا للنطاق) ON DELETE RESTRICT —
-- يمنع حذف Asset مربوط فعليًا (يجب Archive الربط أولًا). owner_ref_id
-- Polymorphic بلا FK (يشير لـ pur.purchase_requests / pur.offers /
-- str.inventory_items بحسب owner_type، عبر نطاقات مختلفة تمامًا).
-- ---------------------------------------------------------------
CREATE TABLE media.attachments (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    asset_ref_id   UUID NOT NULL REFERENCES media.assets(id) ON DELETE RESTRICT,
    owner_type     VARCHAR(24) NOT NULL,
    owner_ref_id   UUID NOT NULL,  -- Polymorphic؛ بلا FK فعلي عمدًا (SSOT عابر للنطاق، نفس نمط كل *_ref_id الأخرى في المشروع)
    sort_order     INT NOT NULL,
    status         VARCHAR(16) NOT NULL DEFAULT 'active',
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT chk_media_attachments_owner_type
        CHECK (owner_type IN ('purchase_request', 'offer', 'inventory_item')),
    CONSTRAINT chk_media_attachments_status
        CHECK (status IN ('active', 'archived')),
    CONSTRAINT uq_media_attachments_asset UNIQUE (asset_ref_id)
);
COMMENT ON TABLE media.attachments IS
    'Media Foundation Approved Baseline v1.0 §5: SSOT لكل ربط Asset↔Business '
    'Entity. UNIQUE(asset_ref_id): كل Asset يُربَط مرة واحدة على الإطلاق '
    '(حتى لو أُرشِف الربط لاحقًا — لا إعادة تدوير Asset لربط آخر). '
    'sort_order يُحسَب من الخادم (MAX+1) داخل قفل + معاملة واحدة (§6/§15) — '
    'لا اعتماد على تسلسل قاعدة البيانات وحده لضمان الترتيب المتزامن الصحيح.';
COMMENT ON COLUMN media.attachments.owner_ref_id IS
    'Polymorphic: pur.purchase_requests.id أو pur.offers.id أو '
    'str.inventory_items.id بحسب owner_type. بلا FK فعلي (عابر للنطاق).';

-- فهرس أساسي لاستعلام "كل مرفقات owner معيَّن" (القراءة الأكثر شيوعًا:
-- عرض صور طلب/عرض/عنصر مخزون محدَّد) — يخدم أيضًا فحص حد الـ5 صور (§6)
CREATE INDEX idx_media_attachments_owner ON media.attachments (owner_type, owner_ref_id, status);
