// Keep browser requests same-origin. Next.js proxies `/api/*` to the API gateway
// so `127.0.0.1` is never resolved against the end user's machine.
const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "/api/v1";
const LOGIN_PATH = "/login";
const AUTH_MESSAGE_KEY = "ideation_auth_message";

function handleInvalidSession(path: string) {
  if (typeof window === "undefined" || path === "/auth/login" || path === "/auth/guest-access") {
    return;
  }

  try {
    localStorage.removeItem("ideation_access_token");
    localStorage.removeItem("ideation_user");
    sessionStorage.setItem(AUTH_MESSAGE_KEY, "نشست شما منقضی شده است. لطفاً دوباره وارد شوید.");
  } catch {
    // Best effort only; navigation still recovers the user flow.
  }

  if (window.location.pathname !== LOGIN_PATH) {
    window.location.assign(LOGIN_PATH);
  }
}

async function logApiFailure(path: string, message: string, status?: number, details?: unknown) {
  if (typeof window === "undefined") {
    return;
  }

  try {
    const { logFrontendEvent } = await import("@/lib/logging/client");
    await logFrontendEvent({
      level: "error",
      event_type: "api_request_failed",
      message,
      metadata: {
        path,
        status,
        details,
        href: window.location.href,
      },
    });
  } catch {
    // Logging must never hide the original API error.
  }
}

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
    public readonly details?: unknown,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

function getErrorMessage(details: unknown, fallback: string) {
  if (typeof details === "object" && details !== null && "detail" in details) {
    const detail = (details as { detail: unknown }).detail;
    if (typeof detail === "string") {
      return detail;
    }
    if (typeof detail === "object" && detail !== null && "message" in detail) {
      return String((detail as { message: unknown }).message);
    }
  }

  return fallback;
}

export async function apiRequest<T>(
  path: string,
  options: RequestInit & { token?: string } = {},
): Promise<T> {
  const headers = new Headers(options.headers);
  if (!(options.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  if (options.token) {
    headers.set("Authorization", `Bearer ${options.token}`);
  }

  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...options,
    headers,
  });

  let responseText = "";
  if (!response.bodyUsed) {
    try {
      responseText = await response.text();
    } catch {
      responseText = "";
    }
  }

  if (!response.ok) {
    let details: unknown = responseText;
    if (responseText) {
      try {
        details = JSON.parse(responseText);
      } catch {
        details = responseText;
      }
    }

    const message = getErrorMessage(details, `API request failed: ${response.status}`);

    if (response.status === 401) {
      handleInvalidSession(path);
    }

    await logApiFailure(path, message, response.status, details);
    throw new ApiError(message, response.status, details);
  }

  if (response.status === 204 || !responseText) {
    return undefined as T;
  }

  try {
    return JSON.parse(responseText) as T;
  } catch {
    return responseText as unknown as T;
  }
}
