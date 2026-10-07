import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { responseClockOffset, runStageLabel } from "./runPresentation";
import { loadSourceCatalogue, retainSourceSelection } from "./sourceSelection";

let apiClockOffsetMs = 0;
const apiNow = () => Date.now() + apiClockOffsetMs;

type Health = { status: "ok" | "degraded"; service: string; database: boolean };
type UserSummary = { id: string; full_name: string; username: string; email: string; role: string; is_active: boolean };
type Page<T> = { items: T[]; meta: { page: number; page_size: number; total: number; pages: number } };
type Source = {
  id: string;
  title: string;
  base_url: string;
  type: "news_site" | "rss" | "custom_page";
  is_active: boolean;
  last_status: "unknown" | "success" | "failed";
  last_status_message: string | null;
  last_checked_at: string | null;
  last_crawl_status: "unknown" | "success" | "partial" | "empty" | "blocked" | "failed";
  last_crawl_message: string | null;
  last_crawled_at: string | null;
};
type SourceCheck = {
  status: Source["last_status"];
  http_status: number | null;
  response_time_ms: number | null;
  reason: string | null;
  last_checked_at: string;
};
type BotIdentity = {
  id: string;
  title: string;
  description: string | null;
  role_definition: string;
  tone: string | null;
  analysis_style: string | null;
  cognitive_framework: string | null;
  is_active: boolean;
  is_default: boolean;
};
type JobProgress = {
  stage: string;
  percent: number;
  sources_completed: number;
  sources_total: number;
  pages_processed: number;
  documents_prepared: number;
  eta_seconds: number | null;
  message: string;
};
type Job = {
  id: string;
  topic: string;
  description?: string | null;
  status: "pending" | "crawling" | "processing" | "analyzing" | "completed" | "failed" | "cancelled";
  date_from_jalali: string;
  date_to_jalali: string;
  source_ids: string[];
  bot_identity_id: string;
  error_message: string | null;
  created_at?: string;
  started_at?: string | null;
  finished_at?: string | null;
  progress?: JobProgress | null;
};
type Report = {
  id: string;
  job_id: string;
  title: string;
  summary: string | null;
  full_report: Record<string, unknown>;
  sources: Record<string, unknown>[];
  pdf_available: boolean;
  docx_available: boolean;
  created_at: string;
};
type SystemLog = {
  id: string;
  level: "debug" | "info" | "warning" | "error" | "critical";
  module: string;
  message: string;
  context: Record<string, unknown>;
  request_id: string | null;
  job_id: string | null;
  created_at: string;
};
type ModelProvider = {
  id: string;
  name: string;
  api_key_masked: string;
  has_api_key: boolean;
  active_model: string | null;
  active_embedding_model: string | null;
  is_active: boolean;
  status: "configured" | "not_configured";
};
type ConnectionCheck = {
  name: string;
  status: "success" | "failed" | "unknown";
  message: string;
  latency_ms: number | null;
};
type ConnectionsStatus = {
  providers: ModelProvider[];
  checks: ConnectionCheck[];
};
type ProviderCatalogItem = Pick<ModelProvider, "id" | "name"> & { logo: string };
type ActiveTab = "monitor" | "sources" | "reports" | "identity" | "users" | "logs" | "connections";
type ReportFilter = "all" | "ready" | "recent";

const API_BASE = `${import.meta.env.VITE_API_URL ?? "http://localhost:8010"}/api/v1`;
const ACTIVE_TAB_KEY = "rasad_active_tab";
const activeTabValues: ActiveTab[] = ["monitor", "sources", "reports", "identity", "users", "logs", "connections"];
const providerCatalog: ProviderCatalogItem[] = [
  { id: "openai", name: "OpenAI", logo: "⚡" },
  { id: "openrouter", name: "OpenRouter", logo: "🌐" },
  { id: "deepseek", name: "DeepSeek", logo: "🧠" },
  { id: "ollama", name: "Ollama", logo: "🦙" },
  { id: "openai_api_compatible", name: "OpenAI-API-compatible", logo: "🔌" },
  { id: "gapgpt", name: "گپ جی پی تی (GapGPT)", logo: "💬" },
  { id: "avalai", name: "اول ای آی (AvalAI)", logo: "🛡️" },
  { id: "gemini", name: "Google Gemini", logo: "✨" },
];

const sourceTypeLabels: Record<Source["type"], string> = {
  news_site: "سایت خبری",
  rss: "RSS",
  custom_page: "صفحه سفارشی",
};

const sourceStatusLabels: Record<Source["last_status"], string> = {
  unknown: "اتصال نامشخص",
  success: "اتصال سالم",
  failed: "اتصال ناموفق",
};

const sourceCrawlStatusLabels: Record<Source["last_crawl_status"], string> = {
  unknown: "هنوز خزش نشده",
  success: "خزش موفق",
  partial: "خزش ناقص",
  empty: "بدون نتیجه مرتبط",
  blocked: "خزش مسدود",
  failed: "خزش ناموفق",
};

const jobStatusLabels: Record<Job["status"], string> = {
  pending: "در صف",
  crawling: "در حال خزش",
  processing: "در حال آماده‌سازی",
  analyzing: "در حال تحلیل",
  completed: "تکمیل‌شده",
  failed: "ناموفق",
  cancelled: "لغوشده",
};

const tabLabels: Array<{ id: ActiveTab; label: string }> = [
  { id: "monitor", label: "رصد تازه" },
  { id: "sources", label: "منابع" },
  { id: "reports", label: "گزارش‌ها" },
  { id: "identity", label: "هویت تحلیلگر" },
  { id: "users", label: "کاربران" },
];

function renderTabIcon(id: ActiveTab) {
  switch (id) {
    case "monitor":
      return (
        <svg className="tab-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M22 12h-4l-3 9L9 3l-3 9H2"/>
        </svg>
      );
    case "sources":
      return (
        <svg className="tab-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M4 22h16a2 2 0 0 0 2-2V4a2 2 0 0 0-2-2H8a2 2 0 0 0-2 2v16a2 2 0 0 1-2 2Zm0 0a2 2 0 0 1-2-2v-9c0-1.1.9-2 2-2h2"/>
          <path d="M18 14h-8M15 18h-5M10 6h8v4h-8V6Z"/>
        </svg>
      );
    case "reports":
      return (
        <svg className="tab-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M14.5 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7.5L14.5 2z"/>
          <polyline points="14 2 14 8 20 8"/>
          <line x1="16" y1="13" x2="8" y2="13"/>
          <line x1="16" y1="17" x2="8" y2="17"/>
          <polyline points="10 9 9 9 8 9"/>
        </svg>
      );
    case "identity":
      return (
        <svg className="tab-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <rect x="3" y="11" width="18" height="10" rx="2"/>
          <circle cx="12" cy="5" r="2"/>
          <path d="M12 7v4M8 16h.01M16 16h.01"/>
          <path d="M6 11V9a4 4 0 0 1 8 0v2"/>
        </svg>
      );
    case "users":
      return (
        <svg className="tab-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/>
          <circle cx="9" cy="7" r="4"/>
          <path d="M23 21v-2a4 4 0 0 0-3-3.87"/>
          <path d="M16 3.13a4 4 0 0 1 0 7.75"/>
        </svg>
      );
    default:
      return null;
  }
}

const jalaliMonthNames = [
  "فروردین",
  "اردیبهشت",
  "خرداد",
  "تیر",
  "مرداد",
  "شهریور",
  "مهر",
  "آبان",
  "آذر",
  "دی",
  "بهمن",
  "اسفند",
];

class ApiRequestError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
  }
}

function normalizeErrorText(text: string, fallback: string) {
  if (!text) return fallback;
  try {
    const payload = JSON.parse(text) as { detail?: unknown; error?: { message?: string } };
    if (typeof payload.error?.message === "string") return payload.error.message;
    if (typeof payload.detail === "string") return payload.detail;
    return JSON.stringify(payload.detail ?? payload);
  } catch {
    return text;
  }
}

