# Environment Setup

این فایل محل تنظیم secretها و کلیدهای اتصال پروژه را مشخص می‌کند.

## Backend

کلیدهای واقعی Dify را فقط در این فایل وارد کن:

```text
backend/.env
```

این فایل در `.gitignore` قرار دارد و نباید commit شود.

مقادیر مهم:

```env
DIFY_BASE_URL=http://localhost/v1
DIFY_CHAT_API_KEY=app-...
DIFY_DATASET_API_KEY=dataset-...
DIFY_DATASET_ID=ea1e709d-d175-4da4-8de4-3d2e5e1b5277
```

برای اجرای backend داخل Docker Compose، مقدار `DIFY_BASE_URL` توسط `infrastructure/docker-compose.yml` به شکل زیر override می‌شود:

```env
DIFY_BASE_URL=http://host.docker.internal/v1
```

بنابراین روی ویندوز local، کلیدها را در `backend/.env` بگذار و نیازی نیست Dify API Key داخل frontend وارد شود.

## Frontend

frontend فقط به backend وصل می‌شود و نباید هیچ کلید Dify داخل آن قرار بگیرد.

اگر لازم شد آدرس backend را تغییر بدهی، فایل زیر را بساز:

```text
frontend/.env.local
```

نمونه:

```env
NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8000/api/v1
```
