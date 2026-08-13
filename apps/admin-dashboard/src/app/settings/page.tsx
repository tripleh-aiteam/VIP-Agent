"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { API, api, apiPost } from "@/components/api";
import { getAuth, logout } from "@/components/AuthGuard";
import { useLanguage } from "@/components/i18n";
import {
  useAppearance,
  THEME_OPTIONS,
  BACKGROUND_OPTIONS,
  ACCENT_OPTIONS,
  SCALE_OPTIONS,
} from "@/components/appearance";

// ---------------------------------------------------------------------------
// Settings, laid out like a phone's Settings app: a grouped list of rows on the
// left, the selected pane on the right. On phones the list IS the page and
// tapping a row drills into the pane (with a back chevron), exactly as iOS /
// Android do it.
// ---------------------------------------------------------------------------

type PanelId = "profile" | "security" | "display" | "language" | "notifications" | "about";

// Multi-path icons separate their subpaths with "|".
const ICON = {
  user: "M16 7a4 4 0 11-8 0 4 4 0 018 0z|M12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z",
  lock: "M12 15v2|M6 21h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2z|M8 11V7a4 4 0 018 0v4",
  display:
    "M12 3v1m0 16v1m9-9h-1M4 12H3m15.364 6.364l-.707-.707M6.343 6.343l-.707-.707m12.728 0l-.707.707M6.343 17.657l-.707.707M16 12a4 4 0 11-8 0 4 4 0 018 0z",
  globe:
    "M21 12a9 9 0 01-9 9m9-9a9 9 0 00-9-9m9 9H3m9 9a9 9 0 01-9-9m9 9c1.657 0 3-4.03 3-9s-1.343-9-3-9m0 18c-1.657 0-3-4.03-3-9s1.343-9 3-9m-9 9a9 9 0 019-9",
  bell: "M15 17h5l-1.405-1.405A2.032 2.032 0 0118 14.158V11a6.002 6.002 0 00-4-5.659V5a2 2 0 10-4 0v.341C7.67 6.165 6 8.388 6 11v3.159c0 .538-.214 1.055-.595 1.436L4 17h5m6 0v1a3 3 0 11-6 0v-1h6z",
  link: "M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101|M10.172 13.828a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1",
  pulse: "M13 10V3L4 14h7v7l9-11h-7z",
  glasses: "M15 12a3 3 0 11-6 0 3 3 0 016 0z|M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z",
  info: "M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z",
};

function Icon({ d, className }: { d: string; className?: string }) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.9}>
      {d.split("|").map((p, i) => (
        <path key={i} strokeLinecap="round" strokeLinejoin="round" d={p} />
      ))}
    </svg>
  );
}

const Chevron = ({ className = "" }: { className?: string }) => (
  <svg className={`w-4 h-4 shrink-0 ${className}`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.2}>
    <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
  </svg>
);

const Check = ({ className = "" }: { className?: string }) => (
  <svg className={`w-4 h-4 shrink-0 ${className}`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.6}>
    <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
  </svg>
);

/** The rounded, tinted icon square that gives phone settings its signature look. */
function IconTile({ d, tint }: { d: string; tint: string }) {
  return (
    <span className="w-7 h-7 shrink-0 rounded-[8px] grid place-items-center text-white" style={{ background: tint }}>
      <Icon d={d} className="w-[15px] h-[15px]" />
    </span>
  );
}

function Switch({ on, onChange, label }: { on: boolean; onChange: (v: boolean) => void; label: string }) {
  return (
    <button
      role="switch"
      aria-checked={on}
      aria-label={label}
      onClick={() => onChange(!on)}
      className={`relative w-[46px] h-[27px] shrink-0 rounded-full transition-colors ${
        on ? "bg-[var(--brand-green)]" : "bg-[var(--border-strong)]"
      }`}
    >
      <span
        className={`absolute top-[3px] left-[3px] w-[21px] h-[21px] rounded-full bg-white transition-transform ${
          on ? "translate-x-[19px]" : "translate-x-0"
        }`}
        style={{ boxShadow: "0 1px 3px rgba(0,0,0,0.25)" }}
      />
    </button>
  );
}

/** Grouped card — section caption above, hairline-divided rows inside. */
function Group({ title, children }: { title?: string; children: React.ReactNode }) {
  return (
    <div className="mb-5">
      {title && (
        <div className="px-3 mb-1.5 text-[11px] font-semibold uppercase tracking-[0.06em] text-[var(--text-muted)]">
          {title}
        </div>
      )}
      <div className="rounded-2xl border border-[var(--border-default)] bg-[var(--bg-card)] overflow-hidden">
        {children}
      </div>
    </div>
  );
}

/**
 * One list row. `divider` draws the hairline indented past the icon (the
 * classic look) rather than edge-to-edge.
 */
