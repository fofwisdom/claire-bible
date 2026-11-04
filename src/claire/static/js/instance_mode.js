// /admin/instance 화면의 네트워크 모드 전환 (설계 §4.2, §17.2).
// CSP 제약: 인라인 스크립트 및 인라인 이벤트 핸들러를 사용하지 않는다.
// 동적 텍스트는 textContent 및 DOM 노드로만 안전하게 삽입한다.
(function () {
  "use strict";

  var ERROR_TEXT = {
    reauth_required: "관리자 본인 확인(비밀번호)이 필요합니다.",
    step_up_requires_session: "로그인 세션에서만 모드를 전환할 수 있습니다.",
    invalid_password: "비밀번호가 맞지 않습니다.",
    too_many_attempts: "시도가 너무 많습니다. 잠시 뒤에 다시 시도하세요.",
    forbidden: "요청 출처(Origin)를 확인하지 못했습니다.",
    network_mode_not_available: "현재 릴리스에서는 네트워크 모드 전환이 제한되어 있습니다 (NETWORK_MODE_AVAILABLE=False).",
    multi_theme_required: "네트워크 모드로 전환하려면 CLAIRE_MULTI_THEME=1 설정이 필요합니다.",
    invalid_mode: "유효하지 않은 모드 값입니다."
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
    if (!body) return "요청을 처리하지 못했습니다 (HTTP " + status + ").";
    if (body.error === "readiness_failed") {
      var fails = body.failures || [];
      var msg = "준비 검사를 통과하지 못했습니다: ";
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
      return "일반 멤버가 등록되어 있어 싱글 모드로 전환할 수 없습니다 (대상: " + (mNames || "멤버 존재") + ").";
    }
    if (body.error && ERROR_TEXT[body.error]) {
      return ERROR_TEXT[body.error];
    }
    if (body.detail) {
      return body.detail;
    }
    return "요청을 처리하지 못했습니다 (HTTP " + status + ").";
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
      show(out, "모드 전환을 처리 중입니다...", false);

      return send("POST", "/api/admin/instance/mode", { mode: targetMode }).then(function (r) {
        if (r.ok) {
          var modeLabel = targetMode === "network" ? "네트워크 모드" : "싱글 모드";
          show(out, modeLabel + "로 성공적으로 전환되었습니다. 화면을 새로 고칩니다...", false);
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
        show(out, "서버와의 통신에 실패했습니다.", true);
      });
    }

    form.addEventListener("submit", function (event) {
      event.preventDefault();
      if (confirmCheckbox && !confirmCheckbox.checked) {
        show(out, "전환 주의사항 확인 체크박스에 체크해 주세요.", true);
        return;
      }
      var targetMode = targetModeInput ? targetModeInput.value : "";
      if (!targetMode) {
        show(out, "전환 대상 모드가 올바르지 않습니다.", true);
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
        show(reauthOut, "비밀번호를 입력하세요.", true);
        return;
      }

      if (reauthBtn) reauthBtn.disabled = true;
      show(reauthOut, "본인 확인 중입니다...", false);

      send("POST", "/api/auth/reauth", { password: password }).then(function (r) {
        if (pwField) pwField.value = "";
        if (reauthBtn) reauthBtn.disabled = false;

        if (r.ok) {
          show(reauthOut, "본인 확인을 완료했습니다. 모드 전환을 실행합니다...", false);
          reauth.hidden = true;
          var targetMode = targetModeInput ? targetModeInput.value : "";
          if (targetMode) {
            executeSwitch(targetMode);
          }
          return;
        }

        var err = (r.body && ERROR_TEXT[r.body.error]) || "본인 확인에 실패했습니다 (HTTP " + r.status + ").";
        show(reauthOut, err, true);
      }).catch(function () {
        if (reauthBtn) reauthBtn.disabled = false;
        show(reauthOut, "서버와의 통신에 실패했습니다.", true);
      });
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
