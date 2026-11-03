// /me 화면의 양식을 JSON API로 보낸다 (설계 §8.7, 키 절은 §11).
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
    invalid_request: "요청 형식이 맞지 않습니다.",
    admin_uses_provider_settings: "관리자는 프로바이더 설정 화면에서 키를 관리합니다.",
    unsupported_provider: "이 프로바이더의 키는 등록할 수 없습니다.",
    invalid_api_key: "API 키 형식이 맞지 않습니다. 공백 없이 붙여 넣으세요.",
    key_not_found: "등록한 키가 없습니다.",
    key_unreadable: "저장한 키를 읽지 못했습니다. 키를 다시 등록하세요."
  };

  var KEY_STATUS_TEXT = { active: "사용 중", rejected: "거부됨", unverified: "확인 전" };
  var KEY_PROVIDER_TEXT = { gemini: "Gemini" };

  function show(form, text, isError) {
    var out = form.querySelector("[data-accounts-result]");
    if (!out) return;
    out.textContent = text;
    out.setAttribute("data-state", isError ? "error" : "ok");
  }

  function errorText(status, body) {
    if (body && body.error === "password_rejected" && body.message) return body.message;
    if (body && body.error === "key_verification_unavailable") {
      return "지금은 키를 확인할 수 없습니다(네트워크·프로바이더 일시 장애). 키는 바뀌지 않았습니다. 잠시 뒤에 다시 시도하세요.";
    }
    if (body && body.error === "key_verification_failed") {
      return "키 확인에 실패해 저장하지 않았습니다." + (body.message ? " " + body.message : "");
    }
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

  function formatTs(value) {
    if (typeof value !== "number") return "-";
    return new Date(value * 1000).toISOString().replace("T", " ").replace(/\.\d+Z$/, " UTC");
  }

  // 키 메타데이터(§11.2)만 다시 그린다. 서버는 키 원문·일부 문자를 돌려주지 않는다.
  function renderKeyMeta(keys) {
    var box = document.querySelector("[data-provider-key-meta]");
    if (!box) return;
    while (box.firstChild) box.removeChild(box.firstChild);
    if (!keys || !keys.length) {
      var empty = document.createElement("p");
      empty.className = "hint";
      empty.textContent = "등록한 키가 없습니다.";
      box.appendChild(empty);
      return;
    }
    keys.forEach(function (k) {
      var dl = document.createElement("dl");
      [
        ["프로바이더", KEY_PROVIDER_TEXT[k.provider] || k.provider],
        ["fingerprint", k.fingerprint],
        ["상태", KEY_STATUS_TEXT[k.status] || k.status],
        ["등록", formatTs(k.created_at)],
        ["마지막 확인", formatTs(k.last_verified_at)],
        ["마지막 사용", formatTs(k.last_used_at)]
      ].forEach(function (row) {
        var dt = document.createElement("dt");
        dt.textContent = row[0];
        var dd = document.createElement("dd");
        dd.textContent = row[1];
        dl.appendChild(dt);
        dl.appendChild(dd);
      });
      box.appendChild(dl);
    });
  }

  function refreshKeys() {
    return fetch("/api/me/keys", { credentials: "same-origin" }).then(function (res) {
      return res.ok ? res.json() : null;
    }).then(function (body) {
      if (body && body.keys) renderKeyMeta(body.keys);
    }).catch(function () {});
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
    },
    "provider-key-put": function (form) {
      var provider = form.elements.namedItem("provider").value;
      var field = form.elements.namedItem("api_key");
      var value = field.value;
      field.value = "";
      return send("PUT", "/api/me/keys", { provider: provider, api_key: value }).then(function (r) {
        value = "";
        show(form, r.ok ? "키를 확인하고 저장했습니다." : errorText(r.status, r.body), !r.ok);
        if (r.ok) return refreshKeys();
      });
    },
    "provider-key-verify": function (form) {
      var provider = form.elements.namedItem("provider").value;
      return send("POST", "/api/me/keys/verify", { provider: provider }).then(function (r) {
        if (r.ok && r.body) {
          show(form, (r.body.ok ? "키가 유효합니다. " : "키가 거부되었습니다. ") + (r.body.message || ""), !r.body.ok);
          return refreshKeys();
        }
        show(form, errorText(r.status, r.body), true);
      });
    },
    "provider-key-delete": function (form) {
      if (!window.confirm("등록한 API 키를 삭제할까요?")) return Promise.resolve();
      return fetch("/api/me/keys", { method: "DELETE", credentials: "same-origin" }).then(function (res) {
        return res.json().catch(function () { return null; }).then(function (body) {
          if (res.ok) {
            show(form, "키를 삭제했습니다.", false);
            return refreshKeys();
          }
          show(form, errorText(res.status, body), true);
        });
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
