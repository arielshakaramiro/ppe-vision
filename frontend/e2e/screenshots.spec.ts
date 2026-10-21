import { test, expect } from "@playwright/test";
import { login, openTab } from "./helpers";

const PAGES: [string, string][] = [
  ["Live", "live"],
  ["Analitik", "analitik"],
  ["Evidence", "evidence"],
  ["Intervensi", "intervensi"],
  ["Agent", "agent"],
  ["Sistem", "sistem"],
  ["Bisnis", "bisnis"],
  ["Audit", "audit"],
  ["Pengaturan", "pengaturan"],
  ["Pengguna", "pengguna"],
];

const DIR = "e2e/screenshots";

test.describe("Screenshot tiap halaman (admin)", () => {
  test("ambil screenshot semua halaman", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByRole("button", { name: "Masuk" })).toBeVisible();
    await page.screenshot({ path: `${DIR}/00-login.png`, fullPage: true });

    await login(page, "admin");
    for (const [label, slug] of PAGES) {
      await openTab(page, label);
      await expect(page.locator("main")).toBeVisible();
      await page.waitForTimeout(600);
      await page.screenshot({ path: `${DIR}/${slug}.png`, fullPage: true });
    }
  });
});

test.describe("Lebar 390px tanpa scroll horizontal", () => {
  test.use({ viewport: { width: 390, height: 844 } });

  test("tidak ada scroll horizontal di halaman utama", async ({ page }) => {
    await login(page, "admin");
    for (const [label] of PAGES) {
      await openTab(page, label);
      await expect(page.locator("main")).toBeVisible();
      await page.waitForTimeout(300);
      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
      );
      expect(overflow, `scroll horizontal di halaman ${label}`).toBeLessThanOrEqual(1);
      await page.screenshot({ path: `${DIR}/mobile-${label.toLowerCase()}.png`, fullPage: true });
    }
  });
});
