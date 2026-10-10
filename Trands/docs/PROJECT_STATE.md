# وضعیت پروژه

## هدف کلی

سامانه‌ای برای رصد محتوای ترند، تحلیل آن و ارسال خروجی در فازهای بعدی.

## معماری انتخاب‌شده

Django monolith با Django Templates فارسی و RTL، PostgreSQL، Redis، Celery و Celery Beat؛ استقرار با Docker Compose.

## فازها

1. زیرساخت اولیه
2. مدل داده و داشبورد مدیریتی
3. هسته جمع‌آوری و تلگرام
4. اتصال X
5. موتور تشخیص ترند
6. اتصال ربات بله
7. زمان‌بندی، امنیت و استقرار

## تصمیم‌های قطعی

- منطق کسب‌وکار در serviceها قرار می‌گیرد.
- اجرای دستی و زمان‌بندی‌شده منطق مشترک دارند.
- عملیات جمع‌آوری و ارسال idempotent هستند.
- secretها فقط از محیط خوانده می‌شوند و متن ساده ذخیره یا ثبت نمی‌شوند.
- قابلیت‌های خارج از فاز جاری پیشاپیش ساخته نمی‌شوند.
- جمع‌آوری Telegram فقط از صفحهٔ عمومی `https://t.me/s/<channel>` یا `https://telegram.me/s/<channel>` و به‌شکل best-effort انجام می‌شود؛ هیچ credential تلگرام در این معماری وجود ندارد.

## بازطراحی رابط عملیات

- مسیر انسانی یکتا `/` است: میهمان صفحه ورود فارسی، کاربر staff داشبورد و کاربر غیرstaff صفحه ۴۰۳ را می‌بیند. `/monitoring/` برای سازگاری به `/` هدایت می‌شود.
- پوستهٔ همهٔ صفحه‌های اپراتور به طراحی مرکز عملیات شبانه با ناوبری راست‌چین، نشان رادار CSS، رنگ‌های سرمه‌ای/طلایی/سبزآبی و حالت موبایل بازطراحی شد.
- اعداد ساختگی dashboard حذف شدند؛ شاخص‌ها و وضعیت رصد خودکار فقط دادهٔ واقعی یا حالت خالی صادقانه را نمایش می‌دهند.
- stylesheet و تعامل‌های رابط به assetهای محلی متکی‌اند؛ Bootstrap و HTMX CDN از صفحه ورود و پوسته اپراتور حذف شدند.
- اعتبارسنجی UI: `pytest -q` با ۱۴۵ تست موفق و یک هشدار غیرمسدودکننده URLField Django 6، `manage.py check` بدون خطا و `makemigrations --check --dry-run` بدون تغییر انجام شد.

## کارهای انجام‌شده در فاز ۱

- اسکلت Django، تنظیمات محیطی و ورود مدیر ایجاد شد.
- PostgreSQL، Redis، Celery worker و Beat در Compose پیکربندی شدند.
- صفحه اصلی فارسی و RTL و endpoint `/health/` ایجاد شد.
- تست‌های تنظیمات، صفحه اصلی، سلامت و اتصال پایگاه داده افزوده شد.

## کارهای انجام‌شده در فاز ۲

- app دامنه‌ای `apps.monitoring` با مدل‌های Topic، Keyword، SourceGroup، Source، CollectionRun، SystemSetting و AuditLog ایجاد شد.
- یکتایی کلیدواژه در هر موضوع/نوع تطبیق و یکتایی شناسه نرمال‌شده Source در هر پلتفرم اعمال شد.
- داشبورد staff-only در `/monitoring/` و CRUD فارسی/RTL برای موضوع‌ها، کلیدواژه‌ها، گروه‌های منابع، منابع و تنظیمات ایجاد شد.
- فهرست کلیدواژه‌ها و منابع جست‌وجو، فیلتر پایه و صفحه‌بندی دارند؛ حذف Topic و SourceGroup دارای وابستگی با پیام فارسی مدیریت می‌شود.
- ثبت AuditLog برای ایجاد و ویرایش Topic، Keyword، SourceGroup و Source فقط از طریق service و با metadata حداقلی انجام می‌شود.
- مدل‌ها در Django Admin ثبت شدند و migrationهای `apps/monitoring/migrations/0001_initial.py` و `0002_systemsetting_singleton.py` ایجاد شدند.
- اعتبارسنجی محلی فاز ۲: `pytest -q` با ۱۱ تست موفق (یک هشدار غیرمسدودکننده URLField برای Django 6)، `makemigrations --check --dry-run` بدون تغییر، و `django check` بدون خطا انجام شد.