function toPersianDate(value: string | null | undefined) {
  if (!value) return "ثبت نشده";
  return new Intl.DateTimeFormat("fa-IR-u-ca-persian-nu-arab", {
    calendar: "persian",
    numberingSystem: "arab",
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

function toPersianDateOnly(value: string | null | undefined) {
  if (!value) return "";
  return new Intl.DateTimeFormat("fa-IR-u-ca-persian-nu-arab", {
    calendar: "persian",
    numberingSystem: "arab",
    dateStyle: "medium",
  }).format(new Date(value));
}

function localizeGregorianDates(value: string) {
  return value.replace(/\b(\d{4})-(\d{2})-(\d{2})\b/g, (match, year, month, day) => {
    const date = new Date(Date.UTC(Number(year), Number(month) - 1, Number(day), 12));
    if (Number.isNaN(date.getTime())) return match;
    return toPersianDateOnly(date.toISOString()) || match;
  });
}

function toPersianDigits(value: string | number): string {
  const persianDigits = ["۰", "۱", "۲", "۳", "۴", "۵", "۶", "۷", "۸", "۹"];
  return String(value).replace(/\d/g, (d) => persianDigits[Number(d)]);
}

function getJobStartTime(job: Job): number | null {
  const val = job.started_at || job.created_at;
  if (!val) return null;
  const ms = new Date(val).getTime();
  return Number.isNaN(ms) ? null : ms;
}

function getJobEndTime(job: Job): number | null {
  if (!job.finished_at) return null;
  const ms = new Date(job.finished_at).getTime();
  return Number.isNaN(ms) ? null : ms;
}

function formatTimerDigits(ms: number): string {
  const totalSeconds = Math.max(0, Math.floor(ms / 1000));
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  const pad = (n: number) => String(n).padStart(2, "0");

  if (hours > 0) {
    return `${toPersianDigits(pad(hours))}:${toPersianDigits(pad(minutes))}:${toPersianDigits(pad(seconds))}`;
  }
  return `${toPersianDigits(pad(minutes))}:${toPersianDigits(pad(seconds))}`;
}

function formatDurationHuman(ms: number): string {
  const totalSeconds = Math.max(0, Math.floor(ms / 1000));
  if (totalSeconds < 60) {
    return `${toPersianDigits(totalSeconds)} ثانیه`;
  }
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;

  const parts: string[] = [];
  if (hours > 0) parts.push(`${toPersianDigits(hours)} ساعت`);
  if (minutes > 0) parts.push(`${toPersianDigits(minutes)} دقیقه`);
  if (seconds > 0 && hours === 0) parts.push(`${toPersianDigits(seconds)} ثانیه`);
  return parts.join(" و ") || `${toPersianDigits(0)} ثانیه`;
}

function formatEta(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "در حال برآورد";
  if (seconds < 60) return "کمتر از یک دقیقه";
  return `حدود ${toPersianDigits(Math.ceil(seconds / 60))} دقیقه`;
}

function statusTone(value: string) {
  if (["success", "completed"].includes(value)) return "success";
  if (["failed", "cancelled"].includes(value)) return "danger";
  if (["unknown", "pending"].includes(value)) return "neutral";
  return "warning";
}

function padDatePart(value: number) {
  return String(value).padStart(2, "0");
}

function parseJalaliDate(value: string) {
  let defaultYear = 1405;
  let defaultMonth = 4;
  let defaultDay = 9;

  try {
    const parts = new Intl.DateTimeFormat("en-US-u-ca-persian-nu-latn", {
      calendar: "persian",
      year: "numeric",
      month: "numeric",
      day: "numeric"
    }).formatToParts(new Date());

    defaultYear = Number(parts.find(p => p.type === 'year')?.value) || 1405;
    defaultMonth = Number(parts.find(p => p.type === 'month')?.value) || 4;
    defaultDay = Number(parts.find(p => p.type === 'day')?.value) || 9;
  } catch {
    // fallback
  }

  if (!value) {
    return { year: defaultYear, month: defaultMonth, day: defaultDay };
  }
  const parts = value.split("/");
  const year = Number(parts[0]);
  const month = Number(parts[1]);
  const day = Number(parts[2]);
  return {
    year: Number.isInteger(year) && year > 0 ? year : defaultYear,
    month: Number.isInteger(month) && month >= 1 && month <= 12 ? month : defaultMonth,
    day: Number.isInteger(day) && day >= 1 && day <= 31 ? day : defaultDay,
  };
}

function formatJalaliDate(year: number, month: number, day: number) {
  return `${year}/${padDatePart(month)}/${padDatePart(day)}`;
}

function jalaliMonthLength(year: number, month: number) {
  if (month <= 6) return 31;
  if (month <= 11) return 30;
  return ((year + 1) % 4 === 0) ? 30 : 29;
}

function jalaliToGregorian(jy: number, jm: number, jd: number): Date {
  const jy_norm = jy - 979;
  const jm_norm = jm - 1;
  const jd_norm = jd - 1;

  let j_day_no = 365 * jy_norm + Math.floor(jy_norm / 33) * 8 + Math.floor(((jy_norm % 33) + 3) / 4);
  for (let i = 0; i < jm_norm; ++i) {
    j_day_no += (i < 6) ? 31 : 30;
  }
  j_day_no += jd_norm;

  let g_day_no = j_day_no + 79;

  let gy = 1600 + 400 * Math.floor(g_day_no / 146097);
  g_day_no = g_day_no % 146097;

  let leap = true;
  if (g_day_no >= 36525) {
    g_day_no--;
    gy += 100 * Math.floor(g_day_no / 36524);
    g_day_no = g_day_no % 36524;

    if (g_day_no >= 365) {
      g_day_no++;
    } else {
      leap = false;
    }
  }

  gy += 4 * Math.floor(g_day_no / 1461);
  g_day_no = g_day_no % 1461;

  if (g_day_no >= 366) {
    leap = false;
    g_day_no--;
    gy += Math.floor(g_day_no / 365);
    g_day_no = g_day_no % 365;
  }

  const sal_g = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  let i = 0;
  while (i < 12 && g_day_no >= sal_g[i]) {
    g_day_no -= sal_g[i];
    i++;
  }
  const gm = i + 1;
  const gd = g_day_no + 1;

  return new Date(gy, gm - 1, gd);
}

function reportHighlights(report: Report | null) {
  if (!report) return [];
  const candidates = [
    report.full_report?.key_findings,
    report.full_report?.findings,
    report.full_report?.recommendations,
    report.full_report?.narratives,
  ];
  for (const candidate of candidates) {
    if (Array.isArray(candidate) && candidate.length > 0) {
      return candidate.slice(0, 6).map((item) => {
        if (typeof item === "string") return item;
        if (item && typeof item === "object") {
          const value = item as Record<string, unknown>;
          return String(value.title ?? value.name ?? value.summary ?? value.description ?? JSON.stringify(value));
        }
        return String(item);
      });
    }
  }
  return [];
}

function reportTitleParts(title: string) {
  const localizedTitle = localizeGregorianDates(title);

  // 1. Remove range in parentheses at the end if present
  const suffix = localizedTitle.match(/\s*\(([^()]*)\)\s*$/u);
  const range = suffix?.[1]?.trim() ?? "";
  const isDateRange = /(?:تا|الی)/u.test(range) && /[۰-۹0-9]{4}/u.test(range);

  let cleanTitle = localizedTitle;
  let finalRange = "";

  if (suffix && isDateRange) {
    cleanTitle = localizedTitle.slice(0, suffix.index).trim();
    finalRange = range.replace(/^بازه\s*/u, "");
  }

  // 2. Remove "در بازه زمانی [تاریخ] تا [تاریخ]" or "بازه [تاریخ] تا [تاریخ]" from the title text
  const midRangeRegex = /\s*(?:در\s*)?بازه\s*(?:زمانی\s*)?[۰-۹0-9]{4}\/[۰-۹0-9]{2}\/[۰-۹0-9]{2}\s*(?:تا|الی)\s*[۰-۹0-9]{4}\/[۰-۹0-9]{2}\/[۰-۹0-9]{2}\s*/u;
  const matchMid = cleanTitle.match(midRangeRegex);
  if (matchMid) {
    if (!finalRange) {
      finalRange = matchMid[0].replace(/^(?:در\s*)?بازه\s*(?:زمانی\s*)?/u, "").trim();
    }
    cleanTitle = cleanTitle.replace(midRangeRegex, " ").replace(/\s+/g, " ").trim();
  }

  // Clean up any double colons or spaces
  cleanTitle = cleanTitle.replace(/\s*:\s*:/g, ":").replace(/^\s*:\s*/g, "").trim();

  return {
    title: cleanTitle,
    range: finalRange,
  };
}

const cognitiveLabels: Record<string, string> = {
  framing: "چارچوب‌بندی",
  dominant_narrative: "روایت غالب",
  biased_language: "زبان سوگیرانه",
  polarization: "قطبی‌سازی",
  persuasion_techniques: "تکنیک‌های اقناع",
  dominant_emotions: "هیجان‌های غالب",
  news_line: "خط خبری",
  narrative_change: "تغییر روایت",
};

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : null;
}

function asText(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "string") return localizeGregorianDates(value.trim());
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return "";
}

function asList(value: unknown): unknown[] {
  if (Array.isArray(value)) return value;
  const text = asText(value);
  return text ? [text] : [];
}

function parseReportObject(value: unknown) {
  const record = asRecord(value);
  if (record) return record;
  const text = asText(value);
  if (!text || !text.startsWith("{")) return null;
  try {
    return asRecord(JSON.parse(text));
  } catch {
    return null;
  }
}

function ReportSection({ title, children }: { title: string; children: ReactNode }) {
  return <section className="report-detail-section"><h3>{title}</h3>{children}</section>;
}

function ReportText({ value, fallback }: { value: unknown; fallback?: string }) {
  const text = asText(value);
  return <p className="long-text">{text || fallback || "اطلاعاتی برای این بخش ثبت نشده است."}</p>;
}

function ReportList({ value, fallback }: { value: unknown; fallback?: string }) {
  const items = asList(value)
    .map((item) => {
      if (typeof item === "string") return item;
      const record = asRecord(item);
      return asText(record?.title) || asText(record?.summary) || asText(record?.description) || asText(item);
    })
    .filter(Boolean);
  if (items.length === 0) return <ReportText value={fallback ?? ""} />;
  return <ul className="findings">{items.map((item) => <li key={item}>{item}</li>)}</ul>;
}

function EvidenceLinks({ value }: { value: unknown }) {
  const links = asList(value).map(asText).filter(Boolean);
  if (links.length === 0) return null;
  return <div className="evidence-links">{links.map((link) => <a key={link} href={link} target="_blank" rel="noreferrer">{link}</a>)}</div>;
}

function CognitiveAnalysis({ value }: { value: unknown }) {
  const record = parseReportObject(value);
  if (!record) return <ReportText value={value} />;
  const entries = Object.entries(record).filter(([, item]) => asText(item));
  if (entries.length === 0) return <ReportText value="" />;
  return <div className="report-kv-grid">
    {entries.map(([key, item]) => <div className="report-kv" key={key}>
      <strong>{cognitiveLabels[key] ?? key}</strong>
      <p>{asText(item)}</p>
    </div>)}
  </div>;
}

function NarrativeCards({ value }: { value: unknown }) {
  const items = asList(value).map(asRecord).filter((item): item is Record<string, unknown> => Boolean(item));
  if (items.length === 0) return <ReportText value="" />;
  return <div className="report-card-list">
    {items.map((item, index) => <article className="report-detail-card" key={`${asText(item.title)}-${index}`}>
      <strong>{asText(item.title) || `روایت ${index + 1}`}</strong>
      <p>{asText(item.description) || "شرحی برای این روایت ثبت نشده است."}</p>
      <EvidenceLinks value={item.evidence_source_urls} />
    </article>)}
  </div>;
}

function ActorCards({ value }: { value: unknown }) {
  const items = asList(value).map(asRecord).filter((item): item is Record<string, unknown> => Boolean(item));
  if (items.length === 0) return <ReportText value="" />;
  return <div className="report-card-list">
    {items.map((item, index) => <article className="report-detail-card" key={`${asText(item.name)}-${index}`}>
      <strong>{asText(item.name) || `بازیگر ${index + 1}`}</strong>
      {asText(item.role) && <small>{asText(item.role)}</small>}
      <p>{asText(item.portrayal) || "تصویری برای این بازیگر ثبت نشده است."}</p>
      <EvidenceLinks value={item.evidence_source_urls} />
    </article>)}
  </div>;
}

function SourceCards({ value }: { value: unknown }) {
  const items = asList(value).map(asRecord).filter((item): item is Record<string, unknown> => Boolean(item));
  if (items.length === 0) return <ReportText value="" />;
  return <div className="report-card-list compact-list">
    {items.map((item, index) => {
      const url = asText(item.url);
      return <article className="report-detail-card" key={`${url}-${index}`}>
        <strong>{asText(item.title) || asText(item.source_name) || `منبع ${index + 1}`}</strong>
        {asText(item.source_name) && <small>{asText(item.source_name)}</small>}
        {(asText(item.published_at_jalali) || asText(item.published_at)) && <small>تاریخ نشر: {asText(item.published_at_jalali) || toPersianDateOnly(asText(item.published_at))}</small>}
        {url && <a href={url} target="_blank" rel="noreferrer">{url}</a>}
      </article>;
    })}
  </div>;
}

function reportCoverageItems(fullReport: Record<string, unknown>, sources: unknown): Record<string, unknown>[] {
  const crawls = asList(fullReport.crawl_coverage).map(asRecord).filter((item): item is Record<string, unknown> => Boolean(item));
  if (crawls.length) return crawls;
  const legacy = asList(fullReport.source_coverage).map(asRecord).filter((item): item is Record<string, unknown> => Boolean(item));
  if (legacy.length) return legacy;
  const provenance = asRecord(fullReport.aggregation_provenance);
  const contributions = asList(provenance?.source_contributions).map(asRecord).filter((item): item is Record<string, unknown> => Boolean(item));
  if (contributions.length) return contributions.map((item) => ({
    source_name: item.source_name, document_count: item.evidence_count,
    count_label: "شاهد", status: "analyzed",
  }));
  const groups = new Map<string, number>();
  for (const raw of asList(sources)) {
    const item = asRecord(raw);
    if (!item) continue;
    const name = asText(item.source_name) || asText(item.title) || "منبع نامشخص";
    groups.set(name, (groups.get(name) || 0) + 1);
  }
  return Array.from(groups, ([source_name, document_count]) => ({ source_name, document_count, count_label: "ارجاع", status: "used" }));
}

function SourceCoverageCards({ value }: { value: unknown }) {
  const items = asList(value).map(asRecord).filter((item): item is Record<string, unknown> => Boolean(item));
  if (items.length === 0) {
    const coverage = asRecord(value);
    if (!coverage) return <ReportText value="" />;
    return <><p>منابع انتخابی: {toPersianDigits(Number(coverage.expected_sources) || 0)} · منابع پردازش‌شده: {toPersianDigits(Number(coverage.successful_sources) || 0)} · خطای پردازش: {toPersianDigits(Number(coverage.failed_sources) || 0)} · تحلیل موفق: {toPersianDigits(Number(coverage.analysis_successful) || 0)} · تحلیل ناموفق: {toPersianDigits(Number(coverage.analysis_failed) || 0)}</p>{asList(coverage.crawl_sources).length > 0 && <SourceCoverageCards value={coverage.crawl_sources} />}</>;
  }
  return <div className="report-card-list compact-list">
    {items.map((item, index) => {
      const used = asText(item.status) === "used";
      return <article className="report-detail-card" key={`${asText(item.source_name)}-${index}`}>
        <strong>{asText(item.source_name) || `منبع ${index + 1}`}</strong>
        <small>{item.selected_total !== undefined ? `دریافت موفق: ${toPersianDigits(Number(item.successful_pages) || 0)} از ${toPersianDigits(Number(item.selected_total) || 0)} · اسناد معتبر: ${toPersianDigits(Number(item.document_count) || 0)}` : used ? `${asText(item.document_count)} سند معتبر استفاده شد` : "بررسی شد؛ سند معتبر داخل بازه نداشت"}</small>
        {!used && asText(item.status_message) && <p>{asText(item.status_message)}</p>}
      </article>;
    })}
  </div>;
}

function FullReportDetails({ report }: { report: Report }) {
  const fullReport = (report.full_report ?? {}) as Record<string, unknown>;
  const titleParts = reportTitleParts(report.title);

  const rangeObj = fullReport.monitoring_range_jalali && typeof fullReport.monitoring_range_jalali === 'object'
    ? fullReport.monitoring_range_jalali as Record<string, unknown>
    : null;
  const monitoringRange = rangeObj
    ? `${rangeObj.from} تا ${rangeObj.to}`
    : titleParts.range;

  const coverageItems = reportCoverageItems(fullReport, report.sources);

  // Detect if this is the new 6-part format by checking if executive_summary is an object (or contains metrics/text)
  const isNewFormat = Boolean(
    fullReport.executive_summary &&
    typeof fullReport.executive_summary === "object" &&
    !Array.isArray(fullReport.executive_summary)
  );

  // Tabs for Sources Analysis
  const sourcesAnalysis = (fullReport.sources_analysis as Record<string, unknown>[]) || [];
  const [activeSourceIdx, setActiveSourceIdx] = useState(0);

  if (isNewFormat) {
    const execSummary = (fullReport.executive_summary as Record<string, unknown>) || {};
    const metrics = (execSummary.metrics as Record<string, unknown>[]) || [];
    const timeline = (fullReport.timeline as Record<string, unknown>[]) || [];
    const cognitive = (fullReport.cognitive_analysis as Record<string, unknown>) || {};
    const subSections = (cognitive.sub_sections as Record<string, unknown>[]) || [];
    const keyFindings = (cognitive.key_findings as string[]) || [];
    const future = (fullReport.future_analysis as Record<string, unknown>) || {};
    const scenarios = (future.scenarios as Record<string, unknown>[]) || [];
    const keyVariables = (future.key_variables as Record<string, unknown>[]) || [];
    const conclusion = fullReport.conclusion || "";

    return (
      <div className="report-detail-stack">
        <div className="report-detail-meta-pill-row" style={{ display: "flex", flexDirection: "column", gap: "10px", marginBottom: "20px" }}>
          {monitoringRange && (
            <div className="report-range" style={{ margin: 0 }}>
              <span>بازه رصد</span>
              <strong>{monitoringRange}</strong>
            </div>
          )}
          <div className="report-card__sources" style={{ margin: 0 }}>
            <span className="sources-label">منابع رصد:</span>
            {coverageItems.map((item, idx) => {
              const name = asText(item.source_name) || asText(item.title) || `منبع ${idx + 1}`;
              const count = typeof item.document_count === 'number' ? item.document_count : 0;
              const isUsed = count > 0;
              return (
                <span
                  key={idx}
                  className={`source-pill ${isUsed ? 'is-used' : 'is-unused'}`}
                  title={isUsed ? `${count} سند استفاده شد` : 'سند معتبر یافت نشد'}
                >
                  {name} {isUsed ? `(${count})` : ''}
                </span>
              );
            })}
          </div>
        </div>
        {/* 1. خلاصه اجرایی */}
        <ReportSection title="۱. خلاصه اجرایی">
          <ReportText value={execSummary.text || report.summary} />
          {metrics.length > 0 && (
            <div className="metrics-grid">
              {metrics.map((metric, idx) => (
                <div key={idx} className="metric-card">
                  <strong>{asText(metric.value)}</strong>
                  <span>{asText(metric.label)}</span>
                </div>
              ))}
            </div>
          )}
        </ReportSection>

        {/* 2. نقشه زمانی تحولات */}
        <ReportSection title="۲. نقشه زمانی تحولات">
          {timeline.length > 0 ? (
            <div className="timeline-container">
              {timeline.map((event, idx) => (
                <div key={idx} className="timeline-event">
                  <div className="timeline-dot" />
                  <div className="timeline-date">{asText(event.date)}</div>
                  <div className="timeline-desc">{asText(event.description)}</div>
                </div>
              ))}
            </div>
          ) : (
            <p className="long-text">رویدادی ثبت نشده است.</p>
          )}
        </ReportSection>

        {/* 3. اخبار و تحلیل‌ها از منابع */}
        <ReportSection title="۳. اخبار و تحلیل‌ها از منابع">
          {sourcesAnalysis.length > 0 ? (
            <div>
              <div className="sources-tabs">
                {sourcesAnalysis.map((group, idx) => (
                  <button
                    key={idx}
                    className={`sources-tab-btn ${activeSourceIdx === idx ? "active" : ""}`}
                    onClick={() => setActiveSourceIdx(idx)}
                  >
                    {asText(group.source_name) || `منبع ${idx + 1}`}
                  </button>
                ))}
              </div>
              <div className="sources-tab-content">
                {((sourcesAnalysis[activeSourceIdx]?.articles as Record<string, unknown>[]) || []).map((art, idx) => (
                  <article key={idx} className="article-card">
                    <h4>{asText(art.title)}</h4>
                    <div className="article-meta">
                      <span>تاریخ: {asText(art.date)}</span>
                      {asText(art.url) && (
                        <a href={asText(art.url)} target="_blank" rel="noreferrer">
                          {asText(art.url)}
                        </a>
                      )}
                    </div>
                    <p className="article-summary">{asText(art.summary)}</p>
                  </article>
                ))}
              </div>
            </div>
          ) : (
            <p className="long-text">اطلاعات منابع ثبت نشده است.</p>
          )}
        </ReportSection>

        {/* 4. تحلیل شناختی گفتمان رسانه‌ای */}
        <ReportSection title="۴. تحلیل شناختی گفتمان رسانه‌ای">
          {subSections.map((sub, idx) => (
            <div key={idx} style={{ marginBottom: "16px" }}>
              <h4 style={{ margin: "0 0 6px", fontSize: "13px", color: "var(--primary)", fontWeight: 700 }}>
                {asText(sub.title)}
              </h4>
              <p className="long-text">{asText(sub.content)}</p>
            </div>
          ))}
          {keyFindings.length > 0 && (
            <div style={{ marginTop: "14px" }}>
              <h5 style={{ margin: "0 0 8px", fontSize: "12px", color: "#1a2b4c", fontWeight: 700 }}>
                یافته‌های کلیدی شناختی:
              </h5>
              <ul className="findings">
                {keyFindings.map((finding, idx) => (
                  <li key={idx}>{asText(finding)}</li>
                ))}
              </ul>
            </div>
          )}
        </ReportSection>

        {/* 5. تحلیل آینده‌پژوهی و سناریوها */}
        <ReportSection title="۵. تحلیل آینده‌پژوهی و سناریوها">
          {scenarios.map((sc, idx) => (
            <article key={idx} className="scenario-card">
              <div className="scenario-header">
                <span className="scenario-title">
                  سناریو {idx + 1}: {asText(sc.title)}
                </span>
                <span className="scenario-prob">احتمال: {asText(sc.probability)}</span>
              </div>
              <p className="scenario-desc">{asText(sc.description)}</p>
              {asText(sc.implications) && (
                <div className="scenario-imp">
                  <strong>پیامدها:</strong> {asText(sc.implications)}
                </div>
              )}
            </article>
          ))}

          {keyVariables.length > 0 && (
            <div style={{ marginTop: "16px" }}>
              <h5 style={{ margin: "0 0 8px", fontSize: "12px", color: "#1a2b4c", fontWeight: 700 }}>
                متغیرهای کلیدی آینده:
              </h5>
              <div className="variables-list">
                {keyVariables.map((varItem, idx) => (
                  <div key={idx} className="variable-item">
                    <div className="variable-name">{asText(varItem.name)}</div>
                    <p className="variable-desc">{asText(varItem.description)}</p>
                  </div>
                ))}
              </div>
            </div>
          )}
        </ReportSection>

        {/* 6. جمع‌بندی و نتیجه‌گیری */}
        <ReportSection title="۶. جمع‌بندی و نتیجه‌گیری">
          <ReportText value={conclusion} />
        </ReportSection>

        {/* منابع کل پروژه */}
        <ReportSection title="منابع و مراجع گزارش">
          <SourceCards value={report.sources} />
        </ReportSection>
      </div>
    );
  }
  // Fallback for the old format
  return (
    <div className="report-detail-stack">
      <div className="report-detail-meta-pill-row" style={{ display: "flex", flexDirection: "column", gap: "10px", marginBottom: "20px" }}>
        {monitoringRange && (
          <div className="report-range" style={{ margin: 0 }}>
            <span>بازه رصد</span>
            <strong>{monitoringRange}</strong>
          </div>
        )}
        <div className="report-card__sources" style={{ margin: 0 }}>
          <span className="sources-label">منابع رصد:</span>
          {coverageItems.map((item, idx) => {
            const name = asText(item.source_name) || asText(item.title) || `منبع ${idx + 1}`;
            const count = typeof item.document_count === 'number' ? item.document_count : 0;
            const isUsed = count > 0;
            return (
              <span
                key={idx}
                className={`source-pill ${isUsed ? 'is-used' : 'is-unused'}`}
                title={isUsed ? `${count} ${asText(item.count_label) || 'سند'} ثبت شد` : 'دادهٔ قابل استناد در گزارش ثبت نشده است'}
              >
                {name} {isUsed ? `(${count})` : ''}
              </span>
            );
          })}
        </div>
      </div>
      <ReportSection title="دامنه رصد"><ReportText value={fullReport.monitoring_scope} /></ReportSection>
      {Boolean(fullReport.source_coverage) && <ReportSection title="پوشش منابع انتخابی"><SourceCoverageCards value={fullReport.source_coverage} /></ReportSection>}
      <ReportSection title="خلاصه مدیریتی"><ReportText value={fullReport.executive_summary ?? report.summary} /></ReportSection>
      <ReportSection title="یافته‌های کلیدی"><ReportList value={fullReport.key_findings ?? fullReport.findings} /></ReportSection>
      <ReportSection title="تحلیل شناختی"><CognitiveAnalysis value={fullReport.cognitive_analysis} /></ReportSection>
      <ReportSection title="روایت‌ها"><NarrativeCards value={fullReport.narratives} /></ReportSection>
      <ReportSection title="بازیگران"><ActorCards value={fullReport.actors} /></ReportSection>
      <ReportSection title="الگوهای رسانه‌ای"><ReportList value={fullReport.media_patterns} /></ReportSection>
      <ReportSection title="نشانه‌های احتمالی عملیات روانی"><ReportList value={fullReport.psychological_operations_signals} /></ReportSection>
      <ReportSection title="جمع‌بندی"><ReportText value={fullReport.conclusion} /></ReportSection>
      <ReportSection title="پیشنهادهای پژوهشی"><ReportList value={fullReport.research_suggestions ?? fullReport.recommendations} /></ReportSection>
      <ReportSection title="منابع"><SourceCards value={fullReport.sources ?? report.sources} /></ReportSection>
    </div>
  );
}

async function apiRequest<T>(path: string, options: RequestInit = {}, token?: string | null): Promise<T> {
  const headers = new Headers(options.headers);
  if (!(options.body instanceof FormData) && options.body !== undefined) {
    headers.set("Content-Type", "application/json");
  }
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const response = await fetch(`${API_BASE}${path}`, { ...options, headers });
  const offset = responseClockOffset(response.headers.get("Date"), Date.now());
  if (offset !== null) apiClockOffsetMs = offset;
  if (!response.ok) {
    const message = normalizeErrorText(await response.text(), `HTTP ${response.status}`);
    throw new ApiRequestError(message, response.status);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

async function downloadFile(path: string, token: string, filename: string) {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!response.ok) {
    throw new ApiRequestError(normalizeErrorText(await response.text(), `HTTP ${response.status}`), response.status);
  }
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

function Badge({ children, tone = "neutral" }: { children: ReactNode; tone?: string }) {
  return <span className={`badge badge--${tone}`}>{children}</span>;
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return <label className="field"><span>{label}</span>{children}</label>;
}

function PageHeader({ eyebrow, title, description }: { eyebrow: string; title: string; description: string }) {
  return <div className="page-header"><div>{eyebrow && <p className="eyebrow">{eyebrow}</p>}<h1>{title}</h1>{description && <p>{description}</p>}</div></div>;
}

function JalaliDatePicker({
  label,
  name,
  value,
  onChange,
}: {
  label: string;
  name: string;
  value: string;
  onChange: (value: string) => void;
}) {
  const parsedValue = parseJalaliDate(value);
  const [isOpen, setIsOpen] = useState(false);
  const [viewYear, setViewYear] = useState(parsedValue.year);
  const [viewMonth, setViewMonth] = useState(parsedValue.month);
  const days = Array.from({ length: jalaliMonthLength(viewYear, viewMonth) }, (_, index) => index + 1);

  const todayJalali = useMemo(() => {
    try {
      const parts = new Intl.DateTimeFormat("en-US-u-ca-persian-nu-latn", {
        calendar: "persian",
        year: "numeric",
        month: "numeric",
        day: "numeric"
      }).formatToParts(new Date());

      const year = Number(parts.find(p => p.type === 'year')?.value);
      const month = Number(parts.find(p => p.type === 'month')?.value);
      const day = Number(parts.find(p => p.type === 'day')?.value);
      return { year, month, day };
    } catch {
      return null;
    }
  }, []);

  const firstDayOfWeek = useMemo(() => {
    try {
      const dateOfFirst = jalaliToGregorian(viewYear, viewMonth, 1);
      return (dateOfFirst.getDay() + 1) % 7;
    } catch {
      return 0;
    }
  }, [viewYear, viewMonth]);

  function moveMonth(step: number) {
    let nextMonth = viewMonth + step;
    let nextYear = viewYear;
    if (nextMonth < 1) {
      nextYear -= 1;
      nextMonth = 12;
    } else if (nextMonth > 12) {
      nextYear += 1;
      nextMonth = 1;
    }
    setViewYear(nextYear);
    setViewMonth(nextMonth);
  }

  return <div className="field date-picker-field">
    <span>{label}</span>
    <button
      type="button"
      className="date-picker-trigger"
      onClick={() => {
        const current = parseJalaliDate(value);
        setViewYear(current.year);
        setViewMonth(current.month);
        setIsOpen((open) => !open);
      }}
    >
      <span>{value || "----/--/--"}</span>
      <small>انتخاب تاریخ</small>
    </button>
    <input type="hidden" name={name} value={value} />
    {isOpen && createPortal(
      <div className="modal-backdrop date-picker-backdrop" onClick={() => setIsOpen(false)}>
        <div className="date-picker-popover" onClick={(event) => event.stopPropagation()}>
          <div className="date-picker-head">
            <button type="button" onClick={() => moveMonth(-1)}>‹</button>
            <div className="date-picker-selects">
              <select
                value={viewMonth}
                onChange={(e) => setViewMonth(Number(e.target.value))}
                className="date-picker-select"
              >
                {jalaliMonthNames.map((name, idx) => (
                  <option key={idx} value={idx + 1}>
                    {name}
                  </option>
                ))}
              </select>
              <select
                value={viewYear}
                onChange={(e) => setViewYear(Number(e.target.value))}
                className="date-picker-select"
              >
                {Array.from({ length: 31 }, (_, idx) => 1390 + idx).map((yr) => (
                  <option key={yr} value={yr}>
                    {yr}
                  </option>
                ))}
              </select>
            </div>
            <button type="button" onClick={() => moveMonth(1)}>›</button>
          </div>
          <div className="date-picker-grid">
            {["ش", "ی", "د", "س", "چ", "پ", "ج"].map((wd) => (
              <div key={wd} className="date-picker-header-cell">
                {wd}
              </div>
            ))}
            {Array.from({ length: firstDayOfWeek }).map((_, idx) => (
              <div key={`pad-${idx}`} className="date-picker-pad-cell" />
            ))}
            {days.map((day) => {
              const option = formatJalaliDate(viewYear, viewMonth, day);
              const isToday = todayJalali &&
                viewYear === todayJalali.year &&
                viewMonth === todayJalali.month &&
                day === todayJalali.day;

              return <button
                type="button"
                key={option}
                className={`${option === value ? "is-selected" : ""} ${isToday ? "is-today" : ""}`}
                onClick={() => {
                  onChange(option);
                  setIsOpen(false);
                }}
              >
                {day}
              </button>;
            })}
          </div>
        </div>
      </div>,
      document.body
    )}
  </div>;
}

const fallbackJobStatusProgress: Record<Job["status"], number> = {
  pending: 15,
  crawling: 45,
  processing: 75,
  analyzing: 90,
  completed: 100,
  failed: 0,
  cancelled: 0,
};

export default function App() {
  const [token, setToken] = useState<string | null>("teb-sso");
  const [dateFromJalali, setDateFromJalali] = useState("");
  const [dateToJalali, setDateToJalali] = useState("");
  const [activeTab, setActiveTab] = useState<ActiveTab>(() => {
    const savedTab = localStorage.getItem(ACTIVE_TAB_KEY);
    return activeTabValues.includes(savedTab as ActiveTab) ? savedTab as ActiveTab : "monitor";
  });
  const [health, setHealth] = useState<Health | null>(null);
  const [me, setMe] = useState<UserSummary | null>(null);
  const [users, setUsers] = useState<UserSummary[]>([]);
  const [sources, setSources] = useState<Source[]>([]);
  const [identities, setIdentities] = useState<BotIdentity[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [now, setNow] = useState<number>(() => apiNow());

  const hasActiveJobs = useMemo(
    () => jobs.some((j) => ["pending", "crawling", "processing", "analyzing"].includes(j.status)),
    [jobs]
  );

  useEffect(() => {
    if (!hasActiveJobs) return;
    const interval = window.setInterval(() => {
      setNow(apiNow());
    }, 1000);
    return () => window.clearInterval(interval);
  }, [hasActiveJobs]);
  const [reports, setReports] = useState<Report[]>([]);
  const [systemLogs, setSystemLogs] = useState<SystemLog[]>([]);
  const [connections, setConnections] = useState<ConnectionsStatus | null>(null);
  const [showAddProviderModal, setShowAddProviderModal] = useState(false);
  const [pendingProviderId, setPendingProviderId] = useState<string | null>(null);
  const [installingProviderId, setInstallingProviderId] = useState<string | null>(null);
  const [installProgress, setInstallProgress] = useState(0);
  const [selectedSourceIds, setSelectedSourceIds] = useState<string[]>([]);
  const [editingJobId, setEditingJobId] = useState<string | null>(null);
  const [monitorTopic, setMonitorTopic] = useState("");
  const [monitorDescription, setMonitorDescription] = useState("");
  const [sourceQuery, setSourceQuery] = useState("");
  const [reportQuery, setReportQuery] = useState("");
  const [reportFilter, setReportFilter] = useState<ReportFilter>("all");
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [sourcePickerOpen, setSourcePickerOpen] = useState(false);
  const [userModal, setUserModal] = useState<UserSummary | "new" | null>(null);
  const [identityModal, setIdentityModal] = useState<BotIdentity | null>(null);
  const [sourceModal, setSourceModal] = useState<{ source: Source; check?: SourceCheck } | null>(null);
  const [reportModal, setReportModal] = useState<Report | null>(null);
  const [identityToDelete, setIdentityToDelete] = useState<BotIdentity | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const settingsRef = useRef<HTMLDivElement | null>(null);

  const defaultIdentity = useMemo(() => identities.find((item) => item.is_default) ?? identities[0], [identities]);
  const activeSources = useMemo(() => sources.filter((source) => source.is_active), [sources]);
  const selectedSources = useMemo(
    () => sources.filter((source) => selectedSourceIds.includes(source.id) && source.is_active),
    [selectedSourceIds, sources],
  );
  const filteredSources = useMemo(() => {
    const query = sourceQuery.trim().toLowerCase();
    if (!query) return sources;
    return sources.filter((source) => `${source.title} ${source.base_url}`.toLowerCase().includes(query));
  }, [sourceQuery, sources]);
  const filteredReports = useMemo(() => {
    const query = reportQuery.trim().toLowerCase();
    const now = Date.now();
    return reports.filter((report) => {
      const matchesQuery = !query || `${report.title} ${report.summary ?? ""}`.toLowerCase().includes(query);
      const matchesFilter =
        reportFilter === "all" ||
        (reportFilter === "ready" && (report.pdf_available || report.docx_available)) ||
        (reportFilter === "recent" && now - new Date(report.created_at).getTime() < 7 * 24 * 60 * 60 * 1000);
      return matchesQuery && matchesFilter;
    });
  }, [reportFilter, reportQuery, reports]);
  const visibleRecentJobs = useMemo(() => {
    const reportedJobIds = new Set(reports.map((report) => report.job_id));
    return jobs.filter((job) => job.status !== "completed" || reportedJobIds.has(job.id));
  }, [jobs, reports]);

  function navigateTab(tab: ActiveTab) {
    setActiveTab(tab);
    localStorage.setItem(ACTIVE_TAB_KEY, tab);
  }

  const clearSession = useCallback((text = "نشست منقضی شده است. لطفاً دوباره وارد شوید.") => {
    localStorage.removeItem("rasad_access_token");
    setToken(null);
    setMe(null);
    setUsers([]);
    setSources([]);
    setIdentities([]);
    setJobs([]);
    setReports([]);
    setSystemLogs([]);
    setConnections(null);
    setSelectedSourceIds([]);
    setMessage(text);
  }, []);

  const refreshSystemLogs = useCallback(async (currentToken = token) => {
    if (!currentToken) return;
    await apiRequest("/system/logs/resolved", { method: "DELETE" }, currentToken);
    const [errors, critical] = await Promise.all([
      apiRequest<Page<SystemLog>>("/system/logs?page_size=100&level=error", {}, currentToken),
      apiRequest<Page<SystemLog>>("/system/logs?page_size=100&level=critical", {}, currentToken),
    ]);
    setSystemLogs([...errors.items, ...critical.items].sort((left, right) => right.created_at.localeCompare(left.created_at)));
  }, [token]);

  const refreshConnections = useCallback(async (currentToken = token) => {
    if (!currentToken) return;
    const result = await apiRequest<ConnectionsStatus>("/system/connections", {}, currentToken);
    setConnections(result);
  }, [token]);

  const refreshData = useCallback(async (currentToken = token) => {
    const healthResult = await apiRequest<Health>("/health");
    setHealth(healthResult);
    if (!currentToken) return;
    try {
      const [meResult, sourceItems, identityPage, jobPage, reportPage] = await Promise.all([
        apiRequest<UserSummary>("/auth/me", {}, currentToken),
        loadSourceCatalogue<Source>(page => apiRequest<Page<Source>>(`/sources?operational_only=true&page_size=100&page=${page}`, {}, currentToken)),
        apiRequest<Page<BotIdentity>>("/bot-identities?page_size=50", {}, currentToken),
        apiRequest<Page<Job>>("/monitoring/jobs?page_size=50", {}, currentToken),
        apiRequest<Page<Report>>("/reports?page_size=50", {}, currentToken),
      ]);
      let userItems = [meResult];
      if (meResult.role === "super_admin") {
        const userPage = await apiRequest<Page<UserSummary>>("/admin/users?page_size=50", {}, currentToken);
        userItems = userPage.items;
      }
      setMe(meResult);
      setSources(sourceItems);
      setIdentities(identityPage.items);
      setJobs(jobPage.items);
      setNow(apiNow());
      setReports(reportPage.items);
      setUsers(userItems);
      if (meResult.role === "super_admin") {
        if (activeTab === "logs") await refreshSystemLogs(currentToken);
        if (activeTab === "connections") await refreshConnections(currentToken);
      }
      setSelectedSourceIds(current => retainSourceSelection(current, sourceItems));
    } catch (error) {
      if (error instanceof ApiRequestError && [401, 403].includes(error.status)) {
        clearSession();
        return;
      }
      throw error;
    }
  }, [activeTab, clearSession, refreshConnections, refreshSystemLogs, token]);

  useEffect(() => {
    localStorage.removeItem("rasad_access_token");
    if (!token) window.location.replace("/login.html");
  }, [token]);

  useEffect(() => {
    refreshData().catch((error) => setMessage(error instanceof Error ? error.message : "خواندن داده‌ها ناموفق بود"));
    const pollInterval = hasActiveJobs ? 4_000 : 10_000;
    const timer = window.setInterval(() => {
      refreshData().catch(() => undefined);
    }, pollInterval);
    return () => window.clearInterval(timer);
  }, [refreshData, hasActiveJobs]);

  useEffect(() => {
    function closeSettings(event: MouseEvent) {
      if (settingsRef.current && !settingsRef.current.contains(event.target as Node)) {
        setSettingsOpen(false);
      }
    }
    document.addEventListener("click", closeSettings);
    return () => document.removeEventListener("click", closeSettings);
  }, []);

  async function runAction(successText: string, action: () => Promise<void>, fallbackText: string) {
    setBusy(true);
    try {
      await action();
      setMessage(successText);
      await refreshData();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : fallbackText);
    } finally {
      setBusy(false);
    }
  }

  async function handleLogout() {
    try {
      await fetch("/api/logout", { method: "POST" });
    } finally {
      clearSession("از حساب خارج شدید.");
      setSettingsOpen(false);
      window.location.replace("/login.html");
    }
  }

  function toggleSource(sourceId: string) {
    setSelectedSourceIds((current) =>
      current.includes(sourceId) ? current.filter((id) => id !== sourceId) : [...current, sourceId],
    );
  }

  async function createSource(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!token) return;
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    await runAction("منبع جدید ثبت شد.", async () => {
      await apiRequest<Source>("/sources", {
        method: "POST",
        body: JSON.stringify({
          title: form.get("title"),
          base_url: form.get("base_url"),
          type: form.get("type"),
          is_active: true,
          crawl_config: {
            crawl_policy: { max_pages: 80, max_concurrency: 3, render_javascript: false },
            processing_policy: { min_text_length: 100, min_matched_score: 0.01 },
          },
        }),
      }, token);
      formElement.reset();
    }, "ثبت منبع ناموفق بود");
  }

  async function deleteSource(source: Source) {
    if (!token || !window.confirm(`منبع «${source.title}» حذف شود؟`)) return;
    await runAction("منبع حذف شد.", async () => {
      await apiRequest(`/sources/${source.id}`, { method: "DELETE" }, token);
    }, "حذف منبع ناموفق بود");
  }

  async function checkSource(source: Source) {
    if (!token) return;
    setBusy(true);
    try {
      const result = await apiRequest<SourceCheck>(`/sources/${source.id}/check`, { method: "POST" }, token);
      setSourceModal({ source, check: result });
      setMessage("بررسی اتصال منبع انجام شد.");
      await refreshData();
    } catch (error) {
      setSourceModal({ source });
      setMessage(error instanceof Error ? error.message : "بررسی منبع ناموفق بود");
    } finally {
      setBusy(false);
    }
  }

  async function createIdentity(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!token) return;
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    await runAction("هویت تحلیلگر ساخته و فعال شد.", async () => {
      const identity = await apiRequest<BotIdentity>("/bot-identities", {
        method: "POST",
        body: JSON.stringify({
          title: form.get("title"),
          description: null,
          role_definition: form.get("role_definition"),
          tone: form.get("tone"),
          analysis_style: form.get("analysis_style"),
          cognitive_framework: form.get("cognitive_framework"),
          output_structure: { format: "json" },
          source_policy: { require_citations: true },
          is_active: true,
        }),
      }, token);
      await apiRequest("/bot-identities/active", {
        method: "PUT",
        body: JSON.stringify({ identity_id: identity.id }),
      }, token);
      formElement.reset();
    }, "ثبت هویت ناموفق بود");
  }

  async function activateIdentity(identity: BotIdentity) {
    if (!token) return;
    await runAction(`هویت «${identity.title}» فعال شد.`, async () => {
      await apiRequest("/bot-identities/active", {
        method: "PUT",
        body: JSON.stringify({ identity_id: identity.id }),
      }, token);
    }, "فعال‌سازی هویت ناموفق بود");
  }

  async function updateIdentity(event: FormEvent<HTMLFormElement>, identity: BotIdentity) {
    event.preventDefault();
    if (!token) return;
    const form = new FormData(event.currentTarget);
    await runAction("هویت تحلیلگر ویرایش شد.", async () => {
      await apiRequest<BotIdentity>(`/bot-identities/${identity.id}`, {
        method: "PUT",
        body: JSON.stringify({
          title: form.get("title"),
          description: null,
          role_definition: form.get("role_definition"),
          tone: form.get("tone"),
          analysis_style: form.get("analysis_style"),
          cognitive_framework: form.get("cognitive_framework"),
          output_structure: { format: "json" },
          source_policy: { require_citations: true },
          is_active: form.get("is_active") === "true",
        }),
      }, token);
      setIdentityModal(null);
    }, "ویرایش هویت تحلیلگر ناموفق بود");
  }

  function deleteIdentity(identity: BotIdentity) {
    setDeleteError(null);
    setIdentityToDelete(identity);
  }

  async function confirmDeleteIdentity(identity: BotIdentity) {
    if (!token) return;
    setBusy(true);
    setDeleteError(null);
    try {
      await apiRequest(`/bot-identities/${identity.id}`, { method: "DELETE" }, token);
      setIdentities((prev) => prev.filter((item) => item.id !== identity.id));
      setIdentityToDelete(null);
      setIdentityModal(null);
      setMessage("هویت تحلیلگر حذف شد.");
    } catch (error) {
      setDeleteError(error instanceof Error ? error.message : "حذف هویت تحلیلگر ناموفق بود");
    } finally {
      setBusy(false);
    }
  }

  async function createJob(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!token || selectedSources.length === 0 || !defaultIdentity) {
      setMessage("برای اجرای رصد، حداقل یک منبع فعال و یک هویت تحلیلگر لازم است.");
      return;
    }
    const form = new FormData(event.currentTarget);
    const topic = form.get("topic") as string;
    const description = form.get("description") as string;
    const dateFrom = form.get("date_from_jalali") as string;
    const dateTo = form.get("date_to_jalali") as string;

    if (!topic || !dateFrom || !dateTo) {
      setMessage("لطفاً موضوع رصد و بازه تاریخی (شروع و پایان) را مشخص کنید.");
      return;
    }

    const payload = {
      topic,
      description: description || null,
      date_from_jalali: dateFrom,
      date_to_jalali: dateTo,
      source_ids: selectedSources.map((source) => source.id),
      bot_identity_id: defaultIdentity.id,
    };
    await runAction(editingJobId ? "رصد ویرایش و دوباره اجرا شد." : "رصد با موفقیت ثبت شد و در حال انجام است.", async () => {
      await apiRequest<Job>(editingJobId ? `/monitoring/jobs/${editingJobId}` : "/monitoring/jobs", {
        method: editingJobId ? "PUT" : "POST",
        body: JSON.stringify(payload),
      }, token);
      setEditingJobId(null);
      setMonitorTopic("");
      setMonitorDescription("");
      setDateFromJalali("");
      setDateToJalali("");
    }, "ساخت رصد ناموفق بود");
  }

  function editJob(job: Job) {
    setEditingJobId(job.id);
    setMonitorTopic(job.topic);
    setMonitorDescription(job.description || "");
    setDateFromJalali(job.date_from_jalali);
    setDateToJalali(job.date_to_jalali);
    const retained = retainSourceSelection(job.source_ids, sources);
    setSelectedSourceIds(retained);
    if (retained.length !== job.source_ids.length) {
      setMessage("برخی منابع قبلی آزمایشی، غیرفعال یا در دسترس فهرست عملیاتی نیستند؛ منابع رصد را دوباره انتخاب کنید.");
    }
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  async function retryJob(job: Job) {
    if (!token) return;
    if (retainSourceSelection(job.source_ids, sources).length !== job.source_ids.length) {
      editJob(job);
      return;
    }
    await runAction("رصد دوباره اجرا شد.", async () => {
      await apiRequest(`/monitoring/jobs/${job.id}/retry`, { method: "POST" }, token);
    }, "اجرای دوباره رصد ناموفق بود");
  }

  async function deleteJob(job: Job) {
    if (!token || !window.confirm(`رصد «${job.topic}» حذف شود؟`)) return;
    await runAction("رصد حذف شد.", async () => {
      await apiRequest(`/monitoring/jobs/${job.id}`, { method: "DELETE" }, token);
    }, "حذف رصد ناموفق بود");
  }

  async function cancelJob(job: Job) {
    if (!token || !window.confirm(`رصد «${job.topic}» متوقف شود؟`)) return;
    await runAction("رصد لغو شد و کارهای وابسته متوقف شدند.", async () => {
      await apiRequest(`/monitoring/jobs/${job.id}/cancel`, { method: "POST" }, token);
    }, "لغو رصد ناموفق بود");
  }

  async function createUser(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!token) return;
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    await runAction("کاربر جدید ساخته شد.", async () => {
      await apiRequest<UserSummary>("/admin/users", {
        method: "POST",
        body: JSON.stringify({
          full_name: form.get("full_name"),
          username: form.get("username"),
          email: form.get("email"),
          password: form.get("password"),
          role: form.get("role"),
          is_active: true,
        }),
      }, token);
      formElement.reset();
      setUserModal(null);
    }, "ساخت کاربر ناموفق بود");
  }

  async function updateUser(event: FormEvent<HTMLFormElement>, user: UserSummary) {
    event.preventDefault();
    if (!token) return;
    const form = new FormData(event.currentTarget);
    await runAction("اطلاعات کاربر ویرایش شد.", async () => {
      await apiRequest<UserSummary>(`/admin/users/${user.id}`, {
        method: "PUT",
        body: JSON.stringify({
          full_name: form.get("full_name"),
          email: form.get("email"),
          role: form.get("role"),
          is_active: form.get("is_active") === "true",
        }),
      }, token);
      setUserModal(null);
    }, "ویرایش کاربر ناموفق بود");
  }

  async function deleteUser(user: UserSummary) {
    if (!token || user.id === me?.id || !window.confirm(`کاربر «${user.username}» حذف شود؟`)) return;
    await runAction("کاربر حذف شد.", async () => {
      await apiRequest(`/admin/users/${user.id}`, { method: "DELETE" }, token);
    }, "حذف کاربر ناموفق بود");
  }

  async function deleteReport(report: Report) {
    if (!token || !window.confirm(`گزارش «${report.title}» حذف شود؟`)) return;
    await runAction("گزارش حذف شد.", async () => {
      await apiRequest(`/reports/${report.id}`, { method: "DELETE" }, token);
    }, "حذف گزارش ناموفق بود");
  }

  async function handleDownload(report: Report, format: "pdf" | "docx") {
    if (!token) return;
    setBusy(true);
    try {
      await downloadFile(`/reports/${report.id}/download/${format}`, token, `${report.id}.${format}`);
      setMessage(`دانلود ${format.toUpperCase()} شروع شد.`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "دانلود فایل ناموفق بود");
    } finally {
      setBusy(false);
    }
  }

  function handleInstallProvider(providerId: string) {
    setPendingProviderId(providerId);
    setShowAddProviderModal(false);
    setMessage("کلید API و نام مدل را وارد کنید، سپس «راه‌اندازی ارائه‌دهنده» را بزنید.");
  }

  async function saveProviderSettings(providerId: string, apiKey: string, activeModel: string, activeEmbeddingModel: string) {
    if (!token) return;
    await runAction("تنظیمات ارائه‌دهنده با موفقیت در پروژه ذخیره شد.", async () => {
      await apiRequest<ModelProvider>(`/system/connections/model-providers/${providerId}`, {
        method: "PUT",
        body: JSON.stringify({
          api_key: apiKey || undefined,
          active_model: activeModel || undefined,
          active_embedding_model: activeEmbeddingModel || undefined,
        }),
      }, token);
      await refreshConnections(token);
    }, "خطا در ذخیره تنظیمات ارائه‌دهنده");
  }

  async function toggleProviderActivation(providerId: string) {
    if (!token) return;
    await runAction("ارائه‌دهنده برای موتور تحلیل داخلی فعال شد.", async () => {
      const result = await apiRequest<ModelProvider[]>(`/system/connections/model-providers/${providerId}/activate`, {
        method: "POST",
      }, token);
      setConnections((current) => current ? { ...current, providers: result } : current);
    }, "خطا در فعال‌سازی ارائه‌دهنده");
  }

  async function deleteProvider(providerId: string) {
    if (!token) return;
    const provider = connections?.providers.find(p => p.id === providerId);
    const providerName = provider ? provider.name : providerId;
    if (!window.confirm(`آیا از حذف تنظیمات ارائه‌دهنده «${providerName}» اطمینان دارید؟`)) {
      return;
    }
    await runAction("ارائه‌دهنده حذف شد.", async () => {
      const result = await apiRequest<ModelProvider[]>(`/system/connections/model-providers/${providerId}`, {
        method: "DELETE",
      }, token);
      setConnections((current) => current ? { ...current, providers: result } : current);
    }, "خطا در حذف ارائه‌دهنده");
  }

  async function checkAllConnections() {
    if (!token) return;
    setBusy(true);
    try {
      const result = await apiRequest<ConnectionsStatus>("/system/connections/check", { method: "POST" }, token);
      setConnections(result);
      setMessage("بررسی اتصال‌ها انجام شد.");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "بررسی اتصال‌ها ناموفق بود");
    } finally {
      setBusy(false);
    }
  }

  function renderMonitor() {
    return <div className="page-stack">
      <PageHeader eyebrow="" title="رصد تازه" description="" />
      <div className="side-by-side-layout side-by-side-layout--monitor">
        <section>
          <form className="card command-card command-card--combined" onSubmit={createJob}>
            <div className="cyber-radar-container">
              <div className="cyber-radar-circle"></div>
              <div className="cyber-radar-sweep"></div>
            </div>
            <div className="section-title"><div><h2>{editingJobId ? "ویرایش رصد" : "فرمان رصد"}</h2>{editingJobId && <p>اطلاعات رصد خطادار را اصلاح و دوباره اجرا کنید.</p>}</div></div>
            <Field label="موضوع"><input name="topic" required value={monitorTopic} onChange={(event) => setMonitorTopic(event.target.value)} /></Field>
            <Field label="توضیحات رصد (اختیاری)"><textarea name="description" placeholder="جزئیات بیشتر، کلیدواژه‌ها یا ابعاد رصد را در این قسمت بنویسید..." value={monitorDescription} onChange={(event) => setMonitorDescription(event.target.value)} rows={3} style={{ width: "100%", padding: "10px", borderRadius: "8px", border: "1px solid rgba(0, 212, 200, 0.2)", backgroundColor: "rgba(0,0,0,0.2)", color: "#fff", resize: "vertical" }} /></Field>
            <div className="two-fields">
              <JalaliDatePicker label="از تاریخ شمسی" name="date_from_jalali" value={dateFromJalali} onChange={setDateFromJalali} />
              <JalaliDatePicker label="تا تاریخ شمسی" name="date_to_jalali" value={dateToJalali} onChange={setDateToJalali} />
            </div>
            <div className="source-selection-row">
              <button type="button" className="secondary-button" onClick={() => setSourcePickerOpen(true)}>انتخاب منابع</button>
              <div className="cyber-data-flow">
                <div className="flow-icons-stack">
                  <span className="flow-icon">📁</span>
                  <span className="flow-icon">🌐</span>
                  <span className="flow-icon">💬</span>
                </div>
                <svg className="flow-lines-svg" width="60" height="34" viewBox="0 0 60 34" fill="none">
                  <path d="M5 5 Q 30 5 50 17" stroke="rgba(0, 212, 200, 0.4)" strokeWidth="1.5" fill="none" />
                  <path d="M5 17 L 50 17" stroke="rgba(0, 212, 200, 0.6)" strokeWidth="2" fill="none" />
                  <path d="M5 29 Q 30 29 50 17" stroke="rgba(0, 212, 200, 0.4)" strokeWidth="1.5" fill="none" />
                  <circle cx="5" cy="17" r="3" fill="#00d4c8" className="flow-particle flow-particle-1" />
                  <circle cx="5" cy="5" r="2" fill="#00d4c8" className="flow-particle flow-particle-2" />
                  <circle cx="5" cy="29" r="2" fill="#00d4c8" className="flow-particle flow-particle-3" />
                </svg>
                <div className="flow-receiver-cylinder">
                  <span className="receiver-glow"></span>
                  <span className="receiver-label">Data Flow</span>
                </div>
              </div>
              <div><strong>منابع رصد</strong><p className="long-text">{selectedSources.length} منبع برای رصد نهایی گزینش شده است.</p></div>
            </div>
            <div className="actions">
              <button className="primary-button" disabled={busy || selectedSources.length === 0 || !defaultIdentity}>{editingJobId ? "ذخیره و اجرای مجدد" : "شروع رصد"}</button>
              {editingJobId && <button type="button" className="secondary-button" onClick={() => { setEditingJobId(null); setMonitorTopic(""); setMonitorDescription(""); setDateFromJalali(""); setDateToJalali(""); }} disabled={busy}>انصراف</button>}
            </div>

          </form>
        </section>
        <section className="card">
          <div className="card-heading" style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "20px" }}>
            <div><h2>رصدهای اخیر</h2></div>
            <button type="button" className="secondary-button" onClick={() => refreshData().catch((error) => setMessage(error.message))}>به‌روزرسانی</button>
          </div>
          {hasActiveJobs && (
            <div className="active-jobs-banner">
              <div className="active-jobs-banner__info">
                <span className="active-jobs-banner__pulse"></span>
                <strong>عملیات در حال اجرا:</strong>
                {jobs
                  .filter((j) => ["pending", "crawling", "processing", "analyzing"].includes(j.status))
                  .map((j) => {
                    const st = getJobStartTime(j);
                    const el = st ? Math.max(0, now - st) : 0;
                    return (
                       <span key={j.id} className="active-job-item">
                         «{j.topic}» ({runStageLabel(j.status, j.progress?.stage) ?? jobStatusLabels[j.status]}) —
                         {j.progress && <strong> {toPersianDigits(j.progress.percent)}٪ — </strong>}
                         <strong className="active-job-timer"> ⏱ {formatTimerDigits(el)}</strong>
                      </span>
                    );
                  })}
              </div>
            </div>
          )}
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>موضوع</th>
                  <th>بازه</th>
                  <th>وضعیت</th>
                  <th>جزئیات اجرا</th>
                  <th>عملیات</th>
                </tr>
              </thead>
              <tbody>
                {visibleRecentJobs.map((job) => {
                  const isRunning = ["pending", "crawling", "processing", "analyzing"].includes(job.status);
                  const startTime = getJobStartTime(job);
                  const endTime = getJobEndTime(job);
                  const elapsedMs = isRunning
                    ? (startTime ? Math.max(0, now - startTime) : 0)
                    : (startTime && endTime ? Math.max(0, endTime - startTime) : null);
                  const durationHuman = elapsedMs !== null ? formatDurationHuman(elapsedMs) : null;
                  const timerDigits = elapsedMs !== null ? formatTimerDigits(elapsedMs) : "۰۰:۰۰";

                  return (
                    <tr key={job.id}>
                      <td><strong>{job.topic}</strong></td>
                      <td>{job.date_from_jalali} تا {job.date_to_jalali}</td>
                      <td>
                        <div className="job-status-cell">
                          <div className="job-status-row">
                            {isRunning ? (
                              <>
                                <div className="job-loading-wrapper" title={runStageLabel(job.status, job.progress?.stage) ?? jobStatusLabels[job.status]}>
                                  <span
                                    className="job-loading-bar"
                                    style={{ width: `${job.progress?.percent ?? fallbackJobStatusProgress[job.status]}%` }}
                                  ></span>
                                </div>
                                <span className="job-timer-tag is-running" title="زمان سپری‌شده از شروع عملیات">
                                  <span className="timer-icon">⏱</span>
                                  <span className="timer-digits">{timerDigits}</span>
                                </span>
                              </>
                            ) : (
                              <>
                                <Badge tone={statusTone(job.status)}>{jobStatusLabels[job.status]}</Badge>
                                {durationHuman && (
                                  <span className="job-timer-tag is-finished" title={`مدت زمان کل اجرای عملیات: ${durationHuman}`}>
                                    <span className="timer-icon">⏱</span>
                                    <span className="timer-text">{durationHuman}</span>
                                  </span>
                                )}
                              </>
                            )}
                          </div>
                          {isRunning && <small className="job-status-subtext">{runStageLabel(job.status, job.progress?.stage) ?? jobStatusLabels[job.status]} — {toPersianDigits(job.progress?.percent ?? fallbackJobStatusProgress[job.status])}٪</small>}
                        </div>
                      </td>
                    <td>
                      {isRunning && job.progress ? (
                        <div className="job-progress-details">
                          <strong>{job.progress.message}</strong>
                          <div className="job-progress-metrics">
                            <span>منابع: {toPersianDigits(job.progress.sources_completed)} از {toPersianDigits(job.progress.sources_total)}</span>
                            {!job.progress.stage.startsWith("distributed_") && <span>صفحات: {toPersianDigits(job.progress.pages_processed)}</span>}
                            <span>اسناد: {toPersianDigits(job.progress.documents_prepared)}</span>
                            <span>زمان باقی‌مانده: {formatEta(job.progress.eta_seconds)}</span>
                          </div>
                        </div>
                      ) : (job.error_message ?? "-")}
                    </td>
                    <td>
                      <span className="actions">
                        <button type="button" className="text-button" onClick={() => editJob(job)} disabled={busy || !["failed", "cancelled", "pending"].includes(job.status)}>ویرایش</button>
                        <button type="button" className="text-button" onClick={() => retryJob(job)} disabled={busy || !["failed", "cancelled"].includes(job.status)}>اجرای مجدد</button>
                        {isRunning && <button type="button" className="text-button danger" onClick={() => cancelJob(job)} disabled={busy}>لغو رصد</button>}
                        <button type="button" className="text-button danger" onClick={() => deleteJob(job)} disabled={busy}>حذف</button>
                      </span>
                    </td>
                  </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          {visibleRecentJobs.length === 0 && <p className="long-text">رصد اخیری برای نمایش وجود ندارد.</p>}
          <div className="cyber-grid-floor-wrapper">
            <div className="cyber-grid-floor"></div>
          </div>

        </section>
      </div>
    </div>;
  }

  function renderSources() {
    return <div className="page-stack">
      <PageHeader eyebrow="" title="منابع خبری" description="" />
      <div className="side-by-side-layout side-by-side-layout--sources">
        <section>
          <form className="card" onSubmit={createSource}>
            <div className="section-title"><div><h2>ثبت منبع</h2></div></div>
            <Field label="عنوان منبع"><input name="title" required /></Field>
            <Field label="نشانی"><input name="base_url" dir="ltr" required /></Field>
            <Field label="نوع"><select name="type" defaultValue="news_site"><option value="news_site">سایت خبری</option><option value="rss">RSS</option><option value="custom_page">صفحه سفارشی</option></select></Field>
            <button className="primary-button" disabled={busy}>ثبت منبع</button>
          </form>
        </section>
        <section className="card">
          <div className="toolbar">
            <div className="search-box"><span>جستجو</span><input value={sourceQuery} onChange={(event) => setSourceQuery(event.target.value)} placeholder="عنوان یا نشانی منبع" /></div>
            <button type="button" className="secondary-button" onClick={() => refreshData().catch((error) => setMessage(error.message))}>به‌روزرسانی منابع</button>
          </div>
          <div className="table-wrap"><table><thead><tr><th>عنوان</th><th>نشانی</th><th>نوع</th><th>فعال‌سازی</th><th>اتصال</th><th>آخرین خزش</th><th>آخرین بررسی</th><th>عملیات</th></tr></thead><tbody>{filteredSources.map((source) => <tr key={source.id}><td><span className="source-cell"><span className="source-mark">{source.title[0]}</span><span><strong>{source.title}</strong></span></span></td><td dir="ltr">{source.base_url}</td><td>{sourceTypeLabels[source.type]}</td><td><Badge tone={source.is_active ? "success" : "neutral"}>{source.is_active ? "فعال" : "غیرفعال"}</Badge></td><td><Badge tone={statusTone(source.last_status)}>{sourceStatusLabels[source.last_status]}</Badge></td><td><div className="source-health-cell"><Badge tone={statusTone(source.last_crawl_status)}>{sourceCrawlStatusLabels[source.last_crawl_status]}</Badge><small>{toPersianDate(source.last_crawled_at)}</small>{source.last_crawl_message && <small title={source.last_crawl_message}>{source.last_crawl_message.slice(0, 180)}{source.last_crawl_message.length > 180 ? "…" : ""}</small>}</div></td><td>{toPersianDate(source.last_checked_at)}</td><td><span className="actions"><button type="button" className="secondary-button" onClick={() => checkSource(source)} disabled={busy}>بررسی اتصال</button><button type="button" className="text-button" onClick={() => setSourceModal({ source })}>جزئیات</button><button type="button" className="text-button danger" onClick={() => deleteSource(source)}>حذف</button></span></td></tr>)}</tbody></table></div>
        </section>
      </div>
    </div>;
  }

  function renderReports() {
    return <div className="page-stack">
      <PageHeader eyebrow="" title="گزارش‌ها" description="" />
      <section className="card">
        <div className="reports-toolbar">
          <div className="search-box"><span>جستجو</span><input value={reportQuery} onChange={(event) => setReportQuery(event.target.value)} placeholder="عنوان یا خلاصه گزارش" /></div>
          <div className="segmented">
            <button type="button" className={reportFilter === "all" ? "active" : ""} onClick={() => setReportFilter("all")}>همه</button>
            <button type="button" className={reportFilter === "ready" ? "active" : ""} onClick={() => setReportFilter("ready")}>دارای خروجی</button>
            <button type="button" className={reportFilter === "recent" ? "active" : ""} onClick={() => setReportFilter("recent")}>۷ روز اخیر</button>
          </div>
        </div>

        <div className="report-grid" style={{ marginTop: 18 }}>
          {filteredReports.map((report) => {
            const titleParts = reportTitleParts(report.title);
            const job = jobs.find((item) => item.id === report.job_id);
            const monitoringRange = job
              ? `${job.date_from_jalali} تا ${job.date_to_jalali}`
              : titleParts.range;

            const fullReportObj = report.full_report && typeof report.full_report === 'object' ? (report.full_report as Record<string, unknown>) : {};
            const coverageItems = reportCoverageItems(fullReportObj, report.sources);

            return <article className="report-card" key={report.id}>
              <div className="report-card__top"><span className="report-icon">گ</span><Badge tone={report.pdf_available || report.docx_available ? "success" : "warning"}>{report.pdf_available || report.docx_available ? "آماده" : "بدون فایل"}</Badge></div>
              <p className="report-date">
                {toPersianDate(report.created_at)} · {coverageItems.length} منبع
                {(() => {
                  const start = job ? getJobStartTime(job) : null;
                  const end = job ? getJobEndTime(job) : null;
                  const dur = start && end ? Math.max(0, end - start) : null;
                  return dur !== null ? (
                    <span className="report-duration-tag" title="مدت زمان کل اجرای عملیات خزش و تحلیل">
                      · ⏱ {formatDurationHuman(dur)}
                    </span>
                  ) : null;
                })()}
              </p>
              <h2>{titleParts.title}</h2>
              {monitoringRange && <p className="report-range"><span>بازه رصد</span><strong>{monitoringRange}</strong></p>}
              <div className="report-card__sources">
                <span className="sources-label">منابع رصد:</span>
                {coverageItems.map((item, idx) => {
                  const name = asText(item.source_name) || asText(item.title) || `منبع ${idx + 1}`;
                  const count = typeof item.document_count === 'number' ? item.document_count : 0;
                  const isUsed = count > 0;
                  return (
                    <span
                      key={idx}
                      className={`source-pill ${isUsed ? 'is-used' : 'is-unused'}`}
                      title={isUsed ? `${count} ${asText(item.count_label) || 'سند'} ثبت شد` : 'دادهٔ قابل استناد در گزارش ثبت نشده است'}
                    >
                      {name} {isUsed ? `(${count})` : ''}
                    </span>
                  );
                })}
              </div>
              <p className="report-card__summary">{report.summary ?? "گزارش ساختاریافته سامانه ذخیره شده است."}</p>
              <div className="tag-row">{reportHighlights(report).slice(0, 3).map((item) => <span key={item}>{item.slice(0, 42)}</span>)}</div>
              <div className="report-actions"><button type="button" className="text-button" onClick={() => setReportModal(report)}>مشاهده</button><button type="button" className="secondary-button" disabled={!report.docx_available || busy} onClick={() => handleDownload(report, "docx")}>Word</button><button type="button" className="primary-button compact" disabled={!report.pdf_available || busy} onClick={() => handleDownload(report, "pdf")}>PDF</button><button type="button" className="text-button danger" onClick={() => deleteReport(report)}>حذف</button></div>
            </article>;
          })}
        </div>
        {filteredReports.length === 0 && <p className="long-text">گزارشی مطابق جستجو یا فیلتر فعلی وجود ندارد.</p>}
      </section>
    </div>;
  }

  function renderIdentity() {
    return <div className="page-stack">
      <PageHeader eyebrow="" title="هویت تحلیلگر" description="" />
      <section className="identity-layout">
        <form className="card" onSubmit={createIdentity}>
          <div className="section-title"><div><h2>ثبت هویت جدید</h2></div></div>
          <Field label="نام هویت"><input name="title" required /></Field>
          <Field label="نقش تحلیلگر"><textarea name="role_definition" required placeholder="شرح نقش و مأموریت تحلیلگر..." /></Field>
          <div className="two-fields">
            <Field label="لحن">
              <select name="tone">
                <option value="رسمی">رسمی</option>
                <option value="بی‌طرف">بی‌طرف</option>
                <option value="تحلیلی و موشکافانه">تحلیلی و موشکافانه</option>
                <option value="انتقادی">انتقادی</option>
                <option value="آکادمیک">آکادمیک</option>
                <option value="هشداری و پیش‌بینانه">هشداری و پیش‌بینانه</option>
              </select>
            </Field>
            <Field label="سبک تحلیل">
              <select name="analysis_style">
                <option value="ساختاریافته">ساختاریافته</option>
                <option value="توصیفی و تبیینی">توصیفی و تبیینی</option>
                <option value="تفسیری">تفسیری</option>
                <option value="داده‌محور">داده‌محور</option>
                <option value="تطبیقی و مقایسه‌ای">تطبیقی و مقایسه‌ای</option>
                <option value="سناریومحور">سناریومحور</option>
              </select>
            </Field>
          </div>
          <Field label="چارچوب شناختی"><textarea name="cognitive_framework" /></Field>
          <button className="primary-button" disabled={busy}>ثبت و فعال‌سازی هویت</button>
        </form>
        <aside className="card identity-preview">
          <div className="ai-orb">AI</div>
          <p className="eyebrow">هویت فعال</p>
          <h2>{defaultIdentity?.title ?? "ثبت نشده"}</h2>
          <p>{defaultIdentity?.description ?? defaultIdentity?.role_definition ?? "هنوز هویت تحلیلی فعال ندارید."}</p>
          <div className="preview-list">
            <span>لحن: {defaultIdentity?.tone ?? "ثبت نشده"}</span>
            <span>سبک: {defaultIdentity?.analysis_style ?? "ثبت نشده"}</span>
            <span>چارچوب: {defaultIdentity?.cognitive_framework ?? "ثبت نشده"}</span>
          </div>
        </aside>
      </section>
      <section className="card">
        <div className="card-heading"><div><h2>هویت‌های موجود</h2><p>هر هویت فعال را می‌توانید برای رصدهای بعدی انتخاب کنید.</p></div></div>
        <div className="table-wrap"><table><thead><tr><th>عنوان</th><th>لحن</th><th>سبک</th><th>وضعیت</th><th>عملیات</th></tr></thead><tbody>{identities.map((identity) => <tr key={identity.id}><td><strong>{identity.title}</strong></td><td>{identity.tone ?? "-"}</td><td>{identity.analysis_style ?? "-"}</td><td><Badge tone={identity.is_default ? "success" : identity.is_active ? "warning" : "neutral"}>{identity.is_default ? "فعال اصلی" : identity.is_active ? "فعال" : "غیرفعال"}</Badge></td><td><span className="actions"><button type="button" className="secondary-button" disabled={busy || identity.is_default || !identity.is_active} onClick={() => activateIdentity(identity)}>انتخاب فعال</button><button type="button" className="text-button" onClick={() => setIdentityModal(identity)}>ویرایش</button><button type="button" className="text-button danger" disabled={busy} onClick={() => deleteIdentity(identity)}>حذف</button></span></td></tr>)}</tbody></table></div>
      </section>
    </div>;
  }

  function renderUsers() {
    const canManage = me?.role === "super_admin";
    return <div className="page-stack">
      <PageHeader eyebrow="" title="کاربران" description="" />
      <section className="card">
        <div className="card-heading"><div><h2>فهرست کاربران</h2></div>{canManage && <button type="button" className="secondary-button" onClick={() => setUserModal("new")}>افزودن کاربر</button>}</div>
        <div className="table-wrap"><table><thead><tr><th>نام</th><th>نام کاربری</th><th>ایمیل</th><th>نقش</th><th>وضعیت</th><th>عملیات</th></tr></thead><tbody>{users.map((user) => <tr key={user.id}><td><strong>{user.full_name}</strong></td><td>{user.username}</td><td>{user.email}</td><td>{user.role}</td><td><Badge tone={user.is_active ? "success" : "danger"}>{user.is_active ? "فعال" : "غیرفعال"}</Badge></td><td>{canManage ? <span className="actions"><button type="button" className="text-button" onClick={() => setUserModal(user)}>ویرایش</button>{user.id !== me?.id && <button type="button" className="text-button danger" onClick={() => deleteUser(user)}>حذف</button>}</span> : "-"}</td></tr>)}</tbody></table></div>
      </section>
    </div>;
  }

  function renderLogs() {
    const canView = me?.role === "super_admin";
    if (!canView) {
      return <div className="page-stack"><section className="card"><p className="long-text">این صفحه فقط برای مدیر ارشد قابل مشاهده است.</p></section></div>;
    }
    return <div className="page-stack">
      <PageHeader eyebrow="" title="لاگ‌ها" description="" />
      <section className="card">
        <div className="toolbar">
          <div className="card-heading"><div><h2>آخرین لاگ‌های سامانه</h2></div></div>
          <button type="button" className="secondary-button" onClick={() => refreshSystemLogs().catch((error) => setMessage(error.message))}>به‌روزرسانی لاگ‌ها</button>
        </div>
        <div className="table-wrap"><table><thead><tr><th>زمان</th><th>سطح</th><th>ماژول</th><th>پیام</th><th>Job</th><th>جزئیات</th></tr></thead><tbody>{systemLogs.map((log) => <tr key={log.id}><td>{toPersianDate(log.created_at)}</td><td><Badge tone={statusTone(log.level === "error" || log.level === "critical" ? "failed" : log.level === "warning" ? "pending" : "success")}>{log.level}</Badge></td><td>{log.module}</td><td>{log.message}</td><td dir="ltr">{log.job_id ?? "-"}</td><td><pre className="inline-json">{JSON.stringify(log.context, null, 2)}</pre></td></tr>)}</tbody></table></div>
        {systemLogs.length === 0 && <p className="long-text">هنوز لاگی در سامانه ثبت نشده است.</p>}
      </section>
    </div>;
  }

  function renderConnections() {
    const canView = me?.role === "super_admin";
    if (!canView) {
      return <div className="page-stack"><section className="card"><p className="long-text">این صفحه فقط برای مدیر ارشد قابل مشاهده است.</p></section></div>;
    }

    const configuredProviders = connections?.providers ?? [];
    const configuredList = configuredProviders.filter(p => p.status === "configured");
    const pendingDefinition = providerCatalog.find(p => p.id === pendingProviderId);
    const pendingProvider: ModelProvider | null = pendingDefinition && !configuredList.some(p => p.id === pendingDefinition.id)
      ? {
          id: pendingDefinition.id,
          name: pendingDefinition.name,
          api_key_masked: "",
          has_api_key: false,
          active_model: pendingDefinition.id === "gemini" ? "gemini-3.6-flash" : null,
          active_embedding_model: null,
          is_active: false,
          status: "not_configured",
        }
      : null;
    const displayedProviders = pendingProvider ? [...configuredList, pendingProvider] : configuredList;

    function renderProviderCard(prov: ModelProvider) {
      const logoEmoji = prov.id === "openai" ? "⚡" : prov.id === "openrouter" ? "🌐" : prov.id === "deepseek" ? "🧠" : prov.id === "ollama" ? "🦙" : prov.id === "openai_api_compatible" ? "🔌" : prov.id === "gapgpt" ? "💬" : prov.id === "avalai" ? "🛡️" : prov.id === "gemini" ? "✨" : "🤖";
      const isConfigured = prov.status === "configured";

      return (
        <form
          key={prov.id}
          className={`provider-card ${prov.is_active ? "active" : ""}`}
          onSubmit={async (e) => {
            e.preventDefault();
            const form = new FormData(e.currentTarget);
            const key = form.get("api_key") as string;
            const model = form.get("active_model") as string;
            const embeddingModel = form.get("active_embedding_model") as string;

            if (!isConfigured) {
              setInstallingProviderId(prov.id);
              setInstallProgress(0);
              const steps = 15;
              const intervalTime = 100;
              for (let i = 1; i <= steps; i++) {
                await new Promise((resolve) => setTimeout(resolve, intervalTime));
                setInstallProgress(Math.round((i / steps) * 100));
              }

              try {
                await apiRequest<ModelProvider>(`/system/connections/model-providers/${prov.id}`, {
                  method: "PUT",
                  body: JSON.stringify({ api_key: key, active_model: model, active_embedding_model: embeddingModel }),
                }, token);
                setPendingProviderId(null);
                await refreshConnections(token);
                setMessage(`ارائه‌دهنده ${prov.name} با موفقیت نصب و راه‌اندازی شد.`);
              } catch (error) {
                setMessage(error instanceof Error ? error.message : "خطا در راه‌اندازی ارائه‌دهنده");
              } finally {
                setInstallingProviderId(null);
                setInstallProgress(0);
              }
            } else {
              saveProviderSettings(prov.id, key, model, embeddingModel);
            }
          }}
        >
          {installingProviderId === prov.id ? (
            <div className="download-progress-container" style={{ padding: "20px 0" }}>
              <p className="download-status-text" style={{ fontSize: "14px", color: "var(--primary)", fontWeight: "bold", textAlign: "center" }}>
                در حال اعتبارسنجی و ذخیره تنظیمات ارائه‌دهنده {prov.name}...
              </p>
              <div className="progress-bar-wrap" style={{ height: "8px", margin: "15px 0" }}>
                <div className="progress-bar-fill" style={{ width: `${installProgress}%` }}></div>
              </div>
              <div style={{ textAlign: "center", fontWeight: "bold" }}>{installProgress}%</div>
            </div>
          ) : (
            <>
              <div className="provider-card-header">
                <div className="provider-info">
                  <span className="provider-icon-badge">{logoEmoji}</span>
                  <div>
                    <span className="provider-name-text">{prov.name}</span>
                    {prov.is_active && (
                      <span style={{ marginRight: "8px" }}><Badge tone="success">فعال برای تحلیل</Badge></span>
                    )}
                    {!isConfigured && (
                      <span style={{ marginRight: "8px" }}><Badge tone="neutral">غیرفعال / تنظیم‌نشده</Badge></span>
                    )}
                  </div>
                </div>
                <div className="provider-top-actions" style={{ display: "flex", gap: "8px" }}>
                  {isConfigured && !prov.is_active && (
                    <button
                      type="button"
                      className="text-button"
                      onClick={() => toggleProviderActivation(prov.id)}
                      disabled={busy}
                    >
                      فعال‌سازی
                    </button>
                  )}
                  {isConfigured && !prov.is_active && (
                    <button
                      type="button"
                      className="text-button danger"
                      onClick={() => deleteProvider(prov.id)}
                      disabled={busy}
                    >
                      حذف
                    </button>
                  )}
                </div>
              </div>
              <div className="provider-fields">
                <Field label="نام مدل فعال">
                  <input
                    name="active_model"
                    dir="ltr"
                    defaultValue={prov.active_model ?? ""}
                    autoComplete="off"
                    placeholder={
                      prov.id === "openai" ? "مثلاً text-embedding-3-large یا gpt-4o-mini"
                      : prov.id === "openrouter" ? "مثلاً openai/gpt-4o-mini"
                      : prov.id === "deepseek" ? "مثلاً deepseek-chat"
                      : prov.id === "ollama" ? "مثلاً llama3"
                      : prov.id === "gapgpt" ? "مثلاً gapgpt-qwen-3.6"
                      : prov.id === "avalai" ? "مثلاً gpt-4o-mini"
                      : prov.id === "gemini" ? "gemini-3.6-flash"
                      : "نام مدل را وارد کنید"
                    }
                    required
                  />
                </Field>
                {prov.id === "gemini" ? (
                  <>
                    <input type="hidden" name="active_embedding_model" value="" />
                    <p className="long-text">برای تحلیل وب با Google فقط نام مدل Gemini لازم است؛ نشانی API به‌صورت خودکار از مسیر امن Worker تنظیم می‌شود.</p>
                  </>
                ) : <Field label="مدل Embedding (اختیاری)">
                  <input
                    name="active_embedding_model"
                    dir="ltr"
                    defaultValue={prov.active_embedding_model ?? ""}
                    autoComplete="off"
                    placeholder={
                      prov.id === "openai" ? "مثلاً text-embedding-3-small"
                      : prov.id === "openrouter" ? "مثلاً representation-model"
                      : prov.id === "deepseek" ? "مثلاً text-embedding-3-small"
                      : prov.id === "ollama" ? "مثلاً nomic-embed-text"
                      : prov.id === "gapgpt" ? "مثلاً text-embedding-3-small"
                      : prov.id === "avalai" ? "مثلاً text-embedding-3-small"
                      : "نام مدل Embedding را وارد کنید"
                    }
                  />
                </Field>}
                <Field label={prov.id === "ollama" ? "آدرس Base URL" : prov.id === "openai_api_compatible" ? "نشانی Endpoint | کلید API جدید" : "کلید API جدید"}>
                  <input
                    name="api_key"
                    dir="ltr"
                    type={prov.id === "ollama" ? "text" : "password"}
                    autoComplete="new-password"
                    placeholder={
                      prov.has_api_key
                        ? `ثبت شده: ${prov.api_key_masked}`
                        : prov.id === "ollama" ? "http://localhost:11434"
                        : prov.id === "openai_api_compatible" ? "https://api.example.com/v1|sk-..."
                        : "کلید API را وارد کنید"
                    }
                    required={!prov.has_api_key}
                  />
                </Field>
              </div>
              <div className="provider-actions">
                <button className="primary-button compact" disabled={busy}>
                  {isConfigured ? "ذخیره تنظیمات" : "راه‌اندازی ارائه‌دهنده"}
                </button>
              </div>
            </>
          )}
        </form>
      );
    }

    return <div className="page-stack">
      <PageHeader eyebrow="" title="اتصالات" description="" />
      <section className="monitor-grid">
        <div style={{ display: "flex", flexDirection: "column", gap: "20px" }}>
          <div className="card">
            <div className="card-heading" style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "20px" }}>
              <div><h2>تنظیمات مدل‌ها و کلیدها</h2></div>
              <button type="button" className="primary-button compact" onClick={() => setShowAddProviderModal(true)}>افزودن ارائه‌دهنده</button>
            </div>

            <div className="provider-list-container">
              {displayedProviders.length === 0 ? (
                <p className="long-text" style={{ textAlign: "center", padding: "30px 0", color: "#888" }}>
                  هنوز ارائه‌دهنده مدلی اضافه نشده است. برای راه‌اندازی از دکمه «افزودن ارائه‌دهنده» استفاده کنید.
                </p>
              ) : (
                displayedProviders.map((prov) => renderProviderCard(prov))
              )}
            </div>
          </div>
        </div>

        <div className="card" style={{ alignSelf: "start" }}>
          <div className="section-title"><div><h2>وضعیت اتصال‌ها</h2></div></div>
          <div className="connection-list">
            {(connections?.checks ?? []).map((check) => <div className="connection-item" key={check.name}>
              <span><strong>{check.name}</strong><small>{check.latency_ms !== null ? `${check.latency_ms}ms` : "بدون زمان"}</small></span>
              <Badge tone={check.status === "success" ? "success" : "danger"}>{check.status === "success" ? "موفق" : "ناموفق"}</Badge>
              <p>{check.message}</p>
            </div>)}
          </div>
          <button type="button" className="secondary-button" disabled={busy} onClick={checkAllConnections}>بررسی همه اتصال‌ها</button>
        </div>
      </section>

      {showAddProviderModal && (
        <div className="modal-backdrop" onClick={() => setShowAddProviderModal(false)}>
          <div className="modal" style={{ width: "min(500px, 100%)" }} onClick={(e) => e.stopPropagation()}>
            <div className="modal-head">
              <h2>افزودن ارائه‌دهنده مدل</h2>
              <button type="button" className="icon-button" onClick={() => setShowAddProviderModal(false)}>×</button>
            </div>
            <div className="provider-selection-grid">
                {providerCatalog.map((prov) => {
                  const isConfigured = configuredList.some(p => p.id === prov.id);
                  return (
                    <div
                      className={`provider-select-card ${isConfigured ? "configured" : ""}`}
                      key={prov.id}
                      onClick={() => !isConfigured && !busy && handleInstallProvider(prov.id)}
                      style={{ cursor: isConfigured ? "default" : "pointer", opacity: isConfigured ? 0.6 : 1 }}
                    >
                      <span className="provider-select-logo">{prov.logo}</span>
                      <span className="provider-select-name">{prov.name}</span>
                      <button
                        type="button"
                        className="secondary-button compact"
                        disabled={isConfigured || busy}
                        style={{ marginTop: "5px", width: "100%" }}
                        onClick={(e) => {
                          e.stopPropagation();
                          if (!isConfigured) handleInstallProvider(prov.id);
                        }}
                      >
                        {isConfigured ? "اضافه شده" : "تنظیم و راه‌اندازی"}
                      </button>
                    </div>
                  );
                })}
              </div>
          </div>
        </div>
      )}
    </div>;
  }

  function renderActiveTab() {
    if (!token) return <section className="login-screen"><div className="card login-card">در حال انتقال به ورود سراسری…</div></section>;
    if (activeTab === "monitor") return renderMonitor();
    if (activeTab === "sources") return renderSources();
    if (activeTab === "reports") return renderReports();
    if (activeTab === "identity") return renderIdentity();
    if (activeTab === "logs") return renderLogs();
    if (activeTab === "connections") return renderConnections();
    return renderUsers();
  }

  return <div className="app-shell cyber-theme">
    <div className="global-bg-overlay">
      <svg viewBox="0 0 1920 1080" preserveAspectRatio="xMidYMid slice">
        <defs>
          {/* Intense neon glow filter for packets */}
          <filter id="glow-packet" x="-100%" y="-100%" width="300%" height="300%">
            <feGaussianBlur stdDeviation="5" result="blur1" />
            <feGaussianBlur stdDeviation="2.5" result="blur2" />
            <feMerge>
              <feMergeNode in="blur1" />
              <feMergeNode in="blur2" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>

          {/* Neon glow filter for circuit lines */}
          <filter id="glow-line" x="-50%" y="-50%" width="200%" height="200%">
            <feGaussianBlur stdDeviation="4" result="blur" />
            <feMerge>
              <feMergeNode in="blur" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>

          {/* Hacker UI Ambient Glow Gradients */}
          <radialGradient id="ambient-left" cx="20%" cy="20%" r="65%">
            <stop offset="0%" stopColor="#006b75" stopOpacity="0.22" />
            <stop offset="100%" stopColor="#030812" stopOpacity="0" />
          </radialGradient>
          <radialGradient id="ambient-right" cx="85%" cy="80%" r="70%">
            <stop offset="0%" stopColor="#004a7c" stopOpacity="0.28" />
            <stop offset="100%" stopColor="#030812" stopOpacity="0" />
          </radialGradient>

          {/* Backlight glows centered behind the two main cards for a floating effect */}
          <radialGradient id="backlight-left-card" cx="30%" cy="45%" r="45%">
            <stop offset="0%" stopColor="#005d66" stopOpacity="0.28" />
            <stop offset="100%" stopColor="#030812" stopOpacity="0" />
          </radialGradient>
          <radialGradient id="backlight-right-card" cx="72%" cy="45%" r="45%">
            <stop offset="0%" stopColor="#003c66" stopOpacity="0.32" />
            <stop offset="100%" stopColor="#030812" stopOpacity="0" />
          </radialGradient>
        </defs>

        {/* --- Ambient Backlighting Pools --- */}
        <rect width="1920" height="1080" fill="url(#ambient-left)" />
        <rect width="1920" height="1080" fill="url(#ambient-right)" />
        <rect width="1920" height="1080" fill="url(#backlight-left-card)" />
        <rect width="1920" height="1080" fill="url(#backlight-right-card)" />

        {/* --- Circuit Board Tracks - Layer 1: Outer Neon Glow (Thick, Low Opacity) --- */}
        <g stroke="rgba(0, 212, 200, 0.18)" strokeWidth="4.5" filter="url(#glow-line)" fill="none" strokeLinecap="round" strokeLinejoin="round">
          {/* Group 1: Left Card Parallel Bus */}
          <path d="M50,180 L220,180 L320,80 L520,80" />
          <path d="M50,195 L215,195 L315,95 L520,95" />
          <path d="M50,210 L210,210 L310,110 L520,110" />

          {/* Group 2: Bottom Left Parallel Bus */}
          <path d="M200,1020 L500,1020 L600,920 L900,920" />
          <path d="M200,1035 L495,1035 L595,935 L900,935" />

          {/* Group 3: Right Card Parallel Bus */}
          <path d="M1870,180 L1700,180 L1600,80 L1400,80" />
          <path d="M1870,195 L1695,195 L1595,95 L1400,95" />
          <path d="M1870,210 L1690,210 L1590,110 L1400,110" />

          {/* Group 4: Bottom Right Parallel Bus */}
          <path d="M1720,1020 L1420,1020 L1320,920 L1020,920" />
          <path d="M1720,1035 L1415,1035 L1315,935 L1020,935" />

          {/* Group 5: Center-Right Connectors */}
          <path d="M1080,300 L1150,300 L1250,400 L1250,700 L1180,770 L1080,770" />

          {/* Group 6: Left Side Vertical Branch */}
          <path d="M150,400 L150,750 L250,850 L450,850" />

          {/* Group 7: Right Side Vertical Branch */}
          <path d="M1770,400 L1770,750 L1670,850 L1470,850" />

          {/* Group 8: Center Loop */}
          <path d="M960,150 L900,210 L900,500 L960,560 L960,850" />
          <path d="M960,150 L1020,210 L1020,500 L960,560" />
        </g>

        {/* --- Circuit Board Tracks - Layer 2: Sharp Inner Core (Thin, High Opacity) --- */}
        <g stroke="rgba(0, 212, 200, 0.45)" strokeWidth="1.5" fill="none" strokeLinecap="round" strokeLinejoin="round">
          {/* Group 1 */}
          <path d="M50,180 L220,180 L320,80 L520,80" />
          <path d="M50,195 L215,195 L315,95 L520,95" />
          <path d="M50,210 L210,210 L310,110 L520,110" />

          {/* Group 2 */}
          <path d="M200,1020 L500,1020 L600,920 L900,920" />
          <path d="M200,1035 L495,1035 L595,935 L900,935" />

          {/* Group 3 */}
          <path d="M1870,180 L1700,180 L1600,80 L1400,80" />
          <path d="M1870,195 L1695,195 L1595,95 L1400,95" />
          <path d="M1870,210 L1690,210 L1590,110 L1400,110" />

          {/* Group 4 */}
          <path d="M1720,1020 L1420,1020 L1320,920 L1020,920" />
          <path d="M1720,1035 L1415,1035 L1315,935 L1020,935" />

          {/* Group 5 */}
          <path d="M1080,300 L1150,300 L1250,400 L1250,700 L1180,770 L1080,770" />

          {/* Group 6 */}
          <path d="M150,400 L150,750 L250,850 L450,850" />

          {/* Group 7 */}
          <path d="M1770,400 L1770,750 L1670,850 L1470,850" />

          {/* Group 8 */}
          <path d="M960,150 L900,210 L900,500 L960,560 L960,850" />
          <path d="M960,150 L1020,210 L1020,500 L960,560" />
        </g>

        {/* --- Circuit Board Pads (Double-ring electronic vias for high detail) --- */}
        <g stroke="rgba(0, 212, 200, 0.65)" strokeWidth="1" fill="none">
          {/* Outers */}
          <circle cx="50" cy="180" r="5.5" />
          <circle cx="50" cy="195" r="5.5" />
          <circle cx="50" cy="210" r="5.5" />
          <circle cx="520" cy="80" r="5.5" />
          <circle cx="520" cy="95" r="5.5" />
          <circle cx="520" cy="110" r="5.5" />
          <circle cx="200" cy="1020" r="5.5" />
          <circle cx="900" cy="920" r="5.5" />
          <circle cx="1870" cy="180" r="5.5" />
          <circle cx="1400" cy="80" r="5.5" />
          <circle cx="1720" cy="1020" r="5.5" />
          <circle cx="1020" cy="920" r="5.5" />
          <circle cx="1080" cy="300" r="5.5" />
          <circle cx="1080" cy="770" r="5.5" />
          <circle cx="960" cy="850" r="5.5" />

          {/* Inners (Filled Cores) */}
          <g fill="rgba(0, 212, 200, 0.85)">
            <circle cx="50" cy="180" r="2.2" />
            <circle cx="50" cy="195" r="2.2" />
            <circle cx="50" cy="210" r="2.2" />
            <circle cx="520" cy="80" r="2.2" />
            <circle cx="520" cy="95" r="2.2" />
            <circle cx="520" cy="110" r="2.2" />
            <circle cx="200" cy="1020" r="2.2" />
            <circle cx="900" cy="920" r="2.2" />
            <circle cx="1870" cy="180" r="2.2" />
            <circle cx="1400" cy="80" r="2.2" />
            <circle cx="1720" cy="1020" r="2.2" />
            <circle cx="1020" cy="920" r="2.2" />
            <circle cx="1080" cy="300" r="2.2" />
            <circle cx="1080" cy="770" r="2.2" />
            <circle cx="960" cy="850" r="2.2" />
          </g>
        </g>

        {/* --- Highly Visible Neon Data Packets (Moving Glowing Dots - High Opacity) --- */}
        <g opacity="0.95">
          {/* Top-Left Bus Packets */}
          <circle r="3.5" fill="#00ffd2" filter="url(#glow-packet)">
            <animateMotion dur="6s" repeatCount="indefinite" begin="0s" path="M50,180 L220,180 L320,80 L520,80" />
          </circle>
          <circle r="3.5" fill="#00e676" filter="url(#glow-packet)">
            <animateMotion dur="7s" repeatCount="indefinite" begin="2s" path="M50,195 L215,195 L315,95 L520,95" />
          </circle>
          <circle r="3.5" fill="#00ffd2" filter="url(#glow-packet)">
            <animateMotion dur="8s" repeatCount="indefinite" begin="4s" path="M50,210 L210,210 L310,110 L520,110" />
          </circle>

          {/* Bottom-Left Bus Packets */}
          <circle r="3.5" fill="#00ffd2" filter="url(#glow-packet)">
            <animateMotion dur="7.5s" repeatCount="indefinite" begin="1s" path="M200,1020 L500,1020 L600,920 L900,920" />
          </circle>
          <circle r="3.5" fill="#00ffd2" filter="url(#glow-packet)">
            <animateMotion dur="7.5s" repeatCount="indefinite" begin="4.5s" path="M200,1035 L495,1035 L595,935 L900,935" />
          </circle>

          {/* Top-Right Bus Packets */}
          <circle r="3.5" fill="#00ffd2" filter="url(#glow-packet)">
            <animateMotion dur="6.5s" repeatCount="indefinite" begin="0.5s" path="M1870,180 L1700,180 L1600,80 L1400,80" />
          </circle>
          <circle r="3.5" fill="#00e676" filter="url(#glow-packet)">
            <animateMotion dur="7.5s" repeatCount="indefinite" begin="2.5s" path="M1870,195 L1695,195 L1595,95 L1400,95" />
          </circle>
          <circle r="3.5" fill="#00ffd2" filter="url(#glow-packet)">
            <animateMotion dur="8.5s" repeatCount="indefinite" begin="4.5s" path="M1870,210 L1690,210 L1590,110 L1400,110" />
          </circle>

          {/* Bottom-Right Bus Packets */}
          <circle r="3.5" fill="#00ffd2" filter="url(#glow-packet)">
            <animateMotion dur="8s" repeatCount="indefinite" begin="1.5s" path="M1720,1020 L1420,1020 L1320,920 L1020,920" />
          </circle>
          <circle r="3.5" fill="#00ffd2" filter="url(#glow-packet)">
            <animateMotion dur="8s" repeatCount="indefinite" begin="5s" path="M1720,1035 L1415,1035 L1315,935 L1020,935" />
          </circle>

          {/* Center-Right Connector Packets */}
          <circle r="3.2" fill="#00ffd2" filter="url(#glow-packet)">
            <animateMotion dur="7s" repeatCount="indefinite" begin="3s" path="M1080,300 L1150,300 L1250,400 L1250,700 L1180,770 L1080,770" />
          </circle>

          {/* Left Vertical Packets */}
          <circle r="3.5" fill="#00e676" filter="url(#glow-packet)">
            <animateMotion dur="9s" repeatCount="indefinite" begin="0s" path="M150,400 L150,750 L250,850 L450,850" />
          </circle>

          {/* Right Vertical Packets */}
          <circle r="3.5" fill="#00ffd2" filter="url(#glow-packet)">
            <animateMotion dur="9s" repeatCount="indefinite" begin="3s" path="M1770,400 L1770,750 L1670,850 L1470,850" />
          </circle>

          {/* Center Column Packets */}
          <circle r="3" fill="#00ffd2" filter="url(#glow-packet)">
            <animateMotion dur="8s" repeatCount="indefinite" begin="1s" path="M960,150 L900,210 L900,500 L960,560 L960,850" />
          </circle>
        </g>
      </svg>
    </div>
    <div className="cyber-grid-overlay"></div>
    <div className="cyber-hud-left">
      <div className="binary-stream">010100100101000101101010 0110010 0110001101011011 11101010010110110101010</div>
      <div className="hud-line font-mono">TRACKER-CONSOLE</div>
      <div className="hud-line font-mono">hacker-console::log</div>
      <div className="hud-line font-mono">node:: 21000 0000 0000 2200 000400</div>
      <div className="hud-line font-mono">-- -</div>
      <div className="hud-line font-mono">tracker-console: active</div>
      <div className="hud-spacer"></div>
      <div className="hud-line font-mono bottom-hud">tracker::console::</div>
      <div className="hud-line font-mono bottom-hud">session: active</div>
      <div className="hud-line font-mono bottom-hud">nodes: active</div>
      <div className="hud-line font-mono bottom-hud">server temp: 32C</div>
      <div className="hud-line font-mono bottom-hud">status: connected</div>
    </div>
    <div className="cyber-hud-right">
      <div className="hud-line font-mono">hacker-console::log</div>
      <div className="hud-line font-mono">hacker-console::1500</div>
      <div className="hud-line font-mono">hacker-console::3400</div>
      <div className="hud-line font-mono">hacker-console::2700243/kg</div>
      <div className="hud-line font-mono">hacker-console::159234/kg</div>
      <div className="hud-line font-mono">hacker-console::1692/318/kg</div>
      <div className="hud-line font-mono">hacker-console: odb@rasad-utr</div>
      <div className="hud-spacer"></div>
      <div className="hud-line font-mono bottom-hud">deep state check...</div>
      <div className="hud-line font-mono bottom-hud">7x23bedd</div>
      <div className="hud-line font-mono bottom-hud">test system:: active</div>
      <div className="hud-line font-mono bottom-hud">api check: status 200</div>
      <div className="hud-line font-mono bottom-hud">line 1</div>
      <div className="hud-line font-mono bottom-hud">line 2</div>
      <div className="hud-line font-mono bottom-hud">line 3</div>
    </div>
    {token && <header className="topbar">
      <a className="brand" href="/" aria-label="بازگشت به صفحه اصلی سامانه" style={{ textDecoration: "none" }}>
        <span className="brand-mark">
          <img src="/site-brand-logo-v2.webp?v=1" alt="" width="44" height="44" style={{ width: "100%", height: "100%", objectFit: "cover", borderRadius: "inherit" }} />
        </span>
        <span><strong>سامانه رصد و تحلیل حامین</strong></span>
      </a>
      <nav className="tabs">
        {tabLabels.map((tab) => (
          <button
            type="button"
            key={tab.id}
            className={activeTab === tab.id ? "active" : ""}
            onClick={() => navigateTab(tab.id)}
          >
            {renderTabIcon(tab.id)}
            <span>{tab.label}</span>
          </button>
        ))}
      </nav>
      <div className="top-actions">
        <button type="button" className={`health-dot ${health?.status === "ok" ? "ready" : ""}`} title={health?.database ? "API و دیتابیس آماده‌اند" : "سلامت سامانه نامشخص است"} onClick={() => refreshData().catch((error) => setMessage(error.message))} />
        {me && <div className="settings-wrap" ref={settingsRef}>
          <button type="button" className="icon-button" onClick={() => setSettingsOpen((open) => !open)} title="تنظیمات">
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ width: "20px", height: "20px" }}>
              <circle cx="12" cy="12" r="3" />
              <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
            </svg>
          </button>
          {settingsOpen && <div className="settings-menu">
            <button type="button" onClick={() => { navigateTab("users"); setSettingsOpen(false); }}>مشاهده حساب</button>
            {me.role === "super_admin" && <button type="button" onClick={() => { navigateTab("logs"); refreshSystemLogs().catch((error) => setMessage(error.message)); setSettingsOpen(false); }}>لاگ‌ها</button>}
            {me.role === "super_admin" && <button type="button" onClick={() => { navigateTab("connections"); refreshConnections().catch((error) => setMessage(error.message)); setSettingsOpen(false); }}>اتصالات</button>}
            <hr />
            <button type="button" className="danger" onClick={handleLogout}>خروج</button>
          </div>}
        </div>}
      </div>
    </header>}

    <main className={token ? "content" : "login-content"}>
      {renderActiveTab()}
    </main>



    {sourcePickerOpen && <div className="modal-backdrop" onClick={() => setSourcePickerOpen(false)}>
      <div className="modal" onClick={(event) => event.stopPropagation()}>
        <div className="modal-head"><h2>انتخاب منابع رصد</h2><button type="button" className="icon-button" onClick={() => setSourcePickerOpen(false)}>×</button></div>
        <p className="long-text">منابع را خودتان انتخاب کنید. نشانی‌های نمونه و آزمایشی و نشانی‌های محلی در فهرست عملیاتی نمایش داده نمی‌شوند؛ سوابق آن‌ها حفظ شده است.</p>
        {activeSources.length > 0 && (
          <div className="picker-quick-actions" style={{
            display: "flex",
            gap: "10px",
            marginBottom: "18px",
            marginTop: "-10px",
            justifyContent: "flex-start"
          }}>
            <button
              type="button"
              className="secondary-button"
              style={{ minHeight: "34px", height: "34px", padding: "0 14px", fontSize: "11px", borderRadius: "8px" }}
              onClick={() => setSelectedSourceIds(activeSources.map(s => s.id))}
            >
              انتخاب همه
            </button>
            <button
              type="button"
              className="secondary-button"
              style={{ minHeight: "34px", height: "34px", padding: "0 14px", fontSize: "11px", borderRadius: "8px" }}
              onClick={() => setSelectedSourceIds([])}
            >
              برداشتن همه
            </button>
          </div>
        )}
        <div className="picker-list">
          {activeSources.map((source) => <button type="button" className={`picker-item ${selectedSourceIds.includes(source.id) ? "is-selected" : ""}`} key={source.id} onClick={() => toggleSource(source.id)} title={source.last_status === "failed" ? "اتصال مستقیم ناموفق است؛ مسیرهای جایگزین هنگام رصد همچنان بررسی می‌شوند." : undefined}>
            <span className="checkbox">{selectedSourceIds.includes(source.id) ? "✓" : ""}</span>
            <span className="source-mark">{source.title[0]}</span>
            <span><strong>{source.title}</strong><small dir="ltr">{source.base_url}</small></span>
            <Badge tone={statusTone(source.last_status)}>{sourceStatusLabels[source.last_status]}</Badge>
          </button>)}
        </div>
        {activeSources.length === 0 && <p className="long-text">هنوز منبع فعالی ثبت نشده است.</p>}
        <div className="modal-actions"><button type="button" className="primary-button compact" onClick={() => setSourcePickerOpen(false)}>تأیید انتخاب</button></div>
      </div>
    </div>}

    {userModal && <div className="modal-backdrop" onClick={() => setUserModal(null)}>
      <form className="modal" onSubmit={userModal === "new" ? createUser : (event) => updateUser(event, userModal)} onClick={(event) => event.stopPropagation()}>
        <div className="modal-head"><h2>{userModal === "new" ? "افزودن کاربر" : "ویرایش کاربر"}</h2><button type="button" className="icon-button" onClick={() => setUserModal(null)}>×</button></div>
        <div className="two-fields">
          <Field label="نام کامل"><input name="full_name" defaultValue={userModal === "new" ? "" : userModal.full_name} required /></Field>
          <Field label="نام کاربری"><input name="username" defaultValue={userModal === "new" ? "" : userModal.username} required minLength={3} disabled={userModal !== "new"} /></Field>
        </div>
        <div className="two-fields">
          <Field label="ایمیل"><input name="email" type="email" defaultValue={userModal === "new" ? "" : userModal.email} required /></Field>
          {userModal === "new" && <Field label="رمز عبور"><input name="password" type="password" required minLength={8} /></Field>}
        </div>
        <Field label="نقش"><select name="role" defaultValue={userModal === "new" ? "user" : userModal.role}><option value="user">کاربر</option><option value="super_admin">مدیر ارشد</option></select></Field>
        {userModal !== "new" && <Field label="وضعیت"><select name="is_active" defaultValue={String(userModal.is_active)}><option value="true">فعال</option><option value="false">غیرفعال</option></select></Field>}
        <button className="primary-button" disabled={busy}>{userModal === "new" ? "ساخت کاربر" : "ذخیره تغییرات"}</button>
      </form>
    </div>}

    {identityModal && <div className="modal-backdrop" onClick={() => setIdentityModal(null)}>
      <form className="modal modal--report-full" onSubmit={(event) => updateIdentity(event, identityModal)} onClick={(event) => event.stopPropagation()}>
        <div className="modal-head"><h2>ویرایش هویت تحلیلگر</h2><button type="button" className="icon-button" onClick={() => setIdentityModal(null)}>×</button></div>
        <div className="two-fields">
          <Field label="نام هویت"><input name="title" defaultValue={identityModal.title} required /></Field>
          <Field label="وضعیت"><select name="is_active" defaultValue={String(identityModal.is_active)}><option value="true">فعال</option><option value="false">غیرفعال</option></select></Field>
        </div>
        <Field label="نقش تحلیلگر"><textarea name="role_definition" defaultValue={identityModal.role_definition} required /></Field>
        <div className="two-fields">
          <Field label="لحن">
            <select name="tone" defaultValue={identityModal.tone ?? "رسمی"}>
              <option value="رسمی">رسمی</option>
              <option value="بی‌طرف">بی‌طرف</option>
              <option value="تحلیلی و موشکافانه">تحلیلی و موشکافانه</option>
              <option value="انتقادی">انتقادی</option>
              <option value="آکادمیک">آکادمیک</option>
              <option value="هشداری و پیش‌بینانه">هشداری و پیش‌بینانه</option>
            </select>
          </Field>
          <Field label="سبک تحلیل">
            <select name="analysis_style" defaultValue={identityModal.analysis_style ?? "ساختاریافته"}>
              <option value="ساختاریافته">ساختاریافته</option>
              <option value="توصیفی و تبیینی">توصیفی و تبیینی</option>
              <option value="تفسیری">تفسیری</option>
              <option value="داده‌محور">داده‌محور</option>
              <option value="تطبیقی و مقایسه‌ای">تطبیقی و مقایسه‌ای</option>
              <option value="سناریومحور">سناریومحور</option>
            </select>
          </Field>
        </div>
        <Field label="چارچوب شناختی"><textarea name="cognitive_framework" defaultValue={identityModal.cognitive_framework ?? ""} /></Field>
        <div className="modal-actions">
          <button type="button" className="secondary-button danger" disabled={busy} onClick={() => deleteIdentity(identityModal)}>حذف هویت</button>
          <button className="primary-button compact" disabled={busy}>ذخیره تغییرات</button>
        </div>
      </form>
    </div>}

    {identityToDelete && (
      <div className="modal-backdrop" onClick={() => setIdentityToDelete(null)}>
        <div className="modal" style={{ width: "min(400px, 100%)" }} onClick={(e) => e.stopPropagation()}>
          <div className="modal-head">
            <h2>تأیید حذف هویت</h2>
            <button type="button" className="icon-button" onClick={() => setIdentityToDelete(null)}>×</button>
          </div>
          <div style={{ marginBottom: "1.5rem" }}>
            <p>آیا از حذف هویت «<strong>{identityToDelete.title}</strong>» اطمینان دارید؟</p>
            {deleteError && (
              <div style={{
                color: "#ef4444",
                background: "rgba(239, 68, 68, 0.1)",
                padding: "12px",
                borderRadius: "8px",
                marginTop: "15px",
                fontSize: "13px",
                border: "1px solid rgba(239, 68, 68, 0.2)",
                lineHeight: "1.6"
              }}>
                {deleteError}
              </div>
            )}
          </div>
          <div className="modal-actions">
            <button type="button" className="secondary-button" onClick={() => setIdentityToDelete(null)}>خیر</button>
            <button type="button" className="primary-button compact" style={{ backgroundColor: "#ef4444", borderColor: "#ef4444" }} disabled={busy} onClick={() => confirmDeleteIdentity(identityToDelete)}>بله، حذف شود</button>
          </div>
        </div>
      </div>
    )}

    {sourceModal && <div className="modal-backdrop" onClick={() => setSourceModal(null)}>
      <div className="modal" onClick={(event) => event.stopPropagation()}>
        <div className="modal-head"><h2>جزئیات منبع</h2><button type="button" className="icon-button" onClick={() => setSourceModal(null)}>×</button></div>
        <div className={sourceModal.check?.status === "failed" || sourceModal.source.last_status === "failed" ? "error-panel" : "card"}>
          <span className="error-icon">{sourceModal.check?.status === "failed" || sourceModal.source.last_status === "failed" ? "!" : "✓"}</span>
          <div><h3>{sourceModal.source.title}</h3><p dir="ltr">{sourceModal.source.base_url}</p></div>
        </div>
        <div className="detail-grid">
          <span>نوع<strong>{sourceTypeLabels[sourceModal.source.type]}</strong></span>
          <span>فعال‌سازی<strong>{sourceModal.source.is_active ? "فعال" : "غیرفعال"}</strong></span>
          <span>اتصال<strong>{sourceStatusLabels[sourceModal.check?.status ?? sourceModal.source.last_status]}</strong></span>
          <span>آخرین خزش<strong>{sourceCrawlStatusLabels[sourceModal.source.last_crawl_status]}</strong></span>
          <span>HTTP<strong>{sourceModal.check?.http_status ?? "-"}</strong></span>
          <span>زمان پاسخ<strong>{sourceModal.check?.response_time_ms ? `${sourceModal.check.response_time_ms}ms` : "-"}</strong></span>
          <span>آخرین بررسی<strong>{toPersianDate(sourceModal.check?.last_checked_at ?? sourceModal.source.last_checked_at)}</strong></span>
          <span>زمان آخرین خزش<strong>{toPersianDate(sourceModal.source.last_crawled_at)}</strong></span>
        </div>
        <p className="long-text">{sourceModal.check?.reason ?? sourceModal.source.last_status_message ?? "خطایی برای این منبع ثبت نشده است."}</p>
        {sourceModal.source.last_crawl_message && <p className="long-text">نتیجه خزش: {sourceModal.source.last_crawl_message}</p>}
      </div>
    </div>}

    {reportModal && <div className="modal-backdrop" onClick={() => setReportModal(null)}>
      <div className="modal modal--report-full" onClick={(event) => event.stopPropagation()}>
        <div className="modal-head"><h2>{reportModal.title}</h2><button type="button" className="icon-button" onClick={() => setReportModal(null)}>×</button></div>
        <div className="modal-report-meta">
          <span>{toPersianDate(reportModal.created_at)}</span>
          <span>{reportModal.sources.length} منبع</span>
          {(() => {
            const job = jobs.find((j) => j.id === reportModal.job_id);
            const start = job ? getJobStartTime(job) : null;
            const end = job ? getJobEndTime(job) : null;
            const dur = start && end ? Math.max(0, end - start) : null;
            return dur !== null ? (
              <span>⏱ مدت زمان اجرا: {formatDurationHuman(dur)}</span>
            ) : null;
          })()}
        </div>
        <FullReportDetails report={reportModal} />
        <div className="modal-actions">
          <button type="button" className="secondary-button" disabled={!reportModal.docx_available || busy} onClick={() => handleDownload(reportModal, "docx")}>دانلود Word</button>
          <button type="button" className="primary-button compact" disabled={!reportModal.pdf_available || busy} onClick={() => handleDownload(reportModal, "pdf")}>دانلود PDF</button>
        </div>
      </div>
    </div>}

    {message && (
      <div className="cyber-toast">
        <span className="cyber-toast-icon">⚠️</span>
        <span className="cyber-toast-text">{message}</span>
        <button type="button" className="cyber-toast-close" onClick={() => setMessage("")}>×</button>
      </div>
    )}
  </div>;
}
