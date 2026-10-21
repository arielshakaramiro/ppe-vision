import { expect, test, type Page } from "@playwright/test";
import { login, openTab } from "./helpers";

async function openEvidence(page: Page) {
  await login(page, "viewer");
  await openTab(page, "Evidence");
  await expect(page.locator(".evidence-tile").first()).toBeVisible({ timeout: 15_000 });
  await page.locator(".evidence-tile").first().click();
  await page.getByRole("button", { name: "Buka foto penuh" }).click();
}

test("foto evidence lama tetap memakai gambar bukti tanpa kotak baru", async ({ page }) => {
  await openEvidence(page);
  await expect(page.getByRole("dialog", { name: "Pratinjau foto evidence" }).getByAltText("Foto evidence penuh")).toBeVisible();
  await expect(page.locator(".photo-detection-stage")).toHaveCount(0);
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog", { name: "Pratinjau foto evidence" })).toHaveCount(0);
  await expect(page.getByRole("dialog", { name: /Detail evidence/ })).toBeVisible();
});

test("foto dengan deteksi menggambar kotak APD nyata saja", async ({ page }) => {
  await page.route("**/api/violations?**", async (route) => {
    const response = await route.fetch();
    const body = await response.json();
    if (!body.items?.length) return route.fulfill({ response });
    const withDetection = (evidence: typeof body.items[number]) => ({
      ...evidence,
      foto_clean: evidence.foto,
      deteksi: {
        frame: { width: 800, height: 600 },
        worker: { box: { x: 25, y: 17, w: 30, h: 70 }, confidence: 0.9 },
        items: [
          { key: "helmet", status: "missing", boxes: [] },
          { key: "vest", status: "present", boxes: [{ box: { x: 29, y: 34, w: 15, h: 27 }, confidence: 0.82 }] },
          { key: "gloves", status: "unknown", boxes: [] },
          { key: "shoes", status: "not_required", boxes: [] },
        ],
      },
    });
    body.items = body.items.map((entry: typeof body.items[number]) => ({
      ...withDetection(entry),
      ...(entry.event_bukti?.length ? { event_bukti: entry.event_bukti.map(withDetection) } : {}),
    }));
    await route.fulfill({ response, json: body });
  });

  await openEvidence(page);
  const viewer = page.getByRole("dialog", { name: "Pratinjau foto evidence" });
  await expect(viewer.locator(".photo-detection-stage img")).toBeVisible();
  const stage = await viewer.locator(".photo-detection-stage").boundingBox();
  const image = await viewer.locator(".photo-detection-stage img").boundingBox();
  expect(stage && image).toBeTruthy();
  expect(Math.abs(stage!.width - image!.width)).toBeLessThan(2);
  expect(Math.abs(stage!.height - image!.height)).toBeLessThan(2);
  await expect(viewer.locator(".av-box")).toHaveCount(1);
  await expect(viewer.locator(".av-ppe")).toHaveCount(1);
  await expect(viewer.locator(".av-ppe")).toContainText("Rompi ✓");
  await expect(viewer.locator(".photo-detection-card")).toContainText("tidak dipakai");
  await expect(viewer.locator(".photo-detection-card")).toContainText("tidak pasti");
  await expect(viewer.locator(".photo-detection-card")).toContainText("tidak dicek di zona ini");
  await expect(viewer.locator(".av-meter-track")).toHaveCount(1);
  await page.setViewportSize({ width: 600, height: 800 });
  await expect(viewer.locator(".photo-detection-stage img")).toBeVisible();
  await expect(viewer.locator(".photo-detection-card")).toBeVisible();
});
