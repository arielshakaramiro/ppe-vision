import { Page, expect } from "@playwright/test";

export type Role = "viewer" | "supervisor" | "admin";

export const USERS: Record<Role, { username: string; password: string }> = {
  viewer: { username: "viewer_e2e", password: "rahasia-e2e-2026" },
  supervisor: { username: "supervisor_e2e", password: "rahasia-e2e-2026" },
  admin: { username: "admin_e2e", password: "rahasia-e2e-2026" },
};

/** Masuk lewat halaman login dan tunggu header dashboard muncul. */
export async function login(page: Page, role: Role) {
  const { username, password } = USERS[role];
  await page.goto("/");
  await page.getByRole("textbox", { name: "Nama pengguna" }).fill(username);
  await page.locator('input[type="password"]').fill(password);
  await page.getByRole("button", { name: "Masuk" }).click();
  await expect(page.locator("header nav")).toBeVisible();
}

/** Buka menu akun yang memuat profil, halaman administratif, dan aksi sesi. */
export async function openAccountMenu(page: Page) {
  const trigger = page.locator("header").getByRole("button", { name: /^Menu akun / });
  if (await trigger.getAttribute("aria-expanded") !== "true") await trigger.click();
  await expect(page.getByRole("menu", { name: "Menu akun" })).toBeVisible();
}

/** Buka satu tab di navigasi utama atau menu akun untuk halaman admin. */
export async function openTab(page: Page, label: string) {
  const navButton = page.locator("header nav").getByRole("button", { name: label, exact: true });
  if (await navButton.isVisible().catch(() => false)) {
    await navButton.click();
    return;
  }

  await openAccountMenu(page);
  const menuLabel = label === "Audit" ? "Audit log" : label;
  await page.getByRole("menuitem", { name: menuLabel, exact: true }).click();
}
