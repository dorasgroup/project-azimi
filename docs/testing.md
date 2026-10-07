# تست جامع پروژه

این سند خروجی فاز پانزده پروژه «رصد و تحلیل» است و روش آزمون تکرارپذیر نسخه فعلی را ثبت می‌کند.

## هدف فاز

هدف فاز پانزده این بود که همه خروجی‌های فازهای قبلی در یک مسیر آزمون قابل تکرار بررسی شوند:

- سلامت سرویس‌های Docker
- تست واحد بک‌اند
- اعتبارسنجی دیتابیس و migration
- قرارداد OpenAPI
- قرارداد Workflow در legacy workflow engine
- اتصال بک‌اند به legacy workflow engine
- تولید PDF و Word
- امنیت، نقش‌ها و مالکیت داده
- لاگ، request id و مشاهده‌پذیری
- خلاصه داشبورد حرفه‌ای و دسترسی مبتنی بر نقش
- ثبت taskهای worker
- lint و build فرانت‌اند
- خزش، رندر JavaScript، آماده‌سازی محتوا، performance و جریان کامل رصد
- smoke test رابط کاربری و API
- سناریوی کامل MVP از مسیر HTTP واقعی

## runner جامع

برای اجرای یکپارچه تست‌ها، اسکریپت زیر اضافه شد:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/verify_project.ps1
```

برای اجرای سریع‌تر و حذف آزمون‌های کندتر خزش و جریان کامل:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/verify_project.ps1 -SkipSlow
```

این runner از ریشه پروژه اجرا می‌شود و به `.env`، سرویس‌های Docker Compose و worker فعال نیاز دارد.

## آماده‌سازی تست

در فاز تست مشخص شد image فعلی API وابستگی‌های dev مثل `pytest` را نصب نمی‌کند. برای تکرارپذیر شدن تست واحد بک‌اند، نصب API در `backend/Dockerfile` از نصب runtime ساده به نصب `.[dev]` تغییر کرد.

پس از این تغییر، image API دوباره build و سرویس API recreate شد:

```powershell
docker compose --env-file .env -f docker/compose.yml build api
docker compose --env-file .env -f docker/compose.yml up -d --no-deps api
```

## نتایج آخرین اجرای کامل

آخرین اجرای کامل در تاریخ ۱۴۰۵/۰۳/۱۸ با موفقیت انجام شد:

| حوزه | نتیجه |
| --- | --- |
| سرویس‌ها | API، frontend، PostgreSQL، Redis و worker فعال بودند؛ API، PostgreSQL و Redis healthy بودند |
| `pytest` | `1 passed, 1 warning` |
| `compileall` | موفق |
| `ruff check app scripts tests` | موفق |
| `alembic check` | بدون migration جدید |
| `scripts.verify_database` | موفق |
| `scripts.verify_openapi` | `24 paths, 33 operations` |
| `scripts.verify_legacy-workflow-engine_workflow` | `5 nodes, 4 edges, 7 inputs` |
| `scripts.verify_legacy-workflow-engine_integration` | موفق، job و report ساخته شد |
| `scripts.verify_report_exports` | موفق، report export ساخته شد |
| `scripts.verify_security` | موفق، auth، refresh rotation، نقش‌ها و مالکیت داده تأیید شد |
| `scripts.verify_observability` | موفق، request id، endpoint لاگ و کنترل دسترسی تأیید شد |
| `scripts.verify_mvp` | موفق، ورود، منبع، هویت، job، گزارش و دانلود PDF/Word تأیید شد |
| `scripts.verify_professional_features` | موفق، داشبورد مدیریتی، شمارش‌ها، وضعیت jobها و موارد اخیر تأیید شد |
| worker tasks | taskهای `crawler`، `monitoring`، `processing` و `system.ping` ثبت بودند |
| frontend lint | موفق |
| frontend build | موفق، bundle تولید شد |
| `scripts.verify_crawler` | `3 links discovered, 3 pages crawled` |
| `scripts.verify_javascript_crawler` | موفق |
| `scripts.verify_content_processing` | `1 prepared, 1 duplicate, 1 rejected` |
| `scripts.verify_performance` | `robots_cached=true, report_exports_3x=0.60s` |
| `scripts.verify_monitoring_flow` | `status=completed, documents=2, reports=1` |
| `git diff --check` | بدون خطا |
| `npm audit --audit-level=moderate` | `0 vulnerabilities` |
| smoke test فرانت‌اند | `http://localhost:5173` با status `200` پاسخ داد |
| smoke test API | `http://localhost:8010/api/v1/health` با status `200` پاسخ داد |

هشدار pytest مربوط به وابستگی `reportlab` و استفاده داخلی آن از `ast.NameConstant` است و در کد پروژه ایجاد نشده است.

## مرز فاز پانزده

در این فاز تست مرورگری end-to-end با ابزارهایی مثل Playwright برای UI اضافه نشد. پس از فاز شانزده و هفده، فرانت‌اند به API واقعی و خلاصه داشبورد متصل است؛ بنابراین برای نسخه بعدی، افزودن تست E2E مرورگری برای مسیرهای login، ایجاد رصد، مشاهده گزارش، دانلود خروجی و مشاهده داشبورد توصیه می‌شود. در وضعیت فعلی، سناریوهای HTTP بک‌اند، smoke test، lint و build وضعیت پذیرش پروژه را پوشش می‌دهند.
