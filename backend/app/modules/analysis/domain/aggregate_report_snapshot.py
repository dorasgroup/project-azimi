"""Freeze report presentation facts during aggregation, before ANALYSIS_READY."""

from collections import Counter
from copy import deepcopy
from typing import Mapping, Sequence
from urllib.parse import urlsplit

REPORT_SNAPSHOT_VERSION = "1.0"


def _url(value: object) -> str:
    text = str(value or "")
    try:
        parsed = urlsplit(text)
    except ValueError:
        return ""
    return text if parsed.scheme in {"http", "https"} and parsed.hostname and not parsed.username else ""


def freeze_report_snapshot(
    summary: str,
    findings: Sequence[Mapping[str, object]],
    inputs: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    """Copy already-produced analysis facts; missing dimensions stay unavailable."""
    topics: Counter[str] = Counter()
    sentiments: Counter[str] = Counter()
    trends, contradictions = [], []
    for finding in findings:
        if isinstance(finding.get("topic"), str) and finding["topic"]:
            topics[str(finding["topic"])] += 1
        if isinstance(finding.get("sentiment"), str) and finding["sentiment"]:
            sentiments[str(finding["sentiment"])] += 1
        if finding.get("trend"):
            trends.append(deepcopy(dict(finding)))
        if finding.get("contradiction"):
            contradictions.append(deepcopy(dict(finding)))
    groups, sources, timeline, contributions = [], [], [], []
    seen_urls: set[str] = set()
    for item in inputs:
        raw = item.get("raw_result")
        raw = raw if isinstance(raw, Mapping) else {}
        source_id = str(item.get("source_id", ""))
        name = str(raw.get("source_title") or source_id)
        own_findings = [dict(f) for f in findings if str(f.get("source_id")) == source_id]
        evidence = item.get("evidence_map")
        evidence = evidence if isinstance(evidence, Mapping) else {}
        headers = raw.get("document_headers")
        headers = headers if isinstance(headers, list) else []
        by_url = {str(h.get("url")): h for h in headers if isinstance(h, dict)}
        urls = list(dict.fromkeys([
            *by_url, *evidence,
            *(str(f.get("document_url", "")) for f in own_findings),
        ]))
        articles = []
        for candidate in urls:
            url = _url(candidate)
            if not url:
                continue
            header = by_url.get(url, {})
            matching = [f for f in own_findings if f.get("document_url") == url]
            title = str(header.get("title") or next((f.get("title") for f in matching if f.get("title")), name))
            text = str(evidence.get(url) or next((f.get("explanation") for f in matching if f.get("explanation")), ""))
            published = str(header.get("published_at") or "نامشخص")
            articles.append({"title": title, "url": url, "date": published, "summary": text})
            if url not in seen_urls:
                sources.append({"title": title, "url": url, "source_name": name, "published_at_jalali": published})
                seen_urls.add(url)
                if header.get("published_at"):
                    timeline.append({"date": published, "description": title})
        groups.append({"source_name": name, "articles": articles})
        contributions.append({
            "source_id": source_id, "source_name": name,
            "source_analysis_result_id": str(item.get("source_analysis_result_id", "")),
            "summary": str(item.get("summary", ""))[:8000],
            "finding_count": len(own_findings), "evidence_count": len(articles),
        })
    key_findings = [
        str(f.get("explanation") or f.get("insight") or f.get("title") or "")
        for f in findings
    ]
    content = {
        "executive_summary": {"text": summary, "metrics": []},
        "timeline": sorted(timeline, key=lambda row: (row["date"], row["description"])),
        "sources_analysis": groups,
        "cognitive_analysis": {
            "sub_sections": [
                *({"title": c["source_name"], "content": c["summary"]} for c in contributions),
                {"title": "احساس کلی", "content": "، ".join(f"{key}: {value}" for key, value in sorted(sentiments.items())) or "دادهٔ احساس در خروجی تحلیل منابع موجود نیست."},
                {"title": "توزیع موضوعات", "content": "، ".join(f"{key}: {value}" for key, value in sorted(topics.items())) or "دادهٔ توزیع موضوعات در خروجی تحلیل منابع موجود نیست."},
                {"title": "روندها", "content": "\n".join(str(f.get("trend")) for f in trends) or "روند ساختاریافته‌ای در خروجی تحلیل منابع ثبت نشده است."},
                {"title": "تناقض‌ها", "content": "\n".join(str(f.get("contradiction")) for f in contradictions) or "تناقض ساختاریافته‌ای در خروجی تحلیل منابع ثبت نشده است."},
            ],
            "key_findings": [f for f in key_findings if f],
        },
        "future_analysis": {"scenarios": [], "key_variables": [{"name": "وضعیت داده", "description": "خروجی تجمیع حاوی سناریوی آینده‌نگری نیست."}]},
        "conclusion": summary,
    }
    return {
        "snapshot_version": REPORT_SNAPSHOT_VERSION,
        "document": content, "sources": sources,
        "overall_sentiment": {"status": "available" if sentiments else "unavailable", "distribution": dict(sorted(sentiments.items()))},
        "topic_distribution": dict(sorted(topics.items())),
        "trends": trends, "contradictions": contradictions,
        "source_contributions": contributions,
        "availability": {
            "overall_sentiment": bool(sentiments), "topic_distribution": bool(topics),
            "trends": bool(trends), "contradictions": bool(contradictions),
            "future_analysis": False,
        },
    }
