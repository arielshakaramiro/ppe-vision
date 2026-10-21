import { expect, test, type Page } from "@playwright/test";
import { installRecordedAnalysisFixture, type RecordedAnalysisFixture } from "./fixtures/recorded-analysis";
import { login, openTab } from "./helpers";

let browserErrors: string[];

test.beforeEach(async ({ page }) => {
  browserErrors = [];
  page.on("console", (message) => {
    if (message.type() === "error") browserErrors.push(message.text());
  });
  page.on("pageerror", (error) => browserErrors.push(error.message));
});

test.afterEach(() => {
  expect(browserErrors, "Tidak boleh ada error browser").toEqual([]);
});

async function openRecordedAnalysis(page: Page): Promise<RecordedAnalysisFixture> {
  const fixture = await installRecordedAnalysisFixture(page);
  await login(page, "admin");
  browserErrors = [];
  await openTab(page, "Analisis rekaman");
  await expect(page.getByRole("heading", { name: "Analisis rekaman" })).toBeVisible();
  return fixture;
}

async function selectRecording(page: Page, camera: string) {
  await page.getByRole("button", { name: "Pilih rekaman" }).click();
  await page.getByRole("option", { name: new RegExp(camera) }).click();
  await expect(page.getByRole("button", { name: "Pilih rekaman" })).toContainText(camera);
}

