# postgres_execution_package — الحزمة الكاملة المكتفية ذاتيًا

## تحديث (بعد أول تشغيل فعلي — 4 إخفاقات في Repository Tests)

تم رصد وإصلاح 4 مشكلات بعد أول تشغيل حقيقي على GitHub Actions:

| # | المشكلة | السبب الجذري | الإصلاح |
|---|---|---|---|
| 1 | `pricing_mode="contact_for_price"` يتجاوز `VARCHAR(16)` رغم Migration 022 | **خطئي أنا**: `scripts/setup_test_database.sh` يحتوي قائمة `REQUIRED_FILES` مُكوَّدة يدويًا كانت لا تزال تتوقف عند 021 — لم تكن تُطبِّق 022 أو 023 إطلاقًا رغم وجودهما في مجلد migrations/. النسخة السابقة من هذا الملف نُسخَت من الحزمة الأصلية الأولى، قبل أي CR، دون تحديثها. | إضافة `022_postgresql_validation_runtime_fixes.sql` وَ`023_iam_sessions.sql` إلى `REQUIRED_FILES` |
| 2 | `TestPostgresTrmRepository`: لم يُرفع `DuplicateRatingError` | خطأ في الاختبار: المحاولة الثانية استخدمت `target_ref_id` **مختلفًا** عشوائيًا، بينما قيد التفرّد الفعلي `uq_ratings_rater_target_source` يشمل `target_ref_id` ضمن المفتاح المركَّب — فلم يقع أي تعارض تفرّد أصلاً | إعادة استخدام نفس `target_ref_id` في كلا الاستدعاءين |
| 3 | `TestPostgresPctRepository`: FK على `categories` | الاختبار مرَّر `category_id` عشوائيًا لا يقابله صف حقيقي في `pct.categories` (لا توجد دالة `insert_category` في المستودع لإدارتها) | إدراج صف فئة حقيقي مباشرة (`INSERT INTO pct.categories DEFAULT VALUES`) قبل استخدام مُعرِّفه |
| 4 | `TestPostgresVctRepository`: `InvalidTextRepresentation` | الاختبار مرَّر `"nonexistent-trim-id"` — سلسلة نصية ليست بصيغة UUID، بينما العمود مُعرَّف `UUID` فعليًا؛ "عدم الوجود" غير "صيغة غير صالحة" | استبدالها بـ`str(uuid.uuid4())` — UUID صحيح الصيغة لكن غير موجود فعليًا |

**لا تعديل على أي منطق Repository أو Migration بسبب الجولة الأولى** — الإصلاحات الثلاثة اختبارية بحتة؛ إصلاح الـWorkflow/Script سكربتي بحت.

## تحديث ثانٍ (بعد ثاني تشغيل — 1 إخفاق متبقٍ)

| # | المشكلة | السبب | الإصلاح |
|---|---|---|---|
| 5 | `TestPostgresInventoryItemRepository`: `ForeignKeyViolation` على `store_id` | `str.inventory_items.store_id` يحمل `REFERENCES str.stores(id)` فعليًا (خلافًا لـ`catalog_part_ref_id`/`condition_ref_id`، إشارتان وصفيتان فقط بلا FK حقيقي في هذا الجدول تحديدًا) — الاختبار كان يمرِّر UUID عشوائيًا لا يقابله صف متجر حقيقي | إدراج صف `str.stores` حقيقي مباشرة (`owner_user_ref_id` لا يحمل FK فعليًا، فقيمة عشوائية تكفي) قبل استخدام `store_id` الناتج |

## تحديث ثالث (بعد ثالث تشغيل — 5 إخفاقات في test_auth_api.py، كلها بسبب واحد)

| # | المشكلة | السبب | الإصلاح |
|---|---|---|---|
| 6 | كل المسارات المحمية تُعيد 401 رغم نجاح تسجيل الدخول | `TestClient(app)` الافتراضي يعمل على `http://testserver`؛ الجلسة تُصدَر بخاصية `Secure=True` (لم تُعطَّل، ولن تُعطَّل)، وCookies من نوع Secure لا تُرسَل إلا فوق HTTPS — فلا يُعيد العميل إرسالها في أي طلب لاحق | `TestClient(app, base_url="https://testserver")` — تشخيص وحل من مالك المشروع، طُبِّق حرفيًا |

اختباري بحت (Fixture فقط)؛ `Secure=True` بقي كما هو في `auth_api.py` دون أي تعديل.

**اعتذار وتصحيح:** الحزمة السابقة (`CR-013_v2_execution_package.zip`) كانت
Delta فقط (الملفات الجديدة/المعدَّلة حصرًا)، لا حزمة مكتفية ذاتيًا — وهذا
تسبَّب في فشل التشغيل لديكم (`scripts/teardown_test_database.sh: No such
file or directory`). هذه النسخة كاملة: **استبدلوا مجلد `postgres_execution_package/`
في المستودع بمحتوى هذا الأرشيف بالكامل، حرفيًا، دون دمج انتقائي.**

## تحقَّقتُ محليًا من كل شرط يفحصه الـWorkflow نفسه قبل الإرسال
```
FOUND: README.md
FOUND: requirements.txt
FOUND: scripts/setup_test_database.sh
FOUND: scripts/teardown_test_database.sh
FOUND: tools/schema_drift_check.py
FOUND: tests/conftest.py
FOUND: tests/test_postgres_repositories.py
FOUND: tests/test_postgres_integration.py
migration_count = 24  ✓ (يطابق الـPatch المعتمَد على الـWorkflow)
```
كما تحقَّقت من أن كل ملف Python الـ42 في الحزمة (لا الجديد فقط) صحيح النحو
(`ast.parse` على كل ملف، بلا استثناء).

## مصدر كل ملف (لا شيء افتراضي أو مخمَّن)

### أصلي دون أي تعديل (من الحزمة المعتمَدة أول مرة، Release Verification Report)
- `README.md`
- `scripts/setup_test_database.sh`, `scripts/teardown_test_database.sh`
- `migrations/000` حتى `021` (22 ملفًا)
- `tests/conftest.py`
- كل الخدمات الـ13 **عدا** `auth`، `order`، `inventory_item` (كما هي، دون مساس)

### CR-011 (تغطية Schema Drift الكاملة + اختبار Auth حقيقي)
- `tests/test_postgres_repositories.py`

### CR-012 (حزمة إصلاحات التنفيذ الحي)
- `migrations/022_postgresql_validation_runtime_fixes.sql`
- `services/order/src/order_repository.py`
- `services/inventory_item/src/inventory_item_repository.py`
- `tests/test_postgres_integration.py`

### CR-013 v1+v2 (جلسات + بيانات اعتماد كلمة المرور + REST API) — أحدث نسخة تراكمية
- `migrations/023_iam_sessions.sql`
- `tools/schema_drift_check.py` (53 جدولًا، شامل PK/FK)
- `services/auth/src/auth_repository.py` (يشمل إصلاح CR-012 + إضافات CR-013 معًا)
- `services/auth/src/auth_service.py`
- `services/auth/src/credential_service.py` **(جديد)**
- `services/auth/src/session_service.py` **(جديد)**
- `services/auth/src/session_repository.py` **(جديد)**
- `services/auth/src/auth_api.py` **(جديد)**
- `tests/test_session_service.py` **(جديد)**
- `tests/test_credential_service.py` **(جديد)**
- `tests/test_auth_api.py` **(جديد)**
- `tests/test_postgres_auth_sessions_integration.py` **(جديد)**
- `tests/test_postgres_auth_credentials_integration.py` **(جديد)**
- `requirements.txt` (+fastapi, +httpx)

## ملف الـWorkflow
يُرفَع بشكل منفصل تمامًا (`postgresql-validation.yml`، أرسلته في الرسالة
السابقة) إلى `.github/workflows/postgresql-validation.yml`. لا علاقة له
بمحتوى هذا الأرشيف؛ الأرشيف الحالي يخص مجلد `postgres_execution_package/`
فقط.

## ما لم يتغيَّر ولا يجوز افتراض تغييره
`services/{cmp,message,message_extended,ntf,pct,scheduler,search,store,trm,vct}/src/*`
كما هي منذ الاعتماد الأول. `full_regression/` (خارج نطاق هذا الأرشيف
تمامًا؛ ملاحظة سابقة: كودها لِـauth منفصل ولن يتأثر بهذه الحزمة).

