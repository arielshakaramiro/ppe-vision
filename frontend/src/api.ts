export type Checklist = Record<"helmet" | "vest" | "gloves" | "shoes", string>;

export type DetectionBox = { x: number; y: number; w: number; h: number };
export type DetectionItem = {
  key: "helmet" | "vest" | "gloves" | "shoes";
  status: "present" | "missing" | "unknown" | "not_required";
  boxes: { box: DetectionBox; confidence: number }[];
};
export type EvidenceDetection = {
  frame: { width: number; height: number };
  worker: { box: DetectionBox; confidence: number };
  items: DetectionItem[];
};

export type Violation = {
  foto_clean?: string | null;
  deteksi?: EvidenceDetection | null;
  id: number;
  waktu: string;
  selesai: string | null;
  durasi_detik: number | null;
  kamera: string;
  zona: string;
  pekerja: string;
  worker_id?: number;
  pekerja_unik?: string;
  jenis: "violation" | "uncertain";
  sumber?: "live" | "rekaman" | "simulasi";
  apd_kurang: string[];
  apd_kurang_kode?: string[];
  apd_tidak_pasti: string[];
  apd_tidak_pasti_kode?: string[];
  apd_dibenarkan?: string[];
  apd_dibenarkan_kode?: string[];
  ringkasan_agent?: string;
  foto: string | null;
  foto_crop: string | null;
  sha256: string;
  klip: string | null;
  klip_sha256: string | null;
  versi_model: string | null;
  review: "pending" | "confirmed" | "false_positive";
  jumlah_event?: number;
  event_bukti?: Violation[];
  waktu_mulai?: string;
  waktu_selesai?: string;
  indikasi_fragmentasi_tracking?: boolean;
};

export type LivePerson = {
  track_id: number; worker_id?: number; pekerja?: string;
  status: "compliant" | "violation" | "uncertain"; zone: string;
  checklist: Checklist; since: string;
  box?: DetectionBox | null;
  confidence?: Partial<Record<keyof Checklist, number>>;
};

export type Live = {
  running: boolean;
  error: string | null;
  fps: number;
  frame?: { width: number; height: number };
  people: LivePerson[];
  kpi: { pekerja_terpantau: number; pelanggaran_aktif: number; safety_score: number | null };
  recent: Violation[];
};

export type Stats = {
  pekerja_terpantau: number;
  pekerja_patuh: number;
  pekerja_melanggar: number;
  pekerja_tidak_pasti: number;
  compliance_rate: number | null;
  event_pelanggaran: number;
  event_tidak_pasti: number;
  menunggu_review: number;
  kepatuhan_per_apd: { item: string; label: string; patuh: number; total: number; persen: number | null }[];
  per_jam: { jam: number; jumlah: number; event?: number }[];
  per_hari: { tanggal: string; jumlah: number; event?: number }[];
  per_kamera: { kamera: string; jumlah: number; event?: number }[];
  per_zona: { zona: string; jumlah: number; event?: number }[];
  jam_rawan: number[];
  per_shift: { shift: string; pekerja: number; compliance_rate: number | null; pekerja_melanggar?: number; pelanggaran: number; menit_paparan: number }[];
  menit_paparan: number;
  menit_paparan_per_apd: { item: string; label: string; menit: number }[];
  rata_durasi_pelanggaran_detik: number | null;
};

export type Intervention = { id: number; waktu: string; judul: string; catatan: string; kamera: string | null };

type Window = {
  mulai: string; selesai: string; hari: number; pekerja: number; compliance_rate: number | null;
  pelanggaran_per_hari: number | null; menit_paparan_per_hari: number | null; kepatuhan_per_apd: Record<string, number | null>;
};

export type Comparison = {
  intervensi: Intervention;
  hari: number;
  sebelum: Window;
  sesudah: Window;
  perubahan: { compliance_rate: number | null; pelanggaran_per_hari: number | null; menit_paparan_per_hari: number | null };
  uji_statistik: { z: number; p_value: number; signifikan: boolean } | null;
  kesimpulan: string;
  catatan: string;
};

export type ModelQuality = {
  versi: string; event: number; confirmed: number; false_positive: number; pending: number;
  precision: number | null; cakupan_review: number | null;
  per_apd: { item: string; label: string; confirmed: number; false_positive: number; precision: number | null }[];
};

