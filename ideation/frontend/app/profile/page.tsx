"use client";

import { FormEvent, useEffect, useState } from "react";

import { AppShell } from "@/components/layout/app-shell";
import { apiRequest } from "@/lib/api/client";
import { getAccessToken, getStoredUser, type AuthUser, updateStoredUser } from "@/lib/auth/session";

export default function ProfilePage() {
  const [email, setEmail] = useState("");
  const [fullName, setFullName] = useState("");
  const [password, setPassword] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  useEffect(() => {
    const user = getStoredUser();
    if (user) {
      setEmail(user.email);
      setFullName(user.full_name ?? "");
    }
  }, []);

  async function handleUpdateProfile(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const token = getAccessToken();
    const currentUser = getStoredUser();
    if (!token || !currentUser) {
      setError("برای ویرایش پروفایل ابتدا وارد پنل شوید.");
      return;
    }

    setIsSubmitting(true);
    setError(null);
    setSuccess(null);

    try {
      const body: Record<string, unknown> = {
        email,
        full_name: fullName,
      };
      if (password.trim()) {
        body.password = password;
      }

      const updatedUser = await apiRequest<AuthUser>("/auth/me", {
        method: "PATCH",
        token,
        body: JSON.stringify(body),
      });
      updateStoredUser({ ...currentUser, ...updatedUser });
      setPassword("");
      setSuccess("پروفایل با موفقیت به‌روزرسانی شد.");
    } catch (caughtError) {
      const message = caughtError instanceof Error ? caughtError.message : "ویرایش پروفایل انجام نشد.";
      setError(message === "Email already exists" ? "این ایمیل قبلا ثبت شده است." : message);
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <AppShell>
      <h1 className="page-title">پروفایل</h1>
      <section className="card stack">
        <h2>ویرایش اطلاعات حساب</h2>
        <form className="form" onSubmit={handleUpdateProfile} style={{ maxWidth: "none" }}>
          <div className="form-grid">
            <label>
              نام کامل
              <input className="input" onChange={(event) => setFullName(event.target.value)} required value={fullName} />
            </label>
            <label>
              ایمیل
              <input className="input" onChange={(event) => setEmail(event.target.value)} required type="email" value={email} />
            </label>
            <label>
              رمز جدید
              <input
                autoComplete="new-password"
                className="input"
                minLength={8}
                onChange={(event) => setPassword(event.target.value)}
                placeholder="برای تغییر ندادن خالی بگذارید"
                type="password"
                value={password}
              />
            </label>
          </div>
          {error ? <p className="form-error">{error}</p> : null}
          {success ? <p className="form-success">{success}</p> : null}
          <button className="button" disabled={isSubmitting} type="submit">
            {isSubmitting ? "در حال ذخیره..." : "ذخیره تغییرات"}
          </button>
        </form>
      </section>
    </AppShell>
  );
}
