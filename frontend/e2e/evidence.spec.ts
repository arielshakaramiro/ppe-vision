import { expect, test, type Page } from "@playwright/test";
import { login, openTab, type Role } from "./helpers";

test.use({ viewport: { width: 1440, height: 900 } });

const pixel = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='800' height='500'%3E%3Crect width='800' height='500' fill='%23434a4f'/%3E%3Crect x='310' y='70' width='180' height='360' rx='12' fill='%2396a1a7'/%3E%3C/svg%3E";

const cameras = [
  {
    id: "CAM-UJI-01", name: "Area pengemasan", source_kind: "stream", sumber: "rtsp://***@uji/satu",
    mode: "", video_id: "", recording_start: null, sample_rate: 10, enabled: true, fps: 12, running: true,
    pemeriksaan_per_detik: 10,
  },
  {
    id: "CAM-UJI-02", name: "Area las", source_kind: "video", sumber: "rekaman-uji.mp4",
    mode: "analisis", video_id: "VID-UJI", recording_start: "2026-10-14T07:00:00+07:00", sample_rate: 5,
    enabled: true, fps: 25, running: false, pemeriksaan_per_detik: 5,
  },
];

const rawEvidence = (id: number, overrides: Record<string, unknown> = {}) => ({
  id,
  waktu: `2026-10-14T08:0${id}:00+07:00`,
  selesai: `2026-10-14T08:0${id}:22+07:00`,
  durasi_detik: 22,
  kamera: "CAM-UJI-01",
  zona: "Area pengemasan",
  pekerja: "W10",
  worker_id: 10,
  pekerja_unik: "CAM-UJI-01:2026-10-14:W10",
  jenis: "violation",
  sumber: "live",
  apd_kurang: ["Helm", "Rompi"],
  apd_kurang_kode: ["helmet", "vest"],
  apd_tidak_pasti: [],
  apd_tidak_pasti_kode: [],
  apd_dibenarkan: [],
  apd_dibenarkan_kode: [],
  foto: pixel,
  foto_crop: pixel,
  foto_clean: null,
  deteksi: null,
  sha256: `hash-foto-${id}`,
  klip: null,
  klip_sha256: null,
  versi_model: "v2",
  review: "pending",
  ...overrides,
});

const firstEvents = [
  rawEvidence(1),
  rawEvidence(2, { waktu: "2026-10-14T08:04:00+07:00", apd_kurang: ["Helm"], apd_kurang_kode: ["helmet"], sha256: "hash-foto-2" }),
];

const groupedEvidence = [
  {
    ...firstEvents[0],
    jumlah_event: 2,
    waktu_mulai: firstEvents[0].waktu,
    waktu_selesai: firstEvents[1].selesai,
    event_bukti: firstEvents,
  },
  rawEvidence(3, {
    kamera: "CAM-UJI-02", zona: "Area las", pekerja: "W21", sumber: "rekaman", apd_kurang: ["Sarung tangan"],
    apd_kurang_kode: ["gloves"], review: "confirmed", waktu_mulai: "2026-10-14T09:12:04+07:00",
    waktu_selesai: "2026-10-14T09:18:30+07:00", jumlah_event: 1,
  }),
];

async function installEvidenceFixture(page: Page) {
  const requests: URL[] = [];
  const reviews: { id: string; body: Record<string, unknown> }[] = [];
  await page.route("**/api/cameras", (route) => route.fulfill({ json: cameras }));
  await page.route("**/api/violations?*", (route) => {
    requests.push(new URL(route.request().url()));
    return route.fulfill({ json: { total: 2, total_event: 3, items: groupedEvidence } });
  });
  await page.route(/\/api\/violations\/(\d+)\/review$/, async (route) => {
    const id = route.request().url().match(/violations\/(\d+)\/review$/)?.[1] ?? "";
    const body = route.request().postDataJSON() as Record<string, unknown>;
    reviews.push({ id, body });
    const source = firstEvents.find((item) => String(item.id) === id) ?? firstEvents[0];
    await route.fulfill({ json: { ...source, review: body.review, apd_dibenarkan_kode: body.corrected_items } });
  });
  return { requests, reviews };
}

async function openEvidence(page: Page, role: Role) {
  await login(page, role);
  await openTab(page, "Evidence");
  await expect(page.locator(".evidence-page")).toBeVisible();
  await expect(page.locator(".evidence-tile")).toHaveCount(2);
}

test("Evidence menampilkan tata letak dan mengelompokkan data API", async ({ page }) => {
  await installEvidenceFixture(page);
  await openEvidence(page, "viewer");

  await expect(page.locator(".evidence-summary > div")).toHaveCount(3);
  await expect(page.locator(".evidence-summary")).toContainText("2");
  await expect(page.locator(".evidence-summary")).toContainText("3");
  await expect(page.locator(".evidence-group")).toHaveCount(2);
  const liveGroup = page.locator(".evidence-group").filter({ hasText: "Area pengemasan" });
  const recordingGroup = page.locator(".evidence-group").filter({ hasText: "Area las" });
  const workerTile = page.getByRole("button", { name: "Buka evidence W10" });
  await expect(liveGroup).toContainText("Area pengemasan · CAM-UJI-01");
  await expect(liveGroup).toContainText("Live");
  await expect(recordingGroup).toContainText("Rekaman");
  await expect(workerTile).toContainText("2 bukti");
  await expect(workerTile).toContainText("Wajah disamarkan");
  await expect(workerTile).toContainText("Menunggu review");
  await expect(page.locator(".evidence-group .gallery").first()).toHaveCSS("grid-template-columns", /.+ .+ .+ .+/);

  await page.screenshot({ path: "e2e/screenshots/evidence-desktop.png", fullPage: true });
});

