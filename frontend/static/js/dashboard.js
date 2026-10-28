/* AgriTrust AI dashboard: one page, views chosen by role and the URL hash. */
(async function () {
  "use strict";
  const { api, t, fmt, el, toast, pill, tokens, logout, loadCatalog } = window.AT;
  const Charts = window.ATCharts;

  if (!tokens.get()) { window.location.href = "/login"; return; }
  await loadCatalog();

  const view = document.getElementById("view");
  const sidebar = document.getElementById("sidebar");
  const dialog = document.getElementById("dialog");
  const STAFF = ["LOAN_OFFICER", "INSURANCE_OFFICER", "COOPERATIVE_OFFICER", "ADMIN", "SUPER_ADMIN"];
  const ADMIN = ["ADMIN", "SUPER_ADMIN"];

  let user;
  try {
    user = await api.get("/auth/me");
  } catch (err) {
    tokens.clear();
    window.location.href = "/login";
    return;
  }
  const isFarmer = user.role === "FARMER";
  const isBuyer = user.role === "BUYER";
  const isLoanOfficer = ["LOAN_OFFICER", ...ADMIN].includes(user.role);
  const can = {
    loans: isLoanOfficer,
    insurance: ["INSURANCE_OFFICER", ...ADMIN].includes(user.role),
    admin: ADMIN.includes(user.role),
    staff: STAFF.includes(user.role),
    buyer: isBuyer || ADMIN.includes(user.role),
  };

  // ================================================================ helpers
  function card(title, ...children) {
    return el("section", { class: "card" }, title ? el("h2", {}, title) : null, ...children);
  }
  function cardWithAction(title, action, ...children) {
    return el("section", { class: "card" }, el("div", { class: "card-head" }, el("h2", {}, title), action), ...children);
  }
  function stat(label, value, sub) {
    return el("div", { class: "card stat" },
      el("span", { class: "label" }, label),
      el("span", { class: "value" }, value),
      sub ? el("span", { class: "sub" }, sub) : null);
  }
  function table(headers, rows, emptyText) {
    if (!rows.length) return el("p", { class: "empty" }, emptyText || t("common.no_data"));
    return el("div", { class: "table-wrap" },
      el("table", {},
        el("thead", {}, el("tr", {}, headers.map((h) => {
          const [label, cls] = Array.isArray(h) ? h : [h, ""];
          return el("th", { class: cls }, label);
        }))),
        el("tbody", {}, rows.map((r) => el("tr", {}, r.map((c) => {
          if (c && typeof c === "object" && !(c instanceof Node) && "v" in c) return el("td", { class: c.cls || "" }, c.v);
          return el("td", {}, c);
        }))))));
  }
  const num = (v) => ({ v, cls: "num" });
  const loading = () => el("p", { class: "muted" }, t("common.loading"));

  /** A small declarative form. fields: [{name, label, type, options, required, value, step, hint, wide}]
   *
   * `options.extra` appends nodes below the fields - used where a field is not a
   * plain input, such as the grouped payment-method chooser.
   */
  function form(fields, submitLabel, onSubmit, options = {}) {
    const errorBox = el("div", { class: "alert error hidden", role: "alert" });
    const inputs = {};
    const body = fields.map((f) => {
      let input;
      const id = `f-${f.name}-${Math.random().toString(36).slice(2, 7)}`;
      if (f.type === "select") {
        input = el("select", { id, name: f.name, required: f.required },
          f.required ? null : el("option", { value: "" }, t("form.please_select")),
          f.options.map(([value, label]) => el("option", { value, selected: String(f.value) === String(value) }, label)));
      } else if (f.type === "choice") {
        // a required pick from a short list, as tappable chips rather than a
        // dropdown: the whole point is that the farmer can see every use at once
        const group = el("div", { class: "choice-row", role: "radiogroup", "aria-labelledby": `${id}-label` });
        const options = f.choices.map(([value, label, hint]) => {
          const radio = el("input", { type: "radio", name: f.name, value, checked: String(f.value) === String(value) });
          radio.addEventListener("change", () => {
            // picking is the farmer answering the "required" complaint, so stop
            // showing it the moment they do
            errorBox.classList.add("hidden");
            f.onPick?.(value, radio);
          });
          group.appendChild(
            el("label", { class: "choice" },
              radio,
              el("span", { class: "choice-text" },
                el("strong", {}, label),
                hint ? el("span", { class: "choice-hint" }, hint) : null))
          );
          return radio;
        });
        inputs[f.name] = { radios: options, value: () => options.find((r) => r.checked)?.value || "" };
        return el("div", { class: f.wide ? "field wide" : "field" },
          el("label", { id: `${id}-label` }, f.label, f.required ? "" : el("span", { class: "muted" }, ` (${t("common.optional")})`)),
          group, f.hint ? el("div", { class: "hint" }, f.hint) : null);
      } else if (f.type === "textarea") {
        input = el("textarea", { id, name: f.name, rows: 3, required: f.required, minlength: f.minlength });
        if (f.value) input.value = f.value;
      } else if (f.type === "checkbox") {
        input = el("input", { id, name: f.name, type: "checkbox", checked: !!f.value });
        inputs[f.name] = input;
        return el("div", { class: "field" }, el("label", { class: "check", for: id }, input, f.label));
      } else {
        input = el("input", {
          id, name: f.name, type: f.type || "text", required: f.required, step: f.step || (f.type === "number" ? "any" : null),
          min: f.min, placeholder: f.placeholder, value: f.value ?? null,
        });
      }
      inputs[f.name] = input;
      // `wide` lets an option label that is too long for half a row have the whole
      // row, rather than being silently cut off by the select's edge
      return el("div", { class: f.wide ? "field wide" : "field" },
        el("label", { for: id }, f.label, f.required ? "" : el("span", { class: "muted" }, ` (${t("common.optional")})`)),
        input, f.hint ? el("div", { class: "hint" }, f.hint) : null);
    });
    const button = el("button", { class: "btn", type: "submit" }, submitLabel);
    const node = el("form", { novalidate: true },
      errorBox, el("div", { class: "form-grid" }, body),
      ...(options.extra || []).filter(Boolean),
      button);
    node.addEventListener("submit", async (e) => {
      e.preventDefault();
      errorBox.classList.add("hidden");
      const data = {};
      for (const f of fields) {
        const input = inputs[f.name];
        if (f.type === "checkbox") { data[f.name] = input.checked; continue; }
        if (f.type === "choice") {
          const picked = input.value();
          if (!picked) {
            if (f.required) {
              errorBox.textContent = `${f.label}: ${t("common.required")}`;
              errorBox.classList.remove("hidden");
              input.radios[0]?.focus();
              return;
            }
            continue;
          }
          data[f.name] = picked;
          continue;
        }
        const raw = input.value.trim();
        if (!raw) {
          if (f.required) {
            errorBox.textContent = `${f.label}: ${t("common.required")}`;
            errorBox.classList.remove("hidden");
            input.focus();
            return;
          }
          continue;
        }
        data[f.name] = f.type === "number" ? Number(raw) : raw;
      }
      button.disabled = true;
      try {
        await onSubmit(data, node);
      } catch (err) {
        errorBox.textContent = err.message;
        errorBox.classList.remove("hidden");
      } finally {
        button.disabled = false;
      }
    });
    node.inputs = inputs;
    return node;
  }

  function openDialog(title, content) {
    dialog.replaceChildren(
      el("div", { class: "card-head" }, el("h2", {}, title),
        el("button", { class: "btn secondary small", type: "button", onclick: () => dialog.close() }, t("common.close"))),
      content);
    dialog.showModal();
  }

  async function guard(node, fn) {
    try {
      await fn();
    } catch (err) {
      node.replaceChildren(el("div", { class: "alert error" }, err.message));
    }
  }

  /**
   * replaceChildren for the view, but a skipped child (`cond ? node : null`) is
   * dropped. The native method prints null as the text "null".
   */
  const setView = (...children) => view.replaceChildren(
    ...children.flat(Infinity).filter((c) => c !== null && c !== undefined && c !== false)
  );

  const riskPill = (level) => pill(level);
  /** 24.9 °C - one decimal and the unit, never a bare number */
  const temp = (v, unit = "°C") => (v === null || v === undefined ? "—" : `${fmt.number(v, 1)} ${unit}`);

  /** "6 months" / "Miezi 6", optionally with the date the period ends. */
  function monthsLabel(months, withEndDate) {
    const label = t("common.months_n", { n: months });
    if (!withEndDate) return label;
    const end = new Date();
    end.setDate(end.getDate() + 30 * months);  // same 30-day months the server uses
    return `${label} (${t("common.until", { date: fmt.date(end) })})`;
  }

  /** Recommendation reasons arrive as {key, params}; format the params before translating. */
  function reasonText(reason) {
    const p = {};
    for (const [k, v] of Object.entries(reason.params || {})) {
      if (["amount", "min", "max", "sum"].includes(k)) p[k] = fmt.money(v);
      else if (k === "score") p[k] = fmt.pct(v);
      else if (k === "month") p[k] = fmt.month(v);
      else if (k === "premium") p[k] = fmt.pct(v, 1);
      else if (k === "level") p[k] = t(`risk.level_${String(v).toLowerCase()}`);
      else if (k === "price") p[k] = fmt.number(v);
      else p[k] = v;
    }
    return t(reason.key, p);
  }

  const DQ_TONE = { GOOD: "good", FAIR: "warning", LOW: "critical" };
  function qualityPill(dq) {
    return el("span", { class: `pill ${DQ_TONE[dq.level]}` }, `${t(`dq.level_${dq.level.toLowerCase()}`)} · ${fmt.pct(dq.score)}`);
  }
  function qualityBlock(dq, showChecks = true) {
    return el("div", {},
      el("div", { class: "row" }, qualityPill(dq), el("span", { class: "muted small" }, t("dq.intro"))),
      dq.needs_review ? el("div", { class: "alert", style: "margin-top:0.6rem" }, t("dq.needs_review")) : null,
      showChecks ? el("ul", { style: "list-style:none;padding:0;margin:0.6rem 0 0" },
        dq.checks.map((c) => el("li", { style: "padding:0.15rem 0" },
          el("span", { "aria-hidden": "true", style: `display:inline-block;width:1.2rem;color:var(${c.passed ? "--good-text" : "--critical"})` }, c.passed ? "✓" : "✗"),
          el("span", { class: "hidden-a11y" }, c.passed ? `${t("common.yes")}: ` : `${t("common.no")}: `),
          t(`dq.${c.key}`)))) : null);
  }
  const optionList = (keys, prefix) => keys.map((k) => [k, t(`${prefix}.${k.toLowerCase()}`)]);

  // ================================================================ navigation
  // The assistant's id is "assistant" everywhere - the sidebar link, the
  // floating panel's "open chat" and the proactive cards' "ask" all have to
  // agree, or the page silently falls back to the overview.
  const FARMER_NAV = [
    ["overview", "nav.dashboard"], ["plan", "nav.plan"], ["farm", "nav.my_farm"], ["money", "nav.money"], ["transactions", "nav.transactions_history"], ["loans", "nav.loans"],
    ["insurance", "nav.insurance"], ["market", "nav.market"], ["sell", "nav.sell"], ["weather", "nav.weather"], ["assistant", "nav.ai_assistant"], ["ussd", "ussd.title"],
  ];
  const STAFF_NAV = [
    ["overview", "nav.dashboard"],
    ["assessments", "f5.review_queue"],
    ["impact", "nav.impact"],
    can.loans && ["queue", "nav.loan_applications"],
    ["farmers", "nav.users"],
    can.insurance && ["claims", "nav.insurance_policies"],
    ["market", "nav.market"],
    can.admin && ["audit", "nav.audit_logs"],
    ["ussd", "ussd.title"],
  ].filter(Boolean);
  const BUYER_NAV = [
    ["buyer", "nav.buyer_dashboard"],
    ["market", "nav.market"],
    ["farmerdir", "nav.farmers"],
  ];
  const OFFICER_NAV = [
    ["officer", "nav.officer_desk"],
    ...STAFF_NAV.slice(1),
  ];
  const NAV = isFarmer ? FARMER_NAV : isBuyer ? BUYER_NAV : isLoanOfficer ? OFFICER_NAV : STAFF_NAV;
  /** Views reachable from the top bar, so deliberately absent from the sidebar. */
  const TOPBAR_NAV = ["notifications", "profile"];
  const roleLabel = {
    FARMER: "auth.role_farmer", BUYER: "auth.role_buyer", LOAN_OFFICER: "auth.role_loan_officer", INSURANCE_OFFICER: "auth.role_insurance_officer",
    COOPERATIVE_OFFICER: "auth.role_cooperative", ADMIN: "nav.admin", SUPER_ADMIN: "nav.admin",
  };

  // ---- sidebar: navigation only. Who you are, alerts and sign-out all moved
  // to the top bar, so the sidebar is just the list of screens.
  function renderSidebar() {
    sidebar.replaceChildren(
      ...NAV.map(([id, key]) => el("a", { href: `#${id}`, dataset: { view: id } }, t(key))));
  }

  // ---- top bar: alerts, identity, and the badge on the bell
  // These live in the header rather than the sidebar, so they are reached from
  // the same place on every screen and never scroll away inside a long list.
  const notifBtn = document.getElementById("notif-btn");
  const notifBadge = document.getElementById("notif-badge");
  const notifBadgeText = document.getElementById("notif-badge-text");
  const profileBtn = document.getElementById("profile-btn");
  const profileName = document.getElementById("profile-name");
  const profileInitial = document.getElementById("profile-initial");

  function renderTopbar() {
    if (profileName) profileName.textContent = user.full_name.split(" ")[0];
    if (profileInitial) profileInitial.textContent = (user.full_name.trim()[0] || "?");
    if (profileBtn) profileBtn.setAttribute("title", `${user.full_name} · ${t(roleLabel[user.role])}`);
    if (notifBtn) {
      notifBtn.onclick = (e) => { e.preventDefault(); go("notifications"); };
    }
    if (profileBtn) {
      profileBtn.onclick = (e) => {
        // a farmer has a full profile editor; everyone else gets the account card
        e.preventDefault();
        go("profile");
      };
    }
  }

  async function refreshUnread() {
    try {
      const data = await api.get("/notifications");
      const unread = data.unread || 0;
      if (notifBadge) {
        notifBadge.textContent = unread > 99 ? "99+" : String(unread);
        notifBadge.classList.toggle("hidden", !unread);
      }
      // the number is also announced, so the bell is not colour-only
      if (notifBadgeText) {
        notifBadgeText.textContent = unread
          ? t("nav.notifications") + ": " + unread
          : "";
      }
      if (notifBtn) notifBtn.setAttribute("aria-label", t("nav.notifications"));
    } catch (_) { /* badge is optional */ }
  }

  const VIEWS = {};
  // Renders run one at a time: a slow screen that started earlier can never
  // finish after, and overwrite, the screen the farmer has since opened.
  let routing = Promise.resolve();
  function route() {
    routing = routing.then(renderRoute, renderRoute);
    return routing;
  }
  async function renderRoute() {
    const id = (location.hash || "#overview").slice(1).split("/")[0];
    // Alerts and the profile are reached from the top bar, so they are not in
    // the sidebar's list - but they are still legitimate targets, and without
    // this the router would treat them as unknown and drop back to the overview.
    const name = (NAV.some(([v]) => v === id) || TOPBAR_NAV.includes(id)) ? id : "overview";
    sidebar.querySelectorAll("a[data-view]").forEach((a) => {
      if (a.dataset.view === name) a.setAttribute("aria-current", "page");
      else a.removeAttribute("aria-current");
    });
    view.replaceChildren(loading());
    const fn = VIEWS[`${isFarmer ? "f" : isBuyer ? "b" : "s"}_${name}`] || VIEWS[name];
    await guard(view, () => fn());
    document.getElementById("main").focus({ preventScroll: true });
  }
  window.addEventListener("hashchange", () => {
    route();
  });
  const go = (id) => { if (location.hash === `#${id}`) route(); else location.hash = id; };

  // ================================================================ farmer views
  async function farmerProfile() {
    try {
      return await api.get("/farmers/me");
    } catch (err) {
      return null;
    }
  }

  VIEWS.f_overview = async () => {
    const [farmer, risk, profile, weather, loans, recentTx, providers] = await Promise.all([
      farmerProfile(),
      api.get("/ai/credit-risk/latest").catch(() => ({ available: false })),
      api.get("/finance/profile").catch(() => null),
      api.get("/weather").catch(() => null),
      api.get("/loans").catch(() => []),
      // the farmer's latest transactions, so the dashboard shows their money moving
      api.get("/finance/transactions?limit=6").catch(() => []),
      loadProviders().catch(() => []),
    ]);
    const nodes = [el("h1", {}, `${t("common.hello")}, ${user.full_name.split(" ")[0]}`)];
    if (farmer && farmer.farm_count === 0) {
      nodes.push(el("div", { class: "alert" }, t("farm.no_farms"), " ",
        el("a", { href: "#farm" }, t("farm.add"))));
    }
    nodes.push(el("div", { class: "grid grid-4" },
      stat(t("risk.credit_risk"), risk.available ? riskPill(risk.risk_level) : t("risk.not_assessed"),
        risk.available ? `${t("risk.score")} ${fmt.number(risk.risk_score, 2)}` : null),
      stat(t("loan.recommended_range"), risk.available ? `${fmt.compact(risk.recommended_min)} – ${fmt.compact(risk.recommended_max)}` : "—", "TZS"),
      stat(t("finance.monthly_income"), profile ? fmt.money(profile.monthly_income) : "—", profile ? `${t("finance.savings_rate")} ${fmt.pct(profile.savings_rate)}` : null),
      stat(t("finance.balance"), profile ? fmt.money(profile.total_balance) : "—"),
    ));

    const riskCard = cardWithAction(t("risk.title"),
      el("button", {
        class: "btn small", onclick: async (e) => {
          e.target.disabled = true;
          try { await api.post("/ai/credit-risk", {}); toast(t("risk.title")); route(); } catch (err) { toast(err.message); }
          e.target.disabled = false;
        },
      }, risk.available ? t("common.retry") : t("common.continue")));
    // the stat tiles above already show the level and the suggested range
    if (risk.available) riskExplanation(riskCard, risk, { withSummary: false });
    else riskCard.append(el("p", { class: "muted" }, t("risk.not_assessed")));

    const weatherCard = card(t("weather.title"));
    if (weather && weather.available) {
      weatherCard.append(
        el("div", { class: "row between" }, el("strong", {}, weather.location), riskPill(weather.risk_level)),
        weather.now ? el("p", {}, el("strong", {}, t("weather.now", { temp: temp(weather.now.temperature, weather.now.unit) })),
          el("span", { class: "muted small" }, ` · ${t("weather.feels_like", { temp: temp(weather.now.feels_like, weather.now.unit) })}`)) : null,
        el("p", { class: "muted small" }, t("weather.rain_last_30", { rain: fmt.number(weather.rainfall_30d, 1) }),
          el("br"), t("weather.crop_needs", { need: fmt.number(weather.rainfall_requirement) })),
        el("ul", {}, weather.advisories.map((a) => el("li", {}, t(`advisory.${a}`)))));
    } else {
      weatherCard.append(el("p", { class: "muted" }, t("common.no_data")));
    }

    const latestLoan = loans[0];
    const loanCard = cardWithAction(t("loan.my_loans"), el("a", { class: "btn small secondary", href: "#loans" }, t("loan.apply")));
    loanCard.append(latestLoan
      ? el("div", {}, el("div", { class: "row between" }, el("strong", {}, latestLoan.reference), pill(latestLoan.status)),
        el("p", { class: "muted small" }, `${fmt.money(latestLoan.requested_amount)} · ${fmt.date(latestLoan.applied_on)}`))
      : el("p", { class: "muted" }, t("loan.no_applications")));

    const advice = await api.get("/finance/advice").catch(() => null);
    const planCard = cardWithAction(t("rec.title"), el("a", { class: "btn small", href: "#plan" }, t("nav.plan")));
    if (risk.available && risk.data_quality) planCard.append(qualityBlock(risk.data_quality, false));
    nodes.push(el("div", { class: "grid grid-2", style: "margin-top:1rem" }, riskCard,
      el("div", { class: "stack" }, advice ? adviceCard(advice, 2) : null, planCard, weatherCard, loanCard)));

    // recent transactions, with a one-click path to the full, filterable history
    const providerName = (key) => (providers.find((p) => p.key === key) || {}).display_name
      || (String(key).startsWith("agritrust") ? t("app.name") : key === "manual" ? t("finance.other") : key);
    const txCard = cardWithAction(t("history.recent"),
      el("a", { class: "btn small secondary", href: "#transactions" }, t("history.see_all")));
    txCard.append(recentTx.length ? txTable(recentTx, providerName) : el("p", { class: "empty" }, t("transaction.no_transactions")));
    nodes.push(el("div", { style: "margin-top:1rem" }, txCard));

    if (profile && profile.monthly_series.length) {
      const chartBox = el("div");
      nodes.push(el("div", { style: "margin-top:1rem" }, card(`${t("finance.income")} / ${t("finance.expenses")}`, chartBox)));
      view.replaceChildren(...nodes);
      incomeChart(chartBox, profile.monthly_series);
      return;
    }
    view.replaceChildren(...nodes);
  };

  function incomeChart(box, series) {
    Charts.columns(box, {
      categories: series.map((s) => s.month),
      labels: series.map((s) => fmt.month(s.month)),
      series: [
        { name: t("finance.income"), color: "--series-1", values: series.map((s) => s.income) },
        { name: t("finance.expenses"), color: "--series-2", values: series.map((s) => s.expenses) },
      ],
      tableCaption: t("common.view_details"),
      ariaLabel: `${t("finance.income")} / ${t("finance.expenses")}`,
    });
  }

  /** `withSummary: false` drops the level + range lines when stat tiles above already show them. */
  function riskExplanation(container, risk, { withSummary = true } = {}) {
    const components = risk.components || (risk.explanation ? risk.explanation.factor_weights : []);
    const positives = risk.positive_factors || (risk.explanation ? risk.explanation.positive_factors : []);
    const negatives = risk.risk_factors || (risk.explanation ? risk.explanation.risk_factors : []);
    // AT.append, not Element.append: the native one would print a skipped line as "null"
    AT.append(container, [
      el("div", { class: "row" }, withSummary ? riskPill(risk.risk_level) : null,
        el("span", { class: "muted small" }, `${t("risk.confidence")} ${fmt.pct(risk.confidence)}`)),
      withSummary ? el("p", { style: "margin-top:0.5rem" }, `${t("risk.recommended")}: ${fmt.money(risk.recommended_min)} – ${fmt.money(risk.recommended_max)}`) : null,
      el("div", { class: "grid grid-3", style: "margin:0.5rem 0" },
        ["weather_risk", "market_risk", "production_risk"].map((k) =>
          el("div", {}, el("div", { class: "muted small" }, t(`risk.${k}`)), riskPill(risk[k])))),
      el("h3", {}, t("risk.explanation")),
      table([t("common.category"), [t("risk.weight"), "num"], t("common.status")],
        components.map((c) => [c.label, num(fmt.pct(c.weight)), riskPill(c.risk_level || c.direction)])),
      el("div", { class: "grid grid-2", style: "margin-top:0.75rem" },
        el("div", {}, el("h3", {}, t("risk.positive_factors")),
          positives.length ? el("ul", {}, positives.map((p) => el("li", {}, p.label))) : el("p", { class: "muted small" }, "—")),
        el("div", {}, el("h3", {}, t("risk.risk_factors")),
          negatives.length ? el("ul", {}, negatives.map((p) => el("li", {}, p.label))) : el("p", { class: "muted small" }, "—"))),
      el("p", { class: "muted small" }, t("risk.human_review"))]);
  }

  // ---------------------------------------------------------------- farm score (plan 5.1-5.6)
  const BAND_TONE = { LOW: "good", MEDIUM: "warning", HIGH: "critical" };
  const REVIEW_TONE = { PENDING_REVIEW: "warning", CONFIRMED: "good", CHANGED: "info" };

  /** Score, factors, cash flow, explanation, recommendation and review in one card. */
  function assessmentCard(a, { staff = false, onChange } = {}) {
    const detail = a.product_detail || {};
    const weather = (a.inputs && a.inputs.weather) || {};
    const cf = a.cash_flow || {};
    const productLines = [];
    if (detail.loan_amount) productLines.push(t("fu.loan_line", { amount: fmt.number(detail.loan_amount), months: detail.term_months }));
    if (detail.insurance) productLines.push(t("fu.insurance_line", {
      product: AT.lang === "sw" ? detail.insurance.product_name_sw : detail.insurance.product_name_en, premium: fmt.number(detail.insurance.premium) }));
    if (detail.cooperative) productLines.push(t("fu.coop_line", { name: detail.cooperative.name }));
    const pointsBox = el("div");

    const body = el("div", { class: "stack" },
      el("div", { class: "row between" },
        el("div", { class: "row" },
          el("span", { class: "hero-figure", style: "font-size:2.5rem" }, `${Math.round(a.risk_score)}`),
          el("span", { class: "muted" }, "/100"),
          el("span", { class: `pill ${BAND_TONE[a.band] || ""}` }, t(`risk.level_${String(a.band).toLowerCase()}`))),
        el("span", { class: `pill ${REVIEW_TONE[a.review_status] || ""}` }, t(`f5.status.${a.review_status.toLowerCase()}`))),
      el("p", { class: "muted small" }, `${t(`crop.${a.crop.toLowerCase()}`)} · ${a.region || "—"} · ${fmt.datetime(a.created_at + "Z")}`),

      el("div", {}, el("h3", {}, t("f5.explanation")),
        el("p", {}, a.explanation || "—"),
        el("span", { class: "tag" }, a.explanation_source === "claude" ? t("f5.source_claude") : t("f5.source_builtin"))),

      el("div", {}, el("h3", {}, t("f5.recommendation")),
        el("p", {}, el("strong", {}, t(`f5.product.${a.product_type.toLowerCase()}`))),
        productLines.length ? el("ul", { class: "small" }, productLines.map((x) => el("li", {}, x))) : null,
        detail.reason ? el("p", { class: "muted small" }, t(detail.reason)) : null,
        a.review_note ? el("p", { class: "small" }, el("strong", {}, `${t("f5.review")}: `), a.review_note) : null),

      el("div", {}, el("h3", {}, t("f5.formula")),
        table([t("common.category"), [t("f5.weight"), "num"], [t("f5.factor_risk"), "num"], [t("f5.points"), "num"]],
          a.components.map((c) => [t(`f5.factor.${c.key}`), num(fmt.pct(c.weight)), num(fmt.number(c.risk, 1)), num(fmt.number(c.points, 1))])
            .concat([[el("strong", {}, t("common.total")), num(""), num(""), num(el("strong", {}, fmt.number(a.risk_score, 1)))]])),
        pointsBox,
        el("p", { class: "small muted" },
          `${t("f5.rain_vs_normal")}: ${fmt.number(weather.rain_30d_mm, 1)} mm / ${weather.normal_30d_mm === null || weather.normal_30d_mm === undefined ? "—" : fmt.number(weather.normal_30d_mm, 1)} mm`,
          weather.source === "open-meteo" ? ` · ${t("f5.real_weather")}` : "")),

      el("div", {}, el("h3", {}, t("f5.cash_flow")),
        table([t("common.category"), ["", "num"]], [
          [t("crop.expected_yield"), num(`${fmt.number(cf.expected_yield_kg)} kg`)],
          [t("market.price"), num(`${fmt.money(cf.price_per_kg)}/kg`)],
          [t("crop.estimated_revenue"), num(fmt.money(cf.expected_revenue))],
          [t("crop.production_cost"), num(`− ${fmt.money(cf.input_cost)}`)],
          [el("strong", {}, t("f5.cash_flow")), num(el("strong", {}, fmt.money(cf.cash_flow_estimate)))],
        ])));

    if (staff && a.review_status === "PENDING_REVIEW") {
      const confirm = form([{ name: "note", label: t("form.note"), type: "textarea" }], t("f5.confirm"), async (data) => {
        await api.post(`/assessments/${a.id}/review`, { decision: "CONFIRM", note: data.note });
        toast(t("f5.status.confirmed")); dialog.close(); if (onChange) onChange();
      });
      const change = form([
        { name: "product_type", label: t("f5.recommendation"), type: "select", required: true,
          options: ["LOAN", "INSURANCE_LOAN", "GROUP_LENDING"].filter((x) => x !== a.product_type).map((x) => [x, t(`f5.product.${x.toLowerCase()}`)]) },
        { name: "note", label: t("form.reason"), type: "textarea", required: true },
      ], t("f5.change"), async (data) => {
        await api.post(`/assessments/${a.id}/review`, { decision: "CHANGE", product_type: data.product_type, note: data.note });
        toast(t("f5.status.changed")); dialog.close(); if (onChange) onChange();
      });
      body.append(el("section", { class: "card" }, el("h3", {}, t("f5.review")), confirm, el("h4", {}, t("f5.change")), change));
    }
    // each factor's contribution to the 0-100 total
    setTimeout(() => Charts.hbars(pointsBox, a.components.map((c) => ({ label: t(`f5.factor.${c.key}`), value: c.points })),
      { format: (v) => fmt.number(v, 1) }), 0);
    return body;
  }

  async function farmScoreSection() {
    const box = el("section", { class: "card" });
    const render = (a) => {
      const run = el("button", {
        class: "btn small", onclick: async (e) => {
          e.target.disabled = true;
          try { render(await api.post("/assessments")); toast(t("f5.title")); }
          catch (err) { toast(err.message); e.target.disabled = false; }
        },
      }, a && a.available !== false ? t("f5.rerun") : t("f5.run"));
      box.replaceChildren(el("div", { class: "card-head" }, el("h2", {}, t("f5.title")), run),
        a && a.available !== false ? assessmentCard(a) : el("p", { class: "muted" }, t("risk.not_assessed")));
    };
    render(await api.get("/assessments/latest").catch(() => null));
    return box;
  }

  // ---------------------------------------------------------------- plan
  VIEWS.f_plan = async () => {
    const [plan, recs, matches, scoreSection] = await Promise.all([
      api.get("/plan/forecast"), api.get("/plan/recommendations"), api.get("/plan/market-matches"), farmScoreSection(),
    ]);
    const balanceBox = el("div"), flowBox = el("div");
    const gapStat = plan.lowest_balance < 0
      ? stat(t("plan.gap"), fmt.money(-plan.lowest_balance), `${t("plan.lowest")}: ${fmt.month(plan.lowest_month)}`)
      : stat(t("plan.lowest"), fmt.month(plan.lowest_month), `${t("plan.no_gap")} · ${fmt.money(plan.lowest_balance)}`);

    setView(
      el("h1", {}, t("plan.title")),
      el("p", { class: "muted" }, t("plan.intro")),
      scoreSection,
      el("div", { class: "grid grid-4", style: "margin-top:1rem" },
        stat(t("finance.balance"), fmt.money(plan.opening_balance)),
        gapStat,
        stat(t("plan.financing_need"), plan.financing_need ? fmt.money(plan.financing_need) : "—"),
        stat(t("plan.closing"), fmt.compact(plan.closing_balance),
          `${fmt.compact(plan.closing_range[0])} – ${fmt.compact(plan.closing_range[1])} TZS`)),
      el("div", { style: "margin-top:1rem" }, card(t("plan.balance"), balanceBox,
        el("p", { class: "muted small", style: "margin-top:0.5rem" }, t("plan.estimate_note")))),
      el("div", { style: "margin-top:1rem" }, recommendationCards(recs)),
      el("div", { style: "margin-top:1rem" }, buyerCard(matches)),
      el("div", { style: "margin-top:1rem" }, card(t("plan.money_in_out"), flowBox)),
      el("div", { style: "margin-top:1rem" }, card(t("dq.title"), qualityBlock(plan.data_quality))),
      el("div", { style: "margin-top:1rem" }, card(t("plan.crop_calendar"),
        table([t("crop.name"), t("plan.season"), t("crop.planting_date"), t("crop.harvest_date"), [t("plan.yield_range"), "num"], [t("plan.price_range"), "num"], [t("crop.estimated_revenue"), "num"]],
          plan.crops.map((c) => [t(`crop.${c.crop.toLowerCase()}`), c.season, fmt.date(c.planting), fmt.date(c.harvest),
            num(`${fmt.number(c.yield_range_kg[0])} – ${fmt.number(c.yield_range_kg[1])}`),
            num(`${fmt.number(c.price_per_kg.low)} – ${fmt.number(c.price_per_kg.high)}`), num(fmt.money(c.expected_revenue))])),
        el("h3", { style: "margin-top:1rem" }, t("plan.assumptions")),
        el("ul", { class: "small muted" },
          el("li", {}, t("plan.assume_history", { months: plan.assumptions.history_months })),
          el("li", {}, t("plan.assume_income", { amount: fmt.money(plan.assumptions.other_income_monthly) })),
          el("li", {}, t("plan.assume_spend", { amount: fmt.money(plan.assumptions.household_spend_monthly) })),
          el("li", {}, t("plan.assume_harvest", { days: plan.assumptions.replant_gap_days })),
          el("li", {}, t("plan.assume_weather", { level: t(`risk.level_${plan.weather_risk.toLowerCase()}`), pct: fmt.pct(plan.yield_uncertainty) }))))));

    Charts.line(balanceBox, {
      points: plan.series.map((m) => ({ label: fmt.month(m.month), value: m.balance, low: m.balance_low, high: m.balance_high })),
      name: t("plan.balance"),
      bandName: t("plan.band"),
      valueFormat: (v) => fmt.money(v),
      format: (v) => fmt.compact(v),
      tableCaption: t("common.view_details"),
    });
    Charts.columns(flowBox, {
      categories: plan.series.map((m) => m.month),
      labels: plan.series.map((m) => fmt.month(m.month)),
      series: [
        { name: t("finance.income"), color: "--series-1", values: plan.series.map((m) => m.inflow) },
        { name: t("finance.expenses"), color: "--series-2", values: plan.series.map((m) => m.outflow) },
      ],
      tableCaption: t("common.view_details"),
    });
  };

  function recommendationCards(recs) {
    const reasons = (list) => el("ul", { class: "small", style: "padding-left:1.1rem;margin:0.5rem 0" }, list.map((r) => el("li", {}, reasonText(r))));
    const f = recs.finance, ins = recs.insurance, m = recs.market;
    const financeCard = el("section", { class: "card" }, el("h3", {}, t("rec.finance")),
      f.action === "LOAN"
        ? [el("div", { class: "stat" }, el("span", { class: "value" }, fmt.money(f.amount)),
          el("span", { class: "sub" }, `${t("loan.term")}: ${f.term_months} · ${t("rec.needed_by", { month: fmt.month(f.needed_by) })}`)),
          reasons(f.reasons),
          el("button", {
            class: "btn small", onclick: () => {
              sessionStorage.setItem("agritrust.loanPrefill", JSON.stringify({ amount: f.amount, term: f.term_months }));
              go("loans");
            },
          }, t("rec.apply_now"))]
        : [el("p", {}, el("strong", {}, t("rec.no_loan"))), reasons(f.reasons)]);
    const insuranceCard = el("section", { class: "card" }, el("h3", {}, t("rec.insurance")),
      ins.action === "INSURE"
        ? [el("div", { class: "stat" }, el("span", { class: "value" }, AT.lang === "sw" ? ins.product_name_sw : ins.product_name_en),
          el("span", { class: "sub" }, `${ins.farm_name} · ${t("insurance.premium")} ${fmt.money(ins.premium)}`)),
          reasons(ins.reasons),
          el("button", {
            class: "btn small", onclick: async (e) => {
              e.target.disabled = true;
              try {
                const policy = await api.post("/insurance/policies", { farm_id: ins.farm_id, product_code: ins.product_code });
                toast(t("insurance.requested", { number: policy.policy_number }), 6000);
                route();
              } catch (err) { toast(err.message); e.target.disabled = false; }
            },
          }, t("rec.insure_now"))]
        : [el("p", {}, el("strong", {}, t("rec.no_insurance"))), reasons(ins.reasons)]);
    const marketCard = el("section", { class: "card" }, el("h3", {}, t("rec.market")),
      m.best
        ? [el("div", { class: "stat" }, el("span", { class: "value" }, m.best.buyer),
          el("span", { class: "sub" }, `${fmt.number(m.best.quantity_kg)} kg · ${fmt.money(m.best.expected_value)}`)),
          reasons(m.reasons), el("a", { class: "btn small secondary", href: "#plan-buyers", onclick: (e) => {
            e.preventDefault(); document.getElementById("plan-buyers").scrollIntoView({ behavior: "smooth" });
          } }, t("rec.connect"))]
        : reasons(m.reasons));
    return el("div", {},
      el("div", { class: "row between" }, el("h2", {}, t("rec.title")), recs.data_quality.needs_review ? qualityPill(recs.data_quality) : null),
      el("div", { class: "grid grid-3" }, financeCard, insuranceCard, marketCard),
      el("p", { class: "muted small", style: "margin-top:0.5rem" }, t("risk.human_review")));
  }

  function buyerCard(matches) {
    return el("section", { class: "card", id: "plan-buyers" },
      el("div", { class: "card-head" }, el("h2", {}, t("rec.buyers"))),
      table([t("market.buyer"), t("market.crop"), t("common.region"), [t("rec.net_price"), "num"], [t("rec.vs_market"), "num"], [t("rec.quantity"), "num"], [t("rec.expected_value"), "num"], ""],
        matches.map((m) => [
          el("span", {}, m.buyer, el("br"), el("span", { class: "muted small" }, `${t(`buyer.${m.buyer_type.toLowerCase()}`)} · ${m.collection_point || ""}`)),
          t(`crop.${m.crop.toLowerCase()}`), m.region || "—",
          num(`${fmt.number(m.net_price_per_kg)}/kg`),
          num(m.premium_pct === null ? "—" : `${m.premium_pct >= 0 ? "+" : ""}${fmt.pct(m.premium_pct, 1)}`),
          num(fmt.number(m.quantity_kg)), num(fmt.money(m.expected_value)),
          m.accepted ? pill("ACTIVE", t("rec.connected"))
            : m.meets_minimum ? el("button", {
              class: "btn small", onclick: async (e) => {
                e.target.disabled = true;
                try {
                  const res = await api.post(`/plan/market-matches/${m.offer_id}/accept`);
                  toast(t("rec.match_body", { buyer: res.buyer, kg: fmt.number(res.quantity_kg) }));
                  refreshUnread();
                  route();
                } catch (err) { toast(err.message); e.target.disabled = false; }
              },
            }, t("rec.connect"))
              : el("span", { class: "muted small", title: t("rec.below_minimum") }, t("rec.minimum", { kg: fmt.number(m.min_quantity_kg) })),
        ]), t("rec.reason_no_buyer")),
      el("p", { class: "muted small", style: "margin-top:0.5rem" }, t("rec.transport_note")));
  }

  // ---------------------------------------------------------------- farm
  VIEWS.f_farm = async () => {
    const farms = (await api.get("/farms")).filter((f) => f.is_active);
    const activeFarms = farms.filter((f) => f.is_active);
    const addFarm = form([
      { name: "name", label: t("farm.name"), required: true },
      { name: "area_acres", label: t("farm.area"), type: "number", required: true, min: 0.1 },
      { name: "irrigation_available", label: t("farm.irrigation"), type: "checkbox" },
    ], t("farm.add"), async (data) => {
      await api.post("/farms", data);
      dialog.close();
      toast(t("farm.saved"));
      route();
    });
    // This screen is the farm's own record: the farms, their crops and their
    // activities. The forecast, prices, harvest outlook and chat live on the AI
    // Assistant screen - showing both here meant the two screens looked
    // identical, and the farms themselves were overwritten before they showed.
    const nodes = [
      el("div", { class: "row between" }, el("h1", {}, t("farm.title")),
        el("button", { class: "btn", onclick: () => openDialog(t("farm.add"), addFarm) }, t("farm.add"))),
    ];
    if (!farms.length) nodes.push(card(null, el("p", { class: "empty" }, t("farm.no_farms"))));
    for (const farm of farms) nodes.push(await farmCard(farm));
    setView(...nodes);
    // a screen that needs a crop (the forecast) can send the farmer straight here to
    // add one, the same way the plan screen hands over a pre-filled loan amount
    if (sessionStorage.getItem("agritrust.addCrop") && farms.length) {
      sessionStorage.removeItem("agritrust.addCrop");
      const first = view.querySelector("button.btn.small.secondary");
      if (first) first.click();
    }
  };

  // ---------------------------------------------------------------- AI assistant
  // One plain chat box. The server answers in the language the farmer wrote in
  // (Kiswahili or English); the page language is only the fallback.
  VIEWS.f_assistant = async () => {
    const log = el("div", { class: "chat-log", "aria-live": "polite" },
      el("div", { class: "msg bot" }, t("assistant.greeting", { name: user.full_name.split(" ")[0] })));
    const input = el("input", { placeholder: t("assistant.placeholder"), "aria-label": t("assistant.placeholder"), maxlength: 1000 });
    const send = async (text) => {
      if (!text.trim()) return;
      log.append(el("div", { class: "msg me" }, text));
      input.value = "";
      const pending = el("div", { class: "msg bot" }, "…");
      log.append(pending);
      log.scrollTop = log.scrollHeight;
      try {
        const res = await api.post("/ai/chat", { message: text, language: window.AT.lang });
        pending.textContent = res.reply;
      } catch (err) {
        pending.textContent = err.message;
      }
      log.scrollTop = log.scrollHeight;
    };
    const chatForm = el("form", {}, input, el("button", { class: "btn", type: "submit" }, t("assistant.send")));
    chatForm.addEventListener("submit", (e) => { e.preventDefault(); send(input.value); });
    setView(
      el("div", { class: "row between" }, el("h1", {}, t("assistant.title"))),
      el("section", { class: "card chat" }, log, chatForm));
    input.focus();
  };

  async function farmCard(farm) {
    const [crops, activities] = await Promise.all([
      api.get(`/farms/${farm.id}/crops`), api.get(`/farms/${farm.id}/activities`),
    ]);
    const cropForm = () => form([
      { name: "crop_name", label: t("crop.name"), type: "select", required: true,
        options: ["Maize", "Rice", "Beans", "Sorghum", "Cassava", "Wheat", "Cotton", "Groundnut", "Vegetables"].map((c) => [c, t(`crop.${c.toLowerCase()}`)]) },
      { name: "season", label: t("crop.season") },
      { name: "planting_date", label: t("crop.planting_date"), type: "date" },
      { name: "expected_harvest_date", label: t("crop.harvest_date"), type: "date" },
      { name: "expected_yield_kg", label: t("crop.expected_yield"), type: "number", min: 0 },
      { name: "previous_yield_kg", label: t("crop.previous_yield"), type: "number", min: 0 },
      { name: "seed_cost", label: t("crop.seed_cost"), type: "number", min: 0 },
      { name: "fertilizer_cost", label: t("crop.fertilizer_cost"), type: "number", min: 0 },
      { name: "labour_cost", label: t("crop.labour_cost"), type: "number", min: 0 },
      { name: "equipment_cost", label: t("crop.equipment_cost"), type: "number", min: 0 },
    ], t("crop.add"), async (data) => {
      await api.post(`/farms/${farm.id}/crops`, data);
      dialog.close(); toast(t("crop.saved")); route();
    });
    const activityForm = () => form([
      { name: "activity_type", label: t("farm.activity_type"), type: "select", required: true,
        options: optionList(["LAND_PREPARATION", "PLANTING", "FERTILIZING", "SPRAYING", "IRRIGATION", "WEEDING", "HARVESTING", "OTHER"], "activity") },
      { name: "activity_date", label: t("farm.activity_date"), type: "date", required: true, value: new Date().toISOString().slice(0, 10) },
      { name: "crop_id", label: t("crop.name"), type: "select", options: crops.map((c) => [c.id, t(`crop.${c.crop_name.toLowerCase()}`)]) },
      { name: "cost", label: t("farm.cost"), type: "number", min: 0 },
      { name: "labour_count", label: t("farm.labour_count"), type: "number", min: 0, step: 1 },
      { name: "description", label: t("common.description") },
    ], t("farm.add_activity"), async (data) => {
      await api.post(`/farms/${farm.id}/activities`, data);
      dialog.close(); toast(t("farm.activity_saved")); route();
    });

    return el("section", { class: "card", style: "margin-top:1rem" },
      el("div", { class: "card-head" },
        el("div", {}, el("h2", {}, farm.name),
          el("span", { class: "muted small" }, [farm.village, farm.district].filter(Boolean).join(", "),
            ` · ${fmt.number(farm.area_acres, 1)} ${t("common.acres")}`,
            farm.irrigation_available ? ` · ${t("farm.irrigation")}` : "")),
        el("div", { class: "row" },
          el("button", { class: "btn small secondary", onclick: () => openDialog(t("crop.add"), cropForm()) }, t("crop.add")),
          el("button", { class: "btn small secondary", onclick: () => openDialog(t("farm.add_activity"), activityForm()) }, t("farm.add_activity")))),
      el("h3", {}, t("crop.title")),
      table([t("crop.name"), t("crop.planting_date"), t("crop.harvest_date"), [t("crop.expected_yield"), "num"], [t("crop.production_cost"), "num"], t("common.status")],
        crops.map((c) => [t(`crop.${c.crop_name.toLowerCase()}`), fmt.date(c.planting_date), fmt.date(c.expected_harvest_date),
          num(fmt.number(c.expected_yield_kg)), num(fmt.money(c.production_cost)), c.is_current ? pill("ACTIVE") : pill("COMPLETED")])),
      el("h3", { style: "margin-top:1rem" }, t("farm.activities")),
      table([t("common.date"), t("farm.activity_type"), [t("farm.labour_count"), "num"], [t("farm.cost"), "num"]],
        activities.slice(0, 8).map((a) => [fmt.date(a.activity_date), t(`activity.${a.activity_type.toLowerCase()}`), num(a.labour_count), num(fmt.money(a.cost))])));
  }

  // ---------------------------------------------------------------- payments
  let providersPromise;
  const loadProviders = () => (providersPromise = providersPromise || api.get("/finance/providers"));
  const KIND_LABEL = { MOBILE_MONEY: "payment.mobile_money", BANK: "payment.banks", GATEWAY: "payment.gateways" };
  async function providerName(key) {
    const list = await loadProviders();
    const p = list.find((x) => x.key === key);
    return p ? p.display_name : key;
  }
  function providerOptions(list, filter = () => true) {
    return list.filter(filter).map((p) => [p.key, `${p.display_name} · ${t(KIND_LABEL[p.kind] || "finance.other")}`]);
  }

  const LAST_PROVIDER_KEY = "agritrust.lastProvider";
  const PAY_STEPS = ["wizard.step_what", "wizard.step_amount", "wizard.step_how", "wizard.step_confirm", "wizard.step_pay"];

  function stepper(current) {
    return el("ol", { class: "stepper", "aria-label": t("payment.make") },
      PAY_STEPS.map((key, i) => el("li", { class: i < current ? "done" : i === current ? "current" : "", "aria-current": i === current ? "step" : null },
        el("span", { class: "dot" }, i < current ? "✓" : String(i + 1)), el("span", { class: "label" }, t(key)))));
  }

  async function activeLoans() {
    const loans = await api.get("/loans");
    const out = [];
    for (const l of loans.filter((x) => ["APPROVED", "DISBURSED", "ACTIVE"].includes(x.status))) {
      const d = await api.get(`/loans/${l.id}`);
      if (d.outstanding_balance > 0) out.push({ id: l.id, reference: l.reference, outstanding: Number(d.outstanding_balance), monthly: Number(d.monthly_payment) });
    }
    return out;
  }

  /**
   * Guided payment: what -> amount -> how -> confirm -> pay (QR / Lipa Namba) -> receipt.
   * Entry points may skip ahead by passing purpose / targetId / amount.
   */
  async function paymentDialog({ purpose, targetId, amount } = {}) {
    const body = el("div", { class: "wizard" });
    openDialog(t("payment.make"), body);
    body.append(loading());
    const [providers, loans, advice] = await Promise.all([
      loadProviders(), activeLoans(), api.get("/finance/advice").catch(() => null),
    ]);
    const state = { purpose, targetId, amount, provider: localStorage.getItem(LAST_PROVIDER_KEY) || "mpesa" };
    state.loan = loans.find((l) => l.id === targetId) || null;

    const nav = (backTo, nextLabel, onNext, disabled) => el("div", { class: "row between", style: "margin-top:1.25rem" },
      backTo !== null ? el("button", { class: "btn secondary", type: "button", onclick: backTo }, `← ${t("wizard.back")}`) : el("span"),
      onNext ? el("button", { class: "btn", type: "button", disabled, onclick: onNext }, nextLabel || t("wizard.next")) : null);

    const stepWhat = () => {
      const choose = (p, loan) => { state.purpose = p; state.loan = loan; state.targetId = loan ? loan.id : null; state.amount = null; stepAmount(); };
      body.replaceChildren(stepper(0), el("h3", {}, t("wizard.what_title")),
        el("div", { class: "choices" },
          loans.map((l) => el("button", { class: "choice", type: "button", onclick: () => choose("LOAN_REPAYMENT", l) },
            el("strong", {}, t("wizard.repay_loan", { reference: l.reference })),
            el("span", { class: "muted small" }, t("wizard.balance_left", { amount: fmt.money(l.outstanding) })))),
          el("button", { class: "choice", type: "button", onclick: () => choose("SAVINGS_DEPOSIT", null) },
            el("strong", {}, t("wizard.save_money")), el("span", { class: "muted small" }, t("wizard.save_hint")))));
    };

    const stepAmount = () => {
      const input = el("input", { type: "number", min: 100, step: 100, inputmode: "numeric", value: state.amount || null, "aria-label": t("wizard.custom") });
      const presets = state.purpose === "LOAN_REPAYMENT" && state.loan
        ? [[t("wizard.instalment"), Math.min(state.loan.monthly, state.loan.outstanding)], [t("wizard.full_balance"), state.loan.outstanding]]
        : [advice && advice.save_now ? [t("wizard.recommended"), advice.save_now] : null, ["", 10000], ["", 50000], ["", 100000]].filter(Boolean);
      const chips = el("div", { class: "chips" }, presets.map(([label, value]) => el("button", {
        class: `chip${Number(state.amount) === Number(value) ? " selected" : ""}`, type: "button",
        onclick: () => { state.amount = Math.round(value); stepAmount(); },
      }, label ? `${label} · ${fmt.money(value)}` : fmt.money(value))));
      input.addEventListener("input", () => { state.amount = Number(input.value) || null; next.disabled = !(state.amount >= 100); });
      const next = el("button", { class: "btn", type: "button", disabled: !(state.amount >= 100), onclick: () => stepHow() }, t("wizard.next"));
      body.replaceChildren(stepper(1), el("h3", {}, t("wizard.amount_title")),
        el("p", { class: "muted small" }, state.purpose === "LOAN_REPAYMENT" && state.loan
          ? t("wizard.balance_left", { amount: fmt.money(state.loan.outstanding) }) : t("wizard.save_hint")),
        chips, el("div", { class: "field", style: "margin-top:0.75rem;max-width:260px" }, el("label", {}, t("wizard.custom")), input),
        el("div", { class: "row between", style: "margin-top:1.25rem" },
          purpose ? el("span") : el("button", { class: "btn secondary", type: "button", onclick: stepWhat }, `← ${t("wizard.back")}`), next));
    };

    const stepHow = () => {
      const tile = (p) => el("button", {
        class: `tile${state.provider === p.key ? " selected" : ""}`, type: "button", "aria-pressed": state.provider === p.key ? "true" : "false",
        onclick: () => { state.provider = p.key; stepHow(); },
      }, el("span", { class: "tile-mark", "aria-hidden": "true" }, p.display_name.replace(/[^A-Za-z]/g, "").slice(0, 2).toUpperCase()),
        el("span", {}, p.display_name),
        localStorage.getItem(LAST_PROVIDER_KEY) === p.key ? el("span", { class: "muted small" }, t("wizard.last_used")) : null);
      body.replaceChildren(stepper(2), el("h3", {}, t("wizard.how_title")),
        ...["MOBILE_MONEY", "BANK", "GATEWAY"].flatMap((kind) => [el("h4", { class: "muted small", style: "margin:0.75rem 0 0.35rem" }, t(KIND_LABEL[kind])),
          el("div", { class: "tiles" }, providers.filter((p) => p.kind === kind).map(tile))]),
        nav(stepAmount, null, stepConfirm, !state.provider));
    };

    const stepConfirm = () => {
      const p = providers.find((x) => x.key === state.provider);
      const payBtn = el("button", {
        class: "btn", type: "button", onclick: async () => {
          payBtn.disabled = true;
          try {
            localStorage.setItem(LAST_PROVIDER_KEY, state.provider);
            const req = await api.post("/payments/requests", {
              provider: state.provider, amount: state.amount, purpose: state.purpose,
              target_id: state.purpose === "LOAN_REPAYMENT" ? state.targetId : undefined,
            });
            showRequest(body, req, state);
          } catch (err) { toast(err.message); payBtn.disabled = false; }
        },
      }, t("wizard.pay_now"));
      body.replaceChildren(stepper(3), el("h3", {}, t("wizard.confirm_title")),
        el("div", { class: "hero-figure", style: "font-size:2.25rem;margin:0.25rem 0 0.75rem" }, fmt.money(state.amount)),
        el("table", {}, el("tbody", {}, [
          [t("payment.purpose"), state.purpose === "LOAN_REPAYMENT" && state.loan ? t("wizard.repay_loan", { reference: state.loan.reference }) : t("payment.purpose_savings_deposit")],
          [t("common.provider"), p ? p.display_name : state.provider],
          [t("payment.pay_to"), "AgriTrust AI"],
        ].map(([k, v]) => el("tr", {}, el("th", {}, k), el("td", {}, v))))),
        el("div", { class: "row between", style: "margin-top:1.25rem" },
          el("button", { class: "btn secondary", type: "button", onclick: stepHow }, `← ${t("wizard.back")}`), payBtn));
    };

    if (!state.purpose) stepWhat();
    else if (!state.amount) stepAmount();
    else stepHow();
  }

  function showRequest(body, req, state) {
    let timer;
    const statusBox = el("div");
    const stepsList = req.channel === "LIPA_NAMBA"
      ? [t("payment.step_dial", { code: req.ussd_code || req.provider_name }), t("payment.step_lipa", { number: req.lipa_number }),
        t("payment.step_amount", { amount: fmt.number(req.amount) }), t("payment.step_reference", { reference: req.reference }), t("payment.step_pin")]
      : req.channel === "BANK_TRANSFER"
        ? [t("payment.step_bank", { account: req.lipa_number }), t("payment.step_amount", { amount: fmt.number(req.amount) }), t("payment.step_reference", { reference: req.reference })]
        : [t("payment.step_checkout", { provider: req.provider_name }), t("payment.step_reference", { reference: req.reference })];

    const showReceipt = async (r) => {
      clearInterval(timer);
      refreshUnread();
      let loanBalance = null;
      if (r.purpose === "LOAN_REPAYMENT" && state && state.targetId) {
        loanBalance = await api.get(`/loans/${state.targetId}`).then((d) => d.outstanding_balance).catch(() => null);
      }
      body.replaceChildren(stepper(5), receipt({
        amount: r.amount, reference: r.reference, provider: r.provider_name, time: r.paid_at,
        purpose: t(`payment.purpose_${r.purpose.toLowerCase()}`), providerRef: r.provider_reference, loanBalance,
      }));
    };
    const renderStatus = (r) => {
      if (r.status === "PAID") return showReceipt(r);
      statusBox.replaceChildren(el("div", { class: "row" },
        pill(r.status === "PENDING" ? "PENDING" : "CANCELLED", t(`payment.status_${r.status.toLowerCase()}`)),
        r.status === "PENDING" ? el("span", { class: "muted small" }, t("payment.expires", { time: fmt.datetime(r.expires_at + "Z") })) : null));
      if (r.status !== "PENDING") clearInterval(timer);
    };
    const simulate = req.is_sandbox ? el("button", {
      class: "btn secondary", onclick: async (e) => {
        e.target.disabled = true;
        try { renderStatus(await api.post(`/payments/requests/${req.reference}/confirm`)); } catch (err) { toast(err.message); e.target.disabled = false; }
      },
    }, t("payment.simulate")) : null;
    body.replaceChildren(
      stepper(4),
      el("div", { class: "grid grid-2" },
        el("div", { style: "text-align:center" },
          el("img", { src: `/api/v1/payments/requests/${req.reference}/qr.svg`, alt: `QR ${req.reference}`, width: 220, height: 220, style: "background:#fff;border-radius:8px;padding:6px" }),
          el("p", { class: "small muted" }, t("payment.scan")),
          el("a", { href: req.pay_url, target: "_blank", rel: "noopener", class: "small" }, req.pay_url)),
        el("div", {},
          el("div", { class: "hero-figure", style: "font-size:2rem" }, fmt.money(req.amount)),
          el("table", {}, el("tbody", {},
            [[t("common.provider"), req.provider_name],
              req.lipa_number ? [req.channel === "BANK_TRANSFER" ? t("payment.account") : t("payment.lipa_number"), el("strong", { style: "font-size:1.2rem;letter-spacing:0.05em" }, req.lipa_number)] : null,
              [t("common.reference"), el("strong", {}, req.reference)]].filter(Boolean)
              .map(([k, v]) => el("tr", {}, el("th", {}, k), el("td", {}, v))))),
          statusBox)),
      el("h3", { style: "margin-top:1rem" }, t("payment.how_to_pay")),
      el("ol", { class: "small" }, stepsList.map((x) => el("li", {}, x))),
      simulate);
    renderStatus(req);
    timer = setInterval(async () => {
      if (!dialog.open) return clearInterval(timer);
      try { renderStatus(await api.get(`/payments/requests/${req.reference}`)); } catch (_) { clearInterval(timer); }
    }, 3000);
    dialog.addEventListener("close", () => { clearInterval(timer); route(); }, { once: true });
  }

  /** Printable receipt used after a payment and from the transaction history. */
  function receipt(r) {
    return el("div", { class: "receipt" },
      el("div", { class: "receipt-head" }, el("span", { class: "brand-mark", "aria-hidden": "true" }, "AT"),
        el("div", {}, el("strong", {}, "AgriTrust AI"), el("div", { class: "muted small" }, t("receipt.title")))),
      el("div", { class: "receipt-ok", "aria-hidden": "true" }, "✓"),
      el("div", { class: "hero-figure", style: "font-size:2.25rem;text-align:center" }, `${r.direction === "out" ? "− " : r.direction === "in" ? "+ " : ""}${fmt.money(r.amount)}`),
      el("p", { style: "text-align:center" }, el("strong", {}, r.purpose)),
      el("table", {}, el("tbody", {}, [
        [t("common.reference"), r.reference],
        r.providerRef ? [`${r.provider} ref`, r.providerRef] : null,
        [t("common.provider"), r.provider],
        r.account ? [t("receipt.account"), r.account] : null,
        r.farmer ? [t("receipt.farmer"), r.farmer] : null,
        [t("receipt.time"), fmt.datetime(r.time && !String(r.time).endsWith("Z") ? `${r.time}Z` : r.time)],
        r.status ? [t("common.status"), r.status] : null,
        r.loanBalance !== null && r.loanBalance !== undefined ? [t("receipt.new_balance"), fmt.money(r.loanBalance)] : null,
      ].filter(Boolean).map(([k, v]) => el("tr", {}, el("th", {}, k), el("td", { class: "num" }, v))))),
      el("div", { class: "row between no-print", style: "margin-top:1rem" },
        el("button", { class: "btn secondary", type: "button", onclick: () => window.print() }, t("receipt.print")),
        el("button", { class: "btn", type: "button", onclick: () => dialog.close() }, t("receipt.done"))));
  }

  // ---------------------------------------------------------------- money
  const TX_TYPES = ["DEPOSIT", "WITHDRAWAL", "FARM_INPUT", "CROP_SALE", "LOAN_REPAYMENT", "INSURANCE_PAYMENT", "OTHER"];
  VIEWS.f_money = async () => {
    const [accounts, txs, profile, providers, requests, advice] = await Promise.all([
      api.get("/finance/accounts"), api.get("/finance/transactions?limit=5"),
      api.get("/finance/profile"), loadProviders(), api.get("/payments/requests"), api.get("/finance/advice"),
    ]);
    const nameOf = (key) => (providers.find((p) => p.key === key) || {}).display_name
      || (String(key).startsWith("agritrust") ? t("app.name") : key === "manual" ? t("finance.other") : key);
    const accountForm = form([
      { name: "provider", label: t("finance.provider"), type: "select", required: true,
        options: providerOptions(providers, (p) => p.kind !== "GATEWAY") },
      { name: "account_type", label: t("finance.account_type"), type: "select", required: true,
        options: optionList(["MOBILE_MONEY", "BANK", "SAVINGS", "OTHER"], "finance") },
      { name: "account_number", label: t("finance.account_number"), required: true },
      { name: "account_name", label: t("finance.account_name") },
      { name: "balance", label: t("finance.balance"), type: "number", min: 0 },
      { name: "is_primary", label: t("finance.primary"), type: "checkbox" },
    ], t("finance.add_account"), async (data) => {
      await api.post("/finance/accounts", data);
      dialog.close(); toast(t("finance.account_added")); route();
    });

    const spendBox = el("div");
    setView(
      el("div", { class: "row between" }, el("h1", {}, t("finance.title")),
        el("button", { class: "btn", onclick: () => paymentDialog() }, t("payment.make"))),
      el("div", { class: "grid grid-4" },
        stat(t("advice.earned_30"), fmt.money(advice.earned)),
        stat(t("advice.spent_30"), fmt.money(advice.spent)),
        stat(t("advice.left"), fmt.money(advice.surplus), advice.savings_rate !== null ? `${t("finance.savings_rate")} ${fmt.pct(advice.savings_rate)}` : null),
        stat(t("advice.suggested_saving"), advice.save_now ? fmt.money(advice.save_now) : "—", `${t("finance.balance")}: ${fmt.money(advice.balance)}`)),
      el("div", { class: "stack", style: "margin-top:1rem" },
        adviceCard(advice),
        card(t("advice.where_money_went"), spendBox)),
      el("div", { style: "margin-top:1rem" }, cardWithAction(t("history.recent"),
        el("a", { class: "btn small secondary", href: "#transactions" }, t("history.see_all")),
        txTable(txs, nameOf))),
      el("div", { style: "margin-top:1rem" }, card(t("payment.channels"),
        el("div", { class: "grid grid-3" }, ["MOBILE_MONEY", "BANK", "GATEWAY"].map((kind) =>
          el("div", {}, el("h3", {}, t(KIND_LABEL[kind])),
            table([t("common.provider"), t("payment.lipa_number")], providers.filter((p) => p.kind === kind).map((p) =>
              [p.display_name, p.lipa_number ? el("span", { class: "num" }, p.lipa_number) : el("span", { class: "muted small" }, t("payment.checkout"))]))))),
        requests.length ? [el("h3", { style: "margin-top:1rem" }, t("payment.recent")),
          table([t("common.date"), t("common.reference"), t("common.provider"), t("payment.purpose"), [t("common.amount"), "num"], t("common.status")],
            requests.slice(0, 5).map((r) => [fmt.datetime((r.paid_at || r.expires_at) + "Z"), r.reference, r.provider_name,
              t(`payment.purpose_${r.purpose.toLowerCase()}`), num(fmt.money(r.amount)),
              pill(r.status === "PAID" ? "PAID" : r.status === "PENDING" ? "PENDING" : "CANCELLED", t(`payment.status_${r.status.toLowerCase()}`))]))] : null)),
      el("div", { class: "grid grid-2", style: "margin-top:1rem" },
        cardWithAction(t("finance.accounts"),
          el("button", { class: "btn small secondary", onclick: () => openDialog(t("finance.add_account"), accountForm) }, t("finance.add_account")),
          table([t("finance.provider"), t("finance.account_number"), [t("finance.balance"), "num"]],
            accounts.map((a) => [el("span", {}, nameOf(a.provider), " ", a.is_primary ? pill("ACTIVE", t("finance.primary")) : null),
              a.account_number_masked, num(fmt.money(a.balance))]), t("finance.no_accounts"))),
        card(t("finance.profile"),
          table([t("common.category"), ["", "num"]], [
            [t("finance.monthly_income"), num(fmt.money(profile.monthly_income))],
            [t("finance.monthly_expenses"), num(fmt.money(profile.monthly_expenses))],
            [t("finance.repayment_behaviour"), num(fmt.pct(profile.on_time_repayment_rate))],
            [t("risk.financial_health"), pill(profile.financial_health)],
          ]))));
    spendingChart(spendBox, advice);
  };

  function spendingChart(box, advice) {
    const rows = advice.breakdown.filter((b) => b.kind === "out" && (b.last_30_days || b.usual_month));
    if (!rows.length) { box.append(el("p", { class: "empty" }, t("common.no_data"))); return; }
    Charts.columns(box, {
      categories: rows.map((r) => r.category),
      labels: rows.map((r) => t(`cat.${r.category}`)),
      series: [
        { name: t("advice.last_30"), color: "--series-1", values: rows.map((r) => r.last_30_days) },
        { name: t("advice.usual_month"), color: "--series-2", values: rows.map((r) => r.usual_month) },
      ],
      tableCaption: t("common.view_details"),
    });
  }

  function adviceCard(advice, limit) {
    const TONE = { good: "good", warning: "serious", info: "info" };
    const tips = limit ? advice.tips.slice(0, limit) : advice.tips;
    const actionFor = (tip) => {
      const a = tip.action;
      if (!a) return null;
      if (a.type === "SAVE" && a.amount > 0) {
        return el("button", { class: "btn small", onclick: () => paymentDialog({ purpose: "SAVINGS_DEPOSIT", amount: a.amount }) },
          t("advice.save_button", { amount: fmt.money(a.amount) }));
      }
      if (a.type === "REPAY") {
        return el("button", { class: "btn small secondary", onclick: () => paymentDialog({ purpose: "LOAN_REPAYMENT", targetId: a.application_id, amount: a.amount }) },
          t("advice.repay_button", { amount: fmt.money(a.amount) }));
      }
      if (a.type === "LOAN") return el("a", { class: "btn small secondary", href: "#loans" }, t("advice.loan_button"));
      return null;
    };
    return el("section", { class: "card" },
      el("h2", {}, t("advice.title")),
      el("p", { class: "muted small" }, t("advice.subtitle")),
      tips.length
        ? el("ul", { class: "tips" }, tips.map((tip) => el("li", { class: `tip ${TONE[tip.tone] || ""}` },
          el("p", {}, adviceText(tip)), actionFor(tip))))
        : el("p", { class: "empty" }, t("common.no_data")));
  }

  function adviceText(tip) {
    const p = {};
    for (const [k, v] of Object.entries(tip.params || {})) {
      if (["earned", "spent", "save", "saved", "amount", "gap", "cut", "per_month", "short", "target", "balance", "usual"].includes(k)) p[k] = fmt.money(v);
      else if (k === "month") p[k] = fmt.month(v);
      else if (k === "pct") p[k] = fmt.pct(v);
      else if (k === "category") p[k] = t(`cat.${v}`);
      else if (k === "crop") p[k] = v ? t(`crop.${String(v).toLowerCase()}`) : "";
      else p[k] = v;
    }
    return t(tip.key, p);
  }

  function txTable(txs, nameOf, emptyText) {
    return table([t("common.date"), t("transaction.type"), t("common.provider"), [t("common.amount"), "num"], t("common.status"), ""],
      txs.map((x) => [fmt.date(x.transaction_date), t(`transaction.${x.transaction_type.toLowerCase()}`), nameOf(x.provider),
        num(el("span", { class: x.direction === "in" ? "amount-in" : "amount-out" }, `${x.direction === "in" ? "+" : "−"} ${fmt.money(x.amount)}`)),
        pill(x.status),
        el("button", { class: "btn small secondary", type: "button", onclick: () => receiptDialog(x.id) }, t("receipt.title"))]),
      emptyText || t("transaction.no_transactions"));
  }

  async function receiptDialog(id) {
    const r = await api.get(`/finance/transactions/${id}`);
    openDialog(t("receipt.title"), receipt({
      amount: r.amount, direction: r.direction, reference: r.reference || "—", provider: r.provider_name,
      purpose: t(`transaction.${r.transaction_type.toLowerCase()}`), time: r.transaction_date, account: r.account,
      farmer: r.farmer_name, status: t(`transaction.${r.status.toLowerCase()}`),
    }));
  }

  // ---------------------------------------------------------------- transaction history
  VIEWS.f_transactions = async () => {
    const providers = await loadProviders();
    const nameOf = (key) => (providers.find((p) => p.key === key) || {}).display_name
      || (String(key).startsWith("agritrust") ? t("app.name") : key === "manual" ? t("finance.other") : key);
    const PAGE = 25;
    let offset = 0;
    const period = el("select", { "aria-label": t("history.period") },
      [["30", "history.last_30"], ["90", "history.last_90"], ["365", "history.last_365"], ["", "history.all"]]
        .map(([v, k]) => el("option", { value: v, selected: v === "90" }, t(k))));
    const type = el("select", { "aria-label": t("transaction.type") }, el("option", { value: "" }, t("history.all_types")),
      [...TX_TYPES, "LOAN_DISBURSEMENT"].map((k) => el("option", { value: k }, t(`transaction.${k.toLowerCase()}`))));
    const direction = el("select", { "aria-label": t("transaction.direction") },
      el("option", { value: "" }, t("history.in_and_out")), el("option", { value: "in" }, t("transaction.in")), el("option", { value: "out" }, t("transaction.out")));
    const search = el("input", { type: "search", placeholder: t("history.search"), "aria-label": t("history.search") });
    const summaryBox = el("div", { class: "grid grid-4" });
    const typeBox = el("div");
    const listBox = el("div");
    const more = el("button", { class: "btn secondary hidden", type: "button" }, t("history.load_more"));

    const params = () => {
      const q = new URLSearchParams();
      if (period.value) q.set("date_from", new Date(Date.now() - Number(period.value) * 864e5).toISOString().slice(0, 10));
      if (type.value) q.set("transaction_type", type.value);
      if (direction.value) q.set("direction", direction.value);
      if (search.value.trim()) q.set("q", search.value.trim());
      return q;
    };
    const monthGroups = (rows) => {
      const groups = [];
      for (const x of rows) {
        const key = String(x.transaction_date).slice(0, 7);
        if (!groups.length || groups[groups.length - 1].key !== key) groups.push({ key, rows: [] });
        groups[groups.length - 1].rows.push(x);
      }
      return groups;
    };
    let shown = [];
    const renderList = () => {
      if (!shown.length) { listBox.replaceChildren(el("p", { class: "empty" }, t("history.none"))); return; }
      listBox.replaceChildren(...monthGroups(shown).map((g) =>
        el("div", { class: "month-group" }, el("h3", {}, fmt.month(g.key)), txTable(g.rows, nameOf))));
    };
    const load = async (reset) => {
      if (reset) { offset = 0; shown = []; listBox.style.opacity = "0.5"; }
      const q = params();
      const [summary, rows] = await Promise.all([
        reset ? api.get(`/finance/transactions/summary?${q}`) : null,
        api.get(`/finance/transactions?${q}&limit=${PAGE}&offset=${offset}`),
      ]);
      if (summary) {
        summaryBox.replaceChildren(
          stat(t("history.money_in"), fmt.money(summary.money_in)),
          stat(t("history.money_out"), fmt.money(summary.money_out)),
          stat(t("history.net"), `${summary.net >= 0 ? "+" : "−"} ${fmt.money(Math.abs(summary.net))}`),
          stat(t("transaction.title"), t("history.count", { n: fmt.number(summary.count) })));
        const byType = Object.entries(summary.by_type || {});
        typeBox.replaceChildren(byType.length
          ? table([t("transaction.type"), [t("common.amount"), "num"]],
            byType.map(([k, v]) => [t(`transaction.${k.toLowerCase()}`), num(fmt.money(v))]))
          : el("p", { class: "empty" }, t("history.none")));
      }
      shown = shown.concat(rows);
      offset += rows.length;
      more.classList.toggle("hidden", rows.length < PAGE);
      listBox.style.opacity = "";
      renderList();
    };
    const exportBtn = el("button", {
      class: "btn secondary", type: "button", onclick: async () => {
        // fetch with the bearer token, then hand the file to the browser
        const res = await fetch(`/api/v1/finance/transactions/export.csv?${params()}`, {
          headers: { Authorization: `Bearer ${tokens.get().access}` },
        });
        if (!res.ok) { toast(t("common.error")); return; }
        const url = URL.createObjectURL(await res.blob());
        const a = el("a", { href: url, download: `agritrust-transactions-${new Date().toISOString().slice(0, 10)}.csv` });
        document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(url);
      },
    }, t("history.export"));
    let timer;
    [period, type, direction].forEach((n) => n.addEventListener("change", () => guard(listBox, () => load(true))));
    search.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(() => guard(listBox, () => load(true)), 300); });
    more.addEventListener("click", () => guard(listBox, () => load(false)));

    setView(
      el("div", { class: "row between" }, el("h1", {}, t("history.title")), exportBtn),
      el("div", { class: "filters" }, period, type, direction, el("div", { style: "flex:1;min-width:200px" }, search)),
      summaryBox,
      el("div", { style: "margin-top:1rem" }, cardWithAction(t("history.by_type"), null, typeBox)),
      el("section", { class: "card", style: "margin-top:1rem" }, listBox, el("div", { style: "text-align:center;margin-top:0.75rem" }, more)));
    await load(true);
  };

  /**
   * Where the loan money goes: a saved account, or any bank / mobile money account.
   * value() returns the payout fields for the API, or throws a readable error.
   */
  function payoutPicker(options) {
    const NEW = "__new__";
    const accountLabel = (a) => `${a.provider_name} ${a.account_masked}${a.has_full_number ? "" : ` (${t("payout.needs_number")})`}`;
    const choice = el("select", { name: "payout_choice", "aria-label": t("payout.title") },
      options.accounts.map((a) => el("option", { value: a.id, selected: a.is_primary }, accountLabel(a))),
      el("option", { value: NEW, selected: !options.accounts.length }, t("payout.new_account")));
    const provider = el("select", { name: "payout_provider", "aria-label": t("common.provider") },
      ...["MOBILE_MONEY", "BANK"].map((kind) => el("optgroup", { label: t(KIND_LABEL[kind]) },
        (options.providers[kind] || []).map((p) => el("option", { value: p.key, dataset: { kind } }, p.display_name)))));
    const number = el("input", { name: "payout_account_number", inputmode: "numeric", autocomplete: "off" });
    const numberLabel = el("label", {});
    const fullNumber = el("input", { name: "payout_full_number", inputmode: "numeric", autocomplete: "off" });
    const newBox = el("div", { class: "form-grid" },
      el("div", { class: "field" }, el("label", {}, t("common.provider")), provider),
      el("div", { class: "field" }, numberLabel, number));
    const fullBox = el("div", { class: "field" }, el("label", {}, t("payout.full_number")), fullNumber);
    // the farmer reads back where the money will go before submitting; we only
    // echo back what they typed, since there is no account-name lookup to do
    const confirmLine = el("p", { class: "payout-confirm" });
    const providerName = () => (provider.selectedOptions[0] ? provider.selectedOptions[0].textContent : "");
    const syncConfirm = () => {
      const isNew = choice.value === NEW;
      const saved = options.accounts.find((a) => a.id === choice.value);
      if (isNew) {
        const typed = number.value.trim();
        confirmLine.textContent = typed
          ? t("payout.confirm_line", { number: maskAccount(typed), provider: providerName() })
          : t("payout.confirm_none");
      } else if (saved) {
        confirmLine.textContent = t("payout.confirm_saved", {
          provider: saved.provider_name, number: saved.account_masked,
        });
      } else {
        confirmLine.textContent = t("payout.confirm_none");
      }
    };
    const sync = () => {
      const isNew = choice.value === NEW;
      const saved = options.accounts.find((a) => a.id === choice.value);
      newBox.classList.toggle("hidden", !isNew);
      fullBox.classList.toggle("hidden", isNew || !saved || saved.has_full_number);
      const kind = provider.selectedOptions[0] ? provider.selectedOptions[0].dataset.kind : "MOBILE_MONEY";
      numberLabel.textContent = kind === "BANK" ? t("payout.number_bank") : t("payout.number_mobile");
      number.placeholder = kind === "BANK" ? "0150 1234 5678" : t("form.phone_placeholder");
    };
    choice.addEventListener("change", sync);
    provider.addEventListener("change", sync);
    const node = el("fieldset", { class: "payout-picker" },
      el("legend", {}, t("payout.title")),
      el("p", { class: "muted small" }, t("payout.hint")),
      el("div", { class: "field" }, choice), fullBox, newBox);
    sync();
    return {
      node,
      value() {
        if (choice.value === NEW) {
          if (!number.value.trim()) throw new Error(t("payout.choose_account"));
          return { payout_provider: provider.value, payout_account_number: number.value.trim() };
        }
        const out = { payout_account_id: choice.value };
        if (!fullBox.classList.contains("hidden")) {
          if (!fullNumber.value.trim()) throw new Error(t("payout.full_number_needed"));
          out.payout_account_number = fullNumber.value.trim();
        }
        return out;
      },
    };
  }

  /** Show only the middle of a number: 0754***456. Never echoes a full account. */
  function maskAccount(value) {
    const digits = String(value || "").replace(/\s+/g, "");
    if (digits.length < 4) return digits ? "****" : "";
    return `${digits.slice(0, 4)}***${digits.slice(-3)}`;
  }

  // ---------------------------------------------------------------- loans
  /**
   * The uses a farmer can borrow for, in the order they are offered.
   *
   * Mirrors ``LoanPurpose`` on the server. "Something else" is last on purpose:
   * it is the honest escape hatch, not an equal alternative, so it should not be
   * the first thing a farmer reaches for.
   */
  const LOAN_PURPOSES = ["FERTILIZER", "DRILLING", "EQUIPMENT", "MONEY", "OTHER"];

  /** The category as a word, for places that show an application at a glance. */
  function purposeLabel(code) {
    return code ? t(`loan.purpose_${String(code).toLowerCase()}`) : null;
  }

  /**
   * The reason the apply form is disabled, shown next to the form.
   *
   * The eligibility rules are real - a farmer cannot stack a second application
   * on one still under review, and high-risk farmers without cover are not lent
   * to - so the fields stay disabled. What was missing is the explanation: the
   * blockers were rendered in the card above the form, which read as an inert
   * grey button rather than a deliberate hold.
   */
  function blockedNotice(elig) {
    const blockers = elig.blockers || [];
    return el("div", { class: "alert", role: "status", style: "border-color:var(--harvest)" },
      el("strong", {}, t("loan.apply_blocked")),
      el("p", { class: "small", style: "margin:0.35rem 0 0" },
        elig.status === "NEEDS_INSURANCE" ? t("elig.needs_insurance") : t("loan.apply_blocked_hint")),
      blockers.length
        ? el("ul", { class: "small", style: "margin:0.35rem 0 0" },
            blockers.map((b) => el("li", {}, reasonText(b), " ", el("a", { href: `#${b.action}` }, "→"))))
        : null);
  }

  VIEWS.f_loans = async () => {
    const [loans, farms, elig, payoutOptions] = await Promise.all([
      api.get("/loans"), api.get("/farms"), api.get("/loans/eligibility"), api.get("/loans/payout-options"),
    ]);
    const payout = payoutPicker(payoutOptions);
    const offer = elig.offer;
    // filled in once the form exists; onPick only ever runs on a user click
    let purposeUI = null;
    const apply = form([
      { name: "requested_amount", label: t("loan.requested_amount"), type: "number", required: true, min: 1000,
        hint: offer ? `${t("loan.recommended_range")}: ${fmt.money(offer.min)} – ${fmt.money(offer.max)} · ${t("elig.rate")} ${fmt.number(offer.rate)}%` : null },
      { name: "term_months", label: t("loan.term"), type: "select", required: true, value: 12, options: [3, 6, 9, 12, 18, 24].map((m) => [m, monthsLabel(m)]) },
      { name: "farm_id", label: t("loan.select_farm"), type: "select", options: farms.filter((f) => f.is_active).map((f) => [f.id, f.name]) },
      // the uses a smallholder actually names, plus "something else" so nothing is
      // forced into a category that does not fit. Picking one is enough on its own;
      // the free text below is only for the detail an officer needs.
      { name: "purpose_code", label: t("loan.purpose_pick"), type: "choice", required: true, wide: true,
        choices: LOAN_PURPOSES.map((code) => [code, t(`loan.purpose_${code.toLowerCase()}`), t(`loan.purpose_${code.toLowerCase()}_hint`)]),
        onPick: (code) => {
          if (!purposeUI) return;
          // "Something else" is the one choice the farmer has to explain, so the
          // box says so rather than sitting there looking optional
          const other = code === "OTHER";
          purposeUI.detail.required = other;
          purposeUI.detail.placeholder = other ? t("loan.purpose_other_hint") : "";
          purposeUI.hint.textContent = other
            ? t("loan.purpose_other_hint")
            : t("loan.purpose_detail_hint");
        } },
      { name: "purpose", label: t("loan.purpose_detail"), type: "textarea", hint: t("loan.purpose_detail_hint") },
    ], t("loan.apply"), async (data) => {
      data.term_months = Number(data.term_months);
      // the category is the purpose; the detail is the farmer's own words
      if (data.purpose_code === "OTHER" && !data.purpose) delete data.purpose;
      Object.assign(data, payout.value());
      const res = await api.post("/loans/apply", data);
      toast(t("loan.applied_success", { reference: res.reference }));
      route();
    });
    purposeUI = (() => {
      const detail = apply.inputs.purpose;
      return { detail, hint: detail.closest(".field").querySelector(".hint") };
    })();
    apply.insertBefore(payout.node, apply.querySelector("button[type=submit]"));
    // A blocked form has to say why, on the form itself. Greying the fields out
    // with the reason left in a card further up the page is what made this look
    // like a broken button, so the blockers are repeated here with their links.
    if (elig.status !== "ELIGIBLE") {
      apply.querySelectorAll("input, select, textarea, button").forEach((n) => { n.disabled = true; });
      apply.insertBefore(blockedNotice(elig), apply.firstChild);
    }
    const prefill = JSON.parse(sessionStorage.getItem("agritrust.loanPrefill") || "null");
    if (prefill && elig.status === "ELIGIBLE") {
      sessionStorage.removeItem("agritrust.loanPrefill");
      apply.inputs.requested_amount.value = Math.min(prefill.amount, offer.max);
      const term = [...apply.inputs.term_months.options].find((o) => Number(o.value) >= prefill.term);
      if (term) apply.inputs.term_months.value = term.value;
    }
    setView(
      el("h1", {}, t("loan.title")),
      eligibilityCard(elig),
      el("div", { class: "stack", style: "margin-top:1rem" },
        card(t("loan.apply"), apply, el("p", { class: "muted small", style: "margin-top:0.75rem" }, t("risk.human_review"))),
        card(t("loan.applications"),
          table([t("common.reference"), [t("loan.purpose_column"), "num"], [t("common.amount"), "num"], [t("elig.rate"), "num"], t("risk.credit_risk"), t("common.status"), ""],
            loans.map((l) => [l.reference, purposeLabel(l.purpose_code) || num("—"), num(fmt.money(l.requested_amount)), num(`${fmt.number(l.interest_rate)}%`), riskPill(l.ai_risk_level), pill(l.status),
              el("button", { class: "btn small secondary", onclick: () => loanDialog(l.id) }, t("common.view"))]),
            t("loan.no_applications")))));
  };

  function eligibilityCard(elig) {
    const headline = {
      ELIGIBLE: elig.insured ? t("elig.eligible_insured") : t("elig.eligible"),
      NEEDS_INSURANCE: t("elig.needs_insurance"),
      NOT_YET: t("elig.not_yet"),
    }[elig.status];
    const tone = elig.status === "ELIGIBLE" ? "APPROVED" : elig.status === "NEEDS_INSURANCE" ? "UNDER_REVIEW" : "PENDING";
    const offerBox = (title, o, highlight) => el("div", { class: "card", style: highlight ? "border:2px solid var(--brand)" : "" },
      el("div", { class: "muted small" }, title),
      o ? [el("div", { class: "stat" }, el("span", { class: "label" }, t("elig.limit")), el("span", { class: "value" }, fmt.money(o.max))),
        el("div", { class: "small" }, `${t("elig.rate")}: `, el("strong", {}, `${fmt.number(o.rate)}%`))]
        : el("p", { class: "small" }, t("elig.needs_insurance")));

    const nodes = [el("div", { class: "card-head" }, el("h2", {}, t("elig.title")), pill(tone, headline))];
    if (elig.blockers.length) {
      nodes.push(el("ul", {}, elig.blockers.map((b) =>
        el("li", {}, reasonText(b), " ", el("a", { href: `#${b.action}` }, "→")))));
    }
    if (elig.uninsured_offer) {
      const cover = elig.with_insurance && elig.with_insurance.cover;
      nodes.push(el("div", { class: "grid grid-2" },
        offerBox(t("elig.without_insurance"), elig.status === "NEEDS_INSURANCE" && !elig.insured ? null : elig.uninsured_offer, !elig.insured && elig.status === "ELIGIBLE"),
        elig.insured
          ? offerBox(`${t("elig.with_insurance")} ✓`, elig.offer, true)
          : offerBox(t("elig.with_insurance"), elig.with_insurance, elig.status === "NEEDS_INSURANCE")));
      if (elig.insured) {
        nodes.push(el("p", { class: "small", style: "margin-top:0.75rem" }, el("strong", {}, `${t("elig.your_cover")}: `),
          elig.cover.map((c) => `${AT.lang === "sw" ? c.product_name_sw : c.product_name_en} (${c.policy_number}, ${fmt.money(c.sum_insured)})`).join(" · ")));
      } else if (cover) {
        nodes.push(el("div", { class: "row", style: "margin-top:0.75rem" },
          el("span", {}, t("elig.unlock", { farm: cover.farm_name, max: fmt.money(elig.with_insurance.max), rate: fmt.number(elig.with_insurance.rate) })),
          el("button", {
            class: "btn small", onclick: async (e) => {
              e.target.disabled = true;
              try {
                const policy = await api.post("/insurance/policies", { farm_id: cover.farm_id, product_code: cover.product_code });
                toast(t("insurance.requested", { number: policy.policy_number }), 6000);
                refreshUnread();
                route();
              } catch (err) { toast(err.message); e.target.disabled = false; }
            },
          }, t("elig.get_cover", { product: AT.lang === "sw" ? cover.product_name_sw : cover.product_name_en, premium: fmt.money(cover.premium) }))));
      }
    }
    if (elig.reasons && elig.reasons.length) {
      nodes.push(el("details", { style: "margin-top:0.75rem" }, el("summary", {}, t("elig.why")),
        el("ul", { class: "small" }, elig.reasons.map((r) => el("li", {}, reasonText(r))))));
    }
    return el("section", { class: "card" }, nodes);
  }

  async function loanDialog(id, officerActions) {
    const [detail, schedule] = await Promise.all([api.get(`/loans/${id}`), api.get(`/loans/${id}/schedule`)]);
    const body = el("div", { class: "stack" },
      el("div", { class: "row" }, pill(detail.status), riskPill(detail.ai_risk_level), el("span", { class: "muted small" }, detail.reference)),
      table([t("common.category"), ["", "num"]], [
        [t("loan.requested_amount"), num(fmt.money(detail.requested_amount))],
        [t("loan.recommended_range"), num(`${fmt.money(detail.recommended_min)} – ${fmt.money(detail.recommended_max)}`)],
        [t("loan.term"), num(detail.term_months)],
        [t("loan.interest_rate"), num(`${fmt.number(detail.interest_rate, 1)}%`)],
        detail.loan_principal ? [t("loan.principal"), num(fmt.money(detail.loan_principal))] : null,
        detail.monthly_payment ? [t("loan.monthly_payment"), num(fmt.money(detail.monthly_payment))] : null,
        detail.outstanding_balance !== null && detail.outstanding_balance !== undefined ? [t("loan.outstanding_balance"), num(fmt.money(detail.outstanding_balance))] : null,
        detail.due_date ? [t("loan.due_date"), num(fmt.date(detail.due_date))] : null,
      ].filter(Boolean)),
      el("p", {}, el("strong", {}, `${t("loan.purpose")} `),
        // the category first, because that is what groups applications; the
        // farmer's own words follow when they wrote more than the category
        purposeLabel(detail.purpose_code)
          ? [el("span", { class: "pill" }, purposeLabel(detail.purpose_code)),
             el("span", { class: "muted small" }, ` · ${detail.purpose}`)]
          : detail.purpose),
      detail.decision_reason ? el("p", {}, el("strong", {}, `${t("loan.decision_reason")}: `), detail.decision_reason) : null,
      detail.payout ? el("div", { class: "row" },
        el("strong", {}, `${t("payout.destination")}:`),
        el("span", {}, `${detail.payout.provider_name} ${detail.payout.account_masked}`),
        pill({ SENT: "PAID", FAILED: "DEFAULTED" }[detail.payout.status] || "PENDING", t(`payout.status_${detail.payout.status.toLowerCase()}`)),
        detail.payout.status !== "SENT" && !officerActions ? el("button", {
          class: "btn small secondary", type: "button", onclick: async () => {
            const picker = payoutPicker(await api.get("/loans/payout-options"));
            const f = form([], t("common.save"), async () => {
              await api.put(`/loans/${id}/payout`, picker.value());
              toast(t("common.save")); dialog.close(); route();
            });
            f.insertBefore(picker.node, f.querySelector("button[type=submit]"));
            openDialog(t("payout.change"), f);
          },
        }, t("payout.change")) : null) : null);

    const repayable = detail.outstanding_balance > 0 && ["APPROVED", "DISBURSED", "ACTIVE"].includes(detail.status);
    if (repayable && !officerActions) {
      body.append(el("button", {
        class: "btn", onclick: () => paymentDialog({ purpose: "LOAN_REPAYMENT", targetId: id, amount: Number(detail.monthly_payment) || null }),
      }, t("payment.pay_loan")));
    }
    if (repayable && officerActions) {
      // officers record cash received in person; farmers pay through a payment request
      body.append(el("h3", {}, t("loan.add_repayment")), form([
        { name: "amount", label: t("loan.payment_amount"), type: "number", required: true, min: 1, value: Number(detail.monthly_payment) || null },
      ], t("loan.add_repayment"), async (data) => {
        await api.post(`/loans/${id}/repayments`, data);
        toast(t("loan.repayment_saved"));
        dialog.close();
        route();
      }));
    }
    if (detail.repayments.length) {
      body.append(el("h3", {}, t("loan.repayments")),
        table([t("loan.payment_date"), [t("common.amount"), "num"], t("common.status")],
          detail.repayments.map((r) => [fmt.date(r.payment_date), num(fmt.money(r.amount)), r.is_on_time ? pill("PAID", t("loan.on_time")) : pill("DEFAULTED", t("loan.late"))])));
    }
    if (schedule.length) {
      body.append(el("details", {}, el("summary", {}, t("loan.repayments_title")),
        table(["#", t("loan.due_date"), [t("loan.monthly_payment"), "num"], [t("loan.outstanding_balance"), "num"]],
          schedule.map((s) => [s.installment, fmt.date(s.due_date), num(fmt.money(s.payment)), num(fmt.money(s.balance))]))));
    }
    if (officerActions) body.append(officerActions(detail));
    openDialog(t("loan.title"), body);
  }

  // ---------------------------------------------------------------- insurance
  const COVER_MONTHS = [3, 4, 5, 6, 9, 12];

  /** The policy statuses a farmer sees, named plainly and coloured as text only. */
  const POLICY_STATUS = {
    PENDING_REVIEW: ["insurance.status_pending_review", "warn"],
    ACTIVE: ["insurance.status_active", "good"],
    CLAIMED: ["insurance.status_triggered", "strong"],
    EXPIRED: ["insurance.status_expired", "muted"],
    CANCELLED: ["insurance.status_cancelled", "muted"],
  };
  function policyBadge(status) {
    const [key, tone] = POLICY_STATUS[status] || ["insurance.status_unknown", "muted"];
    return el("span", { class: `badge-text ${tone}` }, t(key));
  }

  /**
   * How far a policy's parametric trigger has moved, from the stored records.
   * Says "pending" rather than drawing a bar when the data cannot support a number.
   */
  function triggerMeter(policy) {
    const g = policy.trigger;
    if (!g) return null;
    if (!g.available) {
      const key = g.reason === "window_not_open" ? "insurance.window_not_open" : "insurance.weather_pending";
      return el("div", { class: "trigger-meter muted small" }, t(key));
    }
    const pct = Math.max(0, Math.min(100, Number(g.pct_of_threshold) || 0));
    const tone = g.triggered ? "met" : pct >= 75 ? "warn" : "";
    const isRatio = g.unit !== "mm";
    const label = isRatio
      ? t("insurance.trigger_progress_ratio", { observed: fmt.number(g.observed, 2), threshold: fmt.number(g.threshold, 2) })
      : t("insurance.trigger_progress", { observed: `${fmt.number(g.observed, 1)} mm`, threshold: `${fmt.number(g.threshold)} mm` });
    // only quote the window period when the number really was measured over it
    const basis = g.basis === "recent_peak"
      ? t("insurance.trigger_peak_period", { days: g.recent_days || 30 })
      : t("insurance.trigger_period", { start: fmt.date(g.window_start), end: fmt.date(g.window_end) });
    return el("div", { class: "trigger-meter" },
      el("div", { class: "row between" },
        el("span", { class: "small" }, label),
        g.triggered ? el("span", { class: "badge-text strong" }, t("insurance.trigger_reached")) : null),
      el("div", { class: `track ${tone}`, role: "img", "aria-label": label },
        el("div", { class: "fill", style: `width:${pct}%` })),
      el("div", { class: "muted small" }, basis));
  }

  /** One product card, with the recommendation label when the risk match is real. */
  function productCard(p, match) {
    const recommended = match && match.product_code === p.code;
    const label = recommended
      ? (match.crop_name
        ? t("insurance.recommended_for_crop", { crop: t(`crop.${String(match.crop_name).toLowerCase()}`) })
        : t("insurance.recommended_for_farm", { farm: match.farm_name || "" }))
      : null;
    return el("div", { class: "product-card" },
      el("div", { class: "row between" },
        el("strong", {}, AT.lang === "sw" ? p.name_sw : p.name_en),
        label ? el("span", { class: "badge-text strong" }, label) : null),
      el("p", { class: "muted small" }, AT.lang === "sw" ? p.description_sw : p.description_en),
      el("p", { class: "small muted" },
        `${t("insurance.premium")}: ${fmt.number(p.premium_rate_pct, 1)}% · ${t("insurance.sum_insured")}: ${fmt.money(p.sum_insured_per_acre)} / ${t("common.acres")}`),
      recommended && match.reasons && match.reasons.length
        ? el("p", { class: "small" },
          el("strong", {}, `${t("insurance.why_recommended")}: `),
          reasonText(match.reasons[0]))
        : null);
  }

  // ---------------------------------------------------------------- insurance photos
  /**
   * Upload, preview, replace or remove the farmer's photo of one farm.
   *
   * The picker is the farm the photo is for, because a farmer can have more than
   * one and the officer is asking about a specific field. The type and size are
   * checked here as well as on the server, purely so the farmer finds out before
   * waiting for an upload - the server decides for real.
   */
  function farmPhotoCard(farms, onChanged) {
    if (!farms.length) return null;
    let farmId = farms[0].id;
    const farmOf = () => farms.find((f) => f.id === farmId) || farms[0];

    const preview = el("div", { class: "photo-preview" });
    const status = el("div", { class: "small muted", style: "margin-top:0.5rem" });
    const fileInput = el("input", { type: "file", accept: "image/jpeg,image/png,image/webp", class: "hidden" });
    const chooseBtn = el("button", { class: "btn secondary", type: "button" }, t("farm_photo.choose"));
    const uploadBtn = el("button", { class: "btn", type: "button", disabled: true }, t("farm_photo.upload"));
    const removeBtn = el("button", { class: "btn secondary small", type: "button" }, t("farm_photo.remove"));

    let chosen = null;

    const paint = () => {
      const farm = farmOf();
      const url = farm.photo_url;
      removeBtn.classList.toggle("hidden", !url);
      chooseBtn.textContent = url ? t("farm_photo.change") : t("farm_photo.choose");
      if (!url && !chosen) {
        preview.replaceChildren(el("div", { class: "photo-empty" }, t("farm_photo.none")));
        status.textContent = "";
        return;
      }
      if (url) {
        // the server's own URL, straight from the farm row
        preview.replaceChildren(
          el("img", { src: url, alt: t("farm_photo.preview_alt", { farm: farm.name }), loading: "lazy" })
        );
      }
    };

    const setFile = (file) => {
      status.textContent = "";
      if (!file) { chosen = null; uploadBtn.disabled = true; paint(); return; }
      // tell the farmer now rather than after the upload fails
      const allowed = ["image/jpeg", "image/png", "image/webp"];
      if (!allowed.includes(file.type)) {
        chosen = null;
        uploadBtn.disabled = true;
        status.textContent = t("upload.photo_not_an_image");
        status.className = "small";
        status.style.color = "var(--critical)";
        return;
      }
      if (file.size > 5 * 1024 * 1024) {
        chosen = null;
        uploadBtn.disabled = true;
        status.textContent = t("upload.photo_too_large");
        status.className = "small";
        status.style.color = "var(--critical)";
        return;
      }
      chosen = file;
      uploadBtn.disabled = false;
      status.className = "small muted";
      status.style.color = "";
      // show what they picked, before it is saved
      const url = URL.createObjectURL(file);
      preview.replaceChildren(el("img", { src: url, alt: file.name }));
    };

    chooseBtn.addEventListener("click", () => fileInput.click());
    fileInput.addEventListener("change", () => setFile(fileInput.files?.[0]));

    uploadBtn.addEventListener("click", async () => {
      if (!chosen) return;
      uploadBtn.disabled = true;
      uploadBtn.textContent = t("farm_photo.uploading");
      try {
        const body = new FormData();
        body.append("photo", chosen, chosen.name);
        const updated = await api.upload(`/farms/${farmId}/photo`, body);
        const index = farms.findIndex((f) => f.id === updated.id);
        if (index >= 0) farms[index] = updated;
        chosen = null;
        fileInput.value = "";
        toast(t("farm_photo.uploaded"));
        paint();
        onChanged?.();
      } catch (err) {
        status.className = "small";
        status.style.color = "var(--critical)";
        status.textContent = err.message;
      } finally {
        uploadBtn.disabled = !chosen;
        uploadBtn.textContent = t("farm_photo.upload");
      }
    });

    removeBtn.addEventListener("click", async () => {
      removeBtn.disabled = true;
      try {
        const updated = await api.del(`/farms/${farmId}/photo`);
        const index = farms.findIndex((f) => f.id === updated.id);
        if (index >= 0) farms[index] = updated;
        toast(t("farm_photo.removed"));
        paint();
        onChanged?.();
      } catch (err) {
        toast(err.message);
      } finally {
        removeBtn.disabled = false;
      }
    });

    // a farmer can have several farms, and the demo data can hold several with the
    // same name, so the picker says which is which rather than listing "Second
    // plot" a dozen times. Acreage is the thing that matters here: cover is priced
    // per acre, so it is also the useful thing to confirm.
    const label = (f, i) => {
      const acres = `${fmt.number(f.area_acres, 2)} ${t("common.acres")}`;
      const place = f.district || f.village;
      const sameName = farms.filter((o) => o.name === f.name).length > 1;
      return sameName ? `${f.name} #${i + 1} · ${acres}` : `${f.name} · ${acres}${place ? ` · ${place}` : ""}`;
    };
    const picker = el("select", { "aria-label": t("insurance.select_farm") },
      farms.map((f, i) => el("option", { value: f.id }, label(f, i))));
    picker.addEventListener("change", () => {
      farmId = picker.value;
      chosen = null;
      fileInput.value = "";
      uploadBtn.disabled = true;
      paint();
    });

    paint();
    return card(t("farm_photo.title"),
      el("p", { class: "explain" }, t("farm_photo.hint")),
      el("div", { class: "row", style: "margin-bottom:0.6rem" },
        picker,
        el("span", { class: "muted small" }, t("farm_photo.types"))),
      preview,
      el("div", { class: "row", style: "margin-top:0.6rem" },
        chooseBtn, uploadBtn, removeBtn, fileInput),
      status);
  }

  VIEWS.f_insurance = async () => {
    const [products, policies, claims, farms, plan] = await Promise.all([
      api.get("/insurance/products"), api.get("/insurance/policies"), api.get("/insurance/claims"),
      api.get("/farms"), api.get("/plan/recommendations").catch(() => null),
    ]);
    const activeFarms = farms.filter((f) => f.is_active);
    const nameOf = (p) => (AT.lang === "sw" ? p.name_sw : p.name_en);
    // the same engine that powers the "AI recommendations" button decides the match
    const match = plan && plan.insurance && plan.insurance.action === "INSURE" ? plan.insurance : null;

    // ---- the farmer's own photo of the farm, which an officer reads before
    // approving cover. Kept here because insurance is the reason to take one.
    const photoCard = farmPhotoCard(activeFarms, () => route());

    // ---- the premium the farmer will actually pay, for their real acreage
    const quoteBox = el("div", { class: "premium-line" }, el("span", { class: "muted small" }, t("insurance.select_to_quote")));
    const lastQuote = { data: null };
    const updateQuote = async () => {
      const { product_code, farm_id, term_months } = buy.inputs;
      if (!product_code.value || !farm_id.value) {
        quoteBox.replaceChildren(el("span", { class: "muted small" }, t("insurance.select_to_quote")));
        lastQuote.data = null;
        return;
      }
      quoteBox.replaceChildren(el("span", { class: "muted small" }, t("insurance.calculating")));
      try {
        const q = await api.get(`/insurance/quote?farm_id=${encodeURIComponent(farm_id.value)}&product_code=${encodeURIComponent(product_code.value)}&term_months=${Number(term_months.value)}`);
        lastQuote.data = q;
        quoteBox.replaceChildren(
          el("strong", {}, t("insurance.your_premium", {
            premium: fmt.money(q.premium), acres: fmt.number(q.area_acres, 2), date: fmt.date(q.end_date),
          })),
          el("div", { class: "muted small" }, t("insurance.covered_amount", {
            sum: fmt.money(q.sum_insured), rate: fmt.number(q.premium_rate_pct, 1), per_acre: fmt.money(q.sum_insured_per_acre),
          })));
      } catch (err) {
        lastQuote.data = null;
        quoteBox.replaceChildren(el("span", { class: "muted small" }, err.message));
      }
    };

    const buy = form([
      { name: "product_code", label: t("insurance.product"), type: "select", required: true, options: products.map((p) => [p.code, nameOf(p)]) },
      { name: "farm_id", label: t("insurance.select_farm"), type: "select", required: true, options: activeFarms.map((f) => [f.id, f.name]) },
      { name: "term_months", label: t("insurance.coverage"), type: "select", required: true, value: 6, wide: true,
        options: COVER_MONTHS.map((m) => [m, monthsLabel(m, true)]), hint: t("insurance.coverage_hint") },
    ], t("insurance.apply"), async (data) => {
      // task 6: show the farmer what they are buying before anything is written
      const q = lastQuote.data;
      const product = products.find((p) => p.code === data.product_code);
      const farm = activeFarms.find((f) => f.id === data.farm_id);
      const summary = [
        [t("insurance.product"), nameOf(product)],
        [t("insurance.confirm_farm"), farm ? farm.name : "—"],
        [t("insurance.confirm_acres"), farm ? `${fmt.number(q ? q.area_acres : farm.area_acres, 2)} ${t("common.acres")}` : "—"],
        [t("insurance.confirm_premium"), fmt.money(q ? q.premium : 0)],
        [t("insurance.confirm_cover"), fmt.money(q ? q.sum_insured : 0)],
        [t("insurance.confirm_period"), fmt.date(q ? q.end_date : "")],
      ];
      const confirmed = await confirmInsurance(summary);
      if (!confirmed) return;
      const policy = await api.post("/insurance/policies", { ...data, term_months: Number(data.term_months) });
      // nothing is covered and nothing is charged until an officer approves
      toast(t("insurance.requested", { number: policy.policy_number }), 6000);
      refreshUnread();
      route();
    });

    const recommendTerm = async () => {
      const farmId = buy.inputs.farm_id.value;
      const select = buy.inputs.term_months;
      [...select.options].forEach((o) => { o.textContent = monthsLabel(Number(o.value), true); });
      if (!farmId) return;
      const crops = await api.get(`/farms/${farmId}/crops`).catch(() => []);
      const harvest = crops.filter((c) => c.is_current && c.expected_harvest_date).map((c) => new Date(c.expected_harvest_date)).sort((a, b) => b - a)[0];
      if (!harvest || harvest <= new Date()) return;
      const needed = Math.ceil((harvest - new Date()) / (30 * 864e5));
      const pick = COVER_MONTHS.find((m) => m >= needed) || COVER_MONTHS[COVER_MONTHS.length - 1];
      const option = [...select.options].find((o) => Number(o.value) === pick);
      option.textContent = `${monthsLabel(pick, true)} · ${t("insurance.recommended_harvest", { date: fmt.date(harvest) })}`;
      select.value = String(pick);
    };
    // the premium line must follow every field, not just the first two
    buy.inputs.product_code.addEventListener("change", updateQuote);
    buy.inputs.farm_id.addEventListener("change", () => { recommendTerm().then(updateQuote); });
    buy.inputs.term_months.addEventListener("change", updateQuote);

    setView(
      el("h1", {}, t("insurance.title")),
      el("div", { class: "grid grid-2" },
        card(t("insurance.apply"),
          // task 5: what this product actually is, before the form
          el("p", { class: "explain" }, t("insurance.explainer")),
          activeFarms.length ? [buy, quoteBox, el("p", { class: "muted small", style: "margin-top:0.6rem" }, t("insurance.simulation_banner"))]
            : el("p", { class: "muted" }, t("farm.no_farms"))),
        card(t("insurance.products"), products.map((p) => productCard(p, match)))),
      // the photo sits next to the apply form: it is the same decision, and the
      // officer reads it while deciding
      photoCard ? el("div", { style: "margin-top:1rem" }, photoCard) : null,
      el("div", { style: "margin-top:1rem" }, card(t("insurance.my_policies"),
        policies.length
          ? el("div", { class: "stack" }, policies.map((p) => el("div", { class: "policy-card" },
            el("div", { class: "row between" },
              el("strong", {}, p.policy_number),
              policyBadge(p.status)),
            el("p", { class: "small muted" },
              `${AT.lang === "sw" ? p.product_name_sw : p.product_name_en}${p.farm_name ? ` · ${p.farm_name}` : ""} · ${fmt.date(p.start_date)} – ${fmt.date(p.end_date)}`),
            el("p", { class: "small" },
              `${t("insurance.premium")}: ${fmt.money(p.premium_paid)} · ${t("insurance.sum_insured")}: ${fmt.money(p.sum_insured)}`),
            // a pending request is not cover yet, and the premium is not taken
            p.status === "PENDING_REVIEW"
              ? el("p", { class: "small muted" }, `⏳ ${t("insurance.pending_note")}`)
              : null,
            p.status === "ACTIVE" ? triggerMeter(p) : null)))
          : el("p", { class: "empty" }, t("insurance.no_policies")))),
      el("div", { style: "margin-top:1rem" }, card(t("insurance.claims"),
        claims.length
          ? table([t("common.reference"), [t("insurance.claim_amount"), "num"], t("insurance.claim_status"), t("form.note")],
            claims.map((c) => [c.claim_number, num(fmt.money(c.claim_amount)), pill(c.status), c.decision_note || "—"]))
          : el("p", { class: "empty" }, t("insurance.no_claims")))));
    updateQuote();
    recommendTerm().then(updateQuote);
  };

  /** A yes/no dialog over the insurance form. Resolves true only on Confirm. */
  function confirmInsurance(summary) {
    return new Promise((resolve) => {
      // the dialog can also be dismissed with Escape, the backdrop or the close
      // button, and every one of those must mean "no" - never a silent yes and
      // never a promise that never settles
      let answered = false;
      const finish = (answer) => {
        if (answered) return;
        answered = true;
        dialog.removeEventListener("close", onClosed);
        if (dialog.open) dialog.close();
        resolve(answer);
      };
      const onClosed = () => finish(false);
      dialog.addEventListener("close", onClosed, { once: true });
      const confirmButton = el("button", { class: "btn", type: "button", onclick: () => finish(true) }, t("insurance.confirm_button"));
      const cancelButton = el("button", { class: "btn secondary", type: "button", onclick: () => finish(false) }, t("insurance.cancel"));
      openDialog(t("insurance.confirm_title"), el("div", {},
        el("p", { class: "muted small" }, t("insurance.confirm_intro")),
        el("dl", { class: "confirm-list" }, summary.flatMap(([label, value]) => [
          el("dt", {}, label), el("dd", { class: "num" }, value),
        ])),
        el("p", { class: "muted small" }, t("insurance.simulation_banner")),
        el("div", { class: "row", style: "margin-top:0.9rem" }, confirmButton, cancelButton)));
      confirmButton.focus();
    });
  }

  // ---------------------------------------------------------------- sell
  VIEWS.f_sell = async () => {
    const farms = await api.get("/farms");
    const listingForm = form([
      { name: "crop_name", label: t("crop.name"), required: true },
      { name: "quantity_kg", label: `${t("buyer.quantity")} (kg)`, type: "number", min: 1, required: true },
      { name: "price_per_kg", label: `${t("buyer.price_per_kg")} (TZS)`, type: "number", min: 1, required: true },
      { name: "quality_grade", label: t("buyer.quality"), type: "select", options: [["", "-"], ["A", "A"], ["B", "B"], ["C", "C"]] },
      { name: "description", label: t("form.description"), type: "textarea" },
    ], t("buyer.post_listing"), async (d) => {
      try {
        await api.post("/market/listings", {
          crop_name: d.crop_name,
          quantity_kg: Number(d.quantity_kg),
          price_per_kg: Number(d.price_per_kg),
          quality_grade: d.quality_grade || null,
          description: d.description || null,
          farm_id: farms[0] ? farms[0].id : null,
        });
        toast(t("buyer.listing_posted"));
        route();
      } catch (err) { toast(err.message); }
    });
    const mine = await api.get("/market/listings");
    setView(
      el("h1", {}, t("buyer.sell_title")),
      el("p", { class: "muted" }, t("buyer.sell_intro")),
      el("div", { class: "grid grid-2" },
        card(t("buyer.post_listing"), listingForm),
        card(t("buyer.my_listings"),
          mine.length
            ? table([t("crop.name"), [t("buyer.quantity"), "num"], [t("buyer.price_per_kg"), "num"], t("common.status")],
              mine.map((l) => [l.crop_name, num(`${fmt.number(l.quantity_kg, 0)} kg`), num(fmt.money(l.price_per_kg)), pill(l.status === "AVAILABLE" ? "ACTIVE" : l.status === "SOLD" ? "PAID" : "PENDING", t(`buyer.listing_${l.status.toLowerCase()}`))]))
            : el("p", { class: "empty" }, t("buyer.no_listings")))));
  };

  // ---------------------------------------------------------------- market (shared)
  VIEWS.market = async () => {
    const prices = await api.get("/market/prices");
    const detailBox = el("div");
    const trendKey = { INCREASING: "market.trend_increasing", DECREASING: "market.trend_decreasing" };
    const showCrop = async (crop) => {
      detailBox.replaceChildren(loading());
      await guard(detailBox, async () => {
        const intel = await api.get(`/market/crops/${encodeURIComponent(crop)}`);
        if (!intel.available) { detailBox.replaceChildren(el("p", { class: "empty" }, t("common.no_data"))); return; }
        const chart = el("div");
        detailBox.replaceChildren(
          el("div", { class: "card-head" }, el("h2", {}, `${t(`crop.${crop.toLowerCase()}`)} · ${t("market.trend")}`)),
          el("div", { class: "grid grid-3" },
            stat(t("market.price"), `${fmt.money(intel.current_price)}/${intel.unit}`),
            stat(t("market.average_price"), fmt.money(intel.average_price)),
            stat(t("market.best_market"), intel.best_market ? intel.best_market.market : "—",
              intel.best_market ? fmt.money(intel.best_market.latest_price) : null)),
          el("div", { style: "margin-top:1rem" }, chart),
          el("p", { style: "margin-top:0.75rem" }, el("strong", {}, `${t("market.insight")}: `), intel.insight),
          table([t("market.market"), t("common.district"), [t("market.price"), "num"]],
            intel.comparison.map((c) => [c.market, c.district || "—", num(fmt.money(c.latest_price))])));
        Charts.line(chart, {
          points: intel.series.map((p) => ({ label: fmt.date(p.date), value: p.price })),
          name: `${t(`crop.${crop.toLowerCase()}`)} (TZS/${intel.unit})`,
          valueFormat: (v) => fmt.money(v),
          format: (v) => fmt.number(v),
          tableCaption: t("common.view_details"),
        });
      });
    };
    setView(
      el("h1", {}, t("market.title")),
      card(null, table([t("market.crop"), [t("market.price"), "num"], [t("market.average_price"), "num"], t("market.trend"), t("market.demand"), t("market.best_market"), ""],
        prices.map((p) => [t(`crop.${p.crop.toLowerCase()}`), num(fmt.money(p.current_price)), num(fmt.money(p.average_price)),
          `${t(trendKey[p.trend] || "market.trend_stable")} (${p.change_pct >= 0 ? "+" : ""}${fmt.pct(p.change_pct, 1)})`,
          t(`market.demand_${String(p.demand).toLowerCase()}`), p.best_market || "—",
          el("button", { class: "btn small secondary", onclick: () => showCrop(p.crop) }, t("common.view"))]))),
      el("section", { class: "card", style: "margin-top:1rem" }, detailBox));
    if (prices.length) showCrop(prices[0].crop);
  };

  // ---------------------------------------------------------------- seasonal intelligence
  // Reads GET /weather/season-advisory. Every section is rendered only when the
  // server had the data for it, so a card is never shown with a guess in it.
  const SEASON_RATING_KEY = {
    POOR: "season.suitability_poor", FAIR: "season.suitability_fair",
    GOOD: "season.suitability_good", EXCELLENT: "season.suitability_excellent",
  };
  const cropLabel = (name) => t(`crop.${String(name).toLowerCase()}`);

  /** Turn a machine reason code into a sentence, e.g. "rain_below_minimum:58". */
  function seasonReason(code, crop) {
    const [kind, value] = String(code).split(":");
    const c = cropLabel(crop);
    const map = {
      rain_below_minimum: ["season.reason_rain_below_minimum", { crop: c }],
      rain_below_ideal: ["season.reason_rain_below_ideal", { crop: c }],
      rain_near_ideal: ["season.reason_rain_near_ideal", { crop: c }],
      rain_above_ideal: ["season.reason_rain_above_ideal", { crop: c }],
      temp_above_tolerance: ["season.reason_temp_above_tolerance", { crop: c }],
      temp_above_ideal: ["season.reason_temp_above_ideal", { crop: c }],
      temp_below_ideal: ["season.reason_temp_below_ideal", { crop: c }],
      temp_in_range: ["season.reason_temp_in_range", { crop: c }],
    };
    if (kind === "projected_from_partial_season") return t("season.provisional");
    const entry = map[kind];
    return entry ? t(entry[0], entry[1]) : "";
  }

  function seasonCard(season) {
    // the card is titled with the season itself; the section heading above already
    // says "Season Advisor", so repeating it here would read as a stutter
    return card(t(`season.${season.key}`),
      el("div", { class: "row between" },
        el("span", { class: "muted small" }, t("season.day_of", { day: season.day_of_season, total: season.length_days })),
        el("span", { class: "muted small" }, `${fmt.number(season.pct_through, 0)}%`)),
      el("p", { class: "muted small", style: "margin-top:0.6rem" },
        t("season.ends", { date: fmt.date(season.end) }),
        season.days_remaining ? ` · ${t("season.remaining", { days: season.days_remaining })}` : ""),
      season.is_rainy_season ? null : el("p", { class: "small", style: "margin-top:0.4rem" }, t("season.dry_note")));
  }

  function suitabilityCard(fit) {
    if (!fit.available) {
      const key = fit.reason === "crop_not_in_suitability_table"
        ? "season.crop_not_in_table" : "season.no_weather";
      return card(t("season.suitability_title"),
        el("p", { class: "muted small" }, t(key, { crop: cropLabel(fit.crop || "") })));
    }
    const crop = fit.crop;
    const observed = fit.observed;
    const reasons = (fit.reason_codes || []).map((c) => seasonReason(c, crop)).filter(Boolean);
    return card(t("season.suitability_title"),
      el("div", { class: "row between" },
        el("strong", {}, t("season.suitability_for", { crop: cropLabel(crop) })),
        el("span", { class: `badge-text ${fit.rating === "POOR" || fit.rating === "FAIR" ? "strong" : "good"}` },
          t(SEASON_RATING_KEY[fit.rating] || "season.suitability_fair"))),
      el("p", { class: "small muted", style: "margin-top:0.5rem" },
        t("season.rain_so_far", {
          actual: fmt.number(observed.rain_mm, 1), recorded: observed.rain_days_recorded, expected: observed.rain_days_expected,
        })),
      el("p", { class: "small muted" }, t("season.rain_needed", {
        crop: cropLabel(crop), minimum: fmt.number(fit.required.minimum_rain_mm), ideal: fmt.number(fit.required.ideal_rain_mm),
      })),
      fit.provisional
        ? el("p", { class: "small" }, t("season.rain_pace", { projected: fmt.number(observed.projected_rain_mm, 1) }))
        : null,
      observed.mean_temp_c === null || observed.mean_temp_c === undefined ? null
        : el("p", { class: "small muted" }, t("season.temp", {
          temp: fmt.number(observed.mean_temp_c, 1),
          low: fmt.number(fit.required.ideal_temp_min_c), high: fmt.number(fit.required.ideal_temp_max_c),
        })),
      reasons.length ? el("ul", { class: "small", style: "margin:0.5rem 0 0; padding-left:1.1rem" },
        reasons.map((r) => el("li", {}, r))) : null);
  }

  /** Only rendered when the server found crops that genuinely rate higher. */
  function alternativesCard(alts) {
    if (!alts || !alts.length) return null;
    return card(t("season.alternatives_title"),
      el("ul", { style: "margin:0; padding-left:1.1rem" }, alts.map((a) => el("li", {},
        el("strong", {}, cropLabel(a.crop)),
        ` - ${t(SEASON_RATING_KEY[a.rating] || "season.suitability_fair")}`))),
      el("p", { class: "muted small", style: "margin-top:0.5rem" },
        alts.map((a) => t("season.rain_needed", {
          crop: cropLabel(a.crop), minimum: fmt.number(a.required.minimum_rain_mm), ideal: fmt.number(a.required.ideal_rain_mm),
        })).join(" ")));
  }

  function plantingWindowCard(win) {
    if (!win) return null;
    const reasons = [];
    const code = (c) => `season.window_${c}`;
    if (win.clean) reasons.push(t("season.window_clean"));
    else {
      if (win.problem_codes.includes("too_dry")) reasons.push(t(code("too_dry")));
      if (win.problem_codes.includes("too_wet")) reasons.push(t(code("too_wet")));
      if (win.problem_codes.includes("too_hot")) reasons.push(t(code("too_hot")));
      if (win.problem_codes.includes("too_windy")) reasons.push(t(code("too_windy")));
      if (!reasons.length) reasons.push(t("season.window_mixed", { horizon: 14 }));
    }
    return card(t("season.window_title"),
      el("p", { style: "margin:0 0 0.5rem" }, el("strong", {}, t("season.window_dates", {
        start: fmt.date(win.start), end: fmt.date(win.end), days: win.days,
      }))),
      el("p", { class: "small muted" }, `${fmt.number(win.rain_mm, 1)} mm · ${fmt.number(win.rain_days)} ${t("forecast.rain_days").toLowerCase()}`
        + (win.max_temperature_c ? ` · ${temp(win.max_temperature_c)}` : "")),
      el("ul", { class: "small", style: "margin:0.5rem 0 0; padding-left:1.1rem" },
        reasons.map((r) => el("li", {}, r))));
  }

  function historyCard(history) {
    if (!history) return null;  // the server omits it rather than inventing a percentage
    const pct = Math.abs(history.change_pct);
    const key = history.direction === "ABOVE" ? "season.history_above"
      : history.direction === "BELOW" ? "season.history_below" : "season.history_similar";
    return card(t("season.history_title"),
      el("p", { style: "margin:0 0 0.4rem" }, t(key, {
        pct: fmt.number(pct, 0), years: history.past_seasons, average: fmt.number(history.average_mm, 1),
      })),
      el("p", { class: "small muted" },
        t("season.history_range", { low: fmt.number(history.low_mm, 1), high: fmt.number(history.high_mm, 1) })));
  }

  /** Fetches the advisory and appends the season section under the existing cards. */
  async function appendSeasonSection() {
    let data;
    try {
      data = await api.get("/weather/season-advisory");
    } catch (err) {
      return null;  // the seasonal layer must never take the weather page down
    }
    const fit = data.current_crop_suitability || {};
    // crops that rate better, or an honest note that none do
    let cropAdvice = alternativesCard(data.alternative_crops);
    if (!cropAdvice && fit.available && ["POOR", "FAIR"].includes(fit.rating)) {
      cropAdvice = card(t("season.alternatives_title"),
        el("p", { class: "small muted" },
          data.season.key === "DRY"
            ? t("season.alternative_none_dry")
            : t("season.alternative_none", { crop: cropLabel(fit.crop || "") })));
    }

    const section = el("div", { style: "margin-top:1rem" },
      el("h2", { class: "section-title" }, t("season.title")),
      el("div", { class: "grid grid-2" },
        seasonCard(data.season),
        fit.available || fit.reason ? suitabilityCard(fit) : null,
        cropAdvice,
        plantingWindowCard(data.best_planting_window),
        historyCard(data.history_comparison)));
    view.append(section);
    return section;
  }

  // ---------------------------------------------------------------- weather
  VIEWS.f_weather = async () => {
    const w = await api.get("/weather?days=10");
    if (!w.available) { view.replaceChildren(el("h1", {}, t("weather.title")), card(null, el("p", { class: "empty" }, t("common.no_data")))); return; }
    setView(
      el("div", { class: "row between" }, el("h1", {}, `${t("weather.title")} · ${w.location}`)),
      el("div", { class: "grid grid-4" },
        stat(t("weather.temperature"),
          w.now ? temp(w.now.temperature, w.now.unit) : temp(w.current.temperature_max),
          [w.now ? t("weather.feels_like", { temp: temp(w.now.feels_like, w.now.unit) }) : null,
            t("weather.today_range", { max: temp(w.current.temperature_max), min: temp(w.current.temperature_min) })].filter(Boolean).join(" · ")),
        stat(t("weather.humidity"), `${fmt.number(w.current.humidity_pct)}%`),
        stat(t("weather.rain_last_30_label"), `${fmt.number(w.rainfall_30d, 1)} mm`, t("weather.crop_needs", { need: fmt.number(w.rainfall_requirement) })),
        stat(t("weather.risk_level"), riskPill(w.risk_level), w.crop ? t(`crop.${w.crop.toLowerCase()}`) : null)),
      el("div", { class: "grid grid-2", style: "margin-top:1rem" },
        card(t("weather.advisory"), el("ul", {}, w.advisories.map((a) => el("li", {}, t(`advisory.${a}`))))),
        card(t("weather.forecast"), table([t("common.date"), [t("weather.rainfall"), "num"], [t("weather.highest"), "num"], [t("weather.lowest"), "num"]],
          w.forecast.map((f) => [fmt.date(f.date), num(`${fmt.number(f.rainfall_mm, 1)} mm`), num(temp(f.temperature_max)), num(temp(f.temperature_min))])),
          el("button", { class: "btn small secondary", style: "margin-top:0.6rem", onclick: () => go("forecast") }, t("nav.forecast")))),
      null);
    // added below the existing cards, which stay exactly as they were
    await appendSeasonSection();
  };

  // ---------------------------------------------------------------- forecast
  // The forward-looking screen: what the weather is going to do, what the price is
  // going to do, and what the coming harvest is going to be worth.
  /** Why the season service could not say anything, in words that name the cause. */
  function seasonBlockReason(notes, location) {
    const map = {
      no_crop_on_file: t("fcast.suitability_none"),
      crop_not_in_suitability_table: t("fcast.suitability_unknown_crop", { crop: "" }),
      no_weather_data: t("fcast.suitability_no_weather", { location }),
      season_service_failed: t("fcast.seasonal_failed"),
    };
    return notes.map((n) => map[n] || null).filter(Boolean);
  }

  function seasonCard(season, location) {
    if (!season || !season.available) {
      return card(t("fcast.season_card"),
        el("p", { class: "muted small" }, t("fcast.seasonal_unavailable", {
          location, reason: t("fcast.seasonal_failed"),
        })));
    }
    const s = season.season;
    const fit = season.current_crop_suitability || {};
    const reasons = seasonBlockReason(season.notes || [], location);
    const fitBody = fit.available
      ? el("div", {},
        el("div", { class: "row between" },
          el("strong", {}, t("fcast.suitability")),
          el("span", { class: `badge-text ${["POOR", "FAIR"].includes(fit.rating) ? "strong" : "good"}` },
            t(SEASON_RATING_KEY[fit.rating] || "season.suitability_fair"))),
        el("p", { class: "small muted", style: "margin-top:0.4rem" },
          t("season.rain_so_far", {
            actual: fmt.number(fit.observed.rain_mm, 1),
            recorded: fit.observed.rain_days_recorded,
            expected: fit.observed.rain_days_expected,
          })),
        el("p", { class: "small muted" }, t("season.rain_needed", {
          crop: cropLabel(fit.crop), minimum: fmt.number(fit.required.minimum_rain_mm), ideal: fmt.number(fit.required.ideal_rain_mm),
        })),
        fit.provisional ? el("p", { class: "small" }, t("season.provisional")) : null)
      : el("p", { class: "small muted" }, reasons[0] || t("fcast.suitability_none"));
    const history = season.history_comparison
      ? el("p", { class: "small muted", style: "margin-top:0.5rem" },
        t(season.history_comparison.direction === "ABOVE" ? "season.history_above"
          : season.history_comparison.direction === "BELOW" ? "season.history_below" : "season.history_similar", {
          pct: fmt.number(Math.abs(season.history_comparison.change_pct), 0),
          years: season.history_comparison.past_seasons,
          average: fmt.number(season.history_comparison.average_mm, 1),
        }))
      : el("p", { class: "small muted", style: "margin-top:0.5rem" }, t("fcast.history_missing", { location }));
    return card(t("fcast.season_card"),
      el("div", { class: "row between" },
        el("strong", {}, t(`season.${s.key}`)),
        el("span", { class: "muted small" }, t("fcast.season_days", { day: s.day_of_season, total: s.length_days }))),
      el("p", { class: "muted small", style: "margin:0.25rem 0 0.75rem" },
        `${t("fcast.season_ends", { date: fmt.date(s.end) })} · ${t("fcast.season_remaining", { days: s.days_remaining })}`),
      fitBody,
      history,
      el("p", { class: "muted small", style: "margin-top:0.5rem" }, t("fcast.season_source")));
  }

  /** The ECMWF ensemble months, with a specific reason when there are none. */
  function rainfallOutlookCard(seasonal, location) {
    const reasonKey = seasonal && seasonal.reason === "needs_live_weather"
      ? "fcast.seasonal_need_live" : "fcast.seasonal_offline";
    if (!seasonal || !seasonal.available) {
      return card(t("forecast.seasonal_outlook"),
        el("p", { class: "muted small" }, t("fcast.seasonal_unavailable", {
          location, reason: t(seasonal && seasonal.reason ? reasonKey : "fcast.seasonal_offline"),
        })));
    }
    // scale every bar against the largest figure in the set, so the months are
    // actually comparable to each other; scaling each against its own normal makes
    // a uniformly wet run come out as three identical full bars
    const months = seasonal.months;
    const peak = Math.max(1, ...months.map((m) => Math.max(m.expected_mm || 0, m.normal_mm || 0)));
    return card(t("forecast.seasonal_outlook"),
      el("p", { class: "muted small", style: "margin:0 0 0.5rem" },
        t("fcast.seasonal_months", { count: months.length })),
      ...months.map((m) => {
        const share = ((m.expected_mm || 0) / peak) * 100;
        const normalShare = m.normal_mm ? ((m.normal_mm || 0) / peak) * 100 : null;
        return el("div", { class: "hbar-row" },
          el("span", {}, fmt.month(m.month)),
          el("div", { class: "hbar-track" },
            el("div", {
              class: "hbar-fill",
              style: `width:${share}%;background:var(${m.category === "DRIER" ? "--serious" : m.category === "WETTER" ? "--series-1" : "--good"})`,
            }),
            normalShare === null ? null
              : el("div", { class: "hbar-normal", style: `left:${normalShare}%` })),
          el("span", { class: "num", style: "text-align:right" }, `${fmt.number(m.expected_mm)} mm`));
      }),
      el("p", { class: "muted small", style: "margin-top:0.5rem" },
        t("fcast.normal_legend"),
        " ",
        months.map((m) => `${fmt.month(m.month)} ${fmt.number(m.normal_mm || 0)} mm`).join(" · ")));
  }

  /** Each good field-work day, with the numbers that earned it. */
  function fieldWorkCard(week, days) {
    const byDate = new Map(days.map((d) => [d.date, d]));
    const entries = (week.field_work_days || [])
      .map((date) => byDate.get(date))
      .filter(Boolean);
    if (!entries.length) {
      return card(t("forecast.field_work_days"), el("p", { class: "muted small" }, t("common.no_data")));
    }
    return card(t("forecast.field_work_days"),
      el("ul", {}, entries.map((d) => {
        const rain = fmt.number(d.rain_mm, 1);
        const high = d.temperature_max === null || d.temperature_max === undefined ? null : fmt.number(d.temperature_max, 1);
        const low = d.temperature_min === null || d.temperature_min === undefined ? null : fmt.number(d.temperature_min, 1);
        const wind = d.wind_max_kmh === null || d.wind_max_kmh === undefined ? null : fmt.number(d.wind_max_kmh, 1);
        // the clause only mentions a reading the source actually supplied
        const reason = wind !== null && low !== null
          ? t("fcast.field_work_reason", { rain, low, high, wind })
          : low !== null
            ? t("fcast.field_work_reason_no_wind", { rain, low, high })
            : high !== null
              ? t("fcast.field_work_reason_no_wind_no_low", { rain, high })
              : rain;
        return el("li", {}, el("strong", {}, fmt.date(d.date)), ` — ${reason}`);
      })),
      el("p", { class: "muted small", style: "margin-top:0.5rem" }, t("forecast.disclaimer")));
  }

  // ---------------------------------------------------------------- ussd (shared)
  VIEWS.ussd = async () => {
    const config = await fetch("/api/v1/config").then((r) => r.json());
    let sessionId = Math.random().toString(36).slice(2, 12);
    let journey = [];
    const screen = el("div", { class: "phone ussd-screen", "aria-live": "polite" });
    const input = el("input", { inputmode: "text", "aria-label": t("ussd.send") });
    const phoneInput = can.staff ? el("input", { type: "tel", value: "+255754000101", "aria-label": t("ussd.phone") }) : null;
    const submit = async (text) => {
      if (text !== null) journey.push(text);
      try {
        const res = await api.post("/ussd/simulate", {
          session_id: sessionId, text: journey.join("*"), phone_number: phoneInput ? phoneInput.value.trim() : undefined,
        });
        screen.textContent = `${res.continue_session ? "" : "[END] "}${res.response}`;
      } catch (err) { screen.textContent = err.message; }
      input.value = "";
      input.focus();
    };
    const restart = () => { sessionId = Math.random().toString(36).slice(2, 12); journey = []; submit(null); };
    const f = el("form", { class: "row", style: "margin-top:0.6rem" }, input, el("button", { class: "btn", type: "submit" }, t("ussd.send")));
    f.addEventListener("submit", (e) => { e.preventDefault(); if (input.value.trim()) submit(input.value.trim()); });
    const keypad = el("div", { class: "keypad" },
      ["1", "2", "3", "4", "5", "6", "7", "8", "9", "0"].map((k) => el("button", { type: "button", onclick: () => submit(k) }, k)),
      el("button", { type: "button", onclick: restart, style: "grid-column: span 2" }, `↺ ${config.ussd_code}`));
    setView(
      el("h1", {}, t("ussd.demo")),
      el("p", { class: "muted" }, `${t("ussd.code")}: ${config.ussd_code} · ${t("ussd.note")}`),
      el("div", { class: "grid grid-2" },
        el("div", {}, screen, f, keypad),
        card(t("ussd.title"),
          phoneInput ? el("div", { class: "field" }, el("label", {}, t("ussd.phone")), phoneInput) : el("p", {}, `${t("ussd.phone")}: ${user.phone}`),
          el("p", { class: "muted small" }, t("ussd.reply_hint")),
          el("button", { class: "btn secondary", onclick: restart }, `↺ ${config.ussd_code}`))));
    submit(null);
  };

  // ---------------------------------------------------------------- notifications (shared)
  VIEWS.notifications = async () => {
    const data = await api.get("/notifications");
    setView(
      el("h1", {}, t("notify.title")),
      data.items.length
        ? el("div", { class: "stack" }, data.items.map((n) =>
          el("section", { class: "card" },
            el("div", { class: "card-head" }, el("h3", {}, n.title),
              n.is_read ? el("span", { class: "muted small" }, fmt.datetime(n.created_at))
                : el("button", {
                  class: "btn small secondary", onclick: async () => {
                    await api.post(`/notifications/${n.id}/read`); refreshUnread(); route();
                  },
                }, t("notify.mark_read"))),
            el("p", {}, n.body))))
        : card(null, el("p", { class: "empty" }, t("notify.empty"))));
  };

  // ---------------------------------------------------------------- profile
  VIEWS.f_profile = async () => {
    const farmer = await api.get("/farmers/me");
    const f = form([
      { name: "full_name", label: t("auth.full_name"), required: true, value: farmer.user.full_name },
      { name: "village", label: t("common.village"), value: farmer.village },
      { name: "district", label: t("common.district"), value: farmer.district },
      { name: "region", label: t("common.region"), value: farmer.region },
      { name: "gender", label: t("farmer.gender"), type: "select", value: farmer.gender,
        options: [["female", t("farmer.female")], ["male", t("farmer.male")], ["other", t("farmer.other")]] },
      { name: "age_range", label: t("farmer.age_range"), type: "select", value: farmer.age_range,
        options: ["18-25", "26-35", "36-45", "46-60", "60+"].map((a) => [a, a]) },
      { name: "years_farming", label: t("farmer.years_farming"), type: "number", min: 0, step: 1, value: farmer.years_farming },
      { name: "cooperative_name", label: t("farmer.cooperative"), value: farmer.cooperative_name },
      { name: "national_id", label: t("farmer.national_id"), hint: `${t("farmer.national_id_note")} ${farmer.national_id_masked || ""}` },
    ], t("farmer.save_profile"), async (data) => {
      await api.patch("/farmers/me", data);
      toast(t("farmer.profile_updated"));
    });
    view.replaceChildren(el("h1", {}, t("farmer.profile")),
      el("div", { class: "grid grid-3" },
        stat(t("common.phone"), farmer.user.phone),
        stat(t("farmer.farm_size"), `${fmt.number(farmer.total_area_acres, 1)} ${t("common.acres")}`, `${farmer.farm_count} ${t("nav.farms").toLowerCase()}`),
        stat(t("farmer.preferred_language"), farmer.user.preferred_language === "sw" ? t("lang.swahili") : t("lang.english"))),
      el("div", { style: "margin-top:1rem" }, card(t("farmer.personal_info"), f)));
  };

  /** The account card the top-right profile chip opens.
   *
   * A farmer's own editor is registered as ``VIEWS.f_profile`` and wins for
   * farmers, because a prefixed view is preferred over a bare one. This is the
   * fallback for buyers and staff, who have no farmer profile to edit.
   */
  VIEWS.profile = async () => {
    setView(
      el("h1", {}, t("nav.profile")),
      el("div", { class: "grid grid-3" },
        stat(t("auth.full_name"), user.full_name),
        stat(t("common.phone"), user.phone),
        stat(t("dash.role"), t(roleLabel[user.role]))),
      el("div", { style: "margin-top:1rem" },
        card(t("farmer.preferred_language"),
          el("p", {}, user.preferred_language === "sw" ? t("lang.swahili") : t("lang.english")))),
    );
  };

  // ================================================================ staff views
  VIEWS.s_overview = async () => {
    const [stats, charts, highRisk] = await Promise.all([
      api.get("/admin/stats"), api.get("/admin/charts"), api.get("/admin/high-risk"),
    ]);
    const riskBox = el("div"), statusBox = el("div"), regionBox = el("div"), cropBox = el("div"), txBox = el("div");
    setView(
      el("h1", {}, t("admin.title")),
      el("div", { class: "grid grid-4" },
        stat(t("admin.total_farmers"), fmt.number(stats.total_farmers), `${fmt.number(stats.active_farms)} ${t("admin.active_farms").toLowerCase()}`),
        stat(t("admin.pending_reviews"), fmt.number(stats.pending_reviews), `${fmt.number(stats.loan_applications)} ${t("admin.loan_applications").toLowerCase()}`),
        stat(t("admin.total_financing"), fmt.compact(stats.total_financing), `TZS · ${fmt.number(stats.active_loans)} ${t("admin.active_loans").toLowerCase()}`),
        stat(t("admin.farmers_at_risk"), fmt.number(stats.farmers_at_risk), `${fmt.number(stats.insurance_policies)} ${t("admin.insurance_policies").toLowerCase()}`)),
      el("div", { class: "grid grid-2", style: "margin-top:1rem" },
        card(t("admin.risk_distribution"), riskBox),
        card(t("admin.loan_status"), statusBox)),
      el("div", { style: "margin-top:1rem" }, card(t("admin.monthly_transactions"), txBox)),
      el("div", { class: "grid grid-2", style: "margin-top:1rem" },
        card(t("admin.farmers_by_region"), regionBox),
        card(t("admin.crop_distribution"), cropBox)),
      el("div", { style: "margin-top:1rem" }, card(t("admin.high_risk_cases"),
        table([t("common.name"), t("common.district"), [t("risk.score"), "num"], t("risk.weather_risk"), t("risk.generated_at")],
          highRisk.map((r) => [r.farmer_name, r.district || "—", num(fmt.number(r.risk_score, 2)), riskPill(r.weather_risk), fmt.datetime(r.assessed_at)])))));

    // risk levels are status: status colours + the level named in text
    const tone = { LOW: "--good", MEDIUM: "--warning", HIGH: "--critical" };
    Charts.hbars(riskBox, ["LOW", "MEDIUM", "HIGH"].map((k) => ({ label: t(`risk.level_${k.toLowerCase()}`), value: charts.risk_distribution[k] || 0, color: tone[k] })));
    Charts.hbars(statusBox, Object.entries(charts.loan_status).map(([k, v]) => ({ label: t(`loan.${k.toLowerCase()}`), value: v })));
    Charts.hbars(regionBox, Object.entries(charts.farmers_by_region).sort((a, b) => b[1] - a[1]).map(([k, v]) => ({ label: k, value: v })));
    Charts.hbars(cropBox, Object.entries(charts.crop_distribution).sort((a, b) => b[1] - a[1]).map(([k, v]) => ({ label: t(`crop.${k.toLowerCase()}`), value: v })));
    if (charts.monthly_transactions.length) {
      Charts.columns(txBox, {
        categories: charts.monthly_transactions.map((m) => m.month),
        labels: charts.monthly_transactions.map((m) => fmt.month(m.month)),
        series: [
          { name: t("transaction.in"), color: "--series-1", values: charts.monthly_transactions.map((m) => m.in) },
          { name: t("transaction.out"), color: "--series-2", values: charts.monthly_transactions.map((m) => m.out) },
        ],
        tableCaption: t("common.view_details"),
      });
    } else {
      txBox.append(el("p", { class: "empty" }, t("common.no_data")));
    }
  };

  VIEWS.s_assessments = async () => {
    const statusSel = el("select", { "aria-label": t("common.filter") },
      el("option", { value: "PENDING_REVIEW" }, t("f5.status.pending_review")),
      el("option", { value: "" }, t("common.all")),
      el("option", { value: "CONFIRMED" }, t("f5.status.confirmed")),
      el("option", { value: "CHANGED" }, t("f5.status.changed")));
    const list = el("div");
    const load = async () => {
      const rows = await api.get(`/assessments${statusSel.value ? `?status=${statusSel.value}` : ""}`);
      list.replaceChildren(table(
        [t("common.name"), t("crop.name"), t("common.region"), [t("f5.title"), "num"], t("f5.recommendation"), t("common.status"), t("common.date"), ""],
        rows.map((a) => [a.farmer_name, t(`crop.${a.crop.toLowerCase()}`), a.region || "—",
          num(el("span", {}, `${Math.round(a.risk_score)} `, el("span", { class: `pill ${BAND_TONE[a.band] || ""}` }, t(`risk.level_${String(a.band).toLowerCase()}`)))),
          t(`f5.product.${a.product_type.toLowerCase()}`),
          el("span", { class: `pill ${REVIEW_TONE[a.review_status] || ""}` }, t(`f5.status.${a.review_status.toLowerCase()}`)),
          `${fmt.date(a.created_at)}${a.channel === "ussd" ? " · USSD" : ""}`,
          el("button", {
            class: "btn small", onclick: async () => {
              const full = await api.get(`/assessments/${a.id}`);
              openDialog(`${t("f5.title")} · ${full.farmer_name || ""}`, assessmentCard(full, { staff: true, onChange: () => guard(list, load) }));
            },
          }, t("admin.review"))]),
        t("admin.no_pending")));
    };
    statusSel.addEventListener("change", () => guard(list, load));
    view.replaceChildren(el("div", { class: "row between" }, el("h1", {}, t("f5.review_queue")), statusSel),
      el("p", { class: "muted" }, t("risk.human_review")), card(null, list));
    await load();
  };

  VIEWS.s_impact = async () => {
    const [imp, fair] = await Promise.all([api.get("/admin/impact"), api.get("/admin/fairness")]);
    const biasBox = el("section", { class: "card", style: "margin-top:1.5rem" }, el("h2", {}, t("bias.title")), loading());
    api.get("/admin/bias-test").then((b) => {
      const regionText = b.region.verdict === "REVIEW"
        ? t("bias.region_review", { gap: fmt.number(b.region.gap, 1), limit: b.region.threshold, high: b.region.highest.region })
        : t("bias.region_ok", { gap: fmt.number(b.region.gap, 1), limit: b.region.threshold });
      const driver = b.region.drivers[0];
      const females = b.rows.filter((r) => r.gender === "female");
      const chart = el("div");
      biasBox.replaceChildren(el("h2", {}, t("bias.title")),
        el("p", { class: "muted" }, t("bias.intro")),
        el("p", {}, pill(b.gender.verdict === "NO_GAP" ? "ACTIVE" : "DEFAULTED", b.gender.verdict === "NO_GAP" ? t("bias.gender_ok") : t("bias.gender_gap"))),
        el("p", {}, pill(b.region.verdict === "REVIEW" ? "CLAIMED" : "ACTIVE", regionText)),
        driver ? el("p", { class: "small" }, t("bias.driver", { factor: t(`f5.factor.${driver.factor}`), points: fmt.number(driver.points, 1) })) : null,
        chart,
        table([t("common.region"), [`${t("f5.title")} · ${t("farmer.female")}`, "num"], [`${t("f5.title")} · ${t("farmer.male")}`, "num"], [t("f5.rain_vs_normal"), "num"]],
          females.map((r) => {
            const male = b.rows.find((m) => m.region === r.region && m.gender === "male");
            return [r.region, num(fmt.number(r.risk_score, 1)), num(fmt.number(male ? male.risk_score : null, 1)),
              num(`${fmt.number(r.rain_30d_mm, 1)} / ${r.normal_30d_mm === null ? "—" : fmt.number(r.normal_30d_mm, 1)} mm`)];
          })));
      Charts.hbars(chart, females.map((r) => ({ label: r.region, value: r.risk_score })), { format: (v) => fmt.number(v, 1) });
    }).catch((err) => biasBox.replaceChildren(el("h2", {}, t("bias.title")), el("div", { class: "alert error" }, err.message)));
    const statusPill = (r) => r.status === "OK" ? pill("ACTIVE", t("fair.status_ok"))
      : r.status === "REVIEW" ? pill("CLAIMED", t("fair.status_review"))
        : el("span", { class: "pill" }, t("fair.status_insufficient"));
    const fairTable = (rows) => table(
      [t("fair.group"), [t("fair.farmers"), "num"], [t("fair.mean_risk"), "num"], [t("fair.approval_rate"), "num"], [t("fair.data_quality"), "num"], t("common.status")],
      rows.map((r) => [
        r.group === "unknown" ? t("common.not_available") : (t(`farmer.${r.group}`) !== `farmer.${r.group}` ? t(`farmer.${r.group}`) : r.group),
        num(r.farmers),
        num(r.mean_risk_score === null ? "—" : fmt.number(r.mean_risk_score, 2)),
        num(r.approval_rate === null ? "—" : `${fmt.pct(r.approval_rate)} (${r.decided_applications})`),
        num(fmt.pct(r.mean_data_quality)),
        el("div", {}, statusPill(r), r.flags.map((f) => el("div", { class: "small muted" }, t(`fair.flag_${f}`)))),
      ]));
    setView(
      el("h1", {}, t("impact.title")),
      el("div", { class: "grid grid-3" },
        stat(t("impact.farmers_profiled"), fmt.number(imp.farmers_profiled), t("impact.of_registered", { n: imp.farmers_registered })),
        stat(t("impact.finance_referrals"), fmt.number(imp.finance_referrals), t("impact.approved", { n: imp.finance_approved })),
        stat(t("impact.insured_acreage"), `${fmt.number(imp.insured_acreage, 1)} ${t("common.acres")}`, t("impact.policies", { n: imp.insurance_policies })),
        stat(t("impact.market_matches"), fmt.number(imp.market_matches), t("impact.kg_value", { kg: fmt.number(imp.market_match_kg), value: fmt.money(imp.market_match_value) })),
        stat(t("impact.decision_time"), imp.median_decision_hours === null ? "—" : t("impact.hours", { n: fmt.number(imp.median_decision_hours, 1) }),
          t("impact.baseline", { days: imp.manual_baseline_days })),
        stat(t("dq.title"), fmt.pct(imp.average_data_quality))),
      biasBox,
      el("h2", { style: "margin-top:1.5rem" }, t("fair.title")),
      el("p", { class: "muted" }, t("fair.intro")),
      el("p", { class: "small" }, t("fair.overall", {
        risk: fair.overall.mean_risk_score === null ? "—" : fmt.number(fair.overall.mean_risk_score, 2),
        rate: fair.overall.approval_rate === null ? "—" : fmt.pct(fair.overall.approval_rate),
      })),
      el("div", { class: "stack" },
        card(t("fair.by_region"), fairTable(fair.by_region)),
        card(t("fair.by_gender"), fairTable(fair.by_gender))));
  };

  VIEWS.s_queue = async () => {
    const statusSel = el("select", { "aria-label": t("common.filter") },
      el("option", { value: "" }, t("admin.pending_applications")),
      ["APPROVED", "REJECTED", "DISBURSED"].map((s) => el("option", { value: s }, t(`loan.${s.toLowerCase()}`))));
    const list = el("div");
    const load = async () => {
      list.replaceChildren(loading());
      const q = statusSel.value ? `?status=${statusSel.value}` : "";
      const rows = await api.get(`/loans/queue${q}`);
      list.replaceChildren(table(
        [t("common.reference"), t("common.name"), [t("loan.purpose_column"), "num"], [t("loan.requested_amount"), "num"], [t("loan.recommended_range"), "num"], t("risk.credit_risk"), t("common.status"), t("loan.applied_on"), ""],
        rows.map((l) => [l.reference, l.farmer_name, purposeLabel(l.purpose_code) || num("—"), num(fmt.money(l.requested_amount)),
          num(`${fmt.compact(l.recommended_min)} – ${fmt.compact(l.recommended_max)}`), riskPill(l.ai_risk_level), pill(l.status), fmt.date(l.applied_on),
          el("button", { class: "btn small", onclick: () => reviewLoan(l) }, t("admin.review"))]),
        t("admin.no_pending")));
    };
    statusSel.addEventListener("change", () => guard(list, load));
    view.replaceChildren(
      el("div", { class: "row between" }, el("h1", {}, t("loan.applications")), statusSel),
      card(null, list));
    await load();
  };

  async function reviewLoan(l) {
    const [risk, plan, elig] = await Promise.all([
      api.post("/ai/credit-risk", { farmer_id: l.farmer_id, persist: false }),
      api.get(`/plan/forecast?farmer_id=${encodeURIComponent(l.farmer_id)}`),
      api.get(`/loans/eligibility?farmer_id=${encodeURIComponent(l.farmer_id)}`),
    ]);
    loanDialog(l.id, (detail) => {
      const box = el("div", { class: "stack" });
      box.append(el("section", { class: "card" }, el("h3", {}, t("insurance.title")),
        elig.insured
          ? elig.cover.map((c) => el("p", {}, pill("ACTIVE", t("elig.officer_insured", { sum: fmt.money(c.sum_insured), date: fmt.date(c.end_date) })),
            " ", AT.lang === "sw" ? c.product_name_sw : c.product_name_en))
          : el("p", {}, pill("PENDING", t("elig.officer_uninsured")))));
      box.append(el("section", { class: "card" }, el("h3", {}, t("dq.title")), qualityBlock(risk.data_quality, risk.data_quality.needs_review)));
      box.append(el("section", { class: "card" }, el("h3", {}, t("plan.title")),
        table([t("common.category"), ["", "num"]], [
          [t("plan.lowest"), num(`${fmt.month(plan.lowest_month)} · ${fmt.money(plan.lowest_balance)}`)],
          [t("plan.financing_need"), num(plan.financing_need ? fmt.money(plan.financing_need) : "—")],
          [t("plan.closing"), num(`${fmt.money(plan.closing_range[0])} – ${fmt.money(plan.closing_range[1])}`)],
        ])));
      const riskCard = el("section", { class: "card" }, el("h3", {}, t("risk.title")));
      riskExplanation(riskCard, { ...risk, ...risk.explanation, components: risk.explanation.factor_weights.map((c) => ({ ...c, risk_level: c.direction })) });
      box.append(riskCard);
      const act = (path, done) => async (data) => {
        await api.post(`/loans/${detail.id}/${path}`, data instanceof Event ? {} : data);
        toast(done);
        dialog.close();
        route();
      };
      if (["PENDING", "UNDER_REVIEW"].includes(detail.status)) {
        const reason = { name: "reason", label: t("form.reason"), type: "textarea" };
        box.append(
          el("h3", {}, t("common.approve")),
          form([{ name: "approved_amount", label: t("loan.principal"), type: "number", min: 1, value: Number(detail.requested_amount) }, reason], t("common.approve"), act("approve", t("loan.approved"))),
          el("h3", {}, t("common.reject")),
          form([{ ...reason, required: true }], t("common.reject"), act("reject", t("loan.rejected"))),
          el("h3", {}, t("loan.request_info")),
          form([{ name: "reason", label: t("form.note"), type: "textarea", required: true }], t("loan.request_info"), act("request-info", t("loan.request_info_sent"))));
        if (detail.status === "PENDING") {
          box.prepend(el("button", { class: "btn secondary", onclick: act("review", t("loan.under_review")) }, t("loan.under_review")));
        }
      } else if (detail.status === "APPROVED") {
        box.append(el("button", {
          class: "btn", onclick: async (e) => {
            e.target.disabled = true;
            try {
              const res = await api.post(`/loans/${detail.id}/disburse`);
              const p = res.payout || {};
              toast(t("payout.sent_toast", { amount: fmt.number(res.loan_principal), provider: p.provider_name || "", account: p.account_masked || "" }), 6000);
              dialog.close(); route();
            } catch (err) { toast(err.message); e.target.disabled = false; }
          },
        }, detail.payout ? `${t("loan.disburse_now")} → ${detail.payout.provider_name} ${detail.payout.account_masked}` : t("loan.disburse_now")));
      }
      return box;
    });
  }

  VIEWS.s_farmers = async () => {
    const search = el("input", { type: "search", placeholder: t("form.search_placeholder"), "aria-label": t("common.search") });
    const list = el("div");
    const load = async () => {
      const data = await api.get(`/farmers?size=100${search.value ? `&q=${encodeURIComponent(search.value)}` : ""}`);
      list.replaceChildren(table(
        [t("common.name"), t("common.phone"), t("common.district"), t("common.region"), [t("farmer.farm_size"), "num"], t("farmer.cooperative"), ""],
        data.items.map((f) => [f.user.full_name, f.user.phone, f.district || "—", f.region || "—", num(`${fmt.number(f.total_area_acres, 1)}`), f.cooperative_name || "—",
          el("button", {
            class: "btn small secondary", onclick: async () => {
              const risk = await api.post("/ai/credit-risk", { farmer_id: f.id, persist: false });
              const box = el("div");
              riskExplanation(box, { ...risk, ...risk.explanation, components: risk.explanation.factor_weights.map((c) => ({ ...c, risk_level: c.direction })) });
              openDialog(`${t("risk.title")} · ${f.user.full_name}`, box);
            },
          }, t("nav.ai_risk"))])));
    };
    let timer;
    search.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(() => guard(list, load), 250); });
    view.replaceChildren(el("div", { class: "row between" }, el("h1", {}, t("nav.users")), el("div", { style: "min-width:260px" }, search)), card(null, list));
    await load();
  };

  VIEWS.s_claims = async () => {
    const [claims, events, policies, geo] = await Promise.all([
      api.get("/insurance/admin/claims"), api.get("/insurance/admin/events"),
      api.get("/insurance/admin/policies"), api.get("/insurance/admin/geographic-risk"),
    ]);
    // a request is a policy the officer has not ruled on yet
    const pending = policies.filter((p) => p.status === "PENDING_REVIEW");
    const settled = policies.filter((p) => p.status !== "PENDING_REVIEW");
    const scanBtn = el("button", {
      class: "btn", onclick: async (e) => {
        e.target.disabled = true;
        try {
          const res = await api.post("/insurance/admin/scan");
          toast(`${res.policies_scanned} · ${t("insurance.events")}: ${res.events_created} · ${t("insurance.claims")}: ${res.claims_created.length}`);
          route();
        } catch (err) { toast(err.message); }
        e.target.disabled = false;
      },
    }, t("insurance.triggers"));

    /** Approve or reject one request. A rejection must carry a reason, because
     *  the farmer is shown exactly what the officer typed. */
    const decidePolicy = (p, decision) => {
      const rejecting = decision === "REJECT";
      const f = form([
        { name: "note", label: t("insurance.decision_note"), type: "textarea",
          required: rejecting, hint: rejecting ? t("insurance.decision_note_hint") : null },
      ], rejecting ? t("insurance.reject_request") : t("insurance.approve_request"), async (data) => {
        try {
          await api.patch(`/insurance/admin/policies/${p.id}`, { decision, note: data.note || null });
          dialog.close();
          toast(rejecting ? t("insurance.rejected") : t("insurance.approved"));
          refreshUnread();
          route();
        } catch (err) { toast(err.message); }
      });
      openDialog(`${rejecting ? t("insurance.reject_request") : t("insurance.approve_request")} · ${p.policy_number}`, f);
    };

    const policyName = (p) => (AT.lang === "sw" ? p.product_name_sw : p.product_name_en);

    /** Settle a claim - unrelated to a cover request, but the same screen. */
    const decide = (c) => {
      const f = form([
        { name: "status", label: t("insurance.claim_status"), type: "select", required: true, value: "APPROVED",
          options: ["UNDER_REVIEW", "APPROVED", "REJECTED", "PAID"].map((s) => [s, t(`insurance.${s.toLowerCase()}`)]) },
        { name: "decision_note", label: t("form.note"), type: "textarea" },
      ], t("common.save"), async (data) => {
        await api.patch(`/insurance/admin/claims/${c.id}`, data);
        dialog.close(); toast(t("common.save")); route();
      });
      openDialog(`${c.claim_number} · ${fmt.money(c.claim_amount)}`, f);
    };

    const requestRow = (p) => [
      p.policy_number,
      p.farmer_name || "—",
      p.farm_name || "—",
      policyName(p),
      num(fmt.money(p.sum_insured)),
      num(fmt.money(p.premium_paid)),
      `${fmt.date(p.start_date)} – ${fmt.date(p.end_date)}`,
      pill("PENDING_REVIEW"),
      el("div", { class: "row" },
        el("button", { class: "btn small", onclick: () => decidePolicy(p, "APPROVE") }, t("insurance.approve_request")),
        el("button", { class: "btn small secondary", onclick: () => decidePolicy(p, "REJECT") }, t("insurance.reject_request"))),
    ];

    setView(
      el("div", { class: "row between" }, el("h1", {}, t("insurance.title")), scanBtn),
      // requests first: they are the officer's queue, and they go stale
      cardWithAction(t("insurance.pending_requests"),
        el("span", { class: "pill warning" }, fmt.number(pending.length)),
        pending.length
          ? table([t("common.reference"), t("insurance.requested_by"), t("nav.farms"),
              t("insurance.product"), [t("insurance.sum_insured"), "num"], [t("insurance.premium"), "num"],
              t("insurance.coverage"), t("common.status"), ""], pending.map(requestRow))
          : el("p", { class: "empty" }, t("insurance.no_pending_requests"))),
      card(t("insurance.claims"), table([t("common.reference"), [t("insurance.claim_amount"), "num"], t("insurance.claim_status"), t("form.note"), ""],
        claims.map((c) => [c.claim_number, num(fmt.money(c.claim_amount)), pill(c.status), c.decision_note || "—",
          ["PAID", "REJECTED"].includes(c.status) ? "" : el("button", { class: "btn small", onclick: () => decide(c) }, t("admin.review"))]))),
      el("div", { class: "grid grid-2", style: "margin-top:1rem" },
        card(t("insurance.events"), table([t("insurance.event"), t("common.district"), [t("insurance.observed"), "num"], [t("insurance.threshold"), "num"]],
          events.map((e) => [e.event_type, e.location || "—", num(fmt.number(e.observed_value, 1)), num(fmt.number(e.threshold_value, 1))]))),
        card(t("insurance.geographic_risk"), table([t("common.district"), [t("insurance.insured_farms"), "num"], [t("insurance.events"), "num"], t("common.status")],
          geo.map((g) => [g.district, num(fmt.number(g.policies, 1)), num(g.events), riskPill(g.risk)])))),
      el("div", { style: "margin-top:1rem" }, card(t("insurance.insured_farms"),
        table([t("insurance.policy_number"), t("insurance.product"), [t("insurance.sum_insured"), "num"], t("insurance.coverage"), t("common.status")],
          settled.map((p) => [p.policy_number, policyName(p), num(fmt.money(p.sum_insured)),
            `${fmt.date(p.start_date)} – ${fmt.date(p.end_date)}`, pill(p.status)])))));
  };

  VIEWS.s_audit = async () => {
    const [logs, sms] = await Promise.all([api.get("/admin/audit?limit=100"), api.get("/admin/sms?limit=50")]);
    setView(
      el("h1", {}, t("audit.title")),
      card(null, table([t("audit.time"), t("audit.action"), t("audit.actor"), t("audit.entity"), "IP"],
        logs.map((l) => [fmt.datetime(l.time), l.action, l.actor_role || "—", l.entity_type ? `${l.entity_type} ${String(l.entity_id || "").slice(0, 8)}` : "—", l.ip_address || "—"]),
        t("audit.empty"))),
      el("div", { style: "margin-top:1rem" }, card(t("sms.title"),
        el("p", { class: "muted small" }, t("sms.demo_note")),
        table([t("audit.time"), t("common.phone"), t("common.category"), t("sms.body"), t("common.status")],
          sms.map((s) => [fmt.datetime(s.time), s.phone, s.category, s.body,
            pill(String(s.status || "").toUpperCase(), t(`sms.status_${s.status}`))])))));
  };

  // ================================================================ buyer dashboard
  /** Which heading a payment method belongs under, in the buyer's language.
   *
   * The registry tags every provider with a kind and a channel; grouping on
   * those means a new rail appears in the right section without a code change,
   * and "M-Pesa" can never be filed under banks.
   */
  function methodGroupKey(p) {
    if (p.channel === "BANK_TRANSFER") return "buyer.method_group_bank";
    if (p.channel === "LIPA_NAMBA" && p.kind === "GATEWAY") return "buyer.method_group_lipa";
    if (p.channel === "CHECKOUT") return "buyer.method_group_checkout";
    return "buyer.method_group_mobile";
  }
  const METHOD_GROUP_ORDER = [
    "buyer.method_group_mobile",
    "buyer.method_group_lipa",
    "buyer.method_group_bank",
    "buyer.method_group_checkout",
  ];
  const METHOD_GROUP_NOTE = {
    "buyer.method_group_mobile": "buyer.method_mobile_note",
    "buyer.method_group_lipa": "buyer.method_lipa_note",
    "buyer.method_group_bank": "buyer.method_bank_note",
    "buyer.method_group_checkout": "buyer.method_checkout_note",
  };

  /** The payment rails, grouped, each with the number to dial or the account
   *  to pay - so the buyer is told what to do before the order is placed. */
  function paymentChooser(providers) {
    const box = el("div", { class: "pay-methods" });
    const note = el("p", { class: "small muted", id: "pay-method-note" });

    const paint = () => {
      const chosen = box.querySelector('input[name="provider"]:checked');
      if (!chosen) { note.textContent = ""; return; }
      const p = providers.find((x) => x.key === chosen.value);
      if (!p) { note.textContent = ""; return; }
      const bits = [];
      // banks carry the account number in the same field a wallet uses for its
      // Lipa number, so the label has to follow the channel - the pay page
      // does the same
      if (p.lipa_number) {
        bits.push(`${p.channel === "BANK_TRANSFER" ? t("buyer.account_number_is") : t("buyer.lipa_number_is")}: ${p.lipa_number}`);
      }
      if (p.ussd_code) bits.push(`${t("buyer.dial_this")}: ${p.ussd_code}`);
      const groupKey = methodGroupKey(p);
      const noteKey = METHOD_GROUP_NOTE[groupKey];
      note.textContent = [noteKey ? t(noteKey) : "", bits.join(" · ")].filter(Boolean).join(" ");
    };

    const groups = {};
    providers.forEach((p) => { (groups[methodGroupKey(p)] = groups[methodGroupKey(p)] || []).push(p); });

    for (const key of METHOD_GROUP_ORDER) {
      const rows = groups[key];
      if (!rows || !rows.length) continue;
      box.appendChild(el("div", { class: "pay-group" },
        el("h4", {}, t(key)),
        el("div", { class: "pay-group-options" }, rows.map((p, i) =>
          el("label", { class: "pay-option" },
            el("input", {
              type: "radio", name: "provider", value: p.key,
              checked: i === 0 && Object.keys(groups).indexOf(key) === 0,
            }),
            el("span", { class: "pay-option-name" }, p.display_name),
            el("span", { class: "pay-option-kind" }, t(methodGroupKey(p))))))));
    }
    box.addEventListener("change", paint);
    paint();
    return { node: box, note, paint };
  }

  /** The payment statement: every purchase, how it was paid, and what is still
   *  outstanding. Reads /buyer/transactions, which joins the order to the
   *  payment raised for it, so nothing here is counted twice.
   */
  async function transactionsCard() {
    const box = el("div");
    const filters = el("div", { class: "row" });
    // every rail the platform can pay through, not only the ones already used
    let allProviders = [];
    try { allProviders = await api.get("/finance/providers"); } catch (_) { allProviders = []; }

    /** One row per rail: what to dial or the account to pay, and whether this
     *  buyer has used it. Shown for every provider, so the full menu of ways to
     *  pay is visible in one place. */
    const allMethodsPanel = (used) => {
      if (!allProviders.length) return null;
      const byKey = Object.fromEntries(used.map((m) => [m.provider, m]));
      const groups = {};
      allProviders.forEach((p) => {
        const key = methodGroupKey(p);
        (groups[key] = groups[key] || []).push(p);
      });
      return el("div", { style: "margin-top:1rem" },
        el("h3", {}, t("buyer.all_methods_title")),
        el("p", { class: "small muted" }, t("buyer.all_methods_intro")),
        METHOD_GROUP_ORDER.filter((key) => groups[key]).map((key) =>
          el("div", { class: "pay-group", style: "margin-top:0.6rem" },
            el("h4", {}, t(key)),
            table([t("buyer.payment_method"), t("buyer.method_details"), [t("common.actions"), "num"], [t("buyer.total"), "num"]],
              groups[key].map((p) => {
                const mine = byKey[p.key];
                const bits = [];
                if (p.lipa_number) {
                  bits.push(`${p.channel === "BANK_TRANSFER" ? t("buyer.account_number_is") : t("buyer.lipa_number_is")}: ${p.lipa_number}`);
                }
                if (p.ussd_code) bits.push(`${t("buyer.dial_this")}: ${p.ussd_code}`);
                if (p.channel === "CHECKOUT") bits.push(t("buyer.method_checkout_note"));
                return [
                  el("div", {},
                    el("div", {}, p.display_name),
                    mine
                      ? el("div", { class: "muted small" }, t("buyer.method_used"))
                      : el("div", { class: "muted small" }, t("buyer.method_not_used"))),
                  el("span", { class: "small muted" }, bits.join(" · ") || "—"),
                  num(mine ? fmt.number(mine.count) : "—"),
                  num(mine ? fmt.money(mine.total) : "—"),
                ];
              })))));
    };

    const load = async (query = "") => {
      box.replaceChildren(el("p", { class: "muted" }, t("common.loading")));
      let data;
      try {
        data = await api.get(`/buyer/transactions${query}`);
      } catch (err) {
        box.replaceChildren(el("div", { class: "alert error" }, err.message));
        return;
      }
      const rows = data.transactions || [];
      const sum = data.summary || {};

      const statusFilter = el("select", { "aria-label": t("buyer.filter_status") },
        el("option", { value: "" }, t("buyer.all_statuses")),
        ["PENDING", "PAID", "EXPIRED", "FAILED"].map((s) =>
          el("option", { value: s }, t(`payment.status_${s.toLowerCase()}`))));
      const methodFilter = el("select", { "aria-label": t("buyer.filter_method") },
        el("option", { value: "" }, t("buyer.all_methods")),
        (data.methods || []).map((m) =>
          el("option", { value: m.provider }, m.provider_name || m.provider)));
      statusFilter.onchange = methodFilter.onchange = () => {
        const parts = [];
        if (statusFilter.value) parts.push(`status=${statusFilter.value}`);
        if (methodFilter.value) parts.push(`method=${methodFilter.value}`);
        load(parts.length ? `?${parts.join("&")}` : "");
      };
      filters.replaceChildren(statusFilter, methodFilter);

      box.replaceChildren(
        el("div", { class: "grid grid-4", style: "margin-bottom:1rem" },
          stat(t("buyer.transactions"), fmt.number(sum.count)),
          stat(t("buyer.total_paid"), fmt.money(sum.total_paid), `${fmt.number(sum.paid_count)} ${t("buyer.paid").toLowerCase()}`),
          stat(t("buyer.total_pending"), fmt.money(sum.total_pending), `${fmt.number(sum.pending_count)} ${t("buyer.awaiting_payment").toLowerCase()}`)),
        rows.length
          ? table(
            [t("buyer.transaction_date"), t("common.reference"), t("buyer.transaction_crop"),
              [t("buyer.total"), "num"], t("buyer.payment_method"), t("common.status"), ""],
            rows.map((r) => [
              fmt.date(r.paid_at || r.created_at),
              r.reference,
              r.crop_name || "—",
              num(fmt.money(r.amount)),
              el("div", {},
                el("div", {}, r.provider_name || r.provider || "—"),
                el("div", { class: "muted small" },
                  t(methodGroupKey({ kind: r.method_kind, channel: r.channel })))),
              r.status === "PAID" ? pill("ACTIVE", t("payment.status_paid"))
                : r.status === "PENDING" ? pill("PENDING", t("payment.status_pending"))
                  : pill("REJECTED", t(`payment.status_${String(r.status).toLowerCase()}`, r.status)),
              r.status === "PENDING" && r.pay_url
                ? el("a", { class: "btn small", href: r.pay_url }, t("buyer.pay_now"))
                : el("span", { class: "muted small" }, r.paid_at ? fmt.date(r.paid_at) : ""),
            ]))
          : el("p", { class: "empty" }, t("buyer.no_transactions")),
        (data.methods || []).length
          ? el("div", { style: "margin-top:1rem" },
              el("h3", {}, t("buyer.methods_used")),
              table([t("buyer.payment_method"), [t("common.actions"), "num"], [t("buyer.total"), "num"]],
                data.methods.map((m) => [
                  el("div", {},
                    el("div", {}, m.provider_name || m.provider),
                    el("div", { class: "muted small" },
                      t(methodGroupKey({ kind: m.method_kind, channel: m.channel })))),
                  num(fmt.number(m.count)),
                  num(fmt.money(m.total)),
                ])))
          : null,
        allMethodsPanel(data.methods || []),
      );
    };

    await load();
    return cardWithAction(t("buyer.transactions"),
      el("button", { class: "btn small secondary", onclick: () => load() }, t("ai.proactive_refresh")),
      el("p", { class: "muted small" }, t("buyer.transactions_intro")),
      filters, box);
  }

  VIEWS.b_buyer = async () => {
    const data = await api.get("/buyer/dashboard");
    const s = data.stats;

    const listingsBox = el("div");
    const renderListings = (rows) => {
      listingsBox.replaceChildren(
        rows.length
          ? table(
            [t("crop.name"), [t("buyer.quantity"), "num"], [t("buyer.price_per_kg"), "num"], [t("buyer.total"), "num"], t("buyer.quality"), t("common.region"), t("buyer.seller"), ""],
            rows.map((l) => [
              l.crop_name,
              num(`${fmt.number(l.quantity_kg, 0)} kg`),
              num(fmt.money(l.price_per_kg)),
              num(fmt.money(l.total_value)),
              l.quality_grade ? pill("ACTIVE", l.quality_grade) : el("span", { class: "muted" }, "-"),
              l.location || "-",
              l.farmer_name || "-",
              el("button", { class: "btn small", onclick: () => buyDialog(l) }, t("buyer.buy"))]),
          )
          : el("p", { class: "empty" }, t("buyer.no_listings")));
    };
    renderListings(data.listings);

    const ordersBox = el("div");
    const renderOrders = (rows) => {
      ordersBox.replaceChildren(
        rows.length
          ? table(
            [t("common.reference"), t("crop.name"), [t("buyer.total"), "num"], t("common.status"), ""],
            rows.map((o) => [
              o.reference,
              o.crop_name,
              num(fmt.money(o.total_amount)),
              o.status === "PAID" ? pill("ACTIVE", t("buyer.paid"))
                : o.status === "PENDING_PAYMENT" ? pill("PENDING", t("buyer.awaiting_payment"))
                  : pill("REJECTED", t("buyer.cancelled")),
              o.status === "PENDING_PAYMENT" && o.pay_url
                ? el("a", { class: "btn small", href: o.pay_url }, t("buyer.pay_now"))
                : el("span", { class: "muted small" }, o.paid_at ? fmt.date(o.paid_at) : ""),
            ]))
          : el("p", { class: "empty" }, t("buyer.no_orders")));
    };
    renderOrders(data.orders);

    setView(
      el("h1", {}, t("buyer.dashboard_title")),
      el("p", { class: "muted" }, t("buyer.intro")),
      el("div", { class: "grid grid-4" },
        stat(t("buyer.available_listings"), fmt.number(s.listings_available)),
        stat(t("buyer.my_orders"), fmt.number(s.orders_total), t("buyer.pending_count", { n: s.orders_pending })),
        stat(t("buyer.total_spent"), fmt.money(s.total_spent))),
      el("div", { class: "grid grid-2", style: "margin-top:1rem" },
        cardWithAction(t("buyer.crop_listings"),
          el("button", { class: "btn small secondary", onclick: () => route() }, t("ai.proactive_refresh")),
          listingsBox),
        card(t("buyer.order_history"), ordersBox),
        // the payment statement sits under the orders: the orders say what was
        // bought, this says how each one was paid for
        el("div", { style: "margin-top:1rem" }, await transactionsCard())));

    async function buyDialog(listing) {
      // every connected method: mobile money, Lipa Namba, banks and gateways,
      // straight from the live registry
      let providers = [];
      try { providers = await api.get("/finance/providers"); } catch (err) { providers = []; }
      if (!providers.length) providers = [{ key: "mpesa", display_name: "M-Pesa", kind: "MOBILE_MONEY", channel: "LIPA_NAMBA" }];
      const chooser = paymentChooser(providers);
      const quantity = { name: "quantity_kg", label: `${t("buyer.quantity")} (kg, max ${fmt.number(listing.quantity_kg, 0)})`, type: "number", min: 1, max: listing.quantity_kg, value: listing.quantity_kg, required: true };
      const contactLine = listing.farmer_phone
        ? el("p", { class: "small" }, `${t("buyer.seller")}: ${listing.farmer_name || "-"} · ${fmt.money(listing.price_per_kg)}/kg`,
          " ", el("a", { href: `tel:${listing.farmer_phone}` }, listing.farmer_phone))
        : el("p", { class: "small" }, `${t("buyer.seller")}: ${listing.farmer_name || "-"} · ${fmt.money(listing.price_per_kg)}/kg`);
      openDialog(`${t("buyer.buy")} · ${listing.crop_name}`, el("div", {},
        contactLine,
        el("p", { class: "small" }, `${t("buyer.total")}: ~TZS ${fmt.number(listing.quantity_kg * listing.price_per_kg, 0)}`),
        form([quantity], t("buyer.place_order"), async (d, node) => {
          const picked = node.querySelector('input[name="provider"]:checked');
          try {
            const order = await api.post("/buyer/orders", {
              listing_id: listing.id,
              quantity_kg: Number(d.quantity_kg),
              provider: picked ? picked.value : providers[0].key,
            });
            dialog.close();
            toast(t("buyer.order_created"));
            // the payment page opens straight away, with instructions for the
            // chosen method (Lipa Namba, bank transfer or checkout)
            window.location.href = order.pay_url;
          } catch (err) { toast(err.message); }
        }, { extra: [el("h4", { style: "margin-top:1rem" }, t("buyer.choose_method")), chooser.node, chooser.note] })));
    }
  };

  // buyers land on #overview like everyone else; show them the marketplace home
  VIEWS.b_overview = VIEWS.b_buyer;

  // ---------------------------------------------------------------- farmer directory
  VIEWS.farmerdir = async () => {
    const farmers = await api.get("/market/farmers");
    const search = el("input", { placeholder: t("buyer.search_farmer"), "aria-label": t("buyer.search_farmer") });
    const listBox = el("div");
    const render = (rows) => {
      listBox.replaceChildren(
        rows.length
          ? table(
            [t("common.name"), t("farmer.district"), t("common.region"), t("buyer.crops"), [t("buyer.active_listings"), "num"], t("common.phone"), ""],
            rows.map((f) => [
              el("strong", {}, f.name),
              f.district || "-",
              f.region || "-",
              (f.crops_for_sale || []).join(", ") || "-",
              num(f.active_listings || 0),
              el("a", { href: `tel:${f.phone}` }, f.phone),
            ]))
          : el("p", { class: "empty" }, t("buyer.no_farmers")));
    };
    render(farmers);
    search.addEventListener("input", () => {
      const q = search.value.trim().toLowerCase();
      render(!q ? farmers : farmers.filter((f) =>
        [f.name, f.district, f.region, f.cooperative, ...(f.crops_for_sale || [])]
          .filter(Boolean).some((v) => String(v).toLowerCase().includes(q))));
    });
    setView(
      el("h1", {}, t("nav.farmers")),
      el("p", { class: "muted" }, t("buyer.farmer_intro")),
      el("div", { class: "row", style: "margin:0.75rem 0" }, search),
      card(null, listBox));
  };

  // ================================================================ loan-officer desk
  VIEWS.s_officer = async () => {
    const data = await api.get("/officer/loan-dashboard");
    const p = data.portfolio;
    const an = data.analytics || {};
    const TABS = [
      ["queue", "officer.tab_queue"],
      ["collections", "officer.tab_collections"],
      ["institutions", "officer.tab_institutions"],
      ["portfolio", "officer.tab_portfolio"],
    ];
    let activeTab = location.hash.split("/")[1] || "queue";
    if (!TABS.some(([id]) => id === activeTab)) activeTab = "queue";

    const tabBar = el("div", { class: "tabs", role: "tablist" },
      TABS.map(([id, key]) => el("button", {
        class: `tab-btn${id === activeTab ? " active" : ""}`,
        role: "tab", type: "button", "aria-selected": String(id === activeTab),
        onclick: () => { location.hash = `officer/${id}`; },
      }, t(key))));
    const body = el("div");

    const approvedRow = (a) => [a.reference, a.farmer_name || "-", num(fmt.money(a.requested_amount)),
      riskPill(a.ai_risk_level), pill(a.status), fmt.date(a.applied_on),
      el("button", { class: "btn small secondary", onclick: () => openLoanFile(a.id) }, t("officer.open_file"))];

    setView(
      el("h1", {}, t("officer.desk_title")),
      el("p", { class: "muted" }, t("officer.desk_intro")),
      el("div", { class: "grid grid-4" },
        stat(t("officer.pending"), fmt.number(data.queue.pending_count),
          data.queue.high_risk_pending ? t("officer.high_risk_pending", { n: data.queue.high_risk_pending }) : null),
        stat(t("officer.awaiting_disbursement"), fmt.number(data.queue.approved_count)),
        stat(t("officer.overdue"), fmt.number(data.queue.overdue_count)),
        stat(t("officer.active_loans"), fmt.number(p.active_loans))),
      tabBar, body);

    if (activeTab === "queue") renderQueue();
    else if (activeTab === "collections") renderCollections();
    else if (activeTab === "institutions") renderInstitutions();
    else renderPortfolio();

    // -------------------------------------------------------------- work queue
    function renderQueue() {
      const search = el("input", { type: "search", placeholder: t("officer.search"), "aria-label": t("common.search") });
      const riskSel = el("select", { "aria-label": t("officer.filter_risk") },
        el("option", { value: "" }, `${t("officer.filter_risk")}: ${t("officer.all")}`),
        ["LOW", "MEDIUM", "HIGH"].map((r) => el("option", { value: r }, t(`risk.level_${r.toLowerCase()}`))));
      const listBox = el("div");
      const renderList = () => {
        const q = search.value.trim().toLowerCase();
        const rows = data.pending.filter((row) =>
          (!q || [row.reference, row.farmer_name].filter(Boolean).some((v) => String(v).toLowerCase().includes(q)))
          && (!riskSel.value || row.ai_risk_level === riskSel.value));
        listBox.replaceChildren(rows.length ? table(
          [t("common.reference"), t("common.name"), [t("loan.requested_amount"), "num"], t("risk.credit_risk"),
           t("buyer.quality"), t("elig.insurance"), t("officer.suggested"), t("common.status"), ""],
          rows.map((row) => [
            el("strong", {}, row.reference),
            row.farmer_name || "-",
            num(fmt.money(row.requested_amount)),
            riskPill(row.ai_risk_level),
            row.data_quality_score === null || row.data_quality_score === undefined ? "-" : fmt.pct(row.data_quality_score),
            row.insured ? pill("ACTIVE", t("common.yes")) : pill("PENDING", t("common.no")),
            row.suggested_lender
              ? el("span", { class: "small" }, `${row.suggested_lender}${row.suggested_rate ? ` · ${fmt.pct(row.suggested_rate / 100)}` : ""}`)
              : el("span", { class: "muted small" }, t("officer.none_matched")),
            pill(row.status),
            el("div", { class: "insight-actions" },
              el("button", { class: "btn small", onclick: () => openLoanFile(row.id) }, t("officer.open_file")),
              el("button", { class: "btn small secondary", onclick: () => requirementsDialog(row.farmer_id, row.farmer_name) }, t("officer.requirements"))),
          ]), t("admin.no_pending")) : el("p", { class: "empty" }, t("admin.no_pending")));
      };
      renderList();
      search.addEventListener("input", renderList);
      riskSel.addEventListener("change", renderList);

      const approved = data.approved.length
        ? cardWithAction(t("officer.approved_ready"),
          el("a", { class: "btn small secondary", href: "#queue" }, t("loan.applications")),
          table([t("common.reference"), t("common.name"), [t("loan.requested_amount"), "num"], t("risk.credit_risk"), t("common.status"), t("loan.applied_on"), ""],
            data.approved.map(approvedRow)))
        : null;
      body.replaceChildren(
        cardWithAction(t("officer.pending_queue"),
          el("div", { class: "row", style: "gap:0.5rem" }, search, riskSel), listBox),
        approved);
    }

    // ------------------------------------------------------------- collections
    function renderCollections() {
      const remind = (row) => {
        const f = form([
          { name: "channel", label: t("common.category"), type: "select", value: "BOTH",
            options: [["SMS", "SMS"], ["IN_APP", t("nav.notifications")], ["BOTH", "SMS + App"]] },
          { name: "message", label: t("form.note"), type: "textarea", placeholder: t("officer.reminder_body") },
        ], t("officer.remind"), async (d) => {
          await api.post(`/officer/applications/${row.id}/remind`, { channel: d.channel, message: d.message || null });
          toast(t("officer.reminder_sent"));
          dialog.close();
        });
        openDialog(`${t("officer.remind")} · ${row.farmer_name || row.reference}`, f);
      };
      const rows = data.collections || [];
      body.replaceChildren(
        cardWithAction(t("officer.tab_collections"), el("span", { class: "muted small" }, t("officer.collections_intro")),
          table([
            t("common.reference"), t("common.name"), [t("officer.outstanding"), "num"],
            [t("officer.days_overdue"), "num"], t("officer.due_date"), t("officer.last_payment"),
            t("risk.credit_risk"), t("common.phone"), "",
          ], rows.map((row) => [
            el("strong", {}, row.reference),
            row.farmer_name || "-",
            num(fmt.money(row.outstanding_balance)),
            num(fmt.number(row.days_overdue)),
            row.due_date ? fmt.date(row.due_date) : "-",
            row.last_payment_date ? fmt.date(row.last_payment_date) : "-",
            riskPill(row.ai_risk_level),
            row.farmer_phone ? el("a", { href: `tel:${row.farmer_phone}` }, row.farmer_phone) : "-",
            el("div", { class: "insight-actions" },
              el("button", { class: "btn small", onclick: () => remind(row) }, t("officer.remind")),
              el("button", { class: "btn small secondary", onclick: () => openLoanFile(row.id) }, t("officer.open_file"))),
          ]), t("officer.no_collections"))));
    }

    // ------------------------------------------------------------ institutions
    async function renderInstitutions() {
      body.replaceChildren(loading());
      try {
        const inst = await api.get("/officer/financial-institutions");
        const sections = Object.entries(inst.institutions).map(([kind, rows]) =>
          el("div", { style: "margin-top:0.75rem" },
            el("h3", {}, t(`officer.kind_${kind.toLowerCase()}`)),
            table(
              [t("officer.institution"), [t("officer.interest_rate"), "num"], [t("officer.min_loan"), "num"],
               [t("officer.max_loan"), "num"], [t("officer.term"), "num"], t("officer.area"),
               t("officer.wallet"), t("officer.insurance_required")],
              rows.map((l) => [
                el("strong", {}, l.name),
                num(fmt.pct(l.interest_rate / 100)),
                num(fmt.compact(l.min_loan_amount)),
                num(fmt.compact(l.max_loan_amount)),
                num(`${l.typical_repayment_period_months} ${t("common.months")}`),
                l.area || t("officer.nationwide"),
                l.wallet_name || "-",
                l.requires_insurance ? pill("ACTIVE", t("common.yes")) : pill("PENDING", t("common.no")),
              ]))));
        const rails = Object.entries(inst.payment_providers || {}).map(([kind, rows]) =>
          el("div", { style: "margin-top:0.75rem" },
            el("h3", {}, t(`officer.kind_${kind.toLowerCase()}`)),
            table([t("officer.institution"), t("common.category"), t("common.reference")],
              rows.map((r) => [el("strong", {}, r.display_name), r.channel, r.lipa_number || "-"]))));
        body.replaceChildren(
          card(t("officer.tab_institutions"),
            el("p", { class: "muted small" }, t("officer.count", { count: inst.total })),
            ...sections),
          card(t("officer.payment_rails"), ...(rails.length ? rails : [el("p", { class: "empty" }, t("common.no_data"))])));
      } catch (err) {
        body.replaceChildren(el("p", { class: "alert error" }, err.message));
      }
    }

    // --------------------------------------------------------------- portfolio
    function renderPortfolio() {
      const risk = an.risk_distribution || {};
      const status = an.status_distribution || {};
      const exposure = data.exposure || [];
      body.replaceChildren(
        el("div", { class: "grid grid-4" },
          stat(t("officer.approval_rate"), an.approval_rate === null || an.approval_rate === undefined ? "-" : fmt.pct(an.approval_rate)),
          stat(t("officer.avg_ticket"), fmt.money(an.avg_ticket)),
          stat(t("officer.avg_decision_time"), an.avg_decision_hours === null || an.avg_decision_hours === undefined ? "-" : t("impact.hours", { n: fmt.number(an.avg_decision_hours, 1) })),
          stat(t("officer.avg_term_months"), an.avg_term_months ? t("common.months_n", { n: an.avg_term_months }) : "-")),
        el("div", { class: "grid grid-3", style: "margin-top:1rem" },
          stat(t("officer.disbursed_total"), fmt.money(p.disbursed_total)),
          stat(t("officer.repaid_total"), fmt.money(p.repaid_total)),
          stat(t("officer.outstanding_total"), fmt.money(p.outstanding_total))),
        el("div", { class: "grid grid-3", style: "margin-top:0.5rem" },
          stat(t("officer.recovery_rate"), p.recovery_rate === null ? "-" : fmt.pct(p.recovery_rate)),
          stat(t("officer.overdue_amount"), fmt.money(p.overdue_amount)),
          stat(t("officer.institutions_total"), fmt.number((data.institutions || {}).total || 0))),
        el("div", { class: "grid grid-4", style: "margin-top:0.5rem" },
          stat(t("officer.par30"), fmt.money((p.par || {}).par30)),
          stat(t("officer.par60"), fmt.money((p.par || {}).par60)),
          stat(t("officer.par90"), fmt.money((p.par || {}).par90)),
          stat(t("officer.non_performing"), an.non_performing_ratio === undefined ? "-" : fmt.pct(an.non_performing_ratio))),
        el("div", { class: "grid grid-3", style: "margin-top:1rem" },
          card(t("officer.risk_distribution"), table([t("officer.filter_risk"), [t("officer.loans_count"), "num"]],
            Object.entries(risk).map(([level, n]) => [level === "UNKNOWN" ? "-" : riskPill(level), num(n)]))),
          card(t("officer.status_distribution"), table([t("common.status"), [t("officer.loans_count"), "num"]],
            Object.entries(status).map(([s, n]) => [pill(s), num(n)])))));
      body.append(
        el("div", { style: "margin-top:1rem" }, card(t("officer.exposure"),
          table([t("officer.institution"), t("common.category"), [t("officer.loans_count"), "num"],
                 [t("officer.principal"), "num"], [t("officer.outstanding"), "num"]],
            exposure.map((e) => [el("strong", {}, e.name), e.type,
              num(e.loans), num(fmt.money(e.principal)), num(fmt.money(e.outstanding))]),
            t("common.no_data")))));
    }

    // ------------------------------------------------------- the loan file
    async function openLoanFile(id) {
      let file;
      try { file = await api.get(`/officer/applications/${id}`); }
      catch (err) { toast(err.message); return; }
      const paint = () => {
        dialog.replaceChildren(
          el("div", { class: "card-head" }, el("h2", {}, `${t("officer.review_file")} · ${file.application.reference}`),
            el("button", { class: "btn secondary small", type: "button", onclick: () => dialog.close() }, t("common.close"))),
          fileBody(file));
        if (!dialog.open) dialog.showModal();
      };
      const reload = async () => { file = await api.get(`/officer/applications/${id}`); paint(); };
      paint();

      function fileBody(fn) {
        const app = fn.application;
        const req = fn.requirements;
        const box = el("div", { class: "stack" });

        // overview
        box.append(el("section", { class: "card" },
          el("div", { class: "row" }, pill(app.status), riskPill(app.ai_risk_level),
            el("span", { class: "muted small" }, `${t("officer.amount")}: ${fmt.money(app.requested_amount)}`)),
          el("p", {}, el("strong", {}, `${t("loan.purpose")} `), app.purpose),
          table([t("common.category"), ["", "num"]], [
            [t("officer.ai_offer"), num(`${fmt.money((fn.risk || {}).recommended_min)} - ${fmt.money((fn.risk || {}).recommended_max)}`)],
            [t("loan.term"), num(t("common.months_n", { n: app.term_months }))],
            [t("loan.interest_rate"), num(`${fmt.number(app.interest_rate, 1)}%`)],
            [t("officer.institution"), app.lender_code || "-"],
            app.decision_reason ? [t("officer.decision_reason"), app.decision_reason] : null,
          ].filter(Boolean))));

        // requirements
        const reqCard = el("section", { class: "card" }, el("h3", {}, t("officer.requirements")));
        reqCard.append(el("div", { class: "row" },
          pill(req.eligibility_status === "ELIGIBLE" ? "ACTIVE" : "PENDING",
            t(req.eligibility_status === "ELIGIBLE" ? "elig.ready" : "elig.not_yet")),
          req.insured ? pill("ACTIVE", t("elig.insurance")) : pill("PENDING", t("elig.officer_uninsured"))));
        reqCard.append(el("h4", {}, t("officer.blockers")),
          (req.blockers || []).length
            ? el("ul", {}, req.blockers.map((b) => el("li", {}, t(b.key, b.params || {}))))
            : el("p", { class: "small muted" }, t("officer.no_blockers")));
        if (req.data_quality) {
          reqCard.append(el("h4", {}, t("dq.title")), qualityBlock(req.data_quality, req.data_quality.needs_review));
        }
        box.append(reqCard);

        // risk
        const riskCard = el("section", { class: "card" }, el("h3", {}, t("officer.risk_assessment")));
        const r = fn.risk || {};
        riskExplanation(riskCard, {
          risk_level: r.level, confidence: r.confidence,
          recommended_min: r.recommended_min, recommended_max: r.recommended_max,
          weather_risk: r.weather_risk, market_risk: r.market_risk, production_risk: r.production_risk,
          components: r.components,
          positive_factors: (r.positive_factors || []).map((x) => ({ ...x, label: AT.lang === "sw" ? (x.label_sw || x.label_en) : x.label_en })),
          risk_factors: (r.risk_factors || []).map((x) => ({ ...x, label: AT.lang === "sw" ? (x.label_sw || x.label_en) : x.label_en })),
        });
        box.append(riskCard);

        // financial profile
        const fp = fn.financial_profile || {};
        box.append(el("section", { class: "card" }, el("h3", {}, t("officer.financial_profile")),
          el("div", { class: "grid grid-3" },
            stat(t("officer.monthly_income"), fmt.money(fp.monthly_income)),
            stat(t("officer.monthly_expenses"), fmt.money(fp.monthly_expenses)),
            stat(t("officer.monthly_net"), fmt.money(fp.monthly_net))),
          el("div", { class: "grid grid-3", style: "margin-top:0.5rem" },
            stat(t("officer.savings_rate"), fmt.pct(fp.savings_rate)),
            stat(t("officer.on_time_rate"), fmt.pct(fp.on_time_repayment_rate)),
            stat(t("officer.financial_health"), t(`officer.health_${String(fp.financial_health || "unknown").toLowerCase()}`)))));

        // farms
        if ((fn.farms || []).length) {
          box.append(el("section", { class: "card" }, el("h3", {}, t("officer.farms")),
            table([t("common.name"), [t("farmer.farm_size"), "num"], t("farmer.district"), t("crop.name"), t("common.status")],
              fn.farms.map((farm) => [farm.name, num(fmt.number(farm.area_acres, 1)), farm.district || "-",
                (farm.crops || []).filter((c) => c.is_current).map((c) => c.crop_name).join(", ") || "-",
                farm.is_active ? pill("ACTIVE", t("common.yes")) : pill("INACTIVE", t("common.no"))]))));
        }

        // institutions + assign
        const tiers = (fn.institutions && fn.institutions.tiers) || {};
        const eligible = (tiers.eligible && tiers.eligible.lenders) || [];
        const withIns = (tiers.with_insurance && tiers.with_insurance.lenders) || [];
        const lenderTable = (rows) => rows.length
          ? table([t("officer.institution"), [t("officer.interest_rate"), "num"], [t("officer.min_loan"), "num"],
                   [t("officer.max_loan"), "num"], [t("officer.term"), "num"], t("officer.wallet")],
            rows.map((l) => [el("strong", {}, l.name), num(fmt.pct(l.interest_rate / 100)),
              num(fmt.compact(l.min_loan_amount)), num(fmt.compact(l.max_loan_amount)),
              num(t("common.months_n", { n: l.typical_repayment_period_months })), l.wallet_name || "-"]))
          : el("p", { class: "small muted" }, t("officer.none_matched"));
        const instCard = el("section", { class: "card" }, el("h3", {}, t("officer.matched_now")), lenderTable(eligible));
        if (withIns.length) instCard.append(el("h4", { style: "margin-top:0.75rem" }, t("officer.matched_with_insurance")), lenderTable(withIns));
        if (["PENDING", "UNDER_REVIEW"].includes(app.status) && eligible.length) {
          instCard.append(el("h4", { style: "margin-top:0.75rem" }, t("officer.assign_lender")),
            form([
              { name: "lender_code", label: t("officer.institution"), type: "select", required: true, wide: true,
                value: app.lender_code || eligible[0].code,
                options: eligible.map((l) => [l.code, `${l.name} · ${fmt.pct(l.interest_rate / 100)} · ${t("common.months_n", { n: l.typical_repayment_period_months })}`]) },
              { name: "term_months", label: t("loan.term"), type: "number", min: 1, max: 60, value: app.term_months },
            ], t("officer.confirm_assign"), async (d) => {
              await api.post(`/loans/${app.id}/assign-lender`, { lender_code: d.lender_code, term_months: Number(d.term_months) });
              toast(t("officer.lender_assigned"));
              await reload();
            }));
        }
        box.append(instCard);

        // decision
        box.append(decisionCard(fn, reload));

        // timeline
        const steps = ((app.timeline || {}).steps || []).filter((s) => s.reached);
        if (steps.length) {
          box.append(el("section", { class: "card" }, el("h3", {}, t("officer.timeline")),
            el("ul", {}, steps.map((s) => el("li", {}, `${t(s.label_key)}${s.at ? ` · ${fmt.datetime(s.at)}` : ""}`)))));
        }

        // the amortisation the officer booked (once a loan exists)
        if ((fn.schedule || []).length) {
          const tone = { PAID: "PAID", DUE: "PENDING" };
          box.append(el("section", { class: "card" }, el("h3", {}, t("officer.repayment_schedule")),
            table([t("officer.installment"), t("loan.due_date"), [t("officer.monthly_payment"), "num"], [t("officer.balance"), "num"], t("common.status")],
              fn.schedule.map((s) => [s.installment, fmt.date(s.due_date), num(fmt.money(s.payment)), num(fmt.money(s.balance)), pill(tone[s.status] || "SCHEDULED")]))));
        }

        // history
        if ((fn.history || []).length) {
          box.append(el("section", { class: "card" }, el("h3", {}, t("officer.history")),
            table([t("common.reference"), [t("common.amount"), "num"], t("common.status"), t("risk.credit_risk"), t("loan.applied_on")],
              fn.history.map((h) => [h.reference, num(fmt.money(h.requested_amount)), pill(h.status), riskPill(h.risk_level), fmt.date(h.applied_on)]))));
        }

        // notes
        const noteBox = el("div");
        const renderNotes = (rows) => noteBox.replaceChildren(
          rows.length
            ? el("ul", {}, rows.map((n) => el("li", {}, el("strong", {}, n.officer || "-"), ` · ${fmt.datetime(n.at)} — `, n.note)))
            : el("p", { class: "small muted" }, t("officer.no_notes")));
        renderNotes(fn.notes || []);
        box.append(el("section", { class: "card" }, el("h3", {}, t("officer.notes")),
          noteBox,
          form([{ name: "note", label: t("officer.add_note"), type: "textarea", required: true, placeholder: t("officer.note_placeholder") }],
            t("officer.add_note"), async (d) => {
              const saved = await api.post(`/officer/applications/${app.id}/note`, { note: d.note });
              toast(t("officer.note_saved"));
              renderNotes([saved, ...(fn.notes || [])]);
            })));

        return box;
      }

      function decisionCard(fn, reload) {
        const app = fn.application;
        const card = el("section", { class: "card" }, el("h3", {}, t("officer.decide")));
        const after = async (msg) => { toast(msg); await reload(); };
        if (["PENDING", "UNDER_REVIEW"].includes(app.status)) {
          if (app.status === "PENDING") {
            card.append(el("button", { class: "btn secondary", type: "button",
              onclick: async (e) => { e.target.disabled = true; await api.post(`/loans/${app.id}/review`); await after(t("loan.under_review")); } }, t("loan.under_review")));
          }
          const lenders = ((fn.institutions.tiers.eligible || {}).lenders) || [];
          const lenderField = lenders.length
            ? [{ name: "lender_code", label: t("officer.institution"), type: "select", wide: true,
                value: app.lender_code || lenders[0].code, options: lenders.map((l) => [l.code, `${l.name} · ${fmt.pct(l.interest_rate / 100)}`]) }]
            : [];
          card.append(
            el("h4", {}, t("officer.approve_terms")),
            el("p", { class: "muted small" },
              `${t("officer.monthly_payment")}: ${fmt.money((fn.preview || {}).monthly_payment)} · `,
              `${t("officer.total_repayable")}: ${fmt.money((fn.preview || {}).total_repayable)}`),
            form([
              { name: "approved_amount", label: t("officer.amount"), type: "number", min: 1, required: true, value: Number(app.requested_amount) },
              { name: "term_months", label: t("loan.term"), type: "number", min: 1, max: 60, value: app.term_months },
              { name: "interest_rate", label: t("loan.interest_rate"), type: "number", min: 0, step: "0.01", value: Number(app.interest_rate) },
              ...lenderField,
              { name: "conditions", label: t("officer.conditions"), type: "textarea" },
              { name: "reason", label: t("form.reason"), type: "textarea" },
            ], t("common.approve"), async (d) => {
              await api.post(`/loans/${app.id}/approve`, {
                approved_amount: Number(d.approved_amount), term_months: Number(d.term_months),
                interest_rate: Number(d.interest_rate), lender_code: d.lender_code || null,
                conditions: d.conditions || null, reason: d.reason || null,
              });
              await after(t("loan.approved"));
            }),
            el("h4", { style: "margin-top:0.75rem" }, t("common.reject")),
            form([{ name: "reason", label: t("form.reason"), type: "textarea", required: true }], t("common.reject"), async (d) => {
              await api.post(`/loans/${app.id}/reject`, { reason: d.reason });
              await after(t("loan.rejected"));
            }),
            el("h4", { style: "margin-top:0.75rem" }, t("loan.request_info")),
            form([{ name: "reason", label: t("form.note"), type: "textarea", required: true }], t("loan.request_info"), async (d) => {
              await api.post(`/loans/${app.id}/request-info`, { reason: d.reason });
              await after(t("loan.request_info_sent"));
            }));
        } else if (app.status === "APPROVED") {
          card.append(el("button", { class: "btn", type: "button", onclick: async (e) => {
            e.target.disabled = true;
            try {
              const res = await api.post(`/loans/${app.id}/disburse`);
              const pay = res.payout || {};
              toast(t("payout.sent_toast", { amount: fmt.number(res.loan_principal), provider: pay.provider_name || "", account: pay.account_masked || "" }), 6000);
              await reload();
            } catch (err) { toast(err.message); e.target.disabled = false; }
          } }, t("loan.disburse_now")));
        }
        if (["DISBURSED", "ACTIVE"].includes(app.status)) {
          card.append(el("h4", {}, t("loan.add_repayment")), form([
            { name: "amount", label: t("loan.payment_amount"), type: "number", required: true, min: 1, value: Number(app.monthly_payment) || null },
          ], t("loan.add_repayment"), async (d) => {
            await api.post(`/loans/${app.id}/repayments`, d);
            await after(t("loan.repayment_saved"));
          }));
        }
        if ((app.repayments || []).length) {
          card.append(el("h4", {}, t("loan.repayments")),
            table([t("loan.payment_date"), [t("common.amount"), "num"], t("common.status")],
              app.repayments.map((rr) => [fmt.date(rr.payment_date), num(fmt.money(rr.amount)),
                rr.is_on_time ? pill("PAID", t("loan.on_time")) : pill("DEFAULTED", t("loan.late"))])));
        }
        return card;
      }
    }

    async function requirementsDialog(farmerId, farmerName) {
      openDialog(`${t("officer.requirements")} · ${farmerName || ""}`, el("div", {}, loading()));
      try {
        const req = await api.get(`/officer/farmer-requirements?farmer_id=${encodeURIComponent(farmerId)}`);
        const box = el("div", { class: "stack" });
        box.append(el("section", { class: "card" }, el("h3", {}, t("elig.title")),
          pill(req.eligibility_status === "ELIGIBLE" ? "ACTIVE" : "PENDING",
            t(req.eligibility_status === "ELIGIBLE" ? "elig.ready" : "elig.not_yet")),
          req.blockers.length
            ? el("ul", {}, req.blockers.map((b) => el("li", {}, t(b.key, b.params || {}))))
            : el("p", { class: "small muted" }, t("officer.no_blockers"))));
        box.append(el("section", { class: "card" }, el("h3", {}, t("dq.title")),
          qualityBlock(req.data_quality, req.data_quality.needs_review)));
        box.append(el("section", { class: "card" }, el("h3", {}, t("elig.insurance")),
          req.insured ? pill("ACTIVE", t("common.yes")) : pill("PENDING", t("common.no"))));
        const tiers = req.lenders && req.lenders.tiers ? req.lenders.tiers : {};
        const lenderRows = (tier) => (tiers[tier] && tiers[tier].lenders) || [];
        box.append(el("section", { class: "card" }, el("h3", {}, t("officer.matched_now")),
          lenderRows("eligible").length
            ? table([t("officer.institution"), [t("officer.interest_rate"), "num"], [t("officer.max_loan"), "num"]],
              lenderRows("eligible").slice(0, 5).map((l) => [l.name, num(fmt.pct(l.interest_rate / 100)), num(fmt.compact(l.max_loan_amount))]))
            : el("p", { class: "small muted" }, t("officer.none_matched")),
          lenderRows("with_insurance").length
            ? [el("h3", { style: "margin-top:0.75rem" }, t("officer.matched_with_insurance")),
              table([t("officer.institution"), [t("officer.interest_rate"), "num"], [t("officer.max_loan"), "num"]],
                lenderRows("with_insurance").slice(0, 5).map((l) => [l.name, num(fmt.pct(l.interest_rate / 100)), num(fmt.compact(l.max_loan_amount))]))]
            : null));
        const head = el("div", { class: "card-head" }, el("h2", {}, `${t("officer.requirements")} · ${req.farmer_name || ""}`),
          el("button", { class: "btn secondary small", type: "button", onclick: () => dialog.close() }, t("common.close")));
        dialog.replaceChildren(head, box);
      } catch (err) {
        toast(err.message);
      }
    }
  };


  // ---- small screens: the sidebar is a drawer opened from the top-bar menu button
  (function drawer() {
    const btn = document.getElementById("menu-btn");
    if (!btn) return;
    btn.hidden = false;
    const scrim = document.createElement("div");
    scrim.className = "nav-scrim";
    document.body.appendChild(scrim);
    const set = (open) => {
      document.body.classList.toggle("nav-open", open);
      btn.setAttribute("aria-expanded", String(open));
    };
    btn.addEventListener("click", () => set(!document.body.classList.contains("nav-open")));
    scrim.addEventListener("click", () => set(false));
    sidebar.addEventListener("click", (e) => { if (e.target.closest("a")) set(false); });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") set(false); });
    window.addEventListener("hashchange", () => set(false));
  })();

  // ================================================================ boot
  renderSidebar();
  renderTopbar();
  refreshUnread();
  route();
})();