## تحديث رابع (بعد ملاحظتكم: schema-drift.json ما زال 52 جدولًا)

**ليس مقصودًا وليس خارج نطاق CR-013** — خطأ تعبئة مني: عند بناء هذه الحزمة، نُسخ `tools/schema_drift_check.py` من نسخة عمل محلية كانت قد فقدت إدخال `iam.sessions` سهوًا. النسخة الصحيحة (53 جدولًا، شاملة `iam.sessions`) كانت موجودة وسُلِّمت فعليًا كملف مستقل ضمن تسليم CR-013 v1، لكنها لم تُدرَج في نسخة الحزمة الكاملة هذه. الدليل نفسه يؤكد المشكلة: `table-list.txt` من تشغيلكم الأخير يُظهر 53 صفًا فعليًا في قاعدة البيانات الحية (شاملة `iam.sessions`)، بينما الأداة فحصت 52 فقط.

**الإصلاح:** استبدال `tools/schema_drift_check.py` بالنسخة الصحيحة (53 جدولًا، 53 PK، 34 FK، شاملة `iam.sessions`). لا تعديل آخر على أي ملف.

## تحديث خامس — CR-014: عضوية Free دائمة لكل بائع

قرار حوكمي: عضوية Free متاحة دومًا لكل بائع؛ انتهاء خطة مدفوعة يعيده تلقائيًا
لـFree لا إلى حالة مسدودة؛ لا يُمنع من إنشاء مخزون أو استقبال طلبات بسبب
انتهاء الاشتراك وحده؛ الرفض فقط عند تجاوز حدود الخطة (خارج نطاق SUB) أو
إيقاف الحساب. تفاصيل القرار والأثر التقني في CR-014 (مرفق docx منفصل).

الملفات المتأثرة:
- `migrations/026_sub_free_plan.sql` **(جديد)** — عمود `sub.plans.is_free`،
  فهرس تفرّد لخطة Free وحيدة، بذر خطة Free + قيمة مرجعية `subscription_type=free`،
  السماح بـ`expires_at NULL` في `sub.seller_subscriptions`.
- `services/sub/src/sub_service.py` — منطق الترقية من Free، والعودة التلقائية
  لـFree عند الانتهاء (Lazy Check)، بدل حالة `expired` مسدودة.
- `services/sub/src/sub_repository.py` — `get_free_plan()` في كلا التنفيذين
  (Postgres + InMemory)؛ `Plan.is_free`.
