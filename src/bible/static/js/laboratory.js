/* The laboratory pages (/laboratory/*, LABORATORY_DESIGN.md): the forms of the measurement and of the shadow evaluations, and the records of the runs.
 * Each form saves only its part and shows its result next to its button. Each text is a catalog key (ClaireI18n).
 */
(function () {
  "use strict";

  var tr = function (key, params) { return window.ClaireI18n.t(key, params); };
  var POLL_MS = 4000;

  function request(method, url, body) {
    var init = { method: method, credentials: "same-origin", headers: { "Accept": "application/json" } };
    if (body !== undefined) {
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(body);
    }
    return fetch(url, init).then(function (res) {
      return res.json().catch(function () { return null; }).then(function (data) { return { ok: res.ok, status: res.status, data: data }; });
    });
  }

  function show(form, state, text) {
    var out = form.querySelector("[data-lab-result]") || form.querySelector(".result");
    if (!out) return;
    out.setAttribute("data-state", state);
    out.textContent = text;
  }

  function errorText(r) {
    var code = (r && r.data && r.data.error) || ("HTTP " + (r ? r.status : "?"));
    return tr("console.js.admin.laboratory.failed", { error: code });
  }

  // M0: start the run, then ask for its state until it ends; the page then loads again with the record.
  function bindSttHints(form) {
    form.addEventListener("submit", function (ev) {
      ev.preventDefault();
      if (!form.reportValidity()) return;
      var button = form.querySelector("button[type=submit]");
      var hints = String(form.elements.hints.value || "").split(",").map(function (s) { return s.trim(); }).filter(Boolean);
      button.disabled = true;
      show(form, "busy", tr("console.js.admin.laboratory.starting"));
      request("POST", form.getAttribute("data-endpoint"), { url: String(form.elements.url.value || "").trim(), hints: hints }).then(function (r) {
        if (!r.ok) {
          button.disabled = false;
          show(form, "error", errorText(r));
          return;
        }
        var runId = r.data.run.id;
        show(form, "busy", tr("console.js.admin.laboratory.running"));
        var poll = function () {
          request("GET", "/api/admin/laboratory/runs/" + encodeURIComponent(runId)).then(function (s) {
            if (s.ok && s.data && s.data.run && s.data.run.status === "running") {
              window.setTimeout(poll, POLL_MS);
              return;
            }
            window.location.reload();
          });
        };
        window.setTimeout(poll, POLL_MS);
      }).catch(function () {
        button.disabled = false;
        show(form, "error", tr("console.js.admin.laboratory.failed", { error: "network" }));
      });
    });
  }

  // Jev (moved from /admin/providers): PATCH /providers with the jev block only.
  function bindJev(form) {
    form.addEventListener("submit", function (ev) {
      ev.preventDefault();
      var button = form.querySelector("button[type=submit]");
      var jev = { enabled: Boolean(form.querySelector("#lab-jev-enabled").checked), base_url: String(form.elements.base_url.value || "").trim() };
      var key = String(form.elements.api_key.value || "").trim();
      if (key) jev.api_key = key;
      button.disabled = true;
      show(form, "busy", tr("console.js.common.saving"));
      request("PATCH", form.getAttribute("data-endpoint"), { providers: { jev: jev } }).then(function (r) {
        button.disabled = false;
        if (!r.ok) { show(form, "error", errorText(r)); return; }
        form.elements.api_key.value = "";
        var pill = document.querySelector("#lab-jev [data-lab-jev-state]");
        if (pill) {
          pill.setAttribute("data-lab-jev-state", jev.enabled ? "on" : "off");
          pill.setAttribute("data-tone", jev.enabled ? "ok" : "neutral");
          pill.textContent = tr(jev.enabled ? "console.js.admin.laboratory.on" : "console.js.admin.laboratory.off");
        }
        show(form, "ok", tr("console.js.admin.laboratory.saved"));
      });
    });
  }

  // The same-subject source merge (moved from /admin/limits): PATCH /limits with the mode only.
  function bindMerge(form) {
    form.addEventListener("submit", function (ev) {
      ev.preventDefault();
      var button = form.querySelector("button[type=submit]");
      var mode = form.elements.same_subject_merge.value;
      button.disabled = true;
      show(form, "busy", tr("console.js.common.saving"));
      request("PATCH", form.getAttribute("data-endpoint"), { same_subject_merge: mode }).then(function (r) {
        button.disabled = false;
        if (!r.ok) { show(form, "error", errorText(r)); return; }
        var pill = document.querySelector("#lab-merge [data-lab-merge-state]");
        if (pill) {
          pill.setAttribute("data-lab-merge-state", mode);
          pill.setAttribute("data-tone", mode === "shadow" ? "ok" : "neutral");
          pill.textContent = form.elements.same_subject_merge.selectedOptions[0].textContent;
        }
        show(form, "ok", tr("console.js.admin.laboratory.saved"));
      });
    });
  }

  function bindDelete() {
    document.addEventListener("click", function (ev) {
      var btn = ev.target.closest("[data-lab-delete]");
      if (!btn) return;
      if (!window.confirm(tr("console.js.admin.laboratory.delete_confirm"))) return;
      btn.disabled = true;
      request("DELETE", "/api/admin/laboratory/runs/" + encodeURIComponent(btn.getAttribute("data-lab-delete"))).then(function (r) {
        if (r.ok) {
          var run = btn.closest("[data-lab-run]");
          if (run) run.remove();
          return;
        }
        btn.disabled = false;
        var out = btn.parentElement.querySelector(".result");
        if (out) { out.setAttribute("data-state", "error"); out.textContent = errorText(r); }
      });
    });
  }

  // A run that is still running (an earlier page) is followed until it ends.
  function followRunning() {
    var running = document.querySelector('[data-lab-run] [data-lab-status="running"]');
    if (!running) return;
    var runId = running.closest("[data-lab-run]").getAttribute("data-lab-run");
    var poll = function () {
      request("GET", "/api/admin/laboratory/runs/" + encodeURIComponent(runId)).then(function (s) {
        if (s.ok && s.data && s.data.run && s.data.run.status === "running") { window.setTimeout(poll, POLL_MS); return; }
        window.location.reload();
      });
    };
    window.setTimeout(poll, POLL_MS);
  }

  function init() {
    document.querySelectorAll('form[data-lab-form="stt-hints"]').forEach(bindSttHints);
    document.querySelectorAll('form[data-lab-form="jev"]').forEach(bindJev);
    document.querySelectorAll('form[data-lab-form="merge"]').forEach(bindMerge);
    bindDelete();
    followRunning();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
