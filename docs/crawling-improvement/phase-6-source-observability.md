# فاز ششم — diagnostics انتها‌به‌انتها برای هر منبع

## وضعیت

وضعیت اجرایی: **کامل و آماده تأیید کاربر**  
تاریخ اجرا: ۱۴۰۵/۰۵/۰۵ (2026-07-27)

## مسئله رفع‌شده

diagnostics قبلی فقط probe، discovery، selection و fetch را توصیف می‌کرد. بنابراین یک منبع می‌توانست از دید crawler با وضعیت `completed` پایان یابد، اما تمام صفحات آن هنگام پردازش محتوا به علت عدم تطابق موضوع، تاریخ نامعتبر یا متن ناکافی رد شوند. در این حالت علت «صفر سند نهایی» در یک خروجی واحد وجود نداشت.

## طراحی

### SourceExecutionDiagnoser

یک سرویس domain و بدون I/O اضافه شد که فقط دو کار انجام می‌دهد:

1. ترکیب facts خزش و آماده‌سازی سند؛
2. انتخاب یک `reason_code` نهایی همراه با شمارش شواهد و اقدام پیشنهادی.

این سرویس به FastAPI، SQLAlchemy، Celery، legacy workflow engine یا crawler وابسته نیست.

### snapshot مرحله‌ای

تابع `crawl_execution_trace` خروجی bounded دو مرحله را کنار هم قرار می‌دهد:

- `discovery`: profile، providerهای اجرا/skip‌شده، تعداد نتایج، query language و fallback؛
- `selection`: فعال‌بودن ranking هیبریدی، تعداد کاندید/واجد شرایط/منتخب، وزن‌ها و نتایج برتر.

### اتصال به monitoring

در همان system log موجود با پیام `Source crawled and prepared` دو کلید اضافه شد:

- `crawl_stage_trace`؛
- `source_diagnostics`.

هیچ جدول، migration، endpoint یا قرارداد عمومی worker تغییر نکرد.

## reason codeهای نهایی

| کد | مرحله غالب | اقدام اصلی |
|---|---|---|
| `probe_failed` | probe | DNS، TLS و egress |
| `robots_blocked` | access | RSS/API رسمی |
| `no_urls_discovered` | discovery | profile، RSS، sitemap و fallback تاریخی |
| `all_urls_outside_date_range` | selection | بازه و discovery تاریخی |
| `no_pages_fetched` | fetch | admission و سقف خزش |
| `all_fetches_failed` | fetch | HTTP، anti-bot، browser و egress |
| `no_topic_match` | processing | query چندزبانه و ranking |
| `date_out_of_range` | processing | تاریخ ناشر و بازه |
| `missing_publication_date` | processing | extractor تاریخ منبع |
| `extraction_failed` | processing | extractor و browser fallback |
| `duplicate_only` | dedupe | هم‌پوشانی اسناد؛ منبع لزوماً خراب نیست |
| `partial_content_success` | processing | بررسی علت‌های رد پرتکرار |
| `completed` | نهایی | بدون اقدام |

## شواهد ذخیره‌شده

- شمار URLهای کشف‌شده و منتخب؛
- شمار fetch کل و موفق؛
- شمار اسناد آماده، ردشده و تکراری؛
- شمار providerها پیش و پس از selection؛
- شمار علل رد استانداردشده؛
- علت crawl و علت نهایی؛
- خلاصه فارسی و `recommended_action`.

متن کامل صفحات، کلیدها و خروجی legacy workflow engine در diagnostic ذخیره نمی‌شود.

## پذیرش

- ۷ سناریوی قطعی به ۷ reason code صحیح نگاشت شدند؛
- تست‌های هدفمند: ۳۴ موفق؛
- suite کامل backend: ۱۴۱ موفق، ۴ skipped؛
- snapshot ترکیبی discovery/selection در کانتینر worker تأیید شد؛
- lint فایل‌های فاز: موفق؛
- قرارداد عمومی worker: بدون تغییر؛
- migration، تماس شبکه و فراخوانی legacy workflow engine در verifier: صفر.

## خارج از دامنه

- تغییر UI صفحه گزارش یا منابع؛
- ساخت dashboard متریک‌های تجمیعی؛
- تغییر وضعیت اتصال منبع بر اساس relevance یک موضوع خاص؛
- اجرای رصد production جدید؛
- ورود به فاز هفتم.
