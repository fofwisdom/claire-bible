// /admin/instance 화면의 서버 등급 절 (설계 §10.3, P4 L4-c).
// CSP 때문에 인라인 스크립트·이벤트 핸들러를 쓰지 않는다. 서버 응답은 textContent로만 화면에 넣는다.
(function () {
  "use strict";

  // Each text of this script is a catalog key (ClaireI18n, static/locales/{en,ko}.json).
  var tr = function (key, params) { return window.ClaireI18n.t(key, params); };

  var ERROR_TEXT = {
    reauth_required: tr("console.js.admin.tier.error.reauth_required"),
    step_up_requires_session: tr("console.js.admin.tier.error.step_up_requires_session"),
    invalid_password: tr("console.js.common.error.invalid_password"),
    too_many_attempts: tr("console.js.common.error.too_many_attempts"),
    forbidden: tr("console.js.admin.error.forbidden"),
    invalid_request: tr("console.js.common.error.invalid_request")
  };
  var INT_FIELDS = [
    "monthly_tokens", "daily_ingests", "storage_mb", "max_themes", "max_documents"
  ];

  function show(out, text, isError) {
    if (!out) return;
    out.textContent = text;
    out.setAttribute("data-state", isError ? "error" : "ok");
  }

  function errorText(status, body) {
    if (body && body.error === "invalid_server_tier" && body.detail) return tr("console.js.admin.tier.invalid", { detail: body.detail });
    if (body && body.error && ERROR_TEXT[body.error]) return ERROR_TEXT[body.error];
    return tr("console.js.common.http_error", { status: status });
  }

  function send(method, url, payload) {
    return fetch(url, {
      method: method,
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    }).then(function (res) {
      return res.json().catch(function () { return null; }).then(function (body) {
        return { ok: res.ok, status: res.status, body: body };
      });
    });
  }

  function intValue(form, name) {
    var raw = form.elements.namedItem(name).value.trim();
    return /^\d+$/.test(raw) ? parseInt(raw, 10) : raw;
  }

  function collect(form) {
    var tier = {
      provider: form.elements.namedItem("provider").value,
      model: form.elements.namedItem("model").value.trim(),
      max_effort: form.elements.namedItem("max_effort").value,
      features: [],
      byok_caps: { max_concurrency: intValue(form, "byok_max_concurrency") }
    };
    INT_FIELDS.forEach(function (name) { tier[name] = intValue(form, name); });
    Array.prototype.forEach.call(form.querySelectorAll("input[name=features]"), function (box) {
      if (box.checked) tier.features.push(box.value);
    });
    return tier;
  }

  function init() {
    var form = document.querySelector("[data-server-tier-form]");
    var reauth = document.querySelector("[data-server-tier-reauth]");
    if (!form) return;
    var out = form.querySelector("[data-server-tier-result]");

    form.addEventListener("submit", function (event) {
      event.preventDefault();
      send("PUT", "/api/admin/server-tier", collect(form)).then(function (r) {
        if (r.ok) {
          show(out, tr("console.js.admin.tier.saved"), false);
          if (reauth) reauth.hidden = true;
          return;
        }
        if (r.body && r.body.error === "reauth_required" && reauth) reauth.hidden = false;
        show(out, errorText(r.status, r.body), true);
      }).catch(function () {
        show(out, tr("console.js.common.network_error"), true);
      });
    });

    if (!reauth) return;
    var reauthOut = reauth.querySelector("[data-server-tier-reauth-result]");
    reauth.addEventListener("submit", function (event) {
      event.preventDefault();
      var field = reauth.elements.namedItem("password");
      send("POST", "/api/auth/reauth", { password: field.value }).then(function (r) {
        field.value = "";
        if (r.ok) {
          show(reauthOut, tr("console.js.admin.tier.reauth_done"), false);
          return;
        }
        show(reauthOut, errorText(r.status, r.body), true);
      }).catch(function () {
        show(reauthOut, tr("console.js.common.network_error"), true);
      });
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
