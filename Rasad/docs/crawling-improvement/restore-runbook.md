# Runbook بازیابی فاز صفر

## محدوده

پشتیبان معتبر در مسیر زیر قرار دارد و عمداً توسط Git نادیده گرفته می‌شود:

`D:\pr\10\backups\phase-zero\20260727-124903`

این مجموعه فقط به پروژه `rasad-analysis` و legacy workflow engine اختصاصی `legacy-workflow-engine_pr_10` تعلق دارد.
کانتینرهای `legacy-workflow-engine_pr_07-*` و `docker-*` خارج از محدوده‌اند.

## artifactهای اصلی

- `rasad-postgres.dump`: دیتابیس اصلی پروژه
- `legacy-workflow-engine-postgres.dump`: دیتابیس legacy workflow engine اختصاصی
- `legacy-workflow-engine-app-storage.tar.gz`: فایل‌های app storage
- `legacy-workflow-engine-plugin-daemon.tar.gz`: افزونه‌ها و runtime آن‌ها
- `legacy-workflow-engine-weaviate.tar.gz`: داده پایدار Weaviate
- `sources.csv`: تنظیمات قابل خواندن منابع
- `runtime-settings.secret.json`: تنظیمات Runtime دارای secret
- `legacy-workflow-engine.env.secret`: محیط legacy workflow engine دارای secret
- `manifest.json`: اندازه، حساسیت و SHA-256
- `restore-verification.json`: نتیجه بازیابی آزمایشی

## بررسی سلامت

```powershell
& scripts\phase_zero\Test-PhaseZeroArtifacts.ps1 `
  -BackupDirectory 'D:\pr\10\backups\phase-zero\20260727-124903'
```

## بازیابی آزمایشی غیرمخرب

```powershell
& scripts\phase_zero\Test-PhaseZeroRestore.ps1 `
  -BackupDirectory 'D:\pr\10\backups\phase-zero\20260727-124903'
```

این فرمان دیتابیسی با پیشوند کنترل‌شده `phase_zero_restore_` می‌سازد، هشت جدول،
migration و آخرین رصد را مقایسه می‌کند و همان دیتابیس موقت را حذف می‌کند.

## بازیابی عملیاتی

بازیابی روی دیتابیس اصلی یا legacy workflow engine فقط در رخداد واقعی و پس از تأیید صریح کاربر
مجاز است. قبل از آن باید سرویس نویسنده متوقف، یک backup جدید گرفته و target
دقیق دوباره بررسی شود. از اجرای مستقیم `pg_restore --clean` روی دیتابیس فعال
خودداری شود.

فایل‌های دارای پسوند `.secret` نباید وارد Git، log یا پیام‌های عمومی شوند.
