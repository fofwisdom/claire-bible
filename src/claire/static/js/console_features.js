/* Claire console controller for per-account features: /settings/telegram, /settings/bundles, /settings/backups, /admin/backups.
 * APIs: /api/auth/reauth, /api/me/telegram, /api/me/support-bundles*, /api/me/data-bundles*, /api/me/backups*, /api/admin/account-backups*.
 * Server text goes into the DOM only through textContent. No inline handlers.
 */
(function () {
  "use strict";

  var ERROR_TEXT = {
    reauth_required: "먼저 이 화면의 '본인 확인'을 마치고 다시 시도하세요.",
    step_up_requires_session: "로그인 세션에서만 할 수 있습니다. API 토큰으로는 할 수 없습니다.",
    invalid_password: "비밀번호가 맞지 않습니다.",
    too_many_attempts: "시도가 너무 많습니다. 잠시 뒤에 다시 시도하세요.",
    invalid_request: "요청 형식이 맞지 않습니다.",
    invalid_bot_token: "봇 토큰 형식이 맞지 않습니다. BotFather가 준 토큰을 그대로 붙여 넣으세요.",
    invalid_allowed_user_ids: "허용 사용자 ID는 숫자로 1개 이상 적으세요(쉼표로 구분).",
    bot_token_rejected: "텔레그램이 이 토큰을 거부했습니다. 토큰을 다시 확인하세요.",
    bot_verification_unavailable: "지금은 텔레그램에 연결할 수 없습니다. 설정은 바뀌지 않았습니다. 잠시 뒤에 다시 시도하세요.",
    bot_in_use: "이 봇은 이미 다른 계정이나 인스턴스 봇이 쓰고 있습니다.",
    bot_not_found: "등록한 봇이 없습니다.",
    bundle_limit_reached: "활성 번들 수 한도에 이르렀습니다. 기존 번들을 지운 뒤 다시 시도하세요.",
    bundle_not_found: "번들이 없거나 만료되었습니다.",
    theme_not_found: "내 계정의 테마가 아닙니다.",
    invalid_days: "수집 기간이 맞지 않습니다.",
    invalid_target: "추적 대상 값이 맞지 않습니다.",
    backup_failed: "백업을 만들지 못했습니다.",
    backup_not_found: "백업이 없습니다.",
    invalid_policy: "자동 백업 설정 값이 맞지 않습니다.",
    restore_conflict: "복원 대상과 충돌해 아무것도 바꾸지 않았습니다.",
    restore_failed: "복원하지 못했습니다.",
    account_not_found: "계정이 없습니다.",
    unauthorized: "로그인이 필요합니다."
  };
  var SCHEDULE_TEXT = { off: "끔", daily: "매일", weekly: "매주" };

  function parseBootstrap() {
    var el = document.getElementById("console-bootstrap");
    if (!el || !el.textContent) return null;
    try { return JSON.parse(el.textContent); } catch (_err) { return null; }
  }

  function show(scope, text, isError) {
    var out = scope ? scope.querySelector("[data-feature-result]") : null;
    if (!out) return;
    out.textContent = text;
    out.setAttribute("data-state", isError ? "error" : "ok");
  }

  function errorText(status, body) {
    var base = body && typeof body.error === "string" && ERROR_TEXT[body.error] ? ERROR_TEXT[body.error] : "요청을 처리하지 못했습니다 (HTTP " + status + ").";
    if (body && typeof body.message === "string" && body.message) return base + " " + body.message;
    return base;
  }

  function focusReauth() {
    var field = document.querySelector("form[data-feature-form='reauth'] input[name='password']");
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

  function formatSize(bytes) {
    if (typeof bytes !== "number") return "-";
    if (bytes < 1024) return bytes + " B";
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
    return (bytes / (1024 * 1024)).toFixed(2) + " MB";
  }

  function formatIso(value) {
    if (!value) return "-";
    return String(value).replace("T", " ").replace(/\.\d+/, "").replace("+00:00", " UTC");
  }

  function el(tag, text, attrs) {
    var node = document.createElement(tag);
    if (text !== undefined && text !== null) node.textContent = String(text);
    if (attrs) Object.keys(attrs).forEach(function (k) { node.setAttribute(k, attrs[k]); });
    return node;
  }

  function clear(node) { while (node && node.firstChild) node.removeChild(node.firstChild); }

  /* Render a list of items as a table. columns: [[label, fn(item)]]. actions: fn(item, cell). */
  function renderTable(host, items, columns, actions, emptyText) {
    clear(host);
    if (!items || !items.length) {
      host.appendChild(el("p", emptyText, { "class": "hint", "data-feature-empty": "" }));
      return;
    }
    var wrap = el("div", null, { "class": "pref-table-wrap" });
    var table = el("table", null, { "class": "pref-table" });
    var head = el("tr");
    columns.forEach(function (c) { head.appendChild(el("th", c[0])); });
    head.appendChild(el("th", "작업"));
    var thead = el("thead");
    thead.appendChild(head);
    table.appendChild(thead);
    var tbody = el("tbody");
    items.forEach(function (item) {
      var tr = el("tr");
      columns.forEach(function (c) { tr.appendChild(el("td", c[1](item))); });
      var cell = el("td");
      actions(item, cell);
      tr.appendChild(cell);
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    wrap.appendChild(table);
    host.appendChild(wrap);
  }

  function downloadLink(href, label) {
    var a = el("a", label, { "class": "pref-btn", href: href, download: "" });
    return a;
  }

  function actionButton(label, onClick) {
    var b = el("button", label, { type: "button", "class": "pref-btn" });
    b.addEventListener("click", function () { onClick(b); });
    return b;
  }

  // --- reauth ---
  function bindReauth() {
    var form = document.querySelector("form[data-feature-form='reauth']");
    if (!form) return;
    form.addEventListener("submit", function (ev) {
      ev.preventDefault();
      var field = form.querySelector("input[name='password']");
      var value = field ? field.value : "";
      request("POST", "/api/auth/reauth", { password: value }).then(function (r) {
        if (field) field.value = "";
        show(form, r.ok ? "본인 확인을 마쳤습니다. 15분 안에 작업하세요." : errorText(r.status, r.body), !r.ok);
      }).catch(function () { show(form, "서버에 연결하지 못했습니다.", true); });
    });
  }

  // --- telegram ---
  function initTelegram() {
    var reg = document.querySelector("form[data-feature-form='telegram-register']");
    if (reg) {
      reg.addEventListener("submit", function (ev) {
        ev.preventDefault();
        var tokenField = reg.querySelector("input[name='bot_token']");
        var allowed = reg.querySelector("input[name='allowed_user_ids']").value;
        show(reg, "텔레그램에 토큰을 확인하는 중입니다...", false);
        request("PUT", "/api/me/telegram", { bot_token: tokenField.value, allowed_user_ids: allowed }).then(function (r) {
          tokenField.value = "";
          if (!r.ok) { show(reg, errorText(r.status, r.body), true); return; }
          show(reg, "저장했습니다. 봇이 곧 시작됩니다.", false);
          window.setTimeout(function () { window.location.reload(); }, 800);
        }).catch(function () { show(reg, "서버에 연결하지 못했습니다.", true); });
      });
    }
    var allowedForm = document.querySelector("form[data-feature-form='telegram-allowed']");
    if (allowedForm) {
      allowedForm.addEventListener("submit", function (ev) {
        ev.preventDefault();
        var allowed = allowedForm.querySelector("input[name='allowed_user_ids']").value;
        request("PATCH", "/api/me/telegram", { allowed_user_ids: allowed }).then(function (r) {
          show(allowedForm, r.ok ? "허용 사용자를 바꿨습니다. 봇이 곧 다시 시작됩니다." : errorText(r.status, r.body), !r.ok);
        }).catch(function () { show(allowedForm, "서버에 연결하지 못했습니다.", true); });
      });
    }
    var toggle = document.querySelector("[data-feature-toggle='telegram-enabled']");
    if (toggle) {
      toggle.addEventListener("change", function () {
        var section = toggle.closest("section");
        var checked = toggle.checked;
        toggle.disabled = true;
        request("PATCH", "/api/me/telegram", { enabled: checked }).then(function (r) {
          if (!r.ok) { toggle.checked = !checked; show(section, errorText(r.status, r.body), true); return; }
          show(section, checked ? "봇을 켰습니다." : "봇을 껐습니다.", false);
        }).catch(function () {
          toggle.checked = !checked;
          show(section, "서버에 연결하지 못했습니다.", true);
        }).then(function () { toggle.disabled = false; });
      });
    }
    var del = document.querySelector("[data-feature-action='telegram-delete']");
    if (del) {
      del.addEventListener("click", function () {
        var section = del.closest("section");
        if (!window.confirm("봇 토큰과 설정을 지웁니다. 계속할까요?")) return;
        request("DELETE", "/api/me/telegram").then(function (r) {
          if (!r.ok) { show(section, errorText(r.status, r.body), true); return; }
          show(section, "봇을 지웠습니다.", false);
          window.setTimeout(function () { window.location.reload(); }, 600);
        }).catch(function () { show(section, "서버에 연결하지 못했습니다.", true); });
      });
    }
  }

  // --- bundles ---
  function loadSupport() {
    var host = document.querySelector("[data-feature-list='support-bundles']");
    if (!host) return Promise.resolve();
    return request("GET", "/api/me/support-bundles").then(function (r) {
      if (!r.ok) { clear(host); host.appendChild(el("p", errorText(r.status, r.body), { "class": "hint" })); return; }
      renderTable(host, r.body.bundles, [
        ["번들", function (b) { return b.bundle_id; }],
        ["기간", function (b) { return b.days_covered + "일"; }],
        ["크기", function (b) { return formatSize(b.size_bytes); }],
        ["만료", function (b) { return formatIso(b.expires_at); }]
      ], function (b, cell) {
        cell.appendChild(downloadLink("/api/me/support-bundles/download?bundle_id=" + encodeURIComponent(b.bundle_id), "⬇ 받기"));
        cell.appendChild(actionButton("🗑", function () {
          request("DELETE", "/api/me/support-bundles?bundle_id=" + encodeURIComponent(b.bundle_id)).then(loadSupport);
        }));
      }, "활성 서포트 번들이 없습니다.");
    });
  }

  function loadData() {
    var host = document.querySelector("[data-feature-list='data-bundles']");
    if (!host) return Promise.resolve();
    return request("GET", "/api/me/data-bundles").then(function (r) {
      if (!r.ok) { clear(host); host.appendChild(el("p", errorText(r.status, r.body), { "class": "hint" })); return; }
      var labels = {};
      (r.body.themes || []).forEach(function (t) { labels[t.theme_id] = t.icon + " " + t.label; });
      renderTable(host, r.body.bundles, [
        ["번들", function (b) { return b.bundle_id; }],
        ["테마", function (b) { return labels[b.theme_id] || String(b.theme_id); }],
        ["엔티티·관계", function (b) { return b.entities_count + " · " + b.relations_count; }],
        ["크기", function (b) { return formatSize(b.size_bytes); }],
        ["만료", function (b) { return formatIso(b.expires_at); }]
      ], function (b, cell) {
        cell.appendChild(downloadLink("/api/me/data-bundles/download?bundle_id=" + encodeURIComponent(b.bundle_id), "⬇ 받기"));
        cell.appendChild(actionButton("🗑", function () {
          request("DELETE", "/api/me/data-bundles?bundle_id=" + encodeURIComponent(b.bundle_id)).then(loadData);
        }));
      }, "활성 데이터 번들이 없습니다.");
    });
  }

  function initBundles() {
    var sForm = document.querySelector("form[data-feature-form='support-bundle-create']");
    if (sForm) {
      sForm.addEventListener("submit", function (ev) {
        ev.preventDefault();
        var days = parseInt(sForm.querySelector("input[name='days']").value, 10);
        var target = sForm.querySelector("input[name='target']").value.trim();
        var payload = { days: isFinite(days) ? days : 1 };
        if (target) payload.target = target;
        show(sForm, "번들을 만드는 중입니다...", false);
        request("POST", "/api/me/support-bundles", payload).then(function (r) {
          show(sForm, r.ok ? "서포트 번들을 만들었습니다." : errorText(r.status, r.body), !r.ok);
          return loadSupport();
        }).catch(function () { show(sForm, "서버에 연결하지 못했습니다.", true); });
      });
    }
    var dForm = document.querySelector("form[data-feature-form='data-bundle-create']");
    if (dForm) {
      dForm.addEventListener("submit", function (ev) {
        ev.preventDefault();
        var theme = dForm.querySelector("select[name='theme']").value;
        show(dForm, "번들을 만드는 중입니다...", false);
        request("POST", "/api/me/data-bundles", { theme: theme }).then(function (r) {
          show(dForm, r.ok ? "데이터 번들을 만들었습니다." : errorText(r.status, r.body), !r.ok);
          return loadData();
        }).catch(function () { show(dForm, "서버에 연결하지 못했습니다.", true); });
      });
    }
    loadSupport();
    loadData();
  }

  // --- backups ---
  function renderPolicy(policy) {
    var status = document.querySelector("[data-backup-policy-status]");
    if (!status || !policy) return;
    var parts = ["현재: " + (SCHEDULE_TEXT[policy.schedule] || policy.schedule) + ", " + policy.keep + "개 보존"];
    if (policy.next_run_at) parts.push("다음 자동 백업: " + formatIso(policy.next_run_at));
    if (policy.last_run_at) parts.push("마지막 자동 백업: " + formatIso(policy.last_run_at) + " (" + (policy.last_status || "-") + ")");
    if (policy.last_error) parts.push("오류: " + policy.last_error);
    status.textContent = parts.join(" · ");
  }

  function loadBackups() {
    var host = document.querySelector("[data-feature-list='backups']");
    if (!host) return Promise.resolve();
    return request("GET", "/api/me/backups").then(function (r) {
      if (!r.ok) { clear(host); host.appendChild(el("p", errorText(r.status, r.body), { "class": "hint" })); return; }
      renderPolicy(r.body.policy);
      renderTable(host, r.body.backups, [
        ["백업", function (b) { return b.backup_id; }],
        ["종류", function (b) { return b.reason === "scheduled" ? "자동" : "수동"; }],
        ["만든 시각", function (b) { return formatIso(b.created_at); }],
        ["테마", function (b) { return (b.themes || []).map(function (t) { return t.label; }).join(", "); }],
        ["크기", function (b) { return formatSize(b.size_bytes); }]
      ], function (b, cell) {
        cell.appendChild(downloadLink("/api/me/backups/download?backup_id=" + encodeURIComponent(b.backup_id), "⬇ 받기"));
        cell.appendChild(actionButton("🗑", function () {
          if (!window.confirm("이 백업을 지웁니다. 되돌릴 수 없습니다. 계속할까요?")) return;
          var section = host.closest("section");
          request("DELETE", "/api/me/backups?backup_id=" + encodeURIComponent(b.backup_id)).then(function (res) {
            if (!res.ok) show(section, errorText(res.status, res.body), true);
            return loadBackups();
          });
        }));
      }, "백업이 없습니다.");
    });
  }

  function initBackups() {
    var create = document.querySelector("[data-feature-action='backup-create']");
    if (create) {
      create.addEventListener("click", function () {
        var section = create.closest("section");
        create.disabled = true;
        show(section, "백업하는 중입니다...", false);
        request("POST", "/api/me/backups", {}).then(function (r) {
          show(section, r.ok ? "백업을 만들었습니다." : errorText(r.status, r.body), !r.ok);
          return loadBackups();
        }).catch(function () { show(section, "서버에 연결하지 못했습니다.", true); }).then(function () { create.disabled = false; });
      });
    }
    var policy = document.querySelector("form[data-feature-form='backup-policy']");
    if (policy) {
      policy.addEventListener("submit", function (ev) {
        ev.preventDefault();
        var schedule = policy.querySelector("select[name='schedule']").value;
        var keep = parseInt(policy.querySelector("input[name='keep']").value, 10);
        request("PUT", "/api/me/backups/policy", { schedule: schedule, keep: keep }).then(function (r) {
          show(policy, r.ok ? "자동 백업 설정을 저장했습니다." : errorText(r.status, r.body), !r.ok);
          if (r.ok) renderPolicy(r.body.policy);
          return loadBackups();
        }).catch(function () { show(policy, "서버에 연결하지 못했습니다.", true); });
      });
    }
    loadBackups();
  }

  // --- admin restore ---
  function loadAdminBackups() {
    var host = document.querySelector("[data-feature-list='admin-backups']");
    if (!host) return Promise.resolve();
    var section = host.closest("section");
    return request("GET", "/api/admin/account-backups").then(function (r) {
      clear(host);
      if (!r.ok) { host.appendChild(el("p", errorText(r.status, r.body), { "class": "hint" })); return; }
      var accounts = r.body.accounts || [];
      if (!accounts.length) { host.appendChild(el("p", "계정 백업이 없습니다.", { "class": "hint", "data-feature-empty": "" })); return; }
      accounts.forEach(function (entry) {
        var who = entry.account || {};
        var title = (who.display_name || who.username || "계정") + " (#" + (who.public_id || "?") + ", " + (who.status || "-") + ")";
        host.appendChild(el("h3", title));
        var box = el("div");
        host.appendChild(box);
        renderTable(box, entry.backups, [
          ["백업", function (b) { return b.backup_id; }],
          ["만든 시각", function (b) { return formatIso(b.created_at); }],
          ["테마", function (b) { return (b.themes || []).map(function (t) { return t.label; }).join(", "); }],
          ["크기", function (b) { return formatSize(b.size_bytes); }]
        ], function (b, cell) {
          cell.appendChild(actionButton("♻️ 복원", function (btn) {
            if (!window.confirm(title + " 계정에 백업 " + b.backup_id + "을(를) 복원합니다. 백업에 있는 테마의 현재 데이터를 백업 시점으로 바꿉니다. 계속할까요?")) return;
            btn.disabled = true;
            show(section, "복원하는 중입니다...", false);
            request("POST", "/api/admin/account-backups/restore", { account: String(who.public_id), backup_id: b.backup_id }).then(function (res) {
              if (!res.ok) { show(section, errorText(res.status, res.body), true); return; }
              var names = (res.body.restored_themes || []).map(function (t) { return t.label; }).join(", ");
              show(section, "복원했습니다: " + names + " (checkpoint: " + res.body.checkpoint + ")", false);
            }).catch(function () { show(section, "서버에 연결하지 못했습니다.", true); }).then(function () { btn.disabled = false; });
          }));
        }, "백업이 없습니다.");
      });
    });
  }

  function init() {
    var bootstrap = parseBootstrap();
    if (!bootstrap) return;
    bindReauth();
    if (bootstrap.area === "settings" && bootstrap.section === "telegram") initTelegram();
    else if (bootstrap.area === "settings" && bootstrap.section === "bundles") initBundles();
    else if (bootstrap.area === "settings" && bootstrap.section === "backups") initBackups();
    else if (bootstrap.area === "admin" && bootstrap.section === "backups") loadAdminBackups();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
