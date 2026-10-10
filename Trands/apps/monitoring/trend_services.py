"""Deterministic, local rule-based trend analysis. No AI or external calls."""
import hashlib, math, re
from datetime import timedelta
from collections import Counter
from urllib.parse import urlparse
from django.db import transaction
from django.utils import timezone
from .models import ContentItem, Keyword, KeywordMatch, SystemSetting, Topic, TrendAnalysisRun, TrendCluster, ClusterItem
from .text_normalization import normalize_persian_text

URL_RE=re.compile(r"https?://\S+",re.I); HASH_RE=re.compile(r"#[\w\u0600-\u06ff]+")
CHANNEL_HANDLE_RE = re.compile(r"@[a-zA-Z0-9_]{3,32}")
TELEGRAM_LINK_RE = re.compile(r"https?://(?:t\.me|telegram\.me)/\S+", re.I)
NON_CONTENT_RE = re.compile(r"[^\w\s\u0600-\u06FF0-9۰-۹]", re.UNICODE)
PERSIAN_DIGITS_MAP = str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")
PERSIAN_STOPWORDS = {
    "این", "آن", "که", "با", "از", "به", "در", "برای", "را", "یا", "تا", "بر",
    "شد", "است", "بود", "یک", "های", "کرد", "گفت", "شده", "وی", "نیز", "هم",
}
SHARED_PHRASE_BOILERPLATE_RE = re.compile(
    r"دریافت\s+آخرین|آخرین\s+اخبار|اخبار\s+سایت|سایت\s+ایتا\s+بله|عضویت\s+در\s+کانال",
    re.I,
)
SOCIAL_PLATFORM_TOKENS = {"ایتا", "بله", "روبیکا", "سروش", "پلاس"}
PROMOTIONAL_FOOTER_RE = re.compile(
    r"(?:دریافت|مشاهده|پیگیری)\s+آخرین\s+اخبار\b.*$",
    re.I,
)
MACHINE_CLUSTER_STATUSES=(TrendCluster.Status.CANDIDATE,TrendCluster.Status.TRENDING)

PUNCT_RE = re.compile(r"[\u060c\u061b\u061f\u0640\u066a-\u066d!?:;.,\"\'»«\(\)\[\]\{\}\/\\|_\-*~^+=><!؟؛،\n\r\t]")

def fingerprint(text): return hashlib.sha256((text or '').encode()).hexdigest()

def clean_text_for_similarity(text: str) -> str:
    """Normalize text for cross-channel similarity by removing Telegram handles, links, decorative symbols, and punctuation."""
    if not text:
        return ""
    text = normalize_persian_text(text)
    text = PROMOTIONAL_FOOTER_RE.sub(" ", text)
    text = TELEGRAM_LINK_RE.sub(" ", text)
    text = URL_RE.sub(" ", text)
    text = CHANNEL_HANDLE_RE.sub(" ", text)
    text = text.translate(PERSIAN_DIGITS_MAP)
    text = PUNCT_RE.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()

def extract_headline(raw_text: str) -> str:
    lines = [line.strip() for line in (raw_text or "").splitlines() if line.strip()]
    return clean_text_for_similarity(lines[0]) if lines else ""

def extract_tokens(text: str) -> set[str]:
    words = re.findall(r"[\w\u0600-\u06FF]+", text)
    return {w for w in words if len(w) >= 3 and w not in PERSIAN_STOPWORDS}

def jaccard_overlap(tokens_a: set[str], tokens_b: set[str]) -> float:
    if not tokens_a or not tokens_b:
        return 0.0
    intersection = len(tokens_a & tokens_b)
    union = len(tokens_a | tokens_b)
    return intersection / union if union else 0.0

def find_common_phrase(s1: str, s2: str, min_len: int = 14) -> str:
    words = s1.split()
    for n in range(6, 1, -1):
        for i in range(len(words) - n + 1):
            phrase = " ".join(words[i:i + n])
            phrase_tokens = set(re.findall(r"[\w\u0600-\u06FF]+", phrase))
            is_boilerplate = SHARED_PHRASE_BOILERPLATE_RE.search(phrase) or len(phrase_tokens & SOCIAL_PLATFORM_TOKENS) >= 2
            if len(phrase) >= min_len and phrase in s2 and not is_boilerplate:
                return phrase
    return ""

