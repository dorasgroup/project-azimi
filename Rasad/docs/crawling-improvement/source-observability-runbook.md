# راهنمای عملیاتی diagnostics منبع

## آزمون قطعی

```powershell
docker compose -f docker/compose.yml exec -T api pytest -q tests/test_source_execution_diagnostics.py tests/test_crawl_diagnostics.py
docker compose -f docker/compose.yml exec -T api python scripts/verify_phase6_source_diagnostics.py
```

## مشاهده diagnostics یک رصد

در صفحه لاگ‌های سیستم، رکوردهای ماژول `monitoring_worker` و پیام `Source crawled and prepared` را برای `job_id` موردنظر باز کنید.

ترتیب بررسی:

1. `source_diagnostics.reason_code`؛
2. `source_diagnostics.recommended_action`؛
3. `source_diagnostics.stage_counts` برای یافتن مرحله افت؛
4. `source_diagnostics.rejection_counts` برای علت رد محتوا؛
5. `crawl_stage_trace.discovery` برای providerها و fallback؛
6. `crawl_stage_trace.selection` برای رتبه‌بندی و انتخاب؛
7. `crawl_diagnostics.failure_counts` و `http_status_counts` برای fetch.

## الگوهای رایج

| الگو | نتیجه |
|---|---|
| `discovered=0` | مشکل discovery/profile/egress |
| `discovered>0`, `selected=0` | تاریخ یا selection |
| `selected>0`, `fetch_succeeded=0` | HTTP، anti-bot، browser یا egress |
| `fetch_succeeded>0`, `prepared=0`, `no_topic_match>0` | نتایج با موضوع سازگار نیستند |
| `fetch_succeeded>0`, `prepared=0`, `missing_publication_date>0` | extractor تاریخ کافی نیست |
| `prepared=0`, `duplicates>0` | داده موجود است ولی تکراری است |
| `prepared>0`, `status=partial` | منبع قابل استفاده است؛ علت‌های رد پرتکرار بررسی شوند |

## قواعد عملیاتی

- وضعیت اتصال منبع را با relevance یک موضوع خاص یکی ندانید.
- برای `robots_blocked` محدودیت سایت را دور نزنید.
- برای `no_topic_match` موضوع canonical را تغییر ندهید؛ query plan و aliasها را بررسی کنید.
- برای `missing_publication_date` timestamp آرشیو را جای تاریخ ناشر ذخیره نکنید.
- قبل از افزایش سقف خزش، مشخص کنید افت در discovery است یا processing.

