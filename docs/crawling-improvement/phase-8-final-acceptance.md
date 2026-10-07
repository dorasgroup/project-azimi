# فاز هشتم — پذیرش نهایی مسیر خزش

## وضعیت

وضعیت اجرایی: **کامل و آماده تأیید کاربر**  
تاریخ اجرا: ۱۴۰۵/۰۵/۰۵ (2026-07-27)

## هدف

این فاز اجزای فازهای پیشین را در مرز نهایی selection و processing ممیزی می‌کند تا یک provider پرحجم نتواند بودجه خزش را اشغال کند و URLهای معتبر تاریخی یا چندمنبعی حذف نشوند.

## مرزبندی مسئولیت‌ها

- `CandidateRanker`: حذف تاریخ قطعی خارج بازه، امتیازدهی و تخصیص متنوع بودجه؛
- `CrawlOrchestrator`: هماهنگی probe، discovery، selection و fetch؛
- `CrawlDiagnosticsBuilder`: ثبت شواهد کشف، انتخاب و واکشی؛
- `ContentProcessor`: تأیید نهایی موضوع، تاریخ ناشر و کیفیت متن کامل.

هر جزء حداکثر یک یا دو مسئولیت مشخص دارد و قرارداد عمومی Worker یا API در این فاز تغییر نکرد.

## کنترل‌های پذیرش

- flood شامل ۳۰ URL از provider جست‌وجو نتوانست RSS، sitemap، archive و Common Crawl را از پنج جایگاه انتخاب حذف کند؛
- URL دارای تاریخ قطعی خارج بازه پیش از fetch حذف شد؛
- URL بدون تاریخ برای استخراج دقیق تاریخ ناشر پس از fetch حفظ شد؛
- اجرای دوباره روی ورودی یکسان، ترتیب یکسان تولید کرد؛
- موضوع canonical بدون بازنویسی باقی ماند؛
- trace رتبه‌بندی تعداد candidate، eligible و selected را ثبت کرد.

## آزمون‌ها

Verifier آفلاین `verify_phase8_final_acceptance.py` هیچ تماس شبکه، نوشتن دیتابیس یا فراخوانی legacy workflow engine ندارد. آزمون پذیرش زنده `verify_historical_monitoring_acceptance.py` نیز حفظ شده است، اما مطابق تصمیم کاربر تا انتقال پروژه به سرور اجرا نمی‌شود.

اجرای verifier زنده باید داخل container مربوط به Worker انجام شود، زیرا وابستگی `crawl4ai` عمداً در image API نصب نشده و مسئولیت خزش متعلق به Worker است.

نتیجه اجرای محلی:

- آزمون‌های هدفمند فاز: ۲۸ موفق، ۱ skipped؛
- suite کامل Backend: ۱۵۰ موفق، ۴ skipped؛
- Ruff: بدون خطا؛
- OpenAPI: ۲۴ مسیر و ۳۳ عملیات معتبر؛
- smoke در Worker: موفق و Celery پاسخ `pong` داد.

## بازگشت سریع

با تنظیم متغیر زیر و راه‌اندازی مجدد Worker، رتبه‌بندی هیبریدی غیرفعال و مسیر deterministic قدیمی فعال می‌شود:

```dotenv
CRAWL_HYBRID_RANKING_ENABLED=false
```

## خارج از دامنه

- اجرای رصد production جدید؛
- تغییر تنظیمات یا داده‌های منابع موجود؛
- تغییر legacy workflow engine یا report pipeline؛
- رفع محدودیت egress محیط محلی که برای استقرار سرور به تعویق افتاده است.
