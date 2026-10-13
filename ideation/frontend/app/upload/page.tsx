"use client";

import { ChangeEvent, FormEvent, useEffect, useRef, useState } from "react";
import { ArrowUpLeft, TrendingUp } from "lucide-react";

import { AppShell } from "@/components/layout/app-shell";
import { SiteArticlesSection } from "@/components/upload/site-articles-section";
import { SourcePopup } from "@/components/upload/source-popup";
import { apiRequest } from "@/lib/api/client";
import { getAccessToken, getStoredUser } from "@/lib/auth/session";
import { deliveryStatusLabel, difyStatusLabel, statusBadgeStyle } from "@/lib/uploads/status";

type CategoryInfo = {
  id: string;
  name: string;
  available_in_bale: boolean;
};

type UploadItem = {
  id: string;
  original_filename: string;
  file_size_bytes: number;
  mime_type: string;
  status: string;
  categories: CategoryInfo[];
  created_at: string | null;
};

type UploadListResponse = {
  items: UploadItem[];
  total: number;
};

type UploadResponse = {
  id: string;
  filename: string;
  status: string;
  ingestion_job_id?: string | null;
  status_url?: string | null;
  message: string;
};

type IngestionJobResponse = {
  job_id: string;
  upload_id: string;
  status: string;
  attempt_count: number;
  error_code?: string | null;
  message: string;
};

type TrendImportItem = {
  id: string;
  source_system: string;
  source_trend_id: string;
  source_version: number;
  title: string;
  topic: string | null;
  category_names: string[];
  upload_id: string | null;
  trend_status: string;
  score: number | null;
  detected_at: string;
  delivery_status: string;
  dify_status: string | null;
  received_at: string;
  dify_completed_at: string | null;
};

type TrendImportListResponse = {
  items: TrendImportItem[];
  total: number;
};

const ACTIVE_INGESTION_STATUSES = new Set([
  "queued",
  "processing",
  "document_created",
  "indexing",
  "retrying",
]);
const RETRYABLE_INGESTION_STATUSES = new Set(["failed", "enqueue_failed"]);
const ACTIVE_TREND_DIFY_STATUSES = new Set([
  "queued",
  "processing",
  "document_created",
  "indexing",
  "retrying",
]);
const SUCCESSFUL_DELIVERY_STATUSES = new Set(["stored", "received", "delivered"]);
const SUCCESSFUL_DIFY_STATUSES = new Set(["indexed", "completed"]);

function ingestionStatusLabel(status: string) {
  const labels: Record<string, string> = {
    queued: "در صف پردازش",
    processing: "در حال پردازش",
    document_created: "سند ایجاد شد؛ در حال نمایه‌سازی",
    indexing: "در حال نمایه‌سازی",
    indexed: "نمایه‌سازی‌شده",
    retrying: "در انتظار تلاش مجدد",
    failed: "پردازش ناموفق",
    enqueue_failed: "ارسال به صف ناموفق",
    indexing_failed: "نمایه‌سازی ناموفق",
    stored: "ذخیره‌شده",
  };
  return labels[status] ?? status;
}

function formatFileSize(bytes: number) {
  if (bytes >= 1024 * 1024) {
    return `${(bytes / 1024 / 1024).toFixed(1)} مگابایت`;
  }
  return `${Math.max(1, Math.round(bytes / 1024))} کیلوبایت`;
}

function getFileExtension(filename: string, mimeType?: string) {
  const parts = filename.split(".");
  if (parts.length > 1) {
    const ext = parts[parts.length - 1].toUpperCase();
    if (ext.length <= 5) {
      return ext;
    }
  }
  
  if (mimeType) {
    if (mimeType.includes("pdf")) return "PDF";
    if (mimeType.includes("word") || mimeType.includes("document")) return "DOCX";
    if (mimeType.includes("sheet") || mimeType.includes("excel")) return "XLSX";
    if (mimeType.includes("audio")) return "AUDIO";
    if (mimeType.includes("text")) return "TXT";
  }
  return "نامشخص";
}

