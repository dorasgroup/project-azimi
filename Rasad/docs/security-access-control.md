# امنیت و کنترل دسترسی

این سند خروجی فاز سیزده پروژه «رصد و تحلیل» است و وضعیت احراز هویت، نقش‌ها، tokenها و محدودیت مالکیت داده را توضیح می‌دهد.

## هدف فاز

هدف فاز سیزده تبدیل dependencyهای placeholder امنیتی به کنترل دسترسی واقعی بود:

- login با نام کاربری یا ایمیل
- تولید access token و refresh token
- refresh token با چرخش و ابطال token قبلی
- logout اختیاری با ابطال refresh token
- مشاهده و ویرایش پروفایل
- تغییر رمز عبور
- مدیریت کاربران توسط مدیر کل
- اعمال نقش `super_admin` و `user`
- محدود کردن jobها و گزارش‌ها بر اساس مالکیت کاربر

## پیاده‌سازی

### رمز عبور

رمزها با PBKDF2-HMAC-SHA256 و salt تصادفی ذخیره می‌شوند. مقدار خام رمز هیچ‌وقت در دیتابیس ذخیره نمی‌شود.

### access token

access token از نوع JWT با امضای HS256 است و شامل claimهای زیر می‌شود:

- `sub`: شناسه کاربر
- `role`: نقش کاربر
- `typ=access`
- `iat`
- `exp`

کلید امضا از `AUTH_SECRET_KEY` خوانده می‌شود.

### refresh token

refresh token به صورت opaque token تولید می‌شود و فقط hash آن در جدول `refresh_tokens` ذخیره می‌گردد. هنگام refresh، token قبلی revoke می‌شود و جفت token جدید صادر می‌شود. استفاده دوباره از refresh token قبلی با `401` رد می‌شود.

## تنظیمات محیطی

```env
AUTH_SECRET_KEY=change-this-local-development-secret
ACCESS_TOKEN_EXPIRE_MINUTES=30
REFRESH_TOKEN_EXPIRE_DAYS=14
```

در محیط واقعی باید مقدار `AUTH_SECRET_KEY` حتماً تغییر کند.

## کنترل دسترسی

- مسیرهای عمومی: health و login/refresh
- مسیرهای کاربر احرازشده: profile، jobها، گزارش‌ها
- مسیرهای مدیر کل: مدیریت کاربران، ایجاد/ویرایش/حذف منابع و هویت‌ها
- jobها برای کاربر عادی فقط در محدوده `requested_by` خودش قابل مشاهده و لغو هستند.
- گزارش‌ها برای کاربر عادی فقط در محدوده `created_by` خودش قابل مشاهده، حذف و دانلود هستند.
- مدیر کل به فهرست کامل کاربران، jobها و گزارش‌ها دسترسی دارد.

## endpointهای فعال‌شده

- `POST /auth/login`
- `POST /auth/refresh`
- `POST /auth/logout`
- `GET /auth/me`
- `PUT /auth/profile`
- `PUT /auth/change-password`
- `GET /admin/users`
- `POST /admin/users`
- `PUT /admin/users/{user_id}`
- `DELETE /admin/users/{user_id}`

## آزمون پذیرش

```powershell
docker compose --env-file .env -f docker/compose.yml exec -T api python -m scripts.verify_security
```

نتیجه آخرین اجرا در تاریخ ۱۴۰۵/۰۳/۱۷:

- درخواست ناشناس به `/auth/me` با `401` رد شد.
- login با رمز اشتباه با `401` رد شد.
- login با رمز درست access و refresh token صادر کرد.
- کاربر عادی به `/auth/me` دسترسی گرفت.
- کاربر عادی از مسیرهای admin با `403` رد شد.
- کاربر عادی نتوانست job و گزارش کاربر دیگر را ببیند.
- refresh token پس از یک بار مصرف revoke شد و استفاده دوباره از آن با `401` رد شد.
- مدیر کل توانست فهرست کاربران را ببیند.

## مرز فاز سیزده

در این فاز rate limiting، CSRF برای کلاینت‌های cookie-based، سیاست پیچیده مالکیت منابع، ثبت audit log امنیتی و MFA پیاده‌سازی نشدند. این موارد برای فازهای مانیتورینگ، تست نهایی یا نسخه حرفه‌ای‌تر قابل بررسی هستند.
