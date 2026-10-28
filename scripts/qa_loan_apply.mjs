export default async function run(page, ui) {
  const base = "http://127.0.0.1:8000";
  const result = { checks: {}, blocked: {}, eligible: {} };

  // A blocked apply form used to be greyed out with the reason left in a card
  // further up the page, which read as a broken button. It now repeats the
  // blockers on the form itself, with a link to whatever has to be done.
  const readForm = () =>
    page.evaluate(() => {
      // matched on a field name, not the button text, so it works in both languages
      const form = [...document.querySelectorAll("#main form")].find((f) =>
        f.querySelector('input[name="requested_amount"]')
      );
      const alert = [...(form?.querySelectorAll(".alert") || [])].find(
        (a) => !a.classList.contains("hidden") && a.innerText.trim()
      );
      return {
        hasForm: !!form,
        submitDisabled: form?.querySelector("button[type=submit]")?.disabled,
        notice: alert ? alert.innerText.replace(/\s+/g, " ").trim() : null,
        noticeLinks: alert ? [...alert.querySelectorAll("a")].map((a) => a.getAttribute("href")) : [],
      };
    });

  // the access token lives in localStorage, so clearing cookies alone leaves the
  // previous farmer signed in and /login redirects straight past the form
  async function asFarmer(phone, lang) {
    await page.context().clearCookies();
    await page.goto(`${base}/`, { waitUntil: "load" });
    await page.evaluate(() => {
      try {
        localStorage.clear();
        sessionStorage.clear();
      } catch (_) {}
    });
    await page.goto(`${base}/login?lang=${lang}`, { waitUntil: "load" });
    await page.waitForTimeout(500);
    await page.locator('input[name="identifier"]').first().fill(phone);
    await page.locator('input[type="password"]').first().fill("Demo1234");
    await Promise.all([
      page.waitForURL((u) => !u.pathname.includes("/login"), { timeout: 20000 }).catch(() => {}),
      page.locator('button[type="submit"]').first().click(),
    ]);
    await page.waitForTimeout(1200);
    await page.goto(`${base}/dashboard?lang=${lang}#loans`, { waitUntil: "load" });
    await page.waitForTimeout(2600);
  }

  // Mary has an application still under review, so hers is held
  await asFarmer("+255754000101", "en");
  result.blocked.openApplication = await readForm();
  result.checks.blockedFormDisabled = result.blocked.openApplication.submitDisabled === true;
  result.checks.blockedFormExplainsWhy = Boolean(result.blocked.openApplication.notice);
  result.checks.blockedFormLinksSomewhere = result.blocked.openApplication.noticeLinks.length > 0;

  // and the same hold is explained in Kiswahili, not just English
  await asFarmer("+255754000101", "sw");
  result.blocked.swahili = await readForm();
  result.checks.swahiliNoticeShown = Boolean(result.blocked.swahili.notice);
  result.checks.swahiliNoticeIsSwahili = /Huwezi|ombi|Bado/i.test(result.blocked.swahili.notice || "");

  // a farmer with no application open gets a live form
  await asFarmer("+255754000104", "en");
  result.eligible = await readForm();
  result.checks.eligibleFormLive = result.eligible.submitDisabled === false;
  result.checks.eligibleFormHasNoHoldNotice = !result.eligible.notice;

  // The farmer picks what the loan is for, so an officer can group applications
  // without reading every sentence. The four uses a smallholder names, plus an
  // escape hatch. Checked without submitting, so the run leaves no open
  // application behind to block the next one.
  result.purpose = await page.evaluate(() => {
    const group = document.querySelector('.choice-row[role="radiogroup"]');
    const labels = group
      ? [...group.querySelectorAll(".choice")].map((c) => ({
          value: c.querySelector("input").value,
          label: c.querySelector("strong").textContent.trim(),
          hint: c.querySelector(".choice-hint")?.textContent.trim() || null,
        }))
      : [];
    return {
      labels,
      detailLabel: document.querySelector('textarea[name="purpose"]')?.closest(".field")
        ?.querySelector("label")?.textContent.replace(/\s+/g, " ").trim() || null,
      header: [...document.querySelectorAll("#main table th")].map((th) => th.textContent.trim()),
    };
  });
  result.checks.purposePickerShown = result.purpose.labels.length === 5;
  result.checks.purposeCoversTheNamedUses = ["FERTILIZER", "DRILLING", "EQUIPMENT", "MONEY"].every(
    (code) => result.purpose.labels.some((l) => l.value === code)
  );
  result.checks.everyPurposeHasAHint = result.purpose.labels.every((l) => l.hint);
  result.checks.purposeColumnInTable = result.purpose.header.includes("Purpose");
  // the form helper appends "(Optional)" itself, so the label must not repeat it
  result.checks.detailLabelNotDuplicated = !/\(optional\)\s*\(Optional\)/i.test(
    result.purpose.detailLabel || ""
  );

  return result;
}
