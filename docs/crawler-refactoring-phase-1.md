# فاز یک بازطراحی خزش: تفکیک مسئولیت‌ها

## هدف

این فاز ساختار داخلی موتور خزش را بدون تغییر قرارداد عمومی، دیتابیس، Docker یا رفتار قابل مشاهده
تفکیک می‌کند. `CrawlEngine` همچنان برای callerهای قدیمی قابل استفاده است، اما فقط یک facade سازگاری
است و اجرای واقعی را به اجزای متمرکز واگذار می‌کند.

## معماری اجراشده

```text
CrawlEngine (compatibility facade)
    |
    +-- SafeHttpTransport       اعتبارسنجی و دریافت HTTP/redirect
    +-- RobotsAccessPolicy      تصمیم و cache مربوط به robots.txt
    +-- SourceProbeService      سنجش دسترسی‌پذیری منبع
    +-- LinkDiscoveryService    تولید endpoint و استخراج لینک
    +-- Crawl4AiPageFetcher     دریافت صفحه و dedupe محتوای batch
    +-- CrawlOrchestrator       ترتیب probe -> discovery -> selection -> fetch
```

مرز application موجود (`CrawlSource` و `ProbeSource`) و قراردادهای domain شامل
`CrawlRequest` و `CrawlOutcome` تغییر نکرده‌اند. آداپتور `Crawl4AiSourceCrawler` نیز همان خروجی
قبلی را ارائه می‌دهد.

## مسئولیت اجزا

| جزء | مسئولیت |
| --- | --- |
| `SafeHttpTransport` | GET امن و اعتبارسنجی تمام redirectها |
| `RobotsAccessPolicy` | دریافت، تفسیر و cache تصمیم‌های robots.txt |
| `SourceProbeService` | تولید نتیجه typed برای سلامت منبع |
| `LinkDiscoveryService` | تولید candidateها و استخراج لینک از HTML/RSS/sitemap |
| `Crawl4AiPageFetcher` | دریافت صفحات و حذف محتوای تکراری در همان batch |
| `CrawlOrchestrator` | هماهنگی مراحل و انتخاب لینک بر اساس تاریخ/موضوع/سقف صفحات |
| `CrawlEngine` | حفظ سازگاری callerهای legacy |

## موارد عمداً خارج از این فاز

- توسعه discovery تاریخی و پیمایش کامل sitemapها؛
- telemetry و ثبت دلیل رد شدن هر URL؛
- بهبود استخراج تاریخ انتشار؛
- GDELT، Common Crawl و آداپتور اختصاصی رسانه‌ها؛
- تغییر schema یا migration دیتابیس؛
- تغییر پورت‌ها، Docker یا legacy workflow engine.

این موارد در فازهای بعدی و پس از تأیید مستقل انجام می‌شوند.

## معیار پذیرش

- قرارداد عمومی crawling بدون تغییر باقی بماند.
- worker و monitoring همچنان از همان application boundary استفاده کنند.
- orchestration مستقل از Crawl4AI تست‌پذیر باشد.
- facade شامل منطق discovery، HTTP، robots یا fetch نباشد.
- تست‌های application، adapter، architecture و component boundary عبور کنند.

## روش بررسی

```powershell
docker compose --env-file .env -f docker/compose.yml exec -T api `
  python -m pytest `
  tests/test_crawler_orchestrator.py `
  tests/test_crawler_component_boundaries.py `
  tests/test_crawling_application.py `
  tests/test_crawling_adapter.py `
  tests/test_architecture_boundaries.py -q
```

برای smoke test واقعی دریافت خبر:

```powershell
docker compose --env-file .env -f docker/compose.yml exec -T worker `
  python -m scripts.verify_crawler
```
