# سیاست‌گذاری ثبت وقایع (Logging Policy)

به منظور تطابق با استانداردهای بالای امنیت و نگهداری اطلاعات، تفاوت‌های اساسی Application Log و Audit Log به شرح زیر در سیستم اعمال می‌شود:

## ۱. Application Log (تکنیکال و عیب‌یابی)
- **هدف:** دیباگ کردن خطاها، ردیابی جریان سیستم (Tracing) و تحلیل Latency.
- **طول عمر (Retention):** کوتاه مدت (۷ الی ۱۴ روز در Hot Storage).
- **قابلیت حذف:** در صورت نیاز به ذخیره منابع، بر اساس Policy قابل حذف هستند (Volatile).
- **دسترسی:** تیم توسعه و DevOps.
- **استثنائات (ممنوعیات):** ثبت هرگونه متن چت، متن سند، Password، توکن‌ها و Cookie در این لاگ‌ها **کاملا ممنوع** است.

## ۲. Audit Log (ردیابی حساسیت‌های تجاری)
- **هدف:** پاسخگویی قانونی (Compliance)، ردیابی رفتار مدیران، ردیابی عملیات‌های حساس مثل `Delete Tenant` یا `Change Permission`.
- **طول عمر (Retention):** بلند مدت (۲ تا ۵ سال در Cold/Archive Storage).
- **قابلیت حذف:** کاملاً غیرقابل تغییر (Tamper-resistant / Immutable Storage مانند Object Lock در S3).
- **دسترسی:** فقط Security Analysts و Auditors (تیم توسعه دسترسی ندارد).
- **ساختار:** فقط از `audit-schema.json` پیروی می‌کند.

## ۳. فرآیند Redaction (سانسور داده)
تمام Application Log ها باید قبل از ارسال به سیستم Storage، از یک `Redaction Pipeline` در سطح OpenTelemetry Collector عبور کنند تا از عدم نشت PII اطمینان حاصل شود.
