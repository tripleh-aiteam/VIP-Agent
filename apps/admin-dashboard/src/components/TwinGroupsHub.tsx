"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { API, api, apiPost } from "./api";

interface Group {
  id: string;
  name: string;
  description?: string | null;
  avatar_color?: string | null;
  member_count: number;
}

interface Member {
  member_id: string;
  user_id: string | null;
  user_name: string | null;
  user_email: string | null;
  twin_id: string | null;
  twin_name: string | null;
  twin_status?: string | null;
  role: string;
}

interface GroupDetail extends Group {
  members: Member[];
}

interface GroupMessage {
  id: string;
  sender_type: string;
  sender_label: string;
  content: string;
  meta: any;
  created_at: string | null;
}

interface WorkerOption {
  id: string;
  name: string;
  email: string;
  twin_id: string | null;
  twin_name: string | null;
}

/**
 * Boss-facing groups hub. Replaces the old standalone Meeting button flow
 * with a familiar chat UX: pick a group, type "let's meet in 10 minutes",
 * meeting is scheduled, twins auto-join at the right time.
 */
export default function TwinGroupsHub() {
  const [groups, setGroups] = useState<Group[]>([]);
  const [activeGroupId, setActiveGroupId] = useState<string | null>(null);
  const [activeGroup, setActiveGroup] = useState<GroupDetail | null>(null);
  const [messages, setMessages] = useState<GroupMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [twinsOnly, setTwinsOnly] = useState(false);  // v4-F off-day mode
  const [showCreate, setShowCreate] = useState(false);
  const [showAddMember, setShowAddMember] = useState(false);
  const [error, setError] = useState("");
  const router = useRouter();
  const scrollRef = useRef<HTMLDivElement>(null);
  const pollRef = useRef<NodeJS.Timeout | null>(null);

  // ----- load groups + active group thread -----

  useEffect(() => { void loadGroups(); }, []);
  useEffect(() => {
    if (!activeGroupId) return;
    void loadActiveGroup();
    pollRef.current = setInterval(loadActiveGroup, 4000);
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, [activeGroupId]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages]);

  function diagnose(e: any, op: string, path: string) {
    const msg = e?.message || "request failed";
    if (msg === "Not Found") {
      return (
        `Backend doesn't expose ${op} ${path}. ` +
        `Talking to API: ${API}. ` +
        `If that's a remote URL, your deployed backend doesn't have v3 routes yet — ` +
        `restart your local orchestrator-api on :8000 and set ` +
        `NEXT_PUBLIC_API_BASE_URL=http://localhost:8000 in admin-dashboard/.env.local, ` +
        `or deploy the updated backend.`
      );
    }
    return `${op} ${path} failed: ${msg} (API: ${API})`;
  }

  async function loadGroups() {
    try {
      const r = await fetch(`${API}/groups`);
      if (r.status === 404) {
        // Backend doesn't yet expose /groups — leave list empty so user
        // can still try + New, the create call surfaces the real diagnostic
        setGroups([]);
        setError(diagnose({ message: "Not Found" }, "GET", "/groups"));
        return;
      }
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setGroups(await r.json());
    } catch (e: any) { setError(diagnose(e, "GET", "/groups")); }
  }

  async function loadActiveGroup() {
    if (!activeGroupId) return;
    try {
      const [g, msgs] = await Promise.all([
        api<GroupDetail>(`/groups/${activeGroupId}`),
        api<GroupMessage[]>(`/groups/${activeGroupId}/messages`),
      ]);
      setActiveGroup(g);
      setMessages(msgs);
    } catch {/* silent on poll */}
  }

  async function handleCreateGroup(name: string, description: string) {
    try {
      const g: any = await apiPost(`/groups`, { name, description });
      setShowCreate(false);
      setError("");
      await loadGroups();
      setActiveGroupId(g.id);
    } catch (e: any) { setError(diagnose(e, "POST", "/groups")); }
  }

  async function handleAddMember(userId: string) {
    if (!activeGroupId) return;
    try {
      await apiPost(`/groups/${activeGroupId}/members`, { user_id: userId, role: "member" });
      await loadActiveGroup();
    } catch (e: any) { setError(e.message); }
  }

  async function handleRemoveMember(userId: string) {
    if (!activeGroupId) return;
    try {
      await fetch(`${API}/groups/${activeGroupId}/members/${userId}`, { method: "DELETE" });
      await loadActiveGroup();
    } catch {/* ignore */}
  }

  async function handleSend() {
    if (!activeGroupId || !draft.trim()) return;
    setBusy(true);
    setError("");
    const content = draft;
    setDraft("");
    try {
      const r: any = await apiPost(`/groups/${activeGroupId}/messages`, {
        content, sender_type: "boss", twins_only: twinsOnly,
      });
      await loadActiveGroup();
      if (r.schedule_result?.ok && r.schedule_result.meeting_room_url) {
        // Open the meeting room in a new tab so boss can step in when ready
        window.open(r.schedule_result.meeting_room_url, "_blank");
      }
    } catch (e: any) {
      setError(e.message);
      setDraft(content);
    } finally {
      setBusy(false);
    }
  }

  // ----- render -----

  return (
    <div className="flex gap-0 h-[calc(100vh-180px)] min-h-[520px] border border-gray-200 rounded-xl overflow-hidden bg-white">
      {/* Left: groups list */}
      <aside className="w-72 border-r border-gray-200 flex flex-col">
        <div className="p-3 border-b border-gray-100 flex justify-between items-center">
          <h2 className="font-semibold text-sm">Groups</h2>
          <button
            onClick={() => setShowCreate(true)}
            className="text-xs bg-indigo-600 text-white px-2 py-1 rounded"
          >
            + New
          </button>
        </div>
        <div className="flex-1 overflow-y-auto">
          {groups.length === 0 && (
            <p className="p-4 text-xs text-gray-400">No groups yet — click + New.</p>
          )}
          {groups.map(g => (
            <button
              key={g.id}
              onClick={() => setActiveGroupId(g.id)}
              className={`w-full text-left px-3 py-3 border-b border-gray-100 hover:bg-gray-50 ${activeGroupId === g.id ? "bg-indigo-50" : ""}`}
            >
              <div className="text-sm font-medium">{g.name}</div>
              <div className="text-xs text-gray-500">{g.member_count} member(s)</div>
            </button>
          ))}
        </div>
      </aside>

      {/* Right: group thread */}
      <main className="flex-1 flex flex-col">
        {!activeGroup ? (
          <div className="flex-1 flex items-center justify-center text-gray-400 text-sm">
            Pick a group on the left, or create one.
          </div>
        ) : (
          <>
            {/* Header */}
            <header className="p-3 border-b border-gray-100 flex justify-between items-center">
              <div>
                <div className="font-semibold">{activeGroup.name}</div>
                <div className="text-xs text-gray-500">
                  {activeGroup.members.length} member(s):{" "}
                  {activeGroup.members.map(m => m.user_name || m.twin_name).filter(Boolean).join(", ") || "—"}
                </div>
              </div>
              <button
                onClick={() => setShowAddMember(true)}
                className="text-xs bg-emerald-600 text-white px-3 py-1.5 rounded"
              >
                + Add member
              </button>
            </header>

            {/* Messages */}
            <div ref={scrollRef} className="flex-1 overflow-y-auto p-4 bg-gray-50 space-y-2">
              {messages.length === 0 && (
                <p className="text-xs text-gray-400">
                  Type a message. To schedule a meeting say <em>“let’s meet in 10 minutes”</em>{" "}
                  or <em>“회의하자 오후 3시에”</em> — twins will auto-join at that time.
                </p>
              )}
              {messages.map(m => (
                <div
                  key={m.id}
                  className={`max-w-[80%] p-2 rounded ${
                    m.sender_type === "boss"
                      ? "ml-auto bg-indigo-100 text-indigo-900"
                      : m.sender_type === "system"
                      ? "mx-auto bg-amber-50 text-amber-900 border border-amber-200 text-center text-xs"
                      : "bg-white border border-gray-200"
                  }`}
                >
                  {m.sender_type !== "system" && (
                    <div className="text-[10px] font-semibold mb-0.5 opacity-70">{m.sender_label}</div>
                  )}
                  <div className="text-sm whitespace-pre-wrap">{m.content}</div>
                  {m.meta?.meeting_room_url && (
                    <a
                      href={m.meta.meeting_room_url}
                      target="_blank"
                      className="text-indigo-700 underline text-xs mt-1 inline-block"
                      rel="noreferrer"
                    >
                      Open meeting room →
                    </a>
                  )}
                </div>
              ))}
            </div>

            {/* Smart input + twins-only toggle (off-day mode) */}
            <div className="p-3 border-t border-gray-100 space-y-2">
              <label className="flex items-center gap-2 text-xs text-gray-600 cursor-pointer">
                <input
                  type="checkbox"
                  checked={twinsOnly}
                  onChange={e => setTwinsOnly(e.target.checked)}
                  className="rounded"
                />
                <span>
                  <strong>Twins-only meeting</strong> (off-day mode) — only twins join, no workers required.
                  Twins act with full proxy authority.
                </span>
              </label>
              <div className="flex gap-2">
                <input
                  value={draft}
                  onChange={e => setDraft(e.target.value)}
                  onKeyDown={e => e.key === "Enter" && !e.shiftKey && handleSend()}
                  placeholder="Lets meet in 10 minutes, or 회의하자 오후 3시에 — Enter to send"
                  className="flex-1 border rounded px-3 py-2 text-sm"
                  disabled={busy}
                />
                <button
                  onClick={handleSend}
                  disabled={busy || !draft.trim()}
                  className="bg-indigo-600 text-white px-4 py-2 rounded text-sm disabled:opacity-50"
                >
                  Send
                </button>
              </div>
            </div>
          </>
        )}
        {error && (
          <div className="p-2 text-xs text-red-600 bg-red-50 border-t border-red-200">{error}</div>
        )}
      </main>

      {showCreate && (
        <CreateGroupModal
          onCancel={() => setShowCreate(false)}
          onSubmit={(name, desc) => handleCreateGroup(name, desc)}
        />
      )}
      {showAddMember && activeGroup && (
        <AddMemberModal
          existing={activeGroup.members}
          onCancel={() => setShowAddMember(false)}
          onAdd={async (uid) => {
            await handleAddMember(uid);
            setShowAddMember(false);
          }}
          onRemove={handleRemoveMember}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------

function CreateGroupModal({
  onCancel, onSubmit,
}: {
  onCancel: () => void;
  onSubmit: (name: string, desc: string) => void;
}) {
  const [name, setName] = useState("");
  const [desc, setDesc] = useState("");
  return (
    <div className="fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-4" onClick={onCancel}>
      <div className="bg-white rounded-xl w-full max-w-md p-5" onClick={e => e.stopPropagation()}>
        <h3 className="text-lg font-semibold mb-3">New group</h3>
        <input
          autoFocus
          value={name}
          onChange={e => setName(e.target.value)}
          placeholder="Group name (e.g. AI Team)"
          className="w-full border rounded px-3 py-2 text-sm mb-2"
        />
        <input
          value={desc}
          onChange={e => setDesc(e.target.value)}
          placeholder="Description (optional)"
          className="w-full border rounded px-3 py-2 text-sm mb-3"
        />
        <div className="flex justify-end gap-2">
          <button onClick={onCancel} className="px-3 py-1.5 text-sm rounded bg-gray-100">Cancel</button>
          <button
            onClick={() => name.trim() && onSubmit(name.trim(), desc.trim())}
            disabled={!name.trim()}
            className="px-3 py-1.5 text-sm rounded bg-indigo-600 text-white disabled:opacity-50"
          >
            Create
          </button>
        </div>
      </div>
    </div>
  );
}

function AddMemberModal({
  existing, onCancel, onAdd, onRemove,
}: {
  existing: Member[];
  onCancel: () => void;
  onAdd: (userId: string) => Promise<void>;
  onRemove: (userId: string) => void;
}) {
  const [workers, setWorkers] = useState<WorkerOption[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState("");

  useEffect(() => {
    (async () => {
      try {
        const r = await fetch(`${API}/users`);
        const users: any[] = await r.json();
        // include any user that has a linked twin OR role=worker
        const cooked = users
          .filter(u => u && u.id)
          .map(u => ({
            id: u.id,
            name: u.name || "—",
            email: u.email,
            twin_id: u.twin_id || null,
            twin_name: u.twin_name || null,
          }));
        setWorkers(cooked);
      } catch {/* ignore */} finally { setLoading(false); }
    })();
  }, []);

  const existingIds = new Set(existing.map(m => m.user_id));
  const filtered = workers.filter(w =>
    !filter ||
    (w.name + " " + (w.email || "") + " " + (w.twin_name || "")).toLowerCase().includes(filter.toLowerCase()),
  );

  return (
    <div className="fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-4" onClick={onCancel}>
      <div className="bg-white rounded-xl w-full max-w-xl p-5 max-h-[80vh] overflow-y-auto" onClick={e => e.stopPropagation()}>
        <h3 className="text-lg font-semibold mb-3">Add members (workers + their twins)</h3>
        <input
          value={filter}
          onChange={e => setFilter(e.target.value)}
          placeholder="Search by name, email, twin"
          className="w-full border rounded px-3 py-2 text-sm mb-3"
        />
        {loading ? <p className="text-sm text-gray-400">Loading…</p> : (
          <ul className="space-y-1">
            {filtered.map(w => {
              const isIn = existingIds.has(w.id);
              return (
                <li
                  key={w.id}
                  className={`flex justify-between items-center border rounded p-2 ${isIn ? "bg-emerald-50 border-emerald-200" : "bg-white"}`}
                >
                  <div>
                    <div className="text-sm font-medium">{w.name}</div>
                    <div className="text-xs text-gray-500">
                      {w.email} {w.twin_name && <span>· twin: {w.twin_name}</span>}
                    </div>
                  </div>
                  {isIn ? (
                    <button
                      onClick={() => onRemove(w.id)}
                      className="text-xs text-rose-700 hover:underline"
                    >
                      Remove
                    </button>
                  ) : (
                    <button
                      onClick={() => onAdd(w.id)}
                      className="text-xs bg-indigo-600 text-white px-2 py-1 rounded"
                    >
                      Add
                    </button>
                  )}
                </li>
              );
            })}
          </ul>
        )}
        <div className="mt-4 text-right">
          <button onClick={onCancel} className="px-3 py-1.5 text-sm rounded bg-gray-100">Done</button>
        </div>
      </div>
    </div>
  );
}
