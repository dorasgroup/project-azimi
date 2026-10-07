# فاز سوم بازطراحی خزش: تشخیص مطمئن تاریخ انتشار

## هدف

این فاز تشخیص تاریخ را از یک انتخاب رشته‌ای ساده به resolver چندمنبعی تبدیل می‌کند. نتیجه‌ی انتخاب
فقط زمانی برای فیلتر بازه استفاده می‌شود که تاریخ قابل parse، معقول و دارای اطمینان کافی باشد.

## جریان تشخیص

```text
JSON-LD / Meta / Time / RSS / Sitemap / URL / Labelled Text
                         |
                         v
                  DateCandidate[]
                         |
          parse + timezone + plausibility + confidence
                         |
                         v
              ResolvedPublicationDate
```

## منابع و اطمینان پایه

| منبع | اطمینان پایه |
| --- | ---: |
| `JSON-LD datePublished` | ۰٫۹۹ |
| `article:published_time` | ۰٫۹۶ |
| meta `datePublished` | ۰٫۹۳ |
| metaهای publish/pubdate | ۰٫۸۸ تا ۰٫۹۰ |
| RSS | ۰٫۸۸ |
| عنصر `time` دارای نشانه‌ی انتشار | ۰٫۸۸ |
| تاریخ URL | ۰٫۷۲ |
| تاریخ برچسب‌دار داخل متن | ۰٫۶۸ |
| sitemap `lastmod` | ۰٫۵۶ |
| `dateModified` | ۰٫۴۲ تا ۰٫۴۵ |

اگر بیش از یک منبع مستقل روی یک روز توافق کنند، confidence نتیجه تا ۰٫۰۲ افزایش می‌یابد.
`dateModified` به‌تنهایی برای اثبات تاریخ انتشار یک رصد بازه‌ای کافی نیست.

## فرمت‌های پشتیبانی‌شده

- ISO-8601 با یا بدون timezone؛
- تاریخ RFC مورد استفاده در RSS؛
- تاریخ عددی میلادی؛
- تاریخ عددی شمسی؛
- ارقام فارسی و عربی؛
- تاریخ شمسی دارای نام ماه، مانند `۹ اسفند ۱۴۰۴`؛
- نام ماه انگلیسی؛
- تاریخ موجود در مسیر URL؛
- تاریخ برچسب‌دار مانند `تاریخ انتشار:` یا `Published on:`.

تاریخ بدون timezone با timezone تهران تفسیر و سپس به UTC تبدیل می‌شود. مقایسه‌ی بازه با تاریخ محلی
تهران انجام می‌شود تا خبر نیمه‌شب به روز قبل منتقل نشود.

## قرارداد و persistence

فیلدهای زیر به شکل nullable و افزایشی اضافه شده‌اند:

- `published_at_source`
- `published_at_confidence`
- `published_at_raw`

migration: `c7e31f4a92b0_add_publication_date_provenance.py`

برای confidence یک check constraint بین صفر و یک وجود دارد. migration هیچ داده یا ستون قبلی را حذف
نمی‌کند. provenance علاوه بر ستون‌های دیتابیس، در metadata سند آماده نیز قرار می‌گیرد.

## سیاست پذیرش بازه

`ProcessingPolicy.min_publication_date_confidence` با مقدار پیش‌فرض `0.55` اضافه شده است.

- تاریخ مفقود در رصد بازه‌ای رد می‌شود.
- تاریخ با confidence کمتر از حد سیاست رد می‌شود.
- تاریخ معتبر قبل یا بعد از بازه با دلیل دقیق رد می‌شود.
- در رصد بدون بازه، تاریخ کم‌اطمینان می‌تواند برای مشاهده نگهداری شود ولی به‌عنوان اثبات بازه استفاده
  نمی‌شود.

## تست پذیرش

- تاریخ عددی شمسی با ارقام فارسی؛
- نام ماه شمسی؛
- اولویت `datePublished` بر `dateModified`؛
- توافق meta و time؛
- تاریخ URL و متن برچسب‌دار؛
- تفاوت confidence میان RSS و sitemap؛
- بازگشت provenance از extractor؛
- رد `dateModified` کم‌اطمینان برای بازه؛
- پذیرش تاریخ URL و تولید تاریخ شمسی صحیح؛
- repository و migration؛
- تست کامل backend و smoke test worker.

```powershell
docker compose --env-file .env -f docker/compose.yml exec -T worker `
  python -m scripts.verify_publication_dates
```
