# فاز هفتم بازطراحی خزش: پروفایل اختصاصی منبع

## هدف

رسانه‌ها ساختار یکسانی ندارند. مسیر RSS، sitemap، جست‌وجو، آرشیو و شکل URL مقاله در هر سایت متفاوت است. این فاز به‌جای افزودن شرط‌های دامنه‌ای به موتور عمومی، یک profile محدود و قابل اعتبارسنجی به `CrawlPolicy` اضافه می‌کند.

## تنظیمات profile

```json
{
  "crawl_policy": {
    "discovery_profile": {
      "replace_default_seeds": false,
      "feed_paths": ["/api/rss"],
      "sitemap_paths": ["/news/sitemap.xml"],
      "search_templates": ["/find?term={query}"],
      "archive_templates": [
        "/news/{year}/{month}/",
        "/archive/{jalali_year}/{jalali_month}/"
      ],
      "include_url_globs": ["https://example.com/articles/*"],
      "exclude_url_globs": ["*/video/*", "*/author/*"]
    }
  }
}
```

## مسئولیت اجزا

- `SourceDiscoveryProfile`: قرارداد و اعتبارسنجی تنظیمات.
- `SourceProfileSeedBuilder`: تبدیل templateها به seedهای محدود و same-host.
- `SourceUrlAdmissionPolicy`: اعمال include/exclude روی URLهای کشف‌شده.
- `DiscoverySeedBuilder`: ادغام seedهای عمومی و اختصاصی.
- `LinkDiscoveryService`: اعمال admission پیش از merge و محدودیت نهایی.

## placeholderها

- search: فقط `{query}`؛
- archive: `{year}`، `{month}`، `{jalali_year}` و `{jalali_month}`.

مقادیر ناشناخته هنگام اعتبارسنجی رد می‌شوند. query به‌صورت URL-encoded جای‌گذاری می‌شود و URL نهایی باید متعلق به host همان منبع باشد.

## رفتار پیش‌فرض

اگر profile وجود نداشته باشد، رفتار فازهای قبلی دقیقاً حفظ می‌شود. با `replace_default_seeds=false` مسیرهای اختصاصی به مسیرهای عمومی افزوده می‌شوند. مقدار `true` فقط seedهای profile را نگه می‌دارد و برای سایت‌هایی مناسب است که endpointهای عمومی روی آن‌ها خطا یا محتوای نامرتبط تولید می‌کنند.

## امنیت و کنترل منابع

- هر فهرست سقف تعداد و هر مقدار سقف طول دارد؛
- URL خارج از دامنه منبع نادیده گرفته می‌شود؛
- از glob محدود به‌جای regex دلخواه استفاده می‌شود؛
- سقف‌های pagination، archive، sitemap و URL فازهای قبل همچنان اعمال می‌شوند؛
- تنظیم profile نیازمند migration یا تغییر schema دیتابیس نیست و در JSON موجود `crawl_config` ذخیره می‌شود.

## معیار پذیرش

- profile خالی رفتار قبلی را حفظ کند؛
- حالت extend و replace هر دو کار کنند؛
- template میلادی و شمسی درست render شود؛
- endpoint خارج host پذیرفته نشود؛
- include/exclude پیش از مصرف سقف نهایی اعمال شود؛
- production adapter با profile واقعی خزش موفق انجام دهد.