function Row({
  icon,
  tint,
  label,
  sub,
  value,
  divider,
  selected,
  trailing,
  onClick,
  href,
  danger,
}: {
  icon?: string;
  tint?: string;
  label: string;
  sub?: string;
  value?: string;
  divider?: boolean;
  selected?: boolean;
  trailing?: React.ReactNode;
  onClick?: () => void;
  href?: string;
  danger?: boolean;
}) {
  const body = (
    <>
      {icon && tint && <IconTile d={icon} tint={tint} />}
      <span
        className={`flex-1 min-w-0 flex items-center gap-3 py-2.5 ${
          divider ? "border-t border-[var(--border-default)]" : ""
        }`}
      >
        <span className="flex-1 min-w-0">
          <span className={`block text-[13.5px] font-medium truncate ${danger ? "text-[var(--error)]" : "text-[var(--text-primary)]"}`}>
            {label}
          </span>
          {sub && <span className="block text-[11.5px] text-[var(--text-muted)] truncate mt-0.5">{sub}</span>}
        </span>
        {value && <span className="text-[12.5px] text-[var(--text-muted)] shrink-0 max-w-[45%] truncate">{value}</span>}
        {trailing}
        {!trailing && (href || onClick) && <Chevron className="text-[var(--text-muted)] opacity-60" />}
      </span>
    </>
  );

  const cls = `w-full flex items-center gap-3 pl-3 pr-3 text-left transition-colors ${
    selected ? "bg-[var(--sidebar-active)]" : "hover:bg-[var(--bg-hover)]"
  }`;

  if (href) {
    return (
      <Link href={href} className={cls}>
        {body}
      </Link>
    );
  }
  if (onClick) {
    return (
      <button onClick={onClick} className={cls}>
        {body}
      </button>
    );
  }
  return <div className={`w-full flex items-center gap-3 pl-3 pr-3`}>{body}</div>;
}

