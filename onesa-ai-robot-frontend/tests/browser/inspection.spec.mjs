import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { cleanupOperatorSession } from "./session-cleanup.mjs";
const password =
  process.env.ROBOT_TEST_PASSWORD || "browser-test-operator-only";
test.afterEach(async ({ page, baseURL }) => {
  await page.unrouteAll({ behavior: "ignoreErrors" });
  await cleanupOperatorSession(page, baseURL);
});
async function signIn(page) {
  await page.goto("/console");
  await page
    .locator("#name")
    .fill(process.env.ROBOT_TEST_OPERATOR || "operator");
  await page.locator("#password").fill(password);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.locator("#operator")).toBeVisible({ timeout: 30000 });
  await page.locator("#stop").click();
  await expect(page.locator("#reset-form")).toBeVisible();
  await page.locator("#reset-password").fill(password);
  await page.locator("#reset-submit").click();
  await expect(page.locator("#mission-submit")).toBeEnabled();
}
async function plan(page, scenario) {
  await page.locator("#mission-scenario").selectOption(scenario);
  await page.locator("#mission-submit").click();
  await expect(page.locator("#mission-approval")).toBeVisible();
  await expect(page.locator("#mission-steps li")).toHaveCount(5);
  await page.locator("#mission-password").fill(password);
  await page.locator("#mission-approve-submit").click();
}
for (const [scenario, result, finding] of [
  ["normal", "Completed · normal readings", null],
  ["threshold", "Completed · findings to review", "threshold"],
  ["discrepancy", "Completed · findings to review", "discrepancy"],
  ["missing", "Completed · findings to review", "missing"],
  ["stale", "Completed · findings to review", "stale"],
  ["units", "Completed · findings to review", "unit mismatch"],
  ["interrupted", "Interrupted · not completed", null],
])
  test(`owned inspection ${scenario} has a report, export and command-free replay`, async ({
    page,
  }) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    await signIn(page);
    await plan(page, scenario);
    await expect(page.locator("#mission-outcome")).toHaveText(result, {
      timeout: 15000,
    });
    if (finding)
      await expect(page.locator("#mission-findings")).toContainText(finding);
    await expect(page.locator("#mission-replay")).toBeVisible();
    const mutations = [];
    page.on("request", (request) => {
      if (request.method() === "POST" && !request.url().endsWith("/heartbeat"))
        mutations.push(new URL(request.url()).pathname);
    });
    await page.locator("#replay-time").fill("0");
    await page.locator("#replay-time").fill("3");
    await expect(page.locator("#replay-play")).toBeDisabled();
    const download = page.waitForEvent("download");
    await page.locator("#mission-export").click();
    expect((await download).suggestedFilename()).toMatch(
      /^inspection-[a-f0-9-]{36}\.json$/,
    );
    expect(mutations).toEqual([]);
    await expect(page.locator("#mission-outcome")).toHaveText(result);
  });
for (const [width, mode] of [
  [1440, "light"],
  [390, "dark"],
  [320, "light"],
])
  test(`inspection keyboard and responsive layout ${width} ${mode}`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.emulateMedia({ colorScheme: mode, reducedMotion: "reduce" });
    await signIn(page);
    await plan(page, "discrepancy");
    await expect(page.locator("#mission-outcome")).toHaveText(
      "Completed · findings to review",
      { timeout: 15000 },
    );
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
    await page.locator("#replay-time").focus();
    await page.keyboard.press("Home");
    await expect(page.locator("#replay-time")).toBeFocused();
    await page.emulateMedia({
      colorScheme: mode === "dark" ? "light" : "dark",
    });
    await expect(page.locator("#replay-time")).toBeFocused();
    await expect
      .poll(() => page.evaluate(() => getComputedStyle(document.body).color))
      .toBe(mode === "dark" ? "rgb(16, 38, 62)" : "rgb(229, 237, 246)");
    await page.evaluate(
      () =>
        new Promise((resolve) =>
          requestAnimationFrame(() => requestAnimationFrame(resolve)),
        ),
    );
    expect(
      (
        await new AxeBuilder({ page })
          .withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"])
          .analyze()
      ).violations,
    ).toEqual([]);
  });
test("Stop stays available during an inspection approval response", async ({
  page,
}) => {
  await signIn(page);
  await page.locator("#mission-submit").click();
  await expect(page.locator("#mission-approval")).toBeVisible();
  let release;
  let approvals = 0;
  const held = new Promise((resolve) => (release = resolve));
  await page.route("**/missions/*/approve", async (route) => {
    approvals++;
    await held;
    await route.fulfill({
      status: 409,
      contentType: "application/json",
      body: '{"detail":"Synthetic delayed approval rejected"}',
    });
  });
  try {
    await page.locator("#mission-password").fill(password);
    await page.locator("#mission-approve-submit").click();
    await expect(page.locator("#mission-approve-submit")).toBeDisabled();
    // A fresh observation must preserve the pending form lock. Stop is independent.
    await page.waitForResponse((response) =>
      new URL(response.url()).pathname === "/api/v1/status" && response.ok(),
    );
    await expect(page.locator("#mission-approve-submit")).toBeDisabled();
    expect(approvals).toBe(1);
    await page.locator("#stop").click();
    await expect(page.locator("#mission-readiness")).toContainText("Stopped");
  } finally {
    release();
  }
});

test("older run disclosure and keyboard focus survive an unchanged history refresh", async ({page}) => {
  await signIn(page);
  await plan(page,"normal");
  await expect(page.locator("#mission-outcome")).toHaveText("Completed · normal readings",{timeout:15000});
  const current=await page.request.get("/api/v1/missions");
  expect(current.status()).toBe(200);
  const owned=(await current.json()).missions[0];
  // Read-only display fixture: only the first real owned record is opened.
  // Additional labels exercise history layout without creating journal rows.
  const fixture={missions:[owned,...Array.from({length:9},(_,i)=>({...owned,id:`00000000-0000-4000-8000-${String(i).padStart(12,"0")}`}))],history_window:"most_recent_50_tasks"};
  let reads=0;
  await page.route("**/api/v1/missions",async route=>{
    if(route.request().method()!=="GET")return route.continue();
    reads++;await route.fulfill({status:200,contentType:"application/json",body:JSON.stringify(fixture)});
  });
  const history=page.locator("#inspection-history");
  await expect(history.getByText("Show 4 older runs",{exact:true})).toBeVisible({timeout:20000});
  await history.locator("summary").click();
  const first=history.locator("button").first();
  await first.focus();
  const before=reads;
  await expect.poll(()=>reads,{timeout:20000}).toBeGreaterThan(before);
  await expect(first).toBeFocused();
  await expect(history.locator("details")).toHaveAttribute("open","");
  await expect(history.locator(":scope > li")).toHaveCount(7);
  // Restore this completed synthetic scenario's stopped state for later legacy
  // journeys. Logout revokes its session but is not a simulator reset request.
  await page.locator("#stop").click();
  await expect(page.locator("#stop-state")).toContainText("Stopped");
});