export type CameraHealth = {
  kamera: string;
  status: "baik" | "peringatan" | "tidak_ada_data";
  masalah: string[];
  info: string[];
  terkini: Record<string, number | null>;
  baseline: Record<string, number | null>;
  jendela_menit: number;
  per_jam: { jam: string; kecerahan: number; ketajaman: number; conf_orang: number }[];
};

export type CameraSpeed = {
  kamera: string; fps: number; running: boolean;
  waktu_batch_ms: number; pemeriksaan_per_detik: number;
  persen_frame_dilewati: number; di_bawah_batas: boolean;
};

export type SystemStatus = {
  pipeline: { running: boolean; error: string | null; fps: number };
  worker: {
    sehat: boolean; status: "berjalan" | "heartbeat_kedaluwarsa" | "heartbeat_tidak_tersedia";
    usia_heartbeat_detik: number | null; heartbeat_pada: string | null; mulai_pada: string | null;
    pipeline_aktif: boolean; kamera_aktif: number;
  };
  kamera: CameraSpeed[];
  versi_model: string;
  model: { pose: string; apd: string; apd_setiap_n_frame: number };
  kapasitas: {
    waktu_batch_ms: number | null;
    jumlah_frame_batch: number;
    pemeriksaan_per_kamera: number | null;
    batas_aman_per_kamera: number;
    beban_perangkat_persen: number | null;
    beban_perangkat_sumber: string;
    perangkat: string;
    proyeksi_kamera: { jumlah: number; pemeriksaan_per_kamera: number | null; memenuhi_batas: boolean | null }[];
    catatan_proyeksi: string;
  };
  alert: { telegram: boolean; cooldown_s: number; reviewer_terhubung: number };
  retensi: { evidence_days: number; quality_days: number; video_days: number };
  data_contoh: boolean;
};

export type Zone = {
  name: string;
  camera_id: string;
  polygon: [number, number][];
  required: string[];
  updated_at: string;
  updated_by: string;
};

export type Settings = {
  sensitivity: "ketat" | "seimbang" | "longgar";
  shifts: { name: string; start: string; end: string }[];
  alert: { telegram: boolean; cooldown_s: number };
  retention: { evidence_days: number; quality_days: number };
  preset_values: Record<string, { present_threshold: number; missing_threshold: number; event_min_frames: number }>;
};

export type AuditLogItem = {
  waktu: string;
  aksi: string;
  aktor: string;
  detail: string;
  violation_id: number | null;
};

export type CameraStatus = { mode: "demo" | "stream"; sumber: string; running: boolean; error: string | null };

export type CameraRow = {
  id: string; name: string; source_kind: "stream" | "video"; sumber: string;
  mode: "" | "simulasi" | "analisis"; video_id: string; recording_start: string | null; sample_rate: number;
  enabled: boolean; fps: number; running: boolean; pemeriksaan_per_detik: number;
};

export type VideoRow = {
  id: string; label: string; size: number; duration_s: number; fps: number;
  width: number; height: number; created_at: string;
};

export type ServerVideo = { path: string };

export type JobStatus = "menunggu" | "berjalan" | "dijeda" | "selesai" | "dibatalkan" | "gagal";

export type Job = {
  id: string; camera_id: string; video_id: string; recording_start: string; sample_rate: number;
  nama_file: string; zona: string; thumbnail: string | null;
  status: JobStatus; position_s: number; events: number;
  durasi_video_s: number | null; perkiraan_sisa_s: number | null;
  video_hasil: string | null; video_hasil_sha256: string | null;
  started_at: string | null; finished_at: string | null;
};

export type Source = "nyata" | "live" | "semua";

export type AgentAuditData = {
  confidence?: "rendah" | "sedang" | "tinggi" | string;
  sumber_data?: string[];
  periode?: Record<string, unknown>;
  filter?: Record<string, unknown>;
  metrik?: Record<string, unknown>;
  batasan?: string[];
};

