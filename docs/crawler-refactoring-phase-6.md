# فاز ششم بازطراحی خزش: diagnostics و مشاهده‌پذیری

## هدف

خروجی «صفر سند» نباید فقط با یک پیام کلی نمایش داده شود. این فاز بدون تغییر API عمومی یا دیتابیس، از مرحله probe تا discovery، selection و fetch یک خلاصه typed و قابل ثبت تولید می‌کند تا مشخص شود شکست دقیقاً در کدام مرحله رخ داده است.

## قرارداد diagnostics

`CrawlDiagnostics` موارد زیر را نگه می‌دارد:

- وضعیت و `reason_code` نهایی؛
- شمار URLهای هر provider مانند RSS، sitemap، GDELT و Common Crawl؛
- تعداد کل URLها، انتخاب‌شده‌ها، خارج از بازه‌ها و حذف‌شده‌ها به‌علت سقف؛
- تعداد fetch موفق و ناموفق؛
- تعداد مسیرهای HTTP و browser؛
- خطاها براساس `failure_kind` و کدهای HTTP؛
- مدت اجرای crawl و یک خلاصه فارسی قابل نمایش.

## کدهای علت اصلی

| کد | معنی |
| --- | --- |
| `probe_failed` | صفحه اصلی منبع در دسترس نبود |
| `robots_blocked` | robots.txt خزش را منع کرد |
| `no_urls_discovered` | هیچ provider داخلی یا تاریخی URL نداد |
| `all_urls_outside_date_range` | URL پیدا شد اما نتایج تاریخ‌دار خارج بازه بودند |
| `no_pages_fetched` | URL انتخاب شد اما صفحه‌ای دریافت نشد |
| `all_fetches_failed` | تمام دریافت‌ها شکست خوردند |
| `partial_fetch_success` | بخشی از دریافت‌ها موفق بود |
| `completed` | دریافت همه صفحات انتخاب‌شده موفق بود |

## تفکیک مسئولیت‌ها

- `CrawlDiagnosticsBuilder`: فقط محاسبه و طبقه‌بندی، بدون I/O.
- `CrawlOrchestrator`: عبور داده‌های مرحله‌ها به builder.
- `SourceCrawlHealthUpdater`: نگاشت نتیجه به health منبع.
- Monitoring pipeline: ذخیره diagnostics در context لاگ سیستمی.

## سازگاری

- خروجی عمومی task `crawler.crawl_source` همان شش کلید قبلی را دارد.
- diagnostics در `CrawlOutcome` داخلی قابل دسترس است اما به قرارداد عمومی Celery نشت نمی‌کند.
- داده در JSON context لاگ‌های موجود ذخیره می‌شود؛ migration لازم نیست.
- منبعی که probe موفق ولی هیچ صفحه موفقی ندارد دیگر به‌اشتباه `SUCCESS` نمی‌گیرد و وضعیت آن `UNKNOWN` همراه دلیل دقیق است.

## معیار پذیرش

- هر crawl یک reason code قطعی داشته باشد.
- شمار providerها و fetch strategyها قابل مشاهده باشد.
- job بدون نتیجه علت قابل اقدام داشته باشد.
- قرارداد عمومی worker تغییر نکند.
- تست deterministic و smoke واقعی هر دو عبور کنند.
