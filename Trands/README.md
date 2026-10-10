# سامانه رصد محتوای ترند

این پروژه یک Django monolith فارسی و راست‌چین برای رصد محتوا است. PostgreSQL پایگاه داده اصلی، Redis broker/cache و Celery worker و Beat برای وظایف پس‌زمینه هستند. جمع‌آوری Telegram در فاز ۳ فقط از صفحه‌های عمومی کانال انجام می‌شود؛ تحلیل AI و زمان‌بندی خودکار هنوز عمداً پیاده‌سازی نشده‌اند.

## اجرای Docker (روش پیشنهادی)

1. فایل نمونه محیط را کپی کنید و مقادیر محلی امن تعیین کنید:

   ```powershell
   Copy-Item .env.example .env
   ```

2. در `.env` مقدار `DJANGO_SECRET_KEY` و `POSTGRES_PASSWORD` را با مقادیر تصادفی و خصوصی جایگزین کنید.

3. سرویس‌ها را بسازید و اجرا کنید:

   ```powershell
   docker compose up --build
   ```

پس از اجرا، مسیر عادی ورود سامانه `http://localhost:8000/` است و endpoint سلامت در `http://localhost:8000/health/` قرار دارد. endpoint سلامت فقط وضعیت `web`، `database` و `redis` را بدون اطلاعات اتصال یا خطای داخلی گزارش می‌کند.

## داشبورد مدیریتی رصد

پس از ساخت کاربر مدیر، با همان حساب از مسیر `http://localhost:8000/` وارد شوید؛ کاربر staff پس از ورود به داشبورد عملیات می‌رسد. مسیر قدیمی `http://localhost:8000/monitoring/` فقط برای سازگاری حفظ شده و به `/` هدایت می‌شود. از داشبورد می‌توان موضوع‌ها، کلیدواژه‌ها، گروه‌های منابع، منابع و تنظیمات رصد را مدیریت کرد.

رابط اپراتور با زبان بصری «مرکز عملیات شبانه» بازطراحی شده است: زمینهٔ سرمه‌ای، شاخص‌های طلایی و وضعیت‌های سبزآبی، ناوبری ثابت RTL در دسکتاپ و ناوبری کشویی در موبایل. همهٔ شاخص‌های داشبورد دادهٔ واقعی دارند؛ در نبود داده، مقدار صفر یا وضعیت خالی نمایش داده می‌شود. `/admin/` فقط مسیر فنی Django است و در رابط اپراتور لینک نمی‌شود.

پوستهٔ اپراتور به Bootstrap یا HTMX CDN وابسته نیست؛ CSS و تعامل‌های ضروری از assetهای محلی پروژه ارائه می‌شوند تا رابط در شبکه‌های محدود نیز قابل استفاده باشد.

## دستورات مدیریتی Docker

```powershell
docker compose exec web python manage.py migrate
docker compose exec web python manage.py createsuperuser
docker compose exec worker celery -A config inspect ping
docker compose exec web pytest
docker compose down
```

`web` هنگام شروع migrationها را نیز به‌شکل خودکار اجرا می‌کند.

## اجرای محلی

