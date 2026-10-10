# عملیات و استقرار MVP

## پیش‌نیازها

- Docker Engine یا Docker Desktop سالم، PostgreSQL و Redis
- یک domain و reverse proxy HTTPS برای استقرار اینترنتی
- URL عمومی کانال‌های Telegram، X Bearer Token در صورت استفاده از X، و Bale Bot Token و گروه مقصد

## راه‌اندازی اولیه

1. `.env.example` را به `.env` کپی و secretهای محیطی را فقط همان‌جا قرار دهید.
2. سرویس‌ها را اجرا و migration را اعمال کنید: `docker compose up --build -d` و `docker compose exec web python manage.py migrate`.
3. کاربر staff بسازید: `docker compose exec web python manage.py createsuperuser`.
4. Sourceهای عمومی Telegram/X و مقصد بله را از پنل staff ثبت کنید. برای مشاهده محدود chat id بله، `docker compose exec web python manage.py bale_chat_ids` را اجرا کنید.
5. اتصال X و بله را با credentialهای محیطی بررسی و ابتدا پیام آزمایشی بله را ارسال کنید.

## رصد خودکار

از تنظیمات سامانه، «رصد خودکار فعال» و فاصله را تعیین کنید؛ حداقل فاصله پنج دقیقه و مقدار پیش‌فرض ۶۰ دقیقه است. Beat هر یک دقیقه dispatcher سبک را اجرا می‌کند و dispatcher بدون restart شدن Beat، تنظیمات پایگاه‌داده را می‌خواند. وضعیت و stageهای pipeline در `/monitoring/automation/runs/` و Deliveryها در پنل بله قابل مشاهده‌اند.

هر pipeline یک مالک تصادفیِ lease دریافت می‌کند. قفل و زمان انقضا در `AutomationState` هستند و فقط همان مالک می‌تواند lease را تمدید یا آزاد کند. lease پیش از هر stage اصلی تمدید می‌شود؛ اگر مالکیت از دست برود، pipeline پیش از stage بعدی با خطای امن متوقف می‌شود. lease پیش‌فرض ۳۰ دقیقه است؛ در crash worker، پس از پایان lease اجرای تازه می‌تواند قفل را تصاحب کند. اجرای قدیمی هرگز نمی‌تواند قفل مالک جدید را آزاد یا overwrite کند.

## ارسال ترند به سامانه ایده‌پردازی

اتصال با `IDEA_INTEGRATION_ENABLED`، `IDEA_API_BASE_URL` و `IDEA_API_TOKEN` در `.env` کنترل می‌شود. توکن را در پنل، لاگ یا فایل‌های repository قرار ندهید. پس از تغییر این مقادیر، سرویس‌های `web`، `worker` و `beat` را بازآفرینی کنید.

Beat هر دقیقه outbox را بررسی می‌کند. ارسال فقط برای ترندهای `trending` یا `confirmed` و ادغام‌نشده انجام می‌شود. کلید idempotency مانع ساخت فایل یا سند Dify تکراری است. وضعیت دو مرحله مستقل در `/monitoring/idea/deliveries/` نمایش داده می‌شود: پذیرش در سامانه ایده‌پردازی و نمایه‌سازی در Dify.

برای توقف فوری ارسال بدون حذف صف، `IDEA_INTEGRATION_ENABLED=false` را تنظیم و `web`، `worker` و `beat` را بازآفرینی کنید. رکوردهای pending حفظ می‌شوند و پس از فعال‌سازی دوباره ادامه پیدا می‌کنند. خطاهای شبکه، `429` و `5xx` با backoff محدود دوباره تلاش می‌شوند؛ خطاهای احراز هویت و اعتبارسنجی نیازمند اصلاح تنظیمات و تلاش مجدد اپراتور هستند.

برای پایش، سلامت اصلی، لاگ‌های `worker` و `beat` و تعداد وضعیت‌های failed در صفحه تحویل را بررسی کنید. موفقیت نهایی فقط زمانی است که وضعیت ایده‌پردازی `accepted` و وضعیت Dify برابر `indexed` باشد.

## نگهداری و backup

پیش از هر تغییر عملیاتی backup بگیرید:

```powershell
docker compose exec -T db pg_dump -U trend_monitor trend_monitor > backup.sql
Get-Content backup.sql | docker compose exec -T db psql -U trend_monitor -d trend_monitor
```

برای مشاهدهٔ داده‌های قابل پاک‌سازی بدون حذف:

```powershell
docker compose exec web python manage.py cleanup_retention --dry-run
```

پاک‌سازی واقعی فقط task روزانه است. محتواهای متصل به Delivery یا Clusterهای فعال، confirmed یا suppressed حذف نمی‌شوند. هرگز `docker compose down -v` را برای rollback اجرا نکنید؛ rollback عملیاتی با توقف سرویس‌ها (`docker compose down`) و بازگرداندن image/backup انجام می‌شود و volumeها حفظ می‌شوند.

## خطاهای متداول

- نبود X token: فقط جمع‌آوری X ناموفق/partial می‌شود؛ Telegram و تحلیل ادامه می‌یابند.
- rate limit X یا Bale: وضعیت و خطای امن در run/Delivery ثبت می‌شود؛ token در log دیده نمی‌شود.
- تغییر markup Telegram: parser best-effort ممکن است نیازمند اصلاح selector باشد.
- Redis یا worker در دسترس نیست: taskهای صف‌شده اجرا نمی‌شوند؛ health و log سرویس را بررسی کنید.
- Docker Desktop اجرا نمی‌شود: ابتدا engine را راه‌اندازی کنید؛ مسیر پروژه را برای دورزدن این مشکل تغییر ندهید.

## reverse proxy نمونه

reverse proxy باید TLS را terminate کند، `Host` و `X-Forwarded-Proto: https` را ارسال کند و سپس `DJANGO_USE_HTTPS=true`، `DJANGO_CSRF_TRUSTED_ORIGINS=https://your-domain` و در صورت نیاز `DJANGO_SECURE_SSL_REDIRECT=true` تنظیم شوند. `/admin/` را با حساب staff قوی، HTTPS و محدودسازی دسترسی شبکه محافظت کنید. بدون domain و DNS واقعی، certificate یا استقرار خارجی انجام ندهید.
