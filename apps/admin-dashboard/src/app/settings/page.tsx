"use client";

import { useState } from "react";
import { apiPost } from "@/components/api";
import { getAuth } from "@/components/AuthGuard";

export default function SettingsPage() {
  const auth = getAuth();
  const [currentPw, setCurrentPw] = useState("");
  const [newPw, setNewPw] = useState("");
  const [confirmPw, setConfirmPw] = useState("");
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [loading, setLoading] = useState(false);

  const handleChange = async () => {
    if (newPw !== confirmPw) { setError("Passwords don't match"); return; }
    if (newPw.length < 6) { setError("Password must be at least 6 characters"); return; }
    setLoading(true); setError(""); setSuccess("");
    try {
      const result = await apiPost<any>("/auth/change-password", {
        email: auth?.user?.email || "admin",
        current_password: currentPw,
        new_password: newPw,
      });
      if (result.success) {
        setSuccess("Password changed successfully!");
        setCurrentPw(""); setNewPw(""); setConfirmPw("");
      }
    } catch (e: any) {
      setError(e?.message || "Failed to change password. Check your current password.");
    }
    setLoading(false);
  };

  return (
    <div>
      <h1 className="text-[28px] font-semibold tracking-tight mb-1">Settings</h1>
      <p className="text-[14px] text-[var(--text-muted)] mb-8">Account and security</p>

      <div className="max-w-md">
        {/* Account info */}
        <div className="mb-8 p-4 rounded-xl border border-[var(--border-default)] bg-[var(--bg-card)]">
          <h2 className="text-[14px] font-semibold text-[var(--text-primary)] mb-3">Account</h2>
          <div className="space-y-2 text-[13px]">
            <div className="flex justify-between">
              <span className="text-[var(--text-muted)]">Email</span>
              <span className="text-[var(--text-primary)] font-medium">{auth?.user?.email || "—"}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-[var(--text-muted)]">Name</span>
              <span className="text-[var(--text-primary)] font-medium">{auth?.user?.name || "—"}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-[var(--text-muted)]">Role</span>
              <span className="text-[var(--text-primary)] font-medium capitalize">{auth?.user?.role || "—"}</span>
            </div>
          </div>
        </div>

        {/* Change password */}
        <div className="p-4 rounded-xl border border-[var(--border-default)] bg-[var(--bg-card)]">
          <h2 className="text-[14px] font-semibold text-[var(--text-primary)] mb-4">Change Password</h2>
          <div className="space-y-3">
            <div>
              <label className="block text-[12px] font-medium text-[var(--text-muted)] mb-1">Current password</label>
              <input type="password" value={currentPw} onChange={(e) => { setCurrentPw(e.target.value); setError(""); setSuccess(""); }}
                className="w-full px-3 py-2.5 rounded-lg border border-[var(--border-default)] bg-[var(--bg-elevated)] text-[13px] text-[var(--text-primary)] focus:outline-none focus:border-[var(--brand-blue)]" />
            </div>
            <div>
              <label className="block text-[12px] font-medium text-[var(--text-muted)] mb-1">New password</label>
              <input type="password" value={newPw} onChange={(e) => { setNewPw(e.target.value); setError(""); setSuccess(""); }}
                placeholder="At least 6 characters"
                className="w-full px-3 py-2.5 rounded-lg border border-[var(--border-default)] bg-[var(--bg-elevated)] text-[13px] text-[var(--text-primary)] focus:outline-none focus:border-[var(--brand-blue)] placeholder:text-[var(--text-muted)]" />
            </div>
            <div>
              <label className="block text-[12px] font-medium text-[var(--text-muted)] mb-1">Confirm new password</label>
              <input type="password" value={confirmPw} onChange={(e) => { setConfirmPw(e.target.value); setError(""); setSuccess(""); }}
                onKeyDown={(e) => e.key === "Enter" && handleChange()}
                className="w-full px-3 py-2.5 rounded-lg border border-[var(--border-default)] bg-[var(--bg-elevated)] text-[13px] text-[var(--text-primary)] focus:outline-none focus:border-[var(--brand-blue)]" />
            </div>

            {error && <p className="text-[12px] text-red-500 font-medium">{error}</p>}
            {success && <p className="text-[12px] text-green-600 font-medium">{success}</p>}

            <button onClick={handleChange} disabled={!currentPw || !newPw || !confirmPw || loading}
              className="w-full py-2.5 rounded-lg bg-[var(--text-primary)] hover:opacity-80 text-white text-[13px] font-semibold disabled:opacity-30 transition-colors mt-2">
              {loading ? "Changing..." : "Change Password"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
