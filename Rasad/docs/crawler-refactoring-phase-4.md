# فاز چهارم بازطراحی خزش: دریافت تاب‌آور و HTTP-first

## هدف

این فاز مسئولیت «پیدا کردن URL» را تغییر نمی‌دهد. ورودی آن URLهای کشف‌شده در فازهای قبل است و فقط تصمیم می‌گیرد هر صفحه چگونه، با چه محدودیت منابعی و با چه fallbackای دریافت شود.

## مسیر اجرا

1. URL، میزبان مجاز و `robots.txt` بررسی می‌شود.
2. صفحه ابتدا با HTTP سبک دریافت می‌شود.
3. نوع محتوا، کد وضعیت، اندازه پاسخ و مقدار متن قابل مشاهده ارزیابی می‌شود.
4. HTML معمولی مستقیماً پذیرفته و cache می‌شود.
5. shell جاوااسکریپتی یا درخواست صریح rendering به مرورگر Crawl4AI سپرده می‌شود.
6. خطاهای موقت با backoff محدود retry می‌شوند؛ خطاهای دائمی و دسترسی محدود با مرورگر دور زده نمی‌شوند.

## جداسازی مسئولیت‌ها

- `HttpPageFetcher`: یک تلاش HTTP و طبقه‌بندی نتیجه.
- `ResilientHttpFetcher`: retry، backoff، cache، محدودیت هم‌زمانی/نرخ در هر دامنه و circuit breaker.
- `BrowserPageRenderer`: فقط rendering صفحه‌های نیازمند JavaScript.
- `Crawl4AiPageFetcher`: هماهنگ‌کردن HTTP و browser و افزودن provenance کشف.
- `CrawlEngine`: facade سازگار با مصرف‌کنندگان قبلی.

هر جزء یک یا دو مسئولیت مشخص دارد و orchestration از جزئیات transport جدا مانده است.

## کنترل منابع و ایمنی

- concurrency و فاصله درخواست‌ها برای هر میزبان جداگانه اعمال می‌شود.
- تعداد retry و backoff سقف دارند.
- پس از چند خطای موقت متوالی، circuit همان میزبان موقتاً باز می‌شود.
- redirect نهایی دوباره از نظر SSRF و میزبان مجاز اعتبارسنجی می‌شود.
- پاسخ‌های `401` و `403` و منع `robots.txt` باعث browser bypass نمی‌شوند.
- metadata شامل strategy، attempts، response time، cache hit، circuit state و علت fallback است.

## معیار پذیرش

- HTML معمولی بدون browser دریافت شود.
- shell جاوااسکریپتی browser fallback بگیرد.
- خطای موقت retry شود و خطای دائمی retry بی‌دلیل نگیرد.
- cache درخواست تکراری را حذف کند.
- circuit breaker فشار روی میزبان خراب را متوقف کند.
- شکست یک URL مانع پردازش سایر URLهای batch نشود.

تست خودکار در `backend/tests/test_resilient_page_fetching.py` و smoke test واقعی در `backend/scripts/verify_resilient_crawler.py` قرار دارد.
