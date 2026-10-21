import { test, expect } from "@playwright/test";
import { login, openAccountMenu, openTab } from "./helpers";

test.describe("Matriks peran", () => {
  test("viewer tidak melihat aksi yang butuh peran lebih tinggi", async ({ page }) => {
    await login(page, "viewer");
    await openAccountMenu(page);
    await expect(page.getByRole("menuitem", { name: "Pengguna" })).toHaveCount(0);
    await expect(page.getByRole("menuitem", { name: "Pengaturan" })).toHaveCount(0);
    await expect(page.getByRole("menuitem", { name: "Audit log" })).toHaveCount(0);
    await page.keyboard.press("Escape");

    await openTab(page, "Live");
    await expect(page.locator(".preset")).toHaveCount(0);
    await expect(page.locator(".sign").first()).toBeDisabled();

    await openTab(page, "Intervensi");
    await expect(page.getByRole("heading", { name: "Catat intervensi K3" })).toHaveCount(0);

    await openTab(page, "Evidence");
    const tile = page.locator(".tile").first();
    if (await tile.count()) {
      await tile.click();
      await expect(page.getByRole("dialog")).toBeVisible();
      await expect(page.getByRole("button", { name: "Benar pelanggaran" })).toHaveCount(0);
    }
  });

  test("supervisor melihat review, intervensi, audit, bukan ganti kamera, Pengaturan, atau Pengguna", async ({ page }) => {
    await login(page, "supervisor");
    await openAccountMenu(page);
    await expect(page.getByRole("menuitem", { name: "Pengguna" })).toHaveCount(0);
    await expect(page.getByRole("menuitem", { name: "Pengaturan" })).toHaveCount(0);
    await expect(page.getByRole("menuitem", { name: "Audit log" })).toBeVisible();
    await page.keyboard.press("Escape");

    await openTab(page, "Live");
    await expect(page.locator(".preset").first()).toBeEnabled();
    await expect(page.locator(".sign").first()).toBeEnabled();

    await openTab(page, "Intervensi");
    await expect(page.getByRole("heading", { name: "Catat intervensi K3" })).toBeVisible();
  });

  test("admin melihat semua aksi", async ({ page }) => {
    await login(page, "admin");
    await openAccountMenu(page);
    await expect(page.getByRole("menuitem", { name: "Pengguna" })).toBeVisible();
    await expect(page.getByRole("menuitem", { name: "Pengaturan" })).toBeVisible();
    await expect(page.getByRole("menuitem", { name: "Audit log" })).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(page.locator("header nav").getByRole("button", { name: "Analisis rekaman" })).toBeVisible();

    const menu = page.locator("header nav button");
    await expect(menu.nth(0)).toHaveText("Live");
    await expect(menu.nth(1)).toHaveText("Analisis rekaman");

    // Manajemen kamera (termasuk ganti sumber) ada di Pengaturan, bukan lagi di Live.
    await openTab(page, "Pengaturan");
    await expect(page.getByRole("button", { name: "Tambah kamera", exact: true })).toBeVisible();

    await openTab(page, "Live");
    await expect(page.locator(".preset").first()).toBeEnabled();
    await expect(page.locator(".sign").first()).toBeEnabled();

    await openTab(page, "Intervensi");
    await expect(page.getByRole("heading", { name: "Catat intervensi K3" })).toBeVisible();
  });
});
