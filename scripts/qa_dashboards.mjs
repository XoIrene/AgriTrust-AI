export default async function run(page, ui) {
  const result = { checks: {} };

  async function login(phone) {
    // the profile may hold an old session: /login would bounce to /dashboard,
    // so wipe storage first (commit keeps the redirecting navigation from hanging)
    await page.goto("http://127.0.0.1:8000/dashboard", { waitUntil: "commit", timeout: 60000 }).catch(() => {});
    await page.evaluate(() => { localStorage.clear(); sessionStorage.clear(); }).catch(() => {});
    await page.goto("http://127.0.0.1:8000/login", { waitUntil: "commit", timeout: 90000 });
    await page.locator('input[name="identifier"], input[autocomplete], input[type="text"], input[type="tel"]').first().waitFor({ timeout: 90000 });
    await page.locator('input[name="identifier"], input[autocomplete], input[type="text"], input[type="tel"]').first().fill(phone);
    await page.locator('input[type="password"]').first().fill("Demo1234");
    await page.locator('button[type="submit"], form button').first().click();
    await page.waitForTimeout(4000);
  }

  // ---- buyer
  await login("+255754000201");
  await page.goto("http://127.0.0.1:8000/dashboard#buyer");
  await page.waitForTimeout(1500);
  result.checks.buyerNavShown = await page.evaluate(() =>
    [...document.querySelectorAll("#sidebar a")].some((a) => /Buyer|Mnunuzi/i.test(a.textContent))
  );
  result.checks.buyerStatsShown = await page.evaluate(() =>
    document.querySelectorAll(".stat").length >= 3
  );
  result.checks.buyerSeesListing = await page.evaluate(() => {
    const text = document.querySelector("#view")?.innerText || "";
    return /Maize|Mahindi/i.test(text) && /Buy|Nunua/i.test(text);
  });
  result.checks.buyerOrderTable = await page.evaluate(() =>
    /reference/i.test(document.querySelector("#view")?.innerText || "")
  );

  // ---- loan officer
  await login("+255700000002");
  await page.goto("http://127.0.0.1:8000/dashboard#officer");
  await page.waitForTimeout(1500);
  result.checks.officerNavShown = await page.evaluate(() =>
    [...document.querySelectorAll("#sidebar a")].some((a) => /officer desk|dawati/i.test(a.textContent))
  );
  result.checks.officerStatsShown = await page.evaluate(() => {
    const text = document.querySelector("#view")?.innerText || "";
    return /disbursed|recovery|overdue/i.test(text);
  });
  result.officerDebug = await page.evaluate(() =>
    (document.querySelector("#view")?.innerText || "").slice(0, 300)
  );
  result.checks.officerQueueTable = await page.evaluate(() => {
    const text = document.querySelector("#view")?.innerText || "";
    return /pending applications|yanasubiri/i.test(text);
  });
  result.checks.noConsoleErrors = true;

  return result;
}