export default function UploadPage() {
  const inputRef = useRef<HTMLInputElement | null>(null);
  const pollingTimersRef = useRef<Map<string, ReturnType<typeof setTimeout>>>(new Map());
  const trendPollingTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const hasActiveTrendsRef = useRef(false);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [uploads, setUploads] = useState<UploadItem[]>([]);
  const [trends, setTrends] = useState<TrendImportItem[]>([]);
  const [isTrendsPopupOpen, setIsTrendsPopupOpen] = useState(false);
  const [selectedTrendDetails, setSelectedTrendDetails] = useState<TrendImportItem | null>(null);
  const [categories, setCategories] = useState<CategoryInfo[]>([]);
  
  // Upload category selection
  const [selectedCategoryIds, setSelectedCategoryIds] = useState<string[]>([]);
  const [isUploadCategoryModalOpen, setIsUploadCategoryModalOpen] = useState(false);

  // Edit category selection
  const [editingUpload, setEditingUpload] = useState<UploadItem | null>(null);
  const [editingTrend, setEditingTrend] = useState<TrendImportItem | null>(null);
  const [editingCategoryIds, setEditingCategoryIds] = useState<string[]>([]);
  const [isEditCategoryModalOpen, setIsEditCategoryModalOpen] = useState(false);
  const [isSavingCategory, setIsSavingCategory] = useState(false);
  const [canEditTrendCategories, setCanEditTrendCategories] = useState(false);

  // Category list editing/deletion
  const [editingCategoryId, setEditingCategoryId] = useState<string | null>(null);
  const [editingCategoryName, setEditingCategoryName] = useState<string>("");
  const [isUpdatingCategory, setIsUpdatingCategory] = useState(false);
  const [deletingCategoryId, setDeletingCategoryId] = useState<string | null>(null);
  const [updatingBaleCategoryId, setUpdatingBaleCategoryId] = useState<string | null>(null);
  const [canManageBaleCategories, setCanManageBaleCategories] = useState(false);

  // Inline category creation in select modal
  const [inlineCategoryName, setInlineCategoryName] = useState("");
  const [showInlineCreateCategory, setShowInlineCreateCategory] = useState(false);
  const [isCreatingInlineCategory, setIsCreatingInlineCategory] = useState(false);
  const [inlineCatError, setInlineCatError] = useState<string | null>(null);
  const [categorySearchTerm, setCategorySearchTerm] = useState("");

  const [isLoading, setIsLoading] = useState(true);
  const [isUploading, setIsUploading] = useState(false);
  const [deletingUploadId, setDeletingUploadId] = useState<string | null>(null);
  const [retryingUploadId, setRetryingUploadId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [isTrendsLoading, setIsTrendsLoading] = useState(true);
  const [trendsError, setTrendsError] = useState<string | null>(null);


  async function loadUploads() {
    const token = getAccessToken();
    if (!token) {
      setError("برای مشاهده و آپلود فایل ابتدا وارد پنل شوید.");
      setIsLoading(false);
      return;
    }

    const response = await apiRequest<UploadListResponse>("/uploads", { token });
    const items = response.items || [];
    setUploads(items);
    items
      .filter((item) => ACTIVE_INGESTION_STATUSES.has(item.status))
      .forEach((item) => startPolling(item.id));
    setIsLoading(false);
  }

  async function loadCategories() {
    const token = getAccessToken();
    if (!token) return;
    try {
      const response = await apiRequest<{ items: CategoryInfo[] }>("/categories", { token });
      setCategories(response.items || []);
    } catch (err) {
      console.error("Failed to load categories:", err);
    }
  }

  function scheduleTrendRefresh(items: TrendImportItem[]) {
    if (trendPollingTimerRef.current) {
      clearTimeout(trendPollingTimerRef.current);
      trendPollingTimerRef.current = null;
    }
    hasActiveTrendsRef.current = items.some(
      (item) => item.dify_status && ACTIVE_TREND_DIFY_STATUSES.has(item.dify_status),
    );
    if (!hasActiveTrendsRef.current) {
      return;
    }
    trendPollingTimerRef.current = setTimeout(() => {
      trendPollingTimerRef.current = null;
      void loadTrends();
    }, 10_000);
  }

  async function loadTrends(showLoading = false) {
    const token = getAccessToken();
    if (!token) {
      setTrendsError("برای مشاهده ترندها ابتدا وارد پنل شوید.");
      setIsTrendsLoading(false);
      return;
    }

    if (showLoading) setIsTrendsLoading(true);
    try {
      const response = await apiRequest<TrendImportListResponse>("/integrations/trends", { token });
      const items = response.items || [];
      setTrends(items);
      setTrendsError(null);
      scheduleTrendRefresh(items);
    } catch (caughtError) {
      setTrendsError(caughtError instanceof Error ? caughtError.message : "دریافت فهرست ترندها انجام نشد.");
      if (hasActiveTrendsRef.current && !trendPollingTimerRef.current) {
        trendPollingTimerRef.current = setTimeout(() => {
          trendPollingTimerRef.current = null;
          void loadTrends();
        }, 10_000);
      }
    } finally {
      setIsTrendsLoading(false);
    }
  }

  useEffect(() => {
    setCanManageBaleCategories(getStoredUser()?.role === "super_admin");
    setCanEditTrendCategories(getStoredUser()?.role === "super_admin");
    loadUploads().catch((caughtError) => {
      setError(caughtError instanceof Error ? caughtError.message : "دریافت لیست فایل‌ها انجام نشد.");
      setIsLoading(false);
    });
    loadCategories().catch((err) => {
      console.error("Failed to load categories on init:", err);
    });
    void loadTrends();
    return () => {
      pollingTimersRef.current.forEach((timer) => clearTimeout(timer));
      pollingTimersRef.current.clear();
      if (trendPollingTimerRef.current) clearTimeout(trendPollingTimerRef.current);
    };
  }, []);

  function startPolling(uploadId: string, attempt = 0) {
    if (pollingTimersRef.current.has(uploadId)) return;
    const delay = Math.min(10_000, 2_000 * 2 ** Math.min(attempt, 3));
    const timer = setTimeout(async () => {
      pollingTimersRef.current.delete(uploadId);
      const token = getAccessToken();
      if (!token) return;
      try {
        const job = await apiRequest<IngestionJobResponse>(`/uploads/${uploadId}/ingestion`, {
          token,
        });
        setUploads((current) =>
          current.map((item) =>
            item.id === uploadId ? { ...item, status: job.status } : item,
          ),
        );
        if (ACTIVE_INGESTION_STATUSES.has(job.status) && attempt < 60) {
          startPolling(uploadId, attempt + 1);
        } else if (attempt >= 60) {
          setSuccess("پردازش در سرور ادامه دارد؛ برای مشاهده وضعیت، صفحه را بعداً تازه‌سازی کنید.");
        }
      } catch (caughtError) {
        if (attempt < 6) startPolling(uploadId, attempt + 1);
      }
    }, delay);
    pollingTimersRef.current.set(uploadId, timer);
  }

  function handleFileChange(event: ChangeEvent<HTMLInputElement>) {
    setError(null);
    setSuccess(null);
    setSelectedFile(event.target.files?.[0] ?? null);
  }

  async function handleInlineCreateCategory(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const token = getAccessToken();
    if (!token) return;

    const name = inlineCategoryName.trim();
    if (!name) return;

    setIsCreatingInlineCategory(true);
    setInlineCatError(null);

    try {
      const response = await apiRequest<CategoryInfo>("/categories", {
        method: "POST",
        token,
        body: JSON.stringify({ name }),
      });
      setCategories((prev) => [...prev, response].sort((a, b) => a.name.localeCompare(b.name)));
      setSelectedCategoryIds((prev) => [...prev, response.id]);
      setInlineCategoryName("");
      setShowInlineCreateCategory(false);
      setCategorySearchTerm("");
    } catch (caughtError) {
      setInlineCatError(caughtError instanceof Error ? caughtError.message : "ایجاد دسته انجام نشد.");
    } finally {
      setIsCreatingInlineCategory(false);
    }
  }

  async function handleUpload() {
    const token = getAccessToken();
    if (!token) {
      setError("برای آپلود فایل ابتدا وارد پنل شوید.");
      return;
    }
    if (!selectedFile) {
      setError("لطفا یک فایل انتخاب کنید.");
      return;
    }

    setIsUploading(true);
    setError(null);
    setSuccess(null);

    const formData = new FormData();
    formData.append("file", selectedFile);
    
    if (selectedCategoryIds.length > 0) {
      selectedCategoryIds.forEach((id) => {
        formData.append("category_ids", id);
      });
    }

    try {
      const response = await apiRequest<UploadResponse>("/uploads", {
        method: "POST",
        token,
        body: formData,
      });
      setSuccess(response.message || "فایل با موفقیت ثبت شد.");
      if (response.status_url && ACTIVE_INGESTION_STATUSES.has(response.status)) {
        startPolling(response.id);
      }
      setSelectedFile(null);
      setSelectedCategoryIds([]);
      if (inputRef.current) {
        inputRef.current.value = "";
      }
      await loadUploads();
    } catch (caughtError) {
      setError(caughtError instanceof Error ? caughtError.message : "آپلود فایل انجام نشد.");
    } finally {
      setIsUploading(false);
    }
  }

  async function handleRetryIngestion(upload: UploadItem) {
    const token = getAccessToken();
    if (!token || !RETRYABLE_INGESTION_STATUSES.has(upload.status)) return;
    setRetryingUploadId(upload.id);
    setError(null);
    try {
      const job = await apiRequest<IngestionJobResponse>(
        `/uploads/${upload.id}/ingestion/retry`,
        { method: "POST", token },
      );
      setUploads((current) =>
        current.map((item) =>
          item.id === upload.id ? { ...item, status: job.status } : item,
        ),
      );
      setSuccess(job.message);
      if (ACTIVE_INGESTION_STATUSES.has(job.status)) startPolling(upload.id);
    } catch (caughtError) {
      setError(caughtError instanceof Error ? caughtError.message : "تلاش مجدد ثبت نشد.");
    } finally {
      setRetryingUploadId(null);
    }
  }

  async function handleDeleteUpload(upload: UploadItem) {
    const token = getAccessToken();
    if (!token) {
      setError("برای حذف فایل ابتدا وارد پنل شوید.");
      return;
    }

    const confirmed = window.confirm(`فایل ${upload.original_filename} حذف شود؟`);
    if (!confirmed) {
      return;
    }

    setDeletingUploadId(upload.id);
    setError(null);
    setSuccess(null);

    try {
      await apiRequest<void>(`/uploads/${upload.id}`, {
        method: "DELETE",
        token,
      });
      setSuccess("فایل حذف شد.");
      await loadUploads();
    } catch (caughtError) {
      setError(caughtError instanceof Error ? caughtError.message : "حذف فایل انجام نشد.");
    } finally {
      setDeletingUploadId(null);
    }
  }

  async function handleSaveEditedCategories() {
    const targetId = editingUpload?.id ?? editingTrend?.upload_id ?? null;
    const targetLabel = editingUpload?.original_filename ?? editingTrend?.title ?? "";
    if (!targetId) return;
    const token = getAccessToken();
    if (!token) {
      setError("برای ویرایش دسته ابتدا وارد پنل شوید.");
      return;
    }

    setIsSavingCategory(true);
    setError(null);
    setSuccess(null);

    try {
      await apiRequest(`/uploads/${targetId}/categories`, {
        method: "PUT",
        token,
        body: JSON.stringify({ category_ids: editingCategoryIds }),
      });
      setSuccess(
        editingUpload
          ? `دسته‌های فایل "${targetLabel}" با موفقیت ویرایش شدند.`
          : `دسته‌های ترند "${targetLabel}" با موفقیت ویرایش شدند.`,
      );
      setIsEditCategoryModalOpen(false);
      setEditingUpload(null);
      setEditingTrend(null);
      await loadUploads();
      await loadTrends();
    } catch (caughtError) {
      setError(caughtError instanceof Error ? caughtError.message : "ویرایش دسته‌ها انجام نشد.");
    } finally {
      setIsSavingCategory(false);
    }
  }

  async function handleRenameCategory(categoryId: string) {
    const token = getAccessToken();
    if (!token) return;

    const name = editingCategoryName.trim();
    if (!name) {
      setError("نام دسته نمی‌تواند خالی باشد.");
      return;
    }

    setIsUpdatingCategory(true);
    setError(null);
    setSuccess(null);

    try {
      await apiRequest(`/categories/${categoryId}`, {
        method: "PUT",
        token,
        body: JSON.stringify({ name }),
      });
      setSuccess("نام دسته با موفقیت ویرایش شد.");
      setEditingCategoryId(null);
      await loadCategories();
      await loadUploads();
    } catch (caughtError) {
      setError(caughtError instanceof Error ? caughtError.message : "ویرایش دسته انجام نشد.");
    } finally {
      setIsUpdatingCategory(false);
    }
  }

  async function handleDeleteCategory(categoryId: string, categoryName: string) {
    const token = getAccessToken();
    if (!token) return;

    const confirmed = window.confirm(`آیا از حذف دسته "${categoryName}" مطمئن هستید؟ با حذف این دسته، دسته‌بندی فایل‌های مرتبط حذف شده ولی فایل‌ها باقی خواهند ماند.`);
    if (!confirmed) return;

    setDeletingCategoryId(categoryId);
    setError(null);
    setSuccess(null);

    try {
      await apiRequest(`/categories/${categoryId}`, {
        method: "DELETE",
        token,
      });
      setSuccess(`دسته "${categoryName}" با موفقیت حذف شد.`);
      await loadCategories();
      await loadUploads();
    } catch (caughtError) {
      setError(caughtError instanceof Error ? caughtError.message : "حذف دسته انجام نشد.");
    } finally {
      setDeletingCategoryId(null);
    }
  }

  async function handleBaleAvailability(category: CategoryInfo) {
    const token = getAccessToken();
    if (!token || !canManageBaleCategories) return;

    setUpdatingBaleCategoryId(category.id);
    setError(null);
    setSuccess(null);
    try {
      const updated = await apiRequest<CategoryInfo>(
        `/categories/${category.id}/bale-availability`,
        {
          method: "PATCH",
          token,
          body: JSON.stringify({ available_in_bale: !category.available_in_bale }),
        },
      );
      setCategories((current) =>
        current.map((item) => (item.id === updated.id ? updated : item)),
      );
      setSuccess(
        updated.available_in_bale
          ? `دسته «${updated.name}» برای نمایش در ربات بله مجاز شد.`
          : `دسته «${updated.name}» از ربات بله حذف شد.`,
      );
    } catch (caughtError) {
      setError(
        caughtError instanceof Error
          ? caughtError.message
          : "تغییر دسترسی ربات بله انجام نشد.",
      );
    } finally {
      setUpdatingBaleCategoryId(null);
    }
  }


  return (
    <AppShell>
      <h1 className="page-title">آپلود فایل</h1>

      <section className="card stack">
        <h2>آپلود فایل و مدیریت اسناد</h2>
        <div className="dropzone">
          <strong>فایل را انتخاب کنید</strong>
          <span className="muted">PDF، Word، Excel، Text و Audio. حجم مجاز: ۰ تا ۵۰۰ مگابایت.</span>
          {selectedFile ? (
            <span className="file-name">
              {selectedFile.name} - {formatFileSize(selectedFile.size)}
            </span>
          ) : null}
          <input
            ref={inputRef}
            hidden
            name="file"
            onChange={handleFileChange}
            type="file"
          />

          <div style={{ width: "100%", maxWidth: "360px", margin: "14px 0", textAlign: "center" }}>
            <span className="muted" style={{ display: "block", marginBottom: "8px", fontSize: "13px" }}>
              دسته‌های انتخاب شده:{" "}
              <strong>
                {selectedCategoryIds.length > 0
                  ? selectedCategoryIds
                      .map((id) => categories.find((c) => c.id === id)?.name)
                      .filter(Boolean)
                      .join("، ")
                  : "بدون دسته (عمومی)"}
              </strong>
            </span>
            <button
              className="button secondary"
              onClick={() => setIsUploadCategoryModalOpen(true)}
              type="button"
              style={{ width: "100%" }}
            >
              انتخاب دسته‌ها ({selectedCategoryIds.length})
            </button>
          </div>

          <div className="actions">
            <button className="button secondary" onClick={() => inputRef.current?.click()} type="button">
              انتخاب فایل
            </button>
            <button className="button" disabled={isUploading || !selectedFile} onClick={handleUpload} type="button">
              {isUploading ? "در حال آپلود..." : "آپلود"}
            </button>
          </div>
        </div>
        {error ? <p className="form-error">{error}</p> : null}
        {success ? <p className="form-success">{success}</p> : null}
      </section>

      <div className="page-grid-2col" style={{ marginTop: 18 }}>
        <section className="card stack">
          <h2>دسته‌های ایجاد شده</h2>
          {categories.length === 0 ? (
            <p className="muted">هیچ دسته‌ای ثبت نشده است. ابتدا دسته جدید ایجاد کنید.</p>
          ) : (
            <div style={{ display: "grid", gap: "10px", direction: "rtl" }}>
              {categories.map((cat) => (
                <div
                  key={cat.id}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "space-between",
                    padding: "10px 14px",
                    background: "rgba(255, 255, 255, 0.03)",
                    border: "1px solid var(--border)",
                    borderRadius: "8px",
                    gap: "10px"
                  }}
                >
                  {editingCategoryId === cat.id ? (
                    <div style={{ display: "flex", gap: "8px", width: "100%", alignItems: "center" }}>
                      <input
                        className="input"
                        value={editingCategoryName}
                        onChange={(e) => setEditingCategoryName(e.target.value)}
                        style={{ height: "34px", padding: "0 8px", fontSize: "14px" }}
                        disabled={isUpdatingCategory}
                        autoFocus
                      />
                      <button
                        className="button"
                        onClick={() => handleRenameCategory(cat.id)}
                        disabled={isUpdatingCategory}
                        style={{ minHeight: "auto", height: "34px", padding: "0 12px", fontSize: "13px" }}
                      >
                        {isUpdatingCategory ? "ثبت..." : "ذخیره"}
                      </button>
                      <button
                        className="button secondary"
                        onClick={() => setEditingCategoryId(null)}
                        disabled={isUpdatingCategory}
                        style={{ minHeight: "auto", height: "34px", padding: "0 12px", fontSize: "13px" }}
                      >
                        انصراف
                      </button>
                    </div>
                  ) : (
                    <>
                      <span style={{ display: "flex", alignItems: "center", gap: "8px", fontWeight: "500", color: "#e7ddc3" }}>
                        {cat.name}
                        {cat.available_in_bale ? (
                          <small style={{ color: "#83e6ba", fontWeight: 400 }}>فعال در بله</small>
                        ) : null}
                      </span>
                      <div style={{ display: "flex", gap: "6px" }}>
                        {canManageBaleCategories ? (
                          <button
                            className="button secondary"
                            disabled={updatingBaleCategoryId === cat.id}
                            onClick={() => void handleBaleAvailability(cat)}
                            type="button"
                            style={{ padding: "4px 10px", fontSize: "12px", minHeight: "auto", height: "30px" }}
                          >
                            {updatingBaleCategoryId === cat.id
                              ? "ثبت..."
                              : cat.available_in_bale
                                ? "حذف از بله"
                                : "نمایش در بله"}
                          </button>
                        ) : null}
                        <button
                          className="button secondary"
                          onClick={() => {
                            setEditingCategoryId(cat.id);
                            setEditingCategoryName(cat.name);
                          }}
                          type="button"
                          style={{ padding: "4px 10px", fontSize: "12px", minHeight: "auto", height: "30px" }}
                        >
                          ویرایش
                        </button>
                        <button
                          className="button danger"
                          disabled={deletingCategoryId === cat.id}
                          onClick={() => handleDeleteCategory(cat.id, cat.name)}
                          type="button"
                          style={{ padding: "4px 10px", fontSize: "12px", minHeight: "auto", height: "30px" }}
                        >
                          {deletingCategoryId === cat.id ? "حذف..." : "حذف"}
                        </button>
                      </div>
                    </>
                  )}
                </div>
              ))}
            </div>
          )}
        </section>

        <section className="card stack">
          <h2>فایل‌های اخیر</h2>
          {isLoading ? <p className="muted">در حال دریافت فایل‌ها...</p> : null}
          {!isLoading && uploads.length === 0 ? <p className="muted">هنوز فایلی ثبت نشده است.</p> : null}
          {uploads.length > 0 ? (
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th>نام فایل</th>
                    <th>دسته‌ها</th>
                    <th>حجم</th>
                    <th>نوع</th>
                    <th>وضعیت</th>
                    <th>عملیات</th>
                  </tr>
                </thead>
                <tbody>
                  {uploads.map((upload) => (
                    <tr key={upload.id}>
                      <td>{upload.original_filename}</td>
                      <td>
                        <div style={{ display: "flex", flexWrap: "wrap", gap: "4px" }}>
                          {upload.categories && upload.categories.length > 0 ? (
                            upload.categories.map((cat) => (
                              <span key={cat.id} className="badge" style={{ background: "rgba(212,175,55,0.15)", color: "var(--primary)" }}>
                                {cat.name}
                              </span>
                            ))
                          ) : (
                            <span className="badge" style={{ background: "rgba(255,255,255,0.05)" }}>
                              عمومی
                            </span>
                          )}
                        </div>
                      </td>
                      <td>{formatFileSize(upload.file_size_bytes)}</td>
                      <td>{getFileExtension(upload.original_filename, upload.mime_type)}</td>
                      <td>
                        <span className="badge">{ingestionStatusLabel(upload.status)}</span>
                      </td>
                      <td>
                        <div style={{ display: "flex", gap: "6px" }}>
                          <button
                            className="button secondary"
                            onClick={() => {
                              setEditingUpload(upload);
                              setEditingTrend(null);
                              setEditingCategoryIds((upload.categories || []).map((c) => c.id));
                              setIsEditCategoryModalOpen(true);
                            }}
                            type="button"
                            style={{ padding: "4px 8px", fontSize: "12px", minHeight: "auto", height: "30px" }}
                          >
                            ویرایش دسته
                          </button>
                          {RETRYABLE_INGESTION_STATUSES.has(upload.status) ? (
                            <button
                              className="button secondary"
                              disabled={retryingUploadId === upload.id}
                              onClick={() => handleRetryIngestion(upload)}
                              type="button"
                              style={{ padding: "4px 8px", fontSize: "12px", minHeight: "auto", height: "30px" }}
                            >
                              {retryingUploadId === upload.id ? "در حال ثبت..." : "تلاش مجدد"}
                            </button>
                          ) : null}
                          <button
                            className="button danger"
                            disabled={
                              deletingUploadId === upload.id ||
                              ACTIVE_INGESTION_STATUSES.has(upload.status)
                            }
                            onClick={() => handleDeleteUpload(upload)}
                            type="button"
                            style={{ padding: "4px 8px", fontSize: "12px", minHeight: "auto", height: "30px" }}
                          >
                            {deletingUploadId === upload.id ? "در حال حذف..." : "حذف"}
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}
        </section>
      </div>

      <section className="source-entry-actions" aria-label="محتواهای دریافت‌شده از منابع خودکار">
        <button className="source-entry-button" type="button" aria-haspopup="dialog"
          aria-controls="trend-imports-popup" onClick={() => setIsTrendsPopupOpen(true)}>
          <TrendingUp size={21} aria-hidden="true" />
          <span className="source-entry-label">ترندها
            <small>{isTrendsLoading ? "در حال دریافت وضعیت…" : `${trends.length.toLocaleString("fa-IR")} مورد · رصد ترند`}</small>
          </span>
          <ArrowUpLeft size={18} className="source-entry-arrow" aria-hidden="true" />
        </button>
        <SiteArticlesSection />
      </section>

      <SourcePopup open={isTrendsPopupOpen} onClose={() => setIsTrendsPopupOpen(false)}
        title="ترندها" id="trend-imports-popup" className="source-trends-popup">
        <div className="source-popup-intro">
            <p className="muted">
              این موارد از سامانه رصد ترند دریافت شده‌اند و به‌صورت خودکار برای ثبت در Dify ارسال می‌شوند.
            </p>
          {!isTrendsLoading ? (
            <span className="badge" aria-label={`${trends.length} ترند`}>
              {trends.length.toLocaleString("fa-IR")} مورد
            </span>
          ) : null}
        </div>

        {isTrendsLoading ? (
          <p className="muted" role="status">در حال دریافت ترندها...</p>
        ) : null}

        {!isTrendsLoading && trendsError ? (
          <div
            role="alert"
            style={{
              alignItems: "center",
              background: "rgba(255, 123, 123, 0.08)",
              border: "1px solid rgba(255, 123, 123, 0.28)",
              borderRadius: "8px",
              display: "flex",
              flexWrap: "wrap",
              gap: "12px",
              justifyContent: "space-between",
              padding: "12px 14px",
            }}
          >
            <span className="form-error" style={{ margin: 0 }}>{trendsError}</span>
            <button className="button secondary" onClick={() => void loadTrends(true)} type="button">
              تلاش دوباره
            </button>
          </div>
        ) : null}

        {!isTrendsLoading && !trendsError && trends.length === 0 ? (
          <p className="muted">هنوز ترندی دریافت نشده است.</p>
        ) : null}

        {!trendsError && trends.length > 0 ? (
          <>
          <p id="trend-imports-scroll-hint" className="source-scroll-hint muted">
            ۵ مورد در پنجره فهرست نمایش داده می‌شود؛ برای موارد بیشتر اسکرول کنید. جدول را برای دیدن همه ستون‌ها به چپ یا راست حرکت دهید.
          </p>
          <div className="table-wrap source-list-viewport source-trends-table-wrap" tabIndex={0}
            role="region" aria-label="جدول ترندها" aria-describedby="trend-imports-scroll-hint">
            <table className="table source-list-table source-trends-table">
              <thead>
                <tr>
                  <th scope="col">عنوان ترند</th>
                  <th scope="col">موضوع</th>
                  <th scope="col">دسته سامانه</th>
                  <th scope="col">امتیاز</th>
                  <th scope="col">زمان شناسایی</th>
                  <th scope="col">دریافت در سامانه</th>
                  <th scope="col">وضعیت Dify</th>
                  <th scope="col">عملیات</th>
                </tr>
              </thead>
              <tbody>
                {trends.map((trend) => (
                  <tr key={trend.id} data-source-row="trend">
                    <td>
                      <div className="source-cell">
                        <strong className="source-preview" style={{ color: "#f2e8cf", fontWeight: 600 }}>{trend.title}</strong>
                        <button className="source-details-button" type="button" onClick={() => setSelectedTrendDetails(trend)}
                          aria-label={`نمایش متن و جزئیات ترند ${trend.title}`}>متن و جزئیات کامل</button>
                      </div>
                    </td>
                    <td><div className="source-cell"><span className="source-preview">{trend.topic || "—"}</span></div></td>
                    <td>
                      <div className="source-cell">
                      {trend.category_names.length > 0 ? (
                        <div style={{ display: "flex", flexWrap: "wrap", gap: "4px" }}>
                          {trend.category_names.map((categoryName) => (
                            <span
                              key={categoryName}
                              className="badge"
                              style={{ background: "rgba(212,175,55,0.15)", color: "var(--primary)" }}
                            >
                              {categoryName}
                            </span>
                          ))}
                        </div>
                      ) : (
                        <span className="muted">تعیین‌نشده</span>
                      )}</div>
                    </td>
                    <td><div className="source-cell">{trend.score == null ? "—" : trend.score.toLocaleString("fa-IR", { maximumFractionDigits: 2 })}</div></td>
                    <td><div className="source-cell">{new Date(trend.detected_at).toLocaleString("fa-IR")}</div></td>
                    <td>
                      <div className="source-cell">
                      <span className="badge" style={statusBadgeStyle(trend.delivery_status, SUCCESSFUL_DELIVERY_STATUSES)}>
                        {deliveryStatusLabel(trend.delivery_status)}
                      </span></div>
                    </td>
                    <td>
                      <div className="source-cell">
                      <span className="badge" style={statusBadgeStyle(trend.dify_status, SUCCESSFUL_DIFY_STATUSES)}>
                        {difyStatusLabel(trend.dify_status)}
                      </span></div>
                    </td>
                    <td>
                      <div className="source-cell">
                      {canEditTrendCategories && trend.upload_id ? (
                        <button
                          className="button secondary"
                          onClick={() => {
                            setEditingTrend(trend);
                            setEditingUpload(null);
                            setEditingCategoryIds(
                              categories
                                .filter((cat) => trend.category_names.includes(cat.name))
                                .map((cat) => cat.id),
                            );
                            setIsEditCategoryModalOpen(true);
                          }}
                          type="button"
                          style={{ padding: "4px 8px", fontSize: "12px", minHeight: "auto", height: "30px" }}
                        >
                          ویرایش دسته
                        </button>
                      ) : (
                        <span className="muted">—</span>
                      )}</div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          </>
        ) : null}
      </SourcePopup>

      <SourcePopup open={selectedTrendDetails !== null} onClose={() => setSelectedTrendDetails(null)}
        title="متن و جزئیات ترند" id="trend-details-popup" className="source-details-popup">
        {selectedTrendDetails ? (
          <div className="source-full-details">
            <p className="source-full-text">{selectedTrendDetails.title}</p>
            <dl>
              <dt>موضوع</dt><dd>{selectedTrendDetails.topic || "—"}</dd>
              <dt>دسته سامانه</dt><dd>{selectedTrendDetails.category_names.join("، ") || "تعیین‌نشده"}</dd>
              <dt>امتیاز</dt><dd>{selectedTrendDetails.score == null ? "—" : selectedTrendDetails.score.toLocaleString("fa-IR", { maximumFractionDigits: 2 })}</dd>
              <dt>زمان شناسایی</dt><dd>{new Date(selectedTrendDetails.detected_at).toLocaleString("fa-IR")}</dd>
              <dt>دریافت در سامانه</dt><dd>{deliveryStatusLabel(selectedTrendDetails.delivery_status)}</dd>
              <dt>وضعیت Dify</dt><dd>{difyStatusLabel(selectedTrendDetails.dify_status)}</dd>
            </dl>
          </div>
        ) : null}
      </SourcePopup>

      {/* Upload Categories Popup Modal */}
      {isUploadCategoryModalOpen ? (
        <div className="chat-confirm-backdrop" role="presentation" onClick={() => { setIsUploadCategoryModalOpen(false); setShowInlineCreateCategory(false); setInlineCatError(null); setCategorySearchTerm(""); }}>
          <div
            className="chat-confirm select-repo-modal"
            role="dialog"
            aria-modal="true"
            aria-label="انتخاب دسته‌ها"
            onClick={(event) => event.stopPropagation()}
            style={{ minWidth: "380px" }}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "12px", gap: "10px" }}>
              <p style={{ fontWeight: "bold", color: "var(--primary)", fontSize: "16px", textAlign: "right", margin: 0 }}>
                انتخاب دسته‌ها برای فایل جدید
              </p>
              <button
                className="button secondary"
                onClick={() => {
                  setInlineCatError(null);
                  setShowInlineCreateCategory((prev) => !prev);
                }}
                type="button"
                style={{ minHeight: "auto", height: "30px", padding: "0 10px", fontSize: "12px", width: "auto" }}
              >
                {showInlineCreateCategory ? "بستن فرم" : "افزودن دسته"}
              </button>
            </div>

            {showInlineCreateCategory ? (
              <form
                onSubmit={handleInlineCreateCategory}
                style={{
                  display: "grid",
                  gridTemplateColumns: "1fr auto",
                  gap: "8px",
                  alignItems: "center",
                  padding: "10px",
                  background: "rgba(255,255,255,0.03)",
                  border: "1px solid var(--border)",
                  borderRadius: "6px",
                  marginBottom: "12px",
                  direction: "rtl"
                }}
              >
                <input
                  className="input"
                  placeholder="نام دسته جدید..."
                  value={inlineCategoryName}
                  onChange={(e) => setInlineCategoryName(e.target.value)}
                  style={{ height: "34px", fontSize: "13px" }}
                  required
                  autoFocus
                />
                <button
                  className="button"
                  type="submit"
                  disabled={isCreatingInlineCategory}
                  style={{ minHeight: "auto", height: "34px", padding: "0 12px", fontSize: "13px" }}
                >
                  {isCreatingInlineCategory ? "ثبت..." : "ثبت"}
                </button>
                {inlineCatError ? (
                  <p className="form-error" style={{ gridColumn: "span 2", fontSize: "12px", margin: "4px 0 0" }}>
                    {inlineCatError}
                  </p>
                ) : null}
              </form>
            ) : null}

            {categories.length > 0 ? (
              <input
                className="input"
                placeholder="جستجوی دسته..."
                value={categorySearchTerm}
                onChange={(e) => setCategorySearchTerm(e.target.value)}
                style={{ height: "34px", fontSize: "13px", marginBottom: "10px", direction: "rtl", width: "100%" }}
              />
            ) : null}

            {categories.length === 0 ? (
              <p className="muted" style={{ fontSize: "14px", margin: "10px 0", textAlign: "right" }}>
                هیچ دسته‌ای یافت نشد. ابتدا دسته جدید ایجاد کنید.
              </p>
            ) : (() => {
              const filtered = categories.filter((cat) =>
                cat.name.toLowerCase().includes(categorySearchTerm.toLowerCase())
              );
              if (filtered.length === 0) {
                return (
                  <p className="muted" style={{ fontSize: "14px", margin: "10px 0", textAlign: "right" }}>
                    دسته‌ای با این نام یافت نشد.
                  </p>
                );
              }
              return (
                <div className="repo-list" style={{ maxHeight: "240px", overflowY: "auto", display: "grid", gap: "10px", margin: "10px 0", textAlign: "right" }}>
                  {filtered.map((cat) => (
                    <label key={cat.id} className="checkbox-row" style={{ cursor: "pointer", display: "flex", alignItems: "center", gap: "10px", direction: "rtl" }}>
                      <input
                        type="checkbox"
                        checked={selectedCategoryIds.includes(cat.id)}
                        onChange={(e) => {
                          if (e.target.checked) {
                            setSelectedCategoryIds((prev) => [...prev, cat.id]);
                          } else {
                            setSelectedCategoryIds((prev) => prev.filter((id) => id !== cat.id));
                          }
                        }}
                        style={{ cursor: "pointer" }}
                      />
                      <span>{cat.name}</span>
                    </label>
                  ))}
                </div>
              );
            })()}
            <div className="chat-confirm-actions" style={{ marginTop: "14px" }}>
              <button
                className="button"
                onClick={() => { setIsUploadCategoryModalOpen(false); setShowInlineCreateCategory(false); setInlineCatError(null); setCategorySearchTerm(""); }}
                type="button"
                style={{ width: "100%" }}
              >
                تایید
              </button>
            </div>
          </div>
        </div>
      ) : null}

      {/* Edit Categories Popup Modal */}
      {isEditCategoryModalOpen && (editingUpload || editingTrend) ? (
        <SourcePopup open={isEditCategoryModalOpen}
          onClose={() => { if (!isSavingCategory) { setIsEditCategoryModalOpen(false); setEditingUpload(null); setEditingTrend(null); setCategorySearchTerm(""); } }}
          title={editingUpload ? "ویرایش دسته‌های فایل" : "ویرایش دسته‌های ترند"}
          id="edit-categories-popup" className="source-category-popup">
            <span className="muted" style={{ display: "block", marginBottom: "12px", fontSize: "13px", textAlign: "right", wordBreak: "break-all" }}>
              {editingUpload ? editingUpload.original_filename : editingTrend?.title}
            </span>
            {error ? <p className="form-error" role="alert">{error}</p> : null}
            {categories.length > 0 ? (
              <input
                className="input"
                placeholder="جستجوی دسته..."
                value={categorySearchTerm}
                onChange={(e) => setCategorySearchTerm(e.target.value)}
                style={{ height: "34px", fontSize: "13px", marginBottom: "10px", direction: "rtl", width: "100%" }}
              />
            ) : null}
            {categories.length === 0 ? (
              <p className="muted" style={{ fontSize: "14px", margin: "10px 0", textAlign: "right" }}>
                هیچ دسته‌ای یافت نشد. ابتدا دسته جدید ایجاد کنید.
              </p>
            ) : (() => {
              const filtered = categories.filter((cat) =>
                cat.name.toLowerCase().includes(categorySearchTerm.toLowerCase())
              );
              if (filtered.length === 0) {
                return (
                  <p className="muted" style={{ fontSize: "14px", margin: "10px 0", textAlign: "right" }}>
                    دسته‌ای با این نام یافت نشد.
                  </p>
                );
              }
              return (
                <div className="repo-list" style={{ maxHeight: "240px", overflowY: "auto", display: "grid", gap: "10px", margin: "10px 0", textAlign: "right" }}>
                  {filtered.map((cat) => (
                    <label key={cat.id} className="checkbox-row" style={{ cursor: "pointer", display: "flex", alignItems: "center", gap: "10px", direction: "rtl" }}>
                      <input
                        type="checkbox"
                        checked={editingCategoryIds.includes(cat.id)}
                        onChange={(e) => {
                          if (e.target.checked) {
                            setEditingCategoryIds((prev) => [...prev, cat.id]);
                          } else {
                            setEditingCategoryIds((prev) => prev.filter((id) => id !== cat.id));
                          }
                        }}
                        style={{ cursor: "pointer" }}
                      />
                      <span>{cat.name}</span>
                    </label>
                  ))}
                </div>
              );
            })()}
            <div className="chat-confirm-actions" style={{ marginTop: "14px", display: "flex", gap: "10px" }}>
              <button
                className="button"
                disabled={isSavingCategory}
                onClick={handleSaveEditedCategories}
                type="button"
                style={{ flex: 1 }}
              >
                {isSavingCategory ? "در حال ذخیره..." : "ذخیره تغییرات"}
              </button>
              <button
                className="button secondary"
                disabled={isSavingCategory}
                onClick={() => { setIsEditCategoryModalOpen(false); setEditingUpload(null); setEditingTrend(null); setCategorySearchTerm(""); }}
                type="button"
                style={{ flex: 1 }}
              >
                انصراف
              </button>
            </div>
        </SourcePopup>
      ) : null}
    </AppShell>
  );
}