## کارهای انجام‌شده در فاز ۳

- مدل‌های ContentItem، CollectionCursor و CollectionRunSource و migration `0003_collectioncursor_collectionrunsource_contentitem.py` ایجاد شدند.
- هسته مشترک collection service، قرارداد BaseCollector و TelegramCollector مبتنی بر crawl صفحه عمومی `t.me/s/<channel>` پیاده‌سازی شد؛ هیچ credential Telegram استفاده نمی‌شود.
- ذخیره پیام‌ها idempotent است، cursor هر منبع فقط پس از موفقیت جلو می‌رود و خطای یک منبع سایر منابع را متوقف نمی‌کند.
- task Celery برای اجرای CollectionRun و مسیرهای staff-only `/monitoring/runs/`، `/monitoring/runs/<id>/` و POST `/monitoring/runs/telegram-now/` ایجاد شدند.
- Bot API، BOT_TOKEN، api_id، api_hash، Telethon، Pyrogram، شماره تلفن، OTP و session شخصی حذف شدند. URL عمومی کانال ورودی crawler است.
- ContentItem با media_url و فیلدهای آماده آینده ai_status، category و importance_score توسعه داده شد؛ فهرست `/monitoring/posts/` جست‌وجو و فیلتر پست‌ها را ارائه می‌کند.
- migration `0005_delete_botupdatestate_contentitem_ai_status_and_more.py` BotUpdateState را حذف و فیلدهای عمومی crawler را اضافه می‌کند. اعتبارسنجی پس از این تغییر: ۱۵ تست موفق، migration check بدون تغییر و Django check بدون خطا.
- آزمون واقعی crawler روی صفحه عمومی `t.me/s/akhbarefori` در ۱ سپتامبر ۲۰۲۶، ۱۴ پست را با موفقیت ذخیره کرد. migration `0006_alter_contentitem_media_url.py` ظرفیت media_url را برای URLهای بلند رسانه Telegram به ۱۰۰۰ کاراکتر افزایش داد؛ اجرای دوم موفق، ۱۴ پست جدید ثبت کرد.
- اصلاح کیفیت crawler با migration `0007_crawler_quality.py` انجام شد: `Source.discovered_name` نام کشف‌شدهٔ کانال را جدا از `display_name` دستی نگه می‌دارد؛ `ContentItem.normalized_text` برای جست‌وجوی فارسی است و `raw_text` بدون تغییر نمایش داده می‌شود؛ و `CollectionRunSource.parse_error_count`/وضعیت `partial` خطای تک‌پست را ثبت می‌کنند.
- قرارداد Telegram برای Source فقط URL دقیق HTTPS با `/s/<channel>` است؛ identifier به‌شکل خودکار از URL استخراج می‌شود. لینک خصوصی، گروه، دعوت، HTTP و URLهای دارای بخش اضافی خارج از دامنه هستند.
- parser بهترین تلاش را انجام می‌دهد: پست ناقص را skip می‌کند، سایر پست‌ها و Sourceها را ادامه می‌دهد و cursor را پس از نخستین خطای موقعیت‌دار جلو نمی‌برد تا تلاش بعدی ممکن باشد. URL رسانه فقط با scheme عمومی HTTP/HTTPS ذخیره می‌شود.
- تست‌های crawler فقط fixture محلی دارند و درخواست شبکهٔ زنده در suite خودکار ندارند. دستور رسمی تست `pytest` است؛ `python manage.py test` به‌سبب قرارگیری تست‌ها در پوشهٔ سطح‌بالای `tests/` suite را کشف نمی‌کند.
- اعتبارسنجی نهایی اصلاح کیفیت: migration `0007` روی پایگاه‌داده محلی با موفقیت اعمال شد؛ `pytest -q` با ۳۲ تست موفق و یک هشدار غیرمسدودکنندهٔ Django 6 دربارهٔ URLField، `makemigrations --check --dry-run` بدون تغییر و `manage.py check` بدون خطا انجام شدند.

## کارهای انجام‌شده در فاز ۴