def grams(text,n=3):
    text=(text or '').replace(' ','')
    return Counter(text[i:i+n] for i in range(max(0,len(text)-n+1)))

def similarity(a,b):
    a,b=grams(a),grams(b); dot=sum(a[k]*b.get(k,0) for k in a); den=math.sqrt(sum(x*x for x in a.values())*sum(x*x for x in b.values()))
    return dot/den if den else 0

def match_keyword(text,k):
    phrase=normalize_persian_text(k.phrase).casefold()
    norm_text=normalize_persian_text(text or '').casefold()
    if k.match_type==Keyword.MatchType.EXACT: return norm_text==phrase
    if k.match_type==Keyword.MatchType.HASHTAG: return phrase.lstrip('#') in {x.lstrip('#').casefold() for x in HASH_RE.findall(text or '')}
    if len(phrase) <= 3:
        pattern = rf'(?<![^\W_\u200c]){re.escape(phrase)}(?![^\W_])'
        return bool(re.search(pattern, norm_text))
    return phrase in norm_text

def is_message_matching_topics(text, topics):
    norm_text = normalize_persian_text(text or '')
    if not norm_text:
        return False
    for topic in topics:
        neg_keywords = [k for k in topic.keywords.all() if k.is_active and k.is_excluded]
        if any(match_keyword(norm_text, k) for k in neg_keywords):
            continue
        pos_keywords = [k for k in topic.keywords.all() if k.is_active and not k.is_excluded]
        if pos_keywords:
            if any(match_keyword(norm_text, k) for k in pos_keywords):
                return True
        else:
            topic_name = normalize_persian_text(topic.name).casefold()
            if topic_name and topic_name in norm_text:
                return True
    return False

def refresh_keyword_matches(items):
    count=0; active=Keyword.objects.filter(is_active=True)
    for item in items:
        valid_ids=[]
        for k in active.filter(is_excluded=False):
            if match_keyword(item.normalized_text,k):
                _,created=KeywordMatch.objects.update_or_create(content_item=item,keyword=k,defaults={"topic":k.topic,"match_type":k.match_type,"matched_phrase":k.phrase}); count+=created
                valid_ids.append(k.id)
        KeywordMatch.objects.filter(content_item=item).exclude(keyword_id__in=valid_ids).delete()
    return count

def excluded(item,topic): return any(match_keyword(item.normalized_text,k) for k in topic.keywords.filter(is_active=True,is_excluded=True))

def link(a,b,setting):
    if fingerprint(a.normalized_text)==fingerprint(b.normalized_text): return 1.0,"exact"
    clean_a = clean_text_for_similarity(a.raw_text or a.normalized_text)
    clean_b = clean_text_for_similarity(b.raw_text or b.normalized_text)
    if clean_a and clean_b and clean_a == clean_b:
        return 1.0, "exact"

    min_len = min(len(clean_a), len(clean_b))
    if min_len >= setting.trend_min_text_length:
        s = similarity(clean_a, clean_b)
        threshold = min(setting.trend_similarity_threshold, 0.75)
        if s >= threshold:
            return s, "near_duplicate"

    h_a = extract_headline(a.raw_text or a.normalized_text)
    h_b = extract_headline(b.raw_text or b.normalized_text)
    if len(h_a) >= 15 and len(h_b) >= 15:
        if h_a == h_b:
            return 0.95, "headline_match"
        h_sim = similarity(h_a, h_b)
        if h_sim >= 0.80:
            return h_sim, "headline_match"

    tokens_a = extract_tokens(clean_a)
    tokens_b = extract_tokens(clean_b)
    if len(tokens_a) >= 4 and len(tokens_b) >= 4:
        min_tokens = min(len(tokens_a), len(tokens_b))
        overlap_ratio = len(tokens_a & tokens_b) / min_tokens
        if overlap_ratio >= 0.60:
            return max(0.80, overlap_ratio), "word_overlap"

    common_phrase = find_common_phrase(clean_a, clean_b, min_len=16)
    if common_phrase:
        return 0.85, "shared_phrase"

    urls=set(URL_RE.findall(a.normalized_text)) & set(URL_RE.findall(b.normalized_text))
    if any(len(urlparse(u).netloc)>3 for u in urls): return .8,"shared_url"

    tags=set(HASH_RE.findall(a.normalized_text)) & set(HASH_RE.findall(b.normalized_text))
    if any(len(tag)>3 for tag in tags) and similarity(clean_a, clean_b)>=max(.35,setting.trend_similarity_threshold*.6):
        return .8,"shared_hashtag"

    if min_len >= 35:
        shorter, longer = (clean_a, clean_b) if len(clean_a) <= len(clean_b) else (clean_b, clean_a)
        if len(shorter) <= 100 and shorter in longer:
            return 0.85, "contained_text"
        elif len(shorter) > 100:
            lead = shorter[:45].strip()
            if len(lead) >= 30 and lead in longer:
                return 0.82, "shared_lead"

    return 0.0,""

