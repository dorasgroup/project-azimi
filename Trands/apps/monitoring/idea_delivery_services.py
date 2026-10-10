"""Idempotent outbox and status synchronization for the Idea Platform."""

import hashlib
import re
from datetime import timedelta
from urllib.parse import urlparse

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from .idea_client import IdeaClient, IdeaClientError
from .models import ClusterItem, IdeaDelivery, TrendCluster

CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
ACTIVE_DIFY_STATUSES = {
    "queued",
    "processing",
    "document_created",
    "indexing",
    "retrying",
}


def _safe_text(value, limit):
    clean = CONTROL_RE.sub("", value or "").strip()
    return clean[:limit]


def _safe_url(value):
    parsed = urlparse(value or "")
    return value if parsed.scheme in {"http", "https"} and parsed.netloc else None


def eligible_idea_clusters():
    return TrendCluster.objects.filter(
        status__in=[TrendCluster.Status.TRENDING, TrendCluster.Status.CONFIRMED],
        merged_into__isnull=True,
    )


def build_idea_payload(cluster):
    cluster = TrendCluster.objects.select_related("topic", "representative").get(pk=cluster.pk)
    representative_text = _safe_text(
        cluster.representative.raw_text if cluster.representative else "",
        100_000,
    )
    title = _safe_text(representative_text, 500) or f"ترند {cluster.topic.name}"
    summary = representative_text or (
        f"ترند شناسایی‌شده در موضوع {cluster.topic.name} با امتیاز {cluster.score}"
    )
    memberships = (
        ClusterItem.objects.filter(cluster=cluster)
        .select_related("content_item__source")
        .order_by("content_item__published_at", "pk")[:50]
    )
    items = []
    for membership in memberships:
        item = membership.content_item
        source = item.source
        text = _safe_text(item.raw_text or item.normalized_text, 20_000)
        if not text:
            continue
        items.append(
            {
                "external_id": str(item.external_id)[:200],
                "source_name": _safe_text(
                    source.display_name or source.discovered_name or source.identifier,
                    255,
                )
                or "منبع ناشناس",
                "source_url": _safe_url(item.public_url) or _safe_url(source.public_url),
                "published_at": item.published_at.isoformat(),
                "text": text,
            }
        )
    return {
        "source_system": settings.IDEA_SOURCE_SYSTEM,
        "source_trend_id": str(cluster.pk),
        "source_version": 1,
        "title": title,
        "topic": _safe_text(cluster.topic.name, 255),
        "score": float(cluster.score),
        "detected_at": cluster.created_at.isoformat(),
        "status": cluster.status,
        "summary": summary,
        "items": items,
    }


def _content_hash(payload):
    representative_text = " ".join(str(payload.get("summary") or "").split()).casefold()
    if not representative_text:
        representative_text = " ".join(str(payload.get("title") or "").split()).casefold()
    content = representative_text.encode("utf-8")
    return hashlib.sha256(content).hexdigest()


def queue_idea_deliveries(*, clusters=None):
    targets = list(clusters) if clusters is not None else list(eligible_idea_clusters())
    created_ids = []
    for cluster in targets:
        if cluster.status not in {
            TrendCluster.Status.TRENDING,
            TrendCluster.Status.CONFIRMED,
        } or cluster.merged_into_id:
            continue
        payload = build_idea_payload(cluster)
        idempotency_key = f"trend-monitor:cluster:{cluster.pk}:v1"
        content_hash = _content_hash(payload)
        defaults = {
            "trend_cluster": cluster,
            "source_system": settings.IDEA_SOURCE_SYSTEM,
            "source_trend_id": str(cluster.pk),
            "source_version": 1,
            "content_hash": content_hash,
            "title": payload["title"],
            "topic_name": payload["topic"],
            "score": payload["score"],
            "detected_at": cluster.created_at,
            "payload": payload,
        }
        try:
            with transaction.atomic():
                if IdeaDelivery.objects.filter(content_hash=content_hash).exists():
                    continue
                delivery, created = IdeaDelivery.objects.get_or_create(
                    idempotency_key=idempotency_key,
                    defaults=defaults,
                )
        except IntegrityError:
            delivery = (
                IdeaDelivery.objects.filter(content_hash=content_hash).first()
                or IdeaDelivery.objects.get(idempotency_key=idempotency_key)
            )
            created = False
        if created:
            created_ids.append(delivery.pk)
    return created_ids


def _retry_delay(attempt_count):
    return timedelta(seconds=min(3600, 30 * (2 ** min(attempt_count, 7))))


