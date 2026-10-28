/**
 * QA the loan officer desk: log in as the demo officer, then check the tabbed
 * workbench (queue, collections, institutions, portfolio) and the loan file.
 * Run against a local server:
 *   node <browser-automation>/browser.mjs http://127.0.0.1:8011 --script ./scripts/qa_officer_desk.mjs
 */
export default async function run(page, ui) {
  const base = process.env.QA_BASE || "http://127.0.0.1:8000";
  const result = { checks: {} };
  const viewText = () => page.evaluate(() => document.querySelector("#view")?.innerText || "");

  async function login(phone) {
    await page.goto(`${base}/dashboard`, { waitUntil: "commit", timeout: 60000 }).catch(() => {});
    await page.evaluate(() => { localStorage.clear(); sessionStorage.clear(); }).catch(() => {});
    await page.goto(`${base}/login`, { waitUntil: "commit", timeout: 60000 });
    const idInput = page.locator('input[name="identifier"], input[autocomplete], input[type="text"], input[type="tel"]').first();
    await idInput.waitFor({ timeout: 60000 });
    await idInput.fill(phone);
    await page.locator('input[type="password"]').first().fill("Demo1234");
    await page.locator('button[type="submit"], form button').first().click();
    await page.waitForTimeout(3500);
  }

  await login("+255700000002");
  await page.goto(`${base}/dashboard#officer`);
  await page.waitForTimeout(2500);

  result.checks.tabs = await page.evaluate(() =>
    [...document.querySelectorAll(".tab-btn")].map((b) => b.textContent.trim()));
  result.checks.queueTitle = /work queue|foleni ya kazi/i.test(await viewText());
  result.checks.theme = await page.evaluate(() => {
    const s = getComputedStyle(document.documentElement);
    return {
      brand: s.getPropertyValue("--brand").trim(),
      leaf: s.getPropertyValue("--leaf").trim(),
      harvest: s.getPropertyValue("--harvest").trim(),
      page: s.getPropertyValue("--page").trim(),
    };
  });
  result.checks.bodyBackground = await page.evaluate(() =>
    getComputedStyle(document.body).backgroundImage.slice(0, 60));

  const markers = {
    collections: /overdue|chelewa|outstanding|inayodaiwa/i,
    institutions: /bank|benki|sacco|mobile money|simu|gateway|malipo/i,
    portfolio: /approval|kuidhinisha|exposure|mkopo kwa taasisi|recovery|marejesho|par/i,
  };
  for (const tab of ["collections", "institutions", "portfolio"]) {
    await page.evaluate((t) => { location.hash = `officer/${t}`; }, tab);
    await page.waitForTimeout(2000);
    const text = await viewText();
    result.checks[`tab_${tab}`] = {
      textLen: text.length,
      hasMarkers: markers[tab].test(text),
    };
  }

  // open the first loan file, if the desk has any work
  await page.evaluate(() => { location.hash = "officer/queue"; });
  await page.waitForTimeout(1500);
  const openBtn = page.locator("#view button").filter({ hasText: /open file|fungua faili/i }).first();
  if (await openBtn.count()) {
    await openBtn.click();
    await page.waitForTimeout(3000);
    const dialog = await page.evaluate(() => document.querySelector("#dialog")?.innerText || "");
    result.checks.loanFile = {
      open: await page.evaluate(() => !!document.querySelector("#dialog[open]")),
      hasRequirements: /requirements|mahitaji/i.test(dialog),
      hasRisk: /risk|hatari/i.test(dialog),
      hasInstitutions: /institution|taasisi/i.test(dialog),
      hasSchedule: /repayment schedule|ratiba ya marejesho/i.test(dialog),
      sample: dialog.slice(0, 220),
    };
    await page.evaluate(() => document.querySelector("#dialog")?.close());
  } else {
    result.checks.loanFile = { open: false, note: "no applications on the desk to open" };
  }

  await page.evaluate(() => { location.hash = "officer/queue"; });
  await page.waitForTimeout(1500);
  await page.screenshot({ path: process.env.QA_SHOT || "/tmp/officer_desk.png", fullPage: true }).catch(() => {});
  return result;
}
