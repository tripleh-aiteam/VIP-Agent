"use client";

import { useState } from "react";
import Link from "next/link";
import { apiPost } from "@/components/api";
import { getAuth } from "@/components/AuthGuard";
import { useLanguage } from "@/components/i18n";

export default function SettingsPage() {
  const { t } = useLanguage();
  const auth = getAuth();
  const [currentPw, setCurrentPw] = useState("");
  const [newPw, setNewPw] = useState("");
  const [confirmPw, setConfirmPw] = useState("");
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [loading, setLoading] = useState(false);

  const handleChange = async () => {
    if (newPw !== confirmPw) { setError(t("비밀번호가 일치하지 않습니다", "Passwords don't match")); return; }
    if (newPw.length < 6) { setError(t("비밀번호는 6자 이상이어야 합니다", "Password must be at least 6 characters")); return; }
    setLoading(true); setError(""); setSuccess("");
    try {
      const result = await apiPost<any>("/auth/change-password", {
        email: auth?.user?.email || "admin",
        current_password: currentPw,
        new_password: newPw,
      });
      if (result.success) {
        setSuccess(t("비밀번호가 변경되었습니다!", "Password changed successfully!"));
        setCurrentPw(""); setNewPw(""); setConfirmPw("");
      }
    } catch (e: any) {
      setError(e?.message || t("비밀번호 변경에 실패했습니다. 현재 비밀번호를 확인해 주세요.", "Failed to change password. Check your current password."));
    }
    setLoading(false);
  };

  return (
    <div>
      <h1 className="text-[28px] font-semibold tracking-tight mb-1">{t("설정", "Settings")}</h1>
      <p className="text-[14px] text-[var(--text-muted)] mb-8">{t("계정 및 보안", "Account and security")}</p>

      <div className="max-w-md">
        {/* Account info */}
        <div className="mb-8 p-4 rounded-xl border border-[var(--border-default)] bg-[var(--bg-card)]">
          <h2 className="text-[14px] font-semibold text-[var(--text-primary)] mb-3">{t("계정", "Account")}</h2>
          <div className="space-y-2 text-[13px]">
            <div className="flex justify-between">
              <span className="text-[var(--text-muted)]">{t("이메일", "Email")}</span>
              <span className="text-[var(--text-primary)] font-medium">{auth?.user?.email || "—"}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-[var(--text-muted)]">{t("이름", "Name")}</span>
              <span className="text-[var(--text-primary)] font-medium">{auth?.user?.name || "—"}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-[var(--text-muted)]">{t("역할", "Role")}</span>
              <span className="text-[var(--text-primary)] font-medium capitalize">{auth?.user?.role || "—"}</span>
            </div>
          </div>
        </div>

        {/* Integrations — Channels lives here now */}
        <div className="mb-8 p-4 rounded-xl border border-[var(--border-default)] bg-[var(--bg-card)]">
          <h2 className="text-[14px] font-semibold text-[var(--text-primary)] mb-1">{t("연동", "Integrations")}</h2>
          <p className="text-[12px] text-[var(--text-muted)] mb-3">{t("외부 채널 및 커넥터", "External channels and connectors")}</p>
          <Link href="/channels" className="flex items-center justify-between p-3 -mx-1 rounded-lg hover:bg-[var(--bg-elevated)] transition-colors">
            <div>
              <div className="text-[13px] font-medium text-[var(--text-primary)]">{t("채널", "Channels")}</div>
              <div className="text-[11px] text-[var(--text-muted)]">{t("텔레그램, 슬랙, 왓츠앱, 웹, AI 글래스", "Telegram, Slack, WhatsApp, Web, AI Glasses")}</div>
            </div>
            <svg className="w-4 h-4 text-[var(--text-muted)]" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}><path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" /></svg>
          </Link>
        </div>

        {/* Diagnostics — non-daily monitoring */}
        <div className="mb-8 p-4 rounded-xl border border-[var(--border-default)] bg-[var(--bg-card)]">
          <h2 className="text-[14px] font-semibold text-[var(--text-primary)] mb-1">{t("진단", "Diagnostics")}</h2>
          <p className="text-[12px] text-[var(--text-muted)] mb-3">{t("백그라운드 모니터링", "Behind-the-scenes monitoring")}</p>
          <Link href="/chatbot-health" className="flex items-center justify-between p-3 -mx-1 rounded-lg hover:bg-[var(--bg-elevated)] transition-colors">
            <div>
              <div className="text-[13px] font-medium text-[var(--text-primary)]">{t("챗봇 자가 개선", "Chatbot Self-Improvement")}</div>
              <div className="text-[11px] text-[var(--text-muted)]">{t("자동 개선 주기 및 학습 상태", "Auto-improvement cycle and learning health")}</div>
            </div>
            <svg className="w-4 h-4 text-[var(--text-muted)]" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}><path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" /></svg>
          </Link>
          <Link href="/ai-glass" className="flex items-center justify-between p-3 -mx-1 rounded-lg hover:bg-[var(--bg-elevated)] transition-colors">
            <div>
              <div className="text-[13px] font-medium text-[var(--text-primary)]">{t("AI 글래스", "AI Glass")}</div>
              <div className="text-[11px] text-[var(--text-muted)]">{t("공간 캡처 세션 (실험적)", "Spatial capture sessions (experimental)")}</div>
            </div>
            <svg className="w-4 h-4 text-[var(--text-muted)]" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}><path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" /></svg>
          </Link>
        </div>

        {/* Change password */}
        <div className="p-4 rounded-xl border border-[var(--border-default)] bg-[var(--bg-card)]">
          <h2 className="text-[14px] font-semibold text-[var(--text-primary)] mb-4">{t("비밀번호 변경", "Change Password")}</h2>
          <div className="space-y-3">
            <div>
              <label className="block text-[12px] font-medium text-[var(--text-muted)] mb-1">{t("현재 비밀번호", "Current password")}</label>
              <input type="password" value={currentPw} onChange={(e) => { setCurrentPw(e.target.value); setError(""); setSuccess(""); }}
                className="w-full px-3 py-2.5 rounded-lg border border-[var(--border-default)] bg-[var(--bg-elevated)] text-[13px] text-[var(--text-primary)] focus:outline-none focus:border-[var(--brand-blue)]" />
            </div>
            <div>
              <label className="block text-[12px] font-medium text-[var(--text-muted)] mb-1">{t("새 비밀번호", "New password")}</label>
              <input type="password" value={newPw} onChange={(e) => { setNewPw(e.target.value); setError(""); setSuccess(""); }}
                placeholder={t("6자 이상", "At least 6 characters")}
                className="w-full px-3 py-2.5 rounded-lg border border-[var(--border-default)] bg-[var(--bg-elevated)] text-[13px] text-[var(--text-primary)] focus:outline-none focus:border-[var(--brand-blue)] placeholder:text-[var(--text-muted)]" />
            </div>
            <div>
              <label className="block text-[12px] font-medium text-[var(--text-muted)] mb-1">{t("새 비밀번호 확인", "Confirm new password")}</label>
              <input type="password" value={confirmPw} onChange={(e) => { setConfirmPw(e.target.value); setError(""); setSuccess(""); }}
                onKeyDown={(e) => e.key === "Enter" && handleChange()}
                className="w-full px-3 py-2.5 rounded-lg border border-[var(--border-default)] bg-[var(--bg-elevated)] text-[13px] text-[var(--text-primary)] focus:outline-none focus:border-[var(--brand-blue)]" />
            </div>

            {error && <p className="text-[12px] text-red-500 font-medium">{error}</p>}
            {success && <p className="text-[12px] text-green-600 font-medium">{success}</p>}

            <button onClick={handleChange} disabled={!currentPw || !newPw || !confirmPw || loading}
              className="w-full py-2.5 rounded-lg bg-[var(--text-primary)] hover:opacity-80 text-white text-[13px] font-semibold disabled:opacity-30 transition-colors mt-2">
              {loading ? t("변경 중...", "Changing...") : t("비밀번호 변경", "Change Password")}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
