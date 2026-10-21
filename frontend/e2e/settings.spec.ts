import { expect, test, type Page, type Request } from "@playwright/test";
import { USERS } from "./helpers";

const cameras = [
  { id: "CAM-01", name: "Gudang", source_kind: "stream", sumber: "rtsp://***@192.168.1.20/stream", mode: "", video_id: "", recording_start: null, sample_rate: 0, enabled: true, fps: 24, running: true, pemeriksaan_per_detik: 9.8 },
  { id: "CAM-02", name: "Area las", source_kind: "video", sumber: "rekaman-area-las.mp4", mode: "simulasi", video_id: "video-02", recording_start: null, sample_rate: 5, enabled: false, fps: 0, running: false, pemeriksaan_per_detik: 0 },
];
const zones = [
  { name: "Gudang", camera_id: "CAM-01", polygon: [[0.12, 0.15], [0.88, 0.15], [0.92, 0.86], [0.08, 0.86]], required: ["helmet", "vest"], updated_at: "2026-10-16T08:00:00", updated_by: "admin_e2e" },
  { name: "Area las", camera_id: "CAM-02", polygon: [[0.1, 0.1], [0.9, 0.1], [0.9, 0.9], [0.1, 0.9]], required: ["helmet", "vest", "gloves", "shoes"], updated_at: "2026-10-16T08:01:00", updated_by: "admin_e2e" },
];
const settings = {
  sensitivity: "seimbang",
  shifts: [
    { name: "Pagi", start: "06:00", end: "14:00" },
    { name: "Siang", start: "14:00", end: "22:00" },
    { name: "Malam", start: "22:00", end: "06:00" },
  ],
  alert: { telegram: true, cooldown_s: 120 },
  retention: { evidence_days: 90, quality_days: 30 },
  preset_values: {
    ketat: { present_threshold: 0.8, missing_threshold: -1, event_min_frames: 6 },
    seimbang: { present_threshold: 1, missing_threshold: -1.5, event_min_frames: 8 },
    longgar: { present_threshold: 1.3, missing_threshold: -2, event_min_frames: 12 },
  },
};

async function mockSettings(page: Page, mutations: Request[]) {
  await page.route("**/api/cameras**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (request.method() === "GET" && path === "/api/cameras") return route.fulfill({ json: cameras });
    if (request.method() === "GET" || !/^\/api\/cameras\/[^/]+$/.test(path)) return route.continue();
    mutations.push(request);
    const camera = cameras.find((item) => path.includes(item.id)) || cameras[0];
    return route.fulfill({ json: { ...camera, ...(request.postDataJSON() || {}) } });
  });
  await page.route("**/api/zones**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path !== "/api/zones" && !path.startsWith("/api/zones/")) return route.continue();
    if (request.method() === "GET") return route.fulfill({ json: zones });
    mutations.push(request);
    if (request.method() === "DELETE") return route.fulfill({ status: 204, body: "" });
    return route.fulfill({ status: request.method() === "POST" ? 201 : 200, json: { ...zones[0], ...(request.postDataJSON() || {}) } });
  });
  await page.route("**/api/settings", async (route) => {
    const request = route.request();
    if (new URL(request.url()).pathname !== "/api/settings") return route.continue();
    if (request.method() === "GET") return route.fulfill({ json: settings });
    mutations.push(request);
    return route.fulfill({ json: { ...settings, ...(request.postDataJSON() || {}) } });
  });
  await page.route("**/api/retention/simulate", async (route) => {
    mutations.push(route.request());
    return route.fulfill({ json: { message: "Simulasi selesai tanpa menghapus data." } });
  });
  await page.route("**/api/videos", (route) => route.fulfill({ json: [] }));
  await page.route("**/api/videos/server", (route) => route.fulfill({ json: [] }));
  await page.route("**/api/stream.mjpg**", (route) => route.abort());
}

async function loginToSettings(page: Page) {
  await page.goto("/#pengaturan");
  await page.getByRole("textbox", { name: "Nama pengguna" }).fill(USERS.admin.username);
  await page.locator('input[type="password"]').fill(USERS.admin.password);
  await page.getByRole("button", { name: "Masuk" }).click();
  await expect(page.getByRole("heading", { name: "Pengaturan deteksi" })).toBeVisible();
}

