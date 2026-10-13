# راهنمای انتقال پروژه Ideation RAG به VPS

این راهنما برای سرور `5.10.249.198` آماده شده است.

## پیش‌نیاز

- دسترسی SSH به سرور
- Ubuntu/Debian روی VPS
- باز بودن پورت‌های `80` و در صورت SSL پورت `443`
- نصب بودن یا قابل نصب بودن Docker
- مشخص بودن وضعیت Dify روی VPS:
  - یا Dify روی همان VPS نصب می‌شود و از host در مسیر `http://host.docker.internal/v1` در دسترس backend قرار می‌گیرد.
  - یا Dify روی سرور/دامنه جداست و مقدار `DIFY_BASE_URL` باید همان آدرس باشد.

## ساخت پکیج روی ویندوز

از ریشه پروژه:

```powershell
cd D:\pr\07
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\deploy\package-vps.ps1
```

خروجی:

```text
D:\pr\07\dist\ideation-rag-vps.zip
```

## انتقال به VPS

نمونه با کاربر `root`:

```powershell
scp D:\pr\07\dist\ideation-rag-vps.zip root@5.10.249.198:/opt/
```

روی سرور:

```bash
ssh root@5.10.249.198
mkdir -p /opt/ideation-rag
unzip -o /opt/ideation-rag-vps.zip -d /opt/ideation-rag
cd /opt/ideation-rag
chmod +x scripts/deploy/vps-first-run.sh
```

## تنظیم env production

فایل زیر را ویرایش کن:

```bash
nano /opt/ideation-rag/infrastructure/.env
```

مقادیر مهم:

```env
PUBLIC_APP_URL=http://5.10.249.198
POSTGRES_PASSWORD=یک-رمز-قوی
JWT_SECRET_KEY=خروجی-openssl-rand-hex-48
DIFY_BASE_URL=http://host.docker.internal/v1
DIFY_CHAT_API_KEY=کلید-chatflow
DIFY_DATASET_API_KEY=کلید-knowledge-base
DIFY_DATASET_ID=ea1e709d-d175-4da4-8de4-3d2e5e1b5277
```

ساخت JWT secret روی سرور:

```bash
openssl rand -hex 48
```

## اجرای سرویس‌ها

```bash
cd /opt/ideation-rag/infrastructure
docker compose -f docker-compose.prod.yml build
docker compose -f docker-compose.prod.yml up -d
docker compose -f docker-compose.prod.yml ps
```

یا:

```bash
PROJECT_DIR=/opt/ideation-rag /opt/ideation-rag/scripts/deploy/vps-first-run.sh
```

## آدرس‌ها

- پنل پروژه:
  - `http://5.10.249.198`
- سلامت backend:
  - `http://5.10.249.198/api/v1/health`
- سلامت اتصال Dify:
  - `http://5.10.249.198/api/v1/health/dify`

## نکته درباره Dify

این پکیج، پنل اختصاصی پروژه، backend، PostgreSQL و nginx پروژه را deploy می‌کند. خود Dify باید روی VPS به‌صورت self-hosted نصب و تنظیم شود یا آدرس Dify موجود در `DIFY_BASE_URL` قرار بگیرد.

اگر Dify روی همین VPS با Docker و پورت `80` اجرا شود، با nginx این پروژه conflict می‌گیرد. در آن حالت یکی از این دو راه را انتخاب کن:

- Dify را روی پورت داخلی/دامنه جدا اجرا کن و `DIFY_BASE_URL` را به آن بده.
- برای پروژه دامنه جدا و reverse proxy چند hostname تنظیم کن.

## دستورات عیب‌یابی

```bash
cd /opt/ideation-rag/infrastructure
docker compose -f docker-compose.prod.yml logs -f backend
docker compose -f docker-compose.prod.yml logs -f frontend
docker compose -f docker-compose.prod.yml logs -f nginx
docker compose -f docker-compose.prod.yml exec backend python -m app.scripts.create_super_admin
```

## چک‌لیست تست

- `http://5.10.249.198` صفحه login را نشان بدهد.
- login با کاربر admin انجام شود.
- `http://5.10.249.198/api/v1/health` پاسخ `ok` بدهد.
- `http://5.10.249.198/api/v1/health/dify` اتصال Dify را نشان دهد.
- آپلود فایل document در پروژه ثبت شود.
- همان فایل در Dify Knowledge Base دیده شود.
- chat فقط بر اساس Knowledge Base پاسخ بدهد.