/** Header shown at the top of every detail pane. */
function PaneHeader({ title, sub, onBack }: { title: string; sub?: string; onBack: () => void }) {
  const { t } = useLanguage();
  return (
    <div className="mb-5">
      <button
        onClick={onBack}
        className="md:hidden inline-flex items-center gap-1 -ml-1 mb-2 text-[13px] font-medium text-[var(--brand-blue)]"
      >
        <Chevron className="rotate-180" />
        <span>{t("설정", "Settings")}</span>
      </button>
      <h2 className="text-[19px] font-semibold tracking-tight text-[var(--text-primary)]">{title}</h2>
      {sub && <p className="text-[12.5px] text-[var(--text-muted)] mt-0.5">{sub}</p>}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Display pane pieces
// ---------------------------------------------------------------------------

const MINI = {
  light: { app: "#FFFFFF", panel: "#EEF2F6", line: "#C3CBD6" },
  dark: { app: "#0F172A", panel: "#1E293B", line: "#4B5A72" },
};

function MiniWindow({ c }: { c: { app: string; panel: string; line: string } }) {
  return (
    <div className="h-full w-full flex" style={{ background: c.app }}>
      <div className="w-[28%] h-full p-[5px] flex flex-col gap-[3px]" style={{ background: c.panel }}>
        <span className="block h-[3px] rounded-full" style={{ background: c.line, width: "85%" }} />
        <span className="block h-[3px] rounded-full opacity-60" style={{ background: c.line, width: "65%" }} />
        <span className="block h-[3px] rounded-full opacity-60" style={{ background: c.line, width: "75%" }} />
      </div>
      <div className="flex-1 p-[6px] flex flex-col gap-[4px]">
        <span className="block h-[4px] rounded-full" style={{ background: c.line, width: "50%" }} />
        <span className="block h-[11px] rounded-[3px]" style={{ background: c.panel }} />
        <span className="block h-[11px] rounded-[3px]" style={{ background: c.panel }} />
      </div>
    </div>
  );
}

function ThemeCard({
  mode,
  title,
  hint,
  active,
  onClick,
}: {
  mode: "light" | "dark" | "auto";
  title: string;
  hint: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button onClick={onClick} className="text-left group" aria-pressed={active}>
      <div
        className={`h-[74px] rounded-xl overflow-hidden border-2 transition-colors ${
          active ? "border-[var(--brand-blue)]" : "border-[var(--border-default)] group-hover:border-[var(--border-strong)]"
        }`}
      >
        {mode === "auto" ? (
          <div className="flex h-full w-full">
            <div className="w-1/2 h-full overflow-hidden">
              <div className="w-[200%] h-full">
                <MiniWindow c={MINI.light} />
              </div>
            </div>
            <div className="w-1/2 h-full overflow-hidden">
              <div className="w-[200%] h-full -translate-x-1/2">
                <MiniWindow c={MINI.dark} />
              </div>
            </div>
          </div>
        ) : (
          <MiniWindow c={MINI[mode]} />
        )}
      </div>
      <div className="flex items-center gap-1.5 mt-2">
        <span className={`text-[12.5px] font-semibold ${active ? "text-[var(--brand-blue)]" : "text-[var(--text-primary)]"}`}>
          {title}
        </span>
        {active && <Check className="w-3.5 h-3.5 text-[var(--brand-blue)]" />}
      </div>
      <div className="text-[11px] text-[var(--text-muted)] leading-snug">{hint}</div>
    </button>
  );
}

// ---------------------------------------------------------------------------

export default function SettingsPage() {
  const { t, lang } = useLanguage();
  const appearance = useAppearance();
  const auth = getAuth();

  const [panel, setPanel] = useState<PanelId | null>(null);
  const [query, setQuery] = useState("");

  // Desktop always shows a pane; phones start on the list.
  const openPanel: PanelId = panel ?? "profile";

  type RowDef = {
    key: string;
    panel?: PanelId;
    href?: string;
    icon: string;
    tint: string;
    label: string;
    sub: string;
    value?: string;
  };

  const themeLabel = THEME_OPTIONS.find((o) => o.id === appearance.theme);

  const sections: { title: string; rows: RowDef[] }[] = useMemo(
    () => [
      {
        title: t("계정", "Account"),
        rows: [
          {
            key: "profile",
            panel: "profile",
            icon: ICON.user,
            tint: "#1B96FF",
            label: t("프로필", "Profile"),
            sub: auth?.user?.email || "—",
          },
          {
            key: "security",
            panel: "security",
            icon: ICON.lock,
            tint: "#12B76A",
            label: t("비밀번호 및 보안", "Password & Security"),
            sub: t("비밀번호 변경, 세션", "Change password, session"),
          },
        ],
      },
      {
        title: t("환경설정", "Preferences"),
        rows: [
          {
            key: "display",
            panel: "display",
            icon: ICON.display,
            tint: "#7F56D9",
            label: t("화면 및 테마", "Display & Theme"),
            sub: t("배경, 강조색, 글자 크기", "Background, accent, text size"),
            value: themeLabel ? t(themeLabel.ko, themeLabel.en) : undefined,
          },
          {
            key: "language",
            panel: "language",
            icon: ICON.globe,
            tint: "#F79009",
            label: t("언어", "Language"),
            sub: t("대시보드 표시 언어", "Dashboard display language"),
            value: lang === "ko" ? "한국어" : "English",
          },
          {
            key: "notifications",
            panel: "notifications",
            icon: ICON.bell,
            tint: "#F04438",
            label: t("알림", "Notifications"),
            sub: t("브라우저 알림 권한", "Browser notification permission"),
          },
        ],
      },
      {
        title: t("연동", "Integrations"),
        rows: [
          {
            key: "channels",
            href: "/channels",
            icon: ICON.link,
            tint: "#0B5CAB",
            label: t("채널", "Channels"),
            sub: t("텔레그램, 슬랙, 왓츠앱, 웹, AI 글래스", "Telegram, Slack, WhatsApp, Web, AI Glasses"),
          },
        ],
      },
      {
        title: t("진단", "Diagnostics"),
        rows: [
          {
            key: "chatbot-health",
            href: "/chatbot-health",
            icon: ICON.pulse,
            tint: "#12B76A",
            label: t("챗봇 자가 개선", "Chatbot Self-Improvement"),
            sub: t("자동 개선 주기 및 학습 상태", "Auto-improvement cycle and learning health"),
          },
          {
            key: "ai-glass",
            href: "/ai-glass",
            icon: ICON.glasses,
            tint: "#98A2B3",
            label: t("AI 글래스", "AI Glass"),
            sub: t("공간 캡처 세션 (실험적)", "Spatial capture sessions (experimental)"),
          },
        ],
      },
      {
        title: t("정보", "About"),
        rows: [
          {
            key: "about",
            panel: "about",
            icon: ICON.info,
            tint: "#475467",
            label: t("정보 및 연결 상태", "About & Connection"),
            sub: t("버전, 서버 상태, 설정 초기화", "Version, server status, reset preferences"),
          },
        ],
      },
    ],
    [t, lang, auth?.user?.email, themeLabel],
  );

  const q = query.trim().toLowerCase();
  const matches = useMemo(() => {
    if (!q) return null;
    return sections
      .flatMap((s) => s.rows)
      .filter((r) => `${r.label} ${r.sub} ${r.value || ""}`.toLowerCase().includes(q));
  }, [q, sections]);

  const listCls = panel ? "hidden md:block" : "block";
  const paneCls = panel ? "block" : "hidden md:block";

  return (
    // pt-12 on phones clears the fixed mobile header from Sidebar, which would
    // otherwise sit on top of the title.
    <div className="pt-12 md:pt-0">
      <h1 className="text-[28px] font-semibold tracking-tight mb-1">{t("설정", "Settings")}</h1>
      <p className="text-[14px] text-[var(--text-muted)] mb-6">
        {t("계정, 화면, 알림, 연동", "Account, display, notifications, integrations")}
      </p>

      <div className="grid md:grid-cols-[320px_minmax(0,1fr)] gap-6 items-start">
        {/* ---------- Master list ---------- */}
        <div className={listCls}>
          <div className="relative mb-4">
            <svg
              className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-[var(--text-muted)]"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
              strokeWidth={2}
            >
              <path strokeLinecap="round" strokeLinejoin="round" d="M21 21l-4.35-4.35M17 11a6 6 0 11-12 0 6 6 0 0112 0z" />
            </svg>
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={t("설정 검색", "Search settings")}
              className="w-full pl-9 pr-3 py-2.5 rounded-xl border border-[var(--border-default)] bg-[var(--bg-elevated)] text-[13px] text-[var(--text-primary)] placeholder:text-[var(--text-muted)] focus:outline-none focus:border-[var(--brand-blue)]"
            />
          </div>

          {matches ? (
            <Group title={`${matches.length} ${t("개 결과", "results")}`}>
              {matches.length === 0 && (
                <div className="px-4 py-6 text-center text-[12.5px] text-[var(--text-muted)]">
                  {t("일치하는 설정이 없습니다", "No matching settings")}
                </div>
              )}
              {matches.map((r, i) => (
                <Row
                  key={r.key}
                  icon={r.icon}
                  tint={r.tint}
                  label={r.label}
                  sub={r.sub}
                  divider={i > 0}
                  selected={!!r.panel && panel === r.panel}
                  href={r.href}
                  onClick={r.panel ? () => setPanel(r.panel!) : undefined}
                />
              ))}
            </Group>
          ) : (
            sections.map((s) => (
              <Group key={s.title} title={s.title}>
                {s.rows.map((r, i) => (
                  <Row
                    key={r.key}
                    icon={r.icon}
                    tint={r.tint}
                    label={r.label}
                    sub={r.sub}
                    value={r.value}
                    divider={i > 0}
                    selected={!!r.panel && panel === r.panel}
                    href={r.href}
                    onClick={r.panel ? () => setPanel(r.panel!) : undefined}
                  />
                ))}
              </Group>
            ))
          )}
        </div>

        {/* ---------- Detail pane ---------- */}
        <div className={paneCls}>
          {openPanel === "profile" && <ProfilePane onBack={() => setPanel(null)} />}
          {openPanel === "security" && <SecurityPane onBack={() => setPanel(null)} />}
          {openPanel === "display" && <DisplayPane onBack={() => setPanel(null)} />}
          {openPanel === "language" && <LanguagePane onBack={() => setPanel(null)} />}
          {openPanel === "notifications" && <NotificationsPane onBack={() => setPanel(null)} />}
          {openPanel === "about" && <AboutPane onBack={() => setPanel(null)} />}
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Panes
// ---------------------------------------------------------------------------

function ProfilePane({ onBack }: { onBack: () => void }) {
  const { t } = useLanguage();
  const auth = getAuth();
  const name = auth?.user?.name || "—";
  const email = auth?.user?.email || "—";
  const initial = (name !== "—" ? name : email).trim().charAt(0).toUpperCase() || "?";

  return (
    <div className="max-w-xl">
      <PaneHeader title={t("프로필", "Profile")} sub={t("이 기기에 로그인된 계정", "The account signed in on this device")} onBack={onBack} />

      <div className="mb-5 rounded-2xl border border-[var(--border-default)] bg-[var(--bg-card)] p-5 flex items-center gap-4">
        <div className="w-14 h-14 rounded-full grid place-items-center text-white text-[22px] font-semibold bg-[var(--brand-blue)] shrink-0">
          {initial}
        </div>
        <div className="min-w-0">
          <div className="text-[16px] font-semibold text-[var(--text-primary)] truncate">{name}</div>
          <div className="text-[12.5px] text-[var(--text-muted)] truncate">{email}</div>
          <span className="inline-block mt-1.5 px-2 py-0.5 rounded-md text-[11px] font-semibold capitalize bg-[var(--badge-blue-bg)] text-[var(--badge-blue-text)]">
            {auth?.user?.role || "—"}
          </span>
        </div>
      </div>

      <Group title={t("계정 정보", "Account details")}>
        <Row label={t("이메일", "Email")} value={email} />
        <Row label={t("이름", "Name")} value={name} divider />
        <Row label={t("역할", "Role")} value={auth?.user?.role || "—"} divider />
        <Row label={t("사용자 ID", "User ID")} value={auth?.user?.id || "—"} divider />
      </Group>

      <Group>
        <Row label={t("로그아웃", "Sign out")} danger onClick={() => logout()} trailing={<span />} />
      </Group>

      <p className="px-3 text-[11.5px] text-[var(--text-muted)] leading-relaxed">
        {t(
          "계정 정보는 오케스트레이터가 관리합니다. 이름이나 역할 변경은 관리자에게 요청하세요.",
          "Account details are managed by the orchestrator. Ask an administrator to change your name or role.",
        )}
      </p>
    </div>
  );
}

function SecurityPane({ onBack }: { onBack: () => void }) {
  const { t } = useLanguage();
  const auth = getAuth();
  const [currentPw, setCurrentPw] = useState("");
  const [newPw, setNewPw] = useState("");
  const [confirmPw, setConfirmPw] = useState("");
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [loading, setLoading] = useState(false);

  const handleChange = async () => {
    if (newPw !== confirmPw) {
      setError(t("비밀번호가 일치하지 않습니다", "Passwords don't match"));
      return;
    }
    if (newPw.length < 6) {
      setError(t("비밀번호는 6자 이상이어야 합니다", "Password must be at least 6 characters"));
      return;
    }
    setLoading(true);
    setError("");
    setSuccess("");
    try {
      const result = await apiPost<any>("/auth/change-password", {
        email: auth?.user?.email || "admin",
        current_password: currentPw,
        new_password: newPw,
      });
      if (result.success) {
        setSuccess(t("비밀번호가 변경되었습니다!", "Password changed successfully!"));
        setCurrentPw("");
        setNewPw("");
        setConfirmPw("");
      }
    } catch (e: any) {
      setError(
        e?.message ||
          t("비밀번호 변경에 실패했습니다. 현재 비밀번호를 확인해 주세요.", "Failed to change password. Check your current password."),
      );
    }
    setLoading(false);
  };

  const input =
    "w-full px-3 py-2.5 rounded-lg border border-[var(--border-default)] bg-[var(--bg-elevated)] text-[13px] text-[var(--text-primary)] focus:outline-none focus:border-[var(--brand-blue)] placeholder:text-[var(--text-muted)]";

  return (
    <div className="max-w-xl">
      <PaneHeader title={t("비밀번호 및 보안", "Password & Security")} sub={t("로그인 자격 증명 관리", "Manage your sign-in credentials")} onBack={onBack} />

      <div className="mb-5 p-4 rounded-2xl border border-[var(--border-default)] bg-[var(--bg-card)]">
        <h3 className="text-[13.5px] font-semibold text-[var(--text-primary)] mb-4">{t("비밀번호 변경", "Change Password")}</h3>
        <div className="space-y-3">
          <div>
            <label className="block text-[12px] font-medium text-[var(--text-muted)] mb-1">{t("현재 비밀번호", "Current password")}</label>
            <input
              type="password"
              value={currentPw}
              onChange={(e) => {
                setCurrentPw(e.target.value);
                setError("");
                setSuccess("");
              }}
              className={input}
            />
          </div>
          <div>
            <label className="block text-[12px] font-medium text-[var(--text-muted)] mb-1">{t("새 비밀번호", "New password")}</label>
            <input
              type="password"
              value={newPw}
              onChange={(e) => {
                setNewPw(e.target.value);
                setError("");
                setSuccess("");
              }}
              placeholder={t("6자 이상", "At least 6 characters")}
              className={input}
            />
          </div>
          <div>
            <label className="block text-[12px] font-medium text-[var(--text-muted)] mb-1">{t("새 비밀번호 확인", "Confirm new password")}</label>
            <input
              type="password"
              value={confirmPw}
              onChange={(e) => {
                setConfirmPw(e.target.value);
                setError("");
                setSuccess("");
              }}
              onKeyDown={(e) => e.key === "Enter" && handleChange()}
              className={input}
            />
          </div>

          {error && <p className="text-[12px] text-[var(--error)] font-medium">{error}</p>}
          {success && <p className="text-[12px] text-[var(--brand-green)] font-medium">{success}</p>}

          <button
            onClick={handleChange}
            disabled={!currentPw || !newPw || !confirmPw || loading}
            className="w-full py-2.5 rounded-lg bg-[var(--text-primary)] hover:opacity-80 text-[var(--bg-card)] text-[13px] font-semibold disabled:opacity-30 transition-opacity mt-2"
          >
            {loading ? t("변경 중...", "Changing...") : t("비밀번호 변경", "Change Password")}
          </button>
        </div>
      </div>

      <Group title={t("세션", "Session")}>
        <Row label={t("자동 로그아웃", "Auto sign-out")} value={t("24시간", "24 hours")} />
        <Row label={t("이 기기에서 로그아웃", "Sign out on this device")} danger divider onClick={() => logout()} trailing={<span />} />
      </Group>
    </div>
  );
}

function DisplayPane({ onBack }: { onBack: () => void }) {
  const { t } = useLanguage();
  const { theme, background, accent, scale, motion, resolvedTheme, systemDark, set, reset } = useAppearance();
  const isDark = resolvedTheme === "dark";

  return (
    <div className="max-w-2xl">
      <PaneHeader
        title={t("화면 및 테마", "Display & Theme")}
        sub={t("이 브라우저에만 저장되는 개인 설정입니다", "Personal preferences, saved in this browser only")}
        onBack={onBack}
      />

      {/* Theme */}
      <div className="mb-5">
        <div className="px-3 mb-1.5 text-[11px] font-semibold uppercase tracking-[0.06em] text-[var(--text-muted)]">
          {t("배경 테마", "Background theme")}
        </div>
        <div className="rounded-2xl border border-[var(--border-default)] bg-[var(--bg-card)] p-4">
          <div className="grid grid-cols-3 gap-3">
            {THEME_OPTIONS.map((o) => (
              <ThemeCard
                key={o.id}
                mode={o.id}
                title={t(o.ko, o.en)}
                hint={t(o.hintKo, o.hintEn)}
                active={theme === o.id}
                onClick={() => set("theme", o.id)}
              />
            ))}
          </div>
          {theme === "auto" && (
            <p className="mt-3 text-[11.5px] text-[var(--text-muted)]">
              {t("현재 기기 설정: ", "Your device is currently set to: ")}
              <span className="font-semibold text-[var(--text-primary)]">{systemDark ? t("다크", "Dark") : t("라이트", "Light")}</span>
            </p>
          )}
        </div>
      </div>

      {/* Background tint */}
      <div className="mb-5">
        <div className="px-3 mb-1.5 text-[11px] font-semibold uppercase tracking-[0.06em] text-[var(--text-muted)]">
          {t("배경 색조", "Background tint")}
        </div>
        <div className="rounded-2xl border border-[var(--border-default)] bg-[var(--bg-card)] p-4">
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            {BACKGROUND_OPTIONS.map((o) => {
              const c = isDark ? o.dark : o.light;
              const active = background === o.id;
              return (
                <button key={o.id} onClick={() => set("background", o.id)} aria-pressed={active} className="text-left group">
                  <div
                    className={`h-[52px] rounded-xl border-2 overflow-hidden flex transition-colors ${
                      active ? "border-[var(--brand-blue)]" : "border-[var(--border-default)] group-hover:border-[var(--border-strong)]"
                    }`}
                    style={{ background: c.app }}
                  >
                    <div className="m-auto w-[60%] h-[54%] rounded-md border" style={{ background: c.card, borderColor: "rgba(128,128,128,0.22)" }} />
                  </div>
                  <div className="flex items-center gap-1 mt-1.5">
                    <span className={`text-[11.5px] font-medium truncate ${active ? "text-[var(--brand-blue)]" : "text-[var(--text-secondary)]"}`}>
                      {t(o.ko, o.en)}
                    </span>
                    {active && <Check className="w-3 h-3 text-[var(--brand-blue)]" />}
                  </div>
                </button>
              );
            })}
          </div>
          <p className="mt-3 text-[11.5px] text-[var(--text-muted)]">
            {t(
              "색조는 라이트/다크 각각에 맞춰 적용됩니다 — 다크에서 '순백'은 중성 그레이가 됩니다.",
              "Each tint has its own light and dark rendering — “Paper” becomes neutral graphite in dark mode.",
            )}
          </p>
        </div>
      </div>

      {/* Accent */}
      <div className="mb-5">
        <div className="px-3 mb-1.5 text-[11px] font-semibold uppercase tracking-[0.06em] text-[var(--text-muted)]">
          {t("강조 색상", "Accent colour")}
        </div>
        <div className="rounded-2xl border border-[var(--border-default)] bg-[var(--bg-card)] p-4">
          <div className="flex flex-wrap gap-3">
            {ACCENT_OPTIONS.map((o) => {
              const color = isDark ? o.dark : o.light;
              const active = accent === o.id;
              return (
                <button
                  key={o.id}
                  onClick={() => set("accent", o.id)}
                  aria-pressed={active}
                  title={t(o.ko, o.en)}
                  className="flex flex-col items-center gap-1.5 w-[54px]"
                >
                  <span
                    className={`w-9 h-9 rounded-full grid place-items-center transition-transform ${active ? "scale-105" : ""}`}
                    style={{ background: color, boxShadow: active ? `0 0 0 2px var(--bg-card), 0 0 0 4px ${color}` : "none" }}
                  >
                    {active && <Check className="w-4 h-4 text-white" />}
                  </span>
                  <span className={`text-[10.5px] leading-none ${active ? "text-[var(--text-primary)] font-semibold" : "text-[var(--text-muted)]"}`}>
                    {t(o.ko, o.en)}
                  </span>
                </button>
              );
            })}
          </div>
        </div>
      </div>

      {/* Display size + motion */}
      <div className="mb-5">
        <div className="px-3 mb-1.5 text-[11px] font-semibold uppercase tracking-[0.06em] text-[var(--text-muted)]">
          {t("보기 및 접근성", "View & accessibility")}
        </div>
        <div className="rounded-2xl border border-[var(--border-default)] bg-[var(--bg-card)] overflow-hidden">
          <div className="p-4">
            <div className="text-[13px] font-medium text-[var(--text-primary)] mb-0.5">{t("화면 크기", "Display size")}</div>
            <div className="text-[11.5px] text-[var(--text-muted)] mb-3">
              {t("대시보드 전체를 확대/축소합니다", "Scales the whole dashboard up or down")}
            </div>
            <div className="inline-flex rounded-lg border border-[var(--border-default)] overflow-hidden">
              {SCALE_OPTIONS.map((o, i) => (
                <button
                  key={o.id}
                  onClick={() => set("scale", o.id)}
                  aria-pressed={scale === o.id}
                  className={`px-3 py-2 text-[12px] font-semibold transition-colors ${i > 0 ? "border-l border-[var(--border-default)]" : ""} ${
                    scale === o.id ? "bg-[var(--brand-blue)] text-white" : "text-[var(--text-secondary)] hover:bg-[var(--bg-hover)]"
                  }`}
                >
                  <span className="block">{o.pct}</span>
                  <span className="block text-[10px] font-normal opacity-80">{t(o.ko, o.en)}</span>
                </button>
              ))}
            </div>
          </div>

          <div className="flex items-center gap-3 px-4 py-3 border-t border-[var(--border-default)]">
            <div className="flex-1 min-w-0">
              <div className="text-[13px] font-medium text-[var(--text-primary)]">{t("모션 줄이기", "Reduce motion")}</div>
              <div className="text-[11.5px] text-[var(--text-muted)]">
                {t("애니메이션과 전환 효과를 끕니다", "Turns off animations and transitions")}
              </div>
            </div>
            <Switch
              on={motion === "reduced"}
              onChange={(v) => set("motion", v ? "reduced" : "full")}
              label={t("모션 줄이기", "Reduce motion")}
            />
          </div>
        </div>
      </div>

      <button
        onClick={reset}
        className="text-[12.5px] font-semibold text-[var(--brand-blue)] hover:opacity-70 px-3 transition-opacity"
      >
        {t("화면 설정 기본값으로 되돌리기", "Reset display settings to defaults")}
      </button>
    </div>
  );
}

function LanguagePane({ onBack }: { onBack: () => void }) {
  const { t, lang, setLang } = useLanguage();
  const options: { id: "ko" | "en"; label: string; sub: string }[] = [
    { id: "ko", label: "한국어", sub: t("기본 언어", "Default language") },
    { id: "en", label: "English", sub: t("영문 표시", "English display") },
  ];

  return (
    <div className="max-w-xl">
      <PaneHeader title={t("언어", "Language")} sub={t("대시보드 전체에 즉시 적용됩니다", "Applies across the dashboard immediately")} onBack={onBack} />
      <Group>
        {options.map((o, i) => (
          <Row
            key={o.id}
            label={o.label}
            sub={o.sub}
            divider={i > 0}
            onClick={() => setLang(o.id)}
            trailing={lang === o.id ? <Check className="text-[var(--brand-blue)]" /> : <span className="w-4" />}
          />
        ))}
      </Group>
      <p className="px-3 text-[11.5px] text-[var(--text-muted)] leading-relaxed">
        {t(
          "언어 선택은 이 브라우저에 저장되며, 상단 바의 한국어 / EN 버튼과 같은 설정입니다.",
          "Your choice is stored in this browser — it's the same setting as the 한국어 / EN switch in the top bar.",
        )}
      </p>
    </div>
  );
}

function NotificationsPane({ onBack }: { onBack: () => void }) {
  const { t } = useLanguage();
  const [permission, setPermission] = useState<NotificationPermission | "unsupported">("default");
  const [note, setNote] = useState("");

  useEffect(() => {
    if (typeof window === "undefined") return;
    setPermission("Notification" in window ? Notification.permission : "unsupported");
  }, []);

  const request = async () => {
    if (!("Notification" in window)) return;
    try {
      const result = await Notification.requestPermission();
      setPermission(result);
      if (result === "denied") {
        setNote(
          t(
            "브라우저에서 차단되었습니다. 주소창의 자물쇠 아이콘 → 알림 에서 다시 허용할 수 있습니다.",
            "Blocked by the browser. Re-allow it from the padlock icon in the address bar → Notifications.",
          ),
        );
      } else {
        setNote("");
      }
    } catch {
      setNote(t("권한 요청에 실패했습니다.", "Permission request failed."));
    }
  };

  const test = () => {
    try {
      new Notification("VIP Agent", {
        body: t("알림이 정상적으로 동작합니다.", "Notifications are working."),
      });
      setNote(t("테스트 알림을 보냈습니다.", "Test notification sent."));
    } catch {
      setNote(t("이 브라우저에서 알림을 표시할 수 없습니다.", "This browser could not display the notification."));
    }
  };

  const statusLabel =
    permission === "granted"
      ? t("허용됨", "Allowed")
      : permission === "denied"
      ? t("차단됨", "Blocked")
      : permission === "unsupported"
      ? t("지원 안 함", "Not supported")
      : t("미설정", "Not set");

  return (
    <div className="max-w-xl">
      <PaneHeader
        title={t("알림", "Notifications")}
        sub={t("이 기기의 브라우저 알림 권한", "Browser notification permission on this device")}
        onBack={onBack}
      />

      <Group title={t("브라우저 알림", "Browser notifications")}>
        <Row
          label={t("권한 상태", "Permission")}
          value={statusLabel}
          trailing={
            <span
              className="w-2 h-2 rounded-full shrink-0"
              style={{
                background:
                  permission === "granted"
                    ? "var(--brand-green)"
                    : permission === "denied"
                    ? "var(--error)"
                    : "var(--text-muted)",
              }}
            />
          }
        />
        {permission === "default" && (
          <Row label={t("알림 허용하기", "Allow notifications")} divider onClick={request} trailing={<span />} />
        )}
        {permission === "granted" && (
          <Row label={t("테스트 알림 보내기", "Send a test notification")} divider onClick={test} trailing={<span />} />
        )}
      </Group>

      {note && <p className="px-3 mb-5 text-[11.5px] text-[var(--text-muted)] leading-relaxed">{note}</p>}

      <p className="px-3 text-[11.5px] text-[var(--text-muted)] leading-relaxed">
        {t(
          "브라우저 알림은 이 기기에만 적용됩니다. 트윈의 아침 리포트, 태스크 완료 등 업무 알림은 채널 설정(텔레그램/슬랙 등)에서 관리하세요.",
          "Browser notifications apply to this device only. Work alerts — twin morning reports, task completions — are configured per channel (Telegram / Slack) under Integrations.",
        )}
      </p>
    </div>
  );
}

function AboutPane({ onBack }: { onBack: () => void }) {
  const { t } = useLanguage();
  const { reset, theme, background, accent, scale } = useAppearance();
  const [health, setHealth] = useState<{ status: string; database?: string; version?: string; ms: number } | null>(null);
  const [checking, setChecking] = useState(false);
  const [healthError, setHealthError] = useState("");
  const [resetDone, setResetDone] = useState(false);
  const alive = useRef(true);

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  const check = async () => {
    setChecking(true);
    setHealthError("");
    const started = Date.now();
    try {
      const data = await api<{ status: string; database?: string; version?: string }>("/health");
      if (alive.current) setHealth({ ...data, ms: Date.now() - started });
    } catch (e: any) {
      if (alive.current) {
        setHealth(null);
        setHealthError(e?.message || t("서버에 연결할 수 없습니다", "Cannot reach the server"));
      }
    }
    if (alive.current) setChecking(false);
  };

  useEffect(() => {
    check();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="max-w-xl">
      <PaneHeader title={t("정보 및 연결 상태", "About & Connection")} sub={t("이 대시보드와 백엔드", "This dashboard and its backend")} onBack={onBack} />

      <Group title={t("애플리케이션", "Application")}>
        <Row label={t("제품", "Product")} value="VIP Agent Platform" />
        <Row label={t("대시보드", "Dashboard")} value={process.env.NEXT_PUBLIC_APP_VERSION || "0.1.0"} divider />
        <Row label={t("API 주소", "API endpoint")} value={API} divider />
      </Group>

      <Group title={t("오케스트레이터", "Orchestrator")}>
        <Row
          label={t("상태", "Status")}
          value={
            checking
              ? t("확인 중...", "Checking...")
              : health
              ? `${health.status} · ${health.ms}ms`
              : healthError
              ? t("연결 실패", "Unreachable")
              : "—"
          }
          trailing={
            <span
              className="w-2 h-2 rounded-full shrink-0"
              style={{ background: health?.status === "ok" ? "var(--brand-green)" : healthError ? "var(--error)" : "var(--warning)" }}
            />
          }
        />
        <Row label={t("데이터베이스", "Database")} value={health?.database || "—"} divider />
        <Row label={t("서버 버전", "Server version")} value={health?.version || "—"} divider />
        <Row label={t("다시 확인", "Check again")} divider onClick={check} trailing={<span />} />
      </Group>

      {healthError && <p className="px-3 -mt-2 mb-5 text-[11.5px] text-[var(--error)]">{healthError}</p>}

      <Group title={t("이 브라우저의 설정", "Preferences in this browser")}>
        <Row label={t("테마", "Theme")} value={theme} />
        <Row label={t("배경", "Background")} value={background} divider />
        <Row label={t("강조 색상", "Accent")} value={accent} divider />
        <Row label={t("화면 크기", "Display size")} value={scale} divider />
        <Row
          label={t("화면 설정 초기화", "Reset display settings")}
          sub={t("테마, 배경, 강조 색상, 크기, 모션", "Theme, background, accent, size, motion")}
          divider
          onClick={() => {
            reset();
            setResetDone(true);
          }}
          trailing={<span />}
        />
      </Group>

      {resetDone && (
        <p className="px-3 text-[11.5px] text-[var(--brand-green)] font-medium">
          {t("화면 설정을 기본값으로 되돌렸습니다.", "Display settings restored to defaults.")}
        </p>
      )}
    </div>
  );
}
