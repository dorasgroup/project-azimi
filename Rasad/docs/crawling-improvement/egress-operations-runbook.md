# راهنمای عملیاتی خروج خزشگر

## بررسی سلامت

```powershell
docker compose -f docker/compose.yml --profile worker ps
docker compose -f docker/compose.yml logs --tail=100 crawler-egress worker
```

`crawler-egress` باید healthy و Worker باید running باشد. log gateway فقط hostname، port، نتیجه
و latency را نگه می‌دارد.

## حالت‌ها

- `CRAWL_WORKER_EGRESS_MODE=trusted_proxy`: حالت توصیه‌شده و پیش‌فرض Worker.
- `CRAWL_UPSTREAM_SOCKS_PROXY=`: اتصال مستقیم پس از DoH و کنترل SSRF.
- `CRAWL_UPSTREAM_SOCKS_PROXY=host:port`: عبور اتصال‌های pin‌شده از SOCKS5 اختصاصی.
- برای SOCKS5 احراز هویت‌شده از `user:password@host:port` استفاده کنید؛ مقدار را فقط در `.env`
  نگه دارید.

برای rollback موقت می‌توان `CRAWL_WORKER_EGRESS_MODE=direct` را تنظیم و فقط Worker را recreate
کرد؛ این حالت برای شبکه‌ی sinkhole دسترسی رسانه‌های محدود را حل نمی‌کند.

## رفع خطا

- `Crawler egress is unavailable`: سلامت gateway و شبکه‌ی Docker را بررسی کنید.
- HTTP 403 از gateway: مقصد خصوصی، پورت نامجاز یا DNS غیرعمومی بوده است.
- TLS EOF با وجود resolve عمومی: محدودیت مسیر خروج است؛ SOCKS5 سالم لازم است.
- خطای tunnel: endpoint، دسترسی، انقضا و محدودیت IP/حجم پراکسی بالادستی را بررسی کنید.
