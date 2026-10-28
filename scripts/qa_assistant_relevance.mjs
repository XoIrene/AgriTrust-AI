export default async function run(page, ui) {
  const base = "http://127.0.0.1:8000";
  const result = { checks: {}, replies: {} };

  await page.goto(`${base}/login?lang=en`);
  await page.locator('input[name="identifier"]').first().fill("+255754000101");
  await page.locator('input[type="password"]').first().fill("Demo1234");
  await page.locator('button[type="submit"]').first().click();
  await page.waitForTimeout(1500);
  await page.goto(`${base}/dashboard?lang=en#assistant`);
  await page.waitForTimeout(1500);

  // ask each question through the real UI and read the bot's reply
  const questions = [
    ["forecast", "will it rain next week?"],
    ["weather", "what is the weather today?"],
    ["price", "what is the price of maize today?"],
    ["loan", "what is my loan status?"],
    ["insurance", "do I have insurance?"],
  ];

  for (const [name, question] of questions) {
    const input = page.locator(".chat form input").first();
    await input.fill(question);
    await page.locator(".chat form button[type=submit]").first().click();
    // wait for a new bot message that is not the greeting
    await page.waitForFunction(
      () => {
        const msgs = [...document.querySelectorAll(".chat .msg.bot")];
        return msgs.filter((m) => m.textContent.trim().length > 40).length >= 1;
      },
      null,
      { timeout: 20000 }
    ).catch(() => {});
    await page.waitForTimeout(600);
    const reply = await page.evaluate(() => {
      const msgs = [...document.querySelectorAll(".chat .msg.bot")];
      return msgs[msgs.length - 1].textContent.trim();
    });
    result.replies[name] = reply;
  }

  // the reply must match the question that was asked
  result.checks.forecastTalksAboutRain =
    /rain|forecast|mm/i.test(result.replies.forecast) &&
    !/acres|loan officer/i.test(result.replies.forecast);
  result.checks.weatherTalksAboutWeather =
    /weather|rain|°C|risk/i.test(result.replies.weather) &&
    !/acres|loan officer/i.test(result.replies.weather);
  result.checks.priceGivesAPrice = /TZS\s[\d,]+/.test(result.replies.price);
  result.checks.loanGivesALoan = /LOAN-/.test(result.replies.loan);
  result.checks.insuranceGivesAPolicy = /POL-/.test(result.replies.insurance);
  result.checks.insuranceHasNoLoanTail = !/loan officer/i.test(result.replies.insurance);
  result.checks.noRawTranslationKeys = !Object.values(result.replies).some((r) =>
    /(assistant|weather|advisory|risk\.level_)\./.test(r)
  );
  result.checks.noZeroTemperature = !/0-0\s*°C/.test(
    Object.values(result.replies).join(" ")
  );
  return result;
}
