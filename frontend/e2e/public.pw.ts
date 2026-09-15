import { expect, test } from "@playwright/test";

test("public explorer works without a backend, writes, or an external basemap", async ({ page }) => {
  const requests: string[] = [];
  const writes: string[] = [];
  const errors: string[] = [];
  page.on("request", request => {
    requests.push(request.url());
    if (request.method() !== "GET") writes.push(request.url());
  });
  page.on("pageerror", error => errors.push(error.message));
  await page.route("**/tile.openstreetmap.org/**", route => route.abort());
  await page.goto("/");
  await expect(page.locator("#ranking-list .ranking-item")).toHaveCount(53);
  await expect(page.locator("#public-data-note")).toContainText("Data export:");
  await expect(page.locator("#map-add-station")).toBeHidden();
  await expect(page.locator(".scenario-controls")).toBeHidden();
  await expect(page.locator(".scenario-list-panel")).toBeHidden();
  await page.locator('[data-ranking="chargerDeficitScore"]').click();
  const firstDistrict = page.locator("#ranking-list .ranking-item").first();
  const districtName = await firstDistrict.locator(".ranking-name").innerText();
  await firstDistrict.focus();
  await page.keyboard.press("Enter");
  await expect(page.locator("#region-detail")).toContainText(districtName);
  await expect(page.locator("#region-detail .detail-evidence")).toHaveAttribute("open", "");
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(
    await page.evaluate(() => window.innerWidth)
  );
  await expect(page.locator("#region-detail .comparison-grid")).toBeHidden();
  await expect(page.locator("#map-catalog-link")).toHaveAttribute("href", "#sources");
  await expect(page.locator("#map-availability-note")).toContainText("fallback");
  expect(requests.filter(url => /geoserver|localhost:8000|unpkg\.com/.test(url))).toEqual([]);
  expect(writes).toEqual([]);
  expect(errors).toEqual([]);
  await page.screenshot({ path: `test-results/public-${test.info().project.name}.png`, fullPage: true });
});

test("missing district export fails visibly without contacting a backend", async ({ page }) => {
  const backendRequests: string[] = [];
  page.on("request", request => {
    if (request.url().includes("/geoserver/")) backendRequests.push(request.url());
  });
  await page.route("**/tile.openstreetmap.org/**", route => route.abort());
  await page.route("**/data/nrw_regions_sample.geojson", route => route.fulfill({ status: 404, body: "missing" }));
  await page.goto("/");
  await expect(page.locator("#data-quality-note")).toContainText("unavailable");
  await expect(page.locator("#ranking-list .ranking-item")).toHaveCount(0);
  expect(backendRequests).toEqual([]);
});
