"""
client_ip.py — استخراج IP العميل الموثوق (Authoritative Client IP)
المرجع: Admin Operational Completion — Login/Security History
(تصحيح أمني نهائي: بيانات حدود الثقة الفاسدة يجب أن تفشل بوضوح وقابلية
للملاحظة، لا أن تُتجاهَل بصمت — القاعدة الحاكمة الملزمة لهذا الملف بأكمله).

المبدأ الأمني الحاكم (غير قابل للتفاوض): X-Forwarded-For قيمة يتحكم بها
العميل بالكامل ويمكن تزويرها بسهولة تامة. لا تُستخدَم كمصدر موثوق أبدًا إلا
عندما يكون الطرف المتصل مباشرة (TCP Peer الفعلي، غير قابل للتزوير) وسيطًا
(Proxy) مُهيَّأ صراحةً كموثوق.

الإعداد: TRUSTED_PROXY_CIDRS (متغيّر بيئة، قائمة CIDR مفصولة بفواصل،
مثل "10.0.0.0/8,172.16.0.0/12"). الافتراضي: قائمة فارغة — الأمان الافتراضي
الوحيد المقبول (Secure by Default). لا يجوز تخمين نطاقات Proxy في الإنتاج؛
يجب ضبط هذا المتغيّر صراحةً لكل بيئة نشر — هذا قرار تشغيلي/بنية تحتية خارج
نطاق هذا المستودع، لا نخترعه هنا.

قاعدة الفشل الآمن الصريحة (Explicit Fail-Safe Rule — بديل التصحيح السابق
الذي كان يتجاهل الإدخال الفاسد بصمت):
- TRUSTED_PROXY_CIDRS يحتوي أي إدخال غير صالح (CIDR لا يُحلَّل) → كامل
  إعداد الوسطاء الموثوقين يُعتبَر فاسدًا وغير صالح ككل، لا "نتجاهل هذا
  الإدخال ونستخدم الباقي" — يُصدَر تحذير صريح (warnings.warn، قابل
  للرصد في أي سجل تشغيل حقيقي)، وتُعامَل القائمة كأنها فارغة تمامًا
  (الافتراض الآمن: لا وسطاء موثوقون إطلاقًا). سبب الاختيار: قائمة موثوقين
  "جزئية" فاسدة قد تكون إما أضيق أو أوسع من النية الفعلية للمُشغِّل — لا
  طريقة آمنة للتخمين أيهما، فالفشل الكامل والملاحَظ أفضل من التخمين الصامت.
- سلسلة X-Forwarded-For (عندما يكون الـPeer موثوقًا فعلًا) تحتوي أي عنصر
  غير قابل للتحليل كعنوان IP صالح → التحليل بأكمله يفشل صراحة (يُعاد None)،
  لا "نتخطى هذا العنصر ونجرّب التالي" — تخطّي عنصر فاسد يعني إعادة تفسير
  بيانات حدود ثقة مشبوهة بدل رفضها، وهو بالضبط ما مُنِع صراحة.
"""

import ipaddress
import os
import warnings
from typing import Optional


class _TrustedProxyConfigInvalid(Exception):
    """داخلي فقط — يُستخدَم لإيقاف التحليل فورًا عند أول CIDR فاسد."""


def _load_trusted_proxy_networks(raw_config: str) -> "list[ipaddress.IPv4Network | ipaddress.IPv6Network]":
    """يُعيد قائمة الشبكات الموثوقة، أو قائمة فارغة (فشل آمن صريح + تحذير)
    إن احتوى الإعداد أي إدخال فاسد — لا تجاهل جزئي صامت للإدخال الفاسد."""
    raw = raw_config.strip()
    if not raw:
        return []
    networks = []
    try:
        for entry in raw.split(","):
            entry = entry.strip()
            if not entry:
                continue
            try:
                networks.append(ipaddress.ip_network(entry, strict=False))
            except ValueError:
                raise _TrustedProxyConfigInvalid(entry) from None
    except _TrustedProxyConfigInvalid as exc:
        warnings.warn(
            f"TRUSTED_PROXY_CIDRS يحتوي إدخالًا غير صالح ({exc}) — يُعامَل "
            "إعداد الوسطاء الموثوقين بأكمله كفاسد (فشل آمن صريح، لا تجاهل "
            "جزئي): لا وسطاء موثوقون على الإطلاق حتى يُصحَّح الإعداد.",
            RuntimeWarning,
            stacklevel=2,
        )
        return []
    return networks


