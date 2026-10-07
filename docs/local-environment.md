# محیط لوکال سامانه «رصد و تحلیل»

## وضعیت فاز دو

- تاریخ بررسی: ۱۴۰۵/۰۳/۱۶ (2026-06-06)
- Docker Desktop: فعال
- legacy workflow engine محلی در `D:\legacy workflow engine`: فعال
- Backend skeleton: فعال و سالم
- Frontend skeleton: فعال
- PostgreSQL: فعال و سالم
- Redis: فعال و سالم
- Worker و Crawl4AI: فعال؛ نصب مرورگر Crawl4AI مرحله جداگانه و مربوط به شروع پیاده‌سازی خزش است

## ابزارهای مشاهده‌شده روی میزبان

| ابزار | نسخه مشاهده‌شده |
| --- | --- |
| Git | 2.54.0 |
| Docker | 29.5.2 |
| Docker Compose | 5.1.4 |
| Python میزبان | 3.14.0 |
| Node.js میزبان | 24.15.0 |
| npm میزبان | 11.12.1 |
| legacy workflow engine محلی | 1.14.1 |

Python میزبان برای اجرای بک‌اند استفاده نمی‌شود. قرارداد اصلی اجرا Python 3.12 داخل Docker است.

## سرویس‌های پروژه

| سرویس | وضعیت فعلی | آدرس یا دسترسی |
| --- | --- | --- |
| Frontend | فعال | `http://localhost:5173` |
| API | فعال و healthy | `http://localhost:8010` |
| OpenAPI | فعال | `http://localhost:8010/docs` |
| Health endpoint | فعال | `http://localhost:8010/api/v1/health` |
| PostgreSQL | فعال و healthy | فقط شبکه Compose، پورت `5432` |
| Redis | فعال و healthy | فقط شبکه Compose، پورت `6379` |
| Celery worker | فعال | شبکه Compose |
| legacy workflow engine | فعال در `D:\legacy workflow engine\docker` با Compose مستقل | `http://host.docker.internal/v1` از داخل کانتینر پروژه |

## روش اجرای معمول

در محیط دارای دسترسی Docker Hub:

```powershell
Copy-Item .env.example .env
docker compose -f docker/compose.yml up --build -d
docker compose -f docker/compose.yml --profile worker up --build -d
```

## روش اجرای آفلاین جایگزین

اگر دسترسی به Docker Hub دوباره قطع شد، می‌توان موقتاً از imageهای موجود میزبان استفاده کرد:

```powershell
docker compose -f docker/compose.yml -f docker/compose.offline.yml up -d
```

در این حالت:

- API از image محلی `infrastructure-backend:latest` استفاده می‌کند.
- فرانت‌اند وضعیت با `nginx:1.27-alpine` ارائه می‌شود.
- PostgreSQL از `postgres:15-alpine` استفاده می‌کند.
- Redis از `redis:6-alpine` استفاده می‌کند.
- worker اجرا نمی‌شود و profile آن جدا است.

## نتایج بررسی

| بررسی | نتیجه |
| --- | --- |
| اعتبارسنجی Compose اصلی | موفق |
| اعتبارسنجی Compose آفلاین | موفق |
| سلامت API | `status=ok` |
| اتصال API به PostgreSQL | موفق |
| سلامت PostgreSQL | موفق |
| سلامت Redis | موفق |
| پاسخ OpenAPI | HTTP 200 |
| پاسخ Frontend | HTTP 200 |
| بررسی بصری رابط | موفق؛ صفحه فارسی RTL و وضعیت آماده نمایش داده شد |
| فعال‌بودن legacy workflow engine محلی | موفق؛ API، web و workerهای legacy workflow engine در حال اجرا هستند |
| دسترسی API پروژه به legacy workflow engine | موفق؛ endpointهای legacy workflow engine پاسخ `401 unauthorized` دادند که نشان‌دهنده دسترسی صحیح و نیاز به API Key است |
| build مستقل API | موفق |
| build مستقل React/Vite | موفق |
| build مستقل worker | موفق |
| Crawl4AI داخل worker | نسخه `0.8.9` نصب و import شد |
| Playwright داخل worker | نسخه `1.60.0` نصب و import شد |
| اجرای task واقعی Celery | موفق؛ پاسخ `pong` |
| build و lint فرانت‌اند | موفق |
| npm audit | بدون آسیب‌پذیری |

## سابقه محدودیت رجیستری

رجیستری به معنی مخزن اینترنتی دانلود بسته‌ها و imageهای آماده است و ارتباطی با نصب legacy workflow engine در `D:\legacy workflow engine` ندارد. نمونه‌ها:

- Docker Hub برای دانلود imageهای Docker
- npm registry برای دانلود کتابخانه‌های فرانت‌اند
- PyPI برای دانلود کتابخانه‌های Python

در بررسی اولیه فاز دو، Docker Hub پاسخ `403 Forbidden` داد و درخواست‌های package registry میزبان نیز timeout شدند. پس از روشن‌شدن فیلترشکن، دسترسی npm، PyPI و Docker Hub برقرار شد و موارد زیر تکمیل شدند:

- build مستقل image بک‌اند
- build مستقل React/Vite
- نصب Crawl4AI و Playwright داخل worker
- build و اجرای Celery worker
- اجرای Compose اصلی بدون override آفلاین

اگر دانلودهای خارجی در ادامه پروژه دوباره با timeout یا خطای دسترسی روبه‌رو شوند، باید وضعیت فیلترشکن به کارفرما اعلام شود.
