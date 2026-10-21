/**
 * Smoke test UI — jaring pengaman selama migrasi tampilan.
 *
 * Yang dicek di SETIAP halaman yang bisa dibuka dari navigasi (termasuk menu akun):
 *   1. halaman terbuka tanpa error JavaScript (pageerror) dan tanpa console.error,
 *   2. konten utama tidak kosong,
 *   3. screenshot disimpan ke e2e/screens/<nama>.png untuk dibandingkan dengan desain,
 *   4. posisi navigasi header sama persis di semua halaman (di lebar 1440 dan 1075 px),
 *      supaya tombol menu tidak bergeser saat pindah halaman.
 *
 * Variabel lingkungan:
 *   E2E_URL   alamat aplikasi (default http://localhost:5173)
 *   E2E_USER  / E2E_PASS   akun untuk login (kosongkan kalau aplikasi berjalan di mode lokal tanpa akun)
 *   E2E_IGNORE  pola regex pesan console yang boleh diabaikan, misalnya "favicon|stream"
 *
 * Jalankan: npx playwright test e2e/smoke.spec.ts
 */
import { expect, test, type Page } from "@playwright/test";
import { USERS } from "./helpers";

const IGNORE = new RegExp(process.env.E2E_IGNORE || "a^");

async function login(page: Page) {
  const pass = page.locator('input[type="password"]');
  if (!(await pass.first().isVisible().catch(() => false))) return;
  await page.locator('input:not([type="password"]):not([type="hidden"])').first().fill(process.env.E2E_USER || USERS.admin.username);
  await pass.first().fill(process.env.E2E_PASS || USERS.admin.password);
  await pass.first().press("Enter");
  await expect(pass.first()).toBeHidden({ timeout: 15_000 });
}

/** Ambil semua tujuan navigasi: tombol/link di <nav> header + item menu akun. */
async function destinations(page: Page): Promise<string[]> {
  const names = new Set<string>();
  for (const el of await page.locator("header nav a, header nav button, nav a, nav button").all()) {
    const t = (await el.innerText().catch(() => "")).trim();
    if (t && t.length < 40) names.add(t);
  }
  const menu = page.locator('header details > summary, header [aria-haspopup="menu"], header [aria-label*="akun" i]').first();
  if (await menu.isVisible().catch(() => false)) {
    await menu.click();
    for (const el of await page.locator('[role="menuitem"]').all()) {
      const t = (await el.innerText().catch(() => "")).trim();
      if (t && !/keluar|logout|ganti password/i.test(t)) names.add(t);
    }
    await page.keyboard.press("Escape");
    await menu.click().catch(() => {});
  }
  return [...names];
}

async function openDestination(page: Page, name: string) {
  const inNav = page.locator("header nav, nav").getByText(name, { exact: true }).first();
  if (await inNav.isVisible().catch(() => false)) return inNav.click();
  const menu = page.locator('header details > summary, header [aria-haspopup="menu"], header [aria-label*="akun" i]').first();
  await menu.click();
  await page.getByRole("menuitem", { name, exact: true }).first().click();
}

test("semua halaman terbuka tanpa error", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
  page.on("console", (m) => { if (m.type() === "error" && !IGNORE.test(m.text())) errors.push(`console: ${m.text()}`); });

  await page.goto(process.env.E2E_URL || "/");
  await login(page);
  await page.waitForTimeout(1000);

  const pages = await destinations(page);
  expect(pages.length, "navigasi tidak ditemukan — cek selector header nav").toBeGreaterThan(0);

  const base = process.env.E2E_URL || "/";
  const report: string[] = [];
  const navPos = new Map<string, string[]>(); // "lebar → posisi" : daftar halaman
  const WIDTHS = [1440, 1075];
  const measureNav = async (name: string) => {
    const size = page.viewportSize();
    for (const w of WIDTHS) {
      await page.setViewportSize({ width: w, height: size?.height ?? 900 });
      // ukur dalam posisi gulir paling atas (beberapa halaman, misalnya chat, otomatis menggulir ke bawah)
      await page.evaluate(() => window.scrollTo(0, 0));
      await page.waitForTimeout(150);
      const r = await page.locator("header nav").first().boundingBox().catch(() => null);
      const key = `${w}px → ${r ? `x=${Math.round(r.x)} y=${Math.round(r.y)} lebar≈${Math.round(r.width / 4) * 4}` : "nav tidak ada"}`;
      navPos.set(key, [...(navPos.get(key) ?? []), name]);
    }
    if (size) await page.setViewportSize(size);
  };
  for (const name of pages) {
    const before = errors.length;
    let problem = "";
    try {
      await openDestination(page, name);
      await page.waitForTimeout(1500);
      const text = (await page.locator("main").first().innerText().catch(() => "")).trim();
      if (text.length <= 20) problem = "konten utama kosong";
      await page.screenshot({ path: `e2e/screens/${name.replace(/[^\w-]+/g, "_")}.png`, fullPage: true });
      await measureNav(name);
    } catch (e) {
      problem = `tidak bisa dibuka: ${(e as Error).message.split("\n")[0]}`;
    }
    if (errors.length > before) problem = [problem, ...errors.slice(before)].filter(Boolean).join(" | ");
    report.push(`${problem ? "GAGAL" : "ok   "}  ${name}${problem ? "  → " + problem : ""}`);
    if (problem) {
      // halaman yang crash bisa membuat seluruh aplikasi kosong — muat ulang lalu lanjut ke halaman berikutnya
      await page.goto(base).catch(() => {});
      await login(page).catch(() => {});
      await page.waitForTimeout(1000);
    }
  }
  console.log("\nHasil smoke test:\n" + report.join("\n"));
  for (const w of WIDTHS) {
    const rows = [...navPos.entries()].filter(([k]) => k.startsWith(`${w}px`));
    if (rows.length > 1) report.push(`GAGAL  posisi navigasi berbeda di ${w}px:\n` + rows.map(([k, v]) => `         ${k}: ${v.join(", ")}`).join("\n"));
  }
  console.log(report.filter((r) => r.includes("posisi navigasi")).join("\n") || "Posisi navigasi sama di semua halaman.");
  expect(report.filter((r) => r.startsWith("GAGAL")), "ada halaman yang gagal — lihat daftar di atas").toEqual([]);
});