def _parse_ip(value: str) -> Optional[str]:
    value = value.strip()
    if not value:
        return None
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        return None


def _is_trusted(ip_str: Optional[str], trusted_networks: list) -> bool:
    if ip_str is None or not trusted_networks:
        return False
    try:
        addr = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    return any(addr in net for net in trusted_networks)


def resolve_authoritative_client_ip(
    peer_address: Optional[str],
    forwarded_for_header: Optional[str],
    trusted_proxy_cidrs_env: Optional[str] = None,
) -> Optional[str]:
    """
    peer_address: request.client.host الفعلي (من FastAPI/Starlette) — TCP
    Peer الحقيقي، غير قابل للتزوير من طرف العميل.
    forwarded_for_header: قيمة X-Forwarded-For الخام كما وصلت (قد تكون None).
    trusted_proxy_cidrs_env: للاختبار فقط — يسمح بحقن قيمة TRUSTED_PROXY_CIDRS
    مباشرة بدل قراءتها من os.environ. في الإنتاج تُقرأ من os.environ دائمًا
    (القيمة الافتراضية None هنا تعني "اقرأ من os.environ").

    يُعيد سلسلة IP مُطبَّعة وصالحة، أو None إن تعذّر تحديد عنوان موثوق وصالح
    — بما في ذلك حالة سلسلة X-Forwarded-For الفاسدة خلف وسيط موثوق (فشل
    آمن صريح، لا تخمين).
    """
    raw_config = trusted_proxy_cidrs_env if trusted_proxy_cidrs_env is not None else os.environ.get("TRUSTED_PROXY_CIDRS", "")
    trusted_networks = _load_trusted_proxy_networks(raw_config)

    normalized_peer = _parse_ip(peer_address) if peer_address else None

    if not _is_trusted(normalized_peer, trusted_networks):
        # الافتراض الآمن: العميل غير موثوق كوسيط → لا نثق بأي شيء غير الـPeer
        # الفعلي، ونتجاهل X-Forwarded-For تمامًا مهما كانت قيمته (حتى لو
        # ادّعى IP مختلفًا تمامًا — هذا بالضبط ما يمنع التزوير).
        return normalized_peer

    if not forwarded_for_header:
        return normalized_peer

    chain = [entry.strip() for entry in forwarded_for_header.split(",") if entry.strip()]
    if not chain:
        return normalized_peer

    parsed_chain = [_parse_ip(entry) for entry in chain]
    if any(candidate is None for candidate in parsed_chain):
        # قاعدة الفشل الآمن الصريحة: عنصر واحد فاسد يُسقِط تحليل السلسلة
        # بأكملها — لا نتخطاه أملًا في عنصر صالح آخر (ذلك "إعادة تفسير"
        # بيانات حدود ثقة فاسدة، وهو ممنوع صراحة). None يعني: تعذّر تحديد
        # عنوان موثوق، لا قيمة أخرى (لا Peer، لا تخمين).
        warnings.warn(
            "X-Forwarded-For من وسيط موثوق يحتوي عنصرًا غير قابل للتحليل "
            "كعنوان IP صالح — رُفِض التحليل بالكامل (فشل آمن صريح).",
            RuntimeWarning,
            stacklevel=2,
        )
        return None

    for candidate in reversed(parsed_chain):
        if not _is_trusted(candidate, trusted_networks):
            return candidate
        # موثوق (وسيط آخر ضمن السلسلة) → نتخطاه ونتابع لليسار

    # كل عناصر السلسلة موثوقة — لا عميل غير موثوق ظاهر في السلسلة، نرجع
    # لعنوان الـPeer نفسه كأفضل قيمة متاحة وموثوقة.
    return normalized_peer
