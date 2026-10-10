# راهنمای عملیاتی راهبرد منابع

## تشخیص مسیر انتخاب‌شده

برای هر اجرای discovery، structured log با رویداد `crawl_discovery_strategy` ثبت می‌شود. فیلد `strategy_trace` شامل موارد زیر است:

- `profile_key`: پروفایل resolveشده؛
- `attempted`: providerهای واقعاً اجراشده؛
- `skipped`: قابلیت‌های اعلام‌نشده و علت عدم اجرا؛
- `provider_counts`: تعداد URL هر provider؛
- `fallbacks_used`: از جمله historical discovery؛
- `recommended_action`: پیشنهاد اقدام در نتیجه صفر.

برای بررسی مستقل، `python scripts/verify_phase2_source_profiles.py` در کانتینر API اجرا شود. این فرمان کاتالوگ را تغییر نمی‌دهد.

## افزودن یا اصلاح منبع

1. canonical hostname و aliasهای واقعی را مشخص کنید.
2. فقط قابلیت‌های مستند یا مشاهده‌شده را در `SOURCE_PROFILES` فعال کنید.
3. endpointهای هم‌میزبان RSS/sitemap/search/archive را ثبت کنید.
4. اگر HTML به JavaScript وابسته است، `requires_browser` را فعال کنید.
5. paywall/403 رایج را با `content_restricted` و نیاز شبکه سرور را با `remote_egress_required` علامت بزنید.
6. تست registry و capability detector را اجرا کنید.

## تفسیر نتیجه صفر

| وضعیت | اقدام |
|---|---|
| profile=`generic` | hostname/alias را به رجیستری اضافه کنید |
| base ناموفق و `remote_egress_required=true` | پس از انتقال، egress سرور فاز اول را اعتبارسنجی کنید |
| HTML=403 اما RSS/Sitemap موفق | منبع فعال بماند؛ RSS/Sitemap مسیر اصلی است |
| همه endpointها 404 | endpointهای profile را با robots و مستندات منبع بازبینی کنید |
| نتیجه محلی کمتر از آستانه | trace باید `historical_discovery` را نشان دهد |
| provider URL دارد ولی انتخاب نهایی صفر است | مشکل در ranking/date/topic admission است، نه discovery |

## ملاحظات امنیتی

- endpointها باید HTTP/HTTPS عمومی و روی host مجاز باشند.
- redirectها دوباره توسط egress policy اعتبارسنجی می‌شوند.
- پروفایل اجازه دسترسی به IP خصوصی، localhost یا port غیر 80/443 ایجاد نمی‌کند.
- خاموش‌کردن robots یا SSRF guard بخشی از این runbook نیست.

## rollback کاتالوگ

نسخه پشتیبان فاز دوم در `backups/phase-two/20260727-140654/` قرار دارد. restore عملیاتی مخرب است و تنها پس از گرفتن snapshot جدید و تأیید صریح انجام می‌شود.
