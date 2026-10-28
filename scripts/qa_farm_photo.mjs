export default async function run(page, ui) {
  const base = "http://127.0.0.1:8000";
  const result = { checks: {}, info: {} };
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e).slice(0, 200)));

  const asFarmer = async (phone, lang = "en") => {
    await page.context().clearCookies();
    await page.goto(`${base}/`, { waitUntil: "load" });
    await page.evaluate(() => {
      try { localStorage.clear(); sessionStorage.clear(); } catch (_) {}
    });
    await page.goto(`${base}/login?lang=${lang}`, { waitUntil: "load" });
    await page.waitForTimeout(500);
    await page.locator('input[name="identifier"]').first().fill(phone);
    await page.locator('input[type="password"]').first().fill("Demo1234");
    await Promise.all([
      page.waitForURL((u) => !u.pathname.includes("/login"), { timeout: 20000 }).catch(() => {}),
      page.locator('button[type="submit"]').first().click(),
    ]);
    await page.waitForTimeout(1500);
    await page.goto(`${base}/dashboard?lang=${lang}#insurance`, { waitUntil: "load" });
    await page.waitForTimeout(3000);
  };

  const photoCard = () =>
    page.evaluate(() => {
      const card = [...document.querySelectorAll("#main .card")].find((c) =>
        /photo of your farm|picha ya shamba lako/i.test(
          c.querySelector("h2, h3")?.textContent || ""
        )
      );
      if (!card) return null;
      return {
        title: card.querySelector("h2, h3")?.textContent,
        options: [...card.querySelectorAll("select option")].map((o) => o.textContent),
        accepts: card.querySelector('input[type="file"]')?.getAttribute("accept"),
        buttons: [...card.querySelectorAll("button")].map((b) => ({
          label: b.textContent.trim(),
          disabled: b.disabled,
          hidden: b.classList.contains("hidden"),
        })),
        src: card.querySelector(".photo-preview img")?.getAttribute("src") || null,
      };
    });

  await asFarmer("+255754000101");
  result.info.initial = await photoCard();
  result.checks.cardShown = Boolean(result.info.initial);
  result.checks.acceptsOnlyImages = result.info.initial?.accepts === "image/jpeg,image/png,image/webp";
  // the picker has to say which farm is which, or duplicate names make it useless
  result.checks.pickerDistinguishesFarms = (result.info.initial?.options || []).every((o) =>
    /acres|hekiari/i.test(o)
  );
  result.checks.uploadDisabledWithNoFile =
    result.info.initial?.buttons.find((b) => /upload|pakia/i.test(b.label))?.disabled === true;
  // Remove is offered exactly when there is a photo to take down. Asserting the
  // relationship rather than a fixed value, because the run may start with a
  // photo already on the farm from an earlier session.
  result.checks.removeShownExactlyWhenAPhotoExists =
    (result.info.initial?.buttons.find((b) => /remove|ondoa/i.test(b.label))?.hidden === false) ===
    Boolean(result.info.initial?.src);

  // ---- a real JPEG, made in the page and attached to the input
  await page.evaluate(() => {
    const canvas = document.createElement("canvas");
    canvas.width = 240; canvas.height = 160;
    const ctx = canvas.getContext("2d");
    ctx.fillStyle = "#2e7d32"; ctx.fillRect(0, 0, 240, 160);
    ctx.fillStyle = "#e0a72e"; ctx.fillRect(20, 20, 200, 60);
    return canvas.toBlob((blob) => {
      const dt = new DataTransfer();
      dt.items.add(new File([blob], "my-farm.jpg", { type: "image/jpeg" }));
      const input = [...document.querySelectorAll("#main .card input[type=file]")][0];
      input.files = dt.files;
      input.dispatchEvent(new Event("change", { bubbles: true }));
    }, "image/jpeg", 0.9);
  });
  await page.waitForTimeout(700);
  result.info.afterChoose = await photoCard();
  result.checks.uploadEnabledAfterChoosing =
    result.info.afterChoose?.buttons.find((b) => /upload|pakia/i.test(b.label))?.disabled === false;

  // ---- press Upload
  await page.evaluate(() => {
    const card = [...document.querySelectorAll("#main .card")].find((c) =>
      /photo of your farm|picha ya shamba lako/i.test(c.querySelector("h2, h3")?.textContent || "")
    );
    [...card.querySelectorAll("button")].find((b) => /upload photo|pakia picha/i.test(b.textContent))?.click();
  });
  await page.waitForTimeout(3500);
  result.info.afterUpload = await photoCard();
  result.checks.storedUnderServerName =
    /\/static\/uploads\/farms\/[0-9a-f]{32}_[0-9a-f]{16}\.jpg$/.test(result.info.afterUpload?.src || "");
  result.checks.removeOfferedAfterUpload =
    result.info.afterUpload?.buttons.find((b) => /remove|ondoa/i.test(b.label))?.hidden === false;
  result.checks.offerReplaceAfterUpload =
    /replace|badilisha/i.test(result.info.afterUpload?.buttons.find((b) => /choose|replace|chagua|badilisha/i.test(b.label))?.label || "");

  // ---- the stored photo really is a viewable image served by the app
  result.info.rendered = await page.evaluate(() => {
    const img = document.querySelector("#main .card .photo-preview img");
    return img
      ? { loaded: img.complete && img.naturalWidth > 0, w: img.naturalWidth, h: img.naturalHeight }
      : { loaded: false };
  });
  result.checks.photoRenders = result.info.rendered.loaded === true;

  result.info.fetched = await page.evaluate(async () => {
    const src = document.querySelector("#main .card .photo-preview img")?.getAttribute("src");
    if (!src || src.startsWith("blob:")) return { fetched: false };
    const res = await fetch(src);
    const blob = await res.blob();
    return { fetched: res.ok, contentType: res.headers.get("content-type"), bytes: blob.size };
  });
  result.checks.photoFetchable = result.info.fetched.fetched === true;
  result.checks.servedAsImage = /^image\//.test(result.info.fetched.contentType || "");

  // ---- a file that only claims to be a photo is turned away before uploading
  await page.evaluate(() => {
    const dt = new DataTransfer();
    dt.items.add(new File([new Uint8Array([1, 2, 3, 4, 5])], "notes.txt", { type: "text/plain" }));
    const input = [...document.querySelectorAll("#main .card input[type=file]")][0];
    input.files = dt.files;
    input.dispatchEvent(new Event("change", { bubbles: true }));
  });
  await page.waitForTimeout(600);
  result.info.afterBadFile = await page.evaluate(() => {
    const card = [...document.querySelectorAll("#main .card")].find((c) =>
      /photo of your farm|picha ya shamba lako/i.test(c.querySelector("h2, h3")?.textContent || "")
    );
    return {
      warning: card?.querySelector(".small[style*='critical']")?.textContent?.trim() || null,
      uploadDisabled: [...(card?.querySelectorAll("button") || [])]
        .find((b) => /upload photo|pakia picha/i.test(b.textContent))?.disabled,
    };
  });
  result.checks.nonImageRefusedInBrowser = Boolean(result.info.afterBadFile.warning);
  result.checks.uploadStaysDisabledForNonImage = result.info.afterBadFile.uploadDisabled === true;

  result.checks.noPageErrors = errors.length === 0;
  return result;
}
