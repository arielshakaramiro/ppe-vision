import { expect, test, type Page } from "@playwright/test";
import { login, openTab } from "./helpers";

test.use({ viewport: { width: 1440, height: 900 } });

const cameras = [
  {
    id: "CAM-UJI-01", name: "Kamera uji satu", source_kind: "stream", sumber: "rtsp://***@uji/satu",
    mode: "", video_id: "", recording_start: null, sample_rate: 10, enabled: true, fps: 12, running: true,
    pemeriksaan_per_detik: 10,
  },
  {
    id: "CAM-UJI-02", name: "Kamera uji dua", source_kind: "video", sumber: "uji.mp4",
    mode: "analisis", video_id: "VID-UJI", recording_start: "2026-10-10T06:00:00", sample_rate: 5,
    enabled: true, fps: 25, running: false, pemeriksaan_per_detik: 5,
  },
];

const stats = {
  pekerja_terpantau: 37,
  pekerja_patuh: 29,
  pekerja_melanggar: 8,
  pekerja_tidak_pasti: 3,
  compliance_rate: 78.4,
  event_pelanggaran: 13,
  event_tidak_pasti: 3,
  menunggu_review: 5,
  kepatuhan_per_apd: [
    { item: "helmet", label: "Helm", patuh: 35, total: 37, persen: 94.6 },
    { item: "vest", label: "Rompi", patuh: 33, total: 37, persen: 89.2 },
    { item: "gloves", label: "Sarung tangan", patuh: 26, total: 37, persen: 70.3 },
    { item: "shoes", label: "Sepatu", patuh: 31, total: 37, persen: 83.8 },
  ],
  per_jam: Array.from({ length: 24 }, (_, jam) => ({ jam, jumlah: jam >= 6 && jam <= 19 ? (jam % 5) + 1 : 0, event: 0 })),
  per_hari: [
    { tanggal: "2026-10-10", jumlah: 2, event: 3 },
    { tanggal: "2026-10-11", jumlah: 4, event: 5 },
    { tanggal: "2026-10-12", jumlah: 1, event: 2 },
  ],
  per_kamera: [
    { kamera: "CAM-UJI-01", jumlah: 5, event: 8 },
    { kamera: "CAM-UJI-02", jumlah: 3, event: 5 },
  ],
  per_zona: [
    { zona: "Area uji", jumlah: 6, event: 9 },
    { zona: "Gudang uji", jumlah: 2, event: 4 },
  ],
  jam_rawan: [9, 15],
  per_shift: [
    { shift: "Pagi", pekerja: 24, compliance_rate: 83.3, pekerja_melanggar: 4, pelanggaran: 7, menit_paparan: 12.5 },
    { shift: "Siang", pekerja: 13, compliance_rate: 69.2, pekerja_melanggar: 4, pelanggaran: 6, menit_paparan: 8.75 },
  ],
  menit_paparan: 21.25,
  menit_paparan_per_apd: [
    { item: "helmet", label: "Helm", menit: 2.5 },
    { item: "vest", label: "Rompi", menit: 4.25 },
    { item: "gloves", label: "Sarung tangan", menit: 9.5 },
    { item: "shoes", label: "Sepatu", menit: 5 },
  ],
  rata_durasi_pelanggaran_detik: 41,
};

async function installAnalyticsFixture(page: Page) {
  const statsRequests: URL[] = [];
  await page.route("**/api/cameras", (route) => route.fulfill({ json: cameras }));
  await page.route("**/api/stats?*", (route) => {
    statsRequests.push(new URL(route.request().url()));
    return route.fulfill({ json: stats });
  });
  return statsRequests;
}

async function openAnalytics(page: Page) {
  await login(page, "admin");
  await openTab(page, "Analitik");
  await expect(page.locator(".analytics-page")).toBeVisible();
}

