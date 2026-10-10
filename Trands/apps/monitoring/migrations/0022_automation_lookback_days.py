from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("monitoring", "0021_ideadelivery_content_deduplication")]

    operations = [
        migrations.AddField(
            model_name="automationschedule",
            name="lookback_days",
            field=models.PositiveSmallIntegerField(
                default=1,
                validators=[MinValueValidator(1), MaxValueValidator(7)],
                verbose_name="بازه محتوای رصد (روز)",
            ),
        ),
        migrations.AddField(
            model_name="automationrun",
            name="lookback_days",
            field=models.PositiveSmallIntegerField(
                default=1,
                validators=[MinValueValidator(1), MaxValueValidator(7)],
                verbose_name="بازه محتوای رصد (روز)",
            ),
        ),
    ]
