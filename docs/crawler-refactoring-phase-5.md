# فاز پنجم بازطراحی خزش: fallback تاریخی خارجی

## هدف

اگر RSS، sitemap، جست‌وجوی داخلی و آرشیو سایت برای بازه تاریخی نتیجه کافی ندهند، discovery از indexهای مستقل GDELT و Common Crawl کمک می‌گیرد. این مرحله فقط URL کاندید تولید می‌کند؛ دریافت صفحه، تشخیص تاریخ معتبر و پردازش محتوا همچنان در لایه‌های فازهای قبلی انجام می‌شوند.

## جریان اجرا

```text
local hybrid discovery
        |
        +-- sufficient results --> fetch phase
        |
        +-- insufficient + bounded date range
                |
                +-- GDELT DOC API (topic + source domain + date range)
                +-- Common Crawl CDX (overlapping collections + source URL patterns)
                                      |
                                      v
                      host validation + canonicalize + dedupe
                                      |
                                      v
                           merge with local provenance
```

## مسئولیت اجزا

- `GdeltDiscoveryProvider`: ساخت query محدود، parse پاسخ و قبول URLهای همان منبع.
- `CommonCrawlDiscoveryProvider`: انتخاب collectionهای هم‌پوشان و parse رکوردهای CDX.
- `HistoricalDiscoveryService`: تصمیم fallback و جداسازی خطای providerها.
- `LinkDiscoveryService`: ادغام نتایج محلی و تاریخی، بدون دانستن جزئیات API خارجی.

## سیاست‌ها

- `historical_discovery_enabled`: کل fallback تاریخی.
- `gdelt_discovery_enabled` و `common_crawl_discovery_enabled`: فعال‌سازی مستقل providerها.
- `historical_fallback_min_results`: حداقل نتیجه محلی پیش از صرف‌نظر از provider خارجی.
- `max_external_results`: سقف URLهای خارجی.
- `common_crawl_max_indexes`: سقف collectionهای Common Crawl در هر رصد.

## ایمنی و صحت تاریخ

- fallback فقط برای رصد دارای ابتدا و انتهای بازه اجرا می‌شود.
- URL خارجی باید دقیقاً متعلق به host منبع مجاز باشد.
- تاریخ مشاهده GDELT و timestamp آرشیو Common Crawl به‌عنوان تاریخ انتشار ذخیره نمی‌شوند؛ تاریخ واقعی در فاز سوم از صفحه خبر حل می‌شود.
- خطای یک provider باعث توقف discovery محلی یا provider دیگر نمی‌شود.
- هیچ API key، migration دیتابیس یا تغییر endpoint عمومی لازم نیست.

## معیار پذیرش

- وقتی local discovery کافی است هیچ تماس خارجی انجام نشود.
- GDELT فقط URL همان دامنه و داخل بازه را برگرداند.
- Common Crawl فقط collectionهای هم‌پوشان و رکوردهای HTML موفق را بررسی کند.
- provenanceهای `gdelt` و `common_crawl` در merge حفظ شوند.
- قطع provider خارجی باعث شکست کل job نشود.

## منابع فنی

- [مستند رسمی GDELT DOC 2.0 API](https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/)
- [Common Crawl Index Server](https://index.commoncrawl.org/)
- [راهنمای رسمی کار با CDX و collinfo](https://github.com/commoncrawl/whirlwind-python/)
