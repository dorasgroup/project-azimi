"use client";

import { MessageSquareText, Upload, UserRound, Settings } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { apiRequest } from "@/lib/api/client";
import { clearSession, getAccessToken, getStoredUser, type AuthUser } from "@/lib/auth/session";
import { installFrontendErrorLogging } from "@/lib/logging/client";

const mainNavItems = [
  { href: "/dashboard", label: "صفحه چت", icon: MessageSquareText },
  { href: "/upload", label: "آپلود فایل", icon: Upload },
  { href: "/profile", label: "پروفایل", icon: UserRound },
];

const adminMenuItems = [
  { href: "/admin", label: "مدیریت کاربران" },
  { href: "/logs", label: "لاگ‌های سیستم" },
  { href: "/connections", label: "وضعیت اتصالات" },
];

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [isCheckingSession, setIsCheckingSession] = useState(true);
  const [isMenuOpen, setIsMenuOpen] = useState(false);
  const [currentUser, setCurrentUser] = useState<AuthUser | null>(null);

  useEffect(() => installFrontendErrorLogging(), []);

  useEffect(() => {
    setIsMenuOpen(false);
  }, [pathname]);

  useEffect(() => {
    const token = getAccessToken();
    if (!token) {
      sessionStorage.setItem("ideation_auth_message", "برای ورود به پنل ابتدا وارد حساب خود شوید.");
      router.replace(`/login?next=${encodeURIComponent(pathname)}`);
      return;
    }

    const accessToken = token;
    let isActive = true;

    async function verifySession() {
      try {
        const verifiedUser = await apiRequest<AuthUser>("/auth/me", { token: accessToken });
        if (isActive) {
          setCurrentUser(verifiedUser);
          setIsCheckingSession(false);
        }
      } catch {
        clearSession();
        sessionStorage.setItem("ideation_auth_message", "نشست شما معتبر نیست. لطفا دوباره وارد شوید.");
        router.replace(`/login?next=${encodeURIComponent(pathname)}`);
      }
    }

    setCurrentUser(getStoredUser());
    void verifySession();

    return () => {
      isActive = false;
    };
  }, [pathname, router]);

  if (isCheckingSession) {
    return (
      <main className="login-page">
        <section className="card login-card">
          <p className="page-subtitle">در حال بررسی ورود...</p>
        </section>
      </main>
    );
  }

  // Admin menu items are used only in gear dropdown

  function handleLogout() {
    clearSession();
    router.replace("/login");
  }

  const gearMenuItems = currentUser?.role === "super_admin" ? adminMenuItems : [];
  const visibleMainNavItems =
    currentUser?.role === "guest"
      ? mainNavItems.filter((item) => item.href === "/dashboard")
      : mainNavItems;

  return (
    <div className="shell app-frame">
      <header className="topbar">
        <div className="topbar-inner">
          <Link className="brand-compact" href="/dashboard">
            <span className="brand-title">سامانه ایده یابی حامین</span>
          </Link>
          <nav className="main-nav" aria-label="ناوبری اصلی">
            {visibleMainNavItems.map((item) => {
              const isActive = pathname === item.href || (item.href === "/dashboard" && pathname === "/");
              const IconComponent = item.icon;
              return (
                <Link
                  className={`main-nav-link ${isActive ? "active" : ""}`}
                  href={item.href}
                  key={item.href}
                >
                  <IconComponent size={18} className="main-nav-icon" />
                  <span>{item.label}</span>
                </Link>
              );
            })}
          </nav>
        </div>
        <div className="topbar-actions">
          <button
            aria-expanded={isMenuOpen}
            aria-label="باز کردن تنظیمات"
            className="gear-button"
            onClick={() => setIsMenuOpen((current) => !current)}
            type="button"
          >
            <Settings className="gear-icon" size={24} />
          </button>
          {isMenuOpen ? (
            <nav className="gear-menu">
              {visibleMainNavItems.map((item) => (
                <Link className="gear-menu-mobile-only" href={item.href} key={item.href}>
                  {item.label}
                </Link>
              ))}
              {gearMenuItems.map((item) => (
                <Link href={item.href} key={item.href}>
                  {item.label}
                </Link>
              ))}
              <button className="nav-action" onClick={handleLogout} type="button">
                خروج
              </button>
            </nav>
          ) : null}
        </div>
      </header>
      {isMenuOpen ? (
        <button aria-label="بستن منو" className="menu-backdrop" onClick={() => setIsMenuOpen(false)} type="button" />
      ) : null}
      <main className="main">{children}</main>
    </div>
  );
}
