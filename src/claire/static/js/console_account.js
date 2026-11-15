/* Claire user console controller for /settings Group 1 (account, federation, keys, themes, tokens). Design P3-a.
 * Calls only existing APIs: /api/me, /api/me/password, /api/auth/reauth, /api/me/identities*, POST /graph/bundle, /api/me/keys*, /api/federation/status, /api/federation/followers, /themes, /tokens.
 * Server text goes into the DOM only through textContent. No inline handlers. No /admin/* calls.
 */
(function () {
  "use strict";

  var ERROR_TEXT = {
    reauth_required: "먼저 이 화면의 '본인 확인'을 마치고 다시 시도하세요.",
    step_up_requires_session: "로그인 세션에서만 할 수 있습니다. API 토큰으로는 할 수 없습니다.",
    invalid_password: "비밀번호가 맞지 않습니다.",
    password_not_set: "비밀번호가 아직 없습니다. 비밀번호 설정을 바로 하세요.",
    too_many_attempts: "시도가 너무 많습니다. 잠시 뒤에 다시 시도하세요.",
    invalid_display_name: "표시 이름은 64자 이하이고 제어 문자를 넣을 수 없습니다.",
    account_not_active: "계정이 활성 상태가 아닙니다.",
    invalid_request: "요청 형식이 맞지 않습니다.",
    admin_uses_provider_settings: "관리자는 프로바이더 설정 화면에서 키를 관리합니다.",
    unsupported_provider: "이 프로바이더의 키는 등록할 수 없습니다.",
    invalid_api_key: "API 키 형식이 맞지 않습니다. 공백 없이 붙여 넣으세요.",
    key_not_found: "등록한 키가 없습니다.",
    key_unreadable: "저장한 키를 읽지 못했습니다. 키를 다시 등록하세요.",
    model_below_admin_baseline: "관리자가 제공하는 기준 모델·추론 수준보다 낮은 설정은 선택할 수 없습니다.",
    follower_not_found: "이미 목록에 없는 팔로워입니다.",
    cannot_unlink_last_credential: "마지막 로그인 수단은 해제할 수 없습니다. 먼저 비밀번호를 설정하거나 다른 외부 로그인을 연결하세요.",
    unauthorized: "로그인이 필요합니다."
  };

  var KEY_STATUS_TEXT = { active: "사용 중", rejected: "거부됨", unverified: "확인 전" };
  var KEY_PROVIDER_TEXT = { gemini: "Google AI Studio" };

  function parseBootstrap() {
    var el = document.getElementById("console-bootstrap");
    if (!el || !el.textContent) return null;
    try {
      return JSON.parse(el.textContent);
    } catch (_err) {
      return null;
    }
  }

  function formatTs(value) {
    if (typeof value !== "number" || !isFinite(value)) return "-";
    return new Date(value * 1000).toISOString().replace("T", " ").replace(/\.\d+Z$/, " UTC");
  }

  function resultEl(scope) {
    return scope ? scope.querySelector("[data-settings-result]") : null;
  }

  function show(scope, text, isError) {
    var out = resultEl(scope);
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
    if (body && typeof body.error === "string" && ERROR_TEXT[body.error]) return ERROR_TEXT[body.error];
    if (body && typeof body.detail === "string" && body.detail) return body.detail + " (HTTP " + status + ")";
    return "요청을 처리하지 못했습니다 (HTTP " + status + ").";
  }

  function focusReauth() {
    var form = document.querySelector("form[data-settings-form='reauth']");
    if (!form) return;
    var field = form.querySelector("input[name='password']");
    if (field) field.focus();
  }

  function request(method, url, payload) {
    var init = { method: method, credentials: "same-origin", headers: { "Accept": "application/json" } };
    if (payload !== undefined) {
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(payload);
    }
    return fetch(url, init).then(function (res) {
      return res.json().catch(function () { return null; }).then(function (body) {
        if (res.status === 401 && body && body.error === "reauth_required") focusReauth();
        return { ok: res.ok, status: res.status, body: body };
      });
    });
  }

  // --- /settings/keys: metadata only. The server never returns key text or partial characters. ---

  function renderKeyMeta(keys) {
    var box = document.querySelector("[data-provider-key-meta]");
    if (!box) return;
    while (box.firstChild) box.removeChild(box.firstChild);
    if (!keys || !keys.length) {
      var empty = document.createElement("p");
      empty.className = "hint";
      empty.setAttribute("data-provider-key-empty", "");
      empty.textContent = "등록한 키가 없습니다.";
      box.appendChild(empty);
      return;
    }
    keys.forEach(function (k) {
      var dl = document.createElement("dl");
      dl.setAttribute("data-provider-key", String(k.provider));
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
        dd.textContent = row[1] == null ? "-" : String(row[1]);
        dl.appendChild(dt);
        dl.appendChild(dd);
      });
      box.appendChild(dl);
    });
  }

  function renderModelPolicy(policy) {
    if (!policy) return;
    var baseline = policy.admin_baseline || {};
    var ranks = policy.model_ranks || {};
    var effortOrder = Array.isArray(policy.effort_order) ? policy.effort_order : ["none", "minimal", "low", "medium", "high"];
    var prefs = policy.model_prefs || {};
    var summaryEl = document.querySelector("[data-byok-policy-summary]");
    if (summaryEl) {
      var bMod = baseline.model || "기본";
      var bEff = baseline.effort || "medium";
      var sealText = policy.has_cookie_seal ? "브라우저 봉인 쿠키 활성" : "키 저장 시 봉인 쿠키 자동 발급";
      summaryEl.textContent = "관리자 기준선: " + bMod + " (" + bEff + ") 이상만 허용 · Split-Key 상태: " + sealText;
    }
    var modelSel = document.querySelector("[data-byok-model-select]");
    if (modelSel) {
      while (modelSel.firstChild) modelSel.removeChild(modelSel.firstChild);
      var defOpt = document.createElement("option");
      defOpt.value = "";
      defOpt.textContent = "관리자 기본 모델 이상 자동 적용 (" + (baseline.model || "-") + ")";
      modelSel.appendChild(defOpt);
      var baseRank = typeof ranks[baseline.model] === "number" ? ranks[baseline.model] : 0;
      (Array.isArray(policy.allowed_models) ? policy.allowed_models : []).forEach(function (m) {
        if (!m || m === "mock") return;
        var opt = document.createElement("option");
        opt.value = String(m);
        var mRank = typeof ranks[m] === "number" ? ranks[m] : 0;
        var allowed = mRank >= baseRank || baseRank === 0;
        opt.textContent = allowed ? String(m) : String(m) + " (기준선 미만 · 잠금)";
        if (!allowed) opt.disabled = true;
        if (prefs.model && prefs.model === m && allowed) opt.selected = true;
        modelSel.appendChild(opt);
      });
    }
    var effortSel = document.querySelector("[data-byok-effort-select]");
    if (effortSel) {
      var baseEffIdx = effortOrder.indexOf(String(baseline.effort || "medium").toLowerCase());
      Array.prototype.forEach.call(effortSel.options, function (opt) {
        if (!opt.value) {
          opt.disabled = false;
          return;
        }
        var idx = effortOrder.indexOf(opt.value.toLowerCase());
        opt.disabled = baseEffIdx >= 0 && idx >= 0 && idx < baseEffIdx;
      });
      if (prefs.effort) effortSel.value = prefs.effort;
    }
  }

  function refreshKeys() {
    return request("GET", "/api/me/keys?include_policy=1").then(function (r) {
      if (r.ok && r.body) {
        if (Array.isArray(r.body.keys)) renderKeyMeta(r.body.keys);
        if (r.body.model_policy) renderModelPolicy(r.body.model_policy);
      }
    });
  }

  // --- /settings/account: linked identities ---

  function identitiesBox() {
    return document.querySelector("[data-settings-identities]");
  }

  function renderIdentities(items, providerNames) {
    var tbody = document.querySelector("[data-identities-tbody]");
    if (!tbody) return;
    while (tbody.firstChild) tbody.removeChild(tbody.firstChild);
    if (!items.length) {
      var tr0 = document.createElement("tr");
      var td0 = document.createElement("td");
      td0.colSpan = 4;
      td0.style.color = "var(--muted)";
      td0.style.textAlign = "center";
      td0.textContent = "연결된 외부 로그인이 없습니다.";
      tr0.appendChild(td0);
      tbody.appendChild(tr0);
      return;
    }
    items.forEach(function (ident) {
      var tr = document.createElement("tr");
      var name = providerNames[ident.provider_id] || ident.provider_id;
      [String(name), formatTs(ident.linked_at), formatTs(ident.last_login_at)].forEach(function (text) {
        var td = document.createElement("td");
        td.textContent = text;
        tr.appendChild(td);
      });
      var tdBtn = document.createElement("td");
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "pref-btn pref-btn-danger";
      btn.textContent = "해제";
      btn.setAttribute("data-identity-unlink", String(ident.provider_id));
      btn.setAttribute("data-identity-sub", String(ident.sub));
      tdBtn.appendChild(btn);
      tr.appendChild(tdBtn);
      tbody.appendChild(tr);
    });
  }

  function loadIdentities(providerNames) {
    var box = identitiesBox();
    if (!box) return Promise.resolve();
    return request("GET", "/api/me/identities").then(function (r) {
      if (r.ok && Array.isArray(r.body)) {
        renderIdentities(r.body, providerNames);
      } else {
        show(box, "연결 목록을 불러오지 못했습니다. " + errorText(r.status, r.body), true);
      }
    });
  }

  function bindIdentities(bootstrapExtra) {
    var box = identitiesBox();
    if (!box) return;
    var providerNames = {};
    (bootstrapExtra.identity_providers || []).forEach(function (p) {
      providerNames[p.id] = p.display_name;
    });
    loadIdentities(providerNames).catch(function () {
      show(box, "서버에 연결하지 못했습니다.", true);
    });
    box.addEventListener("click", function (ev) {
      var linkBtn = ev.target.closest("[data-identity-link]");
      if (linkBtn) {
        var pid = linkBtn.getAttribute("data-identity-link") || "";
        linkBtn.disabled = true;
        request("POST", "/api/me/identities/oidc/" + encodeURIComponent(pid) + "/start").then(function (r) {
          var target = r.ok && r.body ? r.body.authorization_url : null;
          if (typeof target === "string" && /^https?:\/\//i.test(target)) {
            window.location.assign(target);
            return;
          }
          show(box, r.ok ? "공급자가 올바른 인증 주소를 돌려주지 않았습니다." : errorText(r.status, r.body), true);
        }).catch(function () {
          show(box, "서버에 연결하지 못했습니다.", true);
        }).then(function () {
          linkBtn.disabled = false;
        });
        return;
      }
      var unlinkBtn = ev.target.closest("[data-identity-unlink]");
      if (unlinkBtn) {
        var providerId = unlinkBtn.getAttribute("data-identity-unlink") || "";
        var sub = unlinkBtn.getAttribute("data-identity-sub") || "";
        if (!window.confirm("이 외부 로그인 연결을 해제할까요?")) return;
        unlinkBtn.disabled = true;
        var url = "/api/me/identities/oidc/" + encodeURIComponent(providerId) + "?sub=" + encodeURIComponent(sub);
        request("DELETE", url).then(function (r) {
          if (r.ok) {
            show(box, "연결을 해제했습니다.", false);
            return loadIdentities(providerNames);
          }
          show(box, errorText(r.status, r.body), true);
        }).catch(function () {
          show(box, "서버에 연결하지 못했습니다.", true);
        }).then(function () {
          unlinkBtn.disabled = false;
        });
      }
    });
  }

  // --- /settings/federation: actor state and followers (Fedify sidecar design Sections 9.2 and 11) ---

  // The server has the same table (console_federation_panels.ACTOR_STATE_TEXT).
  var ACTOR_STATE_TEXT = {
    active: "연합 중",
    account_not_active: "일시 중지 · 계정이 활성 상태가 아닙니다",
    no_username: "일시 중지 · USERNAME이 아직 없습니다",
    federation_off: "꺼짐 · 인스턴스 운영자가 연합을 켜지 않았습니다",
    profile_not_public: "일시 중지 · 프로필이 비공개입니다"
  };

  function setFederationField(name, text) {
    var dd = document.querySelector("[data-federation-field='" + name + "']");
    if (dd) dd.textContent = text;
  }

  function renderFederationStatus(status) {
    setFederationField("handle", status.handle || "(USERNAME 설정 전)");
    setFederationField("actor_id", status.actor_id || "-");
    setFederationField("actor_state", ACTOR_STATE_TEXT[status.actor_state] || String(status.actor_state));
    if (status.followers && typeof status.followers.total === "number") {
      setFederationField("followers_total", status.followers.total.toLocaleString("en-US"));
    }
    var card = document.getElementById("settings-federation-status");
    if (card) card.setAttribute("data-actor-state", String(status.actor_state));
  }

  function followerRow(item) {
    var tr = document.createElement("tr");
    tr.setAttribute("data-follower", String(item.id));
    var tdActor = document.createElement("td");
    var code = document.createElement("code");
    code.className = "pref-mono";
    code.textContent = String(item.actor_iri);
    tdActor.appendChild(code);
    var tdTime = document.createElement("td");
    tdTime.textContent = formatTs(item.created_at);
    var tdBtn = document.createElement("td");
    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "pref-btn pref-btn-danger";
    btn.textContent = "제거";
    btn.setAttribute("data-follower-remove", String(item.actor_iri));
    tdBtn.appendChild(btn);
    [tdActor, tdTime, tdBtn].forEach(function (td) { tr.appendChild(td); });
    return tr;
  }

  // Retention of the received posts of this account (FEDERATION_REMOTE_READING_DESIGN.md Section 10, console fields of unit FP4-5). An empty choice is the instance value.
  function bindFederationRetention() {
    var card = document.querySelector("[data-federation-reading]");
    if (!card) return;
    var form = card.querySelector("[data-retention-form]");
    var result = card.querySelector("[data-retention-result]");
    var useInstance = form.elements.use_instance;
    var days = form.elements.retention_days;
    var label = card.querySelector("[data-retention-instance-label]");

    function say(text, isError) {
      result.textContent = text;
      result.setAttribute("data-state", isError ? "error" : "ok");
    }

    function render(settings) {
      var instance = Number(settings.instance_retention_days);
      label.textContent = String(form.getAttribute("data-msg-instance")).replace("{days}", String(instance));
      days.max = String(instance);
      useInstance.checked = settings.retention_days == null;
      days.value = String(settings.retention_days == null ? instance : settings.retention_days);
      days.disabled = useInstance.checked;
    }

    useInstance.addEventListener("change", function () { days.disabled = useInstance.checked; });
    form.addEventListener("submit", function (ev) {
      ev.preventDefault();
      var value = useInstance.checked ? null : Number(days.value);
      request("PUT", "/api/federation/settings", { retention_days: value }).then(function (r) {
        if (!r.ok || !r.body) {
          say(form.getAttribute("data-msg-failed") + (r.body && typeof r.body.detail === "string" ? " " + r.body.detail : ""), true);
          return;
        }
        render(r.body);
        say(form.getAttribute("data-msg-saved"), false);
      }).catch(function () { say(form.getAttribute("data-msg-failed"), true); });
    });
    request("GET", "/api/federation/settings").then(function (r) {
      if (r.ok && r.body) render(r.body);
    }).catch(function () { say(form.getAttribute("data-msg-failed"), true); });
  }

  function bindFederation() {
    bindFederationRetention();
    var box = document.querySelector("[data-federation-followers]");
    if (!box) return;
    var tbody = box.querySelector("[data-followers-tbody]");
    var more = box.querySelector("[data-followers-more]");
    var nextMaxId = null;

    function showEmptyRow() {
      var tr = document.createElement("tr");
      tr.setAttribute("data-followers-empty", "");
      var td = document.createElement("td");
      td.colSpan = 3;
      td.style.color = "var(--muted)";
      td.style.textAlign = "center";
      td.textContent = "팔로워가 없습니다.";
      tr.appendChild(td);
      tbody.appendChild(tr);
    }

    // Gets one page. Without a cursor the list starts again from the newest follower.
    function loadPage(maxId) {
      var url = "/api/federation/followers" + (maxId ? "?max_id=" + encodeURIComponent(maxId) : "");
      return request("GET", url).then(function (r) {
        if (!r.ok || !r.body || !Array.isArray(r.body.items)) {
          show(box, "팔로워 목록을 불러오지 못했습니다. " + errorText(r.status, r.body), true);
          return;
        }
        if (!maxId) {
          while (tbody.firstChild) tbody.removeChild(tbody.firstChild);
          if (!r.body.items.length) showEmptyRow();
        }
        r.body.items.forEach(function (item) { tbody.appendChild(followerRow(item)); });
        nextMaxId = typeof r.body.next_max_id === "string" ? r.body.next_max_id : null;
        if (more) more.hidden = !nextMaxId;
        if (typeof r.body.total === "number") setFederationField("followers_total", r.body.total.toLocaleString("en-US"));
      });
    }

    function refreshStatus() {
      return request("GET", "/api/federation/status").then(function (r) {
        if (r.ok && r.body) renderFederationStatus(r.body);
      });
    }

    loadPage(null).catch(function () {
      show(box, "서버에 연결하지 못했습니다.", true);
    });
    if (more) {
      more.addEventListener("click", function () {
        if (!nextMaxId) return;
        more.disabled = true;
        loadPage(nextMaxId).catch(function () {
          show(box, "서버에 연결하지 못했습니다.", true);
        }).then(function () {
          more.disabled = false;
        });
      });
    }
    box.addEventListener("click", function (ev) {
      var btn = ev.target.closest("[data-follower-remove]");
      if (!btn) return;
      var actorIri = btn.getAttribute("data-follower-remove") || "";
      if (!window.confirm("이 팔로워를 제거할까요?")) return;
      btn.disabled = true;
      request("DELETE", "/api/federation/followers", { actor_iri: actorIri }).then(function (r) {
        // An unknown follower means that the row left the list in the meantime. The list is read again in the two cases.
        if (r.ok || (r.body && r.body.error === "follower_not_found")) {
          show(box, r.ok ? "팔로워를 제거했습니다." : errorText(r.status, r.body), !r.ok);
          return loadPage(null).then(refreshStatus);
        }
        show(box, errorText(r.status, r.body), true);
        btn.disabled = false;
      }).catch(function () {
        show(box, "서버에 연결하지 못했습니다.", true);
        btn.disabled = false;
      });
    });
  }

  // --- /settings/account: graph bundle output ---

  function renderBundle(section, info) {
    var out = section.querySelector("[data-bundle-output]");
    if (!out) return;
    while (out.firstChild) out.removeChild(out.firstChild);
    var dl = document.createElement("dl");
    [
      ["파일", info.filename],
      ["엔티티", info.entities_count],
      ["관계", info.relations_count],
      ["문서", info.documents_count],
      ["크기 (바이트)", info.size_bytes],
      ["만료", info.expires_at]
    ].forEach(function (row) {
      var dt = document.createElement("dt");
      dt.textContent = row[0];
      var dd = document.createElement("dd");
      dd.textContent = row[1] == null ? "-" : String(row[1]);
      dl.appendChild(dt);
      dl.appendChild(dd);
    });
    out.appendChild(dl);
    if (typeof info.download_url === "string" && /^https?:\/\//i.test(info.download_url)) {
      var a = document.createElement("a");
      a.className = "pref-btn pref-btn-accent";
      a.href = info.download_url;
      a.rel = "noopener";
      var mark = document.createElement("span");
      mark.setAttribute("data-icon", "download");
      a.appendChild(mark);
      a.appendChild(document.createTextNode(" 번들 내려받기"));
      out.appendChild(a);
    }
  }

  // --- form handlers ---

  var handlers = {
    "display-name": function (form) {
      var value = form.elements.namedItem("display_name").value;
      return request("PATCH", "/api/me", { display_name: value }).then(function (r) {
        show(form, r.ok ? "저장했습니다." : errorText(r.status, r.body), !r.ok);
        if (r.ok && r.body) {
          var dd = document.querySelector("[data-account-field='display_name']");
          if (dd) dd.textContent = String(r.body.display_name);
        }
      });
    },
    "reauth": function (form) {
      var field = form.elements.namedItem("password");
      var value = field.value;
      field.value = "";
      return request("POST", "/api/auth/reauth", { password: value }).then(function (r) {
        value = "";
        show(form, r.ok ? "본인 확인을 마쳤습니다. 15분 동안 민감한 작업을 할 수 있습니다." : errorText(r.status, r.body), !r.ok);
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
      if (current && current.value) payload.current_password = current.value;
      return request("POST", "/api/me/password", payload).then(function (r) {
        if (current) current.value = "";
        next.value = "";
        confirm.value = "";
        if (r.ok) {
          var n = r.body && typeof r.body.other_sessions_revoked === "number" ? r.body.other_sessions_revoked : 0;
          show(form, "비밀번호를 저장했습니다. 다른 세션 " + n + "개를 끊었습니다.", false);
          var dd = document.querySelector("[data-account-field='password']");
          if (dd) dd.textContent = "설정됨";
        } else {
          show(form, errorText(r.status, r.body), true);
        }
      });
    },
    "bundle-export": function (form) {
      var section = form.closest("section") || form;
      var theme = form.elements.namedItem("theme").value;
      if (!theme) {
        show(section, "내보낼 테마를 고르세요.", true);
        return Promise.resolve();
      }
      show(section, "그래프 번들을 만드는 중입니다...", false);
      return request("POST", "/graph/bundle", { theme: theme }).then(function (r) {
        if (r.ok && r.body) {
          show(section, "그래프 번들을 만들었습니다.", false);
          renderBundle(section, r.body);
        } else {
          show(section, errorText(r.status, r.body), true);
        }
      });
    },
    "provider-key-put": function (form) {
      var provider = form.elements.namedItem("provider").value;
      var field = form.elements.namedItem("api_key");
      var modelEl = form.elements.namedItem("model");
      var effortEl = form.elements.namedItem("effort");
      var value = field.value;
      field.value = "";
      var payload = { provider: provider, api_key: value };
      if (modelEl && modelEl.value) payload.model = modelEl.value;
      if (effortEl && effortEl.value) payload.effort = effortEl.value;
      return request("PUT", "/api/me/keys", payload).then(function (r) {
        value = "";
        show(form, r.ok ? "키를 확인하고 저장했습니다." : errorText(r.status, r.body), !r.ok);
        if (r.ok) return refreshKeys();
      });
    },
    "provider-key-verify": function (form) {
      var provider = form.elements.namedItem("provider").value;
      return request("POST", "/api/me/keys/verify", { provider: provider }).then(function (r) {
        if (r.ok && r.body) {
          show(form, (r.body.ok ? "키가 유효합니다. " : "키가 거부되었습니다. ") + (r.body.message || ""), !r.body.ok);
          return refreshKeys();
        }
        show(form, errorText(r.status, r.body), true);
      });
    },
    "provider-key-delete": function (form) {
      var provider = form.elements.namedItem("provider").value;
      if (!window.confirm("등록한 API 키를 삭제할까요?")) return Promise.resolve();
      return request("DELETE", "/api/me/keys?provider=" + encodeURIComponent(provider)).then(function (r) {
        if (r.ok) {
          show(form, "키를 삭제했습니다.", false);
          return refreshKeys();
        }
        show(form, errorText(r.status, r.body), true);
      });
    }
  };

  function bindForms() {
    var forms = document.querySelectorAll("form[data-settings-form]");
    Array.prototype.forEach.call(forms, function (form) {
      var handler = handlers[form.getAttribute("data-settings-form")];
      if (!handler) return;
      form.addEventListener("submit", function (event) {
        event.preventDefault();
        var button = form.querySelector("button[type=submit]");
        if (button) button.disabled = true;
        handler(form).catch(function () {
          var scope = form.getAttribute("data-settings-form") === "bundle-export" ? (form.closest("section") || form) : form;
          show(scope, "서버에 연결하지 못했습니다.", true);
        }).then(function () {
          if (button) button.disabled = false;
        });
      });
    });
  }

  // --- Step-up dialog (Fedify sidecar design Section 8.4). With federation on, the server refuses the profile_public change 0 -> 1 without recent authentication. ---

  // Resolves to true after POST /api/auth/reauth succeeds, and to false when the user closes the dialog.
  function openStepUpDialog() {
    return new Promise(function (resolve) {
      var dialog = document.createElement("dialog");
      dialog.className = "console-step-up";
      dialog.setAttribute("data-step-up-dialog", "");
      dialog.setAttribute("aria-labelledby", "step-up-title");
      var form = document.createElement("form");
      form.setAttribute("method", "dialog");
      var title = document.createElement("h2");
      title.id = "step-up-title";
      title.textContent = "본인 확인";
      var text = document.createElement("p");
      text.className = "hint";
      text.textContent = "프로필을 공개하면 다른 서버에서 이 계정을 찾고 팔로우할 수 있습니다. 계속하려면 현재 비밀번호를 입력하세요.";
      var label = document.createElement("label");
      label.textContent = "현재 비밀번호";
      var field = document.createElement("input");
      field.type = "password";
      field.name = "password";
      field.className = "pref-input";
      field.required = true;
      field.autocomplete = "current-password";
      label.appendChild(field);
      var result = document.createElement("p");
      result.className = "result";
      result.setAttribute("data-settings-result", "");
      result.setAttribute("role", "status");
      result.setAttribute("aria-live", "polite");
      var confirm = document.createElement("button");
      confirm.type = "submit";
      confirm.className = "pref-btn pref-btn-accent";
      confirm.setAttribute("data-step-up-confirm", "");
      confirm.textContent = "확인하고 공개";
      var cancel = document.createElement("button");
      cancel.type = "button";
      cancel.className = "pref-btn";
      cancel.setAttribute("data-step-up-cancel", "");
      cancel.textContent = "취소";
      [title, text, label, result, confirm, cancel].forEach(function (node) { form.appendChild(node); });
      dialog.appendChild(form);
      document.body.appendChild(dialog);

      var confirmed = false;
      dialog.addEventListener("close", function () {
        field.value = "";
        dialog.remove();
        resolve(confirmed);
      });
      cancel.addEventListener("click", function () { dialog.close(); });
      form.addEventListener("submit", function (event) {
        event.preventDefault();
        var value = field.value;
        field.value = "";
        confirm.disabled = true;
        request("POST", "/api/auth/reauth", { password: value }).then(function (r) {
          value = "";
          if (r.ok) {
            confirmed = true;
            dialog.close();
            return;
          }
          show(form, errorText(r.status, r.body), true);
          field.focus();
        }).catch(function () {
          show(form, "서버에 연결하지 못했습니다.", true);
        }).then(function () {
          confirm.disabled = false;
        });
      });
      dialog.showModal();
      field.focus();
    });
  }

  function bindToggles() {
    var toggles = document.querySelectorAll("input[data-settings-toggle='profile-public']");
    Array.prototype.forEach.call(toggles, function (input) {
      input.addEventListener("change", function () {
        var section = input.closest("section") || input.parentElement;
        var checked = input.checked;
        input.disabled = true;
        // The step-up dialog opens before the change to public is sent. An account without a password has no dialog: the server answers from the session age.
        var ready = checked && input.getAttribute("data-step-up") === "password" ? openStepUpDialog() : Promise.resolve(true);
        ready.then(function (confirmed) {
          if (!confirmed) {
            input.checked = !checked;
            return null;
          }
          return request("PATCH", "/api/me", { profile_public: checked ? 1 : 0 }).then(function (r) {
            if (r.ok) {
              show(section, "저장했습니다.", false);
              var dd = document.querySelector("[data-account-field='profile_public']");
              if (dd) dd.textContent = checked ? "공개 (공개 디렉터리에 표시)" : "비공개";
            } else {
              input.checked = !checked;
              show(section, errorText(r.status, r.body), true);
            }
          });
        }).catch(function () {
          input.checked = !checked;
          show(section, "서버에 연결하지 못했습니다.", true);
        }).then(function () {
          input.disabled = false;
        });
      });
    });
  }

  // I18N design Section 6.2: the interface language selector. The server renders the page text, thus a successful change loads the page again.
  function bindLocaleSelect() {
    var select = document.querySelector("select[data-settings-locale]");
    if (!select) return;
    var i18n = window.ClaireI18n;
    var saved = select.value;
    select.addEventListener("change", function () {
      var section = select.closest("section") || select.parentElement;
      select.disabled = true;
      request("PATCH", "/api/me", { locale: select.value || null }).then(function (r) {
        if (r.ok) {
          if (i18n) i18n.reload(); else window.location.reload();
          return;
        }
        select.value = saved;
        select.disabled = false;
        show(section, i18n ? i18n.t("console.account.locale_error") : errorText(r.status, r.body), true);
      }).catch(function () {
        select.value = saved;
        select.disabled = false;
        show(section, i18n ? i18n.t("console.account.locale_error") : "서버에 연결하지 못했습니다.", true);
      });
    });
  }

  function initManagers(section) {
    var panels = window.ClaireManagePanels || {};
    // The console page has no app.js apiUrl/rootUrl. Unprefixed /themes and /tokens resolve to the caller's own account (N3).
    if (section === "themes") {
      var themeHost = document.getElementById("settings-themes-host");
      if (themeHost && typeof panels.renderThemeManager === "function") {
        panels.renderThemeManager(themeHost, { showClose: false, isOwner: true, isAdmin: false, apiUrl: "/", rootUrl: "/" });
      }
    } else if (section === "tokens") {
      var tokHost = document.getElementById("settings-tokens-host");
      if (tokHost && typeof panels.renderTokenManager === "function") {
        panels.renderTokenManager(tokHost, { showClose: false, canWrite: true, isAdmin: false, rootUrl: "/" });
      }
    }
  }

  function renderPersonalCliCard(host, data) {
    while (host.firstChild) host.removeChild(host.firstChild);
    var pcli = (data && data.personal_cli) || { enabled: false, active_cli: "none", feature_routing: {} };
    var provs = (data && data.providers) || {};
    var caps = ((data && data.capabilities) || {}).providers || {};
    var inContainer = Boolean((data && data.capabilities) && data.capabilities.in_container);
    var agy = provs.antigravity || {};
    var cdx = provs.codex || {};
    var agyCap = caps.antigravity || {};
    var cdxCap = caps.codex || {};

    var header = document.createElement("h2");
    var iconSpan = document.createElement("span");
    iconSpan.setAttribute("data-icon", "terminal");
    header.appendChild(iconSpan);
    header.appendChild(document.createTextNode(" 관리자 개인 CLI 클라이언트"));
    host.appendChild(header);

    var intro = document.createElement("p");
    intro.className = "hint";
    intro.textContent = "개인 CLI 약관 준수를 위해 Antigravity CLI 및 Codex CLI는 관리자 개인 계정 작업에만 적용되며 서버 공용 프로바이더·ServerTier에는 노출되지 않습니다.";
    host.appendChild(intro);

    var form = document.createElement("form");
    form.setAttribute("data-personal-cli-form", "");
    form.style.display = "flex";
    form.style.flexDirection = "column";
    form.style.gap = "12px";

    // Enable & Active CLI
    var toggleLabel = document.createElement("label");
    toggleLabel.style.display = "flex";
    toggleLabel.style.alignItems = "center";
    toggleLabel.style.gap = "8px";
    toggleLabel.style.fontWeight = "600";
    var enabledCb = document.createElement("input");
    enabledCb.type = "checkbox";
    enabledCb.id = "pcli-enabled";
    enabledCb.checked = Boolean(pcli.enabled);
    toggleLabel.appendChild(enabledCb);
    toggleLabel.appendChild(document.createTextNode("관리자 개인 작업에 개인 CLI 클라이언트 사용"));
    form.appendChild(toggleLabel);

    var activeRow = document.createElement("label");
    activeRow.style.display = "flex";
    activeRow.style.flexDirection = "column";
    activeRow.style.gap = "4px";
    activeRow.style.fontWeight = "600";
    activeRow.appendChild(document.createTextNode("기본 개인 CLI 클라이언트"));
    var activeSel = document.createElement("select");
    activeSel.id = "pcli-active";
    activeSel.className = "pref-select";
    [
      ["none", "선택 안 함 (서버 기본 Google AI Studio 사용)"],
      ["antigravity", agyCap.selectable ? "Antigravity CLI (agy)" : "Antigravity CLI (사용 불가 - 점검 필요)"],
      ["codex", cdxCap.selectable ? "Codex CLI (codex)" : "Codex CLI (사용 불가 - 점검 필요)"]
    ].forEach(function (pair) {
      var opt = document.createElement("option");
      opt.value = pair[0];
      opt.textContent = pair[1];
      if (pair[0] === "antigravity" && !agyCap.selectable) opt.disabled = true;
      if (pair[0] === "codex" && !cdxCap.selectable) opt.disabled = true;
      if ((pcli.active_cli || "none") === pair[0]) opt.selected = true;
      activeSel.appendChild(opt);
    });
    activeRow.appendChild(activeSel);
    form.appendChild(activeRow);

    function makeField(labelText, inputEl) {
      var wrap = document.createElement("label");
      wrap.style.display = "flex";
      wrap.style.flexDirection = "column";
      wrap.style.gap = "4px";
      wrap.style.fontSize = "12.5px";
      wrap.style.fontWeight = "600";
      wrap.appendChild(document.createTextNode(labelText));
      wrap.appendChild(inputEl);
      return wrap;
    }

    // Antigravity CLI subsection
    var agyBox = document.createElement("fieldset");
    agyBox.style.border = "1px solid var(--border)";
    agyBox.style.borderRadius = "6px";
    agyBox.style.padding = "10px 12px";
    var agyLegend = document.createElement("legend");
    agyLegend.textContent = "Antigravity CLI (agy)" + (agy.cli_version ? " v" + agy.cli_version : "");
    agyBox.appendChild(agyLegend);
    if (agyCap.locked_reason) {
      var agyLock = document.createElement("p");
      agyLock.className = "hint";
      agyLock.style.color = "var(--error)";
      agyLock.textContent = agyCap.locked_reason;
      agyBox.appendChild(agyLock);
    }
    var agyBin = document.createElement("input");
    agyBin.type = "text";
    agyBin.id = "pcli-agy-bin";
    agyBin.className = "pref-input";
    agyBin.value = agy.bin || "agy";
    agyBox.appendChild(makeField("실행 경로 (Binary)", agyBin));

    var agyModel = document.createElement("select");
    agyModel.id = "pcli-agy-model";
    agyModel.className = "pref-select";
    var agyModels = Array.isArray(agy.available_models) ? agy.available_models : [];
    if (!agyModels.length) {
      var emptyAgy = document.createElement("option");
      emptyAgy.value = agy.model || "";
      emptyAgy.textContent = agy.model ? agy.model + " (agy models 조회 필요)" : "CLI 확인 후 모델 목록을 조회하세요";
      agyModel.appendChild(emptyAgy);
    } else {
      agyModels.forEach(function (m) {
        var mid = typeof m === "string" ? m : (m.id || "");
        var opt = document.createElement("option");
        opt.value = mid;
        opt.textContent = (typeof m === "object" && m.label && m.label !== mid) ? mid + " (" + m.label + ")" : mid;
        if (agy.model === mid) opt.selected = true;
        agyModel.appendChild(opt);
      });
    }
    agyBox.appendChild(makeField("지원 모델 (agy models 동적 조회)", agyModel));

    var agyTestBtn = document.createElement("button");
    agyTestBtn.type = "button";
    agyTestBtn.className = "pref-btn";
    agyTestBtn.style.marginTop = "6px";
    agyTestBtn.textContent = "Antigravity CLI 확인 및 모델 조회";
    agyTestBtn.setAttribute("data-pcli-test", "antigravity");
    agyBox.appendChild(agyTestBtn);
    form.appendChild(agyBox);

    // Codex CLI subsection
    var cdxBox = document.createElement("fieldset");
    cdxBox.style.border = "1px solid var(--border)";
    cdxBox.style.borderRadius = "6px";
    cdxBox.style.padding = "10px 12px";
    var cdxLegend = document.createElement("legend");
    cdxLegend.textContent = "Codex CLI (codex)" + (inContainer ? " - 컨테이너 미지원" : "");
    cdxBox.appendChild(cdxLegend);
    if (cdxCap.locked_reason) {
      var cdxLock = document.createElement("p");
      cdxLock.className = "hint";
      cdxLock.style.color = "var(--error)";
      cdxLock.textContent = cdxCap.locked_reason;
      cdxBox.appendChild(cdxLock);
    }
    var cdxBin = document.createElement("input");
    cdxBin.type = "text";
    cdxBin.id = "pcli-codex-bin";
    cdxBin.className = "pref-input";
    cdxBin.value = cdx.bin || "codex";
    cdxBox.appendChild(makeField("실행 경로 (Binary)", cdxBin));

    var cdxModel = document.createElement("select");
    cdxModel.id = "pcli-codex-model";
    cdxModel.className = "pref-select";
    (Array.isArray(cdx.available_models) ? cdx.available_models : []).forEach(function (m) {
      var mid = typeof m === "string" ? m : (m.id || "");
      var opt = document.createElement("option");
      opt.value = mid;
      opt.textContent = (typeof m === "object" && m.label) ? m.label : (mid || "계정 기본");
      if ((cdx.model || "") === mid) opt.selected = true;
      cdxModel.appendChild(opt);
    });
    cdxBox.appendChild(makeField("지원 모델", cdxModel));

    var cdxTestBtn = document.createElement("button");
    cdxTestBtn.type = "button";
    cdxTestBtn.className = "pref-btn";
    cdxTestBtn.style.marginTop = "6px";
    cdxTestBtn.textContent = "Codex CLI 확인";
    cdxTestBtn.setAttribute("data-pcli-test", "codex");
    cdxBox.appendChild(cdxTestBtn);
    form.appendChild(cdxBox);

    var saveBtn = document.createElement("button");
    saveBtn.type = "submit";
    saveBtn.className = "pref-btn pref-btn-accent";
    saveBtn.textContent = "개인 CLI 설정 저장";
    form.appendChild(saveBtn);

    var result = document.createElement("p");
    result.className = "result";
    result.setAttribute("data-settings-result", "");
    result.setAttribute("role", "status");
    result.setAttribute("aria-live", "polite");
    form.appendChild(result);

    host.appendChild(form);

    form.addEventListener("click", function (ev) {
      var btn = ev.target.closest("[data-pcli-test]");
      if (!btn) return;
      var pname = btn.getAttribute("data-pcli-test");
      btn.disabled = true;
      var payload = {
        provider: pname,
        config: pname === "antigravity" ? { bin: agyBin.value.trim() || "agy" } : { bin: cdxBin.value.trim() || "codex" }
      };
      request("POST", "/api/me/personal-cli/test", payload).then(function (r) {
        if (r.ok && r.body && r.body.ok) {
          show(form, r.body.message || "CLI 확인 완료", false);
          return initAdminPersonalCli();
        }
        show(form, (r.body && (r.body.error || r.body.detail)) || "CLI 확인 실패", true);
      }).catch(function () {
        show(form, "서버에 연결하지 못했습니다.", true);
      }).then(function () {
        btn.disabled = false;
      });
    });

    form.addEventListener("submit", function (ev) {
      ev.preventDefault();
      var payload = {
        enabled: enabledCb.checked,
        active_cli: activeSel.value,
        providers: {
          antigravity: { bin: agyBin.value.trim() || "agy", model: agyModel.value },
          codex: { bin: cdxBin.value.trim() || "codex", model: cdxModel.value }
        }
      };
      request("PATCH", "/api/me/personal-cli", payload).then(function (r) {
        if (r.ok && r.body) {
          renderPersonalCliCard(host, r.body);
          var newForm = host.querySelector("[data-personal-cli-form]");
          if (newForm) show(newForm, "개인 CLI 설정을 저장했습니다.", false);
        } else {
          show(form, errorText(r.status, r.body), true);
        }
      }).catch(function () {
        show(form, "서버에 연결하지 못했습니다.", true);
      });
    });
  }

  function initAdminPersonalCli() {
    var host = document.getElementById("admin-personal-cli-host");
    if (!host) return Promise.resolve();
    return request("GET", "/api/me/personal-cli").then(function (r) {
      if (r.ok && r.body) {
        renderPersonalCliCard(host, r.body);
      } else {
        var msg = host.querySelector("[data-personal-cli-loading]");
        if (msg) msg.textContent = "개인 CLI 정보를 불러오지 못했습니다. (" + errorText(r.status, r.body) + ")";
      }
    }).catch(function () {
      var msg = host.querySelector("[data-personal-cli-loading]");
      if (msg) msg.textContent = "서버에 연결하지 못했습니다.";
    });
  }

  function init() {
    var bootstrap = parseBootstrap();
    if (!bootstrap || bootstrap.area !== "settings") return;
    var section = bootstrap.section;
    if (["account", "federation", "keys", "themes", "tokens"].indexOf(section) < 0) return;
    var extra = bootstrap.settings_account || {};
    bindForms();
    if (section === "account") {
      bindIdentities(extra);
      bindToggles();
      bindLocaleSelect();
    } else if (section === "federation") {
      bindFederation();
    } else if (section === "keys") {
      if (extra.keys_mode === "admin") {
        initAdminPersonalCli();
      } else {
        refreshKeys();
      }
    }
    initManagers(section);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
