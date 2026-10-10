from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("monitoring", "0017_alter_systemsetting_automation_interval_minutes")]

    operations = [
        migrations.CreateModel(
            name="IdeaDelivery",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="زمان ایجاد")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="زمان به‌روزرسانی")),
                ("source_system", models.CharField(default="trend-monitor", max_length=64)),
                ("source_trend_id", models.CharField(max_length=200)),
                ("source_version", models.PositiveIntegerField(default=1)),
                ("idempotency_key", models.CharField(max_length=255, unique=True)),
                ("content_hash", models.CharField(max_length=64)),
                ("title", models.CharField(max_length=500)),
                ("topic_name", models.CharField(blank=True, max_length=255)),
                ("score", models.FloatField(blank=True, null=True)),
                ("detected_at", models.DateTimeField()),
                ("payload", models.JSONField(default=dict)),
                ("idea_status", models.CharField(choices=[("pending", "در انتظار"), ("sending", "در حال ارسال"), ("accepted", "دریافت‌شده"), ("failed", "ناموفق")], db_index=True, default="pending", max_length=16)),
                ("dify_status", models.CharField(choices=[("not_started", "شروع‌نشده"), ("queued", "در صف"), ("processing", "در حال پردازش"), ("document_created", "سند ایجاد شد"), ("indexing", "در حال نمایه‌سازی"), ("indexed", "ثبت‌شده"), ("failed", "ناموفق")], db_index=True, default="not_started", max_length=32)),
                ("attempt_count", models.PositiveIntegerField(default=0)),
                ("last_attempt_at", models.DateTimeField(blank=True, null=True)),
                ("next_attempt_at", models.DateTimeField(blank=True, db_index=True, null=True)),
                ("accepted_at", models.DateTimeField(blank=True, null=True)),
                ("dify_completed_at", models.DateTimeField(blank=True, null=True)),
                ("remote_import_id", models.CharField(blank=True, max_length=64)),
                ("remote_upload_id", models.CharField(blank=True, max_length=64)),
                ("remote_ingestion_job_id", models.CharField(blank=True, max_length=64)),
                ("dify_document_id", models.CharField(blank=True, max_length=64)),
                ("last_http_status", models.PositiveSmallIntegerField(blank=True, null=True)),
                ("error_summary", models.CharField(blank=True, max_length=300)),
                ("trend_cluster", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="idea_deliveries", to="monitoring.trendcluster", verbose_name="خوشه ترند")),
            ],
            options={
                "ordering": ("-created_at",),
                "constraints": [models.UniqueConstraint(fields=("source_system", "source_trend_id", "source_version"), name="unique_idea_delivery_source_version")],
            },
        )
    ]
