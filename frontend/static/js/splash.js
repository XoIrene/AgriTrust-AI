/* AgriTrust AI splash: shows the photograph, then hands the visitor to sign in.
 *
 * The countdown is a convenience, never a cage: any click, key or scroll stops
 * it, the Skip button is always there, and a visitor who asked for reduced
 * motion is never navigated out from under them.
 */
(function () {
  "use strict";

  var SEEN_KEY = "agritrust.splashSeen";
  var HOLD_MS = 6500;
  var reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  var foot = document.getElementById("splash-foot");
  var fill = document.getElementById("splash-fill");
  var skip = document.getElementById("splash-skip");
  if (!foot || !fill || !skip) return;

  var timer = null;

  /** Members go straight to their dashboard; everyone else is offered sign in. */
  function destination() {
    try {
      return window.AT && window.AT.tokens.get() ? "/dashboard" : "/login";
    } catch (_) {
      return "/login";
    }
  }

  function go() {
    try { sessionStorage.setItem(SEEN_KEY, "1"); } catch (_) { /* private mode */ }
    window.location.href = destination();
  }

  // A second visit in the same tab is a deliberate return, not a first
  // impression, so the screen holds still and the buttons do the talking.
  var seen = false;
  try { seen = sessionStorage.getItem(SEEN_KEY) === "1"; } catch (_) { seen = false; }

  function stop() {
    window.clearTimeout(timer);
    foot.hidden = true;
    window.removeEventListener("pointerdown", stop);
    window.removeEventListener("keydown", stop);
    window.removeEventListener("wheel", stop);
    window.removeEventListener("touchstart", stop);
  }

  skip.addEventListener("click", go);

  if (seen || reduceMotion) return;

  foot.hidden = false;
  // every interaction cancels the hand-off, so nobody is moved while reading
  window.addEventListener("pointerdown", stop, { once: true });
  window.addEventListener("keydown", stop, { once: true });
  window.addEventListener("wheel", stop, { once: true, passive: true });
  window.addEventListener("touchstart", stop, { once: true, passive: true });

  // rAF drives the bar so it tracks real elapsed time, not tick drift
  var started = performance.now();
  (function frame(now) {
    var pct = Math.min(1, (now - started) / HOLD_MS);
    fill.style.width = pct * 100 + "%";
    if (pct < 1) window.requestAnimationFrame(frame);
  })(started);

  timer = window.setTimeout(go, HOLD_MS);
})();