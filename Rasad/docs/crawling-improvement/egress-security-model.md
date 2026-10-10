# مدل امنیتی خروج خزشگر

## مرزها

- فقط Worker مجاز به استفاده از `crawler-egress` است.
- API و legacy workflow engine تنظیم proxy عمومی دریافت نمی‌کنند.
- gateway روی میزبان port publish نمی‌کند و فقط عضو شبکه‌ی داخلی پروژه است.
- URL دارای credential، localhost، نام‌های `.local` و `.internal`، IPهای خصوصی و reserved و
  پورت‌های غیر از ۸۰/۴۴۳ رد می‌شوند.
- DNS با Google DoH و TLS معتبر حل می‌شود؛ نتیجه‌ی عمومی برای مدت محدود cache و برای اتصال
  pin می‌شود تا DNS rebinding رخ ندهد.
- مقصد در لایه‌ی برنامه و gateway مستقل از یکدیگر کنترل می‌شود.
- خطاها fail-closed هستند و اطلاعات پراکسی یا credential در پیام خطا و log ثبت نمی‌شود.

## مدل تهدید

کنترل‌ها SSRF به metadata، loopback و شبکه‌ی Docker، credential-in-URL، DNS rebinding، redirect
به مقصد داخلی و دورزدن پراکسی هنگام خرابی را پوشش می‌دهند. endpoint SOCKS5 بالادستی یک مرز
اعتماد است و باید اختصاصی، احراز هویت‌شده و تحت کنترل بهره‌بردار باشد؛ از پراکسی عمومی ناشناس
نباید استفاده شود.
