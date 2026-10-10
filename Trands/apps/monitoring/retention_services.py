from datetime import timedelta

from django.db.models import Q
from django.utils import timezone

from .models import ContentItem, SystemSetting, TrendCluster


def retention_candidates(*, setting=None):
    setting=setting or SystemSetting.get_solo()
    cutoff=timezone.now()-timedelta(days=setting.retention_days)
    protected=[TrendCluster.Status.CANDIDATE,TrendCluster.Status.TRENDING,TrendCluster.Status.CONFIRMED,TrendCluster.Status.SUPPRESSED]
    return ContentItem.objects.filter(published_at__lt=cutoff).exclude(cluster_memberships__cluster__status__in=protected).exclude(cluster_memberships__cluster__deliveries__isnull=False).distinct()


def cleanup_retention(*, batch_size=200, dry_run=False):
    candidates=retention_candidates()
    if dry_run: return candidates.count()
    count=0
    while True:
        ids=list(candidates.values_list("pk",flat=True)[:batch_size])
        if not ids: break
        deleted,_=ContentItem.objects.filter(pk__in=ids).delete();count+=deleted
    return count
