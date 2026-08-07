# PostgreSQL Execution Package — دليل التشغيل من البداية للنهاية

هذه الحزمة **مستقلة تمامًا وقابلة للتنفيذ فعليًا فور فك الضغط**، دون أي
اعتماد على ملفات خارجها. لم يُشغَّل أي جزء منها فعليًا بعد — كل ما فيها
**Ready for PostgreSQL Execution**، لا **Passed**.

تحقُّق ذاتي مُنجَز فعليًا قبل التسليم: تحميل conftest.py (أدناه) ثم استيراد
كل وحدة Repository التي تستخدمها الاختبارات نجح بالكامل في بيئة الإعداد
(بلا حتى تثبيت psycopg2)، مما يثبت أن مسارات الاستيراد وبنية المجلدات
صحيحة فعليًا، لا افتراضًا نظريًا.

## 0) المتطلبات المسبَقة

```bash
# تثبيت PostgreSQL (إن لم يكن مثبَّتًا) — مثال Ubuntu/Debian
sudo apt-get install -y postgresql postgresql-contrib

# تثبيت اعتماديات بايثون (موثَّقة في requirements.txt المرفَق)
pip install -r requirements.txt --break-system-packages
```

## 1) تعيين متغيرات الاتصال

```bash
export PGHOST=localhost
export PGPORT=5432
export PGUSER=postgres
export PGPASSWORD=postgres
export DB_NAME=carparts_test
export TEST_DATABASE_URL="postgresql://$PGUSER:$PGPASSWORD@$PGHOST:$PGPORT/$DB_NAME"
```

## 2) إنشاء قاعدة اختبار فارغة وتطبيق كل الترحيلات من الصفر

```bash
chmod +x scripts/*.sh
./scripts/setup_test_database.sh
```

يتحقق هذا السكربت أولاً من وجود كل ملفات الترحيل الـ22 (000 حتى 021) قبل
البدء، ثم يُطبِّقها بالترتيب الرقمي على قاعدة فارغة تمامًا.

## 3) تشغيل أداة اكتشاف انحراف المخطط

```bash
python3 tools/schema_drift_check.py
```

يجب أن تُظهر النتيجة `"diffs_found": 0` قبل المتابعة لأي اختبار آخر.

## 4) تشغيل اختبارات Repository (لكل الـ13 Repository) — من داخل مجلد tests/

```bash
cd tests
pytest test_postgres_repositories.py -v
```

**لا حاجة لأي إعداد يدوي لمسارات الاستيراد**: ملف `tests/conftest.py`
المرفَق يُضيف تلقائيًا مجلد `src/` لكل خدمة (من `services/<name>/src/`) إلى
`sys.path` بمجرَّد تشغيل pytest داخل هذا المجلد، لأن pytest يُحمِّل
`conftest.py` تلقائيًا من نفس المجلد أو أي مجلد أب.

## 5) تشغيل اختبارات التكامل (قيود، تزامن حقيقي، معاملات، أرشفة، بحث)

```bash
pytest test_postgres_integration.py -v
```

## 6) توثيق النتائج

انسخ الناتج الحرفي الكامل لكل أمر أعلاه (لا تلخيصًا) — هذا هو المدخل
الوحيد المقبول لكتابة PostgreSQL Validation Report لاحقًا.

## 7) إسقاط قاعدة الاختبار

```bash
cd ..
./scripts/teardown_test_database.sh
```

## بنية الحزمة (مستقلة تمامًا، كل ما يلزم موجود هنا)

```
postgres_execution_package/
├── requirements.txt              # psycopg2-binary, pytest
├── migrations/                   # 000-021: كل ملفات الترحيل كاملة
├── scripts/
│   ├── setup_test_database.sh
│   └── teardown_test_database.sh
├── services/                     # كود الخدمات الفعلي الذي تستورده الاختبارات
│   ├── search/src/
│   ├── auth/src/
│   ├── store/src/
│   ├── inventory_item/src/
│   ├── pct/src/
│   ├── vct/src/
│   ├── cmp/src/
│   ├── order/src/
│   ├── message/src/
│   ├── ntf/src/
│   ├── scheduler/src/
│   ├── trm/src/
│   └── message_extended/src/
├── tests/
│   ├── conftest.py                    # يُهيِّئ sys.path تلقائيًا لكل مجلدات services/*/src
│   ├── test_postgres_repositories.py  # اختبارات الـ13 Repository الفعلية (لا SQL خام)
│   └── test_postgres_integration.py   # قيود/تزامن حقيقي/معاملات/أرشفة/بحث
└── tools/
    └── schema_drift_check.py      # مقارنة المخطط الحي بالتصميم المعتمَد
```

## ملاحظة حاسمة

لا تُعتبَر مرحلة PostgreSQL Integration and Migration Validation مغلَقة
حتى تُنفَّذ كل الخطوات أعلاه فعليًا، وتُقدَّم نتائجها الحرفية لمراجعتها.
