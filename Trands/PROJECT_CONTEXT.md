# Project Context

## Overview

- **سامانه رصد محتوای ترند:** ابزار داخلی فارسی/RTL برای رصد منابع عمومی Telegram و X، تحلیل ترند و ارسال گزارش به بله.
- **Stack:** Django 5.2 monolith; Python backend; Django Templates + local JavaScript frontend.
- **UI:** local CSS in `static/css/app.css`; dark navy/gold/teal operations theme; local fonts.
- **Data:** PostgreSQL + Django ORM; Redis for cache/Celery; SQLite only for isolated tests.
- **Auth:** Django session authentication; `/` shows login to guests and dashboard to staff.
- **Package manager:** pip via `requirements.txt`.

## Start Here

- **Routes:** `config/urls.py`, then `apps/core/urls.py` or `apps/monitoring/urls.py`
- **Web views/forms:** `apps/core/views.py`, `apps/monitoring/views.py`, `apps/monitoring/forms.py`
- **Domain/data/services:** `apps/monitoring/models.py` and related modules in `apps/monitoring/`
- **Templates/components:** `templates/core/`, `templates/monitoring/` (no separate frontend component directory)
- **Styles/interactions:** `static/css/app.css`, `static/js/app.js`
- **Assets:** `static/fonts/`, `Font/`
- **Settings:** `config/settings/`; Docker: `compose.yaml`
- **Tests:** `tests/` (pytest-django)

## Development Commands

```powershell
python manage.py runserver
celery -A config worker --loglevel=INFO
celery -A config beat --loglevel=INFO
pytest tests/<affected_file>.py::test_name
```

`pytest` is the full suite. No dedicated lint or type-check command is documented.

## Architecture Notes

- Keep business logic in the owning app's services; views handle request validation and rendering.
- Manual and scheduled workflows share underlying logic; collection and delivery operations are idempotent.
- Telegram uses public `/s/<channel>` pages only; X uses its official API; external tokens remain environment-only.
- `/admin/` is a technical Django tool, not part of the operator navigation.
