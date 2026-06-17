"use client";

import { useState, useEffect } from "react";
import { API } from "@/components/api";
import { DashboardView as Dashboard } from "./dashboard/DashboardView";

type AuthView = "login" | "forgot" | "reset";

export default function TwinPortal() {
  const [isLoggedIn, setIsLoggedIn] = useState(false);
  const [loading, setLoading] = useState(true);
  const [view, setView] = useState<AuthView>("login");

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loggingIn, setLoggingIn] = useState(false);

  // Forgot-password state
  const [forgotEmail, setForgotEmail] = useState("");
  const [forgotSending, setForgotSending] = useState(false);
  const [forgotSent, setForgotSent] = useState(false);

  // Reset-password state
  const [resetToken, setResetToken] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [resetting, setResetting] = useState(false);
  const [resetDone, setResetDone] = useState(false);

  useEffect(() => {
    // Check if already logged in
    const token = localStorage.getItem("twin_token");
    const twinId = localStorage.getItem("twin_id");
    if (token && twinId) {
      setIsLoggedIn(true);
    }

    // Check for a reset token in the URL (link clicked from email)
    if (typeof window !== "undefined") {
      const params = new URLSearchParams(window.location.search);
      const t = params.get("token");
      if (t) {
        setResetToken(t);
        setView("reset");
      }
    }

    setLoading(false);
  }, []);

  function goToLogin() {
    setView("login");
    setError("");
    setForgotSent(false);
    setResetDone(false);
  }

  async function handleLogin() {
    if (!email || !password) return;
    setLoggingIn(true);
    setError("");

    try {
      const res = await fetch(`${API}/auth/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
      });
      const data = await res.json();

      if (!res.ok) {
        setError(data.detail || "Login failed");
        return;
      }

      if (!data.twin_id) {
        setError("No digital twin linked to your account. Contact your admin.");
        return;
      }

      // Save session
      localStorage.setItem("twin_token", data.token || "worker-session");
      localStorage.setItem("twin_id", data.twin_id);
      localStorage.setItem("twin_name", data.twin_name || "My Twin");
      localStorage.setItem("worker_name", data.name || "Worker");
      localStorage.setItem("worker_email", email);

      setIsLoggedIn(true);
    } catch (e) {
      setError("Cannot connect to server. Please try again.");
    } finally {
      setLoggingIn(false);
    }
  }

  async function handleForgot() {
    if (!forgotEmail) return;
    setForgotSending(true);
    setError("");

    try {
      const res = await fetch(`${API}/auth/forgot-password`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: forgotEmail }),
      });
      // Treat any non-network response as success to avoid leaking which
      // emails exist — the message is intentionally generic.
      await res.json().catch(() => ({}));
      setForgotSent(true);
    } catch (e) {
      setError("Cannot connect to server. Please try again.");
    } finally {
      setForgotSending(false);
    }
  }

  async function handleReset() {
    if (!newPassword || !confirmPassword) return;
    if (newPassword !== confirmPassword) {
      setError("Passwords do not match.");
      return;
    }
    setResetting(true);
    setError("");

    try {
      const res = await fetch(`${API}/auth/reset-password`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token: resetToken, new_password: newPassword }),
      });
      const data = await res.json().catch(() => ({}));

      if (!res.ok || data.success === false) {
        setError(data.error || data.detail || "Could not reset password. The link may have expired.");
        return;
      }

      setResetDone(true);
      // Clear the token from the URL so a refresh doesn't reopen the reset view
      if (typeof window !== "undefined" && window.history?.replaceState) {
        window.history.replaceState({}, document.title, window.location.pathname);
      }
    } catch (e) {
      setError("Cannot connect to server. Please try again.");
    } finally {
      setResetting(false);
    }
  }

  if (loading) {
    return <div className="min-h-screen flex items-center justify-center bg-[var(--bg-app)]">
      <div className="text-[var(--text-muted)]">Loading...</div>
    </div>;
  }

  if (isLoggedIn) {
    return <Dashboard onLogout={() => { localStorage.clear(); setIsLoggedIn(false); }} />;
  }

  const inputClass =
    "w-full px-4 py-3 bg-[var(--bg-input)] border border-[var(--card-border)] rounded-xl text-[14px] text-[var(--text-primary)] placeholder:text-[var(--text-muted)] focus:outline-none focus:border-blue-400 transition-colors";
  const primaryBtnClass =
    "w-full py-3 bg-gradient-to-r from-blue-500 to-purple-600 text-white rounded-xl text-[14px] font-semibold hover:opacity-90 transition-opacity disabled:opacity-50";
  const linkBtnClass =
    "text-[12px] text-blue-500 hover:text-blue-600 hover:underline transition-colors";

  const subtitle =
    view === "forgot"
      ? "Reset your password"
      : view === "reset"
        ? "Set a new password"
        : "Sign in to access your digital twin";

  return (
    <div className="min-h-screen flex items-center justify-center bg-[var(--bg-app)] px-4">
      <div className="w-full max-w-sm">
        {/* Logo */}
        <div className="text-center mb-8">
          <div className="w-16 h-16 rounded-2xl bg-gradient-to-br from-blue-500 to-purple-600 flex items-center justify-center mx-auto mb-4">
            <span className="text-white text-[28px]">👤</span>
          </div>
          <h1 className="text-[24px] font-bold text-[var(--text-primary)]">Digital Twin</h1>
          <p className="text-[13px] text-[var(--text-muted)] mt-1">{subtitle}</p>
        </div>

        {/* Card */}
        <div className="bg-[var(--card-bg)] rounded-2xl border border-[var(--card-border)] p-6" style={{ boxShadow: "var(--shadow-md)" }}>
          {error && (
            <div className="mb-4 px-4 py-3 bg-red-50 border border-red-200 rounded-lg text-[12px] text-red-700">
              {error}
            </div>
          )}

          {/* ---- LOGIN VIEW ---- */}
          {view === "login" && (
            <div className="space-y-4">
              <div>
                <label className="block text-[12px] font-medium text-[var(--text-secondary)] mb-1.5">Email</label>
                <input
                  type="email"
                  value={email}
                  onChange={e => setEmail(e.target.value)}
                  onKeyDown={e => e.key === "Enter" && handleLogin()}
                  placeholder="your.email@company.com"
                  className={inputClass}
                />
              </div>
              <div>
                <label className="block text-[12px] font-medium text-[var(--text-secondary)] mb-1.5">Password</label>
                <input
                  type="password"
                  value={password}
                  onChange={e => setPassword(e.target.value)}
                  onKeyDown={e => e.key === "Enter" && handleLogin()}
                  placeholder="Enter your password"
                  className={inputClass}
                />
              </div>
              <button
                onClick={handleLogin}
                disabled={!email || !password || loggingIn}
                className={primaryBtnClass}
              >
                {loggingIn ? "Signing in..." : "Sign In"}
              </button>
              <div className="text-center">
                <button
                  type="button"
                  onClick={() => { setView("forgot"); setError(""); setForgotEmail(email); }}
                  className={linkBtnClass}
                >
                  Forgot password?
                </button>
              </div>
            </div>
          )}

          {/* ---- FORGOT VIEW ---- */}
          {view === "forgot" && (
            <div className="space-y-4">
              {forgotSent ? (
                <>
                  <div className="px-4 py-3 bg-blue-50 border border-blue-200 rounded-lg text-[12px] text-blue-700">
                    If that email exists, a reset link has been sent — check your email.
                  </div>
                  <div className="text-center">
                    <button type="button" onClick={goToLogin} className={linkBtnClass}>
                      Back to sign in
                    </button>
                  </div>
                </>
              ) : (
                <>
                  <div>
                    <label className="block text-[12px] font-medium text-[var(--text-secondary)] mb-1.5">Email</label>
                    <input
                      type="email"
                      value={forgotEmail}
                      onChange={e => setForgotEmail(e.target.value)}
                      onKeyDown={e => e.key === "Enter" && handleForgot()}
                      placeholder="your.email@company.com"
                      className={inputClass}
                    />
                  </div>
                  <button
                    onClick={handleForgot}
                    disabled={!forgotEmail || forgotSending}
                    className={primaryBtnClass}
                  >
                    {forgotSending ? "Sending..." : "Send reset link"}
                  </button>
                  <div className="text-center">
                    <button type="button" onClick={goToLogin} className={linkBtnClass}>
                      Back to sign in
                    </button>
                  </div>
                </>
              )}
            </div>
          )}

          {/* ---- RESET VIEW ---- */}
          {view === "reset" && (
            <div className="space-y-4">
              {resetDone ? (
                <>
                  <div className="px-4 py-3 bg-green-50 border border-green-200 rounded-lg text-[12px] text-green-700">
                    Password updated — sign in.
                  </div>
                  <button
                    type="button"
                    onClick={goToLogin}
                    className={primaryBtnClass}
                  >
                    Sign in
                  </button>
                </>
              ) : (
                <>
                  <div>
                    <label className="block text-[12px] font-medium text-[var(--text-secondary)] mb-1.5">New password</label>
                    <input
                      type="password"
                      value={newPassword}
                      onChange={e => setNewPassword(e.target.value)}
                      onKeyDown={e => e.key === "Enter" && handleReset()}
                      placeholder="Enter a new password"
                      className={inputClass}
                    />
                  </div>
                  <div>
                    <label className="block text-[12px] font-medium text-[var(--text-secondary)] mb-1.5">Confirm password</label>
                    <input
                      type="password"
                      value={confirmPassword}
                      onChange={e => setConfirmPassword(e.target.value)}
                      onKeyDown={e => e.key === "Enter" && handleReset()}
                      placeholder="Re-enter the new password"
                      className={inputClass}
                    />
                  </div>
                  <button
                    onClick={handleReset}
                    disabled={!newPassword || !confirmPassword || resetting}
                    className={primaryBtnClass}
                  >
                    {resetting ? "Updating..." : "Set new password"}
                  </button>
                  <div className="text-center">
                    <button type="button" onClick={goToLogin} className={linkBtnClass}>
                      Back to sign in
                    </button>
                  </div>
                </>
              )}
            </div>
          )}
        </div>

        <p className="text-center text-[11px] text-[var(--text-muted)] mt-6">
          Digital Twin Portal
        </p>
      </div>
    </div>
  );
}