def send_idea_delivery(delivery_id, *, client=None):
    if not settings.IDEA_INTEGRATION_ENABLED:
        return IdeaDelivery.objects.get(pk=delivery_id)
    if not settings.IDEA_API_TOKEN:
        return IdeaDelivery.objects.get(pk=delivery_id)

    now = timezone.now()
    with transaction.atomic():
        delivery = IdeaDelivery.objects.select_for_update().get(pk=delivery_id)
        if delivery.idea_status == IdeaDelivery.IdeaStatus.ACCEPTED:
            return delivery
        if (
            delivery.idea_status == IdeaDelivery.IdeaStatus.SENDING
            and delivery.last_attempt_at
            and delivery.last_attempt_at > now - timedelta(minutes=5)
        ):
            return delivery
        delivery.idea_status = IdeaDelivery.IdeaStatus.SENDING
        delivery.attempt_count += 1
        delivery.last_attempt_at = now
        delivery.next_attempt_at = None
        delivery.error_summary = ""
        delivery.save(
            update_fields=[
                "idea_status",
                "attempt_count",
                "last_attempt_at",
                "next_attempt_at",
                "error_summary",
                "updated_at",
            ]
        )

    try:
        response = (client or IdeaClient()).send_trend(
            delivery.payload,
            delivery.idempotency_key,
        )
    except IdeaClientError as exc:
        with transaction.atomic():
            delivery = IdeaDelivery.objects.select_for_update().get(pk=delivery_id)
            delivery.idea_status = IdeaDelivery.IdeaStatus.FAILED
            delivery.last_http_status = exc.status_code
            delivery.error_summary = str(exc)[:300]
            delivery.next_attempt_at = (
                timezone.now() + _retry_delay(delivery.attempt_count)
                if exc.retryable and delivery.attempt_count < settings.IDEA_MAX_ATTEMPTS
                else None
            )
            delivery.save(
                update_fields=[
                    "idea_status",
                    "last_http_status",
                    "error_summary",
                    "next_attempt_at",
                    "updated_at",
                ]
            )
            return delivery

    with transaction.atomic():
        delivery = IdeaDelivery.objects.select_for_update().get(pk=delivery_id)
        delivery.idea_status = IdeaDelivery.IdeaStatus.ACCEPTED
        delivery.dify_status = response.get("dify_status") or IdeaDelivery.DifyStatus.QUEUED
        delivery.accepted_at = timezone.now()
        delivery.next_attempt_at = timezone.now() + timedelta(seconds=30)
        delivery.remote_import_id = str(response.get("id") or "")[:64]
        delivery.remote_upload_id = str(response.get("upload_id") or "")[:64]
        delivery.remote_ingestion_job_id = str(response.get("ingestion_job_id") or "")[:64]
        delivery.dify_document_id = str(response.get("dify_document_id") or "")[:64]
        delivery.last_http_status = 202
        delivery.error_summary = ""
        delivery.save()
        return delivery


def sync_idea_delivery(delivery_id, *, client=None):
    delivery = IdeaDelivery.objects.get(pk=delivery_id)
    if not settings.IDEA_INTEGRATION_ENABLED or not settings.IDEA_API_TOKEN:
        return delivery
    if delivery.idea_status != IdeaDelivery.IdeaStatus.ACCEPTED:
        return delivery
    try:
        response = (client or IdeaClient()).get_trend_status(
            delivery.source_trend_id,
            delivery.source_version,
        )
    except IdeaClientError as exc:
        delivery.error_summary = str(exc)[:300]
        delivery.last_http_status = exc.status_code
        delivery.next_attempt_at = timezone.now() + _retry_delay(delivery.attempt_count)
        delivery.save(
            update_fields=[
                "error_summary",
                "last_http_status",
                "next_attempt_at",
                "updated_at",
            ]
        )
        return delivery

    with transaction.atomic():
        delivery = IdeaDelivery.objects.select_for_update().get(pk=delivery_id)
        dify_status = str(response.get("dify_status") or delivery.dify_status)
        delivery.dify_status = dify_status
        delivery.dify_document_id = str(response.get("dify_document_id") or "")[:64]
        delivery.error_summary = ""
        delivery.last_http_status = 200
        if dify_status in {"indexed", "completed"}:
            delivery.dify_status = IdeaDelivery.DifyStatus.INDEXED
            delivery.dify_completed_at = timezone.now()
            delivery.next_attempt_at = None
        elif dify_status in {"failed", "enqueue_failed", "indexing_failed"}:
            delivery.dify_status = IdeaDelivery.DifyStatus.FAILED
            delivery.next_attempt_at = None
        else:
            delivery.next_attempt_at = timezone.now() + timedelta(seconds=30)
        delivery.save()
        return delivery


def maintain_idea_outbox(*, send_enqueuer=None, sync_enqueuer=None):
    created_ids = queue_idea_deliveries()
    if not settings.IDEA_INTEGRATION_ENABLED or not settings.IDEA_API_TOKEN:
        return {"created": len(created_ids), "send": 0, "sync": 0}

    now = timezone.now()
    stale = now - timedelta(minutes=5)
    send_ids = list(
        IdeaDelivery.objects.filter(
            Q(idea_status=IdeaDelivery.IdeaStatus.PENDING)
            | Q(
                idea_status=IdeaDelivery.IdeaStatus.FAILED,
                next_attempt_at__isnull=False,
                next_attempt_at__lte=now,
            )
            | Q(
                idea_status=IdeaDelivery.IdeaStatus.SENDING,
                last_attempt_at__lt=stale,
            )
            | Q(
                idea_status=IdeaDelivery.IdeaStatus.SENDING,
                last_attempt_at__isnull=True,
            )
        ).values_list("pk", flat=True)[:100]
    )
    sync_ids = list(
        IdeaDelivery.objects.filter(
            idea_status=IdeaDelivery.IdeaStatus.ACCEPTED,
            dify_status__in=ACTIVE_DIFY_STATUSES,
        )
        .filter(Q(next_attempt_at__isnull=True) | Q(next_attempt_at__lte=now))
        .values_list("pk", flat=True)[:100]
    )
    for delivery_id in send_ids:
        (send_enqueuer or send_idea_delivery)(delivery_id)
    for delivery_id in sync_ids:
        (sync_enqueuer or sync_idea_delivery)(delivery_id)
    return {"created": len(created_ids), "send": len(send_ids), "sync": len(sync_ids)}
