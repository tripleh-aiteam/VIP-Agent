"use client";

import { useState, useEffect } from "react";

const PASS_KEY = "vip-auth-token";
// Password hash — simple but effective for single user
// Boss sets real password via NEXT_PUBLIC_VIP_PASSWORD env var
const VALID_PASSWORD = process.env.NEXT_PUBLIC_VIP_PASSWORD || "VipBoss2026!";

export default function AuthGuard({ children }: { children: React.ReactNode }) {
  const [authenticated, setAuthenticated] = useState(false);
  const [checking, setChecking] = useState(true);
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    const token = localStorage.getItem(PASS_KEY);
    if (token === btoa(VALID_PASSWORD)) {
      setAuthenticated(true);
    }
    setChecking(false);
  }, []);

  const handleLogin = () => {
    if (password === VALID_PASSWORD) {
      localStorage.setItem(PASS_KEY, btoa(VALID_PASSWORD));
      setAuthenticated(true);
      setError("");
    } else {
      setError("Incorrect password");
      setPassword("");
    }
  };

  if (checking) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-50">
        <div className="w-5 h-5 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />
      </div>
    );
  }

  if (!authenticated) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-50">
        <div className="w-full max-w-sm mx-4">
          {/* Logo */}
          <div className="text-center mb-8">
            <h1 className="text-[28px] font-bold text-gray-900 tracking-tight">VIP AGENT</h1>
            <p className="text-[13px] text-gray-400 mt-1">Enterprise Agent Platform</p>
          </div>

          {/* Login card */}
          <div className="bg-white rounded-2xl shadow-lg border border-gray-100 p-8">
            <h2 className="text-[16px] font-semibold text-gray-800 mb-6">Sign in</h2>

            <div className="space-y-4">
              <div>
                <label className="block text-[12px] font-medium text-gray-500 mb-1.5">Password</label>
                <input
                  type="password"
                  value={password}
                  onChange={(e) => { setPassword(e.target.value); setError(""); }}
                  onKeyDown={(e) => e.key === "Enter" && handleLogin()}
                  placeholder="Enter your password"
                  autoFocus
                  className="w-full px-4 py-3 rounded-xl border border-gray-200 text-[14px] text-gray-800 focus:outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500 placeholder:text-gray-300 transition-colors"
                />
              </div>

              {error && (
                <p className="text-[12px] text-red-500 font-medium">{error}</p>
              )}

              <button
                onClick={handleLogin}
                disabled={!password}
                className="w-full py-3 rounded-xl bg-gray-900 hover:bg-gray-800 text-white text-[14px] font-semibold disabled:opacity-30 transition-colors"
              >
                Sign in
              </button>
            </div>
          </div>

          <p className="text-center text-[11px] text-gray-300 mt-6">Authorized access only</p>
        </div>
      </div>
    );
  }

  return <>{children}</>;
}

export function logout() {
  localStorage.removeItem(PASS_KEY);
  window.location.reload();
}
