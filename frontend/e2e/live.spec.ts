import { expect, test } from "@playwright/test";
import { login } from "./helpers";

test("kontrol Live: kamera, zoom dan geser, drawer, serta dock", async ({ page }) => {
  await login(page, "admin");
  await expect(page.locator(".live-hud")).toBeVisible();

  const cameraPicker = page.locator(".live-camera-trigger");
  await cameraPicker.click();
  await expect(page.getByRole("dialog", { name: "Pilih kamera" })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(cameraPicker).toHaveAttribute("aria-expanded", "false");

  await page.getByRole("button", { name: "Perbesar" }).click();
  await expect(page.getByRole("button", { name: "Pilih ukuran zoom" })).toHaveText("150%");
  const resetZoom = page.locator(".live-zoom-reset");
  await expect(resetZoom).toBeVisible();
  await expect(resetZoom).toContainText("Kembali ke ukuran semula · 150%");
  const frame = page.locator(".live-stage-frame");
  const box = await frame.boundingBox();
  expect(box).toBeTruthy();
  await page.mouse.move(box!.x + box!.width / 2, box!.y + box!.height / 2);
  await page.mouse.down();
  await page.mouse.move(box!.x + box!.width / 2 + 60, box!.y + box!.height / 2 + 35);
  await page.mouse.up();
  await expect(page.locator(".live-stage")).toHaveAttribute("style", /\+ 60px.*\+ 35px/);
  await resetZoom.click();
  await expect(page.getByRole("button", { name: "Pilih ukuran zoom" })).toHaveText("100%");
  await expect(resetZoom).toHaveCount(0);
  await expect(page.locator(".live-stage")).toHaveAttribute("style", /\+ 0px.*\+ 0px/);

  for (const level of [25, 50, 75, 100, 150, 200, 400]) {
    await page.getByRole("button", { name: "Pilih ukuran zoom" }).click();
    await page.getByRole("menuitemradio", { name: new RegExp(`^${level}%`) }).click();
    await expect.poll(async () => (await page.locator(".live-stage").boundingBox())?.width).toBeCloseTo(box!.width * level / 100, 0);
    const before = await page.locator(".live-stage").boundingBox();
    expect(before).toBeTruthy();
    await page.mouse.move(box!.x + box!.width / 2, box!.y + box!.height / 2);
    await page.mouse.down();
    await page.mouse.move(box!.x + box!.width / 2 + 32, box!.y + box!.height / 2 + 28, { steps: 3 });
    await page.mouse.up();
    await expect.poll(async () => (await page.locator(".live-stage").boundingBox())?.x).toBeCloseTo(before!.x + 32, 0);
    await expect.poll(async () => (await page.locator(".live-stage").boundingBox())?.y).toBeCloseTo(before!.y + 28, 0);
  }
  await page.locator(".live-zoom-reset").click();

  const recentToggle = page.getByRole("button", { name: "Tampilkan pelanggaran terbaru" });
  await recentToggle.click();
  await expect(page.locator(".live-recent-drawer")).toHaveClass(/open/);
  const hideRecent = page.getByRole("button", { name: "Sembunyikan pelanggaran terbaru" });
  await expect(hideRecent).toHaveAttribute("aria-pressed", "true");
  await hideRecent.click();
  await expect(page.locator(".live-recent-drawer")).not.toHaveClass(/open/);
  await expect(recentToggle).toHaveAttribute("aria-pressed", "false");

  const panelToggle = page.locator(".live-strip-tools").getByRole("button", { name: "Sembunyikan panel bawah" });
  await panelToggle.click();
  await expect(page.locator(".live-hud")).toHaveClass(/dock-hidden/);
  const showPanel = page.locator(".live-strip-tools").getByRole("button", { name: "Tampilkan panel bawah" });
  await expect(showPanel).toHaveAttribute("aria-pressed", "true");
  await showPanel.click();
  await expect(page.locator(".live-hud")).not.toHaveClass(/dock-hidden/);
  await expect(panelToggle).toHaveAttribute("aria-pressed", "false");

  await page.locator(".live-dock-handle").click();
  await expect(page.locator(".live-hud")).toHaveClass(/dock-hidden/);
  await page.locator(".live-dock-show").click();
  await expect(page.locator(".live-hud")).not.toHaveClass(/dock-hidden/);

  await page.locator(".live-all-cameras").click();
  await expect(page.getByRole("heading", { name: "Semua kamera" })).toBeVisible();
  await page.getByRole("button", { name: "Kembali ke live" }).click();
  await expect(page.locator(".live-hud")).toBeVisible();

  await page.getByRole("button", { name: "Atur zona dan kamera" }).click();
  await expect(page).toHaveURL(/#pengaturan$/);
  await expect(page.getByRole("heading", { name: "Pengaturan deteksi" })).toBeVisible();
});

test("kontrol lokasi menjaga akses sesuai peran", async ({ page }) => {
  await login(page, "viewer");

  const locationButton = page.getByRole("button", { name: "Atur zona dan kamera" });
  await expect(locationButton).toBeVisible();
  await expect(locationButton).toBeDisabled();
  await expect(locationButton).toHaveAttribute("title", "Hanya admin yang dapat mengatur zona dan kamera");
  await expect(page).not.toHaveURL(/#pengaturan$/);
});

test("Semua kamera mengikuti toolbar grid referensi", async ({ page }) => {
  const cameraRows = [
    { id: "CAM-01", name: "Area pengemasan", source_kind: "video", sumber: "video", mode: "simulasi", video_id: "demo", recording_start: null, sample_rate: 10, enabled: true, fps: 24, running: true, pemeriksaan_per_detik: 9.8 },
    { id: "CAM-02", name: "Area las", source_kind: "stream", sumber: "stream", mode: "", video_id: "", recording_start: null, sample_rate: 10, enabled: true, fps: 18, running: true, pemeriksaan_per_detik: 8.4 },
    { id: "CAM-03", name: "Loading dock", source_kind: "stream", sumber: "stream", mode: "", video_id: "", recording_start: null, sample_rate: 10, enabled: true, fps: 0, running: false, pemeriksaan_per_detik: 0 },
  ];
  await page.route("**/api/cameras", (route) => route.fulfill({ json: cameraRows }));
  await page.route("**/api/stream.mjpg**", (route) => route.fulfill({
    contentType: "image/svg+xml",
    body: '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="9"><rect width="16" height="9" fill="#789"/></svg>',
  }));
  await page.route("**/api/zones*", (route) => route.fulfill({ json: [
    { name: "Produksi", camera_id: "CAM-01", polygon: [], required: ["helmet"], updated_at: "", updated_by: "" },
    { name: "Produksi", camera_id: "CAM-02", polygon: [], required: ["helmet"], updated_at: "", updated_by: "" },
    { name: "Logistik", camera_id: "CAM-03", polygon: [], required: ["vest"], updated_at: "", updated_by: "" },
  ] }));

  await login(page, "admin");
  await expect(page.locator(".live-camera-thumb.running.loaded")).toHaveCount(2);
  await expect(page.locator(".live-camera-thumb:not(.running) i")).toHaveCount(1);
  await page.locator(".live-camera-options > button").filter({ hasText: "CAM-02" }).click();
  await expect(page.locator(".live-camera-card")).toContainText("CAM-02 · Area las");
  await page.locator(".live-all-cameras").click();

  await expect(page.getByRole("heading", { name: "Semua kamera" })).toBeVisible();
  await expect(page.locator(".live-all-strip")).toContainText("3 kamera · 2 aktif · 1 terputus");
  await expect(page.locator(".camera-tile")).toHaveCount(3);
  await expect(page.getByRole("button", { name: "Buka CAM-01 Produksi" })).toContainText("Simulasi");
  await expect(page.getByRole("button", { name: "Buka CAM-03 Logistik" })).toContainText("Kamera terputus");
  await expect(page.getByAltText("Pratinjau CAM-01")).toHaveAttribute("src", "/api/stream.mjpg?camera=CAM-01");
  await expect(page.getByAltText("Pratinjau CAM-02")).toHaveAttribute("src", "/api/stream.mjpg?camera=CAM-02");
  await expect(page.getByAltText("Pratinjau CAM-03")).toHaveCount(0);

  const toolbox = page.locator(".live-all-view .live-dock");
  await expect(toolbox).toBeVisible();
  await expect(toolbox.locator(".live-camera-card")).toContainText("CAM-02 · Area las");
  await expect(toolbox.locator(".live-control-panel")).toContainText("Pekerja di frame");
  await expect(toolbox.locator(".live-control-panel")).toContainText("Pelanggaran terbaru");
  const crowdCard = toolbox.locator(".live-crowd-card");
  const crowdTitle = crowdCard.locator(".live-tile-head");
  const crowdBox = await crowdCard.boundingBox();
  const crowdTitleBox = await crowdTitle.boundingBox();
  expect(crowdBox).toBeTruthy();
  expect(crowdTitleBox).toBeTruthy();
  expect(crowdTitleBox!.y).toBeGreaterThanOrEqual(crowdBox!.y);
  expect(crowdTitleBox!.y + crowdTitleBox!.height).toBeLessThanOrEqual(crowdBox!.y + crowdBox!.height);
  await expect(toolbox.getByRole("button", { name: "Semua kamera" })).toHaveAttribute("aria-pressed", "true");
  const cameraGrid = page.locator(".live-camera-grid");
  const gridBox = await cameraGrid.boundingBox();
  const toolboxBox = await toolbox.boundingBox();
  expect(gridBox).toBeTruthy();
  expect(toolboxBox).toBeTruthy();
  expect(gridBox!.y + gridBox!.height).toBeLessThanOrEqual(toolboxBox!.y);

  await toolbox.getByRole("button", { name: "Sembunyikan panel bawah" }).click();
  await expect(page.locator(".live-all-view")).toHaveClass(/dock-hidden/);
  await expect(toolbox).toBeHidden();
  const expandedGridBox = await cameraGrid.boundingBox();
  expect(expandedGridBox).toBeTruthy();
  expect(expandedGridBox!.height).toBeGreaterThan(gridBox!.height);
  expect(expandedGridBox!.y + expandedGridBox!.height).toBeGreaterThan(gridBox!.y + gridBox!.height);
  await page.getByRole("button", { name: "Tampilkan panel bawah" }).click();
  await expect(page.locator(".live-all-view")).not.toHaveClass(/dock-hidden/);
  await expect(toolbox).toBeVisible();

  await page.getByRole("button", { name: "Terputus 1" }).click();
  await expect(page.locator(".camera-tile")).toHaveCount(1);
  await expect(page.locator(".camera-tile")).toContainText("CAM-03");

  await page.getByRole("button", { name: "Semua 3" }).click();
  await page.getByLabel("Saring berdasarkan zona").selectOption("Produksi");
  await expect(page.locator(".camera-tile")).toHaveCount(2);
  await expect(page.locator(".live-all-info")).toContainText("2 kamera");

  await page.getByRole("button", { name: "4 per baris" }).click();
  await expect(page.locator(".live-camera-grid")).toHaveCSS("--live-grid-columns", "4");
  await expect(page.getByRole("button", { name: "4 per baris" })).toHaveAttribute("aria-pressed", "true");
});

test("bagian atas Live menampilkan tata letak dan kontrol", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await login(page, "admin");

  const header = page.locator(".av-header");
  const strip = page.locator(".live-strip");
  const nav = page.locator(".av-header .av-nav");
  const tools = page.locator(".live-strip-tools");
  const headerBox = await header.boundingBox();
  const stripBox = await strip.boundingBox();
  const toolsBox = await tools.boundingBox();

  expect(headerBox).toBeTruthy();
  expect(stripBox).toBeTruthy();
  expect(toolsBox).toBeTruthy();
  expect(headerBox!.y).toBeCloseTo(0, 0);
  expect(headerBox!.height).toBeCloseTo(86, 0);
  expect(stripBox!.x).toBeCloseTo(24, 0);
  expect(stripBox!.y).toBeCloseTo(82, 0);
  expect(toolsBox!.x + toolsBox!.width).toBeCloseTo(1416, 0);

  await expect(nav).toHaveCSS("border-radius", "14px");
  await expect(page.locator(".live-camera-trigger")).toHaveCSS("border-radius", "12px");
  await expect(tools).toHaveCSS("border-radius", "14px");
  await expect(tools.locator("svg")).toHaveCount(3);
  await expect(page.locator(".live-summary-chip")).toContainText("ada pelanggaran");
  await expect(page.locator(".live-pipeline-chip")).toContainText(/Pipeline (berjalan|berhenti) · model/);

  const zoom = page.locator(".live-zoom");
  const zoomBox = await zoom.boundingBox();
  expect(zoomBox).toBeTruthy();
  expect(zoomBox!.width).toBeCloseTo(58, 0);
  expect(zoomBox!.height).toBeCloseTo(154, 0);
  await expect(zoom).toHaveCSS("border-radius", "14px");
  await expect(zoom).toHaveCSS("background-color", "rgba(255, 255, 255, 0.1)");
  await expect(page.getByRole("button", { name: "Perbesar" })).toHaveCSS("border-radius", "10px");
  await expect(page.getByRole("button", { name: "Pilih ukuran zoom" })).toHaveCSS("border-radius", "8px");
  await expect(page.getByRole("button", { name: "Pilih ukuran zoom" })).toHaveCSS("height", "26px");
  await expect(page.locator(".live-zoom-menu")).toHaveCSS("width", "200px");
});

test("kotak pekerja membuka dan menutup kartu detail", async ({ page }) => {
  await page.route("**/api/stream.mjpg**", (route) => route.abort());
  await page.route("**/api/live*", async (route) => {
    await route.fulfill({
      json: {
        running: true,
        error: null,
        fps: 12.4,
        frame: { width: 1920, height: 1080 },
        people: [{
          track_id: 7,
          worker_id: 7,
          pekerja: "W007",
          status: "violation",
          zone: "Area Produksi",
          checklist: { helmet: "missing", vest: "present", gloves: "unknown", shoes: "not_required" },
          box: { x: 20, y: 18, w: 16, h: 64 },
          confidence: { vest: 0.85 },
          since: "2026-10-15T08:12:03",
        }, {
          track_id: 8,
          worker_id: 8,
          pekerja: "W008",
          status: "compliant",
          zone: "Area Produksi",
          checklist: { helmet: "present", vest: "present", gloves: "not_required", shoes: "not_required" },
          box: { x: 40, y: 20, w: 14, h: 60 },
          confidence: { helmet: 0.92, vest: 0.9 },
          since: "2026-10-15T08:12:08",
        }],
        kpi: { pekerja_terpantau: 2, pelanggaran_aktif: 1, safety_score: 50 },
        recent: [],
      },
    });
  });

  await login(page, "admin");
  await expect(page.locator(".live-camera-trigger")).toContainText("CAM-01");
  await page.getByRole("button", { name: "Perbesar" }).click();
  await expect.poll(() => page.locator(".live-stage").evaluate(
    (stage) => Number(getComputedStyle(stage).getPropertyValue("--live-inverse-zoom")),
  )).toBeCloseTo(2 / 3, 6);
  const workerBox = page.getByRole("button", { name: "Buka detail pekerja W007" });
  await expect(workerBox).toBeVisible();
  await expect(workerBox).toHaveClass(/bad/);
  await expect(workerBox).toHaveAttribute("style", /left: 20%; top: 18%; width: 16%; height: 64%/);
  await expect(workerBox.locator(".av-box-label")).toHaveText("W007 ✗ H");
  await expect(workerBox.locator(".av-box-label")).toHaveCSS("border-radius", "999px");
  await expect(workerBox.locator(".av-box-label")).toHaveCSS("padding", "3px 9px");
  await expect(page.locator(".live-stage > img")).toHaveAttribute("src", /\/api\/stream\.mjpg\?.*camera=CAM-01.*clean=true/);
  const compliantBox = page.getByRole("button", { name: "Buka detail pekerja W008" });
  await expect(compliantBox).toHaveClass(/mute/);
  await expect(compliantBox).toHaveAttribute("style", /left: 40%; top: 20%; width: 14%; height: 60%/);
  await expect(compliantBox).toHaveCSS("color", "rgba(255, 255, 255, 0.5)");
  await expect(compliantBox.locator(".av-box-label")).toHaveText("W008");
  await workerBox.click();

  const workerCard = page.getByRole("region", { name: "Detail pekerja W007" });
  await expect(workerCard).toBeVisible();
  await expect(workerCard).toContainText("Helm");
  await expect(workerCard).toContainText("tidak dipakai");
  await page.getByRole("button", { name: "Pilih ukuran zoom" }).click();
  await page.getByRole("menuitemradio", { name: "25%" }).click();
  await expect(page.locator(".live-worker-card-layer")).toHaveClass(/zoomed-out/);
  const frameBox = await page.locator(".live-stage-frame").boundingBox();
  expect(frameBox).toBeTruthy();
  await expect.poll(async () => (await page.locator(".live-stage").boundingBox())?.width).toBeCloseTo(frameBox!.width * .25, 0);
  const workerBoxAtSmallZoom = await workerBox.boundingBox();
  expect(workerBoxAtSmallZoom).toBeTruthy();
  await page.mouse.move(workerBoxAtSmallZoom!.x + workerBoxAtSmallZoom!.width / 2, workerBoxAtSmallZoom!.y + workerBoxAtSmallZoom!.height / 2);
  await page.mouse.down();
  await page.mouse.move(workerBoxAtSmallZoom!.x + workerBoxAtSmallZoom!.width / 2 + 40, workerBoxAtSmallZoom!.y + workerBoxAtSmallZoom!.height / 2 + 20);
  await page.mouse.up();
  await expect(page.locator(".live-stage")).toHaveAttribute("style", /\+ 40px.*\+ 20px/);
  await expect(page.getByRole("region", { name: "Detail pekerja W007" })).toHaveCount(1);
  const stageBox = await page.locator(".live-stage").boundingBox();
  const workerCardBox = await workerCard.boundingBox();
  expect(stageBox).toBeTruthy();
  expect(workerCardBox).toBeTruthy();
  expect(workerCardBox!.x + workerCardBox!.width <= stageBox!.x || workerCardBox!.x >= stageBox!.x + stageBox!.width).toBeTruthy();
  await page.getByRole("button", { name: "Tutup kartu pekerja" }).click();
  await expect(page.locator(".live-worker-card-layer")).toHaveClass(/closing/);
  await expect(workerCard).toHaveCount(0, { timeout: 1_000 });
});

test("artwork APD tampil pada posisi yang benar", async ({ page }) => {
  await login(page, "admin");

  const helmetCard = page.locator(".live-control-panel .sign").filter({ hasText: "Helm" });
  const helmetArtwork = helmetCard.locator(".apd-artwork");
  await expect(helmetArtwork).toHaveAttribute("viewBox", "0 0 80 80");
  await expect(helmetArtwork.locator('path[d="M40 2v6M40 72v6M2 40h6M72 40h6"]')).toHaveCount(1);

  const cardBox = await helmetCard.boundingBox();
  const artworkBox = await helmetArtwork.boundingBox();
  expect(cardBox).toBeTruthy();
  expect(artworkBox).toBeTruthy();
  expect(artworkBox!.width).toBeCloseTo(58, 0);
  expect(artworkBox!.height).toBeCloseTo(58, 0);
  expect(artworkBox!.x + artworkBox!.width / 2).toBeCloseTo(cardBox!.x + cardBox!.width / 2, 0);
  expect(artworkBox!.y + artworkBox!.height / 2 - cardBox!.y).toBeCloseTo(59, 0);

  const completeArtwork = page.locator(".apd-preset-artwork").first();
  const completeBox = await completeArtwork.boundingBox();
  expect(completeBox).toBeTruthy();
  expect(completeBox!.width).toBeCloseTo(50, 0);
  expect(completeArtwork.locator('rect[stroke-dasharray="1.5 4"]')).toHaveCount(1);
});

test("preset lengkap menyimpan empat APD untuk zona kamera aktif", async ({ page }) => {
  let required = ["helmet", "vest"];
  const zone = { name: "Area Produksi", camera_id: "CAM-01", polygon: [[0, 0], [1, 0], [1, 1]], updated_at: "", updated_by: "" };
  await page.route("**/api/zones**", async (route) => {
    if (route.request().method() === "PATCH") {
      const body = route.request().postDataJSON();
      expect(body).toEqual({ required: ["helmet", "vest", "gloves", "shoes"] });
      required = body.required;
      await route.fulfill({ json: { ...zone, required } });
      return;
    }
    await route.fulfill({ json: [{ ...zone, required }] });
  });

  await login(page, "admin");
  const complete = page.locator(".live-control-panel .preset").filter({ hasText: "Lengkap" });
  const request = page.waitForRequest((candidate) => candidate.method() === "PATCH" && candidate.url().includes("/api/zones/Area%20Produksi/required?camera_id=CAM-01"));
  await complete.click();
  await request;
  await expect(complete).toHaveClass(/on/);
});