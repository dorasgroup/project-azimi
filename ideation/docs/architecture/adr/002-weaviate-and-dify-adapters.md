# ADR 002: جداسازی Dify و Weaviate پشت Gateway و Adapterها

> **وضعیت: معماری هدف، هنوز در Production فعال نیست.** اتصال جاری FastAPI به Dify و
> Weaviate در `docs/architecture/current-production.md` ثبت شده است.

## Context
پروژه از ابزارهای قدرتمندی مانند Dify به عنوان AI Orchestrator و Weaviate به عنوان Vector Database استفاده می‌کند. اتصال مستقیم کلاینت به این ابزارها خطر نشت اطلاعات (Data Leakage) و وابستگی شدید (Vendor Lock-in) را به همراه دارد.

## Decision
کلاینت (Frontend) **هرگز** اجازه اتصال مستقیم به Dify یا Weaviate را نخواهد داشت. تمام درخواست‌های چت باید از طریق **AI Gateway** و تمام ورود داده‌ها (Ingestion) باید از طریق **Ingestion Worker** عبور کنند. AI Gateway نقش یک Anti-Corruption Layer را بازی می‌کند.

## Consequences
- **مثبت:** امنیت و حفظ حریم خصوصی (Privacy by Design) تضمین می‌شود. امکان تغییر Dify در آینده با یک ابزار دیگر (مثل LangChain) بدون تغییر کد سمت کاربر وجود دارد.
- **منفی:** افزایش یک لایه پردازشی (Network Hop) که ممکن است در حد میلی‌ثانیه به تاخیر اولیه (TTFT) اضافه کند.

## Revisit Trigger
چنانچه Dify ویژگی‌های قدرتمند مدیریت Tenant Isolation و IAM را در سطح Enterprise در نسخه‌های بعدی خود به شکل بومی و مطمئن ارائه کند، می‌توانیم در این لایه بازنگری کنیم.
