import { expect, test } from "@playwright/test";
import { login, openTab } from "./helpers";

test.describe("Halaman admin", () => {
  test.beforeEach(async ({ page }) => {
    await login(page, "admin");
  });

  test("Audit menampilkan filter dan urutan kolom yang benar", async ({ page }) => {
    await openTab(page, "Audit");
    await expect(page.getByRole("heading", { name: "Audit log" })).toBeVisible();
    await expect(page.locator(".audit-table thead th")).toHaveText(["Waktu", "Aktor", "Aksi", "Detail", "Event"]);
    await expect(page.getByRole("button", { name: "Filter aktor" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Filter aksi" })).toBeVisible();

    const todayRequest = page.waitForRequest((request) => request.url().includes("/api/audit?") && request.url().includes("period=hari_ini"));
    await page.getByRole("button", { name: "Hari ini", exact: true }).click();
    await todayRequest;
    await expect(page.getByRole("button", { name: "Hari ini", exact: true })).toHaveClass(/on/);
  });

  test("Sistem menampilkan urutan bagian dan aksi admin", async ({ page }) => {
    await openTab(page, "Sistem");
    await expect(page.getByRole("heading", { name: "Kesehatan kamera" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Ekspor hasil review untuk training ulang" })).toBeVisible();

    const visualSectionOrder = await page.locator(".system-page h3").evaluateAll((headings) => headings
      .map((heading) => ({ text: heading.textContent?.trim() || "", y: heading.getBoundingClientRect().top }))
      .sort((left, right) => left.y - right.y)
      .map(({ text }) => text));
    expect(visualSectionOrder).toEqual([
      "Kesehatan kamera",
      "Kualitas model di lapangan",
      "Pemrosesan rekaman",
      "Kapasitas pemrosesan",
      "Pipeline",
    ]);
    await expect(page.locator(".system-capacity-kpis .av-kpi")).toHaveCount(3);
    await expect(page.getByRole("group", { name: "Simulasi jumlah kamera" })).toBeVisible();
    const qualityHeaders = page.locator(".system-quality-card thead th");
    await expect(qualityHeaders).toHaveText(["Versi", "Event", "Cakupan review", "Precision", "helm", "rompi", "sarung tangan", "sepatu"]);
    const numericHeaderAlignment = await page.locator(".system-quality-card thead th.num")
      .evaluateAll((headers) => headers.map((header) => getComputedStyle(header).textAlign));
    expect(numericHeaderAlignment).toEqual(Array(7).fill("right"));
    await expect(page.locator(".system-quality-card tbody tr").first().locator("td")).toHaveCount(8);
    await expect(page.locator(".system-jobs-table thead th")).toHaveText(["Rekaman", "Mulai rekaman", "Progres", "Event", ""]);
    await expect(page.locator(".system-jobs-card video")).toHaveCount(0);
    await expect(page.locator(".system-capacity-card thead th")).toHaveText(["Kamera", "Fps kamera", "Diperiksa", "Frame dilewati", "Keterangan"]);
    await expect(page.locator(".system-pipeline-card tbody tr").first()).toContainText("Status");
    await expect(page.locator(".system-pipeline-card tbody")).toContainText("Worker");
    const healthGrid = page.locator(".system-page > .cards-3");
    const healthCards = healthGrid.locator(".system-camera-card");
    await expect(healthCards).toHaveCount(3);
    const [gridBox, firstCardBox, lastCardBox] = await Promise.all([
      healthGrid.boundingBox(), healthCards.first().boundingBox(), healthCards.last().boundingBox(),
    ]);
    expect(gridBox && firstCardBox && lastCardBox).toBeTruthy();
    expect(Math.abs(firstCardBox!.x - gridBox!.x)).toBeLessThanOrEqual(1);
    expect(Math.abs((lastCardBox!.x + lastCardBox!.width) - (gridBox!.x + gridBox!.width))).toBeLessThanOrEqual(1);
  });

  test("Bisnis menghitung ulang, mereset contoh, dan dibuka dari posisi atas", async ({ page }) => {
    await openTab(page, "Sistem");
    await expect(page.getByRole("heading", { name: "Kesehatan kamera" })).toBeVisible();
    await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
    expect(await page.evaluate(() => window.scrollY)).toBeGreaterThan(0);

    await openTab(page, "Bisnis");
    await expect.poll(() => page.evaluate(() => window.scrollY)).toBe(0);
    await expect(page.locator(".business-form-grid input")).toHaveCount(10);
    await expect(page.locator(".business-kpis .av-kpi")).toHaveCount(4);

    const cameraInput = page.getByRole("spinbutton", { name: "Jumlah kamera" });
    await cameraInput.fill("8");
    await expect(page.locator(".business-kpis")).toContainText("Rp 15.000.000");
    await page.getByRole("button", { name: "Kembalikan ke contoh" }).click();
    await expect(cameraInput).toHaveValue("4");
    await expect(page.locator(".business-kpis")).toContainText("Rp 13.000.000");
  });

  test("Sistem, Bisnis, dan Audit tidak melebar di ponsel", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    for (const label of ["Sistem", "Bisnis", "Audit"]) {
      await openTab(page, label);
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      expect(overflow, `scroll horizontal di halaman ${label}`).toBeLessThanOrEqual(1);
    }
  });
});