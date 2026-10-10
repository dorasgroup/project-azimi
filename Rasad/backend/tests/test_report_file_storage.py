from tempfile import TemporaryDirectory
from unittest import TestCase
from uuid import uuid4
from unittest.mock import patch

from app.modules.reports.domain import RenderedReport
from app.modules.reports.infrastructure.file_storage import LocalReportFileStorage


class ReportFileStorageTests(TestCase):
    def test_storage_writes_only_under_its_root(self) -> None:
        with TemporaryDirectory() as temporary:
            storage = LocalReportFileStorage(temporary)
            report_id = uuid4()
            path = storage.save(
                report_id,
                RenderedReport(content=b"content", filename=f"{report_id}-report.pdf"),
            )
            self.assertEqual(path.read_bytes(), b"content")
            storage.delete(path)
            self.assertFalse(path.exists())

    def test_storage_rejects_unsupported_extension(self) -> None:
        with TemporaryDirectory() as temporary:
            with self.assertRaises(ValueError):
                LocalReportFileStorage(temporary).path_for(uuid4(), "txt")

    def test_storage_rejects_filename_for_another_report(self) -> None:
        with TemporaryDirectory() as temporary:
            with self.assertRaises(ValueError):
                LocalReportFileStorage(temporary).save(
                    uuid4(),
                    RenderedReport(content=b"content", filename=f"{uuid4()}-report.pdf"),
                )

    def test_deterministic_storage_replaces_atomically_and_cleans_temp(self) -> None:
        with TemporaryDirectory() as temporary:
            storage = LocalReportFileStorage(temporary)
            report_id = uuid4()
            path = storage.save_deterministic(report_id, "pdf", b"first")
            self.assertEqual(path.name, f"{report_id}.pdf")
            self.assertEqual(storage.save_deterministic(report_id, "pdf", b"second"), path)
            self.assertEqual(path.read_bytes(), b"second")
            self.assertEqual(list(path.parent.glob("*.tmp")), [])

    def test_failed_replace_preserves_existing_artifact(self) -> None:
        with TemporaryDirectory() as temporary:
            storage = LocalReportFileStorage(temporary)
            report_id = uuid4()
            path = storage.save_deterministic(report_id, "docx", b"valid")
            with patch(
                "app.modules.reports.infrastructure.file_storage.os.replace",
                side_effect=OSError("replace unavailable"),
            ), self.assertRaises(OSError):
                storage.save_deterministic(report_id, "docx", b"partial")
            self.assertEqual(path.read_bytes(), b"valid")
            self.assertEqual(list(path.parent.glob("*.tmp")), [])

    def test_content_addressed_paths_fence_different_rendered_bytes(self) -> None:
        with TemporaryDirectory() as temporary:
            storage = LocalReportFileStorage(temporary)
            generation_id = uuid4()
            first_key, first_hash, first_size = storage.save_content_addressed(
                generation_id, "pdf", b"worker-b-output",
            )
            stale_key, stale_hash, stale_size = storage.save_content_addressed(
                generation_id, "pdf", b"late-worker-a-output",
            )
            self.assertNotEqual(first_key, stale_key)
            self.assertTrue(storage.verify(generation_id, "pdf", first_key, first_hash, first_size))
            self.assertTrue(storage.verify(generation_id, "pdf", stale_key, stale_hash, stale_size))
            self.assertFalse(storage.verify(generation_id, "pdf", first_key, stale_hash, stale_size))