def parse_views(val):
    if not val:
        return 0
    if isinstance(val, (int, float)):
        return int(val)
    val_str = str(val).strip().upper().replace(',', '')
    try:
        if val_str.endswith('K'):
            return int(float(val_str[:-1]) * 1000)
        if val_str.endswith('M'):
            return int(float(val_str[:-1]) * 1000000)
        if val_str.endswith('B'):
            return int(float(val_str[:-1]) * 1000000000)
        return int(float(val_str))
    except (ValueError, TypeError):
        return 0

def score_cluster(cluster,setting):
    items=[m.content_item for m in cluster.items.select_related('content_item__source__group')]
    independents={m.source.group_id and f'g{m.source.group_id}' or f's{m.source_id}' for m in items}; platforms={m.source.platform for m in items}
    general_source_ids={m.source_id for m in items if m.source.importance == m.source.Importance.GENERAL}
    has_special_source=any(m.source.importance == m.source.Importance.SPECIAL for m in items)
    span=max((cluster.last_published_at-cluster.first_published_at).total_seconds()/3600,0); matches=KeywordMatch.objects.filter(content_item__in=items,topic=cluster.topic)
    engagement=sum(parse_views((i.metadata or {}).get('view_count') or (i.metadata or {}).get('views')) for i in items)
    source_score = min(setting.trend_source_weight, len(independents)/max(setting.minimum_independent_sources, 1)*setting.trend_source_weight)
    if len(independents) >= 2:
        source_score = max(source_score, setting.trend_source_weight * 0.9)
    parts={"sources":source_score,"platforms":setting.trend_platform_weight if len(platforms)>1 else 0,"velocity":setting.trend_velocity_weight if span<=1 and len(items)>1 else 0,"keywords":min(setting.trend_keyword_weight,sum(m.keyword.weight for m in matches)/100*setting.trend_keyword_weight),"engagement":min(setting.trend_engagement_weight,math.log10(engagement+1)*2)}
    total=min(100,round(sum(parts.values()))); cluster.item_count=len(items);cluster.independent_source_count=len(independents);cluster.platforms=sorted(platforms);cluster.score=total;cluster.is_sensitive=total>=setting.sensitive_score_threshold
    if cluster.status in {TrendCluster.Status.CANDIDATE,TrendCluster.Status.TRENDING}:
        if has_special_source or len(general_source_ids) >= 3:
            cluster.status = TrendCluster.Status.TRENDING
        else:
            cluster.status = TrendCluster.Status.CANDIDATE
    source_reason = "منبع خاص" if has_special_source else f"{len(general_source_ids)} منبع عمومی"
    cluster.score_breakdown={"امتیاز":total,"دلایل":[source_reason,f"حضور در {len(platforms)} پلتفرم",f"{len(matches)} کلیدواژه منطبق"]};cluster.save()

