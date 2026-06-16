"use client";

import { useEffect, useState } from "react";
import { api, apiPost } from "@/components/api";
import Badge from "@/components/Badge";
import { useLanguage } from "../../components/i18n";

export default function ChannelsPage() {
  const { t } = useLanguage();
  const [channels, setChannels] = useState<any[]>([]);
  const [activeChannel, setActiveChannel] = useState("telegram");
  const [botStatus, setBotStatus] = useState<any>(null);
  const [tgUsers, setTgUsers] = useState<any[]>([]);
  const [tgCommand, setTgCommand] = useState("/status");
  const [tgResult, setTgResult] = useState<string | null>(null);
  const [tgRunning, setTgRunning] = useState(false);

  const load = () => {
    api<any[]>("/channels").then(setChannels).catch(() => {});
    api<any>("/telegram/status").then(setBotStatus).catch(() => {});
    api<any[]>("/telegram/users").then(setTgUsers).catch(() => {});
  };

  useEffect(() => { load(); const i = setInterval(load, 10000); return () => clearInterval(i); }, []);

  const runTgCommand = async () => {
    setTgRunning(true);
    setTgResult(null);
    try {
      const data = await apiPost<any>(`/telegram/simulate?telegram_user_id=admin_000&command=${encodeURIComponent(tgCommand)}`);
      setTgResult(data.response);
    } catch (e) {
      setTgResult(`${t("오류", "Error")}: ${e}`);
    }
    setTgRunning(false);
  };

  const channelConfig: Record<string, { label: string; icon: string; color: string; description: string }> = {
    telegram: { label: "Telegram", icon: "M12 19l9 2-9-18-9 18 9-2zm0 0v-8", color: "blue", description: t("봇 명령, 알림, 승인", "Bot commands, alerts, approvals") },
    slack: { label: "Slack", icon: "M14.5 10c-.83 0-1.5-.67-1.5-1.5v-5c0-.83.67-1.5 1.5-1.5s1.5.67 1.5 1.5v5c0 .83-.67 1.5-1.5 1.5zm0 0H9m5.5 4c.83 0 1.5.67 1.5 1.5v5c0 .83-.67 1.5-1.5 1.5s-1.5-.67-1.5-1.5v-5c0-.83.67-1.5 1.5-1.5z", color: "purple", description: t("출시 예정", "Coming soon") },
    whatsapp: { label: "WhatsApp", icon: "M17.472 14.382c-.297-.149-1.758-.867-2.03-.967-.273-.099-.471-.148-.67.15-.197.297-.767.966-.94 1.164-.173.199-.347.223-.644.075-.297-.15-1.255-.463-2.39-1.475-.883-.788-1.48-1.761-1.653-2.059-.173-.297-.018-.458.13-.606.134-.133.298-.347.446-.52.149-.174.198-.298.298-.497.099-.198.05-.371-.025-.52-.075-.149-.669-1.612-.916-2.207-.242-.579-.487-.5-.669-.51-.173-.008-.371-.01-.57-.01-.198 0-.52.074-.792.372-.272.297-1.04 1.016-1.04 2.479 0 1.462 1.065 2.875 1.213 3.074.149.198 2.096 3.2 5.077 4.487.709.306 1.262.489 1.694.625.712.227 1.36.195 1.871.118.571-.085 1.758-.719 2.006-1.413.248-.694.248-1.289.173-1.413-.074-.124-.272-.198-.57-.347z", color: "green", description: t("출시 예정", "Coming soon") },
    web: { label: t("웹 대시보드", "Web Dashboard"), icon: "M21 12a9 9 0 01-9 9m9-9a9 9 0 00-9-9m9 9H3m9 9a9 9 0 01-9-9m9 9c1.657 0 3-4.03 3-9s-1.343-9-3-9m0 18c-1.657 0-3-4.03-3-9s1.343-9 3-9m-9 9a9 9 0 019-9", color: "green", description: t("관리자 대시보드 (현재 화면)", "Admin dashboard (this)") },
    ai_glass: { label: t("AI 글래스", "AI Glasses"), icon: "M15 12a3 3 0 11-6 0 3 3 0 016 0z M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z", color: "yellow", description: t("공간 캡처 (예정)", "Spatial capture (planned)") },
  };

  const channelOrder = ["telegram", "slack", "whatsapp", "web", "ai_glass"];

  return (
    <div>
      <h1 className="text-[28px] font-semibold tracking-tight mb-1">{t("채널", "Channels")}</h1>
      <p className="text-[14px] text-[var(--text-muted)] mb-6">{t("커뮤니케이션 채널 및 연동", "Communication channels and integrations")}</p>

      {/* Channel Tabs */}
      <div className="flex gap-2 mb-6">
        {channelOrder.map((key) => {
          const cfg = channelConfig[key];
          const ch = channels.find((c) => c.type === key);
          const isActive = activeChannel === key;
          return (
            <button
              key={key}
              onClick={() => setActiveChannel(key)}
              className={`flex items-center gap-2 px-4 py-2.5 rounded-lg border text-sm transition-colors ${
                isActive
                  ? "border-[var(--border-active)] bg-[var(--bg-hover)] text-[var(--text-primary)]"
                  : "border-[var(--border-default)] bg-[var(--bg-card)] text-[var(--text-secondary)] hover:border-[var(--border-default)]"
              }`}
            >
              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                <path strokeLinecap="round" strokeLinejoin="round" d={cfg.icon} />
              </svg>
              {cfg.label}
              {ch && (
                <span className={`w-1.5 h-1.5 rounded-full ${ch.status === "active" ? "bg-green-400" : ch.status === "planned" ? "bg-[var(--brand-blue)]" : "bg-gray-500"}`} />
              )}
            </button>
          );
        })}
      </div>

      {/* Telegram Channel Content */}
      {activeChannel === "telegram" && (
        <div className="space-y-4">
          {/* Bot Status + Users Row */}
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
            {/* Bot Status */}
            <div className="border border-[var(--border-default)] rounded-lg bg-[var(--bg-card)] p-4">
              <h3 className="text-sm font-semibold mb-3">{t("봇 상태", "Bot Status")}</h3>
              <div className="space-y-2 text-xs">
                <div className="flex justify-between text-[var(--text-secondary)]">
                  <span>{t("설정됨", "Configured")}</span>
                  <Badge text={botStatus?.configured ? "active" : "inactive"} />
                </div>
                {botStatus?.username && (
                  <div className="flex justify-between text-[var(--text-secondary)]">
                    <span>{t("사용자명", "Username")}</span>
                    <span className="text-[var(--text-primary)]">@{botStatus.username}</span>
                  </div>
                )}
                {botStatus?.bot_name && (
                  <div className="flex justify-between text-[var(--text-secondary)]">
                    <span>{t("이름", "Name")}</span>
                    <span className="text-[var(--text-primary)]">{botStatus.bot_name}</span>
                  </div>
                )}
                {botStatus?.error && (
                  <div className="text-[10px] text-[var(--brand-blue)] bg-[var(--bg-hover)] rounded p-2 mt-2">
                    {botStatus.error}
                  </div>
                )}
              </div>
            </div>

            {/* Linked Users */}
            <div className="border border-[var(--border-default)] rounded-lg bg-[var(--bg-card)] p-4 lg:col-span-2">
              <h3 className="text-sm font-semibold mb-3">{t("연결된 사용자", "Linked Users")} ({tgUsers.length})</h3>
              <div className="space-y-1.5">
                {tgUsers.map((u: any) => (
                  <div key={u.id} className="flex items-center justify-between text-xs bg-[var(--bg-elevated)] rounded px-3 py-2">
                    <div className="flex items-center gap-3">
                      <span className="font-mono text-[var(--text-secondary)]">{u.telegram_user_id}</span>
                      <Badge text={u.role} />
                    </div>
                    <Badge text={u.status} />
                  </div>
                ))}
                {tgUsers.length === 0 && <p className="text-xs text-[var(--text-muted)]">{t("연결된 사용자 없음", "No linked users")}</p>}
              </div>
            </div>
          </div>

          {/* Command Simulator */}
          <div className="border border-[var(--border-default)] rounded-lg bg-[var(--bg-card)] p-4">
            <h3 className="text-sm font-semibold mb-3">{t("명령 시뮬레이터", "Command Simulator")}</h3>
            <div className="flex gap-2 mb-3">
              <select
                value={tgCommand}
                onChange={(e) => setTgCommand(e.target.value)}
                className="flex-1 bg-[var(--bg-elevated)] border border-[var(--border-default)] rounded px-3 py-2 text-sm"
              >
                <option value="/status">/status — {t("시스템 상태", "System health")}</option>
                <option value="/agents">/agents — {t("에이전트 목록", "List agents")}</option>
                <option value="/report">/report — {t("최신 일일 보고서", "Latest daily report")}</option>
                <option value="/run_daily">/run_daily — {t("일일 보고서 실행", "Trigger daily report")}</option>
                <option value="/run_weekly">/run_weekly — {t("주간 보고서 실행", "Trigger weekly report")}</option>
                <option value="/approvals">/approvals — {t("대기 중인 건", "Pending cases")}</option>
                <option value="/help">/help — {t("명령 보기", "Show commands")}</option>
              </select>
              <button
                onClick={runTgCommand}
                disabled={tgRunning}
                className="px-4 py-2 rounded bg-blue-600 hover:bg-blue-500 text-[var(--text-primary)] text-sm font-semibold disabled:opacity-50"
              >
                {tgRunning ? "..." : t("전송", "Send")}
              </button>
            </div>

            {/* Response */}
            {tgResult && (
              <div className="bg-[var(--bg-elevated)] border border-[var(--border-default)] rounded p-4 text-xs text-[var(--text-primary)] leading-relaxed whitespace-pre-wrap"
                dangerouslySetInnerHTML={{
                  __html: tgResult
                    .replace(/</g, "&lt;")
                    .replace(/&lt;b&gt;/g, "<b>").replace(/&lt;\/b&gt;/g, "</b>")
                    .replace(/&lt;i&gt;/g, "<i>").replace(/&lt;\/i&gt;/g, "</i>")
                    .replace(/&lt;code&gt;/g, "<code class='bg-gray-700 px-1 rounded'>").replace(/&lt;\/code&gt;/g, "</code>")
                }}
              />
            )}
          </div>

          {/* Setup Instructions */}
          <div className="border border-[var(--border-default)] rounded-lg bg-[var(--bg-card)] p-4">
            <h3 className="text-sm font-semibold mb-2">{t("설정 안내", "Setup Instructions")}</h3>
            <ol className="text-xs text-[var(--text-secondary)] space-y-1.5 list-decimal list-inside">
              <li>{t("텔레그램에서 ", "Message ")}<b className="text-[var(--text-primary)]">@BotFather</b>{t("에게 메시지 → ", " on Telegram → ")}<code className="bg-[var(--bg-elevated)] px-1 rounded">/newbot</code>{t(" → 토큰 받기", " → get token")}</li>
              <li><code className="bg-[var(--bg-elevated)] px-1 rounded">.env</code>{t(" 파일에 ", " set ")}<code className="bg-[var(--bg-elevated)] px-1 rounded">TELEGRAM_BOT_TOKEN=your-token</code>{t(" 설정", "")}</li>
              <li>{t("ngrok 설치: ", "Install ngrok: ")}<code className="bg-[var(--bg-elevated)] px-1 rounded">ngrok http 8000</code></li>
              <li>{t("Swagger로 웹훅 등록: ", "Register webhook via Swagger: ")}<code className="bg-[var(--bg-elevated)] px-1 rounded">POST /telegram/set-webhook</code></li>
              <li>{t("텔레그램에서 봇에게 ", "Send ")}<code className="bg-[var(--bg-elevated)] px-1 rounded">/status</code>{t(" 전송", " to your bot in Telegram")}</li>
            </ol>
          </div>
        </div>
      )}

      {/* Other Channels - Coming Soon */}
      {activeChannel !== "telegram" && (
        <div className="border border-[var(--border-default)] rounded-lg bg-[var(--bg-card)] p-8 text-center">
          <svg className="w-12 h-12 text-[var(--text-secondary)] mx-auto mb-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1}>
            <path strokeLinecap="round" strokeLinejoin="round" d={channelConfig[activeChannel]?.icon || ""} />
          </svg>
          <h3 className="text-lg font-semibold mb-1">{channelConfig[activeChannel]?.label}</h3>
          <p className="text-[14px] text-[var(--text-muted)] mb-4">{channelConfig[activeChannel]?.description}</p>
          {channels.find((c) => c.type === activeChannel) && (
            <Badge text={channels.find((c) => c.type === activeChannel)?.status || "unknown"} />
          )}
        </div>
      )}
    </div>
  );
}
