"use client";

/**
 * KnowledgeUploader — compact modal for uploading documents into the
 * Assistant's per-agent knowledge base.
 *
 * Surface:
 *   - one drop zone (drag-and-drop OR browse button)
 *   - vertical list of files (each shows status + delete ×)
 *
 * Sizing: max-w-md (~448px) — small card, centered, no overflow.
 *
 * Backend:
 *   POST   {apiBase}/assistant/knowledge/upload   (multipart: file, agentId)
 *   GET    {apiBase}/assistant/knowledge/files?agentId=...
 *   DELETE {apiBase}/assistant/knowledge/files/{id}?agentId=...
 */

import { useEffect, useRef, useState } from "react";

interface Props {
  apiBase: string;
  agentId: string;
  uploadedBy?: string;
  onClose: () => void;
}

interface KnownFile {
  id: string;
  filename: string;
  size_bytes: number | null;
  chunk_count: number;
  status: "pending" | "indexed" | "error";
  error_msg: string | null;
}

interface PendingUpload {
  id: string;
  file: File;
  state: "uploading" | "done" | "error";
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

  // Esc closes the modal
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  async function loadList() {
    try {
      const res = await fetch(`${base}/assistant/knowledge/files?agentId=${encodeURIComponent(agentId)}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setFiles(data.files || []);
    } catch (e: any) {
      setError(`Couldn't load files: ${e.message || e}`);
    }
  }
  useEffect(() => { loadList(); /* eslint-disable-next-line */ }, [agentId]);

  function pickFiles(filesArr: File[]) {
    const next: PendingUpload[] = filesArr.map(f => ({
      id: `${f.name}-${f.size}-${Math.random().toString(36).slice(2, 7)}`,
      file: f,
      state: "uploading",
    }));
    setPending(prev => [...prev, ...next]);
    next.forEach(p => { void uploadOne(p); });
  }

  async function uploadOne(p: PendingUpload) {
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
      void loadList();
    } catch (e: any) {
      setPending(prev => prev.map(x => x.id === p.id
        ? { ...x, state: "error", message: e.message || String(e) }
        : x));
    }
  }

  async function deleteFile(f: KnownFile) {
    if (!window.confirm(`Delete "${f.filename}"?`)) return;
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
    <div
      className="fixed inset-0 z-[80] bg-black/50 flex items-center justify-center p-3"
      onClick={onClose}
    >
      <div
        className="bg-white rounded-2xl shadow-2xl flex flex-col w-[min(92vw,28rem)] max-h-[85vh] overflow-hidden"
        onClick={e => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
      >
        {/* Header */}
        <div className="px-4 py-3 border-b border-gray-200 flex items-center justify-between gap-2">
          <div className="min-w-0">
            <div className="text-[14px] font-semibold text-gray-900 flex items-center gap-1.5">
              📚 Knowledge files
            </div>
            <div className="text-[11px] text-gray-500 truncate">
              xlsx · pdf · docx · pptx · csv · txt
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="text-gray-500 hover:bg-gray-100 rounded-lg w-8 h-8 flex items-center justify-center text-[18px] shrink-0"
            aria-label="Close"
          >×</button>
        </div>

        {/* Drop zone */}
        <button
          type="button"
          onClick={() => fileInputRef.current?.click()}
          onDragOver={e => { e.preventDefault(); setIsDragging(true); }}
          onDragLeave={e => { e.preventDefault(); setIsDragging(false); }}
          onDrop={e => {
            e.preventDefault();
            setIsDragging(false);
            const arr = Array.from(e.dataTransfer.files || []);
            if (arr.length > 0) pickFiles(arr);
          }}
          className={`m-3 rounded-xl border-2 border-dashed py-6 px-4 text-center transition-colors ${
            isDragging
              ? "border-blue-500 bg-blue-50"
              : "border-gray-300 bg-gray-50 hover:bg-gray-100"
          }`}
        >
          <div className="text-[28px] mb-1">📁</div>
          <div className="text-[13px] text-gray-700">
            Drag files here or <span className="text-blue-600 underline">browse</span>
          </div>
          <div className="text-[11px] text-gray-400 mt-1">
            multiple OK · 50MB each
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
        </button>

        {/* File list — in-flight uploads first, then already-indexed */}
        <div className="flex-1 overflow-y-auto px-3 pb-3 space-y-1.5">
          {pending.map(p => (
            <div key={p.id} className="flex items-center gap-2 rounded-lg border border-gray-200 bg-white px-2.5 py-1.5">
              <div className="text-[16px] shrink-0">
                {p.state === "done"   ? "✅" :
                 p.state === "error"  ? "⚠️" :
                                        "⏳"}
              </div>
              <div className="flex-1 min-w-0">
                <div className="text-[12px] font-medium text-gray-800 truncate">{p.file.name}</div>
                <div className="text-[10px] text-gray-500 truncate">
                  {p.state === "uploading" && "uploading…"}
                  {p.state === "done"      && `${p.chunkCount} chunks`}
                  {p.state === "error"     && p.message}
                </div>
              </div>
            </div>
          ))}
          {files.map(f => (
            <div key={f.id} className="flex items-center gap-2 rounded-lg border border-gray-200 bg-white px-2.5 py-1.5">
              <div className="text-[16px] shrink-0">
                {f.status === "indexed" ? "📘" :
                 f.status === "error"   ? "⚠️" :
                                          "⏳"}
              </div>
              <div className="flex-1 min-w-0">
                <div className="text-[12px] font-medium text-gray-800 truncate">{f.filename}</div>
                <div className="text-[10px] text-gray-500 truncate">
                  {fmtBytes(f.size_bytes)} · {f.chunk_count} chunks
                  {f.status === "error" && f.error_msg ? ` · ${f.error_msg}` : ""}
                </div>
              </div>
              <button
                onClick={() => deleteFile(f)}
                className="text-gray-400 hover:text-red-500 w-7 h-7 flex items-center justify-center text-[14px] shrink-0"
                aria-label={`Delete ${f.filename}`}
                title="Delete"
              >×</button>
            </div>
          ))}
          {pending.length === 0 && files.length === 0 && (
            <div className="text-[12px] text-gray-400 py-4 text-center">
              No files yet.
            </div>
          )}
          {error && (
            <div className="text-[11px] text-red-600 bg-red-50 border border-red-200 rounded-lg px-2.5 py-1.5">
              {error}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
