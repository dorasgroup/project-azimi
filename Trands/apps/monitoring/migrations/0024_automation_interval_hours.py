from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import migrations, models


def days_to_hours(apps, schema_editor):
    schedule = apps.get_model("monitoring", "AutomationSchedule")
    for item in schedule.objects.only("pk", "interval_hours").iterator():
        item.interval_hours *= 24
        item.save(update_fields=["interval_hours"])


def hours_to_days(apps, schema_editor):
    schedule = apps.get_model("monitoring", "AutomationSchedule")
    for item in schedule.objects.only("pk", "interval_hours").iterator():
        item.interval_hours = max(1, item.interval_hours // 24)
        item.save(update_fields=["interval_hours"])


class Migration(migrations.Migration):
    dependencies = [("monitoring", "0023_automation_lookback_hours")]

    operations = [
        migrations.RenameField(
            model_name="automationschedule",
            old_name="interval_days",
            new_name="interval_hours",
        ),
        migrations.RunPython(days_to_hours, hours_to_days),
        migrations.AlterField(
            model_name="automationschedule",
            name="interval_hours",
            field=models.PositiveSmallIntegerField(
                validators=[MinValueValidator(3), MaxValueValidator(168)],
                verbose_name="بازه اجرا (ساعت)",
            ),
        ),
    ]
