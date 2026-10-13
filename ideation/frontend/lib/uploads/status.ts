export function deliveryStatusLabel(status: string) {
  const labels: Record<string, string> = {
    stored: "دریافت موفق",
    received: "دریافت موفق",
    delivered: "دریافت موفق",
    pending: "در انتظار دریافت",
    queued: "در حال دریافت",
    processing: "در حال دریافت",
    failed: "دریافت ناموفق",
    enqueue_failed: "دریافت ناموفق",
  };
  return labels[status] ?? status;
}

export function difyStatusLabel(status: string | null) {
  if (!status) return "در انتظار ارسال";
  const labels: Record<string, string> = {
    not_started: "در انتظار ارسال",
    update_pending: "در انتظار به‌روزرسانی مقاله",
    queued: "در صف ارسال",
    processing: "در حال پردازش",
    document_created: "سند ایجاد شد؛ در حال نمایه‌سازی",
    indexing: "در حال نمایه‌سازی",
    retrying: "در انتظار تلاش مجدد",
    indexed: "ثبت موفق در Dify",
    completed: "ثبت موفق در Dify",
    failed: "ثبت ناموفق",
    enqueue_failed: "ارسال به صف ناموفق",
    indexing_failed: "نمایه‌سازی ناموفق",
  };
  return labels[status] ?? status;
}

export function statusBadgeStyle(status: string | null, successfulStatuses: Set<string>) {
  if (status && successfulStatuses.has(status)) {
    return {
      background: "rgba(78, 205, 150, 0.12)",
      borderColor: "rgba(78, 205, 150, 0.34)",
      color: "#83e6ba",
    };
  }
  if (status && (status === "failed" || status === "enqueue_failed" || status === "indexing_failed")) {
    return {
      background: "rgba(255, 123, 123, 0.12)",
      borderColor: "rgba(255, 123, 123, 0.38)",
      color: "#ff9c9c",
    };
  }
  return {
    background: "rgba(212, 175, 55, 0.12)",
    borderColor: "rgba(212, 175, 55, 0.3)",
    color: "var(--primary)",
  };
}
