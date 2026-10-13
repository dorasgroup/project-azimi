"use client";

import { useEffect, useRef, useState } from "react";
import { ArrowUpLeft, BookOpen } from "lucide-react";

import { SourcePopup } from "@/components/upload/source-popup";
import { apiRequest } from "@/lib/api/client";
import { getAccessToken } from "@/lib/auth/session";
import { deliveryStatusLabel, difyStatusLabel, statusBadgeStyle } from "@/lib/uploads/status";

type SiteArticleItem = {
  id: string;
  external_id: number;
  title: string;
  source_url: string;
  published_at: string | null;
  modified_at: string | null;
  source_status: "published" | "archived";
  received_at: string | null;
  revision: number;
  upload_id: string | null;
  filename: string | null;
  delivery_status: string;
  dify_status: string | null;
  dify_completed_at: string | null;
  error_message: string | null;
};

type SiteArticlesResponse = {
  items: SiteArticleItem[];
  total: number;
  page: number;
  limit: number;
  summary: { indexed: number; pending: number; failed: number; archived: number };
  sync: {
    enabled: boolean;
    interval_minutes: number;
    last_attempt_at: string | null;
    last_success_at: string | null;
    error_message: string | null;
    source_article_count: number | null;
  };
};

const PAGE_LIMIT = 20;
const STORED_STATUSES = new Set(["stored"]);
// A created document is not a completed Dify import.
const INDEXED_STATUSES = new Set(["indexed"]);

function formatDate(value: string | null) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : date.toLocaleString("fa-IR");
}

function articleSourceUrl(value: string) {
  try {
    const url = new URL(value);
    if (
      url.protocol === "https:" &&
      (url.hostname === "haminyar.ir" || url.hostname === "www.haminyar.ir") &&
      !url.username && !url.password && (!url.port || url.port === "443")
    ) {
      return url.href;
    }
  } catch {
    // Invalid or unexpected source URLs are displayed as plain titles.
  }
  return null;
}

