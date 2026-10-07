# سامانه رصد و تحلیل

اسکلت محلی سامانه رصد و تحلیل شناختی اخبار شامل React SPA، FastAPI، Celery، PostgreSQL و Redis است. legacy workflow engine در مسیر `D:\legacy workflow engine` نصب شده، با Compose مستقل خود اجرا می‌شود و جزو Compose این مخزن نیست.

## پیش‌نیازها

- Git
- Docker Desktop
- Docker Compose
- legacy workflow engine محلی در صورت نیاز به تحلیل

API پروژه از داخل کانتینرها به legacy workflow engine با آدرس `http://host.docker.internal/v1` متصل می‌شود. برای اجرای runtime workflow باید `LEGACY_WORKFLOW_ENGINE_API_KEY` در فایل `.env` تنظیم شود؛ `LEGACY_WORKFLOW_ENGINE_WORKFLOW_ID` فعلا نقش مرجع مستندسازی و پیگیری workflow منتشرشده را دارد.
برای احراز هویت، مقدار `AUTH_SECRET_KEY` نیز باید در محیط واقعی از مقدار نمونه تغییر کند.

## راه‌اندازی

```powershell
Copy-Item .env.example .env
docker compose -f docker/compose.yml up --build -d
```

اگر مخزن اینترنتی Docker Hub برای دانلود imageهای جدید در دسترس نیست، اجرای آفلاین با imageهای موجود سیستم:

```powershell
docker compose -f docker/compose.yml -f docker/compose.offline.yml up -d
```

در اجرای آفلاین، صفحه وضعیت استاتیک به‌جای build توسعه‌ای React اجرا می‌شود. worker کامل نیز تا زمان دسترسی به رجیستری با profile جدا نگه داشته شده است.

آدرس‌ها:

- رابط کاربری: `http://localhost:5173`
- API: `http://localhost:8010`
- مستندات API: `http://localhost:8010/docs`
- سلامت API: `http://localhost:8010/api/v1/health`
- PostgreSQL و Redis فقط داخل شبکه Compose در دسترس هستند.

## بررسی سرویس‌ها

```powershell
docker compose -f docker/compose.yml ps
docker compose -f docker/compose.yml logs api worker
```

## تست جامع پروژه

اجرای کامل آزمون‌های پروژه:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/verify_project.ps1
```

اجرای سریع بدون آزمون‌های کندتر خزش و جریان کامل:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/verify_project.ps1 -SkipSlow
```

راهنمای تست جامع و نتیجه آخرین اجرا در `docs/testing.md` ثبت شده است.

## اجرای MVP

آماده‌سازی کاربر و داده demo محلی:

```powershell
docker compose --env-file .env -f docker/compose.yml exec -T api python -m scripts.seed_mvp
```

ورود در رابط کاربری:

- نشانی: `http://localhost:5173`
- نام کاربری: `mvp_admin`
- رمز عبور: `MvpDemoPass123!`

بررسی سناریوی کامل MVP از مسیر HTTP واقعی:

```powershell
docker compose --env-file .env -f docker/compose.yml exec -T api python -m scripts.verify_mvp
```

راهنمای اجرای MVP در `docs/mvp-runbook.md` ثبت شده است.

## داشبورد حرفه‌ای و نقشه راه

در فاز هفده، خلاصه مدیریتی سیستم با مسیر `GET /api/v1/dashboard/summary` اضافه شد و کارت‌های آماری فرانت‌اند به این مسیر متصل شدند. بررسی مستقل این قابلیت:

```powershell
docker compose --env-file .env -f docker/compose.yml exec -T api python -m scripts.verify_professional_features
```

نقشه راه نسخه حرفه‌ای‌تر و موارد پیشنهادی بعد از پایان فازبندی فعلی در `docs/professional-roadmap.md` ثبت شده است.

## مدیریت دیتابیس

اجرای migrationها:

```powershell
docker compose -f docker/compose.yml exec api alembic upgrade head
```

بررسی همگامی مدل‌ها و schema:

```powershell
docker compose -f docker/compose.yml exec api alembic check
docker compose -f docker/compose.yml exec api python -m scripts.verify_database
```

