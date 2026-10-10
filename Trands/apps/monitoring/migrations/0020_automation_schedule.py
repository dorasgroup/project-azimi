from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import migrations, models
import django.db.models.deletion


def migrate_current_automation(apps, schema_editor):
    SystemSetting=apps.get_model("monitoring","SystemSetting")
    AutomationSchedule=apps.get_model("monitoring","AutomationSchedule")
    setting=SystemSetting.objects.filter(pk=1,automation_enabled=True).first()
    if not setting:
        return
    source_ids=list(setting.automation_sources.values_list("pk",flat=True))
    topic_ids=list(setting.automation_topics.values_list("pk",flat=True))
    if not source_ids or not topic_ids:
        return
    schedule=AutomationSchedule.objects.create(
        interval_days=max(1,min(7,round(setting.automation_interval_minutes/1440))),
        is_active=True,
    )
    schedule.sources.set(source_ids)
    schedule.topics.set(topic_ids)


class Migration(migrations.Migration):
    dependencies=[("monitoring","0019_source_importance")]

    operations=[
        migrations.CreateModel(
            name="AutomationSchedule",
            fields=[
                ("id",models.BigAutoField(auto_created=True,primary_key=True,serialize=False,verbose_name="ID")),
                ("created_at",models.DateTimeField(auto_now_add=True,verbose_name="زمان ایجاد")),
                ("updated_at",models.DateTimeField(auto_now=True,verbose_name="زمان به‌روزرسانی")),
                ("interval_days",models.PositiveSmallIntegerField(validators=[MinValueValidator(1),MaxValueValidator(7)],verbose_name="بازه اجرا (روز)")),
                ("is_active",models.BooleanField(default=True,verbose_name="فعال")),
                ("last_started_at",models.DateTimeField(blank=True,null=True,verbose_name="آخرین شروع")),
                ("sources",models.ManyToManyField(related_name="automation_schedules",to="monitoring.source",verbose_name="منابع")),
                ("topics",models.ManyToManyField(related_name="automation_schedules",to="monitoring.topic",verbose_name="موضوع‌ها")),
            ],
            options={"verbose_name":"رصد خودکار","verbose_name_plural":"رصدهای خودکار","ordering":("-is_active","id")},
        ),
        migrations.AddField(
            model_name="automationrun",
            name="schedule",
            field=models.ForeignKey(blank=True,null=True,on_delete=django.db.models.deletion.SET_NULL,related_name="runs",to="monitoring.automationschedule",verbose_name="رصد خودکار"),
        ),
        migrations.RunPython(migrate_current_automation,migrations.RunPython.noop),
    ]
