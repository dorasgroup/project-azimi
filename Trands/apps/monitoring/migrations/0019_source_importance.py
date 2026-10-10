from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("monitoring", "0018_ideadelivery")]

    operations = [
        migrations.AddField(
            model_name="source",
            name="importance",
            field=models.CharField(
                choices=[("general", "عمومی"), ("special", "خاص")],
                default="general",
                max_length=12,
                verbose_name="نوع منبع",
            ),
        ),
    ]
