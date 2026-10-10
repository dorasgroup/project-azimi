# راهنمای عملیاتی Query Planning

## مشاهده plan

```powershell
docker compose -f docker/compose.yml exec -T api python scripts/verify_phase3_query_planning.py
```

این فرمان read-only است و موضوع‌های benchmark را برای زبان‌های فارسی، انگلیسی و ترکیبی تولید می‌کند.

## فیلدهای تشخیصی

در log رویداد `crawl_discovery_strategy`:

- `query_languages`: زبان‌های profile منبع؛
- `retrieval_queries`: عبارت‌هایی که فقط برای discovery استفاده شدند؛
- `profile_key`: profile انتخاب‌شده؛
- `attempted` و `skipped`: providerهای اجراشده یا کنارگذاشته‌شده.

## قواعد افزودن alias

1. alias باید همان مفهوم canonical را بیان کند.
2. افزودن زمینه جدید مانند «هسته‌ای» به موضوع عمومی «مذاکرات» مجاز نیست.
3. برای موضوع چندمفهومی، query باید همه مفاهیم هسته را با نام یا alias پوشش دهد.
4. variant جدید باید تست مثبت و کنترل منفی داشته باشد.
5. تعداد queryها از ۸ و تعداد query مصرف‌شده در هر search template از ۴ بیشتر نمی‌شود.

## عیب‌یابی

| نشانه | بررسی |
|---|---|
| query فارسی به منبع انگلیسی ارسال شد | languages پروفایل منبع را بررسی کنید |
| search endpoint پاسخ 404 می‌دهد | capability `search` را حذف و RSS/Sitemap را فعال نگه دارید |
| URL پیدا می‌شود ولی سند رد می‌شود | concepts و relevance score را بررسی کنید؛ query planner موضوع پذیرش را تغییر نمی‌دهد |
| query بیش‌ازحد عمومی است | variant را حذف یا پوشش همه مفاهیم را اجباری کنید |
| query زبان غیرقابل‌اعتماد است | از English fallback استفاده کنید؛ ترجمه حدسی اضافه نکنید |

## مرز با جست‌وجوی هیبریدی

این فاز query expansion است، نه ranking هیبریدی. ترکیب BM25/lexical score با embedding/semantic score و تنظیم وزن‌ها در فاز جداگانه انجام می‌شود.
