/**
 * QA the farmer side: the overview's recent-transactions card and the full,
 * filterable transaction history.
 *   node <browser-automation>/browser.mjs http://127.0.0.1:8000 --script ./scripts/qa_farmer_history.mjs
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

  await login(process.env.QA_PHONE || "+255754000102");

  await page.goto(`${base}/dashboard#overview`);
  await page.waitForTimeout(3000);
  const overview = await viewText();
  result.checks.overviewHasRecent = /recent transactions|miamala ya karibuni/i.test(overview);
  result.checks.overviewHasSeeAll = /see all transactions|miamala yote/i.test(overview);
  result.checks.overviewRows = await page.evaluate(() =>
    document.querySelectorAll("#view table tbody tr").length);
  result.checks.background = await page.evaluate(() =>
    getComputedStyle(document.body).backgroundImage.slice(0, 110));
  const shotDir = process.env.QA_SHOT_DIR || "/tmp";
  await page.screenshot({ path: `${shotDir}/farmer_overview.png`, fullPage: true }).catch(() => {});

  await page.evaluate(() => { location.hash = "transactions"; });
  await page.waitForTimeout(3000);
  const history = await viewText();
  result.checks.historyTitle = /transaction history|historia ya miamala/i.test(history);
  result.checks.hasByType = /money by transaction type|fedha kwa aina/i.test(history);
  result.checks.rowCount = await page.evaluate(() =>
    document.querySelectorAll("#view table tbody tr").length);
  result.checks.exportButton = await page.evaluate(() =>
    [...document.querySelectorAll("#view button")].some((b) => /download csv|pakua csv/i.test(b.textContent)));
  result.checks.sample = history.slice(0, 200);
  await page.screenshot({ path: `${shotDir}/farmer_transactions.png`, fullPage: true }).catch(() => {});

  await page.evaluate(() => { location.hash = "weather"; });
  await page.waitForTimeout(3000);
  result.checks.weatherTitle = /weather|hali ya hewa/i.test(await viewText());
  await page.screenshot({ path: `${shotDir}/farmer_weather.png`, fullPage: true }).catch(() => {});

  return result;
}
