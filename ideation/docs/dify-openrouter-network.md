# Dify OpenRouter Network Issue

خطای زیر از frontend یا backend اختصاصی پروژه نیست:

```text
Run failed: [models] Server Unavailable Error ... openrouter.ai ... timed out
```

معنی خطا این است که کانتینرهای Dify نمی‌توانند به OpenRouter وصل شوند.

## وضعیت مشاهده‌شده

- backend پروژه سالم است.
- مسیر chat از frontend به backend کار می‌کند.
- اتصال backend به Dify Knowledge Base سالم است.
- اجرای Chatflow در Dify هنگام رسیدن به LLM node شکست می‌خورد.
- تست اتصال از ویندوز به `https://openrouter.ai` شکست خورد.
- تست اتصال از کانتینر Dify API به `openrouter.ai` هم شکست خورد.

## راه‌حل‌ها

یکی از این مسیرها را انتخاب کن:

1. VPN را طوری فعال کن که Docker Desktop هم از آن استفاده کند.
   - در بسیاری از VPNها باید گزینه TUN Mode، System Proxy یا Allow LAN/Docker فعال شود.

2. اگر proxy محلی داری، proxy را برای سرویس‌های Dify تنظیم کن.
   - مقدارهای معمول:
     - `HTTP_PROXY`
     - `HTTPS_PROXY`
     - `NO_PROXY`
   - این مقادیر باید برای سرویس‌های `api` و `worker` در Dify اعمال شوند.

3. provider مدل را در Dify عوض کن.
   - اگر OpenRouter از شبکه فعلی قابل دسترس نیست، از provider دیگری استفاده کن که داخل Docker به آن دسترسی داری.

## تست سریع

از ریشه پروژه اجرا کن:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\dev\check-dify-network.ps1
```

تا وقتی این تست‌ها برای OpenRouter موفق نشوند، بخش گفتگو در UI هم پاسخ مدل دریافت نمی‌کند.