export type ChatReply = {
  answer: string;
  evidence: Violation[];
  tool_calls: { name: string; input: unknown }[];
  ditolak: boolean; // true jika pertanyaan di luar cakupan agent
  alasan: string | null;
  alasan_jawaban: string[];
  audit_data: AgentAuditData;
  tanda: string; // tanda tangan server; dikirim balik bersama jawaban ini sebagai riwayat
};

export type ChatHistoryItem = {
  role: "user" | "assistant";
  content: string;
  at: string;
  alasan_jawaban?: string[];
  audit_data?: AgentAuditData;
};

export type Conversation = {
  id: string;
  title: string;
  created: string;
  updated: string;
  pinned: boolean;
};

export type ConversationMessage = {
  id: number;
  role: "user" | "assistant";
  content: string;
  at: string;
  tool_calls?: { name: string; input: unknown }[];
  evidence?: Violation[];
  alasan_jawaban?: string[];
  audit_data?: AgentAuditData;
  refused?: boolean;
};

export type ConversationDetail = Conversation & {
  messages: ConversationMessage[];
};

export type Role = "viewer" | "supervisor" | "admin";
export type Me = { username: string; role: Role; lokal: boolean };
export type UserRow = {
  username: string; role: Role; aktif: boolean; dibuat: string; login_terakhir: string | null; telegram_id: string | null;
};

const LEVEL: Record<Role, number> = { viewer: 0, supervisor: 1, admin: 2 };
export const can = (me: Me | null, role: Role) => !!me && LEVEL[me.role] >= LEVEL[role];

async function req<T>(url: string, init?: RequestInit): Promise<T> {
  const r = await fetch(url, { headers: { "Content-Type": "application/json" }, credentials: "same-origin", ...init });
  // sesi habis / dicabut: minta App menampilkan halaman login lagi
  if (r.status === 401 && !url.startsWith("/api/auth/")) window.dispatchEvent(new Event("ppe:logout"));
  if (r.status === 204) return undefined as T;
  if (!r.ok) {
    const body = await r.json().catch(() => ({}));
    // 422 dari FastAPI berisi daftar error validasi, bukan string
    const detail = Array.isArray(body.detail) ? body.detail.map((d: { msg?: string }) => d.msg).join("; ") : body.detail;
    throw new Error(detail || r.statusText || `HTTP ${r.status}`);
  }
  return r.json();
}

async function reqWithTimeout<T>(url: string, ms: number, init?: RequestInit): Promise<T> {
  const ctrl = new AbortController();
  const timer = window.setTimeout(() => ctrl.abort(), ms);
  try {
    return await req<T>(url, { ...init, signal: ctrl.signal });
  } catch (e) {
    if ((e as DOMException).name === "AbortError") throw new Error("Agent belum merespons. Pastikan Ollama berjalan dan model sudah dimuat, lalu coba lagi.");
    throw e;
  } finally {
    window.clearTimeout(timer);
  }
}