بررسی قرارداد و ساختار OpenAPI:

```powershell
docker compose -f docker/compose.yml exec api python -m scripts.verify_openapi
```

بررسی قرارداد Workflow مربوط به legacy workflow engine:

```powershell
docker compose -f docker/compose.yml exec api python -m scripts.verify_legacy-workflow-engine_workflow
```

راهنمای Workflow و روش Import در `docs/legacy-workflow-engine-workflow.md` ثبت شده است.

بررسی اتصال بک‌اند به legacy workflow engine و ذخیره گزارش با mock محلی:

```powershell
docker compose --env-file .env -f docker/compose.yml exec -T api python -m scripts.verify_legacy-workflow-engine_integration
```

راهنمای اتصال بک‌اند به legacy workflow engine در `docs/legacy-workflow-engine-backend-integration.md` ثبت شده است. برای اجرای واقعی تحلیل، مقدار `LEGACY_WORKFLOW_ENGINE_API_KEY` در `.env` باید تنظیم شده باشد.

بررسی موتور خزش با Crawl4AI:

```powershell
docker run --rm --env-file .env -v D:/pr/10/backend:/app -w /app rasad-analysis-worker python -m scripts.verify_crawler
docker run --rm --env-file .env -v D:/pr/10/backend:/app -w /app rasad-analysis-worker python -m scripts.verify_javascript_crawler
docker run --rm --env-file .env -v D:/pr/10/backend:/app -w /app rasad-analysis-worker python -m scripts.verify_content_processing
```

راهنمای موتور خزش و وضعیت رندر JavaScript در `docs/crawler-engine.md` ثبت شده است.

بررسی جریان کامل رصد از ایجاد job تا خزش، پردازش و آماده‌شدن برای legacy workflow engine:

```powershell
docker compose --env-file .env -f docker/compose.yml --profile worker exec -T worker python -m scripts.verify_monitoring_flow
```

راهنمای جریان کامل رصد در `docs/monitoring-flow.md` ثبت شده است.

بررسی تولید خروجی PDF و Word گزارش:

```powershell
docker compose --env-file .env -f docker/compose.yml exec -T api python -m scripts.verify_report_exports
```

راهنمای خروجی‌های گزارش در `docs/report-exports.md` ثبت شده است.

بررسی بهینه‌سازی‌های سرعت و cache خزش:

```powershell
docker compose --env-file .env -f docker/compose.yml --profile worker exec -T worker python -m scripts.verify_performance
```

راهنمای بهینه‌سازی سرعت در `docs/performance-optimization.md` ثبت شده است.

بررسی احراز هویت و کنترل دسترسی:

```powershell
docker compose --env-file .env -f docker/compose.yml exec -T api python -m scripts.verify_security
```

راهنمای امنیت و کنترل دسترسی در `docs/security-access-control.md` ثبت شده است.

بررسی لاگ، request id و مشاهده‌پذیری:

```powershell
docker compose --env-file .env -f docker/compose.yml exec -T api python -m scripts.verify_observability
```

راهنمای لاگ و مانیتورینگ در `docs/observability-logging.md` ثبت شده است.

ساخت migration جدید پس از تغییر مدل‌ها:

```powershell
docker compose -f docker/compose.yml exec api alembic revision --autogenerate -m "describe change"
```

اجرای worker در محیط دارای دسترسی رجیستری:

```powershell
docker compose -f docker/compose.yml --profile worker up --build -d
```

نصب مرورگرهای Crawl4AI داخل worker، در صورت نیاز:

```powershell
docker compose -f docker/compose.yml --profile worker run --rm worker crawl4ai-setup
```

## توقف

```powershell
docker compose -f docker/compose.yml down
```

برای حذف داده PostgreSQL نیز گزینه `-v` را به دستور توقف اضافه کنید.

## ساختار

- `backend/`: API و worker
- `frontend/`: SPA
- `docker/compose.yml`: سرویس‌های محیط محلی
- `docs/`: نیازمندی‌ها و معماری
- `storage/`: فایل‌های گزارش و داده موقت
- `history/pouriya.md`: تاریخچه اجرای پروژه
