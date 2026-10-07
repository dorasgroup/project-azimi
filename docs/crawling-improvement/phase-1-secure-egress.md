# فاز اول: مسیر خروج امن و اختصاصی خزشگر

تاریخ اجرا: ۱۴۰۵/۰۵/۰۵ (2026-07-27)

## نتیجه

مسیر شبکه‌ی خزشگر از API و legacy workflow engine جدا شد. فقط Worker متغیرهای `CRAWL_EGRESS_MODE` و
`CRAWL_PROXY_URL` را دریافت می‌کند و تمام درخواست‌های HTTP و مرورگر Crawl4AI از سرویس داخلی
`crawler-egress` عبور می‌کنند. این سرویس هیچ پورتی روی میزبان منتشر نمی‌کند.

اعتبارسنجی SSRF موجود تغییر نکرده است. سیاست افزوده‌شده مقصد را پیش از درخواست کنترل می‌کند و
gateway نیز مقصد را دوباره بررسی می‌کند، فقط پورت‌های ۸۰ و ۴۴۳ را می‌پذیرد، DNS را با DoH حل
می‌کند و به همان IP عمومی اعتبارسنجی‌شده متصل می‌شود. در خرابی gateway هیچ fallback مستقیمی
وجود ندارد.

## یافته‌ی شبکه

- DNS محلی و حتی پرس‌وجوی مستقیم از resolverهای عمومی، `www.bbc.com` را به IP خصوصی
  `10.10.34.36` sinkhole می‌کنند.
- Google DoH آدرس‌های عمومی صحیح را برمی‌گرداند، اما TLS به IPهای عمومی BBC نیز در شبکه‌ی
  میزبان قطع می‌شود؛ پس مشکل فقط DNS نیست و محدودیت مسیر خروج نیز وجود دارد.
- پراکسی SSRF متعلق به legacy workflow engine همین محدودیت را دارد و برای خزشگر استفاده نشد.
- دو نسخه‌ی containerized WARP بدون انتشار پورت آزمایش شدند؛ ثبت اولیه‌ی یکی با
  `ApiMismatch` و دیگری با timeout شبکه‌ی Cloudflare شکست خورد. container ناموفق حذف شد و
  volumeهای آزمایشی حذف نشدند.

بنابراین منابع داخلی و منابع خارجی آزاد از مسیر جدید کار می‌کنند، اما برای رسانه‌های مسدودشده
باید یک SOCKS5 سالم در `CRAWL_UPSTREAM_SOCKS_PROXY` تنظیم شود. نمونه برای پراکسی میزبان:

```dotenv
CRAWL_UPSTREAM_SOCKS_PROXY=host.docker.internal:1080
```

برای endpoint احراز هویت‌شده قالب `user:password@host:port` نیز پشتیبانی می‌شود. این مقدار در
log ثبت نمی‌شود و فایل `.env` نباید commit شود.

پس از تغییر مقدار:

```powershell
docker compose -f docker/compose.yml --profile worker up -d --build crawler-egress
docker compose -f docker/compose.yml --profile worker up -d --no-build worker
```

## شواهد پذیرش

- تست‌های مرتبط: `18 passed, 1 skipped`
- دریافت `https://www.irna.ir` از داخل Worker: HTTP 200
- تلاش برای `http://127.0.0.1`: HTTP 403 از gateway
- API: HTTP 200
- legacy workflow engine: HTTP 200
- شمارش دیتابیس بعد از تغییر: ۳۱ منبع، ۲ job، ۲۲۸ سند و ۲ گزارش
- پورت‌های میزبان بدون تغییر: Frontend 4173، API 8010، legacy workflow engine 8011/44310، plugin 5030 و
  Weaviate 8081. سرویس egress فقط `expose: 8080` داخلی دارد.

## وضعیت پذیرش فاز

پیاده‌سازی و ایمن‌سازی نرم‌افزاری انجام شده و مسیر داخلی آزموده شده است. با تصمیم مالک پروژه،
آزمون «دسترسی واقعی به رسانه‌های مسدود» و تنظیم endpoint نهایی SOCKS5 به استقرار سرور موکول شد.
این بدهی استقراری مانع شروع فاز دوم نیست، ولی در چک‌لیست انتقال به سرور باید بسته شود.