- collector رسمی `XRecentSearchCollector` با HTTPX و endpoint `GET /2/tweets/search/recent` افزوده شد. فقط API رسمی X، حساب‌های ثبت‌شده و پست‌های اخیر اصلی با query `from:<handle> -is:retweet -is:reply` پشتیبانی می‌شوند.
- token فقط از `X_BEARER_TOKEN` محیط خوانده می‌شود. تنظیمات base URL، lookback اولیه (حداکثر ۱۶۸ ساعت)، سقف پست هر Source (حداکثر ۱۰۰) و timeout نیز محیطی هستند؛ هیچ credential در مدل، migration، log یا UI ذخیره نمی‌شود.
- Source X handle یا URL عمومی `x.com`/`twitter.com` را canonical می‌کند و URLهای status، search، i، HTTP، query، fragment و مسیرهای غیرحساب را رد می‌کند.
- service مشترک اکنون collector را بر اساس platform انتخاب می‌کند. `ContentItem` با همان constraint یکتا برای X ذخیره می‌شود؛ پست تکراری بدون ایجاد رکورد تازه، در صورت تغییر معتبر متن یا metadata به‌روزرسانی می‌شود.
- migration `0008_x_collection.py` شمارنده‌های محتوای به‌روزشده را به CollectionRun و CollectionRunSource افزود. UI اجرای دستی `POST /monitoring/runs/x-now/`، نمایش platform/updated در run detail و فیلتر platform در `/monitoring/posts/` را دریافت کرد.
- collector برای 429 درخواست اضافی نمی‌فرستد و زمان reset را در صورت وجود به‌صورت امن گزارش می‌دهد. 401/403، شبکه و 5xx پیام فارسی امن می‌گیرند و cursor فقط پس از پردازش موفق صفحات جلو می‌رود.
- تست‌های X فقط از Fake HTTP client استفاده می‌کنند و شبکهٔ واقعی ندارند.
- اعتبارسنجی نهایی فاز ۴: migration `0008` روی پایگاه‌داده محلی با موفقیت اعمال شد؛ `pytest -q` با ۵۱ تست موفق و یک هشدار غیرمسدودکنندهٔ Django 6 دربارهٔ URLField، `makemigrations --check --dry-run` بدون تغییر و `manage.py check` بدون خطا انجام شدند.

## کارهای انجام‌شده در فاز ۵

- مدل‌های KeywordMatch، TrendAnalysisRun، TrendCluster و ClusterItem و migration `0009_trend_engine.py` افزوده شدند؛ تنظیمات شباهت، حداقل متن، وزن‌های امتیاز و آستانه حساس به SystemSetting اضافه شد.
- تحلیل دستی و task Celery مشترک با الگوریتم محلی و قابل توضیح افزوده شد: matching کلیدواژه، fingerprint، cosine character n-gram، URL/هشتگ مشترک، منابع مستقل بر اساس SourceGroup و امتیاز محدودشدهٔ engagement.
- مسیرهای `/monitoring/trends/`، `/monitoring/trends/runs/` و POST `/monitoring/trends/analyze-now/` برای مشاهده و اجرای تحلیل افزوده شدند. هیچ AI، زمان‌بندی یا ارسال پیام پیاده‌سازی نشده است.
- اعتبارسنجی نهایی: migration `0009` اعمال شد؛ `pytest -q` با ۵۴ تست موفق و یک هشدار غیرمسدودکننده، migration check بدون تغییر و Django check بدون خطا بودند.
- اصلاح کیفیت فاز ۵: وضعیت‌های انسانی confirmed، dismissed و suppressed در اجرای خودکار تحلیل حفظ می‌شوند؛ فقط candidate و trending بدون عضو در پنجره تحلیل resolved می‌شوند. هشتگ مشترک به‌تنهایی اتصال ایجاد نمی‌کند و باید همراه شباهت متنی حداقلی باشد؛ URL عمومی مشترک معتبر همچنان سیگنال کافی است. KeywordMatchها پس از تغییر متن یا وضعیت Keyword reconciliation می‌شوند.
- reconciliation ادغام فاز ۵ قطعی و قابل رهگیری است: وقتی یک محتوای bridge دو یا چند خوشهٔ ماشینی `candidate`/`trending` از یک Topic را متصل کند، ادغام در تراکنش انجام می‌شود، قدیمی‌ترین خوشه بر اساس `created_at` (و سپس شناسه) باقی می‌ماند و خوشه‌های دیگر بدون حذف فیزیکی `resolved` شده و با `merged_into` به خوشهٔ اصلی اشاره می‌کنند. عضوها به‌شکل idempotent به خوشهٔ اصلی متصل و تمام شاخص‌های آن دوباره محاسبه می‌شوند. خوشه‌های انسانی `confirmed`، `dismissed` و `suppressed` و نیز Topicهای متفاوت هرگز در این ادغام خودکار شرکت نمی‌کنند.
- اعتبارسنجی اصلاح ادغام: migration `0010_trend_cluster_merge` در تنظیمات تست با موفقیت اعمال شد؛ `pytest -q` با ۶۳ تست موفق و یک هشدار غیرمسدودکنندهٔ URLField، migration check بدون تغییر و Django check بدون خطا انجام شدند. اجرای migration توسعه تا راه‌اندازی PostgreSQL محلی منتظر مانده است.
- اعتبارسنجی اصلاح کیفیت: `pytest -q` با ۵۸ تست موفق و یک هشدار غیرمسدودکننده، migration check بدون تغییر و Django check بدون خطا انجام شدند.

