export default async function run(page, ui) {
  const result = { checks: {} };

  // ---- login as the sw demo farmer
  await page.goto("http://127.0.0.1:8000/login");
  await page.locator('input[name="identifier"], input[autocomplete], input[type="text"], input[type="tel"]').first().fill("+255754000101");
  await page.locator('input[type="password"]').first().fill("Demo1234");
  await page.locator('button[type="submit"], form button').first().click();
  await page.waitForTimeout(1200);
  result.checks.loggedIn = !page.url().includes("/login");

  // ---- assistant page
  await page.goto("http://127.0.0.1:1:8000/dashboard#assistant").catch(() => {});
  await page.goto("http://127.0.0.1:8000/dashboard#assistant");
  await page.waitForTimeout(1500);

  result.checks.fabHiddenOnAssistant = await page.evaluate(() => {
    const fab = document.getElementById("ai-fab");
    return !fab || fab.classList.contains("hidden");
  });

  result.checks.chipCount = await page.evaluate(
    () => document.querySelectorAll(".chat .chip").length
  );

  // duplicates: any chip label appearing more than once in the chips row
  result.checks.duplicateChips = await page.evaluate(() => {
    const labels = [...document.querySelectorAll(".chat .chip")].map((c) => c.textContent.trim());
    return labels.filter((l, i) => labels.indexOf(l) !== i).length;
  });

  result.checks.greetingShown = await page.evaluate(() =>
    Boolean(document.querySelector(".chat .msg.bot"))
  );

  result.checks.disclaimerShown = await page.evaluate(() => {
    const texts = [...document.querySelectorAll("#main p")].map((p) => p.textContent);
    // demo farmer's language is Kiswahili; match either language's wording
    return texts.some((t) => /agritrust ai (records only|pekee)/i.test(t));
  });

  result.checks.largeRecCardsGone = await page.evaluate(() => document.querySelectorAll(".chat .insight-list").length === 0);

  // chips row must sit before the input inside the chat card (above the input)
  result.checks.chipsAboveInput = await page.evaluate(() => {
    const chat = document.querySelector(".chat");
    if (!chat) return false;
    const chips = chat.querySelector(".chips");
    const input = chat.querySelector("input");
    if (!chips || !input) return false;
    return chips.getBoundingClientRect().top <= input.getBoundingClientRect().top;
  });

  // ---- another page: the FAB must be visible again there
  await page.goto("http://127.0.0.1:8000/dashboard#overview");
  await page.waitForTimeout(1200);
  result.checks.fabVisibleOnOverview = await page.evaluate(() => {
    const fab = document.getElementById("ai-fab");
    return Boolean(fab && !fab.classList.contains("hidden"));
  });

  return result;
}