test("semua filter diteruskan ke API, chip dapat dihapus, dan reset mengembalikan nilai awal", async ({ page }) => {
  const { requests } = await installEvidenceFixture(page);
  await openEvidence(page, "viewer");
  await expect.poll(() => requests.length).toBeGreaterThan(0);

  const latest = () => requests.at(-1)!;
  expect(latest().searchParams.get("period")).toBe("7_hari");
  expect(latest().searchParams.get("kind")).toBe("violation");
  expect(latest().searchParams.get("group_by_worker")).toBe("true");

  await page.getByRole("button", { name: "30 hari", exact: true }).click();
  await expect.poll(() => latest().searchParams.get("period")).toBe("30_hari");

  await page.getByRole("button", { name: "Sortir kamera" }).click();
  await page.getByRole("option", { name: "Area las — CAM-UJI-02" }).click();
  await expect.poll(() => latest().searchParams.get("camera_id")).toBe("CAM-UJI-02");

  await page.getByRole("button", { name: "Jenis evidence" }).click();
  await page.getByRole("option", { name: "Tidak pasti" }).click();
  await expect.poll(() => latest().searchParams.get("kind")).toBe("uncertain");

  await page.getByRole("button", { name: "APD" }).click();
  await page.getByRole("option", { name: "Tanpa helm" }).click();
  await expect.poll(() => latest().searchParams.get("item")).toBe("helmet");

  await page.getByRole("button", { name: "Status review" }).click();
  await page.getByRole("option", { name: "Menunggu review" }).click();
  await expect.poll(() => latest().searchParams.get("review")).toBe("pending");

  await page.getByRole("button", { name: "Hapus filter APD" }).click();
  await expect.poll(() => latest().searchParams.has("item")).toBe(false);
  await page.getByRole("button", { name: "Atur ulang filter evidence" }).click();
  await expect.poll(() => latest().searchParams.get("period")).toBe("7_hari");
  expect(latest().searchParams.get("kind")).toBe("violation");
  expect(latest().searchParams.has("camera_id")).toBe(false);
  expect(latest().searchParams.has("review")).toBe(false);
  await expect(page.getByRole("button", { name: "Atur ulang filter evidence" })).toBeDisabled();
});

test("viewer dapat menelusuri bukti, foto, dan hash tanpa aksi review", async ({ page }) => {
  await installEvidenceFixture(page);
  await openEvidence(page, "viewer");

  const opener = page.getByRole("button", { name: "Buka evidence W10" });
  await opener.click();
  const modal = page.getByRole("dialog", { name: "Detail evidence W10" });
  await expect(modal).toBeVisible();
  await expect(modal.getByText("2 bukti", { exact: true })).toBeVisible();
  await expect(modal.getByRole("button", { name: "Benar pelanggaran" })).toHaveCount(0);
  const secondEvidence = modal.locator(".worker-evidence-items button").nth(1);
  await secondEvidence.click();
  await expect(secondEvidence).toHaveClass(/on/);

  await modal.getByRole("button", { name: "Lihat hash SHA-256" }).click();
  await expect(modal.getByRole("status", { name: "Hash SHA-256 evidence" })).toContainText("hash-foto-2");
  await modal.getByRole("button", { name: "Buka foto penuh" }).click();
  await expect(page.getByRole("dialog", { name: "Pratinjau foto evidence" })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog", { name: "Pratinjau foto evidence" })).toHaveCount(0);
  await expect(modal).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(modal).toHaveCount(0);
  await expect(opener).toBeFocused();

  await opener.click();
  await page.getByRole("button", { name: "Tutup modal" }).click();
  await expect(page.getByRole("dialog", { name: "Detail evidence W10" })).toHaveCount(0);
  await expect(opener).toBeFocused();
});

test("supervisor mengirim review false positive beserta koreksi APD", async ({ page }) => {
  const { reviews } = await installEvidenceFixture(page);
  await openEvidence(page, "supervisor");
  await page.getByRole("button", { name: "Buka evidence W10" }).click();

  const modal = page.getByRole("dialog", { name: "Detail evidence W10" });
  await expect(modal.getByRole("button", { name: "Benar pelanggaran" })).toBeVisible();
  await expect(modal.getByLabel("Helm")).toBeChecked();
  await expect(modal.getByLabel("Rompi")).toBeChecked();
  await modal.getByLabel("Rompi").uncheck();
  await modal.getByRole("button", { name: "Bukan pelanggaran" }).click();

  await expect.poll(() => reviews.length).toBe(1);
  expect(reviews[0]).toEqual({ id: "1", body: { review: "false_positive", corrected_items: ["helmet"] } });
  await expect(modal.getByRole("status")).toContainText("Review berhasil disimpan sebagai bukan pelanggaran.");
  await expect(modal.locator(".worker-evidence-items button")).toHaveCount(2);
  await expect(modal.getByText("2 bukti", { exact: true })).toBeVisible();
});

test("Evidence responsif tanpa overflow horizontal pada viewport ponsel", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await installEvidenceFixture(page);
  await openEvidence(page, "viewer");

  await expect(page.locator(".evidence-filter-grid")).toHaveCSS("grid-template-columns", /\d+px/);
  await expect(page.locator(".evidence-group .gallery").first()).toHaveCSS("grid-template-columns", /\d+px/);
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(1);
  await page.screenshot({ path: "e2e/screenshots/evidence-mobile.png", fullPage: true });
});