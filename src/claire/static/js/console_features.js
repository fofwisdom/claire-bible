/* Claire console controller for per-account features: /settings/telegram, /settings/bundles, /settings/backups, /admin/backups.
 * APIs: /api/auth/reauth, /api/me/telegram, /api/me/support-bundles*, /api/me/data-bundles*, /api/me/backups*, /api/admin/account-backups*.
 * Server text goes into the DOM only through textContent. No inline handlers.
 */
(function () {
  "use strict";

  var ERROR_TEXT = {
    reauth_required: tr("console.js.common.error.reauth_required"),
    step_up_requires_session: tr("console.js.common.error.step_up_requires_session"),
    invalid_password: tr("console.js.common.error.invalid_password"),
    too_many_attempts: tr("console.js.common.error.too_many_attempts"),
    invalid_request: tr("console.js.common.error.invalid_request"),
    invalid_bot_token: tr("console.js.features.error.invalid_bot_token"),
    invalid_allowed_user_ids: tr("console.js.features.error.invalid_allowed_user_ids"),
    bot_token_rejected: tr("console.js.features.error.bot_token_rejected"),
    bot_verification_unavailable: tr("console.js.features.error.bot_verification_unavailable"),
    bot_in_use: tr("console.js.features.error.bot_in_use"),
    bot_not_found: tr("console.js.features.error.bot_not_found"),
    bundle_limit_reached: tr("console.js.features.error.bundle_limit_reached"),
    bundle_not_found: tr("console.js.features.error.bundle_not_found"),
    theme_not_found: tr("console.js.features.error.theme_not_found"),
    invalid_days: tr("console.js.features.error.invalid_days"),
    invalid_target: tr("console.js.features.error.invalid_target"),
    backup_failed: tr("console.js.features.error.backup_failed"),
    backup_not_found: tr("console.js.features.backups.empty"),
    invalid_policy: tr("console.js.features.error.invalid_policy"),
    restore_conflict: tr("console.js.features.error.restore_conflict"),
    restore_failed: tr("console.js.features.error.restore_failed"),
    account_not_found: tr("console.js.features.error.account_not_found"),
    unauthorized: tr("console.js.common.error.unauthorized")
  };
  var SCHEDULE_TEXT = { off: tr("console.js.features.schedule.off"), daily: tr("console.js.features.schedule.daily"), weekly: tr("console.js.features.schedule.weekly") };

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
    var base = body && typeof body.error === "string" && ERROR_TEXT[body.error] ? ERROR_TEXT[body.error] : tr("console.js.common.http_error", { status: status });
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
    head.appendChild(el("th", tr("console.js.features.col_action")));
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

  /* Put a line icon (static/js/icons.js) before the text of a control. */
  function withIcon(node, icon) {
    if (!icon) return node;
    var mark = el("span", null, { "data-icon": icon });
    if (node.firstChild) { node.insertBefore(document.createTextNode(" "), node.firstChild); node.insertBefore(mark, node.firstChild); }
    else node.appendChild(mark);
    return node;
  }

  function downloadLink(href, label, icon) {
    var a = el("a", label, { "class": "pref-btn", href: href, download: "" });
    return withIcon(a, icon);
  }

  function actionButton(label, onClick, icon, title) {
    var b = el("button", label, { type: "button", "class": "pref-btn" });
    if (title) { b.setAttribute("title", title); b.setAttribute("aria-label", title); }
    b.addEventListener("click", function () { onClick(b); });
    return withIcon(b, icon);
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
        show(form, r.ok ? tr("console.js.features.reauth.done") : errorText(r.status, r.body), !r.ok);
      }).catch(function () { show(form, tr("console.js.common.network_error"), true); });
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
        show(reg, tr("console.js.features.telegram.checking"), false);
        request("PUT", "/api/me/telegram", { bot_token: tokenField.value, allowed_user_ids: allowed }).then(function (r) {
          tokenField.value = "";
          if (!r.ok) { show(reg, errorText(r.status, r.body), true); return; }
          show(reg, tr("console.js.features.telegram.saved"), false);
          window.setTimeout(function () { window.location.reload(); }, 800);
        }).catch(function () { show(reg, tr("console.js.common.network_error"), true); });
      });
    }
    var allowedForm = document.querySelector("form[data-feature-form='telegram-allowed']");
    if (allowedForm) {
      allowedForm.addEventListener("submit", function (ev) {
        ev.preventDefault();
        var allowed = allowedForm.querySelector("input[name='allowed_user_ids']").value;
        request("PATCH", "/api/me/telegram", { allowed_user_ids: allowed }).then(function (r) {
          show(allowedForm, r.ok ? tr("console.js.features.telegram.allowed_saved") : errorText(r.status, r.body), !r.ok);
        }).catch(function () { show(allowedForm, tr("console.js.common.network_error"), true); });
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
          show(section, checked ? tr("console.js.features.telegram.on") : tr("console.js.features.telegram.off"), false);
        }).catch(function () {
          toggle.checked = !checked;
          show(section, tr("console.js.common.network_error"), true);
        }).then(function () { toggle.disabled = false; });
      });
    }
    var del = document.querySelector("[data-feature-action='telegram-delete']");
    if (del) {
      del.addEventListener("click", function () {
        var section = del.closest("section");
        if (!window.confirm(tr("console.js.features.telegram.delete_confirm"))) return;
        request("DELETE", "/api/me/telegram").then(function (r) {
          if (!r.ok) { show(section, errorText(r.status, r.body), true); return; }
          show(section, tr("console.js.features.telegram.deleted"), false);
          window.setTimeout(function () { window.location.reload(); }, 600);
        }).catch(function () { show(section, tr("console.js.common.network_error"), true); });
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
        [tr("console.js.features.bundles.col_bundle"), function (b) { return b.bundle_id; }],
        [tr("console.js.features.bundles.col_period"), function (b) { return tr("console.js.features.bundles.days", { days: b.days_covered }); }],
        [tr("console.js.features.col_size"), function (b) { return formatSize(b.size_bytes); }],
        [tr("console.js.features.bundles.col_expires"), function (b) { return formatIso(b.expires_at); }]
      ], function (b, cell) {
        cell.appendChild(downloadLink("/api/me/support-bundles/download?bundle_id=" + encodeURIComponent(b.bundle_id), tr("console.js.features.download"), "download"));
        cell.appendChild(actionButton("", function () {
          request("DELETE", "/api/me/support-bundles?bundle_id=" + encodeURIComponent(b.bundle_id)).then(loadSupport);
        }, "trash", tr("common.action.delete")));
      }, tr("console.js.features.bundles.support_empty"));
    });
  }

  function loadData() {
    var host = document.querySelector("[data-feature-list='data-bundles']");
    if (!host) return Promise.resolve();
    return request("GET", "/api/me/data-bundles").then(function (r) {
      if (!r.ok) { clear(host); host.appendChild(el("p", errorText(r.status, r.body), { "class": "hint" })); return; }
      var labels = {};
      (r.body.themes || []).forEach(function (t) { labels[t.kb_id] = t.icon + " " + t.label; });
      renderTable(host, r.body.bundles, [
        [tr("console.js.features.bundles.col_bundle"), function (b) { return b.bundle_id; }],
        [tr("console.js.features.col_theme"), function (b) { return labels[b.kb_id] || String(b.kb_id); }],
        [tr("console.js.features.bundles.col_counts"), function (b) { return b.entities_count + " · " + b.relations_count; }],
        [tr("console.js.features.col_size"), function (b) { return formatSize(b.size_bytes); }],
        [tr("console.js.features.bundles.col_expires"), function (b) { return formatIso(b.expires_at); }]
      ], function (b, cell) {
        cell.appendChild(downloadLink("/api/me/data-bundles/download?bundle_id=" + encodeURIComponent(b.bundle_id), tr("console.js.features.download"), "download"));
        cell.appendChild(actionButton("", function () {
          request("DELETE", "/api/me/data-bundles?bundle_id=" + encodeURIComponent(b.bundle_id)).then(loadData);
        }, "trash", tr("common.action.delete")));
      }, tr("console.js.features.bundles.data_empty"));
    });
  }

  function tr(key, params) {
    return window.ClaireI18n ? window.ClaireI18n.t(key, params) : key;
  }

  function copyText(text, field) {
    if (navigator.clipboard && navigator.clipboard.writeText) return navigator.clipboard.writeText(text);
    return new Promise(function (resolve, reject) {
      field.focus();
      field.select();
      if (document.execCommand("copy")) resolve(); else reject(new Error("copy"));
    });
  }

  /* Show the token address of a new bundle below its form. The list API does not return the token, thus this panel is the only place that has the address. */
  function showShare(form, bundle) {
    var panel = form.parentNode.querySelector("[data-bundle-share]");
    if (!panel) {
      panel = el("div", null, { "class": "feature-share", "data-bundle-share": "" });
      form.parentNode.insertBefore(panel, form.nextSibling);
    }
    clear(panel);
    if (!bundle || !bundle.download_url) { panel.hidden = true; return; }
    panel.hidden = false;
    var row = el("div", null, { "class": "feature-inline" });
    var label = el("label", tr("console.js.bundles.share.label"), { "class": "feature-field" });
    var field = el("input", null, { type: "text", readonly: "", "class": "pref-input", "data-bundle-share-url": "" });
    field.value = bundle.download_url;
    field.addEventListener("focus", function () { field.select(); });
    label.appendChild(field);
    row.appendChild(label);
    var status = el("p", null, { "class": "result", role: "status", "aria-live": "polite", "data-bundle-share-status": "" });
    row.appendChild(actionButton(tr("console.js.bundles.share.copy"), function () {
      copyText(bundle.download_url, field).then(function () {
        status.textContent = tr("console.js.bundles.share.copied");
        status.setAttribute("data-state", "ok");
      }).catch(function () {
        field.select();
        status.textContent = tr("console.js.bundles.share.copy_failed");
        status.setAttribute("data-state", "error");
      });
    }, "clipboard"));
    row.appendChild(downloadLink(bundle.download_url, tr("console.js.bundles.share.download"), "download"));
    panel.appendChild(row);
    panel.appendChild(status);
    panel.appendChild(el("p", tr("console.js.bundles.share.hint", { expires: formatIso(bundle.expires_at) }), { "class": "hint" }));
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
        show(sForm, tr("console.js.features.bundles.making"), false);
        request("POST", "/api/me/support-bundles", payload).then(function (r) {
          show(sForm, r.ok ? tr("console.js.features.bundles.support_made") : errorText(r.status, r.body), !r.ok);
          showShare(sForm, r.ok ? r.body.bundle : null);
          return loadSupport();
        }).catch(function () { show(sForm, tr("console.js.common.network_error"), true); });
      });
    }
    var dForm = document.querySelector("form[data-feature-form='data-bundle-create']");
    if (dForm) {
      dForm.addEventListener("submit", function (ev) {
        ev.preventDefault();
        var theme = dForm.querySelector("select[name='theme']").value;
        show(dForm, tr("console.js.features.bundles.making"), false);
        request("POST", "/api/me/data-bundles", { kb: theme }).then(function (r) {
          show(dForm, r.ok ? tr("console.js.features.bundles.data_made") : errorText(r.status, r.body), !r.ok);
          showShare(dForm, r.ok ? r.body.bundle : null);
          return loadData();
        }).catch(function () { show(dForm, tr("console.js.common.network_error"), true); });
      });
    }
    loadSupport();
    loadData();
  }

  // --- backups ---
  function renderPolicy(policy) {
    var status = document.querySelector("[data-backup-policy-status]");
    if (!status || !policy) return;
    var parts = [tr("console.js.features.backups.policy_now", { schedule: SCHEDULE_TEXT[policy.schedule] || policy.schedule, keep: policy.keep })];
    if (policy.next_run_at) parts.push(tr("console.js.features.backups.policy_next", { when: formatIso(policy.next_run_at) }));
    if (policy.last_run_at) parts.push(tr("console.js.features.backups.policy_last", { when: formatIso(policy.last_run_at), status: policy.last_status || "-" }));
    if (policy.last_error) parts.push(tr("console.js.features.backups.policy_error", { error: policy.last_error }));
    status.textContent = parts.join(" · ");
  }

  function loadBackups() {
    var host = document.querySelector("[data-feature-list='backups']");
    if (!host) return Promise.resolve();
    return request("GET", "/api/me/backups").then(function (r) {
      if (!r.ok) { clear(host); host.appendChild(el("p", errorText(r.status, r.body), { "class": "hint" })); return; }
      renderPolicy(r.body.policy);
      renderTable(host, r.body.backups, [
        [tr("console.js.features.backups.col_backup"), function (b) { return b.backup_id; }],
        [tr("console.js.features.backups.col_kind"), function (b) { return b.reason === "scheduled" ? tr("console.js.features.backups.kind_auto") : tr("console.js.features.backups.kind_manual"); }],
        [tr("console.js.features.backups.col_created"), function (b) { return formatIso(b.created_at); }],
        [tr("console.js.features.col_theme"), function (b) { return (b.themes || []).map(function (t) { return t.label; }).join(", "); }],
        [tr("console.js.features.col_size"), function (b) { return formatSize(b.size_bytes); }]
      ], function (b, cell) {
        cell.appendChild(downloadLink("/api/me/backups/download?backup_id=" + encodeURIComponent(b.backup_id), tr("console.js.features.download"), "download"));
        cell.appendChild(actionButton("", function () {
          if (!window.confirm(tr("console.js.features.backups.delete_confirm"))) return;
          var section = host.closest("section");
          request("DELETE", "/api/me/backups?backup_id=" + encodeURIComponent(b.backup_id)).then(function (res) {
            if (!res.ok) show(section, errorText(res.status, res.body), true);
            return loadBackups();
          });
        }, "trash", tr("common.action.delete")));
      }, tr("console.js.features.backups.empty"));
    });
  }

  function initBackups() {
    var create = document.querySelector("[data-feature-action='backup-create']");
    if (create) {
      create.addEventListener("click", function () {
        var section = create.closest("section");
        create.disabled = true;
        show(section, tr("console.js.features.backups.making"), false);
        request("POST", "/api/me/backups", {}).then(function (r) {
          show(section, r.ok ? tr("console.js.features.backups.made") : errorText(r.status, r.body), !r.ok);
          return loadBackups();
        }).catch(function () { show(section, tr("console.js.common.network_error"), true); }).then(function () { create.disabled = false; });
      });
    }
    var policy = document.querySelector("form[data-feature-form='backup-policy']");
    if (policy) {
      policy.addEventListener("submit", function (ev) {
        ev.preventDefault();
        var schedule = policy.querySelector("select[name='schedule']").value;
        var keep = parseInt(policy.querySelector("input[name='keep']").value, 10);
        request("PUT", "/api/me/backups/policy", { schedule: schedule, keep: keep }).then(function (r) {
          show(policy, r.ok ? tr("console.js.features.backups.policy_saved") : errorText(r.status, r.body), !r.ok);
          if (r.ok) renderPolicy(r.body.policy);
          return loadBackups();
        }).catch(function () { show(policy, tr("console.js.common.network_error"), true); });
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
      if (!accounts.length) { host.appendChild(el("p", tr("console.js.features.restore.empty"), { "class": "hint", "data-feature-empty": "" })); return; }
      accounts.forEach(function (entry) {
        var who = entry.account || {};
        var title = (who.display_name || who.username || tr("console.js.features.restore.account")) + " (#" + (who.public_id || "?") + ", " + (who.status || "-") + ")";
        host.appendChild(el("h3", title));
        var box = el("div");
        host.appendChild(box);
        renderTable(box, entry.backups, [
          [tr("console.js.features.backups.col_backup"), function (b) { return b.backup_id; }],
          [tr("console.js.features.backups.col_created"), function (b) { return formatIso(b.created_at); }],
          [tr("console.js.features.col_theme"), function (b) { return (b.themes || []).map(function (t) { return t.label; }).join(", "); }],
          [tr("console.js.features.col_size"), function (b) { return formatSize(b.size_bytes); }]
        ], function (b, cell) {
          cell.appendChild(actionButton(tr("console.js.features.restore.action"), function (btn) {
            if (!window.confirm(tr("console.js.features.restore.confirm", { account: title, backup: b.backup_id }))) return;
            btn.disabled = true;
            show(section, tr("console.js.features.restore.in_work"), false);
            request("POST", "/api/admin/account-backups/restore", { account: String(who.public_id), backup_id: b.backup_id }).then(function (res) {
              if (!res.ok) { show(section, errorText(res.status, res.body), true); return; }
              var names = (res.body.restored_themes || []).map(function (t) { return t.label; }).join(", ");
              show(section, tr("console.js.features.restore.done", { themes: names, checkpoint: res.body.checkpoint }), false);
            }).catch(function () { show(section, tr("console.js.common.network_error"), true); }).then(function () { btn.disabled = false; });
          }, "restore"));
        }, tr("console.js.features.backups.empty"));
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
