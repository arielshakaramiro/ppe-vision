import { resolve } from "node:path";
import type { Page, Route } from "@playwright/test";
import type { CameraRow, Job, JobStatus, VideoRow, Violation } from "../../src/api";

const POSTER_PATH = resolve(process.cwd(), "e2e/fixtures/assets/cctv-uji-poster.jpg");
const VIDEO_PATH = resolve(process.cwd(), "e2e/fixtures/assets/cctv-uji.mp4");

const STATUS_CAMERA: Record<JobStatus, string> = {
  selesai: "CAM-01",
  berjalan: "CAM-02",
  menunggu: "CAM-03",
  gagal: "CAM-04",
  dijeda: "CAM-05",
  dibatalkan: "CAM-06",
};

const STATUS_ZONE: Record<JobStatus, string> = {
  selesai: "Area pengemasan",
  berjalan: "Area las",
  menunggu: "Loading dock",
  gagal: "Gudang bahan",
  dijeda: "Area perakitan",
  dibatalkan: "Gerbang timur",
};

const STATUS_FILE: Record<JobStatus, string> = {
  selesai: "area-pengemasan-14okt-0800.mp4",
  berjalan: "area-las-13okt-shift-malam.mkv",
  menunggu: "loading-dock-13okt.mp4",
  gagal: "gudang-bahan-12okt.mp4",
  dijeda: "perakitan-12okt.mp4",
  dibatalkan: "gerbang-timur-11okt.mp4",
};

function makeJob(status: JobStatus, index: number): Job {
  const position = status === "selesai" ? 7200 : status === "berjalan" ? 3534 : status === "dijeda" ? 1800 : 0;
  const duration = status === "selesai" ? 7200 : 6310;
  return {
    id: `JOB-${status.toUpperCase()}`,
    camera_id: STATUS_CAMERA[status],
    video_id: `VID-${index + 1}`,
    recording_start: `2026-10-${String(14 - index).padStart(2, "0")}T${status === "berjalan" ? "22:00:00" : "08:00:00"}+07:00`,
    sample_rate: status === "berjalan" ? 10 : 5,
    nama_file: STATUS_FILE[status],
    zona: STATUS_ZONE[status],
    thumbnail: "/mock/cctv-uji-poster.jpg",
    status,
    position_s: position,
    events: status === "selesai" ? 3 : status === "berjalan" ? 2 : 0,
    durasi_video_s: duration,
    perkiraan_sisa_s: status === "berjalan" ? 1098 : null,
    video_hasil: status === "selesai" ? "/mock/cctv-uji.mp4" : null,
    video_hasil_sha256: status === "selesai" ? "ed452f1b644e25b0fe369220b82fe663fb7ae43b3fbb1063986814f99bfd905f" : null,
    started_at: `2026-10-${String(14 - index).padStart(2, "0")}T08:01:00+07:00`,
    finished_at: status === "selesai" ? "2026-10-14T10:04:00+07:00" : null,
  };
}

function makeViolation(id: number, camera: string, at: string, uncertain = false): Violation {
  return {
    id,
    waktu: at,
    selesai: null,
    durasi_detik: 18,
    kamera: camera,
    zona: STATUS_ZONE[uncertain ? "berjalan" : "selesai"],
    pekerja: `Pekerja #${id}`,
    jenis: uncertain ? "uncertain" : "violation",
    sumber: "rekaman",
    apd_kurang: uncertain ? [] : ["Helm"],
    apd_kurang_kode: uncertain ? [] : ["helmet"],
    apd_tidak_pasti: uncertain ? ["Sarung tangan"] : [],
    apd_tidak_pasti_kode: uncertain ? ["gloves"] : [],
    foto: "/mock/cctv-uji-poster.jpg",
    foto_crop: "/mock/cctv-uji-poster.jpg",
    sha256: "a".repeat(64),
    klip: null,
    klip_sha256: null,
    versi_model: "ppe-v2",
    review: "pending",
  };
}

export type RecordedAnalysisFixture = {
  jobs: Job[];
  videos: VideoRow[];
  jobListRequests: number;
  controls: { id: string; action: string }[];
  uploadOffsets: number[];
  createdJobBody: Record<string, unknown> | null;
  createdCameraBody: Record<string, unknown> | null;
  createdCameraCount: number;
  failNextJob: boolean;
};

const json = (route: Route, body: unknown, status = 200) => route.fulfill({
  status,
  contentType: "application/json",
  body: JSON.stringify(body),
});

