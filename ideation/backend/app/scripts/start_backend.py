import os

import uvicorn

from app.observability.metrics import prepare_multiprocess_directory


def main() -> None:
    raw_workers = os.getenv("BACKEND_WORKERS", "1")
    try:
        workers = int(raw_workers)
    except ValueError as exc:
        raise ValueError("BACKEND_WORKERS must be an integer") from exc
    if not 1 <= workers <= 16:
        raise ValueError("BACKEND_WORKERS must be between 1 and 16")

    prepare_multiprocess_directory()
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        workers=workers,
        access_log=False,
    )


if __name__ == "__main__":
    main()
