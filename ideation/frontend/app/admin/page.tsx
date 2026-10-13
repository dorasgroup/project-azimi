"use client";

import { FormEvent, useEffect, useState } from "react";

import { AppShell } from "@/components/layout/app-shell";
import { apiRequest } from "@/lib/api/client";
import { getAccessToken, getStoredUser, updateStoredUser } from "@/lib/auth/session";

type UserRole = "user" | "super_admin";

type UserItem = {
  id: string;
  email: string;
  full_name: string;
  role: UserRole;
  is_active: boolean;
  created_at: string | null;
};

type UsersResponse = {
  items: UserItem[];
  total: number;
};

type EditForm = {
  id: string;
  email: string;
  full_name: string;
  password: string;
  role: UserRole;
  is_active: boolean;
};

const roleLabels: Record<UserRole, string> = {
  user: "کاربر",
  super_admin: "مدیر کل",
};

function getFriendlyUserError(message: string) {
  return message === "Email already exists" ? "این ایمیل قبلا ثبت شده است." : message;
}

export default function AdminPage() {
  const [users, setUsers] = useState<UserItem[]>([]);
  const [email, setEmail] = useState("");
  const [fullName, setFullName] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState<UserRole>("user");
  const [editForm, setEditForm] = useState<EditForm | null>(null);
  const [isCreateModalOpen, setIsCreateModalOpen] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [savingUserId, setSavingUserId] = useState<string | null>(null);
  const [deletingUserId, setDeletingUserId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  async function loadUsers() {
    const token = getAccessToken();
    if (!token) {
      setError("برای مدیریت کاربران ابتدا وارد پنل شوید.");
      setIsLoading(false);
      return;
    }

    const response = await apiRequest<UsersResponse>("/admin/users", { token });
    setUsers(response.items);
    setIsLoading(false);
  }

  useEffect(() => {
    loadUsers().catch((caughtError) => {
      setError(caughtError instanceof Error ? caughtError.message : "دریافت کاربران انجام نشد.");
      setIsLoading(false);
    });
  }, []);

  async function handleCreateUser(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const token = getAccessToken();
    if (!token) {
      setError("برای ساخت کاربر ابتدا وارد پنل شوید.");
      return;
    }

    setIsSubmitting(true);
    setError(null);
    setSuccess(null);

    try {
      const createdUser = await apiRequest<UserItem>("/admin/users", {
        method: "POST",
        token,
        body: JSON.stringify({
          email,
          full_name: fullName,
          password,
          role,
        }),
      });
      setSuccess(`کاربر ${createdUser.email} با موفقیت ساخته شد.`);
      setEmail("");
      setFullName("");
      setPassword("");
      setRole("user");
      setIsCreateModalOpen(false);
      await loadUsers();
    } catch (caughtError) {
      const message = caughtError instanceof Error ? caughtError.message : "ساخت کاربر انجام نشد.";
      setError(getFriendlyUserError(message));
    } finally {
      setIsSubmitting(false);
    }
  }


  async function handleUpdateUser(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const token = getAccessToken();
    const currentUser = getStoredUser();
    if (!token || !editForm) {
      return;
    }

    setSavingUserId(editForm.id);
    setError(null);
    setSuccess(null);

    try {
      const body: Record<string, unknown> = {
        email: editForm.email,
        full_name: editForm.full_name,
        role: editForm.role,
        is_active: editForm.is_active,
      };
      if (editForm.password.trim()) {
        body.password = editForm.password;
      }

      const updatedUser = await apiRequest<UserItem>(`/admin/users/${editForm.id}`, {
        method: "PATCH",
        token,
        body: JSON.stringify(body),
      });

      if (currentUser?.id === updatedUser.id) {
        updateStoredUser({
          ...currentUser,
          email: updatedUser.email,
          full_name: updatedUser.full_name,
          role: updatedUser.role,
          is_active: updatedUser.is_active,
        });
      }

      setSuccess(`کاربر ${updatedUser.email} به‌روزرسانی شد.`);
      setEditForm(null);
      await loadUsers();
    } catch (caughtError) {
      const message = caughtError instanceof Error ? caughtError.message : "ویرایش کاربر انجام نشد.";
      setError(getFriendlyUserError(message));
    } finally {
      setSavingUserId(null);
    }
  }

  async function handleDeleteUser(user: UserItem) {
    const token = getAccessToken();
    const currentUser = getStoredUser();
    if (!token) {
      setError("برای حذف کاربر ابتدا وارد پنل شوید.");
      return;
    }
    if (currentUser?.id === user.id) {
      setError("نمی‌توانید حساب کاربری خودتان را حذف کنید.");
      return;
    }

    const confirmed = window.confirm(`کاربر ${user.email} حذف شود؟`);
    if (!confirmed) {
      return;
    }

    setDeletingUserId(user.id);
    setError(null);
    setSuccess(null);

    try {
      await apiRequest<void>(`/admin/users/${user.id}`, {
        method: "DELETE",
        token,
      });
      setSuccess(`کاربر ${user.email} حذف شد.`);
      await loadUsers();
    } catch (caughtError) {
      const message = caughtError instanceof Error ? caughtError.message : "حذف کاربر انجام نشد.";
      setError(message === "You cannot delete your own account" ? "نمی‌توانید حساب کاربری خودتان را حذف کنید." : message);
    } finally {
      setDeletingUserId(null);
    }
  }

  return (
    <AppShell>
      <div className="section-header" style={{ marginBottom: 20 }}>
        <h1 className="page-title" style={{ margin: 0 }}>مدیریت کاربران</h1>
        <button
          className="button"
          onClick={() => {
            setError(null);
            setSuccess(null);
            setIsCreateModalOpen(true);
          }}
          type="button"
          style={{ width: "auto", minHeight: "auto", height: "42px", padding: "0 18px", fontSize: "14px" }}
        >
          افزودن کاربر جدید
        </button>
      </div>

      <section className="card stack">
        <h2>کاربران سیستم</h2>
        {isLoading ? <p className="muted">در حال دریافت کاربران...</p> : null}
        {!isLoading && users.length === 0 ? <p className="muted">هنوز کاربری ثبت نشده است.</p> : null}
        {users.length > 0 ? (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>نام</th>
                  <th>ایمیل</th>
                  <th>نقش</th>
                  <th>وضعیت</th>
                  <th>عملیات</th>
                </tr>
              </thead>
              <tbody>
                {users.map((user) => (
                  <tr key={user.id}>
                    <td>{user.full_name}</td>
                    <td>{user.email}</td>
                    <td>
                      <span className="badge">{roleLabels[user.role]}</span>
                    </td>
                    <td>{user.is_active ? "فعال" : "غیرفعال"}</td>
                    <td className="table-actions">
                      <button
                        className="button secondary"
                        onClick={() =>
                          setEditForm({
                            id: user.id,
                            email: user.email,
                            full_name: user.full_name,
                            password: "",
                            role: user.role,
                            is_active: user.is_active,
                          })
                        }
                        type="button"
                        style={{ padding: "4px 8px", fontSize: "12px", minHeight: "auto", height: "30px" }}
                      >
                        ویرایش
                      </button>
                      <button
                        className="button danger"
                        disabled={deletingUserId === user.id}
                        onClick={() => handleDeleteUser(user)}
                        type="button"
                        style={{ padding: "4px 8px", fontSize: "12px", minHeight: "auto", height: "30px" }}
                      >
                        {deletingUserId === user.id ? "در حال حذف..." : "حذف"}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </section>

      {/* Create User Popup Modal */}
      {isCreateModalOpen ? (
        <div className="chat-confirm-backdrop" role="presentation" onClick={() => setIsCreateModalOpen(false)}>
          <div
            className="chat-confirm select-repo-modal"
            role="dialog"
            aria-modal="true"
            aria-label="افزودن کاربر جدید"
            onClick={(event) => event.stopPropagation()}
            style={{ minWidth: "420px" }}
          >
            <p style={{ fontWeight: "bold", marginBottom: "16px", color: "var(--primary)", fontSize: "16px", textAlign: "right" }}>
              افزودن کاربر جدید
            </p>
            <form className="form" onSubmit={handleCreateUser} style={{ maxWidth: "none" }}>
              <div className="form-grid" style={{ gridTemplateColumns: "1fr", gap: "12px" }}>
                <label>
                  نام کامل
                  <input className="input" onChange={(event) => setFullName(event.target.value)} required value={fullName} />
                </label>
                <label>
                  ایمیل
                  <input className="input" onChange={(event) => setEmail(event.target.value)} required type="email" value={email} />
                </label>
                <label>
                  رمز عبور
                  <input
                    autoComplete="new-password"
                    className="input"
                    minLength={8}
                    onChange={(event) => setPassword(event.target.value)}
                    required
                    type="password"
                    value={password}
                  />
                </label>
                <label>
                  نقش
                  <select className="select" onChange={(event) => setRole(event.target.value as UserRole)} value={role}>
                    <option value="user">کاربر</option>
                    <option value="super_admin">مدیر کل</option>
                  </select>
                </label>
              </div>
              {error ? <p className="form-error">{error}</p> : null}
              {success ? <p className="form-success">{success}</p> : null}
              <div className="chat-confirm-actions" style={{ marginTop: "20px", display: "flex", gap: "10px" }}>
                <button className="button" disabled={isSubmitting} type="submit" style={{ flex: 1 }}>
                  {isSubmitting ? "در حال ساخت..." : "ساخت کاربر"}
                </button>
                <button className="button secondary" onClick={() => setIsCreateModalOpen(false)} type="button" style={{ flex: 1 }}>
                  انصراف
                </button>
              </div>
            </form>
          </div>
        </div>
      ) : null}

      {/* Edit User Popup Modal */}
      {editForm ? (
        <div className="chat-confirm-backdrop" role="presentation" onClick={() => setEditForm(null)}>
          <div
            className="chat-confirm select-repo-modal"
            role="dialog"
            aria-modal="true"
            aria-label="ویرایش کاربر"
            onClick={(event) => event.stopPropagation()}
            style={{ minWidth: "420px" }}
          >
            <p style={{ fontWeight: "bold", marginBottom: "16px", color: "var(--primary)", fontSize: "16px", textAlign: "right" }}>
              ویرایش اطلاعات حساب
            </p>
            <form className="form" onSubmit={handleUpdateUser} style={{ maxWidth: "none" }}>
              <div className="form-grid" style={{ gridTemplateColumns: "1fr", gap: "12px" }}>
                <label>
                  نام کامل
                  <input
                    className="input"
                    onChange={(event) => setEditForm({ ...editForm, full_name: event.target.value })}
                    required
                    value={editForm.full_name}
                  />
                </label>
                <label>
                  ایمیل
                  <input
                    className="input"
                    onChange={(event) => setEditForm({ ...editForm, email: event.target.value })}
                    required
                    type="email"
                    value={editForm.email}
                  />
                </label>
                <label>
                  رمز جدید
                  <input
                    autoComplete="new-password"
                    className="input"
                    minLength={8}
                    onChange={(event) => setEditForm({ ...editForm, password: event.target.value })}
                    placeholder="برای تغییر ندادن خالی بگذارید"
                    type="password"
                    value={editForm.password}
                  />
                </label>
                <label>
                  نقش
                  <select
                    className="select"
                    onChange={(event) => setEditForm({ ...editForm, role: event.target.value as UserRole })}
                    value={editForm.role}
                  >
                    <option value="user">کاربر</option>
                    <option value="super_admin">مدیر کل</option>
                  </select>
                </label>
                <label className="checkbox-row" style={{ marginTop: "4px" }}>
                  <input
                    checked={editForm.is_active}
                    onChange={(event) => setEditForm({ ...editForm, is_active: event.target.checked })}
                    type="checkbox"
                  />
                  حساب فعال باشد
                </label>
              </div>
              <div className="chat-confirm-actions" style={{ marginTop: "20px", display: "flex", gap: "10px" }}>
                <button className="button" disabled={savingUserId === editForm.id} type="submit" style={{ flex: 1 }}>
                  {savingUserId === editForm.id ? "در حال ذخیره..." : "ذخیره تغییرات"}
                </button>
                <button className="button secondary" onClick={() => setEditForm(null)} type="button" style={{ flex: 1 }}>
                  انصراف
                </button>
              </div>
            </form>
          </div>
        </div>
      ) : null}
    </AppShell>
  );
}

