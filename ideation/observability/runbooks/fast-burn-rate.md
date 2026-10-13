# Runbook: هشدارهای Fast Burn Rate (کاهش سریع بودجه خطای سیستم)

## توضیح هشدار (Alert Description)
این هشدار زمانی به صدا درمی‌آید که سیستم در یک پنجره ۵ دقیقه‌ای، با نرخ شدیدی در حال خطا دادن باشد و اگر این روند ادامه یابد، بودجه خطای (Error Budget) مجاز برای ۳۰ روز، در کمتر از ۲ ساعت نابود خواهد شد. این وضعیت **SEV-1 (وضعیت بحرانی)** است.

## ۱. ارزیابی فوری (Triage & Impact)
- آیا کاربران به طور کلی در دریافت پیام خطای ۵۰۰ دریافت می‌کنند؟
- **داشبوردها برای بررسی:**
  - `Platform / Executive Dashboard`
  - `AI Gateway / Chat Request Error Rates`

## ۲. تشخیص ریشه (Root Cause Analysis)
1. **بررسی لاگ‌های Gateway:** به Loki بروید و جستجو کنید: `{service="ai-gateway"} |= "error"`
2. **بررسی Circuit Breakers:** آیا Metric مربوط به `circuit_breaker_state{state="open"}` مقدار ۱ را نشان می‌دهد؟ اگر بله، قطعی از سمت ارائه‌دهنده (Provider) خارجی مثل AvalAI است.
3. **بررسی منابع پایگاه داده:** آیا کانکشن‌های PostgreSQL اشباع شده‌اند؟

## ۳. اقدامات کاهش آسیب (Mitigation Steps)
- **اگر مشکل از Provider است:** اطمینان حاصل کنید که سرویس `Provider Resolver` ترافیک را به مدل Fallback هدایت می‌کند.
- **اگر مشکل از اشباع منابع کلاستر (CPU/Memory) است:** در صورت نیاز مداخله دستی کرده و Replicaهای سیستم را مقیاس دهید (HPA Scaling).
- **اگر پس از یک استقرار (Deployment) جدید رخ داده:** با تایید Incident Commander بلافاصله به نسخه پایدار قبلی Rollback کنید.

## ۴. اقدامات پس از رویداد (Post-Incident)
- نوشتن Postmortem (تحلیل بدون سرزنش).
- بررسی اینکه چرا تست‌های Pipeline جلوی باگ را نگرفتند.
