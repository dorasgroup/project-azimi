# بهینه‌سازی سرعت

این سند خروجی فاز دوازده پروژه «رصد و تحلیل» است و تغییرات سرعت و آزمون‌های سنجش را ثبت می‌کند.

## هدف فاز

هدف فاز دوازده کاهش هزینه اجرای مسیر رصد بدون تغییر رفتار کاربر بود. تمرکز این فاز روی بهینه‌سازی‌های کم‌ریسک در بخش‌هایی بود که در فازهای قبلی واقعاً اجرا می‌شوند:

- خزش منابع با Crawl4AI
- تولید خروجی PDF و Word
- تنظیم‌پذیری timeout تحلیل legacy workflow engine
- آزمون قابل تکرار برای سنجش حداقل عملکرد

## تغییرات انجام‌شده

### cache برای robots.txt

پیش از این، هر صفحه خزیده‌شده می‌توانست دوباره `robots.txt` همان host را درخواست و parse کند. در `CrawlEngine` یک cache در سطح اجرای engine اضافه شد تا برای هر origin فقط یک بار `robots.txt` خوانده شود.

اثر مورد انتظار:

- کاهش درخواست‌های شبکه تکراری
- کاهش زمان crawl برای چند صفحه از یک منبع
- کاهش فشار غیرضروری روی منبع خبری

### cache ثبت فونت PDF

ثبت فونت DejaVu در ReportLab برای هر PDF تکرار می‌شد. در تولیدکننده گزارش، نام فونت ثبت‌شده cache شد تا در تولیدهای پشت‌سرهم تکرار نشود.

اثر مورد انتظار:

- کاهش overhead تولید فایل‌های PDF
- رفتار پایدارتر در تولید چند گزارش پشت‌سرهم

### timeout قابل تنظیم legacy workflow engine

تنظیم `LEGACY_WORKFLOW_ENGINE_TIMEOUT_SECONDS` به تنظیمات پروژه اضافه شد تا زمان انتظار فراخوانی legacy workflow engine بدون تغییر کد قابل کنترل باشد.

نمونه:

```env
LEGACY_WORKFLOW_ENGINE_TIMEOUT_SECONDS=120
```

## آزمون پذیرش

تست فاز دوازده در worker اجرا می‌شود، چون بخش cache خزش به Crawl4AI وابسته است:

```powershell
docker compose --env-file .env -f docker/compose.yml --profile worker exec -T worker python -m scripts.verify_performance
```

نتیجه آخرین اجرا در تاریخ ۱۴۰۵/۰۳/۱۷:

- `robots.txt` برای دو URL از یک origin فقط یک بار خوانده شد.
- تولید سه‌باره PDF و DOCX گزارش نمونه در حدود `0.62s` انجام شد.

## بررسی‌های رگرسیون

پس از تغییرات، این بررسی‌ها با موفقیت اجرا شدند:

```powershell
docker compose --env-file .env -f docker/compose.yml exec -T api python -m scripts.verify_report_exports
docker compose --env-file .env -f docker/compose.yml exec -T api python -m scripts.verify_openapi
docker compose --env-file .env -f docker/compose.yml --profile worker exec -T worker python -m scripts.verify_monitoring_flow
docker compose --env-file .env -f docker/compose.yml exec -T api python -m scripts.verify_database
docker compose --env-file .env -f docker/compose.yml exec -T api alembic check
docker compose --env-file .env -f docker/compose.yml exec -T api python -m compileall app scripts
```

با کلید legacy workflow engine تنظیم‌شده، جریان واقعی رصد دوباره تا وضعیت `completed` پیش رفت و یک گزارش ذخیره کرد.

## مرز فاز دوازده

در این فاز تغییرات پرریسک مانند موازی‌سازی چند منبع داخل یک job، shard کردن workerها، cache پایدار در Redis یا تغییر اساسی در قرارداد legacy workflow engine انجام نشدند. این موارد در صورت نیاز می‌توانند در فازهای تست، MVP یا نسخه حرفه‌ای‌تر بررسی شوند.