test("state selesai mengikuti struktur desain, playback aman, dan modal evidence berfungsi", async ({ page }) => {
  const fixture = await openRecordedAnalysis(page);
  await expect(page.getByLabel("Video hasil analisis rekaman", { exact: true })).toBeVisible();
  const videoButton = page.getByRole("button", { name: "Buka video hasil" });
  await expect(videoButton).toBeVisible();
  let popupOpened = false;
  page.on("popup", () => { popupOpened = true; });
  await videoButton.click();
  const videoDialog = page.getByRole("dialog", { name: "Video hasil rekaman" });
  await expect(videoDialog).toBeVisible();
  await expect(videoDialog.getByLabel("Video hasil di jendela", { exact: true })).toHaveAttribute("src", "/mock/cctv-uji.mp4");
  await expect(page.getByLabel("Video hasil analisis rekaman", { exact: true })).toHaveCount(0);
  expect(popupOpened).toBe(false);
  await page.screenshot({ path: "e2e/screenshots/analisis-rekaman-modal-video.png", fullPage: true });
  await page.keyboard.press("Escape");
  await expect(videoDialog).toHaveCount(0);
  await expect(videoButton).toBeFocused();
  await videoButton.click();
  await videoDialog.getByRole("button", { name: "Tutup video hasil" }).click();
  await expect(videoDialog).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Lihat di Evidence" })).toBeVisible();
  await expect(page.getByLabel("Kecepatan Video hasil analisis rekaman").getByRole("button")).toHaveCount(4);
  await expect(page.getByLabel("Kecepatan Video hasil analisis rekaman").getByRole("button", { name: "1×", exact: true })).toHaveClass(/on/);

  const video = page.getByLabel("Video hasil analisis rekaman", { exact: true });
  await video.evaluate((element: HTMLVideoElement) => { element.playbackRate = 2; });
  await expect.poll(() => video.evaluate((element: HTMLVideoElement) => element.playbackRate)).toBeLessThanOrEqual(1);

  await page.getByRole("button", { name: /Buka bukti Pekerja #101/ }).click();
  await expect(page.getByRole("dialog", { name: "Detail evidence Pekerja #101" })).toBeVisible();
  await expect(page.getByRole("dialog", { name: "Detail evidence Pekerja #101" })).toContainText("Helm");
  await page.getByRole("button", { name: "Tutup modal" }).click();

  const sidebar = await page.getByRole("complementary", { name: "Pelanggaran rekaman" }).boundingBox();
  expect(sidebar?.width).toBeGreaterThanOrEqual(359);
  expect(sidebar?.width).toBeLessThanOrEqual(361);
  await page.screenshot({ path: "e2e/screenshots/analisis-rekaman-selesai.png", fullPage: true });
  expect(fixture.jobListRequests).toBeGreaterThanOrEqual(1);
});

test("polling dan kontrol admin mengubah pekerjaan berjalan hingga dibatalkan", async ({ page }) => {
  const fixture = await openRecordedAnalysis(page);
  await selectRecording(page, "CAM-02");
  await expect(page.getByText("Menganalisis rekaman", { exact: true })).toBeVisible();
  await expect(page.getByText("56%", { exact: true }).first()).toBeVisible();
  await page.screenshot({ path: "e2e/screenshots/analisis-rekaman-berjalan.png", fullPage: true });

  await page.getByRole("button", { name: "Jeda", exact: true }).click();
  await expect(page.getByText("Analisis rekaman dijeda", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Lanjutkan", exact: true }).click();
  await expect(page.getByText("Menganalisis rekaman", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Batalkan", exact: true }).click();
  await expect(page.getByText("Analisis rekaman dibatalkan", { exact: true })).toBeVisible();
  expect(fixture.controls.map(({ action }) => action)).toEqual(["pause", "resume", "cancel"]);

  await expect.poll(() => fixture.jobListRequests, { timeout: 5_000 }).toBeGreaterThanOrEqual(2);
});

test("selector menampilkan dan memilih seluruh status lifecycle", async ({ page }) => {
  await openRecordedAnalysis(page);
  const states = [
    ["CAM-03", "Menunggu giliran analisis"],
    ["CAM-04", "Analisis rekaman gagal"],
    ["CAM-05", "Analisis rekaman dijeda"],
    ["CAM-06", "Analisis rekaman dibatalkan"],
  ];
  for (const [camera, message] of states) {
    await selectRecording(page, camera);
    await expect(page.getByText(message, { exact: true })).toBeVisible();
  }

  await page.getByRole("button", { name: "Pilih rekaman" }).click();
  const listbox = page.getByRole("listbox", { name: "Pilih rekaman" });
  for (const label of ["Selesai", "Berjalan", "Menunggu", "Gagal", "Dijeda", "Dibatalkan"]) {
    await expect(listbox).toContainText(label);
  }
});

test("unggah melanjutkan offset server lalu membuat pekerjaan dengan payload benar", async ({ page }) => {
  const fixture = await openRecordedAnalysis(page);
  await page.getByRole("button", { name: "+ Tambah rekaman" }).click();
  await expect(page.getByRole("heading", { name: "Tambah rekaman" })).toBeVisible();

  await page.locator('input[type="file"]').setInputFiles({
    name: "rekaman-upload.mp4",
    mimeType: "video/mp4",
    buffer: Buffer.from("0123456789ab"),
  });
  await expect.poll(() => fixture.uploadOffsets).toEqual([4]);

  await page.locator('input[type="datetime-local"]').fill("2026-10-15T08:30");
  await page.getByRole("button", { name: "Pemeriksaan per detik rekaman" }).click();
  await page.getByRole("option", { name: "10×/detik" }).click();
  await page.getByRole("button", { name: "Tambahkan ke antrean" }).click();

  await expect(page.getByRole("button", { name: "Pilih rekaman" })).toContainText("CAM-01");
  await expect(page.getByText("Menunggu giliran analisis", { exact: true })).toBeVisible();
  expect(fixture.createdCameraCount).toBe(0);
  expect(fixture.createdJobBody).toEqual({
    camera_id: "CAM-01",
    video_id: "VID-UPLOAD",
    recording_start: "2026-10-15T08:30",
    sample_rate: 10,
  });
});

test("mendaftar kamera baru dengan nama dan satu job rekaman", async ({ page }) => {
  const fixture = await openRecordedAnalysis(page);
  await page.getByRole("button", { name: "+ Tambah rekaman" }).click();
  await page.getByRole("button", { name: "Kamera baru" }).click();
  const form = page.locator(".rec-add-form");
  await form.getByRole("textbox", { name: "ID kamera" }).fill("CAM-BARU");
  await form.getByRole("textbox", { name: "Nama lokasi" }).fill("Area penyimpanan");
  await page.screenshot({ path: "e2e/screenshots/analisis-rekaman-kamera-baru.png", fullPage: true });
  await form.getByRole("button", { name: "Video rekaman" }).click();
  await page.getByRole("option", { name: /rekaman-terdaftar.mp4/ }).click();
  await form.locator('input[type="datetime-local"]').fill("2026-10-15T08:30");
  await form.getByRole("button", { name: "Tambahkan ke antrean" }).click();

  await expect(form).toHaveCount(0);
  expect(fixture.createdCameraCount).toBe(1);
  expect(fixture.createdCameraBody).toEqual({
    id: "CAM-BARU", name: "Area penyimpanan", source_kind: "video",
    video_id: "VID-TERDAFTAR", mode: "analisis", enabled: false,
  });
  expect(fixture.createdJobBody).toEqual({
    camera_id: "CAM-BARU", video_id: "VID-TERDAFTAR",
    recording_start: "2026-10-15T08:30", sample_rate: 5,
  });
});

test("kegagalan job setelah kamera didaftarkan dapat dicoba lagi tanpa menduplikasi kamera", async ({ page }) => {
  const fixture = await openRecordedAnalysis(page);
  fixture.failNextJob = true;
  page.on("console", (message) => {
    if (message.type() === "error" && message.text().includes("Failed to load resource: the server responded with a status of 400")) {
      browserErrors = browserErrors.filter((text) => text !== message.text());
    }
  });
  await page.getByRole("button", { name: "+ Tambah rekaman" }).click();
  await page.getByRole("button", { name: "Kamera baru" }).click();
  const form = page.locator(".rec-add-form");
  await form.getByRole("textbox", { name: "ID kamera" }).fill("CAM-ULANG");
  await form.getByRole("textbox", { name: "Nama lokasi" }).fill("Area ulang");
  await form.getByRole("button", { name: "Video rekaman" }).click();
  await page.getByRole("option", { name: /rekaman-terdaftar.mp4/ }).click();
  await form.locator('input[type="datetime-local"]').fill("2026-10-15T08:30");
  await form.getByRole("button", { name: "Tambahkan ke antrean" }).click();
  await expect(page.getByText(/Kamera CAM-ULANG berhasil didaftarkan, tetapi analisis gagal dibuat/)).toBeVisible();
  await expect(form.getByRole("button", { name: "Kamera terdaftar" })).toHaveAttribute("aria-pressed", "true");
  await form.getByRole("button", { name: "Tambahkan ke antrean" }).click();
  await expect(form).toHaveCount(0);
  expect(fixture.createdCameraCount).toBe(1);
  expect(fixture.createdJobBody).toMatchObject({ camera_id: "CAM-ULANG" });
});

test("ID kamera baru yang sudah terdaftar ditolak tanpa request pembuatan", async ({ page }) => {
  const fixture = await openRecordedAnalysis(page);
  await page.getByRole("button", { name: "+ Tambah rekaman" }).click();
  await page.getByRole("button", { name: "Kamera baru" }).click();
  const form = page.locator(".rec-add-form");
  await form.getByRole("textbox", { name: "ID kamera" }).fill("CAM-01");
  await form.getByRole("textbox", { name: "Nama lokasi" }).fill("Duplikat");
  await form.getByRole("button", { name: "Video rekaman" }).click();
  await page.getByRole("option", { name: /rekaman-terdaftar.mp4/ }).click();
  await form.locator('input[type="datetime-local"]').fill("2026-10-15T08:30");
  await form.getByRole("button", { name: "Tambahkan ke antrean" }).click();
  await expect(page.getByText(/ID kamera CAM-01 sudah terdaftar/)).toBeVisible();
  expect(fixture.createdCameraCount).toBe(0);
  expect(fixture.createdJobBody).toBeNull();
});

test("halaman dan kontrol analisis rekaman tidak tersedia untuk viewer", async ({ page }) => {
  await installRecordedAnalysisFixture(page);
  await login(page, "viewer");
  browserErrors = [];
  await expect(page.locator("header nav").getByRole("button", { name: "Analisis rekaman", exact: true })).toHaveCount(0);
  await page.goto("/#analisis-rekaman");
  await expect(page.getByRole("heading", { name: "Analisis rekaman" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: /^(Jeda|Lanjutkan|Batalkan)$/ })).toHaveCount(0);
});