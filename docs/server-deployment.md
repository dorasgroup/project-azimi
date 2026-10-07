# استقرار اختصاصی روی VPS

این استقرار با دو namespace مستقل اجرا می‌شود:

- سامانه اصلی: `rasad10`
- legacy workflow engine اختصاصی: `legacy-workflow-engine_pr_10`

پورت‌های عمومی رزروشده:

- Frontend: `14173`
- Backend API: `18010`
- legacy workflow engine: `18011`
- legacy workflow engine plugin daemon: `15030` فقط روی loopback سرور

پورت‌های `80`، `443` و `8081` متعلق به سرویس‌های قبلی VPS هستند و توسط این استقرار استفاده یا تغییر نمی‌کنند.

فایل اصلی سامانه در سرور `docker/compose.server.yml` و override مربوط به legacy workflow engine در `legacy-workflow-engine/docker/docker-compose.server.yaml` است. داده‌های PostgreSQL سامانه از dump منطقی و داده‌های legacy workflow engine از snapshot سازگار volumeها بازیابی می‌شوند.

پیش از راه‌اندازی، پورت‌ها باید با `ss -lntup` و پروژه‌های Compose با `docker compose ls` بررسی شوند. عملیات عمومی مانند `docker system prune`، حذف volumeهای ناشناس یا ویرایش Nginx پروژه‌های دیگر ممنوع است.

## استقرار 2026-07-27

- مسیر نصب: `/opt/rasad10`
- مسیر بسته‌های بازیابی: `/opt/rasad10-transfer`
- Swap اختصاصی محافظ OOM: `/swapfile-rasad10` با ظرفیت ۸ GiB
- Frontend: `http://5.10.249.72:14173`
- Backend health: `http://5.10.249.72:18010/api/v1/health`
- legacy workflow engine: `http://5.10.249.72:18011`

شمار داده‌های بازیابی‌شده با محیط مبدأ برابر بود: ۳۱ منبع، ۲۲۸ سند خزش‌شده، ۲ رصد، ۲ گزارش، ۱ کاربر، ۲ برنامه legacy workflow engine، ۱۷ workflow و ۳ provider.

تست‌های استقرار:

- API، Frontend و legacy workflow engine از اینترنت پاسخ موفق دادند؛
- Backend اتصال PostgreSQL، Redis و legacy workflow engine را `success` گزارش کرد؛
- Celery پاسخ `pong` داد؛
- ایرنا از egress اختصاصی Worker با HTTP 200 خوانده شد؛
- درخواست loopback در gateway با HTTP 403 مسدود شد؛
- دو سرویس قبلی `teb.aifluxa.ir` و `tgt.haminyar.ir` همچنان HTTP 200 دادند.

محدودیت باقی‌مانده: اتصال TLS به BBC از شبکه VPS قطع می‌شود. برای این گروه از منابع باید `CRAWL_UPSTREAM_SOCKS_PROXY` به یک SOCKS5 معتبر و اختصاصی تنظیم شود؛ استفاده از پراکسی عمومی توصیه نمی‌شود.

## فرمان‌های مدیریت

سامانه اصلی:

```bash
cd /opt/rasad10
docker compose -f docker/compose.server.yml --env-file .env --profile worker ps
docker compose -f docker/compose.server.yml --env-file .env --profile worker up -d
```

legacy workflow engine:

```bash
cd /opt/rasad10/legacy-workflow-engine/docker
docker compose -f docker-compose.yaml -f docker-compose.server.yaml --env-file .env ps
docker compose -f docker-compose.yaml -f docker-compose.server.yaml --env-file .env up -d
```
