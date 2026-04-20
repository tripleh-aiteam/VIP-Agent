"use client";

import { useState, useEffect } from "react";
import { apiPost, API } from "./api";

const AUTH_KEY = "vip-auth";

type AuthView = "login" | "forgot" | "reset" | "change";

interface AuthData {
  token: string;
  user: { id: string; email: string; name: string; role: string };
}

export function getAuth(): AuthData | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = localStorage.getItem(AUTH_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch { return null; }
}

export function logout() {
  localStorage.removeItem(AUTH_KEY);
  window.location.reload();
}

export default function AuthGuard({ children }: { children: React.ReactNode }) {
  const [auth, setAuth] = useState<AuthData | null>(null);
  const [checking, setChecking] = useState(true);
  const [view, setView] = useState<AuthView>("login");

  // Form states
  const [email, setEmail] = useState("admin");
  const [password, setPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [resetToken, setResetToken] = useState("");
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    const saved = getAuth();
    if (saved) setAuth(saved);
    setChecking(false);

    // Check URL for reset token
    const params = new URLSearchParams(window.location.search);
    const token = params.get("token");
    if (token) { setResetToken(token); setView("reset"); }
  }, []);

  const handleLogin = async () => {
    setLoading(true); setError("");
    try {
      const result = await apiPost<any>("/auth/login", { email, password });
      if (result.success) {
        const authData = { token: result.token, user: result.user };
        localStorage.setItem(AUTH_KEY, JSON.stringify(authData));
        setAuth(authData);
        return;
      }
    } catch (e: any) {
      // If backend auth fails with clear message, show it
      const msg = e?.message || "";
      if (msg && msg !== "Failed to fetch" && !msg.includes("NetworkError")) {
        setError(msg);
        setLoading(false);
        return;
      }
      // Backend unreachable — fall back to local password check
    }

    // Fallback: local password check (works when backend is down)
    const localPw = process.env.NEXT_PUBLIC_VIP_PASSWORD || "VipBoss2026!";
    if (password === localPw) {
      const authData = { token: "local", user: { id: "local", email: email || "admin", name: "VIP Admin", role: "admin" } };
      localStorage.setItem(AUTH_KEY, JSON.stringify(authData));
      setAuth(authData);
    } else {
      setError("Incorrect password");
    }
    setLoading(false);
  };

  const handleForgot = async () => {
    setLoading(true); setError(""); setSuccess("");

    // Try email recovery first, then Telegram fallback
    try {
      const res = await fetch(`${API}/auth/forgot-password`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email }),
      });
      if (res.ok) {
        setSuccess("Recovery sent! Check your email and Telegram.");
        setLoading(false);
        return;
      }
    } catch {}

    // Fallback: send temporary password to Telegram directly
    try {
      const res = await fetch(`${API}/auth/forgot-password-telegram`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email }),
      });
      if (res.ok) {
        const data = await res.json();
        setSuccess(data.message || "Temporary password sent to Telegram! Check @vip_agentbot_bot.");
      } else {
        setError("Failed to reset password. Please try again.");
      }
    } catch {
      setError("Cannot reach the server. Please check your connection.");
    }
    setLoading(false);
  };

  const handleReset = async () => {
    if (newPassword !== confirmPassword) { setError("Passwords don't match"); return; }
    setLoading(true); setError(""); setSuccess("");
    try {
      const result = await apiPost<any>("/auth/reset-password", { token: resetToken, new_password: newPassword });
      setSuccess(result.message || "Password reset! You can now sign in.");
      setTimeout(() => { setView("login"); setSuccess(""); }, 2000);
    } catch (e: any) {
      setError(e?.message || "Reset failed. Link may be expired.");
    }
    setLoading(false);
  };

  if (checking) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-50">
        <div className="w-5 h-5 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />
      </div>
    );
  }

  if (auth) return <>{children}</>;

  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-50">
      <div className="w-full max-w-sm mx-4">
        {/* Logo */}
        <div className="text-center mb-8">
          <h1 className="text-[28px] font-bold text-gray-900 tracking-tight">VIP AGENT</h1>
          <p className="text-[13px] text-gray-400 mt-1">Enterprise Agent Platform</p>
        </div>

        {/* Card */}
        <div className="bg-white rounded-2xl shadow-lg border border-gray-100 p-8">

          {/* LOGIN */}
          {view === "login" && (
            <>
              <h2 className="text-[16px] font-semibold text-gray-800 mb-6">Sign in</h2>
              <div className="space-y-4">
                <div>
                  <label className="block text-[12px] font-medium text-gray-500 mb-1.5">Email</label>
                  <input type="text" value={email} onChange={(e) => { setEmail(e.target.value); setError(""); }}
                    placeholder="admin or your email"
                    className="w-full px-4 py-3 rounded-xl border border-gray-200 text-[14px] text-gray-800 focus:outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500 placeholder:text-gray-300" />
                </div>
                <div>
                  <label className="block text-[12px] font-medium text-gray-500 mb-1.5">Password</label>
                  <input type="password" value={password} onChange={(e) => { setPassword(e.target.value); setError(""); }}
                    onKeyDown={(e) => e.key === "Enter" && handleLogin()}
                    placeholder="Enter your password" autoFocus
                    className="w-full px-4 py-3 rounded-xl border border-gray-200 text-[14px] text-gray-800 focus:outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500 placeholder:text-gray-300" />
                </div>
                {error && <p className="text-[12px] text-red-500 font-medium">{error}</p>}
                <button onClick={handleLogin} disabled={!password || loading}
                  className="w-full py-3 rounded-xl bg-gray-900 hover:bg-gray-800 text-white text-[14px] font-semibold disabled:opacity-30 transition-colors">
                  {loading ? "Signing in..." : "Sign in"}
                </button>
                <button onClick={() => { setView("forgot"); setError(""); setSuccess(""); }}
                  className="w-full text-[12px] text-blue-500 hover:text-blue-700 font-medium mt-2">
                  Forgot password?
                </button>
              </div>
            </>
          )}

          {/* FORGOT PASSWORD */}
          {view === "forgot" && (
            <>
              <h2 className="text-[16px] font-semibold text-gray-800 mb-2">Reset password</h2>
              <p className="text-[12px] text-gray-400 mb-6">Enter your email. We'll send a temporary password to your Telegram bot.</p>
              <div className="space-y-4">
                <div>
                  <label className="block text-[12px] font-medium text-gray-500 mb-1.5">Email</label>
                  <input type="email" value={email} onChange={(e) => { setEmail(e.target.value); setError(""); setSuccess(""); }}
                    placeholder="your@email.com" autoFocus
                    className="w-full px-4 py-3 rounded-xl border border-gray-200 text-[14px] text-gray-800 focus:outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500 placeholder:text-gray-300" />
                </div>
                {error && <p className="text-[12px] text-red-500 font-medium">{error}</p>}
                {success && <p className="text-[12px] text-green-600 font-medium">{success}</p>}
                <button onClick={handleForgot} disabled={!email || loading}
                  className="w-full py-3 rounded-xl bg-gray-900 hover:bg-gray-800 text-white text-[14px] font-semibold disabled:opacity-30 transition-colors">
                  {loading ? "Sending..." : "Send recovery link"}
                </button>
                <button onClick={() => { setView("login"); setError(""); setSuccess(""); }}
                  className="w-full text-[12px] text-gray-400 hover:text-gray-600 font-medium mt-2">
                  Back to sign in
                </button>
              </div>
            </>
          )}

          {/* RESET PASSWORD (from email link) */}
          {view === "reset" && (
            <>
              <h2 className="text-[16px] font-semibold text-gray-800 mb-2">Set new password</h2>
              <p className="text-[12px] text-gray-400 mb-6">Choose a new password for your account.</p>
              <div className="space-y-4">
                <div>
                  <label className="block text-[12px] font-medium text-gray-500 mb-1.5">New password</label>
                  <input type="password" value={newPassword} onChange={(e) => { setNewPassword(e.target.value); setError(""); }}
                    placeholder="At least 6 characters" autoFocus
                    className="w-full px-4 py-3 rounded-xl border border-gray-200 text-[14px] text-gray-800 focus:outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500 placeholder:text-gray-300" />
                </div>
                <div>
                  <label className="block text-[12px] font-medium text-gray-500 mb-1.5">Confirm password</label>
                  <input type="password" value={confirmPassword} onChange={(e) => { setConfirmPassword(e.target.value); setError(""); }}
                    onKeyDown={(e) => e.key === "Enter" && handleReset()}
                    placeholder="Type again"
                    className="w-full px-4 py-3 rounded-xl border border-gray-200 text-[14px] text-gray-800 focus:outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500 placeholder:text-gray-300" />
                </div>
                {error && <p className="text-[12px] text-red-500 font-medium">{error}</p>}
                {success && <p className="text-[12px] text-green-600 font-medium">{success}</p>}
                <button onClick={handleReset} disabled={!newPassword || !confirmPassword || loading}
                  className="w-full py-3 rounded-xl bg-gray-900 hover:bg-gray-800 text-white text-[14px] font-semibold disabled:opacity-30 transition-colors">
                  {loading ? "Resetting..." : "Reset password"}
                </button>
                <button onClick={() => { setView("login"); setError(""); setSuccess(""); }}
                  className="w-full text-[12px] text-gray-400 hover:text-gray-600 font-medium mt-2">
                  Back to sign in
                </button>
              </div>
            </>
          )}
        </div>

        <p className="text-center text-[11px] text-gray-300 mt-6">Authorized access only</p>
      </div>
    </div>
  );
}
