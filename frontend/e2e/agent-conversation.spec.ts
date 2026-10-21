import { expect, test, type Page } from "@playwright/test";

async function openAgent(page: Page) {
  await page.goto("http://127.0.0.1:8099/#agent");
  const password = page.locator('input[type="password"]').first();
  if (await password.isVisible().catch(() => false)) {
    await page.locator('input:not([type="password"]):not([type="hidden"])').first().fill(process.env.E2E_USER || "viewer_e2e");
    await password.fill(process.env.E2E_PASS || "rahasia-e2e-2026");
    await password.press("Enter");
    await expect(password).toBeHidden();
  }
  await expect(page.locator(".agent-shell")).toBeVisible({ timeout: 15_000 });
}

test("multi-percakapan agent: buat, auto-title, persistensi, dan hapus", async ({ page }) => {
  await openAgent(page);

  // 1. Klik Percakapan baru
  await page.getByRole("button", { name: "Percakapan baru" }).click();
  await expect(page.locator(".agent-main-title")).toHaveText("Percakapan baru");

  // 2. Ajukan pertanyaan
  const textarea = page.getByRole("textbox", { name: "Pertanyaan untuk agent" });
  await textarea.fill("Berapa pelanggaran helm hari ini?");
  await page.getByRole("button", { name: "Kirim pertanyaan" }).click();

  // 3. Verifikasi bubble user & balasan assistant
  await expect(page.locator(".msg.user")).toContainText("Berapa pelanggaran helm hari ini?");
  await expect(page.locator(".msg.assistant.agent-answer").first()).toBeVisible({ timeout: 30_000 });
  await expect(page.locator(".agent-tool-chip").first()).toBeVisible();
  await expect(page.locator(".agent-answer-logo").first()).toHaveCSS("width", "32px");
  await expect(page.locator(".agent-answer-logo img").first()).toHaveCSS("width", "19px");

  // 4. Verifikasi auto-title terbentuk di sidebar dan header
  await expect(page.locator(".agent-side-item").first()).toContainText("Berapa pelanggaran helm");
  await expect(page.locator(".agent-main-title")).toContainText("Berapa pelanggaran helm");

  // 5. Muat ulang halaman (reload) dan pastikan percakapan tetap tersimpan
  await page.reload();
  await expect(page.locator(".agent-shell")).toBeVisible({ timeout: 15_000 });
  await expect(page.locator(".agent-side-item").first()).toContainText("Berapa pelanggaran helm");
  await expect(page.locator(".msg.user")).toContainText("Berapa pelanggaran helm hari ini?");
  await expect(page.locator(".agent-tool-chip").first()).toBeVisible();

  // 6. Sematkan (pin) percakapan
  const pinBtn = page.locator('button[aria-label="Sematkan"]').first();
  if (await pinBtn.isVisible()) {
    await pinBtn.click();
    await expect(page.locator(".agent-side-act.pinned")).toBeVisible();
  }

  // 7. Cari percakapan via filter
  const searchInput = page.getByRole("textbox", { name: "Cari percakapan" });
  await searchInput.fill("Helm");
  await expect(page.locator(".agent-side-item")).toHaveCount(1);
  await searchInput.fill("KataKunciPastiTidakAda12345");
  await expect(page.locator(".agent-side-empty")).toContainText("Tidak ada percakapan");
  await searchInput.fill("");

  // 8. Hapus percakapan dengan konfirmasi dialog
  page.on("dialog", (dialog) => dialog.accept());
  await page.locator('button[aria-label="Hapus percakapan"]').first().click();
  await expect(page.locator(".agent-empty")).toBeVisible({ timeout: 5_000 });
});

test("layout agent mengikuti referensi desktop dan drawer seluler", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await openAgent(page);
  await page.getByRole("button", { name: "Percakapan baru" }).click();

  const shell = page.locator(".agent-shell");
  const sidebar = page.locator(".agent-side");
  const emptyLogo = page.locator(".agent-empty-logo");
  const composer = page.locator(".agent-input-box");
  await expect(shell).toBeVisible();
  await expect(emptyLogo).toHaveAttribute("src", "/apd-vision/logo.svg");

  const desktopGeometry = await page.evaluate(() => {
    const rect = (selector: string) => {
      const box = document.querySelector(selector)!.getBoundingClientRect();
      return { x: box.x, width: box.width, height: box.height };
    };
    return {
      shell: rect(".agent-shell"),
      sidebar: rect(".agent-side"),
      main: rect(".agent-main"),
      logo: rect(".agent-empty-logo"),
      composer: rect(".agent-input-box"),
    };
  });
  expect(desktopGeometry.shell.x).toBeCloseTo(48, 0);
  expect(desktopGeometry.shell.width).toBeCloseTo(1344, 0);
  expect(desktopGeometry.shell.height).toBeCloseTo(832, 0);
  expect(desktopGeometry.sidebar.width).toBeCloseTo(272, 0);
  expect(desktopGeometry.main.x - desktopGeometry.sidebar.x - desktopGeometry.sidebar.width).toBeCloseTo(16, 0);
  expect(desktopGeometry.logo.width).toBeCloseTo(64, 0);
  expect(desktopGeometry.composer.width).toBeCloseTo(780, 0);

  await page.getByRole("button", { name: "Tutup panel" }).click();
  await expect(page.getByRole("button", { name: "Buka panel" })).toBeVisible();
  await page.getByRole("button", { name: "Buka panel" }).click();
  await expect(sidebar).toHaveCSS("opacity", "1");
  await page.screenshot({ path: "e2e/screenshots/agent-aligned.png", fullPage: true });

  await page.setViewportSize({ width: 390, height: 844 });
  const scrim = page.locator(".agent-side-scrim");
  await expect(scrim).toBeVisible();
  await expect(sidebar).toHaveCSS("transform", "none");
  await page.screenshot({ path: "e2e/screenshots/mobile-agent-aligned.png" });
  await scrim.click({ position: { x: 350, y: 300 } });
  await expect(page.getByRole("button", { name: "Buka panel" })).toBeVisible();
  await expect(sidebar).toHaveCSS("transform", "matrix(1, 0, 0, 1, -299.2, 0)");
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(1);
});
