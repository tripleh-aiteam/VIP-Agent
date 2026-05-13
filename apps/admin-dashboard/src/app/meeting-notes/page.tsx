"use client";

import { useEffect, useState, useRef } from "react";
import { API } from "../../components/api";
import MeetingsTabs from "@/components/MeetingsTabs";

interface MeetingSummary {
  meeting_title: string;
  generated_at: string;
  transcript_length: number;
  english_summary: string;
  korean_summary: string;
  action_items: { who: string; task: string; deadline: string }[];
  participants: string[];
}

export default function MeetingNotesPage() {
  const [isRecording, setIsRecording] = useState(false);
  const [transcript, setTranscript] = useState("");
  const [liveText, setLiveText] = useState("");
  const [title, setTitle] = useState("");
  const [participants, setParticipants] = useState("");
  const [summary, setSummary] = useState<MeetingSummary | null>(null);
  const [generating, setGenerating] = useState(false);
  const [lang, setLang] = useState<"en" | "ko" | "both">("both");
  const [twins, setTwins] = useState<any[]>([]);
  const [saveTwinIds, setSaveTwinIds] = useState<string[]>([]);
  const [pastNotes, setPastNotes] = useState<MeetingSummary[]>([]);
  const recognitionRef = useRef<any>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    fetch(`${API}/twins`).then(r => r.json()).then(setTwins).catch(() => {});
  }, []);

  function startRecording() {
    const SpeechRecognition = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
    if (!SpeechRecognition) {
      alert("Your browser doesn't support speech recognition. Use Chrome.");
      return;
    }

    const recognition = new SpeechRecognition();
    recognition.continuous = true;
    recognition.interimResults = true;
    recognition.lang = "en-US"; // Will also pick up Korean

    recognition.onresult = (event: any) => {
      let interim = "";
      let final = "";
      for (let i = event.resultIndex; i < event.results.length; i++) {
        const t = event.results[i][0].transcript;
        if (event.results[i].isFinal) {
          final += t + " ";
        } else {
          interim = t;
        }
      }
      if (final) {
        setTranscript(prev => prev + final);
      }
      setLiveText(interim);
    };

    recognition.onerror = (event: any) => {
      console.error("Speech recognition error:", event.error);
      if (event.error === "not-allowed") {
        alert("Microphone access denied. Please allow microphone in browser settings.");
      }
    };

    recognition.onend = () => {
      if (isRecording) {
        recognition.start(); // Auto-restart if still recording
      }
    };

    recognition.start();
    recognitionRef.current = recognition;
    setIsRecording(true);
  }

  function stopRecording() {
    if (recognitionRef.current) {
      recognitionRef.current.stop();
      recognitionRef.current = null;
    }
    setIsRecording(false);
    setLiveText("");
  }

  async function generateSummary() {
    if (!transcript.trim() && !textareaRef.current?.value.trim()) return;

    const finalTranscript = transcript || textareaRef.current?.value || "";
    setGenerating(true);
    try {
      const res = await fetch(`${API}/twins/meetings/summarize`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          transcript: finalTranscript,
          meeting_title: title || "Meeting",
          participants: participants.split(",").map(p => p.trim()).filter(Boolean),
          save_to_twin_ids: saveTwinIds,
        }),
      });
      const data = await res.json();
      setSummary(data);
      setPastNotes(prev => [data, ...prev]);
    } catch (e) {
      console.error("Failed to generate summary:", e);
    } finally {
      setGenerating(false);
    }
  }

  return (
    <div className="p-4 md:p-6 max-w-[1000px] mx-auto">
      <MeetingsTabs />
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-[28px] font-semibold text-[var(--text-primary)]">Meeting Notes</h1>
          <p className="text-[13px] text-[var(--text-muted)] mt-1">Record meetings, auto-transcribe, generate summaries in Korean & English</p>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Left: Recording + Transcript */}
        <div className="space-y-4">
          {/* Meeting Info */}
          <div className="bg-[var(--card-bg)] rounded-2xl border border-[var(--card-border)] p-5" style={{ boxShadow: "var(--shadow-sm)" }}>
            <h2 className="text-[14px] font-semibold text-[var(--text-primary)] mb-3">Meeting Info</h2>
            <div className="space-y-3">
              <div>
                <label className="block text-[11px] font-medium text-[var(--text-muted)] mb-1">Title</label>
                <input type="text" value={title} onChange={e => setTitle(e.target.value)}
                  placeholder="e.g. Weekly Team Standup, Strategy Meeting"
                  className="w-full px-3 py-2.5 bg-[var(--bg-secondary)] border border-[var(--card-border)] rounded-lg text-[13px] text-[var(--text-primary)] placeholder:text-[var(--text-muted)] focus:outline-none focus:border-blue-400" />
              </div>
              <div>
                <label className="block text-[11px] font-medium text-[var(--text-muted)] mb-1">Participants (comma separated)</label>
                <input type="text" value={participants} onChange={e => setParticipants(e.target.value)}
                  placeholder="e.g. Davronbek, Boss, Dev 1, Client"
                  className="w-full px-3 py-2.5 bg-[var(--bg-secondary)] border border-[var(--card-border)] rounded-lg text-[13px] text-[var(--text-primary)] placeholder:text-[var(--text-muted)] focus:outline-none focus:border-blue-400" />
              </div>
              <div>
                <label className="block text-[11px] font-medium text-[var(--text-muted)] mb-1">Save to twins' knowledge</label>
                <div className="flex flex-wrap gap-2">
                  {twins.map((t: any) => (
                    <label key={t.id} className="flex items-center gap-1.5 text-[11px] cursor-pointer">
                      <input type="checkbox" checked={saveTwinIds.includes(t.id)}
                        onChange={e => {
                          if (e.target.checked) setSaveTwinIds(prev => [...prev, t.id]);
                          else setSaveTwinIds(prev => prev.filter(id => id !== t.id));
                        }} className="w-3 h-3" />
                      {t.name}
                    </label>
                  ))}
                </div>
              </div>
            </div>
          </div>

          {/* Voice Recording */}
          <div className="bg-[var(--card-bg)] rounded-2xl border border-[var(--card-border)] p-5" style={{ boxShadow: "var(--shadow-sm)" }}>
            <h2 className="text-[14px] font-semibold text-[var(--text-primary)] mb-3">Record Meeting</h2>

            {/* Record Button */}
            <div className="flex items-center gap-3 mb-4">
              {!isRecording ? (
                <button onClick={startRecording}
                  className="flex items-center gap-2 px-5 py-3 bg-red-500 text-white rounded-xl text-[13px] font-semibold hover:bg-red-600 transition-colors">
                  <div className="w-3 h-3 rounded-full bg-white" />
                  Start Recording
                </button>
              ) : (
                <button onClick={stopRecording}
                  className="flex items-center gap-2 px-5 py-3 bg-gray-700 text-white rounded-xl text-[13px] font-semibold hover:bg-gray-800 transition-colors">
                  <div className="w-3 h-3 rounded bg-red-500 animate-pulse" />
                  Stop Recording
                </button>
              )}
              {isRecording && (
                <div className="flex items-center gap-2 text-[12px] text-red-500">
                  <span className="w-2 h-2 rounded-full bg-red-500 animate-pulse" />
                  Recording... speak now
                </div>
              )}
            </div>

            {/* Live transcription */}
            {liveText && (
              <div className="bg-blue-50 rounded-lg px-4 py-2 mb-3 text-[12px] text-blue-700 italic">
                {liveText}...
              </div>
            )}

            {/* Transcript */}
            <div>
              <label className="block text-[11px] font-medium text-[var(--text-muted)] mb-1">
                Transcript {transcript ? `(${transcript.split(" ").length} words)` : "(record or paste)"}
              </label>
              <textarea
                ref={textareaRef}
                value={transcript}
                onChange={e => setTranscript(e.target.value)}
                rows={10}
                placeholder="Recording will appear here automatically...&#10;&#10;Or paste your meeting transcript / notes here manually."
                className="w-full px-4 py-3 bg-[var(--bg-secondary)] border border-[var(--card-border)] rounded-xl text-[13px] text-[var(--text-primary)] placeholder:text-[var(--text-muted)] focus:outline-none focus:border-blue-400 resize-none leading-relaxed"
              />
            </div>

            {/* Generate Button */}
            <button onClick={generateSummary}
              disabled={(!transcript.trim()) || generating}
              className="w-full mt-3 py-3 bg-blue-600 text-white rounded-xl text-[14px] font-semibold hover:bg-blue-700 disabled:opacity-50 flex items-center justify-center gap-2">
              {generating ? (
                <>
                  <div className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin" />
                  Generating Summary...
                </>
              ) : (
                <>
                  <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}><path strokeLinecap="round" strokeLinejoin="round" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" /></svg>
                  Generate Summary (Korean + English)
                </>
              )}
            </button>
          </div>
        </div>

        {/* Right: Summary Output (Notion-style) */}
        <div>
          {!summary ? (
            <div className="bg-[var(--card-bg)] rounded-2xl border border-[var(--card-border)] p-8 text-center" style={{ boxShadow: "var(--shadow-sm)" }}>
              <div className="text-[48px] mb-3">📝</div>
              <div className="text-[16px] font-semibold text-[var(--text-primary)] mb-2">Meeting Notes</div>
              <div className="text-[13px] text-[var(--text-muted)]">Record or paste a meeting transcript, then click "Generate Summary"</div>
              <div className="text-[12px] text-[var(--text-muted)] mt-2">Summaries will appear here in both Korean and English</div>
            </div>
          ) : (
            <div className="bg-[var(--card-bg)] rounded-2xl border border-[var(--card-border)] overflow-hidden" style={{ boxShadow: "var(--shadow-sm)" }}>
              {/* Notion-style Header */}
              <div className="bg-gradient-to-r from-blue-50 to-purple-50 px-6 py-5 border-b border-[var(--card-border)]">
                <div className="text-[10px] text-[var(--text-muted)] mb-1">{summary.generated_at ? new Date(summary.generated_at).toLocaleString("en-US", { weekday: "long", year: "numeric", month: "long", day: "numeric", hour: "2-digit", minute: "2-digit" }) : ""}</div>
                <h2 className="text-[20px] font-bold text-[var(--text-primary)]">{summary.meeting_title}</h2>
                {summary.participants.length > 0 && (
                  <div className="flex gap-1.5 mt-2 flex-wrap">
                    {summary.participants.map((p, i) => (
                      <span key={i} className="px-2 py-0.5 bg-white rounded-full text-[10px] text-[var(--text-secondary)] border border-gray-200">{p}</span>
                    ))}
                  </div>
                )}
              </div>

              {/* Language Toggle */}
              <div className="px-6 py-3 border-b border-[var(--card-border)] flex gap-2">
                {(["both", "en", "ko"] as const).map(l => (
                  <button key={l} onClick={() => setLang(l)}
                    className={`px-3 py-1.5 rounded-lg text-[11px] font-medium transition-all ${
                      lang === l ? "bg-blue-600 text-white" : "bg-gray-100 text-gray-600 hover:bg-gray-200"
                    }`}>
                    {l === "both" ? "Both" : l === "en" ? "English" : "한국어"}
                  </button>
                ))}
              </div>

              {/* Summary Content */}
              <div className="px-6 py-5 max-h-[calc(100vh-300px)] overflow-y-auto">
                {/* English Summary */}
                {(lang === "en" || lang === "both") && (
                  <div className={lang === "both" ? "mb-6 pb-6 border-b border-[var(--card-border)]" : ""}>
                    {lang === "both" && <div className="text-[11px] font-medium text-blue-600 mb-2 flex items-center gap-1">🇺🇸 English Summary</div>}
                    <div className="text-[13px] text-[var(--text-primary)] leading-relaxed whitespace-pre-wrap">
                      {summary.english_summary}
                    </div>
                  </div>
                )}

                {/* Korean Summary */}
                {(lang === "ko" || lang === "both") && (
                  <div className={lang === "both" ? "mb-6 pb-6 border-b border-[var(--card-border)]" : ""}>
                    {lang === "both" && <div className="text-[11px] font-medium text-purple-600 mb-2 flex items-center gap-1">🇰🇷 한국어 요약</div>}
                    <div className="text-[13px] text-[var(--text-primary)] leading-relaxed whitespace-pre-wrap">
                      {summary.korean_summary}
                    </div>
                  </div>
                )}

                {/* Action Items */}
                {summary.action_items.length > 0 && (
                  <div>
                    <div className="text-[12px] font-semibold text-green-700 mb-2 flex items-center gap-1">
                      <span>✅</span> Action Items
                    </div>
                    <div className="space-y-1.5">
                      {summary.action_items.map((a, i) => (
                        <div key={i} className="flex items-start gap-2 text-[12px]">
                          <input type="checkbox" className="mt-1 w-3.5 h-3.5 rounded" />
                          <div>
                            <span className="font-medium text-[var(--text-primary)]">{a.who}</span>
                            <span className="text-[var(--text-secondary)]"> — {a.task}</span>
                            {a.deadline && <span className="text-[var(--text-muted)]"> (by {a.deadline})</span>}
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>

              {/* Footer Actions */}
              <div className="px-6 py-3 border-t border-[var(--card-border)] flex gap-2">
                <button onClick={() => {
                  const text = `# ${summary.meeting_title}\n\n## English Summary\n${summary.english_summary}\n\n## 한국어 요약\n${summary.korean_summary}`;
                  navigator.clipboard.writeText(text);
                  alert("Copied to clipboard!");
                }} className="px-3 py-1.5 bg-gray-100 text-gray-600 rounded-lg text-[11px] font-medium hover:bg-gray-200">
                  Copy
                </button>
                <button onClick={() => {
                  const text = `# ${summary.meeting_title}\n\n## English Summary\n${summary.english_summary}\n\n## 한국어 요약\n${summary.korean_summary}`;
                  const blob = new Blob([text], { type: "text/markdown" });
                  const url = URL.createObjectURL(blob);
                  const a = document.createElement("a"); a.href = url; a.download = `${summary.meeting_title.replace(/\s+/g, "_")}_notes.md`; a.click();
                }} className="px-3 py-1.5 bg-gray-100 text-gray-600 rounded-lg text-[11px] font-medium hover:bg-gray-200">
                  Download .md
                </button>
                <button onClick={() => { setSummary(null); setTranscript(""); setTitle(""); }}
                  className="px-3 py-1.5 bg-blue-50 text-blue-600 rounded-lg text-[11px] font-medium hover:bg-blue-100 ml-auto">
                  New Meeting
                </button>
              </div>
            </div>
          )}

          {/* Past Notes */}
          {pastNotes.length > 1 && (
            <div className="mt-4 bg-[var(--card-bg)] rounded-2xl border border-[var(--card-border)] p-5" style={{ boxShadow: "var(--shadow-sm)" }}>
              <h3 className="text-[13px] font-semibold text-[var(--text-primary)] mb-2">Previous Notes ({pastNotes.length - 1})</h3>
              {pastNotes.slice(1).map((n, i) => (
                <div key={i} className="flex items-center justify-between py-2 border-b border-gray-100 last:border-0 cursor-pointer hover:bg-gray-50 rounded px-2 -mx-2"
                  onClick={() => setSummary(n)}>
                  <span className="text-[12px] text-[var(--text-primary)]">{n.meeting_title}</span>
                  <span className="text-[10px] text-[var(--text-muted)]">{n.generated_at ? new Date(n.generated_at).toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit" }) : ""}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
