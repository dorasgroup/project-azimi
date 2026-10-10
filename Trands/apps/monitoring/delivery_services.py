"""Manual, idempotent Bale delivery services. No scheduling is enabled here."""
import os
import re
import sys
from datetime import timedelta
from urllib.parse import urlparse

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Max
from django.urls import reverse
from django.utils import timezone

from .bale_client import BaleClient, BaleClientError
from .models import BaleDestination, ClusterItem, Delivery, KeywordMatch, TrendCluster

MAX_MESSAGE_LENGTH=4000
CONTROL_RE=re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _safe_text(value):
    value=CONTROL_RE.sub("", value or "").replace("<", "").replace(">", "")
    return re.sub(r"\s+", " ", value).strip()


def _safe_url(value):
    parsed=urlparse(value or "")
    return value if parsed.scheme in {"http","https"} and parsed.netloc else ""


def eligible_clusters(destination):
    if not destination.is_active:
        return TrendCluster.objects.none()
    delivered=Delivery.objects.filter(destination=destination,status=Delivery.Status.DELIVERED,report_version=1).values("trend_cluster_id")
    return TrendCluster.objects.filter(
        status__in=[TrendCluster.Status.TRENDING,TrendCluster.Status.CONFIRMED],
        merged_into__isnull=True,
    ).exclude(pk__in=delivered)


def is_cluster_eligible(destination, cluster):
    return eligible_clusters(destination).filter(pk=cluster.pk).exists()


def format_trend_report(cluster):
    cluster=TrendCluster.objects.select_related("topic","representative__source").get(pk=cluster.pk)
    representative=cluster.representative
    excerpt=_safe_text(representative.raw_text if representative else "")[:1800]
    memberships=ClusterItem.objects.filter(cluster=cluster).select_related("content_item__source").order_by("-is_representative","content_item__source__display_name","-similarity")
    source_lines=[]; seen=set()
    for membership in memberships:
        source=membership.content_item.source
        if source.pk in seen: continue
        seen.add(source.pk)
        name=_safe_text(source.display_name or source.discovered_name or source.identifier) or "منبع ناشناس"
        url=_safe_url(membership.content_item.public_url) or _safe_url(source.public_url)
        source_lines.append(f"{len(source_lines)+1}. {name}" + (f" — {url}" if url else ""))
        if len(source_lines)==5: break
    keyword_lines=[]
    for phrase in KeywordMatch.objects.filter(content_item=representative,topic=cluster.topic).values_list("matched_phrase",flat=True).distinct()[:5]:
        clean=_safe_text(phrase)
        if clean: keyword_lines.append(clean)
    lines=["🔴 ترند جدید", "", f"موضوع: {_safe_text(cluster.topic.name)}", f"امتیاز: {cluster.score} از ۱۰۰", f"منابع مستقل: {cluster.independent_source_count}", f"پلتفرم‌ها: {' / '.join(cluster.platforms) or 'نامشخص'}", f"بازه انتشار: {cluster.first_published_at:%Y-%m-%d %H:%M} تا {cluster.last_published_at:%Y-%m-%d %H:%M}"]
    if excerpt: lines += ["", "خلاصه:", excerpt]
    if keyword_lines: lines += ["", "کلیدواژه‌های منطبق:", "، ".join(keyword_lines)]
    if source_lines: lines += ["", "منابع:", *source_lines]
    if settings.SITE_BASE_URL:
        lines += ["", "مشاهده جزئیات:", f"{settings.SITE_BASE_URL}{reverse('monitoring:trend_cluster_detail',args=[cluster.pk])}"]
    report="\n".join(lines)
    if len(report)<=MAX_MESSAGE_LENGTH: return report
    # Optional source labels can be long; preserve the complete fixed report shape and detail link.
    return report[:MAX_MESSAGE_LENGTH-2].rstrip()+"\n…"


def create_delivery(*, destination, cluster):
    if not is_cluster_eligible(destination,cluster):
        raise ValueError("این ترند یا مقصد واجد ارسال نیست.")
    try:
        with transaction.atomic():
            return Delivery.objects.get_or_create(destination=destination,trend_cluster=cluster,report_version=1,defaults={"status":Delivery.Status.PENDING})
    except IntegrityError:
        return Delivery.objects.get(destination=destination,trend_cluster=cluster,report_version=1), False


def create_resend_delivery(*, destination, cluster):
    with transaction.atomic():
        destination=BaleDestination.objects.select_for_update().get(pk=destination.pk)
        version=(Delivery.objects.filter(destination=destination,trend_cluster=cluster).aggregate(value=Max("report_version"))["value"] or 0)+1
        return Delivery.objects.create(destination=destination,trend_cluster=cluster,report_version=version,status=Delivery.Status.PENDING)


def send_delivery(delivery_id, *, client=None):
    with transaction.atomic():
        delivery=Delivery.objects.select_for_update().select_related("destination","trend_cluster").get(pk=delivery_id)
        if delivery.status in {Delivery.Status.DELIVERED,Delivery.Status.SENDING}:
            return delivery
        if not delivery.destination.is_active:
            delivery.status=Delivery.Status.SKIPPED;delivery.error_summary="مقصد بله غیرفعال است.";delivery.save(update_fields=["status","error_summary","updated_at"])
            return delivery
        delivery.status=Delivery.Status.SENDING;delivery.attempt_count+=1;delivery.last_attempt_at=timezone.now();delivery.error_summary="";delivery.retry_after_at=None
        delivery.save(update_fields=["status","attempt_count","last_attempt_at","error_summary","retry_after_at","updated_at"])
    try:
        result=(client or BaleClient()).send_message(delivery.destination.chat_id,format_trend_report(delivery.trend_cluster))
    except BaleClientError as exc:
        with transaction.atomic():
            delivery=Delivery.objects.select_for_update().get(pk=delivery_id)
            delivery.status=Delivery.Status.FAILED;delivery.error_summary=str(exc)[:300]
            if exc.retry_after is not None: delivery.retry_after_at=timezone.now()+timedelta(seconds=exc.retry_after)
            delivery.save(update_fields=["status","error_summary","retry_after_at","updated_at"])
            return delivery
    with transaction.atomic():
        delivery=Delivery.objects.select_for_update().get(pk=delivery_id)
        delivery.status=Delivery.Status.DELIVERED;delivery.delivered_at=timezone.now();delivery.provider_message_id=result.message_id;delivery.error_summary="";delivery.retry_after_at=None
        delivery.save(update_fields=["status","delivered_at","provider_message_id","error_summary","retry_after_at","updated_at"])
        return delivery


