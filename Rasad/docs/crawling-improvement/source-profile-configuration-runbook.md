# راهنمای پیکربندی اختصاصی منبع

## آزمون قطعی

```powershell
docker compose -f docker/compose.yml exec -T api pytest -q tests/test_source_crawl_configuration.py tests/test_source_discovery_profiles.py
docker compose -f docker/compose.yml exec -T api python scripts/verify_phase7_source_configuration.py
```

## قواعد endpoint

- مسیرهای نسبی مانند `/api/rss` توصیه می‌شوند.
- URL کامل فقط وقتی پذیرفته می‌شود که host آن دقیقاً host `base_url` باشد.
- search template باید `{query}` داشته باشد.
- archive template فقط از `{year}`، `{month}`، `{jalali_year}` و `{jalali_month}` استفاده کند.
- redirect و URL نهایی همچنان توسط محافظ runtime بررسی می‌شوند.

## حالت extend و replace

- `replace_default_seeds=false`: seedهای اختصاصی به seedهای عمومی و registry افزوده می‌شوند.
- `replace_default_seeds=true`: seedهای عمومی حذف می‌شوند؛ profile ثبت‌شده منبع و profile صریح همچنان مبنا هستند.

حالت replace فقط وقتی استفاده شود که endpointهای عمومی منبع خطا یا محتوای نامرتبط تولید می‌کنند.

## قواعد admission

- glob نسبی مانند `/articles/*` خودکار به host منبع متصل می‌شود.
- ابتدا exclude و سپس include بررسی می‌شود.
- include خالی یعنی همه URLهای same-host مجازند، مگر آن‌هایی که exclude شده‌اند.
- از glob محدود استفاده کنید؛ regex دلخواه پشتیبانی نمی‌شود.

## تشخیص خطای 422

| خطا | اصلاح |
|---|---|
| unknown field | نام کلید را با `crawl_policy` یا `processing_policy` تطبیق دهید |
| search template missing query | `{query}` را به template اضافه کنید |
| unsupported archive placeholder | فقط placeholderهای مجاز را استفاده کنید |
| endpoint must remain on source host | endpoint را نسبی کنید یا host صحیح منبع را وارد کنید |
| policy limit validation | مقدار را به بازه اعلام‌شده در OpenAPI برگردانید |

## ویرایش امن

1. منبع را با GET بخوانید و `crawl_config` فعلی را نگه دارید.
2. فقط بخش موردنظر را اصلاح کنید.
3. PUT کامل را با title، URL، type، is_active و crawl_config ارسال کنید.
4. ابتدا بررسی اتصال و سپس یک رصد محدود انجام دهید.
5. در system log، `crawl_stage_trace` و `source_diagnostics` فاز ششم را بررسی کنید.

