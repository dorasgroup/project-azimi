"use client";

import { useEffect, useState } from "react";

import { AppShell } from "@/components/layout/app-shell";
import { apiRequest } from "@/lib/api/client";
import { getAccessToken } from "@/lib/auth/session";

type SystemLogItem = {
  id: string;
  level: "info" | "warning" | "error";
  event_type: string;
  message: string;
  request_id: string | null;
  created_at: string;
};

type SystemLogsResponse = {
  items: SystemLogItem[];
  total: number;
};

type LogFileResponse = {
  filename: string;
  lines: string[];
};

export default function LogsPage() {
  const [logs, setLogs] = useState<SystemLogItem[]>([]);
  const [backendErrorLines, setBackendErrorLines] = useState<string[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isClearing, setIsClearing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  async function loadSystemLogs() {
    const token = getAccessToken();
    if (!token) {
      setError("برای مشاهده لاگ‌ها ابتدا وارد پنل شوید.");
      setIsLoading(false);
      return;
    }

    setIsLoading(true);
    setError(null);

    try {
      const [logResponse, fileResponse] = await Promise.all([
        apiRequest<SystemLogsResponse>("/admin/system-logs?limit=50", { token }),
        apiRequest<LogFileResponse>("/admin/log-files/backend-errors.log?lines=120", { token }),
      ]);
      setLogs(logResponse.items);
      setBackendErrorLines(fileResponse.lines);
    } catch (caughtError) {
      setError(caughtError instanceof Error ? caughtError.message : "دریافت لاگ‌ها انجام نشد.");
    } finally {
      setIsLoading(false);
    }
  }

  async function clearResolvedLogs() {
    const token = getAccessToken();
    if (!token) {
      setError("برای پاک‌سازی لاگ‌ها ابتدا وارد پنل شوید.");
      return;
    }

    setIsClearing(true);
    setError(null);
    setSuccess(null);

    try {
      await Promise.all([
        apiRequest("/admin/system-logs", { method: "DELETE", token }),
        apiRequest("/admin/log-files/backend-errors.log", { method: "DELETE", token }),
      ]);
      setSuccess("لاگ‌های رفع‌شده پاک شدند و وضعیت تازه دوباره بررسی شد.");
      await loadSystemLogs();
    } catch (caughtError) {
      setError(caughtError instanceof Error ? caughtError.message : "پاک‌سازی لاگ‌ها انجام نشد.");
    } finally {
      setIsClearing(false);
    }
  }

  useEffect(() => {
    loadSystemLogs();
  }, []);

  return (
    <AppShell>
      <h1 className="page-title">لاگ‌های سیستم</h1>

      <section className="card stack">
        <div className="section-header">
          <h2>رویدادهای سیستم</h2>
          <div className="actions">
            <button className="button secondary" disabled={isLoading || isClearing} onClick={loadSystemLogs} type="button">
              {isLoading ? "در حال بررسی..." : "تازه‌سازی"}
            </button>
            <button className="button danger" disabled={isLoading || isClearing} onClick={clearResolvedLogs} type="button">
              {isClearing ? "در حال پاک‌سازی..." : "پاک‌سازی لاگ‌های رفع‌شده"}
            </button>
          </div>
        </div>
        {error ? <p className="form-error">{error}</p> : null}
        {success ? <p className="form-success">{success}</p> : null}
        {isLoading ? <p className="muted">در حال دریافت لاگ‌ها...</p> : null}
        {!isLoading && logs.length === 0 ? <p className="muted">فعلا لاگی ثبت نشده است.</p> : null}
        {logs.length > 0 ? (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>زمان</th>
                  <th>سطح</th>
                  <th>نوع</th>
                  <th>پیام</th>
                  <th>Request ID</th>
                </tr>
              </thead>
              <tbody>
                {logs.map((log) => (
                  <tr key={log.id}>
                    <td>{new Date(log.created_at).toLocaleString("fa-IR")}</td>
                    <td>
                      <span className={`badge log-${log.level}`}>{log.level}</span>
                    </td>
                    <td>{log.event_type}</td>
                    <td className="log-message">{log.message}</td>
                    <td>{log.request_id ?? "-"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </section>

      <section className="card stack" style={{ marginTop: 18 }}>
        <h2>آخرین خطاهای backend</h2>
        {backendErrorLines.length === 0 ? (
          <p className="muted">فایل خطای backend خالی است.</p>
        ) : (
          <div className="log-file">
            <pre>{backendErrorLines.join("\n")}</pre>
          </div>
        )}
      </section>
    </AppShell>
  );
}
