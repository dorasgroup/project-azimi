# نصب آفلاین Dify روی VPS کنار پروژه Ideation RAG

هدف: نصب Dify روی همان VPS بدون نیاز به اینترنت بین‌الملل روی سرور.

پورت‌ها:

- پروژه اصلی: `http://5.10.249.198:18090`
- Dify: `http://5.10.249.198:19080`

## مرحله 1: ساخت بسته Dify روی ویندوز

روی سیستم ویندوز:

```powershell
cd D:\pr\07
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\deploy\build-dify-offline-bundle.ps1
```

خروجی:

```text
D:\pr\07\dist\dify-offline\dify-docker.tar
D:\pr\07\dist\dify-offline\dify-docker-images.tar
D:\pr\07\dist\dify-offline\vps-load-dify-offline.sh
```

نکته: فایل imageها احتمالا چند گیگابایت است.

## مرحله 2: انتقال به VPS

روی ویندوز:

```powershell
scp D:\pr\07\dist\dify-offline\dify-docker.tar root@5.10.249.198:/tmp/dify-docker.tar
scp D:\pr\07\dist\dify-offline\dify-docker-images.tar root@5.10.249.198:/tmp/dify-docker-images.tar
scp D:\pr\07\dist\dify-offline\vps-load-dify-offline.sh root@5.10.249.198:/tmp/vps-load-dify-offline.sh
```

## مرحله 3: نصب روی VPS

روی VPS:

```bash
chmod +x /tmp/vps-load-dify-offline.sh
PROJECT_DIR=/opt/dify \
PACKAGE_PATH=/tmp/dify-docker.tar \
IMAGES_PATH=/tmp/dify-docker-images.tar \
/tmp/vps-load-dify-offline.sh
```

## مرحله 4: باز کردن Dify

در مرورگر:

```text
http://5.10.249.198:19080
```

Workspace، Knowledge Base و Chatflow را مثل لوکال بساز.

## مرحله 5: اتصال پروژه به Dify

بعد از ساخت API Keyها در Dify روی VPS، فایل env پروژه را تنظیم کن:

```bash
cd /opt/ideation-rag/infrastructure
nano .env
```

مقادیر:

```env
DIFY_BASE_URL=http://host.docker.internal:19080/v1
DIFY_CHAT_API_KEY=app-...
DIFY_DATASET_API_KEY=dataset-...
DIFY_DATASET_ID=...
```

بعد backend پروژه را recreate کن:

```bash
cd /opt/ideation-rag/infrastructure
docker compose -f docker-compose.offline.yml up -d --force-recreate backend
```

تست:

```bash
curl http://5.10.249.198:18090/api/v1/health/dify
```
