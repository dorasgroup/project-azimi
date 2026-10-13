import { getAccessToken } from "@/lib/auth/session";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "/api/v1";

type FrontendLogLevel = "info" | "warning" | "error";

type FrontendLogPayload = {
  level: FrontendLogLevel;
  event_type: string;
  message: string;
  metadata?: Record<string, unknown>;
};

export async function logFrontendEvent(payload: FrontendLogPayload) {
  const token = getAccessToken();
  if (!token) {
    return;
  }

  try {
    await fetch(`${API_BASE_URL}/logs/frontend`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify(payload),
    });
  } catch {
    // Logging must never break the user flow.
  }
}

export function installFrontendErrorLogging() {
  if (typeof window === "undefined") {
    return () => undefined;
  }

  const onError = (event: ErrorEvent) => {
    void logFrontendEvent({
      level: "error",
      event_type: "window_error",
      message: event.message || "Unhandled frontend error",
      metadata: {
        filename: event.filename,
        lineno: event.lineno,
        colno: event.colno,
        stack: event.error instanceof Error ? event.error.stack : undefined,
        href: window.location.href,
      },
    });
  };

  const onRejection = (event: PromiseRejectionEvent) => {
    const reason = event.reason instanceof Error ? event.reason.message : String(event.reason);
    void logFrontendEvent({
      level: "error",
      event_type: "unhandled_rejection",
      message: reason || "Unhandled promise rejection",
      metadata: {
        stack: event.reason instanceof Error ? event.reason.stack : undefined,
        href: window.location.href,
      },
    });
  };

  window.addEventListener("error", onError);
  window.addEventListener("unhandledrejection", onRejection);

  return () => {
    window.removeEventListener("error", onError);
    window.removeEventListener("unhandledrejection", onRejection);
  };
}