export async function installRecordedAnalysisFixture(page: Page): Promise<RecordedAnalysisFixture> {
  const statuses: JobStatus[] = ["selesai", "berjalan", "menunggu", "gagal", "dijeda", "dibatalkan"];
  const state: RecordedAnalysisFixture = {
    jobs: statuses.map(makeJob),
    videos: [{ id: "VID-TERDAFTAR", label: "rekaman-terdaftar.mp4", size: 5_242_880, duration_s: 120, fps: 25, width: 1280, height: 720, created_at: "2026-10-14T07:00:00+07:00" }],
    jobListRequests: 0,
    controls: [],
    uploadOffsets: [],
    createdJobBody: null,
    createdCameraBody: null,
    createdCameraCount: 0,
    failNextJob: false,
  };
  const cameras: CameraRow[] = [{ id: "CAM-01", name: "Kamera pengemasan", source_kind: "video", sumber: "rekaman", mode: "analisis", video_id: "", recording_start: null, sample_rate: 5, enabled: true, fps: 25, running: false, pemeriksaan_per_detik: 5 }];
  let uploadSize = 0;
  let uploadOffset = 0;

  await page.route("**/mock/cctv-uji-poster.jpg", (route) => route.fulfill({ path: POSTER_PATH, contentType: "image/jpeg" }));
  await page.route("**/mock/cctv-uji.mp4", (route) => route.fulfill({ path: VIDEO_PATH, contentType: "video/mp4" }));
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const method = request.method();

    if (path === "/api/jobs" && method === "GET") {
      state.jobListRequests += 1;
      return json(route, state.jobs);
    }
    if (path === "/api/jobs" && method === "POST") {
      const body = request.postDataJSON() as Record<string, unknown>;
      state.createdJobBody = body;
      if (state.failNextJob) {
        state.failNextJob = false;
        return json(route, { detail: "Uji kegagalan antrean" }, 400);
      }
      const created: Job = {
        ...makeJob("menunggu", 6),
        id: "JOB-BARU",
        camera_id: String(body.camera_id),
        video_id: String(body.video_id),
        recording_start: String(body.recording_start),
        sample_rate: Number(body.sample_rate),
        nama_file: "rekaman-upload.mp4",
        zona: "Area pengemasan",
        started_at: "2026-10-15T08:31:00+07:00",
      };
      state.jobs.unshift(created);
      return json(route, created, 201);
    }
    const control = path.match(/^\/api\/jobs\/([^/]+)\/(pause|resume|cancel)$/);
    if (control && method === "POST") {
      const [, id, action] = control;
      const job = state.jobs.find((item) => item.id === id);
      if (!job) return json(route, { detail: "Pekerjaan tidak ditemukan" }, 404);
      job.status = action === "pause" ? "dijeda" : action === "resume" ? "berjalan" : "dibatalkan";
      state.controls.push({ id, action });
      return json(route, job);
    }
    if (path === "/api/violations" && method === "GET") {
      const camera = url.searchParams.get("camera_id");
      const items = camera === "CAM-01"
        ? [makeViolation(101, "CAM-01", "2026-10-14T08:18:00+07:00"), makeViolation(102, "CAM-01", "2026-10-14T09:04:00+07:00")]
        : camera === "CAM-02" ? [makeViolation(201, "CAM-02", "2026-10-13T22:24:00+07:00", true)] : [];
      return json(route, { total: items.length, total_event: items.length, items });
    }
    if (path === "/api/cameras" && method === "GET") return json(route, cameras);
    if (path === "/api/cameras" && method === "POST") {
      const body = request.postDataJSON() as Record<string, unknown>;
      state.createdCameraBody = body;
      state.createdCameraCount += 1;
      if (cameras.some((camera) => camera.id === body.id)) return json(route, { detail: "ID kamera sudah dipakai" }, 400);
      const camera: CameraRow = {
        id: String(body.id), name: String(body.name), source_kind: "video", sumber: String(body.video_id),
        mode: "analisis", video_id: String(body.video_id), recording_start: null, sample_rate: 5,
        enabled: Boolean(body.enabled), fps: 0, running: false, pemeriksaan_per_detik: 0,
      };
      cameras.push(camera);
      return json(route, camera, 201);
    }
    if (path === "/api/videos" && method === "GET") return json(route, state.videos);
    if (path === "/api/uploads" && method === "POST") {
      const body = request.postDataJSON() as { size: number };
      uploadSize = body.size;
      uploadOffset = Math.min(4, uploadSize);
      return json(route, { id: "UPLOAD-E2E", offset: 0, chunk_mb: 1 }, 201);
    }
    if (path === "/api/uploads/UPLOAD-E2E" && method === "GET") {
      return json(route, { id: "UPLOAD-E2E", offset: uploadOffset, size: uploadSize });
    }
    if (path === "/api/uploads/UPLOAD-E2E" && method === "PUT") {
      const offset = Number(url.searchParams.get("offset"));
      state.uploadOffsets.push(offset);
      uploadOffset = offset + (request.postDataBuffer()?.byteLength ?? 0);
      return json(route, { offset: uploadOffset });
    }
    if (path === "/api/uploads/UPLOAD-E2E/complete" && method === "POST") {
      const video: VideoRow = { id: "VID-UPLOAD", label: "rekaman-upload.mp4", size: uploadSize, duration_s: 90, fps: 25, width: 1280, height: 720, created_at: "2026-10-15T08:29:00+07:00" };
      state.videos.push(video);
      return json(route, video);
    }
    return route.fallback();
  });
  return state;
}