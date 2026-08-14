"""
audit_service_imports.py — تحقق شامل بلا Fail-Fast لاكتمال services/*/src
=============================================================================
يُشغَّل يدويًا (أو يُدمَج كخطوة Debug إضافية) — لا يتوقف عند أول
ModuleNotFoundError؛ يجمع كل النقص دفعة واحدة ويطبعه في تقرير واحد، لتفادي
دورة "أصلح ملفًا واحدًا ثم أعد التشغيل لاكتشاف ملف آخر ناقص".

الاستخدام:
    cd postgres_execution_package/tests && python3 audit_service_imports.py

لا يعتمد على pytest؛ يستورد conftest.py يدويًا (نفس آلية pytest التلقائية)
ثم يحاول استيراد كل module متوقَّع اسمه صراحةً (لا اكتشاف تلقائي للأسماء —
القائمة هنا مبنية على ما هو موجود فعليًا في المرجع المحلي الكامل الذي بُني
عليه Batch 1، لتكون مرجعًا مستقلًا للمقارنة، لا نسخة طبق الأصل مما قد يكون
ناقصًا على GitHub).
"""

import glob
import importlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import conftest  # noqa: E402 — يُشغِّل اكتشاف sys.path الديناميكي (نفس ما يفعله pytest تلقائيًا)

# قائمة كل module متوقَّع لكل خدمة (repository + service + api)، من المرجع الكامل.
EXPECTED_MODULES = {
    "auth": ["auth_repository", "auth_service", "auth_api"],
    "cmp": ["cmp_repository", "cmp_service", "cmp_api"],
    "cnt": ["cnt_repository", "cnt_service", "cnt_api"],
    "inventory_item": ["inventory_item_repository", "inventory_item_service", "inventory_item_api"],
    "message": ["message_repository", "message_service", "message_api"],
    "message_extended": ["message_extended_repository", "message_extended_service", "message_extended_api"],
    "ntf": ["ntf_repository", "ntf_service", "ntf_api"],
    "order": ["order_repository", "order_service", "order_api"],
    "pct": ["pct_repository", "pct_service", "pct_api"],
    "ref": ["ref_repository", "ref_service", "ref_api"],
    "scheduler": ["scheduler_repository", "scheduler_service", "scheduler_api"],
    "search": ["search_repository", "search_service", "search_api"],
    "shared": ["advisory_lock", "idempotency_repository", "idempotency_service"],
    "store": ["store_repository", "store_service", "store_api"],
    "sub": ["sub_repository", "sub_service", "sub_api"],
    "sup": ["sup_repository", "sup_service", "sup_api"],
    "trm": ["trm_repository", "trm_service", "trm_api"],
    "vct": ["vct_repository", "vct_service", "vct_api"],
}


def main() -> int:
    services_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "services"))

    print("=== 1. اكتشاف مجلدات الخدمات الفعلية (services/*/src) ===")
    discovered_dirs = sorted(glob.glob(os.path.join(services_dir, "*", "src")))
    discovered_names = sorted({os.path.basename(os.path.dirname(p)) for p in discovered_dirs})
    expected_names = sorted(EXPECTED_MODULES.keys())
    missing_dirs = sorted(set(expected_names) - set(discovered_names))
    print(f"متوقَّع: {len(expected_names)} | موجود فعليًا: {len(discovered_names)}")
    if missing_dirs:
        print("مجلدات خدمات مفقودة بالكامل:", missing_dirs)
    else:
        print("لا مجلدات خدمات مفقودة.")

    print()
    print("=== 2. تحقق استيراد كل module متوقَّع، بلا توقف عند أول فشل ===")
    missing_modules = []       # (service, module) — الملف غير موجود إطلاقًا
    other_errors = []          # (service, module, error) — الملف موجود لكن فشل الاستيراد لسبب آخر (مثل fastapi غير مثبَّتة)
    ok_count = 0

    for service in expected_names:
        for module_name in EXPECTED_MODULES[service]:
            # فحص وجود الملف مباشرة على القرص أولًا (أدق من محاولة import فقط،
            # يميّز "غير موجود" عن "موجود لكن فشل الاستيراد لسبب آخر مثل fastapi")
            expected_path = os.path.join(services_dir, service, "src", module_name + ".py")
            file_exists = os.path.isfile(expected_path)
            try:
                importlib.import_module(module_name)
                ok_count += 1
            except ModuleNotFoundError as e:
                if not file_exists:
                    missing_modules.append((service, module_name))
                else:
                    other_errors.append((service, module_name, repr(e)))
            except Exception as e:  # noqa: BLE001 — نريد تجميع كل شيء دون توقف، حتى الأخطاء غير المتوقَّعة
                other_errors.append((service, module_name, repr(e)))

    total = sum(len(v) for v in EXPECTED_MODULES.values())
    print(f"إجمالي المحاولات: {total} | نجح: {ok_count} | ملفات مفقودة فعليًا: {len(missing_modules)} | أخطاء أخرى: {len(other_errors)}")

    if missing_modules:
        print()
        print("--- الملفات المفقودة فعليًا من القرص (Missing Modules) ---")
        for service, module_name in missing_modules:
            print(f"  services/{service}/src/{module_name}.py  ->  MISSING")

    if other_errors:
        print()
        print("--- أخطاء أخرى (الملف موجود، الفشل لسبب مختلف — عادةً حزمة خارجية مثل fastapi غير مثبَّتة) ---")
        for service, module_name, err in other_errors:
            print(f"  {service}/{module_name}: {err}")

    print()
    if not missing_dirs and not missing_modules:
        print("=== النتيجة: لا مجلدات ولا ملفات مفقودة. أي فشل متبقٍ هو اعتمادية خارجية (fastapi) لا مشكلة اكتمال. ===")
        return 0
    else:
        print("=== النتيجة: توجد فجوة اكتمال حقيقية (انظر القوائم أعلاه). ===")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
