import hashlib

from django.db import migrations, models


def fingerprint(payload):
    representative_text = " ".join(str((payload or {}).get("summary") or "").split()).casefold()
    if not representative_text:
        representative_text = " ".join(str((payload or {}).get("title") or "").split()).casefold()
    return hashlib.sha256(representative_text.encode("utf-8")).hexdigest()


def backfill_content_hashes(apps, schema_editor):
    IdeaDelivery = apps.get_model("monitoring", "IdeaDelivery")
    claimed = set()
    for delivery in IdeaDelivery.objects.order_by("created_at", "pk"):
        semantic_hash = fingerprint(delivery.payload)
        if semantic_hash in claimed:
            semantic_hash = hashlib.sha256(f"legacy-duplicate:{semantic_hash}:{delivery.pk}".encode("utf-8")).hexdigest()
        claimed.add(semantic_hash)
        delivery.content_hash = semantic_hash
        delivery.save(update_fields=["content_hash"])


class Migration(migrations.Migration):
    dependencies = [("monitoring", "0020_automation_schedule")]

    operations = [
        migrations.RunPython(backfill_content_hashes, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="ideadelivery",
            constraint=models.UniqueConstraint(
                fields=("content_hash",),
                name="unique_idea_delivery_content_hash",
            ),
        ),
    ]
