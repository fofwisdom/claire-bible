// /admin/instance 화면의 서버 등급 절 (설계 §10.3, P4 L4-c).
// CSP 때문에 인라인 스크립트·이벤트 핸들러를 쓰지 않는다. 서버 응답은 textContent로만 화면에 넣는다.
(function () {
  "use strict";

  var ERROR_TEXT = {
    reauth_required: "먼저 본인 확인을 하세요.",
    step_up_requires_session: "로그인 세션에서만 저장할 수 있습니다.",
    invalid_password: "비밀번호가 맞지 않습니다.",
    too_many_attempts: "시도가 너무 많습니다. 잠시 뒤에 다시 시도하세요.",
    forbidden: "요청 출처를 확인하지 못했습니다.",
    invalid_request: "요청 형식이 맞지 않습니다."
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
    if (body && body.error === "invalid_server_tier" && body.detail) return "값이 올바르지 않습니다: " + body.detail;
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
          show(out, "저장했습니다. 준비 검사(G5)는 화면을 새로 고치면 갱신됩니다.", false);
          if (reauth) reauth.hidden = true;
          return;
        }
        if (r.body && r.body.error === "reauth_required" && reauth) reauth.hidden = false;
        show(out, errorText(r.status, r.body), true);
      }).catch(function () {
        show(out, "서버에 연결하지 못했습니다.", true);
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
          show(reauthOut, "본인 확인을 마쳤습니다. 다시 저장하세요.", false);
          return;
        }
        show(reauthOut, errorText(r.status, r.body), true);
      }).catch(function () {
        show(reauthOut, "서버에 연결하지 못했습니다.", true);
      });
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