- `services/sub/src/sub_api.py` — `PlanResponse.is_free`.
- `scripts/setup_test_database.sh` — إضافة `026_sub_free_plan.sql` لقائمة
  `REQUIRED_FILES` (نفس فخ المشكلة #1 أعلاه؛ عولج مسبقًا هذه المرة).
- `api_spec/openapi.yaml` → v1.9.0 — `PlanResponse.is_free`، توصيف
  `POST /subscriptions` و`GET /subscriptions/mine` المحدَّث.
- `tests/test_sub_api.py` — اختبارات محدَّثة/مضافة تعكس السلوك الجديد.

لا مساس بأي خدمة أخرى (Inventory/Orders/إلخ)؛ إنفاذ حدود الخطط يبقى خارج
نطاق SUB بتصريح صريح في CR-014، لتفعيله لاحقًا عبر CR منفصلة إن قُرِّر.

## تحديث خامس — إضافة PCT REST API (بعد اعتماد PCT Contract Extension)

### ملفات جديدة
- `services/pct/src/pct_api.py` — طبقة REST (5 عمليات): propose, get, approve (فحص صلاحية admin/super_admin)، إضافة اسم، تسجيل OEM.
- `tests/test_pct_api.py` — اختبارات وحدة (InMemory)، 15 اختبارًا، شاملة اختبارات صلاحية approve الستة (buyer مرفوض، seller مرفوض، admin مقبول، super_admin مقبول، قطعة غير موجودة 404، اعتماد مزدوج 409).
- `tests/test_postgres_pct_api_integration.py` — تكامل حي: FK حقيقي على category_id، صلاحية admin حقيقية عبر iam.users.primary_role، وتفرّد OEM حقيقي عبر القيد.

### ملفات معدَّلة (إضافة فقط، لا حذف)
- `services/pct/src/pct_service.py`: إضافة `add_localized_name_via_repository()` (كان الغلاف مفقودًا رغم وجود الدالة النقية).
- `services/auth/src/auth_repository.py`: إضافة `get_user_role()` للتحقُّق من `primary_role` (فحص موضعي REQ-PCT-002؛ لا RBAC عام).
- `api_spec/openapi.yaml`: v1.2.0 — +5 عمليات PCT (14 مسارًا، 15 عملية إجمالًا).
- `.github/workflows/postgresql-validation.yml` (يُرفَع منفصلًا): +2 خطوة (`pct-api-tests`، `pct-integration-tests`).

### REQ-PCT-006 (Aftermarket)
مؤجَّل صراحةً كما تقرَّر — لا Repository ولا Service ولا API له في هذه الدفعة.

## تحديث سادس — إضافة VCT REST API (بعد اعتماد VCT Contract Extension)

### ملفات جديدة
- `services/vct/src/vct_api.py` — 7 عمليات: propose/get/approve manufacturer (فحص صلاحية admin/super_admin نفسه)، propose model (يتحقق REQ-VCT-003: الشركة معتمَدة)، create generation، create trim، get trim.
- `tests/test_vct_api.py` — 12 اختبار وحدة، شاملة تسلسل manufacturer→model→generation→trim الكامل الذي تحتاجه CMP لاحقًا.
- `tests/test_postgres_vct_api_integration.py` — 5 اختبارات تكامل حي.

### ملفات معدَّلة (إضافة فقط)
- `services/vct/src/vct_repository.py`: إضافة `get_model_by_id()` وَ`get_generation_by_id()` (كانتا مفقودتين رغم وجود `insert_model`/`insert_generation`).
- `services/vct/src/vct_service.py`: إضافة `propose_model_via_repository()` (يتحقق REQ-VCT-003 فعليًا — لم يكن مُتحقَّقًا من قبل)، `create_generation_via_repository()`، `create_trim_via_repository()`.
- `api_spec/openapi.yaml`: v1.3.0 — +7 عمليات VCT (21 مسارًا، 22 عملية إجمالًا).
- `.github/workflows/postgresql-validation.yml`: +2 خطوة (`vct-api-tests`، `vct-integration-tests`).

### مؤجَّل عمدًا (كما تقرَّر)
- REQ-VCT-006 (الاسم متعدد اللغات) — جدول موجود، لا Repository/Service.
- REQ-VCT-007 (أرشفة تتالية تؤثر على CMP) — يُبنى مع/بعد CMP مباشرة.

## تحديث سابع — إضافة CMP REST API (بعد اعتماد CMP Contract Extension)

### اكتشاف مهم أثناء الجرد
حقول `fitment_type`/`compatibility_notes`/`source` في `CompatibilityRecord` (Python) **لا تقابلها أعمدة في قاعدة البيانات إطلاقًا** (007_cmp.sql يحتوي فقط: catalog_part_ref_id, trim_ref_id, status). هذا موثَّق أصلاً في تعليق الكود نفسه كـ"Backlog" (مقترح مالك سابق لم يُستكمَل). **لم يُضَف أي عمود جديد** — العقد يقتصر على الحقول الثلاثة المخزَّنة فعليًا فقط، تفاديًا لعقد يَعِد بحفظ بيانات لا تُخزَّن فعليًا.

### ملفات جديدة
- `services/cmp/src/cmp_api.py` — 4 عمليات: إنشاء سجل توافق (يتحقق فعليًا من PCT+VCT عبر الحقن، لا استعلام مباشر)، عرض سجل، عرض سجلات قطعة، أرشفة (REQ-CMP-003).
- `tests/test_cmp_api.py` — 12 اختبارًا، يستخدم PCT/VCT الحقيقيتين (لا Fixtures منفصلة) لبناء قطعة معتمَدة وفئة صالحة فعليًا قبل اختبار CMP، إثباتًا لعمل SSOT عبر الخدمات الثلاث معًا.
- `tests/test_postgres_cmp_api_integration.py` — 4 اختبارات تكامل حي، تشمل تفرّد `uq_compatibility_part_trim` الفعلي.

### ملفات معدَّلة (إضافة فقط)
- `services/cmp/src/cmp_service.py`: إضافة `archive_compatibility_record_via_repository()` وَ`CompatibilityRecordNotFoundError` (كان الغلاف مفقودًا).
- `services/cmp/src/cmp_repository.py`: إضافة `get_record_by_id()` (Postgres + InMemory).
- `api_spec/openapi.yaml`: v1.4.0 — +4 عمليات CMP (25 مسارًا، 26 عملية إجمالًا).
- `.github/workflows/postgresql-validation.yml`: +2 خطوة (`cmp-api-tests`، `cmp-integration-tests`).

### صلاحيات
REQ-CMP-001 وREQ-CMP-003 كلاهما "مدير النظام حصريًا" — نفس فحص `SYSTEM_ADMIN_ROLES` من PCT/VCT حرفيًا، بلا تكرار منطق (استيراد مباشر من `pct_api.py`).

## تحديث ثامن — إضافة Search REST API (الدفعة 1: CMP + Search)

### لا امتداد عقد جديد
`GET /search/parts` كان موثَّقًا بالكامل أصلًا ضمن الشريحة الأولى المعتمَدة في `openapi.yaml` (5 عمليات أصلية) — لم يكن مُنفَّذًا فقط. لا تغيير على `openapi.yaml` في هذا التحديث.

### ملفات جديدة
- `services/search/src/search_api.py` — ينفِّذ `GET /search/parts` عبر `execute_search_via_repository()` الموجودة فعليًا وجاهزة للاستدعاء المباشر.
- `tests/test_search_api.py` — 10 اختبارات وحدة (InMemory)، شغَّلت المنطق الأساسي (فلترة، تقسيم صفحات، الدولة الفعّالة) **فعليًا يدويًا هنا (5/5 PASSED)** قبل التسليم.
- `tests/test_postgres_search_api_integration.py` — 3 اختبارات تكامل حي، **الأهم بينها**: يثبت أن البحث يستخدم `cmp.compatibility_records` الحقيقية (لا محاكاة) لتصفية النتائج حسب `trim_ref_id`، مستخدمًا سلسلة PCT→VCT→CMP الكاملة المبنية في هذه الدفعة والدفعة السابقة.

### فجوات نطاق موثَّقة صراحة (لا تُخفى)
- معاملا `q` (نص حر) وَ`sort` (ترتيب مخصَّص) موجودان في العقد لكن **غير مُنفَّذين** في `search_service.py`؛ يُقبَلان بالطلب دون أي أثر.
- `account_country_code`/`geolocation_country_code`/`ip_country_code` (REQ-SRC-006-C) تتطلب مصادر بيانات غير موجودة بعد؛ يُستخدَم `country_ref_id` المُرسَل من العميل كـ`manual` فقط.
- `image_url` يُعاد `null` دائمًا — لا نظام تخزين صور مبني في المشروع بعد.

### بنية اختبار مؤقَّتة
`str.stores`/`str.inventory_items` لا REST API لهما بعد (الدفعة التالية: Store + Inventory)؛ اختبارات تكامل البحث تُنشئ بيانات هذين الجدولين مباشرة عبر SQL خام، بنفس أسلوب `pct.categories` قبل اكتمال عقد PCT.

## تحديث تاسع — إصلاح محدود: ترتيب إعداد اختبار CMP (بعد أول تشغيل للدفعة 1)

| # | المشكلة | السبب | الإصلاح |
|---|---|---|---|
| 7 | `TestCreateRecordAuthorization::test_regular_user_forbidden` فشل بـ`KeyError: 'id'` | الاختبار سجَّل الدخول كـ`individual_buyer` **قبل** تجهيز بيانات VCT/PCT، بينما اعتماد الشركة المصنّعة (VCT) واعتماد القطعة (PCT) يتطلبان `admin`/`super_admin` أيضًا — فشل إنشاء الموديل صامتًا (403) ثم انهار الاختبار عند محاولة قراءة `id` من استجابة فاشلة | إعادة ترتيب الاختبار: تسجيل دخول admin → تجهيز قطعة معتمَدة وفئة صالحة → **تسجيل خروج صريح** → تسجيل دخول buyer → محاولة الإنشاء → التحقق من 403 |

اختباري بحت (ترتيب Fixture فقط)؛ لا تعديل على `cmp_api.py` أو `vct_api.py` أو أي Migration، تمامًا كما طلب مالك المشروع. باقي الاختبارات في نفس الملف كانت تتبع الترتيب الصحيح أصلًا (تحقَّقت من كل استخدام لـ`_login_as` في الملف).

## تحديث عاشر — نفس الإصلاح على اختبار التكامل الحي لِCMP

| # | المشكلة | الإصلاح |
|---|---|---|
| 8 | `test_postgres_cmp_api_integration.py::TestCreateRecordOnLivePostgres::test_regular_buyer_forbidden_on_live_postgres` فشل بنفس نمط `KeyError: 'id'` | نفس الترتيب المُصحَّح: admin يُجهِّز البيانات → `logout` صريح → دخول buyer → محاولة الإنشاء → تحقق 403 |

**فحص استباقي إضافي:** راجعت كل استخدامات `role="individual_buyer"` في ملفات اختبارات PCT وVCT (وحدة وتكامل) للتأكد من عدم وجود نفس الخلل هناك — **جميعها آمنة**: تُنشئ الكيان بنفسها كـbuyer (propose لا يتطلب صلاحية) ثم تحاول اعتماده هي نفسها (لا اعتماد على سلسلة مُجهَّزة مسبقًا من admin). الخلل كان مقصورًا فعليًا على اختباري CMP فقط (وحدة وتكامل)، لأنهما الوحيدان اللذان يحتاجان بيانات جاهزة (قطعة معتمَدة + فئة صالحة) قبل اختبار الصلاحية نفسها.

## تحديث حادي عشر — الدفعة الثانية: Store + Inventory (+ إصلاح توافق حرج)

### إصلاح توافق حرج: بادئة `/api/v1/` كانت غائبة في توثيق 4 خدمات مُغلَقة
اكتشفت أن `openapi.yaml` كان يوثِّق مسارات Auth/PCT/VCT/CMP **بلا** بادئة `/api/v1/` (مثل `/auth/logout`)، بينما الكود الفعلي المُنفَّذ والمُختبَر يستخدم البادئة بالفعل (`/api/v1/auth/logout`) — مطابقةً لِDD الحزمة 2 ("تُعتمَد آلية إصدار صريحة... بصيغة `/api/v1/...` لكل واجهة"). **الكود صحيح؛ التوثيق فقط كان خاطئًا** — صُحِّح توثيقيًا بحتًا لكل الـ20 مسارًا القديمة دون أي تعديل على الكود أو الاختبارات. `search_api.py` كان الاستثناء الوحيد (كوده أيضًا كان بلا بادئة) — أُضيفت البادئة لكوده هو تحديدًا، وحُدِّثت اختباراته تبعًا لذلك.

### التزام بالعقد المعتمَد سلفًا لِ`/api/v1/inventory-items`
- `store_id` **لا يُرسَل في الطلب** — يُشتَق من `store_repo.get_store_by_owner_id(current_session.user_id)`.
- الاستجابة مختصرة: `{id, business_code, status}` فقط (لا الكائن الكامل).
- `Idempotency-Key` **مطلوب (required)** لهذه العملية تحديدًا؛ إعادة إرسال نفس المفتاح تُعيد نفس النتيجة دون إنشاء عنصر مكرَّر.

### ملفات جديدة
- `migrations/025_sys_idempotency_keys.sql` — جدول `sys.idempotency_keys` (لا تعديل على 000-023).
- `services/inventory_item/src/idempotency_service.py` + `idempotency_repository.py` — منطق التخزين/الإعادة (Postgres + InMemory). **نُفِّذ وتحقَّقت منه يدويًا فعليًا قبل التسليم (كل الفحوصات نجحت).**
- `services/store/src/store_api.py` — 4 عمليات (إنشاء/عرض متجر، تغيير حالة بصلاحية `admin`/`moderator` فقط REQ-STR-004، نقل ملكية بصلاحية `admin` حصرًا REQ-STR-006 — **نموذج صلاحيات مختلف عن كل الخدمات السابقة**، تحقَّقت من نصه في SRS مباشرة قبل التنفيذ).
- `services/inventory_item/src/inventory_item_api.py` — العملية الأصلية `/inventory-items` + 6 عمليات إدارة (كمية/تسعير/إخفاء/إظهار/أرشفة/عرض) بصلاحية **البائع المالك فقط** (REQ-STR-019 — نموذج صلاحيات ثالث مختلف: لا دور، بل ملكية فعلية للمتجر).
- اختبارات وحدة وتكامل لكل من Store وInventory، شاملة اختبار Idempotency الحقيقي على قاعدة بيانات حية (`sys.idempotency_keys` يُتحقَّق منه بعدد الصفوف فعليًا).

### ملفات معدَّلة
- `services/store/src/store_repository.py`: إضافة `get_store_by_owner_id()`.
- `services/inventory_item/src/inventory_item_service.py`: تمرير `is_part_approved_checker` كان مفقودًا في `create_inventory_item_via_repository()` (لم يكن يُستدعى فعليًا رغم دعم الدالة النقية له)؛ إضافة أغلفة `update_pricing_via_repository`/`hide_item_via_repository`/`unhide_item_via_repository` الناقصة.
- `tools/schema_drift_check.py`: 54 جدولًا (كان 53)، فهرس `idx_idempotency_keys_lookup` مُستكمَل.
- `scripts/setup_test_database.sh`: القائمة تشمل الآن Migration 025 (كانت تشير خطأً لملف غير موجود `024_idempotency_keys.sql` — **صُحِّح قبل التسليم**، لم يكن ليُكتشَف إلا بعد فشل التشغيل).
- `api_spec/openapi.yaml`: v1.5.0 — 35 مسارًا، 38 نموذج بيانات، بادئة `/api/v1/` موحَّدة عبر كل المسارات دون استثناء.
- `.github/workflows/postgresql-validation.yml`: +4 خطوات (`store-api-tests`، `store-integration-tests`، `inventory-api-tests`، `inventory-integration-tests`)، وتصحيح شرط `migration_count` إلى 25.

### مؤجَّل عمدًا (Backlog، موثَّق في `openapi.yaml` نفسه)
- آلية طلب التصحيح (REQ-STR-007/008) — الحقول غير مخزَّنة في قاعدة البيانات إطلاقًا.
- تعليق المتجر التلقائي عند حظر حساب البائع (REQ-STR-004 Cross-Ref مع REQ-IAM-005) — يتطلب آلية أحداث بين IAM وStore غير موجودة بعد.

## تحديث ثاني عشر — السبب الجذري الوحيد وراء كل إخفاقات Inventory (بعد تشخيصكم)

### السبب الجذري (مؤكَّد، لا 11 مشكلة مستقلة كما خُشِي)
دالة الإعداد `_make_approved_part()` في كلا ملفَي اختبار Inventory (الوحدة والتكامل الحي) كانت تستدعي `POST /pct/parts/{id}/approve` **تحت الجلسة الحالية أيًّا كانت** (غالبًا `individual_seller`)، دون التحقق من نجاح الاستجابة. بما أن اعتماد قطعة PCT يتطلب `admin`/`super_admin` (REQ-PCT-002)، كانت `approve` تفشل صامتًا بـ403، فتبقى القطعة `proposed` لا `approved`. أول طلب `POST /inventory-items` بعدها يفشل حتمًا بـ409 (`PART_NOT_APPROVED`)، وكل الاختبارات اللاحقة التي تعتمد على `item_id` الناتج تنهار بـ`KeyError: 'id'` — تمامًا كما شخَّصتم.

### فحص استباقي شامل قبل الإصلاح
راجعت كل تعريفات `_make_approved_part` عبر مجموعة الاختبارات كاملة (Search، CMP وحدة وتكامل، Inventory وحدة وتكامل) للتأكد من عدم تكرار هذا الخلل. **CMP وSearch سليمتان تمامًا** — كل استدعاء لـ`_make_approved_part` فيهما مسبوق مباشرة بتسجيل دخول `admin` صريح (نفس النمط الذي أصلحناه سابقًا في CMP). الخلل كان مقصورًا فعليًا على ملفَي Inventory فقط.

### الإصلاح (بنية واحدة، لا إصلاح اختبار تلو آخر)
أعيد بناء `_make_approved_part()` في كلا الملفَين لتُنفِّذ **جلسة `admin` مستقلة بالكامل داخليًا** (تسجيل دخول → اعتماد القطعة مع التحقق الصريح من نجاح الاستجابة `assert approve_resp.status_code == 200` → تسجيل خروج فوري)، ويجب استدعاؤها **قبل** تسجيل دخول البائع صاحب الاختبار الفعلي، لا بعده. أُعيد ترتيب كل الاختبارات الـ15 (وحدة) و5 (تكامل) وفق هذا النمط الموحَّد.

## تحديث ثالث عشر — الدفعة الثالثة: Orders/Purchase Requests + Messaging + Notifications

### إصلاح فجوة `business_code` إضافية (نفس نمط CR-012)
`PurchaseRequest` وَ`Offer` لم يكونا يُعيدان `business_code` رغم أن `PostgresOrderRepository` يولِّده داخليًا منذ البداية (نفس فجوة `InventoryItem` قبل CR-012، لم تُكتشَف/تُصلَح وقتها لهذين الكيانين). أُضيف الحقل لكلا الـdataclass، وصُحِّح كلا المستودعين (Postgres + InMemory) لإرجاعه فعليًا — **تحقَّق منه بتنفيذ حقيقي مباشر قبل التسليم**.

### ملفات جديدة
- `services/order/src/order_api.py` — 6 عمليات: إنشاء/عرض/إلغاء طلب شراء (REQ-PUR-009: المشتري المالك حصرًا)، تقديم/قبول/سحب عرض (REQ-PUR-011/013/018).
- `services/message/src/message_api.py` — إرسال/عرض/حذف نسبي رسالة (REQ-COM-001/002/007).
- `services/message_extended/src/message_extended_api.py` — نطاق مختصر عمدًا: تسجيل بيانات مرفق وصفية فقط.
- `services/ntf/src/ntf_api.py` — مركز الإشعارات فقط (عرض/تعليم مقروء/أرشفة)، مطابقةً للنطاق الموثَّق أصلًا داخل `ntf_service.py` نفسه.
- 4 ملفات اختبار وحدة + **ملف تكامل حي واحد شامل** (`test_postgres_orders_messaging_notifications_integration.py`) ينفِّذ السيناريو الكامل: طلب شراء → عرض → رسالة → إشعار، مع تحقق مباشر من قاعدة البيانات لكل خطوة (لا الطبقة العلوية فقط).

### مؤجَّل عمدًا (Backlog، موثَّق في `openapi.yaml` نفسه)
- إدارة حملات الإشعارات (Campaign/Delivery/Template/ChannelProvider) — لا أغلفة *_via_repository لها أصلًا.
- MessageExtended: الحضور، الكتم/الأرشفة لكل مستخدم، تتبع التسليم/القراءة — موجودة بالكود وجاهزة، مؤجَّلة REST لهذه الدفعة فقط.
- REQ-PUR-007/008/012 (تعديل حقول الطلب/العرض قبل أول عرض أو قبل القبول).
- إنفاذ عضوية طرفين صارم في المحادثات (Participants ACL حقيقي).

### إصلاح توثيقي إضافي
أُضيف `security: [{sessionAuth: []}]` المفقود على `/api/v1/purchase-requests/{requestId}/offers` (كان غائبًا منذ العقد الأصلي قبل أي عمل في هذا المشروع).

### الـWorkflow
+5 خطوات (`orders-api-tests`، `messaging-api-tests`، `notifications-api-tests`، وتكامل حي واحد مشترك للثلاثة).

## تحديث رابع عشر — إصلاح: وحدة Idempotency مكرَّرة ومتضاربة

### المشكلة المكتشفة أثناء المراجعة المحلية (قبل أي تشغيل CI)
وُجد نسختان منفصلتان من `idempotency_service.py`/`idempotency_repository.py`: واحدة في `services/inventory_item/src/` (التي وثَّقناها سابقًا في CR-013)، وأخرى في `services/shared/src/` (مُدرَجة أيضًا في `conftest.py`). بما أن `conftest.py` يُدرِج `shared` **آخر** القائمة، وكل إدراج يستخدم `sys.path.insert(0, ...)`، فإن **نسخة `shared` هي التي تُستدعى فعليًا دائمًا**، لا نسخة `inventory_item` الموثَّقة — الأخيرة كانت كودًا ميتًا (Dead Code) منذ إضافة `shared` دون أن يُلاحَظ.

### التحقق قبل الحسم
قارنت المنطق الفعلي للنسختين عند نقطة استهلاكهما الوحيدة (`inventory_item_api.py`): **متوافقتان وظيفيًا تمامًا** عند هذه النقطة تحديدًا (نفس ترتيب المعاملات، نفس شكل القيمة المُعادة `.response_body`)؛ لا فرق سلوكي فعلي ظهر في أي اختبار سابق لهذا السبب. **الفرق الوحيد الجوهري**: نسخة `shared` لا تحتوي حارس `is_cacheable_status` الصريح (تخزين 2xx فقط) — لكن هذا غير مؤثِّر عمليًا لأن `inventory_item_api.py` لا يستدعي التخزين إطلاقًا إلا بعد نجاح العملية (المسارات الفاشلة تُنهي الطلب عبر `raise error(...)` قبل الوصول لأي استدعاء تخزين).

### الإصلاح
حذف نسخة `services/inventory_item/src/` المكرِّرة (كود ميت)، والإبقاء على `services/shared/src/` كموقع وحيد ومعتمَد — وهو فعليًا **الاختيار المعماري الأصوب**: DD الحزمة 2 (القسم 2.2) تنص صراحة أن سياسة Idempotency تشمل عمليات مستقبلية أخرى (طلب شراء، عرض سعر، استيراد جماعي) لا Inventory وحدها، فوضعها في وحدة عابرة للخدمات (`shared`) بدل حصرها داخل خدمة واحدة كان الأصح من البداية.

**لا تعديل على أي منطق فعلي** — الإصلاح يقتصر على حذف الملفَين المكرَّرين غير المُستخدَمين فعليًا، وتنظيف كل مجلدات `__pycache__` من الحزمة بالكامل.

## تحديث خامس عشر — الدفعة الأخيرة (نهائية، بانتظار CI): Scheduler + TRM + Reference Data + Bulk Import

**الحالة: مُنفَّذة ومُتحقَّق منها محليًا بالكامل — بانتظار تشغيل GitHub Actions فعليًا. لا اعتماد أو إغلاق رسمي حتى تصل الأدلة الخام.**

### الخدمات المكتملة في هذه الدفعة
| الخدمة | العمليات | نقاط التحقق الجوهرية |
|---|---|---|
| **TRM** (تقييمات) | 4: إنشاء/تعديل/أرشفة تقييم، متوسط الدرجات | الأهلية تُتحقَّق فعليًا عبر `OrderRepository` — لا تقييم إلا لصفقة `fulfilled` حقيقية |
| **Scheduler** (إداري) | 3: إنشاء/عرض/إلغاء مهمة مجدوَلة | مدير النظام حصريًا؛ التنفيذ الدوري الفعلي يبقى خارج REST |
| **Reference Data** | 3: إضافة/عرض/أرشفة قيمة مرجعية | مدير النظام حصريًا للكتابة، REQ-REF-002: أرشفة لا حذف |
| **Bulk Import** | 1: معاينة استيراد جماعي `.xlsx` | **أول تنفيذ فعلي لتحليل ملفات xlsx حقيقية عبر `openpyxl`** — تحقَّق بتوليد ملف حقيقي وتحليله فعليًا قبل التسليم، لا محاكاة |

### اكتشاف وإصلاح إضافي
لا تكرار لأسماء وحدات عبر كل الخدمات (فحصتُ صراحة بعد درس وحدتَي Idempotency المتنافستين سابقًا) — لا خطر استيراد غامض مماثل.

### مؤجَّل عمدًا — ليس نقص تنفيذ بل غياب تصميم كامل
**SUB (الاشتراكات)، CNT (المحتوى)، SUP (الدعم الفني)**: لا يوجد أي كود Service أو Repository لها إطلاقًا (خلافًا لكل خدمة أخرى في هذا المشروع، حيث كان المنطق التجاري جاهزًا وينقصه REST فقط). هذه ثلاثة مجالات أعمال كاملة (REQ-SUB-001..008، REQ-CNT-001/002، REQ-SUP-001..006) تستوجب جلسة مراجعة SRS وتصميم قواعد عمل مستقلة قبل أي تنفيذ — لم تُجرَ بعد. موثَّقة صراحة داخل `openapi.yaml` نفسه كـBacklog، لا افتراضًا صامتًا لسلوكها.

### اختبارات محلية منفَّذة لهذه الدفعة
- `test_trm_api.py` (8)، `test_scheduler_api.py` (5)، `test_ref_api.py` (9، شاملة رفع ملف `.xlsx` حقيقي عبر multipart)، `test_postgres_final_batch_integration.py` (6، تكامل حي).
- منطق `ref_service.py` الخالص نُفِّذ يدويًا وتحقَّق منه فعليًا قبل الكتابة (تصنيف صفوف الاستيراد: جديد/محدَّث/مرفوض).
- تحليل `.xlsx` الفعلي (`_parse_xlsx_rows`) نُفِّذ يدويًا بملف حقيقي مُولَّد بـ`openpyxl` وتحقَّق من صحة النتيجة.

## بيان ختامي — كل الخدمات المكتملة عبر كل الدفعات (بانتظار CI نهائي واحد)
Auth (CR-011/012/013) · PCT · VCT · CMP · Search · Store · Inventory · Orders/Purchase Requests · Messaging · MessageExtended (مرفقات) · Notifications (مركز الإشعارات) · TRM · Scheduler (إداري) · Reference Data + Bulk Import.
**14 خدمة، 57 مسار REST، `openapi.yaml` v1.7.0، 25 Migration، 79 ملف Python.**
مؤجَّل بالكامل وموثَّق: إدارة حملات الإشعارات، SUB، CNT، SUP، وبعض تفاصيل MessageExtended الثانوية (الحضور/الكتم/تتبع القراءة).

## تحديث سادس عشر — حسم SUB/CNT/SUP (لم تعد Backlog، نُفِّذت كاملة)

**نتيجة الجرد المطلوب**: الثلاثة ضمن نطاق v1 فعليًا — أغلب متطلباتها الجوهرية بأولوية **Must** صراحة في SRS الحزمة E (REQ-SUB-001/002/004/004-A/004-B/004-C، REQ-CNT-001/002، REQ-SUP-001/002/003/005). غيابها كان فجوة تنفيذية، لا تأجيلًا مقصودًا. نُفِّذت الآن بالكامل: منطق أعمال + Repository + REST + اختبارات، بنفس المعيار المطبَّق على كل خدمة سابقة.

### SUB (الاشتراكات) — 6 عمليات
خطط (إنشاء بصلاحية مدير النظام REQ-SUB-001، عرض)، اشتراك بائع (REQ-SUB-002، يمنع اشتراكين نشطين معًا)، عرض اشتراكي، تغيير خطة يسري فورًا (REQ-SUB-005/005-A، صاحبه حصرًا). انتهاء الاشتراك (REQ-SUB-004) يُطبَّق بفحص كسول عند كل قراءة، لا مهمة مجدوَلة منفصلة — تحقَّق بتنفيذ حقيقي.

### CNT (إدارة المحتوى) — 5 عمليات
إنشاء/عرض/نشر/إلغاء نشر مقال — دور "محرر الأخبار" (news_editor) حصريًا للكتابة (REQ-CNT-001/002)، عرض عام للمنشور فقط.

### SUP (الدعم الفني) — 8 عمليات
دورة حياة كاملة (open→in_progress→resolved→closed، REQ-SUP-002)، إسناد لمشرف (REQ-SUP-003، دور support_moderator)، ردود متعددة ضمن الطلب (REQ-SUP-005)، إعادة فتح خلال مهلة قابلة للضبط (REQ-SUP-006، افتراضي 72 ساعة) — **نُفِّذ ونُفِّذ اختباره فعليًا يدويًا (9 فحوصات، شاملة انقضاء المهلة)** قبل الكتابة.

### النقطة الوحيدة المتبقية مؤجَّلة — قرار حوكمي معلَّق، لا نقص تنفيذ
**REQ-SUB-003 وREQ-SUB-004-B** (استهلاك حالة الاشتراك في أهلية استقبال الطلبات، ومنع إنشاء عناصر مخزون جديدة بعد انتهاء الاشتراك) يستوجبان تعديل `order_api.py` وَ`inventory_item_api.py` **المُختبَرَين والمُغلَقَين فعليًا عبر CI في دفعات سابقة**. لم يُحسَم: هل الاشتراك إلزامي لكل بائع أم طبقة أهلية اختيارية إضافية؟ الأثر على كود مُغلَق يستوجب قرارًا صريحًا من مالك المشروع قبل التنفيذ — لم يُفتَرض صامتًا. الكيانات والانتقالات الأساسية لـSUB منفَّذة ومُختبَرة بالكامل بمعزل عن هذه النقطة.
REQ-SUP-004 (إشعار NTF عند تغيّر حالة الطلب) — أولوية Should لا Must، مؤجَّل لتبسيط النطاق.

### اختبارات محلية جديدة
`test_sub_api.py` (9)، `test_cnt_api.py` (8)، `test_sup_api.py` (11)، `test_postgres_sub_cnt_sup_integration.py` (4 تكامل حي). منطق `sub_service.py` وَ`sup_service.py` الخالص نُفِّذ يدويًا وتحقَّق منه فعليًا قبل الكتابة (7 و9 فحوصات على التوالي، كلها PASSED).

## البيان الختامي المُحدَّث — Backend مكتمل بالكامل (بانتظار CI نهائي واحد)
**17 خدمة، 73 مسار REST، `openapi.yaml` v1.8.0، 25 Migration، 92 ملف Python.**
Auth · PCT · VCT · CMP · Search · Store · Inventory · Orders · Messaging · MessageExtended · Notifications · TRM · Scheduler · Reference Data + Bulk Import · **SUB · CNT · SUP**.
لا خدمة مؤجَّلة بالكامل بعد الآن. المتبقي محصور في نقاط تكامل جزئية موثَّقة صراحة داخل `openapi.yaml` نفسه (تفعيل قيد SUB-004-B، حملات NTF، بعض تفاصيل MessageExtended الثانوية).

## تحديث سابع عشر — CR-022: Purchase Request Condition & Buyer Notes

**Baseline قبل الدفعة:** CR-021 = CI VERIFIED / EXCEPTIONAL GATE PASSED / CLOSED (Run ID 31330871623)، `openapi.yaml v1.15.0`، 27 Migration، 692 اختبار (0 فشل/خطأ/تخطي)، Schema Drift 55/55/35/0/0.

**النطاق المعتمَد حرفيًا فقط** (بلا توسيع): `condition_ref_id` UUID اختياري (NULL = بلا تفضيل) + `notes` TEXT اختياري (حد 2000 حرف على مستوى API) على `pur.purchase_requests`. لا صور/Media، لا تعديل على `offers.notes`، لا VCT Migration، لا CR-020 Search.

### Migration جديدة
`028_pur_purchase_request_condition_notes.sql` — عمودان NULL على `pur.purchase_requests` (`condition_ref_id UUID`, `notes TEXT`)، بلا FK فعلي (بنفس نمط كل حقول `*_ref_id` الأخرى في المشروع — SSOT بلا قيد DB)، بلا Backfill. آخر ترقيم متاح 028 بعد التحقق المستقل من فجوة 024 (مقصودة وموثَّقة مسبقًا في CR-015، غير متعلقة بـCR-022).

### التحقق الحقيقي من condition_ref_id
دالة جديدة `is_value_of_type(value_id, ref_type)` في `RefRepository` (Abstract + Postgres + InMemory) — تتحقق من وجود القيمة، تطابق `ref_type='part_condition'`، **و`status='active'`** (بنفس دلالة "قابل للاستخدام" المعتمَدة أصلًا في `get_values_for_type(include_archived=False)` — REQ-REF-002: أرشفة لا حذف). محقونة إلى `order_service.py` بنفس نمط `is_part_approved_checker` القائم (SSOT). قيمة من `ref_type` آخر أو مؤرشَفة أو غير موجودة → `400 INVALID_CONDITION_REF`.

### الملفات المعدَّلة
- `services/ref/src/ref_repository.py`: + `is_value_of_type` (3 مواقع: abstract/Postgres/InMemory).
- `services/order/src/order_service.py`: + حقلا `condition_ref_id`/`notes` على `PurchaseRequest`، + `MAX_PURCHASE_REQUEST_NOTES_LENGTH=2000`، + `InvalidConditionRefError`/`InvalidPurchaseRequestNotesError`، تحقق في `create_purchase_request`/`create_purchase_request_via_repository`.
- `services/order/src/order_repository.py`: INSERT/SELECT للعمودين الجديدين في `PostgresOrderRepository` (`InMemoryOrderRepository` لا يحتاج تعديلًا — يخزّن كائن Dataclass كاملًا).
- `services/order/src/order_api.py`: `PurchaseRequestCreateRequest`/`PurchaseRequestResponse` + الحقلين (نفس النموذج يُستخدَم فعليًا لكل مسارات PR: إنشاء/عرض/قائمة — لا Read Model منفصل كحالة CR-021، لأن هذين عمودان حقيقيان لا حقول محلولة عبر JOIN)، Pydantic `max_length=2000` على notes (يرفض تلقائيًا بـ422)، حقن `ref_repo` + معالجة `INVALID_CONDITION_REF`/`NOTES_TOO_LONG`.
- `tools/schema_drift_check.py`: تحديث إدخال `('pur','purchase_requests')` بالعمودين الجديدين (بلا تغيير على عدد الجداول/PK/FK: 55/55/35 كما هي).
- `scripts/setup_test_database.sh`: REQUIRED_FILES + رسائل التحقق تعكس 028 (000-028).
- `api_spec/openapi.yaml`: **v1.15.0 → v1.16.0** — `PurchaseRequestCreateRequest`/`PurchaseRequestResponse` + الحقلين الجديدين.

### أثر توافقي على عقد قائم (تغيير ضروري لا توسيع نطاق)
`GET /purchase-requests/mine` (CR-015) يستخدم نفس `PurchaseRequestResponse`؛ اختبار CR-021 القائم `test_original_mine_endpoint_unchanged` كان يثبّت مجموعة مفاتيح صارمة — حُدِّث ليعكس إضافة حقلين Nullable فقط (توافق خلفي قياسي لإضافات JSON، لا كسر لأي عقد). لم تُمَس حقول `part_name`/`manufacturer_name` (Read Model CR-021 يبقى معزولًا كما هو).

### اختبارات جديدة
- `test_order_api.py`: **+9** ضمن `TestCR022PurchaseRequestConditionAndNotes` (NULL=لا تفضيل، UUID صحيح مقبول، UUID من نوع آخر مرفوض، UUID غير موجود مرفوض، UUID مؤرشَف مرفوض، notes غائبة، notes=2000 مقبولة، notes=2001 مرفوضة 422، ظهور الحقلين على `/mine`) + تحديث اختبار CR-021 القائم أعلاه. **19 → 28 اختبارًا** في هذا الملف.
- `test_postgres_orders_messaging_notifications_integration.py`: **+4** ضمن `TestCR022ConditionAndNotesOnLivePostgres` (round-trip حقيقي يقرأ من العمود مباشرة، رفض نوع مرجعي خاطئ، رفض قيمة مؤرشَفة، سجل بلا الحقلين الجديدين يبقى NULL/NULL بدون كسر أي مسار). **4 → 8 اختبارات** في هذا الملف.
- **إجمالي اختبارات جديدة: 13.**

### Workflow Coverage
لا تعديل مطلوب على `.github/workflows/postgresql-validation.yml` — الاختبارات الجديدة أُضيفت داخل نفس ملفَي `test_order_api.py`/`test_postgres_orders_messaging_notifications_integration.py` المُجمَّعَين فعليًا ضمن Jobs قائمة (`orders-api-tests.xml`/`orders-messaging-notifications-integration-tests.xml`). تحقَّقتُ سطرًا بسطر من ملف الـworkflow لتأكيد ذلك.

### Repository Hygiene Gaps مسجَّلة (خارج نطاق CR-022، بقرار صريح من المالك)
1. نسخة `openapi.yaml` الجذرية (v1.9.0، خارج `postgres_execution_package/`) — لا مرجع لها في أي CI أو Manifest، تبدو يتيمة. تُركت كما هي.
2. شجرة `full_regression/services/...` (361 اختبارًا، Job منفصل) لم تُزامَن يدويًا مع تغييرات CR-022 — بقرار صريح، إلا إذا ظهر Regression حقيقي بالدليل.

### الحالة
تنفيذ محلي كامل + تحقق تركيبي (`py_compile` لكل ملفات Python المعدَّلة، `yaml.safe_load` لـ`openapi.yaml`، `bash -n` لسكربت الإعداد، تنفيذ AST لـ`schema_drift_check.py` للتأكد من سلامة `EXPECTED_TABLES`). **لا تشغيل pytest فعلي محليًا** (لا اتصال شبكة لتثبيت `fastapi`/`psycopg2` في هذه الجلسة) — بانتظار GitHub Actions (نفس نهج Deferred CI المعتمَد سابقًا). **بانتظار Exceptional Gate — لم يُعتمَد ذاتيًا.**

## تحديث ثامن عشر — Batch 1: Core Marketplace (VCT + CMP + Search + PR/VCT + Offers) — Backend مكتمل

**Baseline قبل الدفعة:** CR-022 = CI VERIFIED / CLOSED (Run 31349992270) — `openapi.yaml v1.16.0`، 28 migration، 705 اختبار (0/0/0)، Schema Drift 55/55/35/0/0.

**المصدر التنفيذي المعتمَد:** Approved VCT Design Baseline (رسالة المالك التفصيلية، الأقسام 1-25) — يغطي VCT (Trim Model Years + Market Availability)، Compatibility (General/Year-specific)، Search Semantics §18-19، Advisory Locking §15-17، Purchase Request/VCT integration §23.

### Migrations جديدة (3)
- `029_vct_trim_model_years_and_market_availability.sql`: `vct.generations` (+start_year/end_year)، جدول `vct.trim_model_years` جديد، جدول `vct.trim_market_availability` جديد (Exactly-one-target + Partial Unique Indexes)، توسيع CHECK على `vct.localized_names.owner_type` ليشمل generation/trim.
- `030_cmp_year_specific_compatibility.sql`: إزالة `uq_compatibility_part_trim` القديم، `trim_ref_id` يصبح NULLable، عمود `trim_model_year_ref_id` جديد، CHECK Exactly-one-target، فهرسان جزئيان (`uq_compatibility_general`, `uq_compatibility_year_specific`) محلّين.
- `031_pur_purchase_request_trim_model_year.sql`: عمود `trim_model_year_ref_id` اختياري على `pur.purchase_requests`، بلا FK، بلا Backfill.
- **العدد الكلي: 31 migration**، مؤكَّد رقميًا مطابقًا لـ`REQUIRED_FILES` (31/31).

⚠️ **اكتُشف وصُحِّح ذاتيًا خلال هذه الدفعة:** ملف Migration يتيم مكرَّر (`031_pur_trim_model_year.sql`، غير مُدرَج في `REQUIRED_FILES`) كان سيرفع عدد ملفات `.sql` الفعلية على القرص إلى 32 ويُفشل فحص `migration_count` في CI. حُذِف قبل وصول أي Gate.

### الوحدات الجديدة/المُعاد تصميمها
- `services/shared/src/advisory_lock.py` **(جديد)**: `compute_advisory_lock_key(namespace, *parts)` — BLAKE2b digest_size=8، signed 64-bit، حتمية عبر أي عملية تشغيل (بديل صريح عن `hash()` المدمجة المحظورة صراحةً في التصميم المعتمَد §15).
- **VCT**: `vct_service.py`/`vct_repository.py`/`vct_api.py` — Trim Model Years (تحقق نطاق الجيل §4)، Market Availability (Whitelist Semantics §7، منع تعايش Trim-level/Year-specific عبر Advisory Lock §17).
- **CMP**: إعادة تصميم كامل — General/Year-specific Compatibility، Exactly-one-target (§10)، منع تعايش عبر Advisory Lock (§13، §16)، توافق خلفي كامل مع الطلبات القديمة (trim_ref_id فقط).
- **Search**: `q` مفعَّل (Exact+Prefix، 4 أنواع أسماء، تطبيع عربي آمن v1)، دمج فعلي مع VCT/CMP (`year` param، دلالة §18 الموحَّدة بدون/مع سنة) عبر SQL مباشر (لا N+1).
- **Purchase Requests**: `trim_ref_id` يُتحقَّق منه الآن فعليًا عبر VCT الحقيقي (لا نص حر)؛ `trim_model_year_ref_id` اختياري مع تحقق انتمائه لنفس الفئة. `condition_ref_id`/`notes` (CR-022) محفوظان حرفيًا بلا إعادة فتح.
- **Display Projection (CR-021) موسَّعة**: trim_name/generation_name/model_year/condition_code/notes — كانت مؤجَّلة صراحةً بانتظار اكتمال VCT، وهذه الدفعة هي ذلك الاكتمال.
- **Offers Integration**: مسار جديد `GET /purchase-requests/{prId}/offers/display` — سياق الطلب الكامل محلولًا لكل عرض، بلا Raw IDs حيث يوجد اسم حقيقي، بلا N+1 (استعلام سياق واحد لكل الطلب).

### OpenAPI
`v1.16.0 → v1.17.0` (رفعة واحدة في نهاية الدفعة، لا رفعات جزئية). Structural Verification آليًا (سكربت مقارنة مسارات الكود مقابل الـspec): **صفر فجوات** بعد إصلاح فجوة واحدة اكتُشِفت (`GET /vct/generations/{id}`). 88 مسارًا، 91 مخططًا، 96 operationId بلا تكرار.

### Schema Drift المتوقَّع
57 جدولًا / 57 PK / 38 FK (بلا تغيير عن آخر تحقق — الإضافات كلها أعمدة/فهارس جزئية على جداول قائمة أو جدولان جديدان (`trim_model_years`, `trim_market_availability`) محسوبان مسبقًا).

### الاختبارات
- إجمالي اختبارات `postgres_execution_package/tests/*.py` الحالي: **427** (مؤكَّد بطريقتين: `grep` مباشر وعدّ مُنمَّط، تطابق كامل).
- Baseline المقارِن (بمعزل عن `full_regression/` غير المُزامَنة): 705 - 361 (full_regression) = 344. **صافي الإضافة داخل هذه الدفعة: +83 اختبارًا.**
- تشمل: Unit (InMemory) وPostgreSQL Integration لكل من VCT، CMP، Search، PR/VCT، Display Projection الموسَّعة، Offer Display.
- **اختبارا Concurrency حقيقيان** (اتصالان منفصلان فعليًا + Threads متزامنة + Barrier): تعايش CMP General/Year-specific، وتعايش VCT Trim-level/Year-specific Market Availability — كلاهما يثبت أن بالضبط عملية واحدة تنجح والأخرى تُرفَض، أبدًا الاثنتان معًا.
- `full_regression/services/...` (361 اختبارًا، شجرة منفصلة): **لم تُزامَن** — Repository Hygiene Gap مسجَّلة مسبقًا، بقرار صريح سابق بعدم لمسها إلا بدليل Regression حقيقي.

### Workflow Coverage
كل ملفات الاختبار الاثني عشر المتأثرة/الجديدة مُجمَّعة فعليًا ضمن Jobs قائمة في `.github/workflows/postgresql-validation.yml` — تحقَّقت آليًا، لا فجوة.

### Blocker حقيقي — Frontend
**لا يوجد أي مستودع Frontend مرفوع في هذه الجلسة.** فُحص المستودع بالكامل (بما يتجاوز `postgres_execution_package`) وملفات `/mnt/user-data/uploads` — لا وجود لأي كود Next.js/React. بند "Frontend Journey" من نطاق Batch 1 **متوقف فعليًا بانتظار رفع تلك الحزمة**؛ لا يمكن تنفيذه بالتخمين دون كسر مخاطرة حقيقية على عمل Frontend قائم فعليًا (وفق الذاكرة: Login/Register/Home/Search/Part Detail/Store/Articles/My Requests مكتملة سلفًا في مستودع منفصل لم يُرفَع هنا).

### الحالة
**كل عمل Backend لـBatch 1 مكتمل ومُتحقَّق تركيبيًا بالكامل (py_compile، AST، yaml.safe_load، Structural Verification آلي، Migration/Schema Drift رقميًا).** لا تشغيل pytest فعلي (لا شبكة لتثبيت fastapi/psycopg2 في هذه الجلسة) — Deferred CI كالمعتاد. **Batch 1 غير مكتملة ككل** (Frontend محجوب بـBlocker حقيقي). **لا اعتماد ذاتي — بانتظار حزمة Frontend أو توجيه صريح بشأن التعامل مع هذا البند.**

## تحديث تاسع عشر — Batch 1: Frontend Enablement (VCT Public Browsing + Journey الكامل)

**Baseline Frontend:** `carsmaint-frontend-foundation(6).zip` — Next.js 16، next-intl v4، Tailwind v4، React Query. اعتُمِد ككود قائم فعليًا، **لم يُنشَأ Frontend موازٍ**. فُحصت البنية كاملةً قبل أي تعديل (API layer، i18n، Components، Routing) واتُّبِعت اصطلاحاته حرفيًا.

### فجوة Backend حقيقية إضافية اكتُشِفت وسُدَّت (Required for correctness)
لا مسارات "قائمة" (List) كانت موجودة في VCT — فقط Create/Get بمعرّف مباشر، ما يمنع بناء منتقي سيارة قابل للتصفح تمامًا. أُضيفت **4 مسارات عامة جديدة بلا جلسة** (نفس طبيعة `/search/parts` العامة، `security: []`):
- `GET /vct/manufacturers` — الشركات المصنِّعة المعتمَدة.
- `GET /vct/manufacturers/{id}/models` — الموديلات المعتمَدة.
- `GET /vct/models/{id}/generations` — الأجيال.
- `GET /vct/generations/{id}/trims` — الفئات.
- `GET /vct/trims/{id}/model-years` (كانت موجودة، **حُوِّلت من محمية بجلسة إلى عامة** لنفس السبب).

كلها تحل الأسماء الحقيقية عبر `vct.localized_names` (Postgres، بنفس نمط LATERAL JOIN المعتمَد في CR-021/Display Projection)، مع دعم اختباري مقابل في `InMemoryVctRepository` (`seed_localized_name_for_testing`).

### Frontend — الملفات الجديدة/المعدَّلة (16)
- `src/lib/api/vct.ts` **(جديد)**: طبقة Server-only لتصفح VCT (SSR الأولي).
- `src/lib/api/vct-client.ts` **(جديد)**: نسخة آمنة للمتصفح (نفس المسارات، عبر `apiFetch` الموحَّد) — تُستهلَك من `VehicleSelector` التفاعلي حصرًا.
- `src/components/vehicle/vehicle-selector.tsx` **(جديد)**: منتقي متسلسل Manufacturer→Model→Generation→Trim→Model Year، Client Component، يُعيد ضبط كل مستوى تابع تلقائيًا عند تغيير مستوى أعلى، حالات Loading/Empty/Error لكل مستوى، Hidden Inputs اختيارية لدعم نماذج GET التقليدية (بحث) أو `onChange` callback (نموذج إنشاء الطلب).
- `src/components/search/search-filter-form.tsx` **(أُعيد كتابته بالكامل)**: تحوَّل لـClient Component، استبدال حقل `trim_ref_id` النصي الخام بـ`VehicleSelector`، **إزالة كاملة لمربع `verified_sellers_only`** (Deferred وفق القرار المعتمَد — لم يُمَس Business Semantics في الـBackend).
- `src/app/[locale]/(public)/search/page.tsx`: إضافة معامل `year`، إزالة قراءة/تمرير `verified_sellers_only`.
- `src/lib/api/search.ts`: إضافة `year` لـ`SearchParams`، توثيق أن `verified_sellers_only` يبقى في العقد للاستهلاك المباشر فقط (بلا عنصر واجهة).
- `src/lib/api/purchase-requests.ts` **(أُعيد كتابته بالكامل)**: `PurchaseRequestDisplayItem`/`listMyPurchaseRequestsDisplay`، `createPurchaseRequest`، `OfferDisplayItem`/`getPurchaseRequestOffersDisplay` — تطابق العقود الجديدة حرفيًا.
- `src/components/purchase-requests/create-purchase-request-form.tsx` **(جديد)**: نموذج إنشاء تفاعلي (VehicleSelector + Condition + Notes حتى 2000 حرف)، بنفس نمط `login-form.tsx` القائم.
- `src/components/ui/form.tsx`: إضافة `Textarea` (نفس أنماط `Input` حرفيًا) — إعادة استخدام، لا مكوّن موازٍ.
- `src/app/[locale]/(account)/purchase-requests/new/page.tsx` **(جديد)**: صفحة الإنشاء، تتطلب `catalog_part_ref_id` حقيقيًا (catalog-only، مطابقة `order_service.py` حرفيًا)؛ توجيه واضح للعودة للبحث عند غيابه، بلا نموذج مكسور أو معرّف وهمي.
- `src/app/[locale]/(account)/purchase-requests/[id]/page.tsx` **(جديد)**: تفاصيل الطلب + العروض (Offers Display)، سياق محلول بالكامل، Seller Notes، Scoping مطابق للخادم حرفيًا (403/404 مُعالَجان).
- `src/app/[locale]/(account)/requests/page.tsx` **(أُعيد كتابته بالكامل)**: يستهلك `/purchase-requests/mine/display` بدل الخام — أسماء حقيقية، رابط لكل طلب نحو صفحة التفاصيل، CTA لإنشاء طلب جديد.
- `src/app/[locale]/(public)/parts/[id]/page.tsx`: زر "اطلب هذه القطعة" بـ`catalog_part_ref_id` حقيقي من استجابة CR-019 الفعلية.
- `src/lib/api-client.ts`: تحديث تعليق نسخة العقد (v1.9.0 → v1.17.0).
- `messages/ar.json` / `messages/en.json`: namespaces جديدة (`vehicle`, `purchaseRequestForm`, `offers`) + تحديث `search`/`requests`/`parts` — **تطابق مفاتيح تام بين اللغتين** (تحقَّقت آليًا).

### لا صور/Media (Batch 2 كما هو مقرَّر)، لا Mock IDs/trim-1، لا Query Params لتعويض Backend ناقص — كل بيانات هذه الدفعة حقيقية من العقود الفعلية.

### Final Sweep — Frontend
- **TypeScript**: فُحصت الملفات الجديدة/المعدَّلة عبر `tsc` مباشرة (لا `node_modules` في هذه الجلسة — Deferred CI، نفس نهج Backend). اكتُشِفت وصُحِّحت **2 خطأ حقيقيان**: (أ) عدم تصدير أنواع VCT من `vct-client.ts` (إعادة تصدير ناقصة)، (ب) `noUncheckedIndexedAccess` على `offersData.items[0]` في صفحة التفاصيل (أُصلِح بتفكيك مصفوفة آمن). باقي التحذيرات (`Cannot find module 'react'`, `JSX.IntrinsicElements`) مؤكَّدة أنها ضجيج بيئي بحت (تظهر أيضًا على ملفات قائمة لم تُمَس، مثل `button.tsx`، بسبب غياب `@types/react` فقط).
- **Lint/Build/Test**: لا تشغيل فعلي (لا اتصال شبكة لتثبيت الاعتماديات) — الـWorkflow القائم (`frontend-ci.yml`) يُشغِّل `lint`+`typecheck`+`build` تلقائيًا على أي تغيير تحت `carsmaint-frontend/**`، بلا حاجة لتوصيل يدوي لملفات جديدة (خلافًا للـBackend). لا ملفات اختبار Vitest/Playwright موجودة أصلًا في الحزمة (0 ملفات `.test.ts`) — لا فجوة CI جديدة، الوضع القائم لم يتغيَّر.
- **RTL/LTR/i18n**: تطابق مفاتيح ar/en تام، لا نصوص مُدرَجة مباشرة في الكود (Hardcoded)، استخدام Classes منطقية (`ms-*`) بنفس نمط الكود القائم.
- **لا Raw IDs**: تحقَّقت يدويًا أن `requests/page.tsx` و`purchase-requests/[id]/page.tsx` يعرضان الأسماء المحلولة (part_name/trim_name/manufacturer_name/...) لا المعرِّفات الخام، مع Fallback نصي صريح (`partFallback`/`vehicleFallback`) عند غياب التوطين — لا إخفاء صامت ولا بيانات مختلقة.

### الحالة
**Batch 1 مكتملة الآن Backend + Frontend معًا.** بانتظار Exceptional Gate. لا اعتماد ذاتي.