test("Analitik menampilkan tata letak dan data API", async ({ page }) => {
  await installAnalyticsFixture(page);
  await openAnalytics(page);

  await expect(page.locator(".analytics-kpi")).toHaveCount(4);
  await expect(page.locator(".analytics-kpis")).toHaveCSS("grid-template-columns", /.+ .+ .+ .+/);
  await expect(page.locator(".analytics-grid")).toHaveCSS("grid-template-columns", /.+ .+/);
  await expect(page.locator(".analytics-kpi").nth(0)).toContainText("78,4%");
  await expect(page.locator(".analytics-kpi").nth(0)).toContainText("29 dari 37 pekerja patuh");
  await expect(page.locator(".analytics-kpi").nth(1)).toContainText("21,3 mnt");
  await expect(page.locator(".analytics-kpi").nth(2)).toContainText("dari 13 event · 3 tidak pasti");
  await expect(page.getByRole("link", { name: "Buka antrean review" })).toHaveAttribute("href", "#evidence");

  await expect(page.locator(".analytics-chart svg")).toHaveCount(2);
  await expect(page.locator(".analytics-chart").first()).toContainText("06");
  await expect(page.locator(".analytics-daily")).toContainText("10/10");
  await page.locator(".analytics-chart").first().locator(".recharts-bar-rectangle").first().hover();
  await expect(page.locator(".recharts-tooltip-wrapper").first()).toContainText("Pekerja melanggar");
  await expect(page.locator(".analytics-breakdown")).toContainText("Per shift");
  await expect(page.locator(".analytics-breakdown")).toContainText("Per kamera & zona");
  await expect(page.locator(".analytics-breakdown")).toContainText("Kamera uji satu");
  await expect(page.locator(".analytics-breakdown")).toContainText("Area uji");

  const dailyBox = await page.locator(".analytics-daily").boundingBox();
  const breakdownBox = await page.locator(".analytics-breakdown").boundingBox();
  expect(dailyBox).toBeTruthy();
  expect(breakdownBox).toBeTruthy();
  expect(dailyBox!.y).toBeCloseTo(breakdownBox!.y, 0);
  expect(dailyBox!.width).toBeCloseTo(breakdownBox!.width, 0);
  await page.screenshot({ path: "e2e/screenshots/analitik-desktop.png", fullPage: true });
});

test("filter periode, sumber, dan kamera tetap diteruskan ke API", async ({ page }) => {
  const requests = await installAnalyticsFixture(page);
  await openAnalytics(page);
  await expect.poll(() => requests.length).toBeGreaterThanOrEqual(1);
  expect(requests.at(-1)?.searchParams.get("period")).toBe("7_hari");
  expect(requests.at(-1)?.searchParams.get("source")).toBe("nyata");

  await page.getByRole("button", { name: "30 hari", exact: true }).click();
  await expect.poll(() => requests.at(-1)?.searchParams.get("period")).toBe("30_hari");

  await page.getByRole("button", { name: "Termasuk simulasi", exact: true }).click();
  await expect.poll(() => requests.at(-1)?.searchParams.get("source")).toBe("semua");
  await expect(page.getByText("Termasuk data dari video simulasi.", { exact: false })).toBeVisible();

  await page.getByRole("button", { name: "Kamera", exact: true }).click();
  await page.getByRole("option", { name: "Kamera uji dua" }).click();
  await expect.poll(() => requests.at(-1)?.searchParams.get("camera_ids")).toBe("CAM-UJI-02");
  await expect(page.getByText("Analitik difilter ke kamera Kamera uji dua.")).toBeVisible();

  await expect(page.getByRole("link", { name: "Laporan K3 (cetak / PDF)" })).toHaveAttribute("href", "/api/reports/k3?period=30_hari");
});

test("tabel kamera dan zona menampilkan keadaan kosong yang jujur", async ({ page }) => {
  await page.route("**/api/cameras", (route) => route.fulfill({ json: cameras }));
  await page.route("**/api/stats?*", (route) => route.fulfill({ json: { ...stats, per_kamera: [], per_zona: [] } }));
  await openAnalytics(page);

  await expect(page.getByText("Belum ada data kamera.")).toBeVisible();
  await expect(page.getByText("Belum ada data zona.")).toBeVisible();
});