"use client";

/**
 * KnowledgeUploader — modal for uploading documents into the Assistant's
 * knowledge base.
 *
 * Mounts inside the ChatbotInbox header. Boss clicks "📚 Add knowledge",
 * picks (or drags) one or more files (xlsx / pdf / docx / pptx / csv / txt /
 * md / json), and the modal POSTs each to:
 *
 *     POST {apiBase}/assistant/knowledge/upload
 *         multipart: file=<the file>, agentId=<this agent>, uploadedBy=<email?>
 *
 * The backend parses → embeds → stores chunks in pgvector. Subsequent
 * Assistant questions auto-retrieve from this knowledge base via the RAG-
 * first shim in assistant_agent._run_agent_impl. No further wiring needed.
 *
 * Lifecycle visible to the user:
 *   - "Pending" → file selected, not yet uploaded
 *   - "Uploading…" → request in flight (progress %)
 *   - "Indexed (N chunks)" → done; the Assistant can already use it
 *   - "Failed: …" → error message surfaced inline
 *
 * Also lists previously-uploaded files (GET /assistant/knowledge/files)
 * with a delete (×) per row.
 */

import { useEffect, useRef, useState } from "react";

interface Props {
  /** Base URL of the orchestrator (e.g. https://vip-orchestrator.onrender.com) */
  apiBase: string;
  /** Which agent owns this knowledge — "vip" / "realty" / "asset" / ... */
  agentId: string;
  /** Optional uploader id, e.g. boss email */
  uploadedBy?: string;
  /** Called when modal closes */
  onClose: () => void;
}

interface KnownFile {
  id: string;
  filename: string;
  size_bytes: number | null;
  chunk_count: number;
  status: "pending" | "indexed" | "error";
  error_msg: string | null;
  uploaded_at: string | null;
  uploaded_by: string | null;
}

interface PendingUpload {
  id: string;
  file: File;
  state: "pending" | "uploading" | "done" | "error";
  message?: string;
  chunkCount?: number;
}

const ACCEPT = ".xlsx,.xls,.csv,.pdf,.docx,.pptx,.txt,.md,.json";

