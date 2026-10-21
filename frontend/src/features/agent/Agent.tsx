import { useEffect, useMemo, useRef, useState } from "react";
import { api, fmtTime, type Conversation, type Violation } from "../../api";
import { EvidenceModal } from "../../components";
import { useMe } from "../../session";

type Msg = {
  role: "user" | "assistant";
  content: string;
  evidence?: Violation[];
  refused?: boolean;
  tools?: string[];
};

const shortEvidenceLabel = (v: Violation) => {
  if (v.ringkasan_agent) return v.ringkasan_agent;
  const items = v.apd_kurang.length ? v.apd_kurang.join(", ") : "APD tidak lengkap";
  return `Tanpa ${items}`;
};

const SUGGESTIONS: { q: string; hint: string }[] = [
  { q: "Berapa orang yang tidak memakai APD lengkap hari ini?", hint: "pelanggaran per pekerja" },
  { q: "Jam berapa pelanggaran paling sering terjadi minggu ini?", hint: "pola per jam" },
  { q: "Tunjukkan pekerja tanpa sarung tangan kemarin", hint: "evidence per APD" },
  { q: "Rangkum kepatuhan 7 hari terakhir", hint: "ringkasan periode" },
];

function getThinkingSteps(query: string): string[] {
  const q = (query || "").toLowerCase();
  if (
    q.includes("regulasi") ||
    q.includes("permenaker") ||
    q.includes("undang-undang") ||
    q.includes("uu") ||
    q.includes("pasal") ||
    q.includes("sop") ||
    q.includes("hukum") ||
    q.includes("loading dock") ||
    q.includes("gratis") ||
    q.includes("cuma-cuma") ||
    q.includes("standar")
  ) {
    return [
      "Menganalisis rujukan hukum & standar keselamatan kerja",
      "Pencarian semantik ke korpus Permenaker No. 8/2010 & UU No. 1/1970",
      "Mengekstrak nomor pasal & merumuskan dasar hukum resmi",
    ];
  }
  if (
    q.includes("decision") ||
    q.includes("saran") ||
    q.includes("rekomendasi") ||
    q.includes("solusi") ||
    q.includes("kurang") ||
    q.includes("berkurang") ||
    q.includes("tindakan") ||
    q.includes("langkah")
  ) {
    return [
      "Mengambil statistik kepatuhan K3 & durasi paparan pekerja",
      "Mengidentifikasi jenis APD paling kritis & jam rawan pelanggaran",
      "Merumuskan rekomendasi tindakan operasional & rencana intervensi",
    ];
  }
  if (
    q.includes("intervensi") ||
    q.includes("briefing") ||
    q.includes("efek") ||
    q.includes("sebelum") ||
    q.includes("sesudah")
  ) {
    return [
      "Mengambil riwayat pencatatan intervensi K3 di sistem",
      "Membandingkan data kepatuhan sebelum vs sesudah tindakan",
      "Menghitung uji signifikansi statistik (two-proportion z-test)",
    ];
  }
  if (
    q.includes("kamera") ||
    q.includes("cam") ||
    q.includes("zona") ||
    q.includes("area") ||
    q.includes("berapa") ||
    q.includes("jumlah") ||
    q.includes("hitung") ||
    q.includes("bukti") ||
    q.includes("evidence")
  ) {
    return [
      "Menentukan filter rentang waktu & parameter kamera CCTV",
      "Mengelompokkan identitas tracker & klaster visual pekerja",
      "Mengambil sampel foto bukti beranotasi dengan verifikasi hash SHA-256",
    ];
  }
  return [
    "Memahami konteks pertanyaan keselamatan kerja",
    "Membaca database sistem pemantauan APD Vision",
    "Menyusun dan memvalidasi ringkasan jawaban",
  ];
}

function PinIcon({ className = "" }: { className?: string }) {
  return (
    <svg className={className} width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M9 4h6l-1 6 4 3H6l4-3z" />
      <path d="M12 13v7" />
    </svg>
  );
}

function RenameIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M4 20h4l11-11a2.8 2.8 0 0 0-4-4L4 16v4z" />
      <path d="m13.5 6.5 4 4" />
    </svg>
  );
}

function DeleteIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M4 7h16" />
      <path d="M9 7V4h6v3" />
      <path d="m7 7 1 13h8l1-13" />
      <path d="M10 11v5M14 11v5" />
    </svg>
  );
}

const TOOL_LABELS: Record<string, string> = {
  count_violations: "Menghitung data pelanggaran APD",
  get_evidence: "Mengambil bukti foto CCTV",
  compliance_summary: "Analisis statistik kepatuhan",
  intervention_effect: "Evaluasi efektivitas intervensi",
  search_regulation: "Pencarian regulasi & standar K3",
};

function renderInlineText(text: string): React.ReactNode[] {
  const parts: React.ReactNode[] = [];
  const regex = /(\*\*.*?\*\*|\*.*?\*|`.*?`)/g;
  let lastIdx = 0;
  let match;
  let key = 0;

  while ((match = regex.exec(text)) !== null) {
    if (match.index > lastIdx) {
      parts.push(text.substring(lastIdx, match.index));
    }
    const token = match[1];
    if (token.startsWith("**") && token.endsWith("**")) {
      parts.push(<strong key={key++}>{token.slice(2, -2)}</strong>);
    } else if (token.startsWith("*") && token.endsWith("*")) {
      parts.push(<em key={key++}>{token.slice(1, -1)}</em>);
    } else if (token.startsWith("`") && token.endsWith("`")) {
      parts.push(<code key={key++} className="agent-code-inline">{token.slice(1, -1)}</code>);
    }
    lastIdx = regex.lastIndex;
  }
  if (lastIdx < text.length) {
    parts.push(text.substring(lastIdx));
  }
  return parts.length ? parts : [text];
}