export const api = {
  live: (camera?: string) => req<Live>(`/api/live${camera ? `?camera=${encodeURIComponent(camera)}` : ""}`),
  stats: (period: string, source: Source = "nyata", cameraIds: string[] = []) => {
    const params = new URLSearchParams({ period, source });
    if (cameraIds.length) params.set("camera_ids", cameraIds.join(","));
    return req<Stats>(`/api/stats?${params}`);
  },
  violations: (params: Record<string, string>) =>
    req<{ total: number; total_event?: number; items: Violation[] }>(`/api/violations?${new URLSearchParams(params)}`),
  violation: (id: number) => req<Violation>(`/api/violations/${id}`),
  review: (id: number, review: string, corrected_items: string[] = []) =>
    req<Violation>(`/api/violations/${id}/review`, { method: "PATCH", body: JSON.stringify({ review, corrected_items }) }),
  agentHistory: () => req<ChatHistoryItem[]>("/api/agent/history?limit=30"),
  conversations: (q?: string) => req<Conversation[]>(`/api/agent/conversations${q ? `?q=${encodeURIComponent(q)}` : ""}`),
  createConversation: (title?: string) =>
    req<Conversation>("/api/agent/conversations", { method: "POST", body: JSON.stringify({ title }) }),
  conversation: (id: string, signal?: AbortSignal) =>
    req<ConversationDetail>(`/api/agent/conversations/${encodeURIComponent(id)}`, { signal }),
  renameConversation: (id: string, title: string) =>
    req<Conversation>(`/api/agent/conversations/${encodeURIComponent(id)}`, { method: "PATCH", body: JSON.stringify({ title }) }),
  pinConversation: (id: string, pinned: boolean) =>
    req<Conversation>(`/api/agent/conversations/${encodeURIComponent(id)}`, { method: "PATCH", body: JSON.stringify({ pinned }) }),
  deleteConversation: (id: string) =>
    req<void>(`/api/agent/conversations/${encodeURIComponent(id)}`, { method: "DELETE" }),
  chat: (message: string, conversation_id?: string, signal?: AbortSignal) =>
    reqWithTimeout<ChatReply & { conversation_id?: string; conversation_title?: string }>("/api/agent/chat", 120000, {
      method: "POST",
      body: JSON.stringify({ message, conversation_id }),
      signal,
    }),
  interventions: () => req<Intervention[]>("/api/interventions"),
  addIntervention: (body: { at: string; title: string; note: string; camera_id: string }) =>
    req<Intervention>("/api/interventions", { method: "POST", body: JSON.stringify(body) }),
  deleteIntervention: (id: number) => req<void>(`/api/interventions/${id}`, { method: "DELETE" }),
  me: () => req<Me>("/api/auth/me"),
  login: (username: string, password: string, totp_code?: string) =>
    req<Me>("/api/auth/login", { method: "POST", body: JSON.stringify({ username, password, totp_code }) }),
  logout: () => req<void>("/api/auth/logout", { method: "POST" }),
  changePassword: (old_password: string, new_password: string) =>
    req<void>("/api/auth/password", { method: "POST", body: JSON.stringify({ old_password, new_password }) }),
  users: () => req<UserRow[]>("/api/users"),
  addUser: (username: string, password: string, role: Role) =>
    req<UserRow>("/api/users", { method: "POST", body: JSON.stringify({ username, password, role }) }),
  updateUser: (username: string, body: { role?: Role; active?: boolean; password?: string; telegram_id?: string }) =>
    req<UserRow>(`/api/users/${encodeURIComponent(username)}`, { method: "PATCH", body: JSON.stringify(body) }),
  compare: (id: number, days: number) => req<Comparison>(`/api/interventions/${id}/compare?days=${days}`),
  modelQuality: () => req<ModelQuality[]>("/api/model-quality"),
  exportModelReviews: async (version?: string) => {
    const params = version ? `?version=${encodeURIComponent(version)}` : "";
    const response = await fetch(`/api/model-quality/export${params}`, { method: "POST", credentials: "same-origin" });
    if (response.status === 401) window.dispatchEvent(new Event("ppe:logout"));
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      throw new Error(body.detail || response.statusText || `HTTP ${response.status}`);
    }
    const fallback = "hasil-review-apd.zip";
    const filename = response.headers.get("Content-Disposition")?.match(/filename="([^"]+)"/)?.[1] || fallback;
    const url = URL.createObjectURL(await response.blob());
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
    return filename;
  },
  camerasHealth: () => req<CameraHealth[]>("/api/cameras/health"),
  camera: () => req<CameraStatus>("/api/camera"),
  setCamera: (mode: "demo" | "stream", url?: string) =>
    req<CameraStatus>("/api/camera", { method: "POST", body: JSON.stringify({ mode, url }) }),
  systemStatus: () => req<SystemStatus>("/api/system/status"),
  zones: (cameraId?: string) => req<Zone[]>(`/api/zones${cameraId != null ? `?camera_id=${encodeURIComponent(cameraId)}` : ""}`),
  createZone: (body: Partial<Zone>) => req<Zone>("/api/zones", { method: "POST", body: JSON.stringify(body) }),
  updateZone: (name: string, body: Partial<Zone>, cameraId = "") =>
    req<Zone>(`/api/zones/${encodeURIComponent(name)}?camera_id=${encodeURIComponent(cameraId)}`, { method: "PUT", body: JSON.stringify(body) }),
  deleteZone: (name: string, cameraId = "") =>
    req<void>(`/api/zones/${encodeURIComponent(name)}?camera_id=${encodeURIComponent(cameraId)}`, { method: "DELETE" }),
  updateZoneRequired: (name: string, required: string[], cameraId = "") =>
    req<Zone>(`/api/zones/${encodeURIComponent(name)}/required?camera_id=${encodeURIComponent(cameraId)}`, { method: "PATCH", body: JSON.stringify({ required }) }),
  settings: () => req<Settings>("/api/settings"),
  updateSettings: (body: Partial<Settings>) => req<Settings>("/api/settings", { method: "PUT", body: JSON.stringify(body) }),
  retentionSimulate: () => req<{ message: string }>("/api/retention/simulate", { method: "POST" }),
  audit: (params: Record<string, string>) => req<{ total: number; items: AuditLogItem[] }>(`/api/audit?${new URLSearchParams(params)}`),
  auditActors: (params: Record<string, string>) => req<{ items: string[] }>(`/api/audit/actors?${new URLSearchParams(params)}`),
  cameras: () => req<CameraRow[]>("/api/cameras"),
  createCamera: (body: Record<string, unknown>) => req<CameraRow>("/api/cameras", { method: "POST", body: JSON.stringify(body) }),
  updateCamera: (id: string, body: Record<string, unknown>) => req<CameraRow>(`/api/cameras/${encodeURIComponent(id)}`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteCamera: (id: string) => req<void>(`/api/cameras/${encodeURIComponent(id)}`, { method: "DELETE" }),
  testCamera: (id: string, body: Record<string, unknown>) => req<{ ok: boolean; pesan: string }>(`/api/cameras/${encodeURIComponent(id)}/test`, { method: "POST", body: JSON.stringify(body) }),
  videos: () => req<VideoRow[]>("/api/videos"),
  serverVideos: () => req<ServerVideo[]>("/api/server-videos"),
  registerVideo: (path: string, label?: string) =>
    req<{ id: string; label: string }>("/api/videos/register", { method: "POST", body: JSON.stringify({ path, label }) }),
  startUpload: (name: string, size: number) =>
    req<{ id: string; offset: number; chunk_mb: number }>("/api/uploads", { method: "POST", body: JSON.stringify({ name, size }) }),
  uploadStatus: (id: string) => req<{ id: string; offset: number; size: number }>(`/api/uploads/${id}`),
  uploadChunk: async (id: string, offset: number, data: ArrayBuffer) => {
    const r = await fetch(`/api/uploads/${id}?offset=${offset}`, {
      method: "PUT", credentials: "same-origin",
      headers: { "Content-Type": "application/octet-stream" }, body: data,
    });
    if (r.status === 401) window.dispatchEvent(new Event("ppe:logout"));
    if (!r.ok && r.status !== 409) throw new Error((await r.json().catch(() => ({}))).detail || `HTTP ${r.status}`);
    return r.json() as Promise<{ offset: number }>;  // 409 juga mengembalikan offset benar (idempoten)
  },
  completeUpload: (id: string) =>
    req<{ id: string; label: string; duration_s: number; size: number }>(`/api/uploads/${id}/complete`, { method: "POST" }),
  cancelUpload: (id: string) => req<void>(`/api/uploads/${id}`, { method: "DELETE" }),
  jobs: () => req<Job[]>("/api/jobs"),
  createJob: (body: { camera_id: string; video_id: string; recording_start: string; sample_rate: number }) =>
    req<Job>("/api/jobs", { method: "POST", body: JSON.stringify(body) }),
  controlJob: (id: string, action: "pause" | "resume" | "cancel") => req<Job>(`/api/jobs/${encodeURIComponent(id)}/${action}`, { method: "POST" }),
};

export const PERIODS: [string, string][] = [
  ["hari_ini", "Hari ini"],
  ["kemarin", "Kemarin"],
  ["7_hari", "7 hari"],
  ["30_hari", "30 hari"],
];

export const ITEM_LABEL: Record<string, string> = {
  helmet: "Helm",
  vest: "Rompi",
  gloves: "Sarung tangan",
  shoes: "Sepatu",
};

export const fmtNum = (v: number | null | undefined, digits = 1) =>
  v == null ? "–" : v.toLocaleString("id-ID", { maximumFractionDigits: digits });

export const fmtTime = (iso: string) =>
  new Date(iso).toLocaleString("id-ID", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", second: "2-digit" });
