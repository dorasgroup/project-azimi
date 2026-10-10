# راهنمای عملیاتی بازیابی تاریخی

## آزمون قطعی بدون شبکه

```powershell
docker compose -f docker/compose.yml exec -T api pytest -q tests/test_historical_url_discovery.py tests/test_source_strategy_routing.py tests/test_source_profile_registry.py tests/test_crawler_component_boundaries.py
docker compose -f docker/compose.yml exec -T api python scripts/verify_phase5_historical_recall.py
```

این اجرا هیچ رصدی ایجاد نمی‌کند، دیتابیس را تغییر نمی‌دهد و legacy workflow engine را فراخوانی نمی‌کند.

## smoke روی سرور

پس از استقرار و تأیید egress:

```powershell
docker compose -f docker/compose.yml exec -T api python scripts/verify_historical_discovery.py
```

سپس logهای worker را بررسی کنید:

```powershell
docker compose -f docker/compose.yml logs --since 10m worker | Select-String "crawl_gdelt_historical_discovery|crawl_common_crawl_historical_discovery|crawl_discovery_strategy"
```

## تشخیص خطا

| نشانه | تفسیر و اقدام |
|---|---|
| `windows_failed` برابر کل پنجره‌هاست | egress، DNS، TLS یا محدودیت GDELT بررسی شود |
| پنجره‌ها موفق‌اند ولی `result_count=0` | query انگلیسی، domain منبع و پوشش واقعی GDELT بررسی شود |
| `collections=0` | بازه با collection موجود هم‌پوشانی ندارد یا `collinfo.json` قابل دسترسی نیست |
| `page_count_failures>0` | CDX page-count در دسترس نیست؛ سیستم به page صفر fallback می‌کند |
| pageها موفق‌اند ولی نتیجه موضوعی نیست | URLهای source در Common Crawl فاقد مفهوم موضوعی در path هستند؛ fetch و رتبه‌بندی نهایی تعیین‌کننده است |
| fallback اصلاً اجرا نشده | بازه ابتدا/انتها، `historical_discovery_enabled` و `historical_fallback_min_results` بررسی شوند |

## محدودیت‌های ایمنی

- سقف نهایی `max_external_results` تغییر نکرده است.
- host منبع پس از دریافت نتیجه دوباره اعتبارسنجی می‌شود.
- تاریخ مشاهده/آرشیو جای تاریخ انتشار ذخیره نمی‌شود.
- تغییر بودجه‌های داخلی فقط پس از بنچمارک recall و latency مجاز است.

