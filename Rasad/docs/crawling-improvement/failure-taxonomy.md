# طبقه‌بندی شکست خزش

| کد | معنا | Retry | اقدام پیشنهادی |
|---|---|---|---|
| dns_sinkhole | DNS دامنه عمومی را به IP غیرعمومی نگاشت کرده است | بله | خروجی اختصاصی با Remote DNS و حفظ SSRF |
| ssrf_rejected | URL طبق سیاست امنیتی ناامن است | خیر | بررسی URL؛ محافظ حذف نشود |
| network_unreachable | مسیر شبکه یا همه اتصال‌ها ناموفق‌اند | بله | بررسی egress، proxy و timeout |
| connection_failed | probe عمومی منبع شکست خورده است | بله | بررسی DNS، TLS و endpoint جایگزین |
| robots_blocked | robots.txt خزش را منع کرده است | خیر | RSS/API رسمی یا منبع دارای مجوز |
| http_restricted | پاسخ‌هایی مانند 403/406 دریافت شده است | محدود | پروفایل منبع، API رسمی یا browser مجاز |
| anti_bot | سامانه ضدربات پاسخ قابل پردازش نمی‌دهد | محدود | browser/proxy کنترل‌شده و رعایت شرایط استفاده |
| no_urls_discovered | discovery هیچ URL خبری تولید نکرده است | بله | RSS، sitemap و template اختصاصی |
| no_topic_match | صفحات دریافت شدند ولی موضوع را پوشش ندادند | بله | query چندزبانه و رتبه‌بندی هیبریدی |
| date_out_of_range | تاریخ انتشار خارج از بازه است | خیر | اصلاح ranking و discovery تاریخی |
| missing_publication_date | تاریخ ناشر قابل اثبات نیست | محدود | extractor اختصاصی منبع |
| extraction_failed | متن مفید استخراج نشده است | بله | extractor یا browser fallback |
| redirected_outside_allowed_host | redirect خارج از دامنه مجاز است | محدود | allowlist دقیق دامنه canonical |
| source_configuration_error | پروفایل یا URL منبع اشتباه است | پس از اصلاح | تصحیح کاتالوگ منابع |
| test_or_irrelevant_source | منبع آزمایشی وارد رصد واقعی شده است | خیر | حذف از انتخاب عملیاتی |
| unknown | داده تشخیصی کافی ثبت نشده است | بله | افزایش observability |
| duplicate_only | تمام اسناد پذیرفته‌شده تکراری بودند | خیر | منبع سالم است؛ هم‌پوشانی و dedupe بررسی شود |
| partial_content_success | بخشی از صفحات به سند نهایی تبدیل شد | محدود | علت‌های رد پرتکرار بررسی شوند |
| no_documents_prepared | خزش پایان یافت اما سندی آماده نشد | بله | trace مرحله‌ای و علل رد بررسی شوند |

هر منبع بدون سند در artifact فاز صفر دقیقاً یک `failure_category` دارد. این
طبقه‌بندی نتیجه عملیاتی را تغییر نمی‌دهد و فقط برای اندازه‌گیری فازهای بعد است.