به PostgreSQL و Redis در حال اجرا دسترسی داشته باشید، سپس متغیرهای `.env.example` را در محیط shell خود تنظیم کنید (برای اجرای محلی `POSTGRES_HOST=localhost` و `REDIS_URL=redis://localhost:6379/0` است). وابستگی‌ها را نصب و دستورات زیر را اجرا کنید:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python manage.py migrate
python manage.py runserver
celery -A config worker --loglevel=INFO
celery -A config beat --loglevel=INFO
pytest
```

`pytest` suite رسمی پروژه است. `python manage.py test` معیار اجرای تست‌ها نیست، زیرا تست‌ها با `pytest-django` و در پوشهٔ سطح‌بالای `tests/` نگهداری می‌شوند.

برای اجرای یک تست مشخص:

```powershell
pytest tests/test_project.py::test_home_page_opens
```

## تنظیمات

تنظیمات توسعه، production و test به‌ترتیب در `config.settings.development`، `config.settings.production` و `config.settings.test` هستند. اجرای عادی از PostgreSQL استفاده می‌کند. تست‌ها از یک SQLite موقت و ایزوله استفاده می‌کنند تا بدون نیاز به سرویس خارجی قابل تکرار باشند. تنظیمات production بدون `DJANGO_SECRET_KEY` شروع نمی‌شود.

## رصد کانال‌های عمومی Telegram در فاز ۳

این پروژه فقط صفحهٔ عمومی کانال‌های Telegram را از backend می‌خواند و هیچ Bot API، token، api_id، api_hash، Telethon، Pyrogram، حساب یا authentication Telegram نیاز ندارد. برای Source با platform Telegram فقط یکی از URLهای دقیق زیر مجاز است:

```text
https://t.me/s/channelname
https://telegram.me/s/channelname
```

لینک دعوت، گروه، کانال خصوصی، URL بدون `/s/`، HTTP و URLهای دارای بخش اضافی پذیرفته نمی‌شوند. `identifier` کانال از URL معتبر استخراج می‌شود و ورودی مستقل آن برای Telegram نادیده گرفته می‌شود. crawler متن/caption خام و متن نرمال‌شدهٔ قابل جست‌وجو، نام کشف‌شدهٔ کانال، زمان انتشار، شناسه و URL پست، views در صورت وجود و URL رسانهٔ HTTP/HTTPS را ثبت می‌کند؛ فایل رسانه دانلود نمی‌شود.

این crawler یک best-effort adapter است، نه API رسمی Telegram؛ تغییر markup عمومی Telegram ممکن است به به‌روزرسانی parser نیاز داشته باشد. خطای یک پست ناقص باعث توقف سایر پست‌ها یا سایر Sourceها نمی‌شود و نتیجهٔ آن Source به‌شکل `partial` ثبت می‌شود. cursor فقط تا آخرین پستِ پیوسته و قابل‌اعتماد جلو می‌رود تا پست خراب در اجرای بعد دوباره قابل تلاش باشد.

از داشبورد `/monitoring/` اجرای دستی را آغاز کنید. پست‌های تاریخی و جدید در `/monitoring/posts/` با جست‌وجو در متن نرمال‌شده، کانال و بازه تاریخ در دسترس‌اند؛ نمایش اصلی همیشه متن خام را نشان می‌دهد. بررسی دوره‌ای خودکار و تحلیل AI عمداً در فازهای آینده باقی مانده‌اند.

## رصد حساب‌های ثبت‌شدهٔ X در فاز ۴

اتصال X فقط از API رسمی X v2 و endpoint اخیر `GET /2/tweets/search/recent` استفاده می‌کند. برای استفاده، یک X Developer App با سطح دسترسی مناسب بسازید و فقط مقدار Bearer Token آن را در متغیر محیطی `X_BEARER_TOKEN` قرار دهید؛ این مقدار هرگز در Source، دیتابیس، رابط کاربری یا log ذخیره نمی‌شود.

متغیرهای مرتبط:

```text
X_BEARER_TOKEN=
X_API_BASE_URL=https://api.x.com/2
X_INITIAL_LOOKBACK_HOURS=24
X_MAX_POSTS_PER_SOURCE=50
X_HTTP_TIMEOUT_SECONDS=15
```

برای Source با platform `X`، handle مانند `XDevelopers` یا `@XDevelopers`، یا URL دقیق `https://x.com/XDevelopers` و `https://twitter.com/XDevelopers` را وارد کنید. نشانی در نهایت به `https://x.com/<handle>` canonical می‌شود. فقط پست‌های اخیر و اصلی حساب (`-is:retweet -is:reply`) رصد می‌شوند؛ archive، جست‌وجوی سراسری، repost و reply عمداً خارج از دامنه‌اند.

از داشبورد `/monitoring/` دکمهٔ «رصد X اکنون» را انتخاب کنید. هر اجرا فقط Sourceهای فعال X را در یک CollectionRun مشترک پردازش می‌کند؛ نتیجه، تعداد جدید/تکراری/به‌روزشده و خطاها در صفحهٔ جزئیات run دیده می‌شود. پست‌های X با فیلتر platform در `/monitoring/posts/` قابل مشاهده‌اند.

## تحلیل ترند فاز ۵