## کارهای انجام‌شده در فاز ۶

- اتصال بله فقط با Bale Bot API و client مستقل backend انجام می‌شود. token تنها از `BALE_BOT_TOKEN` محیط خوانده می‌شود و در مدل، migration، فرم، log، AuditLog، error summary یا گزارش قرار نمی‌گیرد.
- مدل‌های `BaleDestination` و `Delivery` با migration `0011_bale_destination_delivery.py` افزوده شدند. Delivery روی `(destination, trend_cluster, report_version)` یکتا است؛ تحویل موفق دوباره ارسال نمی‌شود و ارسال مجدد فقط با action صریح staff و نسخهٔ جدید گزارش ایجاد می‌شود.
- service انتخاب واجد ارسال فقط مقصد فعال و Clusterهای `trending` یا `confirmed` را بدون Delivery موفق نسخهٔ ۱ برمی‌گرداند؛ `suppressed`، `dismissed` و `resolved` واجد ارسال نیستند.
- formatter محلی گزارش فارسی، خلاصهٔ کوتاه و امن، حداکثر پنج کلیدواژه و منبع و لینک اختیاری جزئیات را تولید می‌کند. ارسال اولیه فقط متن است.
- مسیرهای staff-only برای مقصدها و تحویل‌ها و actionهای دستی ارسال، retry، ارسال مجدد، بررسی اتصال و پیام آزمایشی افزوده شدند. command محدود `bale_chat_ids` فقط `chat_id`، نوع و نام محدود chat را از `getUpdates` نمایش می‌دهد و متن پیام کاربران را نه نمایش می‌دهد و نه ذخیره می‌کند. هیچ webhook، polling دائمی یا زمان‌بندی خودکار اضافه نشده است.
- تست‌های بله فقط mock HTTP transport دارند و هیچ درخواست واقعی به بله اجرا نمی‌کنند. اعتبارسنجی فاز ۶: `pytest -q` با ۷۲ تست موفق و یک هشدار غیرمسدودکنندهٔ URLField، migration check بدون تغییر، Django check بدون خطا و اعمال migration `0011_bale_destination_delivery` در محیط تست موفق بود.

## کارهای انجام‌شده در فاز ۷

- `SystemSetting` اکنون فعال/غیرفعال‌بودن و فاصلهٔ automation را نگه می‌دارد؛ `AutomationState` قفل زمان‌دار و آخرین اجرا و `AutomationRun` trigger، stageها، runهای مرتبط و آمار تحویل را ثبت می‌کنند. migration `0012_automation.py` این ساختار را اضافه می‌کند.
- Celery Beat فقط dispatcher سبک هر دقیقه و پاک‌سازی روزانه را اجرا می‌کند. dispatcher تنظیمات database را می‌خواند و فقط در موعد، pipeline مشترک collection Telegram، collection X، تحلیل ترند و ایجاد Delivery نسخهٔ ۱ را اجرا می‌کند.
- قفل database با زمان انقضا، اجرای هم‌زمان pipelineهای دستی و زمان‌بندی‌شده را به run `skipped` تبدیل می‌کند؛ Deliveryهای موجود با هر وضعیت، از ایجاد نسخهٔ ۱ تکراری در automation جلوگیری می‌کنند.
- dashboard وضعیت automation، آخرین اجرا، تاریخچه و actionهای staff-only اجرای کامل و فعال/غیرفعال را نشان می‌دهد. پاک‌سازی روزانه فقط ContentItemهای قدیمی و بی‌وابستگی مهم را batch-based حذف می‌کند؛ command `cleanup_retention --dry-run` فقط شمارش می‌کند.
- hardening production برای CSRF trusted origins، HTTPS proxy، cookie امن مشروط و headerهای امنیتی اضافه شد. سند [OPERATIONS.md](OPERATIONS.md) راه‌اندازی، backup/restore، retention، rollback و خطاهای متداول را ثبت می‌کند.
- اعتبارسنجی فاز ۷: `pytest -q` با ۷۸ تست موفق و یک هشدار غیرمسدودکنندهٔ URLField، migration check بدون تغییر، Django check بدون خطا و اعمال migration `0012_automation` در محیط تست موفق بود. `docker compose config --quiet` موفق شد اما build به مانع Docker Desktop ثبت‌شده رسید.
- اصلاح نهایی ایمنی automation: `AutomationState.lock_owner` و `AutomationRun.lease_owner` با migration `0013_automation_lease_owner.py` اضافه شدند. قفل اکنون lease-based، مالک‌دار و قابل بازیابی پس از expiry است؛ تمدید و آزادسازی فقط با مالک فعلی ممکن است و pipeline پیش از هر stage مالکیت را بررسی می‌کند. اجرای قدیمی قادر به overwrite یا آزادکردن قفل مالک جدید نیست.
- اعتبارسنجی اصلاح lease: `pytest -q` با ۸۵ تست موفق و یک هشدار غیرمسدودکنندهٔ URLField، migration check بدون تغییر، Django check بدون خطا و اعمال migration `0013` در محیط تست موفق بود. مانع Docker Desktop همچنان بدون تغییر باقی مانده است.

