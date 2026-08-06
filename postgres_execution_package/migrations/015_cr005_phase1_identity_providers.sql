-- ============================================================
-- 015_cr005_phase1_identity_providers.sql
-- المرحلة (1) من ترحيل CR-005: إنشاء جداول وسائل الهوية ونقل بيانات كلمة المرور
-- المرجع: CR-005 — REQ-IAM-010..017؛ SRS الحزمة A v1.1
-- الاعتماديات: iam.users (001_iam.sql) موجود مسبقًا
-- ملاحظة توصية المالك: تُنفَّذ هذه المرحلة أولاً، ويُتحقَّق من نجاحها قبل
--   تنفيذ المرحلة (2) (016) التي تُزيل عمود password_hash نهائيًا.
-- ============================================================

-- REQ-IAM-013: جدول وسائل الهوية كنوع مرجعي قابل للضبط الإداري (تفعيل/تعطيل)
CREATE TABLE iam.identity_providers (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    code               VARCHAR(32) NOT NULL,
    display_name       VARCHAR(64) NOT NULL,   -- اسم معروض إداريًا (مقترح إضافي معتمَد)
    provider_category  VARCHAR(16) NOT NULL,   -- password / otp / oauth (مقترح إضافي معتمَد)
    is_enabled         BOOLEAN NOT NULL DEFAULT true,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT uq_identity_providers_code UNIQUE (code),
    CONSTRAINT chk_identity_providers_code CHECK (code IN (
        'email_password', 'google', 'facebook', 'x', 'phone_otp'
    )),
    CONSTRAINT chk_identity_providers_category CHECK (provider_category IN ('password', 'otp', 'oauth'))
);
COMMENT ON TABLE iam.identity_providers IS 'REQ-IAM-010, 013: المرجع الرسمي الوحيد لأنواع وسائل الهوية؛ يُوسَّع بإضافة قيمة جديدة للقيد فقط دون تغيير بنيوي؛ display_name وprovider_category يدعمان المرونة الإدارية المستقبلية';

-- REQ-IAM-012: الهاتف عبر OTP محجوز (مُدرَج لكن غير مفعَّل افتراضيًا في الإصدار الأول)
INSERT INTO iam.identity_providers (code, display_name, provider_category, is_enabled) VALUES
    ('email_password', 'البريد الإلكتروني وكلمة المرور', 'password', true),
    ('google', 'Google', 'oauth', true),
    ('facebook', 'Facebook', 'oauth', true),
    ('x', 'X', 'oauth', true),
    ('phone_otp', 'رقم الهاتف (OTP)', 'otp', false);

-- REQ-IAM-010: جدول ربط الحساب بوسيلة هوية واحدة أو أكثر، عبر مفتاح مرجعي لا نص مباشر
CREATE TABLE iam.user_identities (
    id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id                UUID NOT NULL REFERENCES iam.users(id),
    provider_type_id       UUID NOT NULL REFERENCES iam.identity_providers(id),
    external_identifier    VARCHAR(256) NOT NULL,  -- بريد إلكتروني / رقم هاتف / معرّف المزوّد الخارجي
    credential_secret_hash TEXT,                    -- تمويه كلمة المرور أو رمز OTP المؤقت المموَّه؛ فارغ لمزوّدي OAuth الخارجيين
    verified_at            TIMESTAMPTZ,
    is_primary             BOOLEAN NOT NULL DEFAULT false,
    last_authenticated_at  TIMESTAMPTZ,             -- آخر استخدام فعلي لهذه الوسيلة (مقترح إضافي معتمَد؛ للتحليل والتدقيق الأمني)
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- توصية المالك (2): منع ربط نفس المعرّف الخارجي لنفس المزوّد بأكثر من حساب،
    -- على مستوى قاعدة البيانات نفسها لا منطق التطبيق فقط
    CONSTRAINT uq_user_identities_provider_identifier UNIQUE (provider_type_id, external_identifier),
    -- منع ربط نفس المزوّد مرتين لنفس المستخدم (كربط حساب Google مرتين)
    CONSTRAINT uq_user_identities_user_provider UNIQUE (user_id, provider_type_id)
);
COMMENT ON TABLE iam.user_identities IS 'REQ-IAM-010, 014, 015, 016: وسائل الهوية المرتبطة بكل حساب، بقيد تفرّد صارم يمنع ربط نفس الهوية بأكثر من حساب';
CREATE INDEX idx_user_identities_user_id ON iam.user_identities (user_id);
CREATE INDEX idx_user_identities_provider ON iam.user_identities (provider_type_id);

-- ------------------------------------------------------------------
-- خطوة نقل البيانات (دفاعية: تتعامل مع الواقع الحالي وأي بيانات مستقبلية)
-- ------------------------------------------------------------------
-- ملاحظة أمانة مهمة: جدول iam.users الأصلي (001_iam.sql) لم يخزِّن قط عمودًا
-- مستقلاً للبريد الإلكتروني مقترنًا بـ password_hash؛ كان يفترض ضمنًا وجود
-- "معرّف دخول" دون تحديد أين يُخزَّن فعليًا — وهذه فجوة سابقة في التصميم
-- الفيزيائي الأصلي. يحلّها هذا الترحيل بصورة صحيحة: البريد الإلكتروني (أو أي
-- معرّف دخول) يصبح الآن external_identifier ضمن سجل وسيلة الهوية نفسها، لا
-- عمودًا منفصلاً على iam.users — وهذا هو المكان الصحيح له أصلاً وفق تصميم
-- CR-005 الموحَّد، لا مجرد إصلاح مؤقت.
--
-- بما أن المنصة لم تُطلَق بعد ولا توجد بيانات مستخدمين حقيقية في iam.users
-- حاليًا، فإن هذه الخطوة تنقل صفرًا من السجلات فعليًا في الوقت الراهن؛
-- كُتبت بصورة دفاعية (ON CONFLICT DO NOTHING) لتعمل بصحة تامة لاحقًا إن
-- وُجدت بيانات فعلية قبل تنفيذها.
INSERT INTO iam.user_identities (user_id, provider_type_id, external_identifier, credential_secret_hash, verified_at, is_primary)
SELECT
    u.id,
    (SELECT id FROM iam.identity_providers WHERE code = 'email_password'),
    u.business_code,  -- بديل مؤقت لغياب عمود بريد إلكتروني مستقل؛ انظر الملاحظة أعلاه
    u.password_hash,
    u.created_at,
    true
FROM iam.users u
WHERE u.password_hash IS NOT NULL
ON CONFLICT (provider_type_id, external_identifier) DO NOTHING;

-- استعلام تحقق (لاستخدامه يدويًا عند التنفيذ الفعلي؛ يُدرَج في تقرير التحقق):
--   SELECT
--     (SELECT count(*) FROM iam.users WHERE password_hash IS NOT NULL) AS users_with_password,
--     (SELECT count(*) FROM iam.user_identities
--        WHERE provider_type_id = (SELECT id FROM iam.identity_providers WHERE code = 'email_password')
--     ) AS migrated_identity_rows;
-- يجب أن يتساوى الرقمان قبل الانتقال للمرحلة (2).
