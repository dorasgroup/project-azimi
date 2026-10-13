# انتقال کامل Dify لوکال به VPS

این روش برای انتقال کامل وضعیت فعلی Dify لوکال است؛ شامل:

- Workspace
- Knowledge Base
- Documents
- Vector index
- فایل‌های آپلودشده
- تنظیمات مدل‌ها و Pluginها
- دیتابیس PostgreSQL Dify

مسیر Dify لوکال:

```text
D:\Dify\docker
```

پورت Dify روی VPS:

```text
http://5.10.249.198:19080
```

## مرحله 1: Dify لوکال را متوقف کن

روی ویندوز PowerShell:

```powershell
cd D:\Dify\docker
docker compose down
```

این کار برای این است که دیتابیس و vector store هنگام بسته‌بندی در حال نوشتن نباشند.

## مرحله 2: ساخت بسته کامل Dify

روی ویندوز PowerShell:

```powershell
cd D:\pr\07
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\deploy\build-dify-full-migration-bundle.ps1
```

خروجی:

```text
D:\pr\07\dist\dify-full-migration\dify-full-docker-with-data.tar
D:\pr\07\dist\dify-full-migration\dify-docker-images.tar
D:\pr\07\dist\dify-full-migration\vps-load-dify-full-migration.sh
```

## مرحله 3: انتقال فایل‌ها به VPS

روی ویندوز:

```powershell
scp D:\pr\07\dist\dify-full-migration\dify-full-docker-with-data.tar root@5.10.249.198:/tmp/dify-full-docker-with-data.tar
scp D:\pr\07\dist\dify-full-migration\dify-docker-images.tar root@5.10.249.198:/tmp/dify-docker-images.tar
scp D:\pr\07\dist\dify-full-migration\vps-load-dify-full-migration.sh root@5.10.249.198:/tmp/vps-load-dify-full-migration.sh
```

## مرحله 4: نصب روی VPS

روی VPS:

```bash
chmod +x /tmp/vps-load-dify-full-migration.sh
PROJECT_DIR=/opt/dify \
PACKAGE_PATH=/tmp/dify-full-docker-with-data.tar \
IMAGES_PATH=/tmp/dify-docker-images.tar \
/tmp/vps-load-dify-full-migration.sh
```

اگر `/opt/dify` از قبل وجود داشته باشد، اسکریپت از آن backup می‌گیرد.

## مرحله 5: تست Dify

در مرورگر:

```text
http://5.10.249.198:19080
```

باید همان Workspace و Knowledge Base لوکال را ببینی.

## مرحله 6: اتصال پروژه Ideation RAG به Dify منتقل‌شده

روی VPS:

```bash
cd /opt/ideation-rag/infrastructure
nano .env
```

مقادیر:

```env
DIFY_BASE_URL=http://host.docker.internal:19080/v1
DIFY_CHAT_API_KEY=کلید_Chatflow_منتقل_شده
DIFY_DATASET_API_KEY=کلید_Knowledge_Base_منتقل_شده
DIFY_DATASET_ID=ea1e709d-d175-4da4-8de4-3d2e5e1b5277
```

بعد:

```bash
docker compose -f docker-compose.offline.yml up -d --force-recreate backend
curl http://5.10.249.198:18090/api/v1/health/dify
```

## نکته

کلیدهای API در دیتابیس Dify منتقل می‌شوند، اما اگر پس از انتقال کار نکردند، از داخل Dify روی VPS دوباره API Key جدید بساز و در `.env` پروژه قرار بده.