## بازطراحی رابط اپراتوری

- بازطراحی UI/UX اپراتوری انجام شد: `/` ورودی یگانه است، مهمان login اختصاصی، staff داشبورد واقعی و non-staff پاسخ 403 فارسی می‌بیند؛ `/monitoring/` برای سازگاری به `/` هدایت می‌شود و `/admin/` فقط fallback فنی بدون پیوند در UI است.
- design system رابط با پوسته navy، سطح محتوای روشن، accent نارنجی، تایپوگرافی fallback محلی، sidebar واکنش‌گرا، focus قابل مشاهده، فرم‌ها و جدول‌های یکدست در `static/css/app.css` ثبت شده است. تعامل‌های محدود موبایل، نمایش گذرواژه و جلوگیری از submit تکراری login در `static/js/app.js` قرار دارند.
- داشبورد KPIهای واقعی موضوع، کلیدواژه، Source، ترند، حساسیت و Delivery و آخرین اجراهای عملیاتی را با queryهای فقط‌خواندنی نشان می‌دهد. ۱۴ سناریوی ساختاری احراز هویت و UI به suite افزوده شد.
- هیچ تصویر raster یا وابستگی frontend جدیدی اضافه نشد؛ motif صفحه login عمداً CSS-only است. اعتبارسنجی بصری روی مرورگرها و دستگاه‌های واقعی پس از رفع مانع محیط اجرای Docker همچنان توصیه می‌شود.
- اعتبارسنجی بازطراحی: `pytest -q` با ۹۹ تست موفق و یک هشدار غیرمسدودکننده URLField، `manage.py check` بدون خطا و migration check بدون تغییر انجام شد. مانع Docker Desktop رفع‌شده تلقی نمی‌شود.
- داشبورد اکنون دو حالت روشن دارد: «رصد ۱ تا ۷ روز گذشته» با انتخاب دستی منابع، و «رصد خودکار» با انتخاب منابع، فعال/غیرفعال‌سازی و فاصلهٔ ساعتی. اجرای بازه‌ای فقط collection است، بازه و منابعش ثبت می‌شوند و cursor عادی را جلو نمی‌برد. انتخاب خودکار هنگام ایجاد AutomationRun snapshot می‌شود؛ تا نخستین ذخیرهٔ انتخاب منابع، رفتار سازگار قبلی یعنی همهٔ منابع فعال حفظ می‌شود. پنل «آخرین فعالیت‌ها» از داشبورد حذف شده است.
- هر دو حالت رصد اکنون کنترل‌های جداگانهٔ «منابع» و «موضوعات» با modal محلی، RTL و قابل استفاده با keyboard دارند. در رصد ۱ تا ۷ روز گذشته، موضوع‌های انتخابی در `CollectionRun.selected_topics` snapshot می‌شوند و فقط بخشی از سابقهٔ اجرا هستند؛ این حالت همچنان تحلیل انجام نمی‌دهد. در رصد خودکار، موضوع‌ها در `SystemSetting` ذخیره و در `AutomationRun.selected_topics` snapshot می‌شوند و تحلیل مشترک pipeline فقط همان scope را دریافت می‌کند. تا نخستین ذخیرهٔ موضوع‌ها، رفتار سازگار قبلی یعنی همهٔ موضوع‌های فعال حفظ می‌شود.
- migration `0015_automationrun_selected_topics_and_more` روی PostgreSQL توسعه اعمال شد. اعتبارسنجی این اصلاح: `pytest -q` با ۱۱۶ تست موفق و یک هشدار URLField غیرمسدودکننده، `manage.py check` بدون خطا و `makemigrations --check --dry-run` بدون تغییر انجام شد.
- پنل «آخرین رصدها» به داشبورد و صفحهٔ مستقل `/monitoring/runs/` افزوده شد. هر اجرا با نوع، وضعیت، پیشرفت واقعی بر اساس `CollectionRunSource`های پایان‌یافته، منبع در حال بررسی، زمان و کنترل‌های امن نمایش داده می‌شود. وقتی اجرای pending/running وجود دارد، فقط همین پنل با HTMX هر ۸ ثانیه به‌روزرسانی می‌شود.
- migration `0016_collectionrun_cancel_requested_at_and_more` وضعیت `cancelled` و timestamp درخواست لغو را اضافه می‌کند. لغو pending فوری است؛ لغو running به‌صورت cooperative ثبت می‌شود و worker پیش از منبع بعدی و پیش از ذخیره‌سازی قابل‌برگشت آن را بررسی می‌کند. حذف فقط برای runهای terminal است و فقط خود run و نتیجه‌های منبع وابسته را حذف می‌کند؛ ContentItem، Source و Cursor حفظ می‌شوند. همهٔ لغوها و حذف‌های مؤثر staff-only، POST-based و AuditLogدار هستند.
- اعتبارسنجی پنل رصد: migration `0016` با موفقیت روی PostgreSQL توسعه اعمال شد؛ `pytest -q` با ۱۲۳ تست موفق و یک هشدار URLField غیرمسدودکننده، `manage.py check` بدون خطا و `makemigrations --check --dry-run` بدون تغییر انجام شدند.