function FormattedContent({ content }: { content: string }) {
  if (!content) return null;
  const lines = content.split("\n");
  const elements: React.ReactNode[] = [];
  let currentList: { type: "ul" | "ol"; items: string[] } | null = null;
  let elKey = 0;

  const flushList = () => {
    if (!currentList) return;
    if (currentList.type === "ul") {
      elements.push(
        <ul key={elKey++} className="agent-answer-list">
          {currentList.items.map((item, idx) => (
            <li key={idx}>{renderInlineText(item)}</li>
          ))}
        </ul>
      );
    } else {
      elements.push(
        <ol key={elKey++} className="agent-answer-list ordered">
          {currentList.items.map((item, idx) => (
            <li key={idx}>{renderInlineText(item)}</li>
          ))}
        </ol>
      );
    }
    currentList = null;
  };

  for (let i = 0; i < lines.length; i++) {
    const rawLine = lines[i];
    const trimmed = rawLine.trim();

    if (!trimmed) {
      flushList();
      continue;
    }

    const olMatch = trimmed.match(/^(\d+)\.\s+(.*)/);
    if (olMatch) {
      if (!currentList || currentList.type !== "ol") {
        flushList();
        currentList = { type: "ol", items: [] };
      }
      currentList.items.push(olMatch[2]);
      continue;
    }

    const ulMatch = trimmed.match(/^[-*•]\s+(.*)/);
    if (ulMatch) {
      if (!currentList || currentList.type !== "ul") {
        flushList();
        currentList = { type: "ul", items: [] };
      }
      currentList.items.push(ulMatch[1]);
      continue;
    }

    if (currentList && (rawLine.startsWith("  ") || rawLine.startsWith("\t"))) {
      const lastIdx = currentList.items.length - 1;
      if (lastIdx >= 0) {
        currentList.items[lastIdx] += "\n" + trimmed;
        continue;
      }
    }

    flushList();

    const hMatch = trimmed.match(/^(#{1,4})\s+(.*)/);
    if (hMatch) {
      const level = hMatch[1].length;
      if (level <= 2) {
        elements.push(<h3 key={elKey++} className="agent-answer-h3">{renderInlineText(hMatch[2])}</h3>);
      } else {
        elements.push(<h4 key={elKey++} className="agent-answer-h4">{renderInlineText(hMatch[2])}</h4>);
      }
      continue;
    }

    elements.push(
      <p key={elKey++} className="agent-answer-p">
        {renderInlineText(trimmed)}
      </p>
    );
  }

  flushList();
  return <div className="agent-formatted-text">{elements}</div>;
}

function groupTime(dateStr: string): "Hari ini" | "Kemarin" | "7 hari terakhir" | "Lebih lama" {
  const d = new Date(dateStr);
  const now = new Date();
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const startOfYesterday = startOfToday - 86400000;
  const startOf7Days = startOfToday - 6 * 86400000;
  const t = d.getTime();
  if (t >= startOfToday) return "Hari ini";
  if (t >= startOfYesterday) return "Kemarin";
  if (t >= startOf7Days) return "7 hari terakhir";
  return "Lebih lama";
}

export default function Agent() {
  const me = useMe();
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeConvId, setActiveConvId] = useState<string | null>(null);
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [historyLoaded, setHistoryLoaded] = useState(false);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState<Violation | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [copiedIdx, setCopiedIdx] = useState<number | null>(null);
  const [search, setSearch] = useState("");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editingTitle, setEditingTitle] = useState("");
  const [currentPrompt, setCurrentPrompt] = useState("");
  const [thinkingStep, setThinkingStep] = useState(0);

  const thinkingSteps = useMemo(() => getThinkingSteps(currentPrompt), [currentPrompt]);

  useEffect(() => {
    if (!busy) {
      setThinkingStep(0);
      return;
    }
    const t1 = window.setTimeout(() => setThinkingStep(1), 1100);
    const t2 = window.setTimeout(() => setThinkingStep(2), 2900);
    return () => {
      window.clearTimeout(t1);
      window.clearTimeout(t2);
    };
  }, [busy]);

  const end = useRef<HTMLDivElement>(null);
  const activeConvIdRef = useRef<string | null>(null);
  const abortCtrlRef = useRef<AbortController | null>(null);

  useEffect(() => {
    end.current?.scrollIntoView({ behavior: "smooth" });
  }, [msgs, busy]);

  const loadConversationMessages = async (id: string) => {
    abortCtrlRef.current?.abort();
    const ctrl = new AbortController();
    abortCtrlRef.current = ctrl;
    activeConvIdRef.current = id;
    setActiveConvId(id);
    setMsgs([]);
    setHistoryLoaded(false);
    try {
      const detail = await api.conversation(id, ctrl.signal);
      if (activeConvIdRef.current !== id) return;
      setMsgs(
        detail.messages.map((m) => ({
          role: m.role,
          content: m.content,
          tools: m.tool_calls?.map((t) => t.name),
          evidence: m.evidence,
          refused: m.refused,
        }))
      );
    } catch (e) {
      if ((e as Error).name === "AbortError") return;
      setMsgs([{ role: "assistant", content: `Gagal memuat percakapan: ${(e as Error).message}` }]);
    } finally {
      if (activeConvIdRef.current === id) setHistoryLoaded(true);
    }
  };

  useEffect(() => {
    api
      .conversations()
      .then((rows) => {
        setConversations(rows);
        if (rows.length > 0) {
          loadConversationMessages(rows[0].id);
        } else {
          setHistoryLoaded(true);
        }
      })
      .catch(() => {
        setConversations([]);
        setHistoryLoaded(true);
      });
  }, []);

  const startNewConversation = () => {
    if (busy) return;
    abortCtrlRef.current?.abort();
    setActiveConvId(null);
    activeConvIdRef.current = null;
    setMsgs([]);
    setInput("");
    setHistoryLoaded(true);
  };

  const selectConversation = (id: string) => {
    if (busy || activeConvId === id) return;
    loadConversationMessages(id);
  };

  const handleTogglePin = async (e: React.MouseEvent, c: Conversation) => {
    e.stopPropagation();
    try {
      const updated = await api.pinConversation(c.id, !c.pinned);
      setConversations((prev) => {
        const next = prev.map((item) => (item.id === c.id ? updated : item));
        return [...next].sort((a, b) => {
          if (a.pinned !== b.pinned) return a.pinned ? -1 : 1;
          return new Date(b.updated).getTime() - new Date(a.updated).getTime();
        });
      });
    } catch (err) {
      alert(`Gagal menyematkan percakapan: ${(err as Error).message}`);
    }
  };

  const startRename = (e: React.MouseEvent, c: Conversation) => {
    e.stopPropagation();
    setEditingId(c.id);
    setEditingTitle(c.title);
  };

  const saveRename = async (id: string) => {
    const trimmed = editingTitle.trim();
    if (!trimmed) {
      setEditingId(null);
      return;
    }
    try {
      const updated = await api.renameConversation(id, trimmed);
      setConversations((prev) => prev.map((item) => (item.id === id ? updated : item)));
    } catch (err) {
      alert(`Gagal mengubah judul: ${(err as Error).message}`);
    } finally {
      setEditingId(null);
    }
  };

  const handleDelete = async (e: React.MouseEvent, id: string) => {
    e.stopPropagation();
    if (!window.confirm("Hapus percakapan ini beserta seluruh pesannya?")) return;
    try {
      await api.deleteConversation(id);
      setConversations((prev) => {
        const next = prev.filter((item) => item.id !== id);
        if (activeConvId === id) {
          if (next.length > 0) {
            loadConversationMessages(next[0].id);
          } else {
            startNewConversation();
          }
        }
        return next;
      });
    } catch (err) {
      alert(`Gagal menghapus percakapan: ${(err as Error).message}`);
    }
  };

  const send = async (text: string) => {
    if (!text.trim() || busy) return;
    const prompt = text.trim();
    setCurrentPrompt(prompt);
    setThinkingStep(0);
    setInput("");
    setBusy(true);

    let targetId = activeConvId;
    if (!targetId) {
      try {
        const created = await api.createConversation();
        targetId = created.id;
        setActiveConvId(targetId);
        activeConvIdRef.current = targetId;
        setConversations((prev) => [created, ...prev]);
      } catch (e) {
        setMsgs((m) => [...m, { role: "assistant", content: `Gagal membuat percakapan: ${(e as Error).message}` }]);
        setBusy(false);
        return;
      }
    }

    setMsgs((m) => [...m, { role: "user", content: prompt }]);

    try {
      const r = await api.chat(prompt, targetId);
      if (activeConvIdRef.current === targetId) {
        setMsgs((m) => [
          ...m,
          {
            role: "assistant",
            content: r.answer,
            evidence: r.evidence,
            refused: r.ditolak,
            tools: r.tool_calls?.map((t) => t.name),
          },
        ]);
      }
      if (r.conversation_title) {
        setConversations((prev) => {
          const next = prev.map((c) =>
            c.id === targetId ? { ...c, title: r.conversation_title!, updated: new Date().toISOString() } : c
          );
          return [...next].sort((a, b) => {
            if (a.pinned !== b.pinned) return a.pinned ? -1 : 1;
            return new Date(b.updated).getTime() - new Date(a.updated).getTime();
          });
        });
      }
    } catch (e) {
      if (activeConvIdRef.current === targetId) {
        setMsgs((m) => [...m, { role: "assistant", content: `Agent gagal merespons: ${(e as Error).message}` }]);
      }
    } finally {
      setBusy(false);
    }
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send(input);
    }
  };

  const copyAnswer = async (text: string, i: number) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopiedIdx(i);
      window.setTimeout(() => setCopiedIdx((cur) => (cur === i ? null : cur)), 1500);
    } catch {
      /* clipboard tidak tersedia */
    }
  };

  const filteredConversations = useMemo(() => {
    if (!search.trim()) return conversations;
    const q = search.toLowerCase();
    return conversations.filter((c) => c.title.toLowerCase().includes(q));
  }, [conversations, search]);

  const grouped = useMemo(() => {
    const groups: Record<string, Conversation[]> = {
      "Hari ini": [],
      Kemarin: [],
      "7 hari terakhir": [],
      "Lebih lama": [],
    };
    for (const c of filteredConversations) {
      groups[groupTime(c.updated)].push(c);
    }
    return Object.entries(groups).filter(([, items]) => items.length > 0);
  }, [filteredConversations]);

  const activeConv = conversations.find((c) => c.id === activeConvId);
  const accountInitial = (me?.username.trim().slice(0, 1) || "A").toUpperCase();
  const currentTitle = activeConv?.title || (msgs.length ? msgs[0].content : "Percakapan baru");

  return (
    <div className={`agent-shell ${sidebarOpen ? "" : "sidebar-collapsed"}`}>
      <button
        type="button"
        className="agent-sidebar-open"
        aria-label="Buka panel"
        aria-expanded={sidebarOpen}
        onClick={() => setSidebarOpen(true)}
      >
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <rect x="3" y="4" width="18" height="16" rx="3" />
          <path d="M9 4v16" />
          <path d="M13 10l2 2-2 2" />
        </svg>
      </button>
      <button
        type="button"
        className="agent-side-scrim"
        aria-label="Tutup panel"
        onClick={() => setSidebarOpen(false)}
      />
      <aside className="agent-side av-card">
        <div className="agent-side-head">
          <span className="av-label">Agent APD</span>
          <button
            type="button"
            className="av-icon-btn"
            aria-label="Tutup panel"
            aria-expanded={sidebarOpen}
            onClick={() => setSidebarOpen(false)}
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <rect x="3" y="4" width="18" height="16" rx="3" />
              <path d="M9 4v16" />
              <path d="M15 10l-2 2 2 2" />
            </svg>
          </button>
        </div>

        <button type="button" className="av-btn primary agent-new" onClick={startNewConversation} disabled={busy}>
          <span aria-hidden="true">＋</span> Percakapan baru
        </button>

        <label className="agent-search">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
            <circle cx="11" cy="11" r="7" />
            <path d="M20 20l-3.5-3.5" />
          </svg>
          <input
            placeholder="Cari percakapan"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            aria-label="Cari percakapan"
          />
        </label>

        <div className="agent-side-list" aria-label="Riwayat percakapan">
          {grouped.length ? (
            grouped.map(([groupName, items]) => (
              <div key={groupName}>
                <div className="av-label agent-side-group">{groupName}</div>
                {items.map((c) => {
                  const isCurrent = c.id === activeConvId;
                  const isEditing = editingId === c.id;
                  return (
                    <div key={c.id} className={`agent-side-row ${isCurrent ? "on" : ""}`}>
                      {isEditing ? (
                        <input
                          className="agent-rename-input"
                          value={editingTitle}
                          autoFocus
                          maxLength={120}
                          onChange={(e) => setEditingTitle(e.target.value)}
                          onKeyDown={(e) => {
                            if (e.key === "Enter") saveRename(c.id);
                            if (e.key === "Escape") setEditingId(null);
                          }}
                          onBlur={() => saveRename(c.id)}
                        />
                      ) : (
                        <>
                          <button
                            type="button"
                            className="agent-side-item"
                            onClick={() => selectConversation(c.id)}
                            title={c.title}
                          >
                            {c.pinned && (
                              <span className="agent-pinned-marker" aria-label="disematkan">
                                <PinIcon />
                              </span>
                            )}
                            {c.title}
                          </button>
                          <div className="agent-side-acts">
                            <button
                              type="button"
                              className={`agent-side-act ${c.pinned ? "pinned" : ""}`}
                              onClick={(e) => handleTogglePin(e, c)}
                              aria-label={c.pinned ? "Lepas sematan" : "Sematkan"}
                              title={c.pinned ? "Lepas sematan" : "Sematkan"}
                            >
                              <PinIcon />
                            </button>
                            <button
                              type="button"
                              className="agent-side-act"
                              onClick={(e) => startRename(e, c)}
                              aria-label="Ubah judul"
                              title="Ubah judul"
                            >
                              <RenameIcon />
                            </button>
                            <button
                              type="button"
                              className="agent-side-act"
                              onClick={(e) => handleDelete(e, c.id)}
                              aria-label="Hapus percakapan"
                              title="Hapus percakapan"
                            >
                              <DeleteIcon />
                            </button>
                          </div>
                        </>
                      )}
                    </div>
                  );
                })}
              </div>
            ))
          ) : (
            <p className="muted small agent-side-empty">
              {search.trim() ? "Tidak ada percakapan yang cocok." : "Belum ada riwayat percakapan."}
            </p>
          )}
        </div>

        <div className="agent-side-foot">
          <span className="account-avatar" aria-hidden="true">{accountInitial}</span>
          <div className="agent-side-foot-text">
            <b>{me?.username ?? "Akun"}</b>
            <small className="muted">Riwayat tersimpan di server lokal</small>
          </div>
        </div>
      </aside>

      <section className="agent-main av-card">
        <header className="agent-main-head">
          <b className="agent-main-title">{currentTitle}</b>
          <span className="av-tag good"><i /> Ollama lokal · data APD saja</span>
        </header>

        <div className="chat-log agent-log">
          {!historyLoaded && <p className="muted small">Memuat percakapan…</p>}
          {historyLoaded && !msgs.length && (
            <div className="agent-empty">
              <img className="agent-empty-logo" src="/apd-vision/logo.svg" alt="" aria-hidden="true" />
              <h2>Halo {me?.username ?? "di sana"}, mau cek apa hari ini?</h2>
              <p className="muted">
                Tanya soal pelanggaran, evidence, atau tren kepatuhan APD dari kamera di sistem ini. Semua angka diambil lewat tool.
              </p>
              <div className="agent-suggest">
                {SUGGESTIONS.map((s) => (
                  <button key={s.q} type="button" onClick={() => send(s.q)}>
                    <span>{s.q}</span>
                    <small className="muted">{s.hint}</small>
                  </button>
                ))}
              </div>
            </div>
          )}
          {msgs.map((m, i) =>
            m.role === "user" ? (
              <div key={i} className="msg user">
                <div className="bubble">{m.content}</div>
              </div>
            ) : (
              <div key={i} className="msg assistant agent-answer">
                <span className="agent-answer-logo" aria-hidden="true">
                  <img src="/apd-vision/logo.svg" alt="" />
                </span>
                <div className="agent-answer-body">
                  {m.refused && <div className="small warn">Di luar cakupan agent</div>}
                  {!!m.tools?.length && (
                    <div className="agent-tools">
                      {m.tools.map((t, ti) => {
                        const label = TOOL_LABELS[t] || t.replace(/_/g, " ");
                        return (
                          <span key={ti} className="agent-tool-chip">
                            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                              <polyline points="20 6 9 17 4 12" />
                            </svg>
                            <span>{label}</span>
                          </span>
                        );
                      })}
                    </div>
                  )}
                  <div className="agent-answer-text">
                    <FormattedContent content={m.content} />
                  </div>
                  {!!m.evidence?.length && (
                    <div className="ev-strip">
                      {m.evidence.map((v) => (
                        <button key={v.id} className="ev" onClick={() => setOpen(v)}>
                          {v.foto_crop && <img src={v.foto_crop} alt="" />}
                          <span className="small">{fmtTime(v.waktu)}</span>
                          <span className="small muted">
                            {v.kamera}
                            {v.zona ? ` · ${v.zona}` : ""}
                          </span>
                          <span className="small bad">{shortEvidenceLabel(v)}</span>
                        </button>
                      ))}
                    </div>
                  )}
                  <div className="agent-answer-actions">
                    <button
                      type="button"
                      className={`agent-copy-btn ${copiedIdx === i ? "copied" : ""}`}
                      aria-label="Salin jawaban"
                      onClick={() => copyAnswer(m.content, i)}
                      title={copiedIdx === i ? "Tersalin!" : "Salin jawaban"}
                    >
                      {copiedIdx === i ? (
                        <>
                          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                            <polyline points="20 6 9 17 4 12" />
                          </svg>
                          <span>Tersalin</span>
                        </>
                      ) : (
                        <>
                          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                            <rect x="9" y="9" width="13" height="13" rx="2" ry="2" />
                            <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
                          </svg>
                          <span>Salin</span>
                        </>
                      )}
                    </button>
                  </div>
                </div>
              </div>
            )
          )}
          {busy && (
            <div className="msg assistant agent-answer">
              <span className="agent-answer-logo" aria-hidden="true">
                <img src="/apd-vision/logo.svg" alt="" />
              </span>
              <div className="agent-thinking-card" role="status" aria-live="polite">
                <div className="agent-thinking-header">
                  <span className="agent-thinking-pulse" aria-hidden="true" />
                  <span className="agent-thinking-current-label">
                    {thinkingSteps[thinkingStep] || "Memproses query..."}
                  </span>
                </div>
                <div className="agent-thinking-substeps">
                  {thinkingSteps.map((stepText, sIdx) => {
                    const isDone = sIdx < thinkingStep;
                    const isActive = sIdx === thinkingStep;
                    return (
                      <div
                        key={sIdx}
                        className={`agent-thinking-step-row ${isDone ? "step-done" : isActive ? "step-active" : "step-pending"}`}
                      >
                        <span className="agent-step-icon" aria-hidden="true">
                          {isDone ? (
                            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
                              <polyline points="20 6 9 17 4 12" />
                            </svg>
                          ) : isActive ? (
                            <span className="step-spinner-ring" />
                          ) : (
                            <span className="step-dot" />
                          )}
                        </span>
                        <span className="agent-step-text">{stepText}</span>
                      </div>
                    );
                  })}
                </div>
              </div>
            </div>
          )}
          <div ref={end} />
        </div>

        <form
          className="agent-input"
          onSubmit={(e) => {
            e.preventDefault();
            send(input);
          }}
        >
          <div className="agent-input-box">
            <textarea
              value={input}
              maxLength={1000}
              rows={1}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={onKeyDown}
              placeholder="Tanya agent, misalnya: berapa pelanggaran helm kemarin di CAM-02?"
              aria-label="Pertanyaan untuk agent"
            />
            <div className="agent-input-foot">
              <span className="muted small agent-scope">
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                  <rect x="4" y="10" width="16" height="10" rx="2" />
                  <path d="M8 10V7a4 4 0 0 1 8 0v3" />
                </svg>
                Hanya data APD · read-only
              </span>
              <button type="submit" className="agent-send" disabled={busy || !input.trim()} aria-label="Kirim pertanyaan">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                  <path d="M12 19V5M6 11l6-6 6 6" />
                </svg>
              </button>
            </div>
          </div>
          <p className="note small agent-input-note">
            Agent hanya menjawab pertanyaan tentang data APD dari kamera di sistem ini. Angka diambil lewat tool, bukan dikarang model.
          </p>
        </form>
      </section>
      <EvidenceModal v={open} onClose={() => setOpen(null)} />
    </div>
  );
}
