# فاز هفتم — پیکربندی معتبر و قابل ممیزی برای هر منبع

## وضعیت

وضعیت اجرایی: **کامل و آماده تأیید کاربر**  
تاریخ اجرا: ۱۴۰۵/۰۵/۰۵ (2026-07-27)

## مسئله رفع‌شده

زیرساخت profile اختصاصی از قبل seedهای RSS، sitemap، search، archive و قواعد include/exclude را اجرا می‌کرد، اما `crawl_config` در API یک dictionary خام بود. در نتیجه خطاهایی مانند placeholder اشتباه، سقف نامعتبر یا endpoint متعلق به دامنه دیگر هنگام ثبت منبع کشف نمی‌شدند و می‌توانستند در زمان اجرای رصد باعث شکست دیرهنگام شوند. همچنین تنظیمات ذخیره‌شده در پاسخ منابع قابل مشاهده نبود.

## طراحی

### SourceCrawlConfiguration

قرارداد ورودی فقط دو بخش شناخته‌شده دارد:

- `crawl_policy` با validation کامل `CrawlPolicy`؛
- `processing_policy` با validation کامل `ProcessingPolicy`.

کلید ناشناخته رد می‌شود. هنگام ذخیره فقط فیلدهایی که کاربر صریحاً ارسال کرده است در JSON موجود نوشته می‌شوند و defaultهای مدل، ستون را حجیم نمی‌کنند.

### نرمال‌سازی host-safe

تابع pure با نام `normalize_source_discovery_profile`:

- endpoint دارای scheme غیر HTTP/HTTPS را رد می‌کند؛
- feed، sitemap، search و archive خارج از host منبع را رد می‌کند؛
- glob نسبی را به glob کامل همان host تبدیل می‌کند؛
- ترتیب و dedupe تنظیمات profile را حفظ می‌کند.

محافظ SSRF و same-host runtime همچنان باقی است؛ این validation یک کنترل زودهنگام اضافی است.

### خواندن تنظیمات

فیلد `crawl_config` به `SourceRead` اضافه شد. این تغییر backward-compatible است و اجازه می‌دهد تنظیمات منبع پس از ثبت از API خوانده، ممیزی و برای PUT کامل استفاده شود.

## نمونه معتبر

```json
{
  "title": "Example News",
  "base_url": "https://news.example.com",
  "type": "news_site",
  "is_active": true,
  "crawl_config": {
    "crawl_policy": {
      "max_pages": 80,
      "discovery_profile": {
        "replace_default_seeds": true,
        "feed_paths": ["/api/rss"],
        "sitemap_paths": ["/news/sitemap.xml"],
        "search_templates": ["/find?term={query}"],
        "archive_templates": [
          "/news/{year}/{month}/",
          "/archive/{jalali_year}/{jalali_month}/"
        ],
        "include_url_globs": ["/articles/*"],
        "exclude_url_globs": ["*/video/*", "*/author/*"]
      }
    },
    "processing_policy": {
      "min_text_length": 100,
      "min_matched_score": 0.01
    }
  }
}
```

## سازگاری داده

- دیتابیس پیش از تغییر به‌صورت read-only بررسی شد: ۳۱ منبع وجود دارد و `crawl_config`های فعلی کلید سفارشی ثبت‌شده ندارند.
- هیچ migration یا بازنویسی رکوردی انجام نشد.
- registry ایستای فاز دوم برای منابع شناخته‌شده همچنان فعال است.
- profile صریح منبع با profile registry ادغام می‌شود و سپس به providerهای موجود می‌رسد.

## پذیرش

- تست‌های هدفمند: ۱۹ موفق؛
- suite کامل backend: ۱۴۹ موفق، ۴ skipped؛
- OpenAPI: ۲۴ path و ۳۳ operation معتبر؛
- config جزئی فقط فیلدهای صریح را ذخیره کرد؛
- RSS و sitemap اختصاصی در حالت replace تولید شدند؛
- template میلادی و شمسی تولید seed کرد؛
- include/exclude پیش از مصرف بودجه اعمال شد؛
- endpoint خارج host، policy نامعتبر و کلید ناشناخته رد شدند؛
- `SourceRead` تنظیمات ذخیره‌شده را برگرداند؛
- verifier بدون شبکه، legacy workflow engine یا نوشتن دیتابیس عبور کرد.

## خارج از دامنه

- ساخت ویرایشگر گرافیکی profile در frontend؛
- تشخیص خودکار selectorهای اختصاصی متن و تاریخ؛
- تغییر رکوردهای موجود بدون تأیید؛
- اجرای رصد production جدید؛
- ورود به فاز هشتم.
