# لاگ، مانیتورینگ و خطایابی

این سند در فاز چهارده پروژه «رصد و تحلیل» ایجاد شد و وضعیت فعلی مشاهده‌پذیری، لاگ ساختاریافته، request id و ثبت رویدادهای سیستم را توضیح می‌دهد.

## هدف فاز

هدف فاز چهارده این بود که سیستم در زمان اجرا قابل ردیابی و خطایابی باشد:

- هر درخواست API شناسه ردیابی داشته باشد.
- لاگ‌های API به صورت JSON روی stdout نوشته شوند.
- خطاهای مهم و رویدادهای job در جدول `system_logs` ذخیره شوند.
- مدیر کل بتواند لاگ‌های سیستم را از API ببیند.
- مسیر کامل job از crawl تا legacy workflow engine و تولید گزارش رویدادهای قابل پیگیری داشته باشد.

## تصمیم طراحی

برای نسخه فعلی از logging استاندارد Python استفاده شد. مستند رسمی Python logging امکان formatterهای سفارشی و خروجی ساختاریافته را پشتیبانی می‌کند و برای پروژه محلی بدون وابستگی اضافه مناسب است. برای FastAPI نیز الگوی middleware با request id به‌کار رفت تا requestها و خطاها قابل correlation باشند.

منابع بررسی‌شده:

- [Python Logging Cookbook](https://docs.python.org/3.10/howto/logging-cookbook.html)
- [Python logging library](https://docs.python.org/3.9/library/logging.html)

## اجزای پیاده‌سازی

- `app.core.logging`: پیکربندی logging ساختاریافته JSON و context متغیر request id.
- `app.api.middleware.RequestLoggingMiddleware`: تولید/خواندن `X-Request-ID`، ثبت زمان پاسخ، status و خطا.
- `app.modules.monitoring.infrastructure.log_operations.record_system_log`: ثبت رویدادهای مهم در جدول `system_logs` از طریق repository ماژول Monitoring.
- `app.modules.monitoring.infrastructure.system_log_read_model.SystemLogReadModel`: query مستقل و read-only لاگ‌های سیستم.
- `app.api.operations.observability.QueryOperations` و `app.api.routes.system_logs`: مشاهده کنترل‌شده لاگ‌ها برای مدیر کل.
- `app.modules.monitoring.infrastructure.pipeline_steps.MonitoringPipelineSteps`: ثبت شروع crawl، پردازش منبع، شروع legacy workflow engine، تکمیل job و خطاها.
- `scripts.verify_observability`: آزمون پذیرش request id، endpoint لاگ‌ها و کنترل دسترسی.

## خروجی لاگ API

نمونه خروجی stdout:

```json
{
  "timestamp": "2026-06-08T06:20:35.677439+00:00",
  "level": "info",
  "logger": "rasad.api",
  "message": "HTTP request completed",
  "request_id": "df0f4e52-1eaa-4118-bcc3-26341a96856d",
  "method": "GET",
  "path": "/api/v1/health",
  "status_code": 200,
  "duration_ms": 3.68
}
```

اگر درخواست ورودی header معتبر `X-Request-ID` از نوع UUID داشته باشد، همان مقدار در پاسخ و لاگ‌ها حفظ می‌شود. اگر مقدار header UUID معتبر نباشد، middleware برای سازگاری با ستون UUID دیتابیس یک شناسه جدید تولید می‌کند.

## مسیر مشاهده لاگ‌ها

```http
GET /api/v1/system/logs
```

این مسیر فقط برای `super_admin` در دسترس است و از صفحه‌بندی و فیلترهای زیر پشتیبانی می‌کند:

- `page`
- `page_size`
- `level`
- `module`

## رویدادهای job

worker رویدادهای زیر را در `system_logs` ثبت می‌کند:

- `Monitoring job crawling started`
- `Source crawled and prepared`
- `legacy workflow engine analysis started`
- `Monitoring job completed`
- `Monitoring job failed`

هر رویداد job شامل `job_id` و context مرتبط است.

## آزمون پذیرش

```powershell
docker compose --env-file .env -f docker/compose.yml exec -T api python -m scripts.verify_observability
```

نتیجه آخرین اجرا در تاریخ ۱۴۰۵/۰۳/۱۸:

- header `X-Request-ID` در پاسخ health برگشت داده شد.
- یک log قابل فیلتر در `system_logs` ثبت و از API خوانده شد.
- دسترسی ناشناس به `/system/logs` با `401` رد شد.

## بررسی‌های رگرسیون

پس از اجرای فاز چهارده، این بررسی‌ها با موفقیت اجرا شدند:

```powershell
docker compose --env-file .env -f docker/compose.yml exec -T api python -m scripts.verify_observability
docker compose --env-file .env -f docker/compose.yml exec -T api python -m scripts.verify_security
docker compose --env-file .env -f docker/compose.yml exec -T api python -m scripts.verify_openapi
docker compose --env-file .env -f docker/compose.yml --profile worker exec -T worker python -m scripts.verify_monitoring_flow
docker compose --env-file .env -f docker/compose.yml exec -T api python -m scripts.verify_database
docker compose --env-file .env -f docker/compose.yml exec -T api alembic check
docker compose --env-file .env -f docker/compose.yml exec -T api python -m compileall app scripts
```

## مرز فاز چهارده

در این فاز ابزار خارجی مانیتورینگ مانند Prometheus، Grafana، OpenTelemetry، Sentry یا ELK اضافه نشدند. زیرساخت فعلی برای MVP محلی کافی است و داده‌های کلیدی را در stdout و PostgreSQL نگه می‌دارد.