export function SiteArticlesSection() {
  const [isOpen, setIsOpen] = useState(false);
  const [selectedArticle, setSelectedArticle] = useState<SiteArticleItem | null>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const [page, setPage] = useState(1);
  const [data, setData] = useState<SiteArticlesResponse | null>(null);
  const [isRefreshing, setIsRefreshing] = useState(true);
  const [error, setError] = useState<{ page: number; message: string } | null>(null);
  const refreshRef = useRef<(() => void) | null>(null);
  const dataRef = useRef<SiteArticlesResponse | null>(null);

  useEffect(() => { if (listRef.current) listRef.current.scrollTop = 0; }, [page]);

  useEffect(() => {
    let disposed = false;
    let running = false;
    let refreshQueued = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let controller: AbortController | null = null;

    function scheduleRefresh() {
      if (disposed) return;
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => {
        timer = null;
        void refresh();
      }, dataRef.current && dataRef.current.summary.pending > 0 ? 10_000 : 30_000);
    }

    async function refresh() {
      if (disposed) return;
      if (running) {
        refreshQueued = true;
        return;
      }
      if (timer) {
        clearTimeout(timer);
        timer = null;
      }
      const token = getAccessToken();
      if (!token) {
        setError({ page, message: "برای مشاهده مقالات سایت ابتدا وارد پنل شوید." });
        setIsRefreshing(false);
        return;
      }
      running = true;
      controller = new AbortController();
      setIsRefreshing(true);
      try {
        const response = await apiRequest<SiteArticlesResponse>(
          `/integrations/site-articles?page=${page}&limit=${PAGE_LIMIT}`,
          { token, signal: controller.signal, cache: "no-store" },
        );
        if (disposed) return;
        dataRef.current = response;
        setData(response);
        setError(null);
        const lastPage = Math.max(1, Math.ceil(response.total / response.limit));
        if (page > lastPage) setPage(lastPage);
      } catch {
        if (disposed) return;
        setError({
          page,
          message: dataRef.current?.page === page
            ? "تازه‌سازی مقالات انجام نشد؛ آخرین اطلاعات دریافتی همچنان نمایش داده می‌شود."
            : "دریافت فهرست مقالات سایت انجام نشد. لطفاً دوباره تلاش کنید.",
        });
      } finally {
        running = false;
        controller = null;
        if (!disposed) {
          setIsRefreshing(false);
          if (refreshQueued) {
            refreshQueued = false;
            void refresh();
          } else {
            scheduleRefresh();
          }
        }
      }
    }

    const requestRefresh = () => { void refresh(); };
    refreshRef.current = requestRefresh;
    window.addEventListener("focus", requestRefresh);
    void refresh();
    return () => {
      disposed = true;
      if (timer) clearTimeout(timer);
      controller?.abort();
      window.removeEventListener("focus", requestRefresh);
      if (refreshRef.current === requestRefresh) refreshRef.current = null;
    };
  }, [page]);

  const visibleData = data?.page === page ? data : null;
  const visibleError = error?.page === page ? error.message : null;
  const totalPages = data ? Math.max(1, Math.ceil(data.total / data.limit)) : 1;
  const firstItem = visibleData && visibleData.total > 0
    ? (visibleData.page - 1) * visibleData.limit + 1 : 0;
  const lastItem = visibleData
    ? Math.min(visibleData.total, firstItem + visibleData.items.length - 1) : 0;

  return (
    <>
      <button className="source-entry-button" type="button" aria-haspopup="dialog"
        aria-controls="site-articles-popup" onClick={() => setIsOpen(true)}>
        <BookOpen size={21} aria-hidden="true" />
        <span className="source-entry-label">مقالات سایت حامین یار
          <small>{data ? `${data.total.toLocaleString("fa-IR")} مقاله · مخزن ازدواج` : "مشاهده مقالات و وضعیت ثبت"}</small>
        </span>
        <ArrowUpLeft size={18} className="source-entry-arrow" aria-hidden="true" />
      </button>
      <SourcePopup open={isOpen} onClose={() => setIsOpen(false)} title="مقالات سایت حامین یار"
        id="site-articles-popup" className="site-articles-section source-articles-popup"
        footer={visibleData && visibleData.total > 0 ? (
          <nav className="site-articles-pagination" aria-label="صفحه‌بندی مقالات سایت">
            <span className="muted">
              نمایش {firstItem.toLocaleString("fa-IR")} تا {lastItem.toLocaleString("fa-IR")} از {visibleData.total.toLocaleString("fa-IR")} مقاله
            </span>
            <div className="site-articles-pagination-actions">
              <button className="button secondary" type="button" disabled={page <= 1}
                onClick={() => setPage((current) => Math.max(1, current - 1))}>صفحه قبل</button>
              <span className="muted">صفحه {page.toLocaleString("fa-IR")} از {totalPages.toLocaleString("fa-IR")}</span>
              <button className="button secondary" type="button" disabled={page >= totalPages}
                onClick={() => setPage((current) => Math.min(totalPages, current + 1))}>صفحه بعد</button>
            </div>
          </nav>
        ) : null}>
      <div className="source-popup-intro">
          <p className="muted site-articles-description">
            مقاله‌های منتشرشده سایت به‌صورت خودکار دریافت می‌شوند و در مخزن «ازدواج» برای ثبت در Dify قرار می‌گیرند.
          </p>
        <div className="site-articles-heading-actions">
          {data ? <span className="badge">{data.total.toLocaleString("fa-IR")} مقاله</span> : null}
          <button className="button secondary" type="button" disabled={isRefreshing}
            onClick={() => refreshRef.current?.()}>
            {isRefreshing ? "در حال تازه‌سازی…" : "تازه‌سازی وضعیت"}
          </button>
        </div>
      </div>

      {data ? (
        <>
          <details className="site-articles-sync source-sync-details">
            <summary>
              <span className="source-sync-indicator" data-enabled={data.sync.enabled && !data.sync.error_message} />
              {data.sync.enabled ? "همگام‌سازی خودکار فعال" : "همگام‌سازی خودکار تنظیم نشده است"}
              {data.sync.enabled ? <span className="muted"> · هر {data.sync.interval_minutes.toLocaleString("fa-IR")} دقیقه</span> : null}
              <span className="source-sync-more">جزئیات همگام‌سازی</span>
            </summary>
            <div className="site-articles-sync-details muted">
              {data.sync.enabled ? (
                <span>بررسی مقاله‌های جدید هر {data.sync.interval_minutes.toLocaleString("fa-IR")} دقیقه</span>
              ) : <span>پس از فعال‌سازی اتصال سایت، مقاله‌ها به‌صورت خودکار دریافت می‌شوند.</span>}
              <span>آخرین همگام‌سازی موفق: {data.sync.last_success_at
                ? <time dateTime={data.sync.last_success_at}>{formatDate(data.sync.last_success_at)}</time>
                : "هنوز انجام نشده"}</span>
              {data.sync.error_message && data.sync.last_attempt_at ? (
                <span>آخرین تلاش: <time dateTime={data.sync.last_attempt_at}>{formatDate(data.sync.last_attempt_at)}</time></span>
              ) : null}
            </div>
          </details>
          {data.sync.error_message ? (
            <p className="form-error" role="status">{data.sync.error_message}</p>
          ) : null}
          <div className="site-articles-summary" role="group" aria-label="خلاصه وضعیت همه مقالات سایت">
            <span className="badge" style={statusBadgeStyle("indexed", INDEXED_STATUSES)}>
              ثبت موفق در Dify: {data.summary.indexed.toLocaleString("fa-IR")}
            </span>
            <span className="badge">در انتظار تکمیل: {data.summary.pending.toLocaleString("fa-IR")}</span>
            <span className="badge" style={data.summary.failed > 0 ? statusBadgeStyle("failed", INDEXED_STATUSES) : undefined}>
              ناموفق: {data.summary.failed.toLocaleString("fa-IR")}
            </span>
            {data.summary.archived > 0 ? (
              <span className="badge site-articles-archived">آرشیوشده: {data.summary.archived.toLocaleString("fa-IR")}</span>
            ) : null}
          </div>
        </>
      ) : null}

      {visibleError ? (
        <div className="site-articles-error form-error" role="alert">
          <span>{visibleError}</span>
          <button className="button secondary" type="button" disabled={isRefreshing}
            onClick={() => refreshRef.current?.()}>تلاش دوباره</button>
        </div>
      ) : null}
      <div className="site-articles-live" role="status" aria-live="polite" aria-atomic="true">
        {isRefreshing && !visibleData ? "در حال دریافت مقالات سایت…" : null}
        {isRefreshing && visibleData ? "در حال تازه‌سازی وضعیت مقالات…" : null}
      </div>
      {visibleData && visibleData.items.length === 0 ? (
        <p className="muted site-articles-empty">
          {visibleData.sync.enabled
            ? "هنوز مقاله‌ای دریافت نشده است. همگام‌سازی خودکار، مقاله‌های منتشرشده سایت را به این بخش اضافه می‌کند."
            : "هنوز مقاله‌ای از سایت حامین یار دریافت نشده است."}
        </p>
      ) : null}

      {visibleData && visibleData.items.length > 0 ? (
        <p id="site-articles-scroll-hint" className="muted site-articles-scroll-hint">
          ۵ مورد در پنجره فهرست نمایش داده می‌شود؛ برای موارد بیشتر اسکرول کنید. برای دیدن همه ستون‌ها، جدول را به چپ یا راست حرکت دهید.
        </p>
      ) : null}

      {visibleData && visibleData.items.length > 0 ? (
        <div ref={listRef} className="table-wrap site-articles-table-wrap source-list-viewport" tabIndex={0}
          role="region" aria-label="جدول مقالات سایت حامین یار"
          aria-describedby="site-articles-scroll-hint" aria-busy={isRefreshing}>
          <table className="table site-articles-table source-list-table">
            <caption className="site-articles-sr-only">وضعیت دریافت و ثبت مقالات سایت حامین یار در مخزن ازدواج و Dify</caption>
            <thead>
              <tr>
                <th scope="col">عنوان مقاله</th>
                <th scope="col">دسته سامانه</th>
                <th scope="col">فایل ذخیره‌شده</th>
                <th scope="col">آخرین تغییر در سایت</th>
                <th scope="col">دریافت در سامانه</th>
                <th scope="col">وضعیت Dify</th>
              </tr>
            </thead>
            <tbody>
              {visibleData.items.map((article) => {
                const sourceUrl = articleSourceUrl(article.source_url);
                return (
                  <tr key={article.id} data-source-row="article">
                    <td className="site-articles-title-cell">
                      <div className="source-cell">
                      {sourceUrl ? (
                        <a className="site-articles-link source-preview" href={sourceUrl} target="_blank" rel="noopener noreferrer"
                          aria-label={`${article.title} (مشاهده مقاله در پنجره جدید)`}>{article.title}</a>
                      ) : <strong className="source-preview">{article.title}</strong>}
                      <button className="source-details-button" type="button" onClick={() => setSelectedArticle(article)}
                        aria-label={`نمایش جزئیات مقاله ${article.title}`}>
                        جزئیات کامل{article.source_status === "archived" ? " · آرشیوشده" : article.error_message ? " · دارای خطا" : ""}
                      </button>
                      </div>
                    </td>
                    <td><div className="source-cell"><span className="badge">ازدواج</span>
                      {article.revision > 1 ? <small className="muted">نسخه {article.revision.toLocaleString("fa-IR")}</small> : null}
                    </div></td>
                    <td className="site-articles-filename-cell">
                      <div className="source-cell"><span className="source-preview">
                        {article.filename ? <bdi>{article.filename}</bdi> : <span className="muted">در انتظار ساخت فایل TXT</span>}
                      </span></div>
                    </td>
                    <td className="site-articles-date-cell">
                      <div className="source-cell">
                      {article.modified_at ? <time dateTime={article.modified_at}>{formatDate(article.modified_at)}</time> : "—"}
                      </div>
                    </td>
                    <td>
                      <div className="source-cell">
                      <span className="badge" style={statusBadgeStyle(article.delivery_status, STORED_STATUSES)}>
                        {deliveryStatusLabel(article.delivery_status)}
                      </span>
                      {article.received_at ? <small className="site-articles-status-date muted">
                        <time dateTime={article.received_at}>{formatDate(article.received_at)}</time>
                      </small> : null}</div>
                    </td>
                    <td>
                      <div className="source-cell">
                      <span className="badge" style={statusBadgeStyle(article.dify_status, INDEXED_STATUSES)}>
                        {difyStatusLabel(article.dify_status)}
                      </span>
                      {article.dify_status === "indexed" && article.dify_completed_at ? (
                        <small className="site-articles-status-date muted">
                          <time dateTime={article.dify_completed_at}>{formatDate(article.dify_completed_at)}</time>
                        </small>
                      ) : null}</div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : null}

      </SourcePopup>
      <SourcePopup open={selectedArticle !== null} onClose={() => setSelectedArticle(null)}
        id="site-article-details-popup" title="جزئیات مقاله" className="source-details-popup">
        {selectedArticle ? (
          <div className="source-full-details">
            <h3>{selectedArticle.title}</h3>
            {articleSourceUrl(selectedArticle.source_url) ? (
              <a className="site-articles-link" href={articleSourceUrl(selectedArticle.source_url)!} target="_blank" rel="noopener noreferrer">مشاهده مقاله در سایت حامین یار</a>
            ) : null}
            <dl>
              <dt>دسته سامانه</dt><dd>ازدواج</dd>
              <dt>فایل ذخیره‌شده</dt><dd>{selectedArticle.filename ? <bdi>{selectedArticle.filename}</bdi> : "در انتظار ساخت فایل TXT"}</dd>
              <dt>وضعیت در منبع</dt><dd>{selectedArticle.source_status === "archived" ? "آرشیوشده در منبع" : "منتشرشده"} · نسخه {selectedArticle.revision.toLocaleString("fa-IR")}</dd>
              <dt>انتشار در سایت</dt><dd>{formatDate(selectedArticle.published_at)}</dd>
              <dt>آخرین تغییر در سایت</dt><dd>{formatDate(selectedArticle.modified_at)}</dd>
              <dt>دریافت در سامانه</dt><dd>{deliveryStatusLabel(selectedArticle.delivery_status)} · {formatDate(selectedArticle.received_at)}</dd>
              <dt>وضعیت Dify</dt><dd>{difyStatusLabel(selectedArticle.dify_status)}{selectedArticle.dify_completed_at ? ` · ${formatDate(selectedArticle.dify_completed_at)}` : ""}</dd>
            </dl>
            {selectedArticle.error_message ? <p className="form-error">{selectedArticle.error_message}</p> : null}
          </div>
        ) : null}
      </SourcePopup>
    </>
  );
}
