from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import migrations, models


def days_to_hours(apps, schema_editor):
    for model_name in ("AutomationSchedule", "AutomationRun"):
        model = apps.get_model("monitoring", model_name)
        for item in model.objects.only("pk", "lookback_hours").iterator():
            item.lookback_hours *= 24
            item.save(update_fields=["lookback_hours"])


def hours_to_days(apps, schema_editor):
    for model_name in ("AutomationSchedule", "AutomationRun"):
        model = apps.get_model("monitoring", model_name)
        for item in model.objects.only("pk", "lookback_hours").iterator():
            item.lookback_hours = max(1, item.lookback_hours // 24)
            item.save(update_fields=["lookback_hours"])


class Migration(migrations.Migration):
    dependencies = [("monitoring", "0022_automation_lookback_days")]

    operations = [
        migrations.RenameField(
            model_name="automationschedule",
            old_name="lookback_days",
            new_name="lookback_hours",
        ),
        migrations.RenameField(
            model_name="automationrun",
            old_name="lookback_days",
            new_name="lookback_hours",
        ),
        migrations.RunPython(days_to_hours, hours_to_days),
        migrations.AlterField(
            model_name="automationschedule",
            name="lookback_hours",
            field=models.PositiveSmallIntegerField(
                default=24,
                validators=[MinValueValidator(3), MaxValueValidator(168)],
                verbose_name="بازه محتوای رصد (ساعت)",
            ),
        ),
        migrations.AlterField(
            model_name="automationrun",
            name="lookback_hours",
            field=models.PositiveSmallIntegerField(
                default=24,
                validators=[MinValueValidator(3), MaxValueValidator(168)],
                verbose_name="بازه محتوای رصد (ساعت)",
            ),
        ),
    ]
