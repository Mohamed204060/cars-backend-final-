"""
conftest.py — إعداد تلقائي لمسارات الاستيراد (sys.path) لكل الخدمات
=====================================================================
يُحمَّل هذا الملف تلقائيًا بواسطة pytest قبل أي اختبار؛ لا حاجة لأي إعداد
يدوي إضافي. يُضيف مجلد src/ لكل خدمة إلى sys.path، بحيث تعمل عبارات
الاستيراد المضمَّنة داخل دوال الاختبار (كـ"from ntf_repository import
PostgresNtfRepository") مباشرة دون أي تكوين خارجي.

Batch 1 — إصلاح جذري (Gate Infrastructure Fix): كان هذا الملف يعتمد على
قائمة أسماء خدمات ثابتة يدويًا (_SERVICE_NAMES)، فتظهر ModuleNotFoundError
لأي خدمة جديدة (مثل services/vct، services/cmp، services/shared) نُسيت من
القائمة أو أُضيفت بعد كتابتها، أو إن انحرفت نسخة هذا الملف على أي بيئة عن
آخر تحديث محلي — بالضبط ما حدث في CI (advisory_lock ضمن services/shared/src
غير قابل للاستيراد رغم أن الدليل موجود فعليًا). الحل الجذري: اكتشاف ديناميكي
لكل services/*/src فعليًا موجود على القرص عبر glob، لا قائمة يدوية يمكن أن
تتقادم أبدًا — يعمل تلقائيًا لأي خدمة حالية أو مستقبلية دون أي تعديل لاحق
على هذا الملف.
"""

import glob
import os
import sys

_PACKAGE_ROOT = os.path.dirname(os.path.abspath(__file__))
_SERVICES_DIR = os.path.abspath(os.path.join(_PACKAGE_ROOT, "..", "services"))

# اكتشاف ديناميكي: كل services/*/src فعليًا موجود، بأي ترتيب أبجدي ثابت
# (sorted) لضمان تكرارية النتيجة عبر عمليات تشغيل مختلفة.
for _src_path in sorted(glob.glob(os.path.join(_SERVICES_DIR, "*", "src"))):
    if os.path.isdir(_src_path) and _src_path not in sys.path:
        sys.path.insert(0, _src_path)