function fmtBytes(n: number | null): string {
  if (n == null) return "—";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

export function KnowledgeUploader({ apiBase, agentId, uploadedBy, onClose }: Props) {
  const base = apiBase.replace(/\/$/, "");
  const [files, setFiles] = useState<KnownFile[]>([]);
  const [loading, setLoading] = useState(true);
  const [pending, setPending] = useState<PendingUpload[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [isDragging, setIsDragging] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Lock body scroll while open
  useEffect(() => {
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => { document.body.style.overflow = prev; };
  }, []);

  async function loadList() {
    setLoading(true);
    try {
      const res = await fetch(`${base}/assistant/knowledge/files?agentId=${encodeURIComponent(agentId)}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setFiles(data.files || []);
    } catch (e: any) {
      setError(`Couldn't load file list: ${e.message || e}`);
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => { loadList(); /* eslint-disable-next-line */ }, [agentId]);

  function pickFiles(filesArr: File[]) {
    const next: PendingUpload[] = filesArr.map(f => ({
      id: `${f.name}-${f.size}-${Math.random().toString(36).slice(2, 7)}`,
      file: f,
      state: "pending",
    }));
    setPending(prev => [...prev, ...next]);
    // Kick off uploads in parallel (max 3 at a time)
    next.forEach(p => { void uploadOne(p); });
  }

  async function uploadOne(p: PendingUpload) {
    setPending(prev => prev.map(x => x.id === p.id ? { ...x, state: "uploading" } : x));
    try {
      const fd = new FormData();
      fd.append("file", p.file, p.file.name);
      fd.append("agentId", agentId);
      if (uploadedBy) fd.append("uploadedBy", uploadedBy);
      const res = await fetch(`${base}/assistant/knowledge/upload`, {
        method: "POST",
        body: fd,
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.ok) {
        throw new Error(data?.detail || data?.error || `HTTP ${res.status}`);
      }
      setPending(prev => prev.map(x => x.id === p.id
        ? { ...x, state: "done", chunkCount: data.chunk_count }
        : x));
      // refresh canonical list
      void loadList();
    } catch (e: any) {
      setPending(prev => prev.map(x => x.id === p.id
        ? { ...x, state: "error", message: e.message || String(e) }
        : x));
    }
  }

  async function deleteFile(f: KnownFile) {
    if (!window.confirm(`Delete "${f.filename}" from the knowledge base?\n\nThe Assistant will no longer be able to answer questions from this file.`)) {
      return;
    }
    try {
      const res = await fetch(
        `${base}/assistant/knowledge/files/${f.id}?agentId=${encodeURIComponent(agentId)}`,
        { method: "DELETE" },
      );
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setFiles(prev => prev.filter(x => x.id !== f.id));
    } catch (e: any) {
      setError(`Delete failed: ${e.message || e}`);
    }
  }

  return (
    <div className="fixed inset-0 z-[80] bg-black/50 flex items-center justify-center p-3 md:p-6">
      <div className="w-full max-w-3xl bg-white rounded-2xl shadow-2xl flex flex-col max-h-[90vh] overflow-hidden">
        {/* Header */}
        <div className="px-5 py-4 border-b border-gray-200 flex items-center justify-between">
          <div>
            <h2 className="text-[16px] font-semibold text-gray-900 flex items-center gap-2">
              📚 Knowledge Base — <span className="text-blue-600">{agentId}</span>
            </h2>
            <p className="text-[12px] text-gray-500 mt-0.5">
              Upload files the Assistant should learn from. Supports xlsx, pdf, docx, pptx, csv, txt, md, json.
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="text-gray-500 hover:bg-gray-100 rounded-lg w-9 h-9 flex items-center justify-center text-[20px]"
            aria-label="Close"
          >×</button>
        </div>

        {/* Drop zone */}
        <div
          onDragOver={e => { e.preventDefault(); setIsDragging(true); }}
          onDragLeave={e => { e.preventDefault(); setIsDragging(false); }}
          onDrop={e => {
            e.preventDefault();
            setIsDragging(false);
            const arr = Array.from(e.dataTransfer.files || []);
            if (arr.length > 0) pickFiles(arr);
          }}
          className={`m-4 md:m-5 rounded-xl border-2 border-dashed transition-colors p-6 text-center ${
            isDragging ? "border-blue-500 bg-blue-50" : "border-gray-300 bg-gray-50"
          }`}
        >
          <div className="text-[34px] mb-2">📁</div>
          <div className="text-[14px] text-gray-700">
            Drag files here, or{" "}
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              className="text-blue-600 underline hover:text-blue-700 font-medium"
            >
              browse
            </button>
          </div>
          <div className="text-[11px] text-gray-400 mt-1">
            Multiple files OK · 50MB each max
          </div>
          <input
            ref={fileInputRef}
            type="file"
            multiple
            accept={ACCEPT}
            className="hidden"
            onChange={e => {
              const arr = Array.from(e.target.files || []);
              if (arr.length > 0) pickFiles(arr);
              if (e.target) e.target.value = "";
            }}
          />
        </div>

        {/* Active uploads */}
        {pending.length > 0 && (
          <div className="px-5 pb-3 space-y-2">
            <div className="text-[11px] font-semibold text-gray-500 uppercase tracking-wide">
              Uploads
            </div>
            {pending.map(p => (
              <div key={p.id} className="flex items-center gap-3 rounded-lg border border-gray-200 bg-white px-3 py-2">
                <div className="text-[18px]">
                  {p.state === "done"     ? "✅" :
                   p.state === "error"    ? "⚠️" :
                   p.state === "uploading"? "⏳" :
                                            "📄"}
                </div>
                <div className="flex-1 min-w-0">
                  <div className="text-[13px] font-medium text-gray-800 truncate">{p.file.name}</div>
                  <div className="text-[11px] text-gray-500">
                    {fmtBytes(p.file.size)}
                    {p.state === "uploading" && " · embedding…"}
                    {p.state === "done"      && ` · indexed ${p.chunkCount} chunk${p.chunkCount === 1 ? "" : "s"}`}
                    {p.state === "error"     && ` · ${p.message}`}
                  </div>
                </div>
                {p.state === "uploading" && (
                  <div className="w-16 h-1 bg-gray-200 rounded-full overflow-hidden">
                    <div className="h-full bg-blue-500 animate-pulse" />
                  </div>
                )}
              </div>
            ))}
          </div>
        )}

        {/* Existing files */}
        <div className="flex-1 overflow-y-auto px-5 pb-5 min-h-[80px]">
          <div className="text-[11px] font-semibold text-gray-500 uppercase tracking-wide mb-2 flex items-center justify-between">
            <span>Indexed files {files.length > 0 && <span className="text-gray-400">({files.length})</span>}</span>
            <button onClick={loadList} className="text-gray-400 hover:text-gray-600 text-[14px]" title="Refresh">⟳</button>
          </div>
          {loading ? (
            <div className="text-[12px] text-gray-400 py-4 text-center">Loading…</div>
          ) : files.length === 0 ? (
            <div className="text-[12px] text-gray-400 py-4 text-center">
              No files yet — upload something above to teach the Assistant.
            </div>
          ) : (
            <div className="space-y-1.5">
              {files.map(f => (
                <div key={f.id} className="flex items-center gap-3 rounded-lg border border-gray-200 bg-white px-3 py-2">
                  <div className="text-[18px]">
                    {f.status === "indexed" ? "📘" :
                     f.status === "error"   ? "⚠️" : "⏳"}
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="text-[13px] font-medium text-gray-800 truncate">{f.filename}</div>
                    <div className="text-[11px] text-gray-500">
                      {fmtBytes(f.size_bytes)} · {f.chunk_count} chunk{f.chunk_count === 1 ? "" : "s"}
                      {f.status === "error" && f.error_msg ? ` · ${f.error_msg}` : ""}
                      {f.uploaded_by ? ` · by ${f.uploaded_by}` : ""}
                    </div>
                  </div>
                  <button
                    onClick={() => deleteFile(f)}
                    className="text-gray-400 hover:text-red-500 px-2 py-1 text-[14px]"
                    title="Delete from knowledge base"
                  >×</button>
                </div>
              ))}
            </div>
          )}
          {error && (
            <div className="mt-3 text-[12px] text-red-600 bg-red-50 border border-red-200 rounded-lg px-3 py-2">
              {error}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="px-5 py-3 border-t border-gray-200 flex items-center justify-between text-[11px] text-gray-500">
          <span>
            The Assistant searches this knowledge base BEFORE answering — so uploads take effect immediately.
          </span>
          <button
            onClick={onClose}
            className="px-3 py-1.5 rounded-lg bg-gray-900 text-white text-[12px] font-medium hover:bg-gray-800"
          >
            Done
          </button>
        </div>
      </div>
    </div>
  );
}
