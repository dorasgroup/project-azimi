"""Offline real-renderer resource profile; no DB/model/crawler access."""

import json
import resource
import sys
import time
from tempfile import TemporaryDirectory
from uuid import uuid4
from app.modules.analysis.domain.aggregate_report_snapshot import freeze_report_snapshot
from app.modules.reports.application.document_snapshot import ReportDocumentSnapshot
from app.modules.reports.domain.contracts import ReportExportFormat, ReportExportRequest
from app.modules.reports.infrastructure.legacy_exporter import LegacyReportRenderer
from app.modules.reports.infrastructure.file_storage import LocalReportFileStorage


def benchmark(count):
    inputs = [{"source_id": str(uuid4()), "summary": ("این متن یک خلاصهٔ تحلیلی فارسی برای آزمون بار گزارش است. " * 200)[:8000],
               "raw_result": {"source_title": f"منبع {i}", "document_headers": [
                   {"url": f"https://example.invalid/{i}/{j}", "title": f"سند {j}", "published_at": "2026-09-30"} for j in range(15)]}}
              for i in range(count)]
    summary = "\n\n".join(str(i["summary"]) for i in inputs)
    findings = [{"source_id": inputs[i % count]["source_id"], "title": f"یافته {i}", "explanation": "یافتهٔ مستند برای تست " * 30} for i in range(50)]
    snapshot = freeze_report_snapshot(summary, findings, inputs)
    document = ReportDocumentSnapshot(uuid4(), "پروفایل گزارش بزرگ", summary, snapshot["document"], snapshot["sources"])
    renderer = LegacyReportRenderer()
    profile = {}
    with TemporaryDirectory() as directory:
        storage = LocalReportFileStorage(directory)
        for fmt in ("pdf", "docx"):
            start = time.monotonic()
            rendered = renderer.render(ReportExportRequest(document, ReportExportFormat(fmt)))
            duration = time.monotonic() - start
            start = time.monotonic()
            key, checksum, size = storage.save_content_addressed(document.id, fmt, rendered.content)
            assert storage.verify(document.id, fmt, key, checksum, size)
            profile[fmt] = {"render_seconds": round(duration, 3), "write_verify_seconds": round(time.monotonic() - start, 3), "bytes": size}
            print(json.dumps({"format_completed": fmt, **profile[fmt]}), flush=True)
    return {"source_count": count, "headers": count * 15, "summary_chars": len(summary),
            "findings": 50, "max_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss, "formats": profile}


if __name__ == "__main__":
    print(json.dumps(benchmark(int(sys.argv[1]) if len(sys.argv) > 1 else 100)))
