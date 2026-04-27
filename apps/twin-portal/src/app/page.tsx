"use client";

import { useState, useEffect } from "react";
import { API } from "@/components/api";
import Dashboard from "./dashboard/page";

export default function TwinPortal() {
  const [isLoggedIn, setIsLoggedIn] = useState(false);
  const [loading, setLoading] = useState(true);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loggingIn, setLoggingIn] = useState(false);

  useEffect(() => {
    // Check if already logged in
    const token = localStorage.getItem("twin_token");
    const twinId = localStorage.getItem("twin_id");
    if (token && twinId) {
      setIsLoggedIn(true);
    }
    setLoading(false);
  }, []);

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

  if (loading) {
    return <div className="min-h-screen flex items-center justify-center bg-[var(--bg-app)]">
      <div className="text-[var(--text-muted)]">Loading...</div>
    </div>;
  }

  if (isLoggedIn) {
    return <Dashboard onLogout={() => { localStorage.clear(); setIsLoggedIn(false); }} />;
  }

  // Login Page
  return (
    <div className="min-h-screen flex items-center justify-center bg-[var(--bg-app)] px-4">
      <div className="w-full max-w-sm">
        {/* Logo */}
        <div className="text-center mb-8">
          <div className="w-16 h-16 rounded-2xl bg-gradient-to-br from-blue-500 to-purple-600 flex items-center justify-center mx-auto mb-4">
            <span className="text-white text-[28px]">👤</span>
          </div>
          <h1 className="text-[24px] font-bold text-[var(--text-primary)]">Digital Twin</h1>
          <p className="text-[13px] text-[var(--text-muted)] mt-1">Sign in to access your digital twin</p>
        </div>

        {/* Login Form */}
        <div className="bg-[var(--card-bg)] rounded-2xl border border-[var(--card-border)] p-6" style={{ boxShadow: "var(--shadow-md)" }}>
          {error && (
            <div className="mb-4 px-4 py-3 bg-red-50 border border-red-200 rounded-lg text-[12px] text-red-700">
              {error}
            </div>
          )}

          <div className="space-y-4">
            <div>
              <label className="block text-[12px] font-medium text-[var(--text-secondary)] mb-1.5">Email</label>
              <input
                type="email"
                value={email}
                onChange={e => setEmail(e.target.value)}
                onKeyDown={e => e.key === "Enter" && handleLogin()}
                placeholder="your.email@company.com"
                className="w-full px-4 py-3 bg-[var(--bg-input)] border border-[var(--card-border)] rounded-xl text-[14px] text-[var(--text-primary)] placeholder:text-[var(--text-muted)] focus:outline-none focus:border-blue-400 transition-colors"
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
                className="w-full px-4 py-3 bg-[var(--bg-input)] border border-[var(--card-border)] rounded-xl text-[14px] text-[var(--text-primary)] placeholder:text-[var(--text-muted)] focus:outline-none focus:border-blue-400 transition-colors"
              />
            </div>
            <button
              onClick={handleLogin}
              disabled={!email || !password || loggingIn}
              className="w-full py-3 bg-gradient-to-r from-blue-500 to-purple-600 text-white rounded-xl text-[14px] font-semibold hover:opacity-90 transition-opacity disabled:opacity-50"
            >
              {loggingIn ? "Signing in..." : "Sign In"}
            </button>
          </div>
        </div>

        <p className="text-center text-[11px] text-[var(--text-muted)] mt-6">
          Digital Twin Portal
        </p>
      </div>
    </div>
  );
}
