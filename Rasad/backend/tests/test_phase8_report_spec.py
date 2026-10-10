from unittest import TestCase
from uuid import uuid4

from app.modules.reports.domain.generation import ReportGenerationSpec


class ReportGenerationSpecTests(TestCase):
    def test_digest_is_stable_and_round_trips(self) -> None:
        spec = ReportGenerationSpec(
            title="تحلیل", created_by=uuid4(),
            date_from_jalali="1405/07/01", date_to_jalali="1405/07/08",
        )
        restored = ReportGenerationSpec.from_json(spec.to_json())
        self.assertEqual(restored, spec)
        self.assertEqual(restored.digest, spec.digest)
        self.assertEqual(restored.formats, ("pdf", "docx"))

    def test_spec_rejects_added_secret_and_unsupported_options(self) -> None:
        spec = ReportGenerationSpec(
            title="تحلیل", created_by=uuid4(),
            date_from_jalali="1405/07/01", date_to_jalali="1405/07/08",
        )
        data = spec.to_json()
        data["api_key"] = "secret"
        with self.assertRaises(ValueError):
            ReportGenerationSpec.from_json(data)
        data.pop("api_key")
        data["formats"] = ["pdf"]
        with self.assertRaises(ValueError):
            ReportGenerationSpec.from_json(data)
