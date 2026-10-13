"use client";

import { useRouter } from "next/navigation";
import { FormEvent, useEffect, useState } from "react";

import { apiRequest } from "@/lib/api/client";
import { LoginResponse, saveSession } from "@/lib/auth/session";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("admin@example.com");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  useEffect(() => {
    const authMessage = sessionStorage.getItem("ideation_auth_message");
    if (authMessage) {
      setError(authMessage);
      sessionStorage.removeItem("ideation_auth_message");
    }
  }, []);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setIsSubmitting(true);

    const formData = new FormData(event.currentTarget);
    const submittedEmail = String(formData.get("email") ?? "");
    const submittedPassword = String(formData.get("password") ?? "");

    try {
      const session = await apiRequest<LoginResponse>("/auth/login", {
        method: "POST",
        body: JSON.stringify({ email: submittedEmail, password: submittedPassword }),
      });
      saveSession(session);
      router.push("/dashboard");
    } catch (caughtError) {
      const message = caughtError instanceof Error ? caughtError.message : "ورود انجام نشد.";
      setError(
        message === "Invalid email or password" || message === "Invalid credentials"
          ? "ایمیل یا رمز عبور درست نیست."
          : message,
      );
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <main className="login-page">
      <section className="card login-card">
        <h1 className="page-title">ورود به سامانه دانش داخلی</h1>
        <p className="page-subtitle">با حساب داخلی خود وارد شوید.</p>
        <form className="form" onSubmit={handleSubmit}>
          <label>
            ایمیل
            <input
              className="input"
              name="email"
              onChange={(event) => setEmail(event.target.value)}
              placeholder="user@example.com"
              required
              type="email"
              value={email}
            />
          </label>
          <label>
            رمز عبور
            <input
              autoComplete="current-password"
              className="input"
              name="password"
              onChange={(event) => setPassword(event.target.value)}
              required
              type="password"
              value={password}
            />
          </label>
          {error ? <p className="form-error">{error}</p> : null}
          <button className="button" disabled={isSubmitting} type="submit">
            {isSubmitting ? "در حال ورود..." : "ورود"}
          </button>
        </form>
      </section>
    </main>
  );
}
