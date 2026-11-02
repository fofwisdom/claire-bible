// /me 화면의 양식을 JSON API로 보낸다 (설계 §8.7).
// CSP 때문에 인라인 스크립트·이벤트 핸들러를 쓰지 않는다. 서버 응답은 textContent로만 화면에 넣는다.
(function () {
  "use strict";

  var ERROR_TEXT = {
    reauth_required: "먼저 본인 확인을 하세요.",
    step_up_requires_session: "로그인 세션에서만 할 수 있습니다.",
    invalid_password: "비밀번호가 맞지 않습니다.",
    password_not_set: "비밀번호가 아직 없습니다. 비밀번호 변경에서 바로 설정하세요.",
    too_many_attempts: "시도가 너무 많습니다. 잠시 뒤에 다시 시도하세요.",
    invalid_display_name: "표시 이름은 64자 이하이고 제어 문자를 넣을 수 없습니다.",
    account_not_active: "계정 설정을 먼저 마쳐야 합니다.",
    invalid_request: "요청 형식이 맞지 않습니다."
  };

  function show(form, text, isError) {
    var out = form.querySelector("[data-accounts-result]");
    if (!out) return;
    out.textContent = text;
    out.setAttribute("data-state", isError ? "error" : "ok");
  }

  function errorText(status, body) {
    if (body && body.error === "password_rejected" && body.message) return body.message;
    if (body && body.error && ERROR_TEXT[body.error]) return ERROR_TEXT[body.error];
    return "요청을 처리하지 못했습니다 (HTTP " + status + ").";
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

  var handlers = {
    "display-name": function (form) {
      var value = form.elements.namedItem("display_name").value;
      return send("PATCH", "/api/me", { display_name: value }).then(function (r) {
        show(form, r.ok ? "저장했습니다." : errorText(r.status, r.body), !r.ok);
      });
    },
    "reauth": function (form) {
      var field = form.elements.namedItem("password");
      return send("POST", "/api/auth/reauth", { password: field.value }).then(function (r) {
        field.value = "";
        show(form, r.ok ? "본인 확인을 마쳤습니다. 15분 안에 비밀번호를 바꿀 수 있습니다." : errorText(r.status, r.body), !r.ok);
      });
    },
    "password": function (form) {
      var current = form.elements.namedItem("current_password");
      var next = form.elements.namedItem("new_password");
      var confirm = form.elements.namedItem("new_password_confirm");
      if (next.value !== confirm.value) {
        show(form, "새 비밀번호 확인이 일치하지 않습니다.", true);
        return Promise.resolve();
      }
      var payload = { new_password: next.value };
      if (current.value) payload.current_password = current.value;
      return send("POST", "/api/me/password", payload).then(function (r) {
        if (r.ok) {
          current.value = "";
          next.value = "";
          confirm.value = "";
          var n = r.body && typeof r.body.other_sessions_revoked === "number" ? r.body.other_sessions_revoked : 0;
          show(form, "비밀번호를 바꿨습니다. 다른 세션 " + n + "개를 끊었습니다.", false);
        } else {
          show(form, errorText(r.status, r.body), true);
        }
      });
    }
  };

  document.querySelectorAll("form[data-accounts-form]").forEach(function (form) {
    var handler = handlers[form.getAttribute("data-accounts-form")];
    if (!handler) return;
    form.addEventListener("submit", function (event) {
      event.preventDefault();
      var button = form.querySelector("button[type=submit]");
      if (button) button.disabled = true;
      handler(form).catch(function () {
        show(form, "서버에 연결하지 못했습니다.", true);
      }).then(function () {
        if (button) button.disabled = false;
      });
    });
  });
})();
