"use client";

import { useEffect, useState } from "react";

import { AppShell } from "@/components/layout/app-shell";
import { apiRequest } from "@/lib/api/client";

type CheckState = {
  label: string;
  status: "checking" | "ok" | "failed";
  detail: string;
};

type DifyHealth = {
  status: string;
  dify: {
    datasets_total: number | null;
    message?: string;
  };
};

export default function ConnectionsPage() {
  const [checks, setChecks] = useState<CheckState[]>([
    { label: "Backend API", status: "checking", detail: "در انتظار بررسی" },
    { label: "Dify API", status: "checking", detail: "در انتظار بررسی" },
  ]);

  async function runChecks() {
    setChecks([
      { label: "Backend API", status: "checking", detail: "در حال اتصال به API پروژه..." },
      { label: "Dify API", status: "checking", detail: "در حال بررسی اتصال backend به Dify..." },
    ]);

    const nextChecks: CheckState[] = [];

    try {
      await apiRequest<{ status: string }>("/health");
      nextChecks.push({
        label: "Backend API",
        status: "ok",
        detail: "API پروژه روی پورت 8000 در دسترس است.",
      });
    } catch (caughtError) {
      nextChecks.push({
        label: "Backend API",
        status: "failed",
        detail: caughtError instanceof Error ? caughtError.message : "اتصال به API پروژه برقرار نشد.",
      });
    }

    try {
      const difyHealth = await apiRequest<DifyHealth>("/health/dify");
      if (difyHealth.status !== "ok") {
        nextChecks.push({
          label: "Dify API",
          status: "failed",
          detail: difyHealth.dify.message ?? "اتصال backend به Dify برقرار نشد.",
        });
        setChecks(nextChecks);
        return;
      }
      nextChecks.push({
        label: "Dify API",
        status: "ok",
        detail: `اتصال به Dify برقرار است. تعداد Knowledge Base قابل مشاهده: ${difyHealth.dify.datasets_total ?? 0}`,
      });
    } catch (caughtError) {
      nextChecks.push({
        label: "Dify API",
        status: "failed",
        detail: caughtError instanceof Error ? caughtError.message : "اتصال backend به Dify برقرار نشد.",
      });
    }

    setChecks(nextChecks);
  }

  useEffect(() => {
    runChecks();
  }, []);

  return (
    <AppShell>
      <h1 className="page-title">وضعیت اتصالات</h1>

      <section className="card stack">
        <div className="section-header">
          <h2>بررسی سلامت سرویس‌ها</h2>
          <button className="button secondary" onClick={runChecks} type="button">
            بررسی مجدد
          </button>
        </div>
        <div className="connection-grid">
          {checks.map((check) => (
            <article className={`connection-card ${check.status}`} key={check.label}>
              <div>
                <h3>{check.label}</h3>
                <p>{check.detail}</p>
              </div>
              <span className={`badge connection-${check.status}`}>
                {check.status === "checking" ? "در حال بررسی" : check.status === "ok" ? "متصل" : "قطع"}
              </span>
            </article>
          ))}
        </div>
      </section>
    </AppShell>
  );
}
