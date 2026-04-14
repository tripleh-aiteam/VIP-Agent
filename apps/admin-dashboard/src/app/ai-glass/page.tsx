"use client";

import { useEffect, useState } from "react";
import { api, apiPost } from "@/components/api";
import Badge from "@/components/Badge";
import { AskVIPFloat } from "@/components/AskVIP";
import StatCard from "@/components/StatCard";

export default function AIGlassPage() {
  const [sessions, setSessions] = useState<any[]>([]);
  const [stats, setStats] = useState<any>({ total: 0, pending: 0, processing: 0, completed: 0, failed: 0 });
  const [filter, setFilter] = useState<string>("all");
  const [submitting, setSubmitting] = useState(false);
  const [detail, setDetail] = useState<any>(null);

  const load = () => {
    api<any[]>("/ai-glass/sessions?limit=30").then(setSessions).catch(() => {});
    api<any>("/ai-glass/stats").then(setStats).catch(() => {});
  };

  useEffect(() => { load(); const i = setInterval(load, 5000); return () => clearInterval(i); }, []);

  const submitCapture = async () => {
    setSubmitting(true);
    await apiPost("/ai-glass/capture", {
      trace_id: `tr-glass-${Date.now()}`,
      device_id: `glass-device-${String.fromCharCode(65 + Math.floor(Math.random() * 5))}${Math.floor(Math.random() * 9) + 1}`,
      capture_type: "spatial_3d",
      property_ref: `PROP-${new Date().toISOString().slice(0, 10)}-${Math.floor(Math.random() * 900) + 100}`,
      video_uri: `s3://vip-captures/demo/${Date.now()}/capture.mp4`,
      audio_uri: `s3://vip-captures/demo/${Date.now()}/audio.wav`,
      metadata: { fps: 30, resolution: "4K", stereo: true, location: { lat: 37.5665, lng: 126.978 } },
    });
    load();
    setSubmitting(false);
  };

  const filtered = filter === "all" ? sessions : sessions.filter((s) => s.processing_status === filter);

  const statusIcon: Record<string, string> = {
    pending: "bg-yellow-400",
    processing: "bg-blue-400 animate-pulse",
    completed: "bg-green-400",
    failed: "bg-red-400",
    manual_review: "bg-orange-400",
  };

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold mb-1">AI Glass</h1>
          <p className="text-sm text-gray-500">Spatial capture sessions and processing</p>
        </div>
        <button onClick={submitCapture} disabled={submitting}
          className="px-4 py-2 rounded bg-yellow-600 hover:bg-yellow-500 text-white text-xs font-semibold disabled:opacity-50">
          {submitting ? "Submitting..." : "Simulate Capture"}
        </button>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-5 gap-3 mb-6">
        <StatCard label="Total" value={stats.total} color="gray" />
        <StatCard label="Pending" value={stats.pending} color="yellow" />
        <StatCard label="Processing" value={stats.processing} color="blue" />
        <StatCard label="Completed" value={stats.completed} color="green" />
        <StatCard label="Failed / Review" value={stats.failed} color="red" />
      </div>

      {/* Filter */}
      <div className="flex gap-1 mb-4 border-b border-gray-800">
        {["all", "pending", "processing", "completed", "failed", "manual_review"].map((f) => (
          <button key={f} onClick={() => setFilter(f)}
            className={`px-3 py-2 text-xs font-medium capitalize transition-colors ${
              filter === f ? "text-yellow-400 border-b-2 border-yellow-400" : "text-gray-500 hover:text-gray-300"
            }`}>
            {f === "manual_review" ? "Manual Review" : f}
          </button>
        ))}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {/* Session List */}
        <div className="lg:col-span-2 space-y-2">
          {filtered.map((s: any) => (
            <div key={s.id} onClick={() => setDetail(s)}
              className={`border rounded-lg bg-gray-900/50 cursor-pointer hover:border-gray-600 transition-colors ${
                detail?.id === s.id ? "border-yellow-600" : "border-gray-800"
              }`}>
              <div className="px-4 py-3 flex items-center justify-between">
                <div className="flex items-center gap-3 flex-1 min-w-0">
                  <div className={`w-2 h-2 rounded-full ${statusIcon[s.processing_status] || "bg-gray-500"}`} />
                  <span className="text-xs font-mono text-gray-400">{s.device_id}</span>
                  {s.property_ref && <span className="text-[10px] px-2 py-0.5 rounded-full bg-purple-900/30 text-purple-400">{s.property_ref}</span>}
                  <Badge text={s.processing_status} />
                </div>
                <span className="text-[10px] text-gray-600">{s.created_at ? new Date(s.created_at).toLocaleString() : ""}</span>
              </div>
              <div className="px-4 pb-2 flex gap-3 text-[10px] text-gray-500">
                {s.video_uri && <span>Video</span>}
                {s.audio_uri && <span>Audio</span>}
                {s.model_3d_uri && <span className="text-green-400">3D Model</span>}
              </div>
            </div>
          ))}
          {filtered.length === 0 && (
            <p className="text-center text-gray-500 py-10 text-sm">No capture sessions. Click &quot;Simulate Capture&quot; to create one.</p>
          )}
        </div>

        {/* Detail Panel */}
        <div className="border border-gray-800 rounded-lg bg-gray-900/50 h-fit sticky top-6">
          {detail ? (
            <div>
              <div className="px-4 py-3 border-b border-gray-800 flex items-center justify-between">
                <h3 className="text-sm font-semibold">Session Detail</h3>
                <button onClick={() => setDetail(null)} className="text-gray-500 hover:text-white text-xs">Close</button>
              </div>
              <div className="p-4 space-y-3 text-xs max-h-[70vh] overflow-y-auto">
                <div className="flex justify-between text-gray-400">
                  <span>ID</span><span className="text-white font-mono">{detail.id.slice(0, 12)}...</span>
                </div>
                <div className="flex justify-between text-gray-400">
                  <span>Device</span><span className="text-white">{detail.device_id}</span>
                </div>
                <div className="flex justify-between text-gray-400">
                  <span>Property</span><span className="text-white">{detail.property_ref || "—"}</span>
                </div>
                <div className="flex justify-between text-gray-400">
                  <span>Status</span><Badge text={detail.processing_status} />
                </div>

                {/* URIs */}
                <div className="pt-2 border-t border-gray-800 space-y-1.5">
                  <h4 className="text-[10px] text-gray-500 font-medium">Files</h4>
                  {detail.video_uri && <p className="text-[10px] text-blue-400 truncate">{detail.video_uri}</p>}
                  {detail.audio_uri && <p className="text-[10px] text-cyan-400 truncate">{detail.audio_uri}</p>}
                  {detail.model_3d_uri && <p className="text-[10px] text-green-400 truncate">{detail.model_3d_uri}</p>}
                  {!detail.video_uri && !detail.audio_uri && !detail.model_3d_uri && <p className="text-[10px] text-gray-600">No files</p>}
                </div>

                {/* Metadata */}
                {detail.metadata && Object.keys(detail.metadata).length > 0 && (
                  <div className="pt-2 border-t border-gray-800">
                    <h4 className="text-[10px] text-gray-500 font-medium mb-1">Metadata</h4>
                    <div className="bg-gray-800 rounded p-2 text-[10px] text-gray-400 max-h-40 overflow-y-auto">
                      <pre className="whitespace-pre-wrap">{JSON.stringify(detail.metadata, null, 2)}</pre>
                    </div>
                  </div>
                )}
              </div>
            </div>
          ) : (
            <div className="p-8 text-center">
              <svg className="w-8 h-8 text-gray-700 mx-auto mb-2" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z" />
              </svg>
              <p className="text-xs text-gray-500">Click a session to view details</p>
            </div>
          )}
        </div>
      </div>

      <AskVIPFloat defaultPrompt="show latest AI Glass status" />
    </div>
  );
}
