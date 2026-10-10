# فاز دوم — پروفایل اختصاصی منابع و مسیریابی راهبردی

## وضعیت

وضعیت اجرایی: **کامل و آماده تأیید کاربر**  
تاریخ اجرا: ۱۴۰۵/۰۵/۰۵ (2026-07-27)

فاز اول در وضعیت `software_complete_server_egress_deferred` باقی مانده است. آزمون نهایی egress محدودشده، طبق تصمیم کاربر، پس از انتقال پروژه به سرور انجام می‌شود و مانع فاز دوم نیست.

## مسئله حل‌شده

پیش از این، همه منابع با یک مجموعه seed و ترتیب تقریباً یکسان بررسی می‌شدند. در فاز دوم، انتخاب روش کشف از URL منبع جدا شده و به سه جزء محدود تقسیم شد:

1. `SourceCrawlProfile`: دانش ایستا درباره دامنه، alias، زبان، قابلیت‌ها و محدودیت منبع؛
2. `SourceProfileRegistry`: تطبیق دقیق hostname با پروفایل و fallback عمومی؛
3. `SourceStrategyRouter`: تبدیل پروفایل به policy مؤثر برای providerهای موجود.

`LinkDiscoveryService` همچنان فقط هماهنگ‌کننده providerها است، اما provider فاقد قابلیت را اجرا نمی‌کند و trace شامل attempted، skipped، provider counts، fallback و اقدام پیشنهادی تولید می‌کند.

## تصمیم ایمنی معماری

تحلیل اثر تغییر `CrawlDiagnostics`، ۴۵ وابستگی و ۳۴ مصرف‌کننده مستقیم را نشان داد. به همین دلیل schema قدیمی تغییر نکرد. `DiscoveryStrategyTrace` مستقل ساخته شد و با تابع `discovery_trace` و structured log در دسترس است. نقطه اتصال `LinkDiscoveryService` ریسک LOW داشت.

## اصلاح کاتالوگ

- پیش از تغییر، dump باینری و CSV از هر ۳۱ رکورد در `backups/phase-two/20260727-140654/` ساخته شد.
- `Python.org` به دلیل خبری‌نبودن غیرفعال شد؛ رکورد و روابط آن حذف نشد.
- seed استقرار نیز اصلاح شد تا Python.org را دوباره ایجاد/فعال نکند.
- URL رکورد `شرق` از Mashregh News به `https://www.sharghdaily.com/` اصلاح شد؛ شناسه و تاریخچه حفظ شد.
- نتیجه پس از تراکنش: ۳۱ رکورد، ۳۰ فعال، ۳۰ منبع فعال دارای پروفایل اختصاصی.

## قابلیت‌های پیاده‌شده

- تطبیق canonical host و alias بدون تطبیق مبهم substring؛
- fallback عمومی برای منبع ناشناخته؛
- راهبردهای RSS، sitemap، HTML، search، archive، GDELT و Common Crawl؛
- ثبت محدودیت محتوا، نیاز احتمالی به browser و نیاز به egress سرور؛
- ادغام profile seeds با تنظیمات صریح هر منبع؛
- حفظ URL admission، robots policy، SSRF guard و egress فاز اول؛
- capability detector محدود برای base URL، redirect، robots، RSS و sitemap؛
- fallback تاریخی فقط در بازه تاریخ‌دار و وقتی نتیجه محلی کمتر از آستانه است.

## آزمون پذیرش

- lint فایل‌های فاز دوم: موفق؛
- ۲۴ تست هدفمند: موفق؛
- smoke زنده محدود: ۴ از ۴ منبع پایه قابل دسترس؛
- ایرنا: RSS مشاهده شد؛
- شرق: sitemap مشاهده شد؛
- اکونومیست: صفحه اصلی 403، اما RSS معتبر 200؛
- گاردین: redirect سالم و RSS معتبر 200.

نتایج کامل در `artifacts/phase-2-source-audit.json` و `artifacts/phase-2-smoke-result.json` ثبت شده‌اند.

## خارج از دامنه

- تکمیل egress منابع محدود در محیط سرور (باقی‌مانده فاز اول)؛
- query planner و جست‌وجوی چندزبانه پیشرفته (فاز بعدی)؛
- اجرای یک رصد production کامل؛
- تغییر schema یا migration پایگاه داده.

## بازگردانی داده

در صورت نیاز، ابتدا وضعیت جاری export شود؛ سپس فایل `sources.dump` با `pg_restore` روی جدول `public.sources` بازیابی شود. CSV کنار dump برای ممیزی انسانی است. بازیابی نباید بدون تأیید و بدون بررسی روابط خارجی اجرا شود.
