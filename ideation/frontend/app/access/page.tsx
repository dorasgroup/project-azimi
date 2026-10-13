"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { apiRequest } from "@/lib/api/client";
import { LoginResponse, saveSession } from "@/lib/auth/session";

export default function SharedAccessPage() {
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;

    async function enter() {
      const token = window.location.hash.slice(1).trim();
      window.history.replaceState(null, "", "/access");
      if (!token) {
        setError("لینک ورود ناقص است.");
        return;
      }
      try {
        const session = await apiRequest<LoginResponse>("/auth/guest-access", {
          method: "POST",
          body: JSON.stringify({ token }),
        });
        if (!active) return;
        saveSession(session);
        router.replace("/dashboard");
      } catch {
        if (active) setError("این لینک معتبر نیست یا غیرفعال شده است.");
      }
    }

    void enter();
    return () => {
      active = false;
    };
  }, [router]);

  return (
    <main className="login-page">
      <section className="card login-card">
        <h1 className="page-title">ورود مهمان</h1>
        <p className={error ? "form-error" : "page-subtitle"}>
          {error ?? "در حال ورود به سامانه..."}
        </p>
      </section>
    </main>
  );
}
