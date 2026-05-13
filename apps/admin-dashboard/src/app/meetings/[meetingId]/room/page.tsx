"use client";

import { useEffect, useRef, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { API, api, apiPost } from "../../../../components/api";

interface Participant {
  participant_id: string;
  twin_id: string;
  name: string;
  role: string | null;
  session_status: string;
  worker_email: string | null;
  worker_name: string | null;
  is_proxy: boolean;
  avatar_url?: string | null;
}

function avatarFor(p: Participant): string {
  if (p.avatar_url && p.avatar_url.startsWith("http")) return p.avatar_url;
  const seed = encodeURIComponent(p.twin_id || p.name || "default");
  return `https://api.dicebear.com/9.x/personas/svg?seed=${seed}&size=200&radius=50`;
}

interface Utterance {
  id: string;
  speaker_role: string;
  speaker_label: string | null;
  text: string;
  audio_url: string | null;
  is_commitment: boolean;
}

interface Hand {
  raise_id: string;
  twin_id: string;
  twin_name: string;
  twin_role: string | null;
  confidence_score: number;
  reasoning: string;
  status: string;
  question_text: string;
}

export default function HybridMeetingRoomPage() {
  const params = useParams();
  const router = useRouter();
  const meetingId = (params?.meetingId as string) || "";

  const [meeting, setMeeting] = useState<any>(null);
  const [participants, setParticipants] = useState<Participant[]>([]);
  const [utterances, setUtterances] = useState<Utterance[]>([]);
  const [hands, setHands] = useState<Hand[]>([]);
  const [question, setQuestion] = useState("");
  const [askBusy, setAskBusy] = useState(false);
  const [grantBusy, setGrantBusy] = useState<string | null>(null);
  const [recording, setRecording] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [finalizing, setFinalizing] = useState(false);
  const [finalResult, setFinalResult] = useState<any>(null);
  const [error, setError] = useState("");

  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const audioChunksRef = useRef<Blob[]>([]);
  const pollRef = useRef<NodeJS.Timeout | null>(null);
  const audioPlayerRef = useRef<HTMLAudioElement>(null);

  useEffect(() => {
    if (!meetingId) return;
    (async () => {
      try {
        const m: any = await api(`/meetings/${meetingId}`);
        setMeeting(m);
        setParticipants(m.participants || []);
      } catch (e: any) { setError(`Couldn't load meeting: ${e.message}`); }
    })();
  }, [meetingId]);

  useEffect(() => {
    if (!meetingId || !participants.length) return;
    const poll = async () => {
      try {
        const twinId = participants[0]?.twin_id;
        if (!twinId) return;
        const [u, h]: any[] = await Promise.all([
          api(`/twins/${twinId}/meetings/${meetingId}/utterances`),
          api(`/groups/_meetings/${meetingId}/hands`),
        ]);
        setUtterances(u.utterances || []);
        setHands(h.hands || []);
      } catch {/* silent */}
    };
    poll();
    pollRef.current = setInterval(poll, 2500);
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, [meetingId, participants]);

  // ----- recording -----

  const startRecording = async () => {
    setError("");
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const recorder = new MediaRecorder(stream, { mimeType: "audio/webm" });
      audioChunksRef.current = [];
      recorder.ondataavailable = e => { if (e.data.size > 0) audioChunksRef.current.push(e.data); };
      recorder.onstop = async () => {
        const blob = new Blob(audioChunksRef.current, { type: "audio/webm" });
        await uploadRecording(blob);
        stream.getTracks().forEach(t => t.stop());
      };
      recorder.start();
      mediaRecorderRef.current = recorder;
      setRecording(true);
    } catch (e: any) {
      setError(`Microphone denied: ${e.message}`);
    }
  };
  const stopRecording = () => {
    if (mediaRecorderRef.current && mediaRecorderRef.current.state !== "inactive") {
      mediaRecorderRef.current.stop();
    }
    setRecording(false);
  };
  const uploadRecording = async (blob: Blob) => {
    if (!participants.length) return;
    setUploading(true);
    try {
      const twinId = participants[0].twin_id;
      const form = new FormData();
      form.append("file", blob, "recording.webm");
      const r = await fetch(
        `${API}/twins/${twinId}/meetings/${meetingId}/listen/upload?speaker_label=Boss`,
        { method: "POST", body: form },
      );
      const data = await r.json();
      if (!r.ok) throw new Error(data.detail || "Transcription failed");
    } catch (e: any) { setError(e.message); }
    finally { setUploading(false); }
  };

  // ----- ask question / grant floor -----

  const handleAsk = async () => {
    if (!question.trim()) return;
    setAskBusy(true);
    setError("");
    try {
      const r: any = await apiPost(`/groups/_meetings/${meetingId}/ask`, {
        question,
      });
      setHands(r.hands || []);
      // If a twin was directly named, auto-grant the floor
      if (r.auto_grant_participant_id && r.hands && r.hands.length) {
        const auto = r.hands.find((h: any) => h.auto_granted);
        if (auto) await handleGrant(auto.raise_id);
      }
    } catch (e: any) { setError(e.message); }
    finally { setAskBusy(false); }
  };

  const handleGrant = async (raiseId: string) => {
    setGrantBusy(raiseId);
    setError("");
    try {
      const r: any = await apiPost(`/groups/_meetings/${meetingId}/grant-floor`, {
        raise_id: raiseId,
      });
      const audio = r?.reply?.audio_url;
      if (audio && audioPlayerRef.current) {
        audioPlayerRef.current.src = `${API}${audio}`;
        audioPlayerRef.current.play().catch(() => {});
      }
    } catch (e: any) { setError(e.message); }
    finally { setGrantBusy(null); }
  };

  // ----- finalize -----

  const finalizeMeeting = async () => {
    if (!confirm("End meeting? Twins get summary in knowledge base + email is sent.")) return;
    setFinalizing(true);
    try {
      const r: any = await apiPost(`/twins/meetings/${meetingId}/finalize`);
      setFinalResult(r);
    } catch (e: any) { setError(e.message); }
    finally { setFinalizing(false); }
  };

  // ----- tile component -----

  const handsByParticipant: Record<string, Hand> = {};
  hands.filter(h => h.status === "raised").forEach(h => {
    const p = participants.find(p => p.twin_id === h.twin_id);
    if (p) handsByParticipant[p.participant_id] = h;
  });

  if (!meetingId) return <div className="p-6">No meeting selected.</div>;
  const isScheduled = meeting?.status === "scheduled";

  return (
    <div className="p-4 md:p-6 max-w-[1280px] mx-auto">
      <audio ref={audioPlayerRef} className="hidden" />

      {/* Top bar */}
      <div className="flex justify-between items-start mb-4">
        <div>
          <button onClick={() => router.push("/meetings")} className="text-xs text-indigo-600 underline mb-1">
            ← Back to meetings
          </button>
          <h1 className="text-2xl font-bold">{meeting?.title || "Meeting room"}</h1>
          <p className="text-sm text-gray-500">
            Status: <span className="font-semibold">{meeting?.status}</span>
            {isScheduled && meeting?.scheduled_at && (
              <span className="ml-2 text-amber-700">
                Starts at {new Date(meeting.scheduled_at).toLocaleTimeString()} — twins are waiting.
              </span>
            )}
          </p>
        </div>
        <div className="flex gap-2">
          {!recording ? (
            <button
              onClick={startRecording}
              disabled={uploading || meeting?.status === "ended"}
              className="bg-emerald-600 text-white rounded-lg px-3 py-2 text-sm disabled:opacity-50"
            >
              🎙 Record
            </button>
          ) : (
            <button
              onClick={stopRecording}
              className="bg-rose-600 text-white rounded-lg px-3 py-2 text-sm animate-pulse"
            >
              ■ Stop + transcribe
            </button>
          )}
          <button
            onClick={finalizeMeeting}
            disabled={finalizing || meeting?.status === "ended"}
            className="bg-gray-900 text-white rounded-lg px-3 py-2 text-sm disabled:opacity-50"
          >
            {finalizing ? "Ending…" : "End meeting"}
          </button>
        </div>
      </div>

      {error && (
        <div className="bg-red-50 border border-red-200 text-red-700 px-3 py-2 rounded text-sm mb-4">
          {error}
        </div>
      )}

      {/* Zoom-style attendee tiles */}
      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-3 mb-4">
        {participants.length === 0 && (
          <div className="col-span-full bg-gray-50 rounded-xl p-8 text-center text-sm text-gray-500">
            No attendees yet. {isScheduled && "Twins will appear here when the meeting starts."}
          </div>
        )}
        {participants.map(p => {
          const hand = handsByParticipant[p.participant_id];
          const isWaiting = p.session_status === "active" && isScheduled;
          return (
            <div
              key={p.participant_id}
              onClick={() => hand && handleGrant(hand.raise_id)}
              className={`relative bg-gray-900 text-white rounded-xl aspect-video p-3 flex flex-col justify-between cursor-${hand ? "pointer hover:ring-4 ring-amber-400" : "default"}`}
            >
              {/* Avatar (DiceBear illustrated portrait — v4-J) */}
              <div className="flex-1 flex items-center justify-center">
                <img
                  src={avatarFor(p)}
                  alt={p.name}
                  className="w-20 h-20 rounded-full bg-white object-cover ring-2 ring-white/30"
                />
              </div>

              {/* Hand-raise badge */}
              {hand && (
                <div className="absolute top-2 right-2 bg-amber-400 text-amber-900 rounded-full px-2 py-1 text-[10px] font-bold flex items-center gap-1 animate-pulse">
                  🖐 {Math.round(hand.confidence_score * 100)}%
                </div>
              )}

              {/* Waiting badge */}
              {isWaiting && (
                <div className="absolute top-2 left-2 bg-blue-500 text-white rounded-full px-2 py-0.5 text-[10px]">
                  waiting
                </div>
              )}

              {/* Footer label */}
              <div className="text-xs">
                <div className="font-semibold truncate">{p.name}</div>
                <div className="text-[10px] opacity-70 truncate">
                  {p.is_proxy ? `proxying ${p.worker_name || "worker"}` : p.role || "twin"}
                </div>
                {hand && grantBusy === hand.raise_id && (
                  <div className="text-[10px] text-amber-300 mt-1">Twin speaking…</div>
                )}
                {hand && grantBusy !== hand.raise_id && (
                  <div className="text-[10px] text-amber-300 mt-1">Click to grant floor</div>
                )}
              </div>
            </div>
          );
        })}
      </div>

      {/* Boss ask box */}
      <div className="bg-white rounded-xl border border-gray-200 p-3 mb-4 flex gap-2">
        <input
          value={question}
          onChange={e => setQuestion(e.target.value)}
          onKeyDown={e => e.key === "Enter" && handleAsk()}
          placeholder='Ask the room (e.g. "What is the status of the Q2 report?" or "Davronbek, can you brief us?")'
          className="flex-1 border rounded px-3 py-2 text-sm"
          disabled={askBusy}
        />
        <button
          onClick={handleAsk}
          disabled={askBusy || !question.trim()}
          className="bg-indigo-600 text-white rounded px-4 py-2 text-sm disabled:opacity-50"
        >
          {askBusy ? "Asking…" : "Ask"}
        </button>
      </div>

      {/* Live transcript */}
      <section className="bg-white rounded-xl border border-gray-200 p-4">
        <h2 className="font-semibold text-sm mb-2">Live transcript ({utterances.length})</h2>
        <div className="bg-gray-50 rounded p-3 max-h-[280px] overflow-y-auto space-y-2">
          {utterances.length === 0 && (
            <p className="text-xs text-gray-400">No utterances yet. Record audio or ask a question.</p>
          )}
          {utterances.map(u => (
            <div key={u.id} className={`p-2 rounded text-sm ${u.speaker_role === "twin" ? "bg-sky-50" : "bg-white border"}`}>
              <span className="font-semibold text-xs mr-2">{u.speaker_label || u.speaker_role}</span>
              {u.is_commitment && <span className="text-xs bg-amber-200 text-amber-800 px-1 rounded mr-1">COMMIT</span>}
              <span>{u.text}</span>
              {u.audio_url && (
                <audio src={`${API}${u.audio_url}`} controls className="block mt-1 w-full h-7" />
              )}
            </div>
          ))}
        </div>
      </section>

      {/* Finalize result */}
      {finalResult && (
        <section className="mt-4 bg-emerald-50 border border-emerald-200 rounded-xl p-4">
          <h2 className="font-semibold mb-1">Meeting ended</h2>
          <p className="text-sm text-gray-600 mb-2">Saved to {finalResult.saved_to_twin_knowledge?.length || 0} twin knowledge bases.</p>
          {finalResult.summary?.korean_summary && (
            <details>
              <summary className="cursor-pointer text-sm font-medium">한국어 요약</summary>
              <pre className="whitespace-pre-wrap text-xs bg-white rounded p-3 mt-2">{finalResult.summary.korean_summary}</pre>
            </details>
          )}
          {finalResult.summary?.english_summary && (
            <details>
              <summary className="cursor-pointer text-sm font-medium">English summary</summary>
              <pre className="whitespace-pre-wrap text-xs bg-white rounded p-3 mt-2">{finalResult.summary.english_summary}</pre>
            </details>
          )}
        </section>
      )}
    </div>
  );
}