دکمهٔ «تحلیل ترند اکنون» در `/monitoring/` یک تحلیل دستی را آغاز می‌کند. این نسخه کاملاً rule-based و محلی است: کلیدواژه‌ها، fingerprint متن، character n-gram، URL و هشتگ مشترک را بررسی می‌کند؛ هیچ embedding، مدل AI یا API هوش مصنوعی استفاده نمی‌شود. خروجی در `/monitoring/trends/` قابل بررسی، تأیید، رد یا عدم‌ارسال است و در این فاز هیچ پیامی ارسال نمی‌شود. برچسب «حساس» فقط نیاز به توجه بیشتر را نشان می‌دهد و حکمی دربارهٔ صحت یا خطرناک‌بودن محتوا نیست.

## ارسال دستی گزارش به بله در فاز ۶

ارسال بله فقط از Bale Bot API و فقط از backend انجام می‌شود. token ربات را هرگز در پنل ثبت نکنید؛ تنها در environment قرار دهید:

```text
BALE_BOT_TOKEN=
BALE_API_BASE_URL=https://tapi.bale.ai
BALE_HTTP_TIMEOUT_SECONDS=15
SITE_BASE_URL=
```

1. در پیام‌رسان بله یک ربات بسازید و token آن را فقط در `BALE_BOT_TOKEN` قرار دهید.
2. ربات را به گروه مقصد اضافه کنید و مجوز ارسال پیام را به آن بدهید.
3. یک پیام آزمایشی در گروه بفرستید و سپس ابزار محدود زیر را اجرا کنید تا فقط `chat_id`، نوع و نام محدود chat نمایش داده شود؛ متن پیام‌ها نه نمایش داده می‌شود و نه در پایگاه‌داده ذخیره می‌شود:

   ```powershell
   python manage.py bale_chat_ids
   ```
4. در `/monitoring/bale/destinations/` مقصد را ثبت کنید، سپس «بررسی اتصال» و «پیام آزمایشی» را اجرا کنید.
5. از جزئیات یک ترند `trending` یا `confirmed`، مقصد فعال را انتخاب و «ارسال گزارش به بله» را بزنید.

هر ترکیب مقصد، ترند و نسخه گزارش فقط یک Delivery دارد. تحویل موفق دوباره ارسال نمی‌شود؛ «ارسال مجدد» فقط با اقدام صریح staff و نسخهٔ جدید گزارش مجاز است. زمان‌بندی یا ارسال خودکار در فاز ۶ فعال نیست و به فاز ۷ واگذار شده است.

## automation و عملیات فاز ۷

از `/monitoring/settings/` رصد خودکار را فعال و فاصلهٔ آن را تعیین کنید؛ حداقل فاصله ۵ و مقدار پیش‌فرض ۶۰ دقیقه است. Celery Beat هر دقیقه فقط dispatcher سبک را اجرا می‌کند و dispatcher مطابق تنظیمات database، pipeline مشترک Telegram → X → تحلیل ترند → Delivery را آغاز می‌کند. اجرای کامل دستی و تاریخچه در `/monitoring/automation/runs/` در دسترس است. ارسال خودکار تنها از Delivery idempotent استفاده می‌کند و برای Delivery نسخهٔ ۱ موجود، نسخهٔ جدیدی ایجاد نمی‌کند.

برای راهنمای production، backup/restore، retention، reverse proxy و خطاهای عملیاتی، [سند عملیات](docs/OPERATIONS.md) را بخوانید.

## ورود و رابط کاربری

مسیر `/` تنها ورودی عادی سامانه است: کاربران مهمان فرم ورود فارسی را می‌بینند و کاربران staff پس از ورود مستقیماً به داشبورد عملیات می‌رسند. مسیر قدیمی `/monitoring/` برای سازگاری حفظ شده و به `/` هدایت می‌شود. خروج فقط با درخواست POST و محافظت CSRF انجام می‌شود.

مسیر `/admin/` صرفاً ابزار فنی اضطراری Django برای مدیر سامانه است و در رابط عادی پیوندی به آن نمایش داده نمی‌شود. رابط اپراتوری فارسی، راست‌چین و واکنش‌گرا است و برای کار روزمره باید از `/` استفاده شود.
