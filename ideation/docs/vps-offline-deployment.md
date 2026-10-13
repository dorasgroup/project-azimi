# Deploy آفلاین پروژه روی VPS

این مسیر برای وقتی است که VPS اینترنت بین‌الملل ندارد و نباید روی سرور `docker pull`، `npm install`، `pip install` یا build آنلاین انجام شود.

## نکته مهم

این روش imageهای لازم پروژه را روی سیستم ویندوز خودت می‌سازد و به فایل tar تبدیل می‌کند. سپس روی VPS فقط `docker load` انجام می‌شود.

اگر خود Dify یا مدل LLM نیاز به OpenRouter داشته باشد، همچنان باید Dify/LLM به اینترنت آزاد یا مدل local مثل Ollama دسترسی داشته باشد. این bundle فقط پنل پروژه، backend، frontend، nginx و PostgreSQL را آفلاین deploy می‌کند.

## مرحله 1: ساخت bundle روی ویندوز

روی سیستم خودت، جایی که اینترنت و Docker Desktop کار می‌کند:

```powershell
cd D:\pr\07
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\deploy\build-offline-bundle.ps1
```

خروجی در این مسیر ساخته می‌شود:

```text
D:\pr\07\dist\ideation-rag-offline
```

فایل‌های مهم:

```text
ideation-rag-vps.tar
ideation-rag-vps.zip
ideation-rag-docker-images.tar
vps-load-offline.sh
```

## مرحله 2: ارسال فایل‌ها به VPS

```powershell
scp D:\pr\07\dist\ideation-rag-offline\ideation-rag-vps.tar root@5.10.249.198:/tmp/ideation-rag-vps.tar
scp D:\pr\07\dist\ideation-rag-offline\ideation-rag-docker-images.tar root@5.10.249.198:/tmp/ideation-rag-docker-images.tar
scp D:\pr\07\dist\ideation-rag-offline\vps-load-offline.sh root@5.10.249.198:/tmp/vps-load-offline.sh
```

## مرحله 3: اجرای bundle روی VPS

```bash
ssh root@5.10.249.198
chmod +x /tmp/vps-load-offline.sh
PROJECT_DIR=/opt/ideation-rag \
PACKAGE_PATH=/tmp/ideation-rag-vps.tar \
IMAGES_PATH=/tmp/ideation-rag-docker-images.tar \
/tmp/vps-load-offline.sh
```

اگر فایل `.env` تازه ساخته شد، قبل از اجرای نهایی آن را ویرایش کن:

```bash
nano /opt/ideation-rag/infrastructure/.env
```

مقادیر ضروری:

```env
PUBLIC_APP_URL=http://5.10.249.198
POSTGRES_PASSWORD=یک_رمز_قوی
JWT_SECRET_KEY=خروجی_openssl_rand_hex_48
DIFY_BASE_URL=http://host.docker.internal/v1
DIFY_CHAT_API_KEY=کلید_chatflow
DIFY_DATASET_API_KEY=کلید_knowledge_base
DIFY_DATASET_ID=ea1e709d-d175-4da4-8de4-3d2e5e1b5277
```

ساخت JWT:

```bash
openssl rand -hex 48
```

بعد اجرا:

```bash
cd /opt/ideation-rag/infrastructure
docker compose -f docker-compose.offline.yml up -d
```

## تست

```bash
docker compose -f /opt/ideation-rag/infrastructure/docker-compose.offline.yml ps
curl http://5.10.249.198/api/v1/health
curl http://5.10.249.198/api/v1/health/dify
```

پنل:

```text
http://5.10.249.198
```