test.describe("Pengaturan", () => {
  test("komposisi desktop, batalkan, simpan, kamera, dan simulasi berfungsi", async ({ page }) => {
    const mutations: Request[] = [];
    await mockSettings(page, mutations);
    await loginToSettings(page);

    await expect(page.getByRole("heading", { name: "Pengaturan deteksi" })).toBeVisible();
    await expect(page.locator(".settings-camera-table tbody tr")).toHaveCount(2);
    await expect(page.locator(".settings-zone-item")).toHaveCount(2);
    await expect(page.locator(".settings-apd")).toHaveCount(4);
    await expect(page.locator(".settings-grid > .av-card")).toHaveCount(4);
    await expect(page.getByRole("button", { name: "Simpan perubahan" })).toBeDisabled();

    const heading = page.locator(".settings-heading");
    const cameraCard = page.locator(".settings-camera-card");
    await expect(heading).toHaveCSS("position", "static");
    const headingBox = await heading.boundingBox();
    const cameraBox = await cameraCard.boundingBox();
    expect(headingBox).not.toBeNull();
    expect(cameraBox).not.toBeNull();
    expect(headingBox!.y + headingBox!.height).toBeLessThan(cameraBox!.y);

    const zoneBox = await page.locator(".settings-zone-card").boundingBox();
    const editorBox = await page.locator(".settings-zone-editor").boundingBox();
    expect(zoneBox?.width).toBeGreaterThan(1200);
    expect(editorBox?.width).toBeGreaterThan(850);

    await page.getByRole("button", { name: "Longgar" }).click();
    await expect(page.getByText("12 frame", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: /^Sarung tangan (tidak )?wajib$/ }).click();
    await page.getByLabel("Mulai shift Pagi").fill("07:00");
    await page.getByLabel("Alert Telegram").click();
    await expect(page.getByRole("button", { name: "Simpan perubahan" })).toBeEnabled();

    await page.getByRole("button", { name: "Batalkan" }).click();
    await expect(page.getByRole("button", { name: "Seimbang" })).toHaveClass(/on/);
    await expect(page.getByLabel("Mulai shift Pagi")).toHaveValue("06:00");
    await expect(page.getByLabel("Alert Telegram")).toHaveAttribute("aria-checked", "true");
    await expect(page.getByRole("button", { name: "Simpan perubahan" })).toBeDisabled();

    await page.getByRole("button", { name: "Ketat" }).click();
    await page.getByLabel("Nama zona").fill("Gudang utama");
    await page.getByRole("button", { name: "Simpan perubahan" }).click();
    await expect.poll(() => mutations.filter((request) => request.method() === "PUT").length).toBe(2);
    const settingsRequest = mutations.find((request) => new URL(request.url()).pathname === "/api/settings" && request.method() === "PUT");
    const zoneRequest = mutations.find((request) => new URL(request.url()).pathname.startsWith("/api/zones/") && request.method() === "PUT");
    expect(settingsRequest?.postDataJSON().sensitivity).toBe("ketat");
    expect(zoneRequest?.postDataJSON().name).toBe("Gudang utama");
    expect(new URL(zoneRequest!.url()).pathname).toContain("Gudang");

    await page.locator(".settings-camera-table tbody tr").first().getByRole("switch").click();
    await expect.poll(() => mutations.some((request) => request.method() === "PATCH" && new URL(request.url()).pathname === "/api/cameras/CAM-01")).toBe(true);
    await page.getByRole("button", { name: "Ubah", exact: true }).first().click();
    await expect(page.getByRole("heading", { name: "Ubah kamera CAM-01" })).toBeVisible();
    await expect(page.getByLabel("ID kamera (mis. CAM-02)")).toBeDisabled();

    await page.getByRole("button", { name: "Simulasi (tanpa menghapus)" }).click();
    await expect(page.getByText("Simulasi selesai tanpa menghapus data.")).toBeVisible();
    await page.getByRole("button", { name: "Tutup" }).click();
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({ path: "e2e/screenshots/pengaturan-desktop.png", fullPage: true });
  });

  test("ponsel tidak memiliki overflow horizontal", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await mockSettings(page, []);
    await loginToSettings(page);
    await expect(page.locator(".settings-grid")).toHaveCSS("grid-template-columns", "358px");
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow).toBeLessThanOrEqual(1);
    await page.screenshot({ path: "e2e/screenshots/pengaturan-mobile.png", fullPage: true });
  });
});