"use client";

export default function MeetingsPage() {
  return (
    <div>
      <h1 className="text-[28px] font-semibold tracking-tight mb-1">Meetings</h1>
      <p className="text-[14px] text-[var(--text-muted)] mb-8">Digital Twin meeting room — coming soon</p>

      <div className="border border-[var(--border-default)] rounded-xl p-8 bg-[var(--bg-card)] text-center">
        <div className="w-16 h-16 rounded-2xl bg-blue-50 flex items-center justify-center mx-auto mb-4">
          <svg className="w-8 h-8 text-blue-500" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0zm6 3a2 2 0 11-4 0 2 2 0 014 0zM7 10a2 2 0 11-4 0 2 2 0 014 0z" />
          </svg>
        </div>
        <h2 className="text-[18px] font-semibold text-[var(--text-primary)] mb-2">Digital Twin Meetings</h2>
        <p className="text-[13px] text-[var(--text-muted)] max-w-md mx-auto">
          Schedule meetings with your AI digital twins. Assign tasks, get reports,
          and make decisions — even on weekends and after hours.
        </p>
      </div>
    </div>
  );
}
