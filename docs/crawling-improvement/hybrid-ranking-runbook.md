# راهنمای عملیاتی رتبه‌بندی هیبریدی

## اجرای آزمون پذیرش

```powershell
docker compose -f docker/compose.yml exec -T api pytest -q tests/test_hybrid_candidate_ranking.py tests/test_candidate_ranking.py tests/test_crawler_orchestrator.py tests/test_crawler_component_boundaries.py
docker compose -f docker/compose.yml exec -T api python scripts/verify_phase4_hybrid_ranking.py
```

اسکریپت دوم داده‌ای را در پایگاه داده تغییر نمی‌دهد و هیچ درخواست شبکه یا مدل ایجاد نمی‌کند.

## مشاهده علت رتبه

در logهای worker رویداد `crawl_candidate_ranking` را پیدا کنید. برای هر کاندید برتر، فیلد `score` تفکیک lexical، semantic، evidence، temporal، structure و total را نشان می‌دهد.

| نشانه | بررسی |
|---|---|
| نتیجه مرتبط رتبه پایین دارد | عنوان و URL کاندید و `retrieval_queries` فاز سوم را بررسی کنید |
| یک provider غالب شده است | `candidate_provider_slots` و providerهای trace را بررسی کنید |
| نتیجه خارج از بازه انتخاب شده است | `date_evidence` و `published_at` را بررسی کنید؛ تاریخ قطعی خارج از بازه باید حذف شود |
| واژه‌های عمومی امتیاز بالا گرفته‌اند | پوشش مفاهیم و BM25 را کنار هم بررسی کنید؛ تغییر وزن فقط با corpus برچسب‌خورده انجام شود |
| تأخیر انتخاب بالا رفته است | benchmark ۵۰۰۰ کاندید را اجرا و با artifact فاز مقایسه کنید |

## بازگشت فوری

در فایل env سرویس worker مقدار زیر را تنظیم کنید:

```dotenv
CRAWL_HYBRID_RANKING_ENABLED=false
```

سپس فقط worker را بازراه‌اندازی کنید. مسیر legacy بدون نیاز به migration یا تغییر داده فعال می‌شود. برای فعال‌سازی مجدد مقدار را `true` کنید.

## قواعد تنظیم وزن

1. وزن‌ها فقط با fixture برچسب‌خورده و ثبت Precision/Recall/MRR تغییر کنند.
2. موضوع canonical، متن سند و prompt تحلیل نباید برای بهبود ranking تغییر کنند.
3. provider weight جای relevance را نمی‌گیرد؛ فقط بخشی از evidence است.
4. سرویس رتبه‌بندی محلی باقی بماند؛ افزودن embedding شبکه‌ای نیازمند فاز و بودجه تأخیر جداگانه است.

