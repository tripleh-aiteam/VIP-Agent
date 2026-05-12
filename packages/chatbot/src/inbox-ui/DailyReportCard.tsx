"use client";

/**
 * DailyReportCard — today's summary card at the top of the inbox.
 * Mirrors the visual style of the calls dashboard's report card.
 */

import type { InboxDailyReport } from "./types";

interface Props {
  report: InboxDailyReport;
}

export function DailyReportCard({ report }: Props) {
  return (
    <div className="rounded-2xl border border-blue-200 bg-gradient-to-br from-blue-50 to-purple-50 p-4">
      <div className="flex items-start justify-between mb-3">
        <div>
          <h3 className="text-[13px] font-semibold text-gray-900">
            🌅 Today's summary
          </h3>
          <p className="text-[11px] text-gray-600 mt-0.5">
            Conversations the bot handled + items needing your review
          </p>
        </div>
        <button className="text-[11px] text-blue-600 hover:text-blue-700 font-medium">
          📧 Email report →
        </button>
      </div>

      <div className="grid grid-cols-4 gap-3">
        <Stat label="Total" value={report.totalConversations} />
        <Stat label="AI handled" value={report.handledByBot} color="green" />
        <Stat label="Needs review" value={report.needsReview} color="amber" />
        <Stat label="Urgent" value={report.escalated} color="red" />
      </div>

      <div className="mt-3 pt-3 border-t border-blue-200/50">
        <div className="flex items-start justify-between gap-3">
          <div className="flex-1">
            <div className="text-[11px] text-gray-600 mb-1.5 font-medium">Top topics</div>
            <div className="flex flex-wrap gap-1.5">
              {report.topTopics.map((t) => (
                <span
                  key={t.topic}
                  className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-white border border-blue-200 text-[11px] text-gray-700"
                >
                  {t.topic}
                  <span className="text-gray-400">·</span>
                  <span className="font-medium text-blue-700">{t.count}</span>
                </span>
              ))}
            </div>
          </div>
          {report.averageResponseSec !== undefined && (
            <div className="shrink-0 text-right">
              <div className="text-[10px] text-gray-500 uppercase tracking-wider">Avg response</div>
              <div className="text-[18px] font-semibold text-blue-700">
                {report.averageResponseSec}
                <span className="text-[11px] text-gray-500 ml-0.5">s</span>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function Stat({
  label,
  value,
  color,
}: {
  label: string;
  value: number;
  color?: "green" | "red" | "amber" | "gray";
}) {
  const colors = {
    green: "text-green-700",
    red: "text-red-700",
    amber: "text-amber-700",
    gray: "text-gray-700",
  };
  const c = color ? colors[color] : "text-blue-700";
  return (
    <div className="bg-white rounded-lg p-2.5 border border-blue-100">
      <div className="text-[10px] text-gray-500 uppercase tracking-wider">{label}</div>
      <div className={`text-xl font-semibold mt-0.5 ${c}`}>{value}</div>
    </div>
  );
}
