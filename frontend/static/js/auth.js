/* Login + registration forms. */
(async function () {
  "use strict";
  const { api, t, tokens, loadCatalog, el } = window.AT;
  await loadCatalog();

  const errorBox = document.getElementById("form-error");
  function showError(message) {
    errorBox.textContent = message;
    errorBox.classList.remove("hidden");
  }
  function clearError() {
    errorBox.classList.add("hidden");
    errorBox.textContent = "";
  }

  async function submit(form, path, body) {
    const button = form.querySelector("button[type=submit]");
    button.disabled = true;
    errorBox.classList.add("hidden");
    try {
      const data = await api.post(path, body);
      tokens.set(data);
      // A language picked in this browser wins and is saved to the account;
      // otherwise open the app in the language already saved on the account.
      const chosenHere = /(?:^|;\s*)agritrust_lang=/.test(document.cookie);
      const preferred = data.user && data.user.preferred_language;
      if (chosenHere && preferred !== AT.lang) {
        await api.patch(`/auth/me/language?language=${AT.lang}`).catch(() => {});
      } else if (!chosenHere && preferred && preferred !== AT.lang) {
        await fetch("/api/v1/lang", {
          method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ language: preferred }),
        });
      }
      window.location.href = "/dashboard";
    } catch (err) {
      showError(err.message);
    } finally {
      button.disabled = false;
    }
  }

  const login = document.getElementById("login-form");
  if (login) {
    if (tokens.get()) window.location.href = "/dashboard";
    login.addEventListener("submit", (e) => {
      e.preventDefault();
      const identifier = login.identifier.value.trim();
      const password = login.password.value;
      if (!identifier || !password) return showError(t("error.validation"));
      submit(login, "/auth/login", { identifier, password });
    });
  }

  const register = document.getElementById("register-form");
  if (register) {
    const farmerFields = document.getElementById("farmer-fields");
    register.role.addEventListener("change", () => {
      farmerFields.classList.toggle("hidden", register.role.value !== "FARMER");
      // the national ID is only mandatory for some roles, so the section and
      // the button both follow the picker
      applyRoleScope();
    });

    // The server is the only thing that decides whether an identity is verified.
    // This holds the session id and the ticket it hands back; it never infers
    // "verified" from anything the user did.
    //
    // Every element here is null when IDENTITY_REQUIRED is off, because the
    // server does not render the section at all - so the whole block is guarded
    // and registration falls back to behaving exactly as it did before.
    const id = {
      section: document.getElementById("id-section"),
      nidaStep: document.getElementById("id-step-nida"),
      nidaInput: document.getElementById("nida_number"),
      nidaBtn: document.getElementById("id-verify-btn"),
      nidaStatus: document.getElementById("id-nida-status"),
      nidaResult: document.getElementById("id-nida-result"),
      otpStep: document.getElementById("id-step-otp"),
      otpInput: document.getElementById("otp_code"),
      otpBtn: document.getElementById("id-otp-btn"),
      otpResend: document.getElementById("id-otp-resend"),
      otpStatus: document.getElementById("id-otp-status"),
      otpResult: document.getElementById("id-otp-result"),
      otpBody: document.getElementById("id-otp-body"),
      submitBtn: document.getElementById("register-submit"),
      skipNote: document.getElementById("id-skip-note"),
      required: false,
      isSandbox: false,
      verificationId: null,
      ticket: null,
      nidaVerified: false,
      phoneVerified: false,
      expectedLengths: [],
      // the roles the server gates, read off the section it rendered
      requiredRoles: new Set(
        ((document.getElementById("id-section")?.dataset.requiredRoles) || "")
          .split(",")
          .map((r) => r.trim())
          .filter(Boolean)
      ),
    };

    /** Is the national ID mandatory for the role currently picked? */
    function roleNeedsId() {
      return Boolean(id.section) && id.requiredRoles.has(register.role.value);
    }

    /** "16 or 20" / "16 au 20" - the accepted lengths, in the reader's language. */
    function lengthsPhrase(lengths) {
      const parts = (lengths || []).map(String);
      const joiner = AT.lang === "sw" ? " au " : " or ";
      return parts.length > 1 ? parts.join(joiner) : (parts[0] || "");
    }

    /**
     * Fill the one placeholder the server cannot render in the reader's
     * language, then translate. Used for both reasons we choose and messages
     * the API already translated.
     */
    function fillLengths(text) {
      const out = String(text == null ? "" : text);
      return out.includes("{lengths}")
        ? out.split("{lengths}").join(lengthsPhrase(id.expectedLengths))
        : out;
    }
    function reasonText(key) {
      return fillLengths(t(key || "common.error"));
    }

    function setBusy(step, busy) {
      step.classList.toggle("is-busy", busy);
      step.querySelectorAll("button").forEach((b) => { b.disabled = busy; });
    }

    function paintStatus(node, tone, text) {
      if (!text) { node.classList.add("hidden"); node.textContent = ""; return; }
      node.className = `id-status ${tone}`;
      node.textContent = text;
    }

    function paintResult(node, tone, title, body) {
      node.className = `id-result ${tone}`;
      node.replaceChildren(
        // replaceChildren prints a null child as the literal text "null", so the
        // optional body is filtered out rather than passed through
        ...[title ? el("span", { class: "id-result-title" }, title) : null,
           body ? el("span", { class: "id-result-body" }, body) : null]
          .filter(Boolean)
      );
      node.classList.remove("hidden");
    }

    function syncSubmit() {
      // no section rendered, or a role that does not need one: nothing to verify
      const ready = !roleNeedsId() || (id.nidaVerified && id.phoneVerified);
      if (id.submitBtn) id.submitBtn.disabled = !ready;
    }

    /**
     * Show the identity steps only for a role that has to verify, so a buyer or
     * an officer is never blocked by a field that does not apply to them, and say
     * so in as many words. Mirrors the role list the server enforces in
     * /auth/register.
     */
    function applyRoleScope() {
      if (!id.section) return;
      const needed = roleNeedsId();
      id.section.classList.toggle("hidden", !needed);
      if (id.skipNote) id.skipNote.classList.toggle("hidden", needed);
      syncSubmit();
    }

    /** Ask the API to text a code, and show the outcome. */
    async function requestOtp() {
      clearError();
      const phone = register.phone.value.trim();
      if (!phone) {
        showError(`${t("auth.phone")}: ${t("common.required")}`);
        register.phone.focus();
        return;
      }
      id.otpResend.disabled = true;
      paintStatus(id.otpStatus, "busy", t("identity.otp_sending"));
      try {
        const res = await api.post("/identity/otp/request", {
          verification_id: id.verificationId, phone,
        });
        if (!res.sent) {
          paintResult(id.otpResult, "critical", reasonText(res.reason));
          paintStatus(id.otpStatus, "critical", t("identity.failed_badge"));
          return;
        }
        id.otpStep.classList.remove("hidden");
        id.otpStep.classList.add("is-done");
        paintStatus(id.otpStatus, "good", t("identity.otp_sent"));
        // the server says how long the code is, so the wording is never a guess
        const digits = res.otp_digits || 6;
        id.otpInput.maxLength = digits;
        id.otpBody.textContent = t("identity.otp_body", {
          digits: String(digits),
          phone: `${phone.slice(0, 4)}••••${phone.slice(-4)}`,
        });
        // A test double cannot text a real phone, so it hands the code back and
        // the page says so. A real provider never returns one.
        if (res.dev_code) {
          paintResult(id.otpResult, "", t("identity.otp_sent"),
            t("identity.otp_dev_code", { code: res.dev_code }));
          id.otpInput.value = res.dev_code;
        } else {
          id.otpResult.classList.add("hidden");
        }
        id.otpInput.focus();
      } catch (err) {
        paintResult(id.otpResult, "critical", fillLengths(err.message));
        paintStatus(id.otpStatus, "critical", t("identity.failed_badge"));
      } finally {
        id.otpResend.disabled = false;
      }
    }

    async function verifyNida() {
      clearError();
      const number = id.nidaInput.value.trim();
      if (!number) {
        id.nidaInput.setAttribute("aria-invalid", "true");
        showError(t("identity.nida_required"));
        id.nidaInput.focus();
        return;
      }
      id.nidaInput.removeAttribute("aria-invalid");
      setBusy(id.nidaStep, true);
      id.nidaResult.classList.remove("hidden");
      paintStatus(id.nidaStatus, "busy", t("identity.verifying"));
      paintResult(id.nidaResult, "", t("identity.verifying"));
      try {
        const res = await api.post("/identity/nida/verify", {
          nida_number: number,
          full_name: register.full_name.value.trim() || null,
        });
        if (!res.verified) {
          id.nidaStep.classList.add("is-failed");
          id.nidaStep.classList.remove("is-done");
          paintStatus(id.nidaStatus, "critical", `✗ ${t("identity.failed_badge")}`);
          paintResult(id.nidaResult, "critical", reasonText(res.reason));
          return;
        }
        // verified: the number is never echoed back, only its masked last four
        id.nidaVerified = true;
        id.verificationId = res.verification_id;
        id.nidaStep.classList.add("is-done");
        id.nidaStep.classList.remove("is-failed");
        paintStatus(id.nidaStatus, "good", `✓ ${t("identity.verified_badge")}`);
        id.otpResult.classList.add("hidden");
        paintResult(id.nidaResult, "good", t("identity.verified_title"),
          t("identity.verified_body"));
        syncSubmit();
        await requestOtp();
      } catch (err) {
        id.nidaStep.classList.add("is-failed");
        paintStatus(id.nidaStatus, "critical", `✗ ${t("identity.failed_badge")}`);
        paintResult(id.nidaResult, "critical", fillLengths(err.message));
      } finally {
        setBusy(id.nidaStep, false);
      }
    }

    async function confirmOtp() {
      clearError();
      const code = id.otpInput.value.trim();
      if (!code) { showError(t("identity.otp_not_requested")); id.otpInput.focus(); return; }
      setBusy(id.otpStep, true);
      try {
        const res = await api.post("/identity/otp/verify", {
          verification_id: id.verificationId, code,
        });
        id.phoneVerified = true;
        id.ticket = res.identity_ticket;
        paintStatus(id.otpStatus, "good", `✓ ${t("identity.verified_badge")}`);
        paintResult(id.otpResult, "good", t("identity.verified_title"), null);
        // the code has been spent server-side; do not invite a second try
        id.otpInput.disabled = true;
        id.otpBtn.disabled = true;
        id.otpResend.classList.add("hidden");
        syncSubmit();
      } catch (err) {
        paintStatus(id.otpStatus, "critical", `✗ ${t("identity.failed_badge")}`);
        paintResult(id.otpResult, "critical", fillLengths(err.message));
        id.otpInput.select();
      } finally {
        setBusy(id.otpStep, false);
      }
    }

    /**
     * Ask the server what identity checks exist before showing the section, so
     * the page never offers a method this deployment cannot actually perform.
     */
    async function initIdentity() {
      if (!id.section) { syncSubmit(); return; }
      try {
        const cfg = await api.get("/identity/methods");
        id.required = !!cfg.required;
        id.isSandbox = !!cfg.is_sandbox;
        const method = (cfg.methods || []).find((m) => m.active && m.available);
        if (!method) {
          // nothing usable: say so rather than offering a check that cannot run
          id.section.replaceChildren(
            el("h2", {}, t("identity.title")),
            el("div", { class: "alert error" }, t("identity.provider_unavailable"))
          );
          syncSubmit();
          return;
        }        id.expectedLengths = method.expected_lengths || [];
        id.nidaInput.placeholder = t(method.placeholder_key);
        id.nidaInput.maxLength = Math.max(...id.expectedLengths);
        id.nidaBtn.textContent = t(method.action_key);
        document.getElementById("id-nida-hint").textContent =
          t(method.hint_key, { lengths: lengthsPhrase(id.expectedLengths) });

        applyRoleScope();
      } catch (_) {
        // the section stays visible but inert; the server still refuses an
        // unverified registration, so this fails closed
        id.nidaBtn.disabled = true;
        paintStatus(id.nidaStatus, "critical", t("identity.provider_unavailable"));
        syncSubmit();
      }
    }

    if (id.section) {
      id.nidaBtn.addEventListener("click", verifyNida);
      id.otpBtn.addEventListener("click", confirmOtp);
      id.otpResend.addEventListener("click", requestOtp);
      // only digits in either field
      id.nidaInput.addEventListener("input", () => {
        const cleaned = id.nidaInput.value.replace(/[^\d]/g, "");
        if (cleaned !== id.nidaInput.value) id.nidaInput.value = cleaned;
      });
      id.otpInput.addEventListener("input", () => {
        id.otpInput.value = id.otpInput.value.replace(/[^\d]/g, "");
      });
      initIdentity();
    } else {
      syncSubmit();
    }

    register.addEventListener("submit", (e) => {
      e.preventDefault();
      const f = register;
      if (!f.full_name.value.trim() || !f.phone.value.trim()) return showError(t("error.validation"));
      if (f.password.value.length < 8) return showError(t("password.too_short"));
      if (f.password.value !== f.confirm.value) return showError(`${t("auth.confirm_password")}: ✗`);
      if (roleNeedsId() && !(id.nidaVerified && id.phoneVerified)) {
        return showError(t("identity.verification_required"));
      }
      const body = {
        full_name: f.full_name.value.trim(),
        phone: f.phone.value.trim(),
        password: f.password.value,
        role: f.role.value,
        preferred_language: f.preferred_language.value,
      };
      if (f.email.value.trim()) body.email = f.email.value.trim();
      if (f.role.value === "FARMER") {
        for (const key of ["village", "district", "region"]) {
          if (f[key].value.trim()) body[key] = f[key].value.trim();
        }
      }
      // the proof the server demands in place of a NIDA number
      if (id.ticket) body.identity_ticket = id.ticket;
      submit(register, "/auth/register", body);
    });
  }
})();