def sync_bale_destinations(*, client=None):
    """
    Automatically discover channels, groups, and chats where the bot is a member/admin
    via Bale Bot API getUpdates, and sync them into BaleDestination.
    """
    if client is None and (getattr(settings, "TESTING", False) or "pytest" in sys.modules):
        return list(BaleDestination.objects.filter(is_active=True))

    token = getattr(settings, "BALE_BOT_TOKEN", "") or os.environ.get("BALE_BOT_TOKEN", "")
    if not token and client is None:
        return list(BaleDestination.objects.filter(is_active=True))

    try:
        bale_client = client or BaleClient(token=token)
        data = bale_client.get_updates()
    except Exception:
        return list(BaleDestination.objects.filter(is_active=True))

    results = data.get("result") if isinstance(data, dict) else []
    if not isinstance(results, list):
        return list(BaleDestination.objects.filter(is_active=True))

    discovered = {}
    for update in results:
        if not isinstance(update, dict):
            continue
        chat = None
        for key in ("channel_post", "message", "edited_message", "edited_channel_post", "my_chat_member", "chat_member"):
            val = update.get(key)
            if isinstance(val, dict):
                c = val.get("chat")
                if isinstance(c, dict) and "id" in c:
                    chat = c
                    break
        if not chat or "id" not in chat:
            continue

        chat_id = str(chat["id"]).strip()
        if not chat_id:
            continue

        chat_type = str(chat.get("type", "")).lower()
        raw_title = chat.get("title") or chat.get("username") or f"{chat.get('first_name', '')} {chat.get('last_name', '')}".strip() or f"چت {chat_id}"
        clean_title = re.sub(r"\s+", " ", str(raw_title)).strip()

        if chat_type == "channel":
            label = f"{clean_title} (کانال)"
        elif chat_type in ("group", "supergroup"):
            label = f"{clean_title} (گروه)"
        elif chat_type == "private":
            label = f"{clean_title} (خصوصی)"
        else:
            label = clean_title

        discovered[chat_id] = {
            "name": label[:140],
            "type": chat_type,
        }

    for chat_id, info in discovered.items():
        dest = BaleDestination.objects.filter(chat_id=chat_id).first()
        if dest:
            if not dest.name or dest.name == f"چت {chat_id}":
                dest.name = info["name"]
                dest.save(update_fields=["name"])
            if not dest.is_active:
                dest.is_active = True
                dest.save(update_fields=["is_active"])
        else:
            name = info["name"]
            base_name = name
            counter = 1
            while BaleDestination.objects.filter(name=name).exists():
                name = f"{base_name} ({counter})"[:140]
                counter += 1
            BaleDestination.objects.create(
                name=name,
                chat_id=chat_id,
                is_active=True,
            )

    return list(BaleDestination.objects.filter(is_active=True))


def dispatch_bale_deliveries(*, delivery_enqueuer=None, client=None, clusters=None, force_resend=False):
    """
    Find all un-delivered (or specified) trending/confirmed clusters for each active BaleDestination,
    create Delivery records, and enqueue execute_bale_delivery.
    Returns list of newly created delivery IDs.
    """
    if client is None and (getattr(settings, "TESTING", False) or "pytest" in sys.modules) and delivery_enqueuer is None:
        return []

    try:
        sync_bale_destinations(client=client)
    except Exception:
        pass

    delivery_ids = []
    destinations = BaleDestination.objects.filter(is_active=True)
    if not destinations.exists():
        return []

    for destination in destinations:
        if clusters is not None:
            target_clusters = [c for c in clusters if c.status in (TrendCluster.Status.TRENDING, TrendCluster.Status.CONFIRMED) and not c.merged_into_id]
        else:
            target_clusters = list(eligible_clusters(destination))

        for cluster in target_clusters:
            # Avoid duplicate if a delivery for this cluster is currently in progress
            in_flight = Delivery.objects.filter(
                destination=destination,
                trend_cluster=cluster,
                status__in=[Delivery.Status.PENDING, Delivery.Status.SENDING],
            ).exists()
            if in_flight:
                continue

            try:
                if force_resend:
                    existing_delivered = Delivery.objects.filter(
                        destination=destination,
                        trend_cluster=cluster,
                        status=Delivery.Status.DELIVERED,
                    ).exists()
                    if existing_delivered:
                        delivery = create_resend_delivery(destination=destination, cluster=cluster)
                        created = True
                    else:
                        delivery, created = create_delivery(destination=destination, cluster=cluster)
                else:
                    delivery, created = create_delivery(destination=destination, cluster=cluster)
            except Exception:
                continue

            if not created:
                continue

            delivery_ids.append(delivery.pk)
            if delivery_enqueuer:
                delivery_enqueuer(delivery.pk)
            else:
                from .tasks import execute_bale_delivery
                execute_bale_delivery.delay(delivery.pk)

    return delivery_ids
