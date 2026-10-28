/* AgriTrust AI - shared browser helpers: API client, i18n, formatting, DOM builder. */
(function () {
  "use strict";

  const PREFIX = "/api/v1";
  const TOKEN_KEY = "agritrust.tokens";
  const lang = document.documentElement.lang || "en";
  let catalog = {};

  // ------------------------------------------------------------------ i18n
  async function loadCatalog() {
    try {
      const res = await fetch(`${PREFIX}/i18n/${lang}`);
      catalog = res.ok ? await res.json() : {};
    } catch (_) {
      catalog = {};
    }
  }

  /** Translate a key; falls back to the key itself, same as the server. */
  function t(key, params) {
    let value = catalog[key] || key;
    if (params) {
      for (const [k, v] of Object.entries(params)) value = value.split(`{${k}}`).join(v);
    }
    return value;
  }

  /** Error details from the API are translation keys (sometimes without a prefix). */
  function errorText(detail) {
    if (!detail) return t("common.error");
    if (typeof detail === "string") {
      if (catalog[detail]) return catalog[detail];
      if (catalog[`error.${detail}`]) return catalog[`error.${detail}`];
      return detail;
    }
    if (Array.isArray(detail)) {
      // FastAPI validation errors
      return detail
        .map((d) => `${(d.loc || []).slice(1).join(".")}: ${d.msg}`)
        .join("; ");
    }
    if (typeof detail === "object") {
      return Object.values(detail).flat().join(" ");
    }
    return t("common.error");
  }

  // ------------------------------------------------------------------ tokens
  const tokens = {
    get() {
      try { return JSON.parse(localStorage.getItem(TOKEN_KEY)) || null; } catch (_) { return null; }
    },
    set(data) {
      localStorage.setItem(TOKEN_KEY, JSON.stringify({
        access: data.access_token, refresh: data.refresh_token, user: data.user,
      }));
    },
    clear() { localStorage.removeItem(TOKEN_KEY); },
  };

  class ApiError extends Error {
    constructor(status, detail) {
      super(errorText(detail));
      this.status = status;
      this.detail = detail;
    }
  }

  let refreshing = null;
  async function refreshTokens() {
    const current = tokens.get();
    if (!current || !current.refresh) return false;
    refreshing = refreshing || fetch(`${PREFIX}/auth/refresh`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: current.refresh }),
    }).then(async (res) => {
      if (!res.ok) return false;
      tokens.set(await res.json());
      return true;
    }).finally(() => { refreshing = null; });
    return refreshing;
  }

  async function request(method, path, body, retry = true) {
    const headers = { "Accept-Language": lang };
    const current = tokens.get();
    if (current && current.access) headers.Authorization = `Bearer ${current.access}`;
    if (body !== undefined) headers["Content-Type"] = "application/json";
    const res = await fetch(PREFIX + path, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    if (res.status === 401 && retry && current && current.refresh && !path.startsWith("/auth/")) {
      if (await refreshTokens()) return request(method, path, body, false);
      tokens.clear();
      window.location.href = "/login";
      throw new ApiError(401, "invalid_credentials");
    }
    const data = res.status === 204 ? null : await res.json().catch(() => null);
    if (!res.ok) throw new ApiError(res.status, data && data.detail);
    return data;
  }

  /**
   * Send a FormData body, for a file upload.
   *
   * Deliberately not routed through request(): that one JSON-encodes the body and
   * sets Content-Type: application/json, which would leave the multipart boundary
   * unset and the server would see an empty file. Letting the browser set the
   * header itself is the whole point of using FormData here.
   */
  async function upload(path, formData) {
    const headers = { "Accept-Language": lang };
    const current = tokens.get();
    if (current && current.access) headers.Authorization = `Bearer ${current.access}`;
    const res = await fetch(PREFIX + path, { method: "POST", headers, body: formData });
    const data = await res.json().catch(() => null);
    if (!res.ok) throw new ApiError(res.status, data && data.detail);
    return data;
  }

  const api = {
    get: (p) => request("GET", p),
    post: (p, b) => request("POST", p, b === undefined ? {} : b),
    patch: (p, b) => request("PATCH", p, b),
    del: (p) => request("DELETE", p),
    upload,
  };

  async function setLanguage(code) {
    await fetch(`${PREFIX}/lang`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ language: code }),
    });
    if (tokens.get()) {
      await api.patch(`/auth/me/language?language=${code}`).catch(() => {});
    }
    // Reload so the page renders in the new language. Assigning the same URL is not
    // enough: with a #hash (every dashboard screen) the browser only scrolls, it does not reload.
    const url = new URL(window.location.href);
    if (url.searchParams.has("lang")) {
      url.searchParams.delete("lang");
      window.location.replace(url.toString());
    } else {
      window.location.reload();
    }
  }

  async function logout() {
    const current = tokens.get();
    if (current) {
      await api.post("/auth/logout", { refresh_token: current.refresh }).catch(() => {});
    }
    tokens.clear();
    window.location.href = "/login";
  }

  // ------------------------------------------------------------------ format
  const locale = lang === "sw" ? "sw-TZ" : "en-GB";
  const fmt = {
    money(v) {
      if (v === null || v === undefined || v === "") return "—";
      return `TZS ${Number(v).toLocaleString(locale, { maximumFractionDigits: 0 })}`;
    },
    number(v, digits = 0) {
      if (v === null || v === undefined || v === "") return "—";
      return Number(v).toLocaleString(locale, { maximumFractionDigits: digits });
    },
    compact(v) {
      const n = Number(v) || 0;
      const abs = Math.abs(n);
      if (abs >= 1e9) return `${(n / 1e9).toFixed(1)}B`;
      if (abs >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
      if (abs >= 1e3) return `${(n / 1e3).toFixed(abs >= 1e4 ? 0 : 1)}K`;
      return `${Math.round(n)}`;
    },
    pct(v, digits = 0) {
      if (v === null || v === undefined) return "—";
      return `${(Number(v) * 100).toFixed(digits)}%`;
    },
    date(v) {
      if (!v) return "—";
      const d = new Date(v);
      return isNaN(d) ? v : d.toLocaleDateString(locale, { day: "numeric", month: "short", year: "numeric" });
    },
    datetime(v) {
      if (!v) return "—";
      const d = new Date(v);
      return isNaN(d) ? v : d.toLocaleString(locale, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
    },
    month(key) {
      // "2026-04" -> "Apr 26"
      const [y, m] = String(key).split("-").map(Number);
      if (!y || !m) return key;
      return new Date(y, m - 1, 1).toLocaleDateString(locale, { month: "short", year: "2-digit" });
    },
  };

  // ------------------------------------------------------------------ DOM
  /**
   * el("div", {class: "card", onclick: fn}, "text", childNode, [more])
   * Strings become text nodes, so API data is never parsed as HTML.
   */
  function el(tag, attrs, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs || {})) {
      if (value === null || value === undefined || value === false) continue;
      if (key.startsWith("on") && typeof value === "function") node.addEventListener(key.slice(2), value);
      else if (key === "class") node.className = value;
      else if (key === "dataset") Object.assign(node.dataset, value);
      else if (value === true) node.setAttribute(key, "");
      else node.setAttribute(key, String(value));
    }
    append(node, children);
    return node;
  }
  function append(node, children) {
    for (const child of children.flat(Infinity)) {
      if (child === null || child === undefined || child === false) continue;
      node.appendChild(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return node;
  }

  function toast(message, ms = 3200) {
    let box = document.getElementById("toast");
    if (!box) {
      box = el("div", { id: "toast", role: "status", "aria-live": "polite" });
      document.body.appendChild(box);
    }
    box.textContent = message;
    box.classList.remove("hidden");
    clearTimeout(box._timer);
    box._timer = setTimeout(() => box.classList.add("hidden"), ms);
  }

  /** Map risk / status values to a pill (dot + words, never colour alone). */
  const STATUS_TONE = {
    LOW: "good", MEDIUM: "warning", HIGH: "critical",
    HEALTHY: "good", MODERATE: "warning", WEAK: "critical", UNKNOWN: "",
    PENDING: "info", UNDER_REVIEW: "warning", APPROVED: "good", REJECTED: "critical",
    DISBURSED: "good", ACTIVE: "good", COMPLETED: "good", DEFAULTED: "critical",
    EXPIRED: "", CANCELLED: "", CLAIMED: "serious", PENDING_REVIEW: "warning",
    ELIGIBLE: "serious", PAID: "good", DETECTED: "serious", CONFIRMED: "critical", DISMISSED: "",
    SENT: "info", DELIVERED: "good", FAILED: "critical",
  };
  const STATUS_KEY = {
    PENDING_REVIEW: "insurance.status_pending_review",
    LOW: "risk.level_low", MEDIUM: "risk.level_medium", HIGH: "risk.level_high",
    HEALTHY: "finance.healthy", MODERATE: "finance.needs_attention", WEAK: "finance.needs_attention",
    UNKNOWN: "common.not_available",
    PENDING: "loan.pending", UNDER_REVIEW: "loan.under_review", APPROVED: "loan.approved",
    REJECTED: "loan.rejected", DISBURSED: "loan.disbursed", ACTIVE: "loan.active",
    COMPLETED: "loan.completed", DEFAULTED: "loan.defaulted",
    EXPIRED: "insurance.expired", CANCELLED: "insurance.cancelled", CLAIMED: "insurance.claimed",
    ELIGIBLE: "insurance.eligible", PAID: "insurance.paid",
  };
  function pill(value, labelOverride) {
    if (!value) return el("span", { class: "muted" }, "—");
    const label = labelOverride || (STATUS_KEY[value] ? t(STATUS_KEY[value]) : String(value).replace(/_/g, " "));
    return el("span", { class: `pill ${STATUS_TONE[value] || ""}` }, label);
  }


  // ---- show / hide password: every password box gets an eye button
  const EYE = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M2 12s3.600-7 10-7 10 7 10 7-3.600 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/></svg>';
  const EYE_OFF = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 3l18 18"/><path d="M10.600 5.100A10 10 0 0 1 12 5c6.400 0 10 7 10 7a17 17 0 0 1-3.200 4.200M6.300 6.300A16.500 16.500 0 0 0 2 12s3.600 7 10 7a9.600 9.600 0 0 0 4-.9"/><path d="M9.900 9.900a3 3 0 0 0 4.200 4.200"/></svg>';
  function addPasswordToggle(input) {
    if (input.dataset.toggle) return;
    input.dataset.toggle = "1";
    const sw = lang === "sw";
    let box = input.parentElement;
    if (!box.classList.contains("input-icon")) {
      box = document.createElement("div");
      box.className = "input-icon no-lead";
      input.replaceWith(box);
      box.appendChild(input);
    }
    box.classList.add("has-toggle");
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "pw-toggle";
    const set = (shown) => {
      input.type = shown ? "text" : "password";
      btn.innerHTML = shown ? EYE_OFF : EYE;
      btn.setAttribute("aria-pressed", String(shown));
      btn.setAttribute("aria-label", shown ? (sw ? "Ficha nenosiri" : "Hide password") : (sw ? "Onyesha nenosiri" : "Show password"));
    };
    btn.addEventListener("click", () => { set(input.type === "password"); input.focus(); });
    set(false);
    box.appendChild(btn);
  }
  const scanPasswords = (root) => root.querySelectorAll('input[type="password"]').forEach(addPasswordToggle);
  scanPasswords(document);
  new MutationObserver(() => scanPasswords(document)).observe(document.documentElement, { childList: true, subtree: true });

  window.AT = { api, ApiError, t, errorText, tokens, loadCatalog, setLanguage, logout, fmt, el, append, toast, pill, lang };

  // language and logout buttons exist on every page
  document.addEventListener("click", (event) => {
    const btn = event.target.closest("[data-set-lang]");
    if (btn) setLanguage(btn.dataset.setLang);
    if (event.target.closest("[data-logout]")) logout();
  });
})();
