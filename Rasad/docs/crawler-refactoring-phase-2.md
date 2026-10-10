# فاز دوم بازطراحی خزش: کشف هیبریدی URL

## هدف

در این فاز، مرحله‌ی discovery از اتکا به صفحه‌ی اصلی و چند endpoint ثابت خارج شد و URLهای خبر
از چند provider مستقل جمع‌آوری می‌شوند. دریافت و پردازش محتوای مقاله تغییری نکرده است.

## جریان جدید

```text
source + topic + date range
        |
        +-- robots.txt ---------> sitemap seeds
        +-- default sitemaps ---> recursive sitemap traversal
        +-- RSS / Atom ---------> feed links
        +-- homepage -----------> HTML links
        +-- internal search ----> result pagination
        +-- monthly archives ---> archive pagination
                                  |
                                  v
                 canonicalize + deduplicate + provenance merge
```

## اجزای پیاده‌سازی‌شده

| جزء | مسئولیت |
| --- | --- |
| `DiscoverySeedBuilder` | ساخت seedهای feed، sitemap، search و آرشیو |
| `HtmlDiscoveryProvider` | خواندن HTML و دنبال‌کردن pagination محدود |
| `FeedDiscoveryProvider` | استخراج URL از RSS و Atom |
| `SitemapDiscoveryProvider` | خواندن robots و پیمایش بازگشتی sitemap index |
| `merge_discovered_links` | canonicalization، dedupe و ادغام provenance |
| `LinkDiscoveryService` | هماهنگی providerها بدون انجام parsing یا I/O تخصصی |

## قابلیت‌ها

- خواندن تمام دستورهای `Sitemap:` معتبر از `robots.txt`؛
- پیمایش sitemap index تا عمق قابل تنظیم؛
- حذف محدودیت ثابت ۱۲ sitemap و جایگزینی با سقف ایمنی قابل تنظیم؛
- اولویت‌دادن به sitemapهای دارای سال و ماه بازه‌ی هدف؛
- پشتیبانی از sitemap فشرده‌ی gzip؛
- ساخت archive seed میلادی و برای منابع فارسی، شمسی؛
- دنبال‌کردن لینک‌های `rel=next` و pagination در search/archive؛
- حذف پارامترهای رهگیری مانند `utm_*` و `fbclid`؛
- حفظ پارامترهای محتوایی query و مرتب‌سازی آن‌ها؛
- ثبت همه‌ی مسیرهای کشف در `discovery_methods`؛
- ادغام RSS، sitemap، search، archive و HTML برای یک canonical URL.

## سقف‌های ایمنی

مقادیر زیر در `CrawlPolicy` قابل تنظیم‌اند:

| گزینه | پیش‌فرض | کاربرد |
| --- | ---: | --- |
| `max_sitemaps` | ۱۲۰ | حداکثر sitemap در هر منبع |
| `max_sitemap_depth` | ۴ | عمق sitemap index |
| `max_discovery_pages` | ۴ | صفحات pagination هر search/archive seed |
| `max_archive_seeds` | ۱۸ | endpointهای آرشیوی عمومی |
| `max_discovered_urls` | ۵۰۰۰ | سقف URL کاندید پیش از مرحله fetch |

این سقف‌ها برای جلوگیری از حلقه، مصرف بی‌حد حافظه و فشار ناخواسته روی یک دامنه هستند و محدودیت
ثابت قبلی ۱۲ sitemap محسوب نمی‌شوند.

## سازگاری

- `CrawlEngine.discover` و `CrawlEngine.crawl` همان امضای قبلی را دارند.
- فیلد `discovered_via` برای callerهای موجود حفظ شده است.
- `discovery_methods` به‌شکل افزایشی و دارای مقدار پیش‌فرض اضافه شده است.
- دیتابیس، migration، API، Docker، legacy workflow engine و پورت‌ها تغییر نکرده‌اند.

## تست پذیرش

تست‌های فاز موارد زیر را اثبات می‌کنند:

- پیمایش بیش از ۱۲ sitemap فرزند؛
- خواندن sitemap فشرده؛
- اولویت ماه و سال هدف؛
- کشف sitemap از robots؛
- pagination جست‌وجو؛
- archive seed شمسی و میلادی؛
- حذف tracking query و dedupe؛
- ادغام provenance یک URL از RSS، sitemap و HTML؛
- عبور تست‌های معماری، adapter، monitoring و کل backend.

```powershell
docker compose --env-file .env -f docker/compose.yml exec -T api `
  python -m pytest tests/test_hybrid_url_discovery.py -q
```

Smoke test واقعی در worker:

```powershell
docker compose --env-file .env -f docker/compose.yml exec -T worker `
  python -m scripts.verify_crawler
```
