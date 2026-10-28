export default async function run(page, ui) {
  const result = { checks: {} };

  async function login(phone) {
    await page.goto("http://127.0.0.1:8000/dashboard", { waitUntil: "commit", timeout: 60000 }).catch(() => {});
    await page.evaluate(() => { localStorage.clear(); sessionStorage.clear(); }).catch(() => {});
    await page.goto("http://127.0.0.1:8000/login", { waitUntil: "commit", timeout: 90000 });
    await page.locator('input[name="identifier"], input[autocomplete], input[type="text"], input[type="tel"]').first().waitFor({ timeout: 90000 });
    await page.locator('input[name="identifier"], input[autocomplete], input[type="text"], input[type="tel"]').first().fill(phone);
    await page.locator('input[type="password"]').first().fill("Demo1234");
    await page.locator('button[type="submit"], form button').first().click();
    await page.waitForTimeout(5000);
  }

  await login("+255754000201"); // Bora Buyer

  // ---- farmer directory
  await page.goto("http://127.0.0.1:8000/dashboard#farmerdir");
  await page.waitForTimeout(4000);
  result.checks.farmerNav = await page.evaluate(() =>
    [...document.querySelectorAll("#sidebar a")].some((a) => /Farmers|Wakulima/i.test(a.textContent))
  );
  result.directoryDebug = await page.evaluate(() =>
    (document.querySelector("#view")?.innerText || "").slice(0, 250)
  );
  result.checks.directoryHasContacts = await page.evaluate(() => {
    const links = [...document.querySelectorAll("#view a[href^='tel:']")];
    return links.length > 0;
  });
  result.checks.whatsappLinks = await page.evaluate(
    () => document.querySelectorAll("#view a[href^='https://wa.me/']").length
  );

  // ---- buy dialog: payment methods, now grouped radios rather than one select
  await page.goto("http://127.0.0.1:8000/dashboard#buyer");
  await page.waitForTimeout(4000);
  const buyBtn = page.locator('#view button:has-text("Buy"), #view button:has-text("Nunua")').first();
  result.checks.buyButtonPresent = await buyBtn.count() > 0;
  if (result.checks.buyButtonPresent) {
    await buyBtn.click();
    await page.waitForSelector("#dialog .pay-methods", { timeout: 30000 });
    // the method names live on the labels, not in <option> text any more
    result.paymentMethods = await page.evaluate(() =>
      [...document.querySelectorAll("#dialog .pay-option .pay-option-name")].map((n) => n.textContent.trim())
    );
    result.paymentGroups = await page.evaluate(() =>
      [...document.querySelectorAll("#dialog .pay-group h4")].map((n) => n.textContent.trim())
    );
    result.checks.allMethodsOffered = result.paymentMethods.length >= 15;
    result.checks.mobileMoneyMethods = result.paymentMethods.some((m) => /M-Pesa|Airtel|Mixx|Halo|Azam|T-Pesa/i.test(m));
    result.checks.bankMethods = result.paymentMethods.some((m) => /NMB|CRDB|NBC|Stanbic|Equity|Exim/i.test(m));
    result.checks.checkoutMethods = result.paymentMethods.some((m) => /Selcom|ClickPesa|Pesapal|DPO|Flutterwave/i.test(m));
    // each group is labelled, so a wallet is never mistaken for a bank account.
    // The session may be in either language, so match both.
    const GROUPS = [
      /Mobile money|Pesa ya simu/i,
      /Lipa number|Namba ya Lipa/i,
      /Bank account|Akaunti ya benki/i,
      /Online checkout|Malipo mtandaoni/i,
    ];
    result.checks.groupsLabeled = GROUPS.every((re) => result.paymentGroups.some((g) => re.test(g)));
    // picking a rail must tell the buyer what to dial or which account to pay
    await page.locator('#dialog .pay-option input[value="mpesa"]').check();
    await page.waitForTimeout(300);
    result.mobileNote = await page.evaluate(() =>
      document.getElementById("pay-method-note")?.textContent?.trim() || "");
    await page.locator('#dialog .pay-option input[value="nmb"]').check();
    await page.waitForTimeout(300);
    result.bankNote = await page.evaluate(() =>
      document.getElementById("pay-method-note")?.textContent?.trim() || "");
    result.checks.instructionsShown =
      /Lipa number|Namba ya Lipa/i.test(result.mobileNote)
      && /Account number|Namba ya akaunti/i.test(result.bankNote);
    await page.keyboard.press("Escape").catch(() => {});
  }

  // ---- the payment statement lists every rail, not only the ones used
  await page.waitForTimeout(800);
  result.statementMethods = await page.evaluate(() =>
    [...document.querySelectorAll("#view .pay-group")].reduce(
      (n, g) => n + g.querySelectorAll("tbody tr").length, 0));
  result.checks.statementListsAllMethods = result.statementMethods >= 15;
  result.checks.statementHasRows = await page.evaluate(() => {
    // the statement table is the one naming the payment-method column, in
    // whichever language the session is using
    const tb = [...document.querySelectorAll("#view table")].find((t) =>
      /payment method|Njia ya malipo/i.test(t.querySelector("thead")?.innerText || ""));
    return tb ? tb.querySelectorAll("tbody tr").length : 0;
  });

  return result;
}