## کارهای باقی‌مانده

- اعتبارسنجی کامل اجرای زنده Docker پس از رفع مانع Docker Desktop این workstation باقی مانده است.
- اعمال migrationهای `0010` تا `0012` روی PostgreSQL توسعه پس از راه‌اندازی Docker Desktop باقی مانده است.

## بدهی‌های فنی یا تصمیم‌های باز

- سیاست نهایی TLS و فعال‌سازی cookie امن، به محیط استقرار وابسته است.
- برنامه زمان‌بندی Beat پس از مشخص شدن وظایف فازهای بعدی تعریف می‌شود.
- Telegram public web crawler به markup عمومی وابسته است و در صورت تغییر HTML ممکن است parser نیازمند به‌روزرسانی شود؛ کانال خصوصی، گروه و لینک دعوت پشتیبانی نمی‌شوند.
- سطح دسترسی، quota و نگهداری دادهٔ API رسمی X به X Developer App و طرح انتخابی آن وابسته است. فقط recent-search پشتیبانی می‌شود و pagination به سقف محیطی هر Source محدود است.
- در ۱ سپتامبر ۲۰۲۶، `docker compose config --quiet` موفق بود؛ اما `docker compose up --build -d` پیش از ایجاد کانتینرهای web، worker و beat با خطای Docker Desktop متوقف شد: `failed to dial gRPC: rpc error: code = Internal desc = rpc error: code = Internal desc = header key "x-docker-expose-session-sharedkey" contains value with non-printable ASCII characters`. در همان اجرا، db و redis سالم بودند. به‌دلیل ساخته‌نشدن image وب، بررسی health، Django Admin، pytest داخل container و Celery worker ممکن نشد. این مانع محیطی است و هیچ تغییر کدی انجام نشد.
- در اعتبارسنجی نهایی ادغام، Docker Desktop در دسترس نبود: `failed to connect to the docker API at npipe:////./pipe/dockerDesktopLinuxEngine; check if the path is correct and if the daemon is running: open //./pipe/dockerDesktopLinuxEngine: The system cannot find the file specified.` بنابراین PostgreSQL توسعه روی `localhost:5432` در دسترس نبود و `manage.py migrate` توسعه تکمیل نشد؛ migration در محیط تست SQLite با موفقیت اعتبارسنجی شد.
