// /admin/server 화면의 네트워크 모드 전환 (설계 §4.2, §17.2).
// CSP 제약: 인라인 스크립트 및 인라인 이벤트 핸들러를 사용하지 않는다.
// 동적 텍스트는 textContent 및 DOM 노드로만 안전하게 삽입한다.
(function () {
  "use strict";

  // Each text of this script is a catalog key (ClaireI18n, static/locales/{en,ko}.json).
  var tr = function (key, params) { return window.ClaireI18n.t(key, params); };

  var ERROR_TEXT = {
    reauth_required: tr("console.js.admin.mode.error.reauth_required"),
    step_up_requires_session: tr("console.js.admin.mode.error.step_up_requires_session"),
    invalid_password: tr("console.js.common.error.invalid_password"),
    too_many_attempts: tr("console.js.common.error.too_many_attempts"),
    forbidden: tr("console.js.admin.error.forbidden"),
    network_mode_not_available: tr("console.js.admin.mode.error.network_mode_not_available"),
    multi_theme_required: tr("console.js.admin.mode.error.multi_theme_required"),
    invalid_mode: tr("console.js.admin.mode.error.invalid_mode")
  };

  function show(out, text, isError) {
    if (!out) return;
    out.textContent = text;
    out.setAttribute("data-state", isError ? "error" : "ok");
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

  function formatErrorMessage(status, body) {
    if (!body) return tr("console.js.common.http_error", { status: status });
    if (body.error === "readiness_failed") {
      var fails = body.failures || [];
      var msg = tr("console.js.admin.mode.readiness_failed") + " ";
      if (fails.length > 0) {
        msg += fails.map(function (f) { return f.id + "(" + (f.detail || f.name) + ")"; }).join(", ");
      } else if (body.detail) {
        msg += body.detail;
      }
      return msg;
    }
    if (body.error === "members_exist") {
      var members = body.members || [];
      var mNames = members.map(function (m) { return m.username || "(ID:" + m.id + ")"; }).join(", ");
      return tr("console.js.admin.mode.members_exist", { members: mNames || tr("console.js.admin.mode.members_exist_fallback") });
    }
    if (body.error && ERROR_TEXT[body.error]) {
      return ERROR_TEXT[body.error];
    }
    if (body.detail) {
      return body.detail;
    }
    return tr("console.js.common.http_error", { status: status });
  }

  function init() {
    var form = document.querySelector("[data-mode-switch-form]");
    var reauth = document.querySelector("[data-mode-switch-reauth]");
    if (!form) return;

    var out = form.querySelector("[data-mode-switch-result]");
    var submitBtn = form.querySelector("button[type=submit]");
    var targetModeInput = form.elements.namedItem("target_mode");
    var confirmCheckbox = form.elements.namedItem("confirm");

    function executeSwitch(targetMode) {
      if (submitBtn) submitBtn.disabled = true;
      show(out, tr("console.js.admin.mode.in_work"), false);

      return send("POST", "/api/admin/server/mode", { mode: targetMode }).then(function (r) {
        if (r.ok) {
          show(out, tr(targetMode === "network" ? "console.js.admin.mode.switched_network" : "console.js.admin.mode.switched_single"), false);
          if (reauth) reauth.hidden = true;
          setTimeout(function () {
            window.location.reload();
          }, 900);
          return;
        }

        if (submitBtn) submitBtn.disabled = false;

        if (r.body && r.body.error === "reauth_required") {
          if (reauth) {
            reauth.hidden = false;
            var pwInput = reauth.elements.namedItem("password");
            if (pwInput) pwInput.focus();
          }
          show(out, ERROR_TEXT.reauth_required, true);
          return;
        }

        show(out, formatErrorMessage(r.status, r.body), true);
      }).catch(function () {
        if (submitBtn) submitBtn.disabled = false;
        show(out, tr("console.js.common.network_error"), true);
      });
    }

    form.addEventListener("submit", function (event) {
      event.preventDefault();
      if (confirmCheckbox && !confirmCheckbox.checked) {
        show(out, tr("console.js.admin.mode.confirm_needed"), true);
        return;
      }
      var targetMode = targetModeInput ? targetModeInput.value : "";
      if (!targetMode) {
        show(out, tr("console.js.admin.mode.bad_target"), true);
        return;
      }
      executeSwitch(targetMode);
    });

    if (!reauth) return;
    var reauthOut = reauth.querySelector("[data-mode-switch-reauth-result]");
    var reauthBtn = reauth.querySelector("button[type=submit]");

    reauth.addEventListener("submit", function (event) {
      event.preventDefault();
      var pwField = reauth.elements.namedItem("password");
      var password = pwField ? pwField.value : "";
      if (!password) {
        show(reauthOut, tr("console.js.admin.mode.password_needed"), true);
        return;
      }

      if (reauthBtn) reauthBtn.disabled = true;
      show(reauthOut, tr("console.js.admin.mode.reauth_in_work"), false);

      send("POST", "/api/auth/reauth", { password: password }).then(function (r) {
        if (pwField) pwField.value = "";
        if (reauthBtn) reauthBtn.disabled = false;

        if (r.ok) {
          show(reauthOut, tr("console.js.admin.mode.reauth_done"), false);
          reauth.hidden = true;
          var targetMode = targetModeInput ? targetModeInput.value : "";
          if (targetMode) {
            executeSwitch(targetMode);
          }
          return;
        }

        var err = (r.body && ERROR_TEXT[r.body.error]) || tr("console.js.admin.mode.reauth_failed", { status: r.status });
        show(reauthOut, err, true);
      }).catch(function () {
        if (reauthBtn) reauthBtn.disabled = false;
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
