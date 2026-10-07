# تولید خروجی PDF و Word

این سند در فاز یازده پروژه «رصد و تحلیل» ایجاد شد و روش فعلی تولید فایل‌های قابل دانلود گزارش را در ماژول `reports` توضیح می‌دهد.

## هدف فاز

هدف فاز یازده تبدیل گزارش JSON ذخیره‌شده در `analysis_reports` به دو خروجی قابل استفاده بود:

- فایل PDF فارسی و راست‌چین برای مطالعه و آرشیو.
- فایل Word/DOCX فارسی و راست‌چین برای ویرایش و بازنشر.

## اجزای پیاده‌سازی فعلی

- `app.modules.reports.application.GenerateReportExport`: use case تولید و ذخیره یک artifact بدون وابستگی به renderer یا filesystem مشخص.
- `app.modules.reports.infrastructure.LegacyReportRenderer`: adapter تولید PDF و DOCX با generator تثبیت‌شده پروژه.
- `app.modules.reports.infrastructure.LocalReportFileStorage`: ذخیره و حذف امن artifactها زیر مسیر گزارش‌ها.
- `app.modules.reports.infrastructure.ReportExportBatchAdapter`: تولید اولیه هر دو فرمت پس از تحلیل legacy workflow engine.
- `app.modules.reports.infrastructure.operations.ReportCommandAdapter.export_path`: بازتولید lazy فایل در صورت حذف یا نبود فایل.
- `app.api.operations.reports.ReportOperations`: کنترل دسترسی، transaction و اتصال route دانلود به adapter گزارش.
- `GET /reports/{report_id}/download/pdf`: دانلود PDF.
- `GET /reports/{report_id}/download/docx`: دانلود Word.
- `scripts.verify_report_exports`: آزمون پذیرش تولید و بازتولید فایل‌ها.

## محل ذخیره فایل‌ها

فایل‌ها در volume محلی پروژه ذخیره می‌شوند:

```text
/storage/reports/<report-id>-<report-title>.pdf
/storage/reports/<report-id>-<report-title>.docx
```

در محیط میزبان این مسیر به پوشه `storage/reports` پروژه متصل است.

## وابستگی‌ها

- `python-docx`: تولید فایل Word.
- `reportlab`: تولید PDF.
- `arabic-reshaper` و `python-bidi`: شکل‌دهی و نمایش بهتر متن فارسی در PDF.
- `fonts-dejavu-core`: فونت DejaVu Sans داخل imageهای API و worker.

## قالب خروجی

هر دو خروجی از داده ساختاریافته مشترک استفاده می‌کنند:

- عنوان گزارش
- خلاصه مدیریتی
- دامنه رصد
- یافته‌های کلیدی
- تحلیل شناختی
- روایت‌ها
- بازیگران
- الگوهای رسانه‌ای
- نشانه‌های احتمالی عملیات روانی
- جمع‌بندی
- پیشنهادهای پژوهشی
- جدول منابع

## رفتار بازتولید

اگر مسیر فایل در دیتابیس وجود داشته باشد ولی فایل از storage حذف شده باشد، route دانلود فایل را دوباره از JSON ذخیره‌شده گزارش تولید می‌کند و مسیر جدید را در دیتابیس نگه می‌دارد.

## آزمون پذیرش

```powershell
docker compose --env-file .env -f docker/compose.yml exec -T api python -m scripts.verify_report_exports
```

نتیجه آخرین اجرا در تاریخ ۱۴۰۵/۰۳/۱۷:

- گزارش نمونه در دیتابیس موقت ساخته شد.
- PDF تولید شد و header معتبر `%PDF` داشت.
- DOCX تولید شد و ساختار zip/openxml آن معتبر بود.
- فایل‌ها حذف شدند و از طریق operation و use case گزارش دوباره تولید شدند.
- داده‌های آزمون rollback و فایل‌های آزمایشی پاک شدند.

## محدودیت فعلی

در نسخه فعلی، PDF با `reportlab` تولید می‌شود نه HTML/CSS و Playwright. این انتخاب برای داشتن خروجی پایدار و سبک در همین فاز انجام شد. کیفیت صفحه‌آرایی فارسی قابل قبول است، اما طراحی چاپی پیشرفته‌تر، header/footer سفارشی، شماره صفحه و قالب سازمانی می‌تواند در فازهای بهینه‌سازی یا نسخه حرفه‌ای‌تر تکمیل شود.
