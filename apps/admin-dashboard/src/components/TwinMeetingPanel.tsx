"use client";

import { useEffect, useRef, useState } from "react";
import { API, api, apiPost } from "./api";

interface Meeting {
  id: string;
  title: string;
  status: string;
}

interface Utterance {
  id: string;
  speaker_role: string;
  speaker_label: string | null;
  text: string;
  text_korean: string | null;
  audio_url: string | null;
  spoken_at: string | null;
  is_commitment: boolean;
  requires_worker_review: boolean;
  latency_ms: number | null;
}

interface TwinMeetingPanelProps {
  twinId: string;
  twinName: string;
  onClose: () => void;
}

type Authority = "listener_only" | "answer_factual" | "answer_and_commit" | "full_proxy";

export default function TwinMeetingPanel({ twinId, twinName, onClose }: TwinMeetingPanelProps) {
  const [meetings, setMeetings] = useState<Meeting[]>([]);
  const [selectedMeetingId, setSelectedMeetingId] = useState<string>("");
  const [authority, setAuthority] = useState<Authority>("answer_factual");
  const [reason, setReason] = useState<string>("");
  const [participantId, setParticipantId] = useState<string | null>(null);
  const [utterances, setUtterances] = useState<Utterance[]>([]);
  const [listenSessionId, setListenSessionId] = useState<string | null>(null);
  const [listenStatus, setListenStatus] = useState<string>("");
  const [promptText, setPromptText] = useState<string>("");
  const [twinReply, setTwinReply] = useState<{ text: string; audio_url: string | null; escalated: boolean } | null>(null);
  const [busy, setBusy] = useState<string>("");
  const [error, setError] = useState<string>("");
  const fileInputRef = useRef<HTMLInputElement>(null);
  const audioRef = useRef<HTMLAudioElement>(null);
  const pollRef = useRef<NodeJS.Timeout | null>(null);

  // Load meetings — hide ones already ended so boss can't try to join them
  useEffect(() => {
    (async () => {
      try {
        const data: any[] = await api(`/meetings`);
        const open = (data || [])
          .filter(m => m.status !== "ended")
          .map(m => ({ id: m.id, title: m.title, status: m.status }));
        setMeetings(open);
      } catch (e: any) {
        setError(`Could not load meetings: ${e.message}`);
      }
    })();
  }, []);

  // Poll utterances + listen status ONLY after the twin has joined.
  // Polling before join hit /utterances on meetings the twin wasn't in,
  // and that caused spurious 'Not Found' noise during early development.
  useEffect(() => {
    if (!participantId || !selectedMeetingId) return;
    const poll = async () => {
      try {
        const data: { utterances: Utterance[] } = await api(
          `/twins/${twinId}/meetings/${selectedMeetingId}/utterances`,
        );
        setUtterances(data.utterances || []);
      } catch {/* poll noise — surfaced via main error path on the relevant button */}
      if (listenSessionId) {
        try {
          const s: any = await api(
            `/twins/${twinId}/meetings/${selectedMeetingId}/listen/status?session_id=${listenSessionId}`,
          );
          setListenStatus(`${s.status} (${s.chunks_processed} chunks)`);
        } catch {/* ignore */}
      }
    };
    poll();
    pollRef.current = setInterval(poll, 2000);
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [participantId, selectedMeetingId, twinId, listenSessionId]);

  // --------------- Actions ---------------

  const handleJoin = async () => {
    if (!selectedMeetingId) {
      setError("Pick a meeting first");
      return;
    }
    setBusy("joining");
    setError("");
    try {
      const result: any = await apiPost(`/twins/${twinId}/meetings/join`, {
        meeting_id: selectedMeetingId,
        authority,
        reason,
      });
      setParticipantId(result.participant_id);
    } catch (e: any) {
      // Surface the backend's actual message so the boss knows why (ended meeting,
      // already attending, twin not found, rate-limit, etc.).
      const msg = e?.message || "Join failed";
      setError(
        msg === "Not Found"
          ? `Join failed at POST /twins/${twinId}/meetings/join. The endpoint returned a generic 404 — confirm the backend is running and the twin/meeting still exist.`
          : msg,
      );
    } finally {
      setBusy("");
    }
  };

  const handleLeave = async () => {
    if (!selectedMeetingId) return;
    setBusy("leaving");
    try {
      await apiPost(`/twins/${twinId}/meetings/${selectedMeetingId}/leave`, {
        generate_summary: true,
      });
      setParticipantId(null);
    } catch (e: any) {
      setError(e.message || "Leave failed");
    } finally {
      setBusy("");
    }
  };

  const handleUpload = async () => {
    const file = fileInputRef.current?.files?.[0];
    if (!file || !selectedMeetingId) return;
    setBusy("uploading");
    setError("");
    const form = new FormData();
    form.append("file", file);
    try {
      const res = await fetch(
        `${API}/twins/${twinId}/meetings/${selectedMeetingId}/listen/upload?speaker_label=Boss`,
        { method: "POST", body: form },
      );
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Upload failed");
      setListenSessionId(data.session_id);
      setListenStatus("running (0 chunks)");
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy("");
    }
  };

  const handleAskTwin = async () => {
    if (!promptText.trim() || !selectedMeetingId) return;
    setBusy("asking");
    setError("");
    setTwinReply(null);
    try {
      const result: any = await apiPost(
        `/twins/${twinId}/meetings/${selectedMeetingId}/twin-respond`,
        { prompt: promptText, speak_aloud: true },
      );
      setTwinReply({
        text: result.text || result.wanted_to_say || "(no reply)",
        audio_url: result.audio_url,
        escalated: !!result.escalated,
      });
      // Auto-play the reply
      if (result.audio_url && audioRef.current) {
        audioRef.current.src = `${API}${result.audio_url}`;
        audioRef.current.play().catch(() => {/* user gesture issues */});
      }
    } catch (e: any) {
      setError(e.message || "Ask failed");
    } finally {
      setBusy("");
    }
  };

  // --------------- Render ---------------

  return (
    <div className="fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-4">
      <div className="bg-white rounded-xl shadow-2xl w-full max-w-4xl max-h-[90vh] overflow-y-auto">
        <div className="p-5 border-b flex justify-between items-center sticky top-0 bg-white z-10">
          <div>
            <h2 className="text-xl font-bold">Twin Meeting Console</h2>
            <p className="text-sm text-gray-500">{twinName}</p>
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-700 text-2xl leading-none">
            ×
          </button>
        </div>

        <div className="p-5 space-y-5">
          {error && (
            <div className="bg-red-50 border border-red-200 text-red-700 px-3 py-2 rounded text-sm">
              {error}
            </div>
          )}

          {/* Meeting selection + authority */}
          <section className="space-y-3">
            <label className="block text-sm font-medium text-gray-700">Meeting</label>
            <select
              value={selectedMeetingId}
              onChange={e => setSelectedMeetingId(e.target.value)}
              className="w-full border rounded px-3 py-2"
              disabled={!!participantId}
            >
              <option value="">— pick a meeting —</option>
              {meetings.map(m => (
                <option key={m.id} value={m.id}>
                  {m.title} ({m.status})
                </option>
              ))}
            </select>

            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Authority</label>
                <select
                  value={authority}
                  onChange={e => setAuthority(e.target.value as Authority)}
                  className="w-full border rounded px-3 py-2"
                  disabled={!!participantId}
                >
                  <option value="listener_only">Listener only</option>
                  <option value="answer_factual">Answer factual questions</option>
                  <option value="answer_and_commit">Answer + commit</option>
                  <option value="full_proxy">Full proxy</option>
                </select>
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Reason</label>
                <input
                  value={reason}
                  onChange={e => setReason(e.target.value)}
                  placeholder="sick day, personal..."
                  className="w-full border rounded px-3 py-2"
                  disabled={!!participantId}
                />
              </div>
            </div>

            <div className="flex gap-2">
              {!participantId ? (
                <button
                  onClick={handleJoin}
                  disabled={!selectedMeetingId || busy === "joining"}
                  className="bg-indigo-600 text-white px-4 py-2 rounded disabled:opacity-50"
                >
                  {busy === "joining" ? "Joining…" : "Send Twin to Meeting"}
                </button>
              ) : (
                <button
                  onClick={handleLeave}
                  disabled={busy === "leaving"}
                  className="bg-rose-600 text-white px-4 py-2 rounded disabled:opacity-50"
                >
                  {busy === "leaving" ? "Leaving…" : "Leave Meeting + Summarize"}
                </button>
              )}
            </div>
          </section>

          {/* STT: upload WAV */}
          {participantId && (
            <section className="border-t pt-4 space-y-2">
              <label className="block text-sm font-medium text-gray-700">
                Stream meeting audio (upload WAV — Sprint 2)
              </label>
              <div className="flex gap-2 items-center">
                <input ref={fileInputRef} type="file" accept=".wav,audio/wav" className="flex-1 text-sm" />
                <button
                  onClick={handleUpload}
                  disabled={busy === "uploading"}
                  className="bg-emerald-600 text-white px-3 py-2 rounded text-sm disabled:opacity-50"
                >
                  {busy === "uploading" ? "Uploading…" : "Transcribe"}
                </button>
              </div>
              {listenStatus && (
                <p className="text-xs text-gray-500">STT session: {listenStatus}</p>
              )}
            </section>
          )}

          {/* Twin speaks */}
          {participantId && (
            <section className="border-t pt-4 space-y-2">
              <label className="block text-sm font-medium text-gray-700">
                Ask the twin (Sprint 3 — twin speaks aloud)
              </label>
              <textarea
                value={promptText}
                onChange={e => setPromptText(e.target.value)}
                placeholder="e.g. 'What's the status of the Q2 report?'"
                rows={2}
                className="w-full border rounded px-3 py-2 text-sm"
              />
              <button
                onClick={handleAskTwin}
                disabled={busy === "asking" || !promptText.trim()}
                className="bg-sky-600 text-white px-3 py-2 rounded text-sm disabled:opacity-50"
              >
                {busy === "asking" ? "Twin thinking…" : "Ask twin"}
              </button>
              {twinReply && (
                <div className={`mt-2 p-3 rounded border ${twinReply.escalated ? "bg-amber-50 border-amber-200" : "bg-sky-50 border-sky-200"}`}>
                  <p className="text-xs font-semibold mb-1">
                    {twinReply.escalated ? "⚠ Escalated to worker — stall phrase spoken:" : "Twin said:"}
                  </p>
                  <p className="text-sm">{twinReply.text}</p>
                  {twinReply.audio_url && (
                    <audio ref={audioRef} controls className="mt-2 w-full" />
                  )}
                </div>
              )}
            </section>
          )}

          {/* Live utterance log */}
          {participantId && (
            <section className="border-t pt-4">
              <h3 className="text-sm font-medium text-gray-700 mb-2">
                Live transcript ({utterances.length})
              </h3>
              <div className="bg-gray-50 rounded p-3 max-h-72 overflow-y-auto space-y-2">
                {utterances.length === 0 && (
                  <p className="text-xs text-gray-400">No utterances yet — upload an audio file or ask the twin.</p>
                )}
                {utterances.map(u => (
                  <div
                    key={u.id}
                    className={`p-2 rounded text-sm ${u.speaker_role === "twin" ? "bg-sky-100" : "bg-white border"}`}
                  >
                    <div className="flex justify-between items-start gap-2">
                      <span className="font-semibold text-xs">
                        {u.speaker_label || u.speaker_role}
                      </span>
                      <div className="flex gap-1">
                        {u.is_commitment && (
                          <span className="text-xs bg-amber-200 text-amber-800 px-1.5 rounded">COMMIT</span>
                        )}
                        {u.requires_worker_review && (
                          <span className="text-xs bg-rose-200 text-rose-800 px-1.5 rounded">REVIEW</span>
                        )}
                      </div>
                    </div>
                    <p className="mt-1">{u.text}</p>
                    {u.audio_url && (
                      <audio src={`${API}${u.audio_url}`} controls className="mt-1 w-full h-7" />
                    )}
                  </div>
                ))}
              </div>
            </section>
          )}
        </div>
      </div>
    </div>
  );
}
