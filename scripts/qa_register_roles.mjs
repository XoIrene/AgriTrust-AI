export default async function run(page, ui) {
  const base = "http://127.0.0.1:8000";
  const result = { checks: {} };
  const stamp = Date.now().toString().slice(-6);

  const openRegister = async () => {
    await page.context().clearCookies();
    await page.goto(`${base}/`, { waitUntil: "load" });
    await page.evaluate(() => {
      try { localStorage.clear(); sessionStorage.clear(); } catch (_) {}
    });
    await page.goto(`${base}/register`, { waitUntil: "load", timeout: 90000 });
    await page.locator("#full_name").waitFor({ timeout: 60000 });
    // the identity section enables its button once /identity/methods has answered
    await page.waitForFunction(
      () => !document.getElementById("id-verify-btn")?.disabled,
      { timeout: 60000 }
    );
  };

  const fillCommon = async (name, phone, role) => {
    await page.fill("#full_name", name);
    await page.fill("#phone", phone);
    await page.fill("#password", "Passw0rd!123");
    await page.fill("#confirm", "Passw0rd!123");
    await page.selectOption("#role", role);
    await page.waitForTimeout(400);
  };

  // ---- the national ID is a farmer-account requirement only
  await openRegister();
  result.checks.roleOptions = await page.evaluate(() =>
    [...document.querySelectorAll("#role option")].map((o) => o.value)
  );
  result.checks.publishedGatedRoles = await page.evaluate(
    () => document.getElementById("id-section")?.dataset.requiredRoles
  );

  // farmer: the section is there and Register is held shut
  await fillCommon("QA Farmer " + stamp, "+2557542" + stamp, "FARMER");
  result.checks.farmerSeesIdSection = await page.evaluate(
    () => !document.getElementById("id-section").classList.contains("hidden")
  );
  result.checks.submitBlockedUntilVerified = await page.evaluate(
    () => document.getElementById("register-submit").disabled
  );
  result.checks.testModeAdvertised = await page.evaluate(
    () => !document.getElementById("id-test-mode").classList.contains("hidden")
  );

  // ---- step 1: a NIDA number the test double does not know
  await page.fill("#nida_number", "1234567890123456");
  await page.click("#id-verify-btn");
  await page.waitForFunction(
    () => document.getElementById("id-step-nida").classList.contains("is-failed"),
    { timeout: 60000 }
  );
  result.checks.unknownNidaRejected = await page.evaluate(() =>
    document.getElementById("id-nida-status").textContent.includes("✗"));

  // ---- step 2: an invented number from the mock's reserved range. One national
  // ID may back one account, so a fresh one is minted for every run.
  const nida = ("7000" + stamp + String(Date.now()).slice(-6)).padEnd(16, "0").slice(0, 16);
  await page.fill("#nida_number", nida);
  await page.click("#id-verify-btn");
  await page.waitForFunction(
    () => document.getElementById("id-step-nida").classList.contains("is-done"),
    { timeout: 60000 }
  );
  result.checks.nidaVerified = await page.evaluate(() =>
    document.getElementById("id-nida-status").textContent.includes("✓"));
  // the number is never echoed back to the page
  result.checks.numberNotEchoed = await page.evaluate((n) =>
    !document.getElementById("id-section").innerText.includes(n), nida);

  // ---- step 3: the phone code, prefilled by the mock SMS provider
  await page.waitForFunction(
    () => document.getElementById("otp_code")?.value?.length > 0,
    { timeout: 60000 }
  );
  result.checks.otpStepShown = await page.evaluate(() =>
    !document.getElementById("id-step-otp").classList.contains("hidden"));
  await page.click("#id-otp-btn");
  await page.waitForFunction(
    () => document.getElementById("id-otp-status").textContent.includes("✓"),
    { timeout: 60000 }
  );
  result.checks.submitUnblocked = await page.evaluate(() =>
    !document.getElementById("register-submit").disabled);

  await page.fill("#village", "Kijiji");
  await page.fill("#district", "Morogoro");
  await page.fill("#region", "Morogoro");
  await page.click('#register-form button[type="submit"]');
  await page.waitForTimeout(6000);

  result.checks.farmerLandedOnDashboard = page.url().includes("/dashboard");
  result.checks.farmerSeesFarmerView = await page.evaluate(() =>
    /Hello|habari/i.test(document.querySelector("#view")?.innerText || "")
  );
  result.checks.identityOnFarmerAccount = await page.evaluate(async () => {
    const token = JSON.parse(localStorage.getItem("agritrust.tokens") || "{}").access;
    const res = await fetch("/api/v1/identity/me", { headers: { Authorization: `Bearer ${token}` } });
    const body = await res.json();
    return { verified: body.identity_verified, masked: body.nida_masked };
  });

  // ---- a buyer registers with no national ID at all
  await openRegister();
  await fillCommon("QA Buyer " + stamp, "+2557543" + stamp, "BUYER");
  result.checks.buyerIdSectionHidden = await page.evaluate(
    () => document.getElementById("id-section").classList.contains("hidden")
  );
  result.checks.buyerToldNoIdNeeded = await page.evaluate(() => {
    const note = document.getElementById("id-skip-note");
    return !!note && !note.classList.contains("hidden");
  });
  result.checks.buyerSubmitLive = await page.evaluate(
    () => !document.getElementById("register-submit").disabled
  );
  result.checks.farmerFieldsHidden = await page.evaluate(
    () => document.getElementById("farmer-fields").classList.contains("hidden")
  );

  await page.click('#register-form button[type="submit"]');
  await page.waitForTimeout(6000);
  result.checks.buyerRegistered = page.url().includes("/dashboard");
  result.checks.buyerViewLoaded = await page.evaluate(() =>
    /Buyer Dashboard|Dashibodi ya Mnunuzi/i.test(document.querySelector("#view")?.innerText || "")
  );
  result.checks.buyerNav = await page.evaluate(() =>
    [...document.querySelectorAll("#sidebar a")].some((a) => /Buyer Dashboard|Mnunuzi/i.test(a.textContent))
  );
  // honest about it: the account exists, with no verified identity behind it
  result.checks.buyerHasNoVerifiedIdentity = await page.evaluate(async () => {
    const token = JSON.parse(localStorage.getItem("agritrust.tokens") || "{}").access;
    const res = await fetch("/api/v1/identity/me", { headers: { Authorization: `Bearer ${token}` } });
    return (await res.json()).identity_verified === false;
  });

  return result;
}