def merge_machine_clusters(clusters, setting, run):
    """Merge active machine clusters for one topic, keeping the oldest identity."""
    cluster_ids=sorted({cluster.pk for cluster in clusters})
    if len(cluster_ids)<2:
        return TrendCluster.objects.get(pk=cluster_ids[0]), 0
    with transaction.atomic():
        locked=list(TrendCluster.objects.select_for_update().filter(
            pk__in=cluster_ids,
            status__in=MACHINE_CLUSTER_STATUSES,
            merged_into__isnull=True,
        ).order_by("created_at", "pk"))
        if not locked:
            return TrendCluster.objects.get(pk=cluster_ids[0]), 0
        primary=locked[0]
        merged=0
        for secondary in locked[1:]:
            if secondary.topic_id != primary.topic_id:
                continue
            for membership in secondary.items.select_related("content_item"):
                target, created=ClusterItem.objects.get_or_create(
                    cluster=primary,
                    content_item=membership.content_item,
                    defaults={
                        "similarity":membership.similarity,
                        "link_type":membership.link_type,
                        "is_representative":membership.content_item_id == primary.representative_id,
                    },
                )
                if not created and membership.similarity > target.similarity:
                    target.similarity=membership.similarity
                    target.link_type=membership.link_type
                    target.save(update_fields=["similarity", "link_type"])
            secondary.status=TrendCluster.Status.RESOLVED
            secondary.merged_into=primary
            secondary.last_analysis_run=run
            secondary.save(update_fields=["status", "merged_into", "last_analysis_run", "updated_at"])
            merged+=1
        member_items=list(primary.items.select_related("content_item").order_by("content_item__published_at", "content_item_id"))
        if member_items:
            primary.first_published_at=member_items[0].content_item.published_at
            primary.last_published_at=member_items[-1].content_item.published_at
            if primary.representative_id is None:
                primary.representative=member_items[0].content_item
        primary.last_analysis_run=run
        primary.save(update_fields=["first_published_at", "last_published_at", "representative", "last_analysis_run", "updated_at"])
        score_cluster(primary, setting)
        return primary, merged
def create_analysis_run(): return TrendAnalysisRun.objects.create()
def execute_analysis(run, *, topics=None, since=None):
    setting=SystemSetting.get_solo(); run.status=run.Status.RUNNING;run.started_at=timezone.now();run.save()
    if since is None:
        since=timezone.now()-timedelta(hours=setting.trend_window_hours)
    items=list(ContentItem.objects.filter(published_at__gte=since).exclude(normalized_text='').select_related('source'))
    run.content_checked_count=len(items);run.keyword_match_count=refresh_keyword_matches(items)
    scoped_topics = topics if topics is not None else Topic.objects.all()
    for cluster in TrendCluster.objects.filter(status__in=MACHINE_CLUSTER_STATUSES,merged_into__isnull=True,topic__in=scoped_topics):
        if not cluster.items.filter(content_item__published_at__gte=since).exists(): cluster.status=TrendCluster.Status.RESOLVED;cluster.save(update_fields=['status']);run.clusters_resolved_count+=1
    topic_matches = KeywordMatch.objects.filter(content_item__in=items)
    if topics is not None:
        topic_matches = topic_matches.filter(topic__in=scoped_topics)
    for topic_id in topic_matches.values_list('topic_id',flat=True).distinct():
        topic_items=[i for i in items if not excluded(i,Keyword.objects.filter(topic_id=topic_id).first().topic) and KeywordMatch.objects.filter(content_item=i,topic_id=topic_id).exists()]
        for item in topic_items:
            candidates=TrendCluster.objects.filter(topic_id=topic_id,status__in=MACHINE_CLUSTER_STATUSES,merged_into__isnull=True).prefetch_related('items__content_item')
            connected=[]
            for candidate in candidates:
                if candidate.items.filter(content_item=item).exists() or any(link(item,m.content_item,setting)[0] for m in candidate.items.all()):
                    connected.append(candidate)
            if len(connected)>1:
                existing, merged_count=merge_machine_clusters(connected,setting,run)
                run.clusters_updated_count+=merged_count
            else:
                existing=connected[0] if connected else None
            if not existing:
                existing=TrendCluster.objects.create(topic_id=topic_id,first_published_at=item.published_at,last_published_at=item.published_at,representative=item,last_analysis_run=run);run.clusters_created_count+=1
            sim,kind=link(item,existing.representative,setting);ClusterItem.objects.get_or_create(cluster=existing,content_item=item,defaults={'similarity':sim or 1,'link_type':kind or 'exact','is_representative':item_id(existing.representative_id,item.id)})
            existing.first_published_at=min(existing.first_published_at,item.published_at);existing.last_published_at=max(existing.last_published_at,item.published_at);existing.last_analysis_run=run;existing.save();score_cluster(existing,setting);run.clusters_updated_count+=1
    run.status=run.Status.SUCCESS;run.finished_at=timezone.now();run.save();return run
def item_id(a,b): return a==b
