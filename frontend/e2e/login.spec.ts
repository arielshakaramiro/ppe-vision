import { test, expect } from "@playwright/test";
import { login, openAccountMenu, USERS } from "./helpers";

test.describe("Login", () => {
  test("gagal dengan password salah", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("textbox", { name: "Nama pengguna" }).fill(USERS.admin.username);
    await page.locator('input[type="password"]').fill("salah-sekali-2026");
    await page.getByRole("button", { name: "Masuk" }).click();
    await expect(page.getByRole("alert")).toBeVisible();
    await expect(page.locator("header nav")).toHaveCount(0);
  });

  test("berhasil dengan akun admin", async ({ page }) => {
    await login(page, "admin");
    await openAccountMenu(page);
    const accountMenu = page.getByRole("menu", { name: "Menu akun" });
    await expect(accountMenu.getByText("Masuk sebagai")).toBeVisible();
    await expect(accountMenu.getByText(USERS.admin.username)).toBeVisible();
  });

  test("keluar mengembalikan ke halaman login", async ({ page }) => {
    await login(page, "admin");
    await openAccountMenu(page);
    await page.getByRole("menuitem", { name: "Keluar" }).click();
    await expect(page.getByRole("button", { name: "Masuk" })).toBeVisible();
  });
});
