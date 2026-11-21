/* Claire user console controller for /settings Group 1 (account, federation, keys, themes, tokens). Design P3-a.
 * Calls only existing APIs: /api/me, /api/me/password, /api/auth/reauth, /api/me/identities*, POST /kb/{number}/graph/bundle, /api/me/keys*, /api/federation/status, /api/federation/followers, /tokens.
 * Server text goes into the DOM only through textContent. No inline handlers. No /admin/* calls.
 */
(function () {
  "use strict";

  // Each text of this script is a catalog key (ClaireI18n, static/locales/{en,ko}.json).
  var tr = function (key, params) { return window.ClaireI18n.t(key, params); };

  var ERROR_TEXT = {
    reauth_required: tr("console.js.common.error.reauth_required"),
    step_up_requires_session: tr("console.js.common.error.step_up_requires_session"),
    invalid_password: tr("console.js.common.error.invalid_password"),
    password_not_set: tr("console.js.account.error.password_not_set"),
    too_many_attempts: tr("console.js.common.error.too_many_attempts"),
    invalid_display_name: tr("console.js.account.error.invalid_display_name"),
    account_not_active: tr("console.js.account.error.account_not_active"),
    invalid_request: tr("console.js.common.error.invalid_request"),
    unsupported_provider: tr("console.js.account.error.unsupported_provider"),
    invalid_api_key: tr("console.js.account.error.invalid_api_key"),
    key_not_found: tr("console.js.account.keys.empty"),
    key_unreadable: tr("console.js.account.error.key_unreadable"),
    follower_not_found: tr("console.js.account.error.follower_not_found"),
    cannot_unlink_last_credential: tr("console.js.account.error.cannot_unlink_last_credential"),
    unauthorized: tr("console.js.common.error.unauthorized")
  };

  var KEY_STATUS_TEXT = { active: tr("console.js.account.keys.status.active"), rejected: tr("console.js.account.keys.status.rejected"), unverified: tr("console.js.account.keys.status.unverified") };
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
      return tr("console.js.account.error.key_verification_unavailable");
    }
    if (body && body.error === "key_verification_failed") {
      return tr("console.js.account.error.key_verification_failed") + (body.message ? " " + body.message : "");
    }
    if (body && typeof body.error === "string" && ERROR_TEXT[body.error]) return ERROR_TEXT[body.error];
    if (body && typeof body.detail === "string" && body.detail) return body.detail + " (HTTP " + status + ")";
    return tr("console.js.common.http_error", { status: status });
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
      empty.textContent = tr("console.js.account.keys.empty");
      box.appendChild(empty);
      return;
    }
    keys.forEach(function (k) {
      var dl = document.createElement("dl");
      dl.setAttribute("data-provider-key", String(k.provider));
      [
        [tr("console.js.account.keys.field.provider"), KEY_PROVIDER_TEXT[k.provider] || k.provider],
        ["fingerprint", k.fingerprint],
        [tr("console.js.account.keys.field.status"), KEY_STATUS_TEXT[k.status] || k.status],
        [tr("console.js.account.keys.field.created"), formatTs(k.created_at)],
        [tr("console.js.account.keys.field.last_verified"), formatTs(k.last_verified_at)],
        [tr("console.js.account.keys.field.last_used"), formatTs(k.last_used_at)]
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
    // The administrator values are only the defaults. Each allowed model can be selected (no baseline).
    var defaults = (policy.admin_defaults || {}).extract_graph || {};
    var prefs = policy.model_prefs || {};
    var summaryEl = document.querySelector("[data-byok-policy-summary]");
    if (summaryEl) {
      var dMod = defaults.model || tr("console.js.account.byok.model_default");
      var dEff = defaults.effort || "medium";
      summaryEl.textContent = tr("console.js.account.byok.policy_summary", { model: dMod, effort: dEff });
    }
    var modelSel = document.querySelector("[data-byok-model-select]");
    if (modelSel) {
      while (modelSel.firstChild) modelSel.removeChild(modelSel.firstChild);
      var defOpt = document.createElement("option");
      defOpt.value = "";
      defOpt.textContent = tr("console.js.account.byok.model_auto", { model: defaults.model || "-" });
      modelSel.appendChild(defOpt);
      var preferredVal = prefs.model || "";
      if (preferredVal && prefs.effort && !/-(?:none|minimal|low|medium|high|xhigh|max)$/i.test(preferredVal)) {
        preferredVal = preferredVal + "-" + String(prefs.effort).toLowerCase();
      }
      (Array.isArray(policy.allowed_models) ? policy.allowed_models : []).forEach(function (m) {
        if (!m) return;
        var opt = document.createElement("option");
        opt.value = String(m);
        opt.textContent = String(m);
        if (preferredVal && preferredVal === m) opt.selected = true;
        modelSel.appendChild(opt);
      });
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
      td0.textContent = tr("console.js.account.identities.empty");
      tr0.appendChild(td0);
      tbody.appendChild(tr0);
      return;
    }
    items.forEach(function (ident) {
      var row = document.createElement("tr");
      var name = providerNames[ident.provider_id] || ident.provider_id;
      [String(name), formatTs(ident.linked_at), formatTs(ident.last_login_at)].forEach(function (text) {
        var td = document.createElement("td");
        td.textContent = text;
        row.appendChild(td);
      });
      var tdBtn = document.createElement("td");
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "pref-btn pref-btn-danger";
      btn.textContent = tr("console.js.account.identities.unlink");
      btn.setAttribute("data-identity-unlink", String(ident.provider_id));
      btn.setAttribute("data-identity-sub", String(ident.sub));
      tdBtn.appendChild(btn);
      row.appendChild(tdBtn);
      tbody.appendChild(row);
    });
  }

  function loadIdentities(providerNames) {
    var box = identitiesBox();
    if (!box) return Promise.resolve();
    return request("GET", "/api/me/identities").then(function (r) {
      if (r.ok && Array.isArray(r.body)) {
        renderIdentities(r.body, providerNames);
      } else {
        show(box, tr("console.js.account.identities.load_failed") + " " + errorText(r.status, r.body), true);
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
      show(box, tr("console.js.common.network_error"), true);
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
          show(box, r.ok ? tr("console.js.account.identities.bad_auth_url") : errorText(r.status, r.body), true);
        }).catch(function () {
          show(box, tr("console.js.common.network_error"), true);
        }).then(function () {
          linkBtn.disabled = false;
        });
        return;
      }
      var unlinkBtn = ev.target.closest("[data-identity-unlink]");
      if (unlinkBtn) {
        var providerId = unlinkBtn.getAttribute("data-identity-unlink") || "";
        var sub = unlinkBtn.getAttribute("data-identity-sub") || "";
        if (!window.confirm(tr("console.js.account.identities.unlink_confirm"))) return;
        unlinkBtn.disabled = true;
        var url = "/api/me/identities/oidc/" + encodeURIComponent(providerId) + "?sub=" + encodeURIComponent(sub);
        request("DELETE", url).then(function (r) {
          if (r.ok) {
            show(box, tr("console.js.account.identities.unlinked"), false);
            return loadIdentities(providerNames);
          }
          show(box, errorText(r.status, r.body), true);
        }).catch(function () {
          show(box, tr("console.js.common.network_error"), true);
        }).then(function () {
          unlinkBtn.disabled = false;
        });
      }
    });
  }

  // --- /settings/federation: actor state and followers (Fedify sidecar design Sections 9.2 and 11) ---

  // The server has the same table (console_federation_panels.ACTOR_STATE_TEXT).
  var ACTOR_STATE_TEXT = {
    active: tr("console.js.account.federation.state.active"),
    account_not_active: tr("console.js.account.federation.state.account_not_active"),
    no_username: tr("console.js.account.federation.state.no_username"),
    federation_off: tr("console.js.account.federation.state.federation_off"),
    profile_not_public: tr("console.js.account.federation.state.profile_not_public")
  };

  function setFederationField(name, text) {
    var dd = document.querySelector("[data-federation-field='" + name + "']");
    if (dd) dd.textContent = text;
  }

  function renderFederationStatus(status) {
    setFederationField("handle", status.handle || tr("console.js.account.federation.handle_unset"));
    setFederationField("actor_id", status.actor_id || "-");
    setFederationField("actor_state", ACTOR_STATE_TEXT[status.actor_state] || String(status.actor_state));
    if (status.followers && typeof status.followers.total === "number") {
      setFederationField("followers_total", status.followers.total.toLocaleString("en-US"));
    }
    var card = document.getElementById("settings-federation-status");
    if (card) card.setAttribute("data-actor-state", String(status.actor_state));
  }

  function followerRow(item) {
    var row = document.createElement("tr");
    row.setAttribute("data-follower", String(item.id));
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
    btn.textContent = tr("console.js.account.followers.remove");
    btn.setAttribute("data-follower-remove", String(item.actor_iri));
    tdBtn.appendChild(btn);
    [tdActor, tdTime, tdBtn].forEach(function (td) { row.appendChild(td); });
    return row;
  }

  function bindFederation() {
    var box = document.querySelector("[data-federation-followers]");
    if (!box) return;
    var tbody = box.querySelector("[data-followers-tbody]");
    var more = box.querySelector("[data-followers-more]");
    var nextMaxId = null;

    function showEmptyRow() {
      var row = document.createElement("tr");
      row.setAttribute("data-followers-empty", "");
      var td = document.createElement("td");
      td.colSpan = 3;
      td.style.color = "var(--muted)";
      td.style.textAlign = "center";
      td.textContent = tr("console.js.account.followers.empty");
      row.appendChild(td);
      tbody.appendChild(row);
    }

    // Gets one page. Without a cursor the list starts again from the newest follower.
    function loadPage(maxId) {
      var url = "/api/federation/followers" + (maxId ? "?max_id=" + encodeURIComponent(maxId) : "");
      return request("GET", url).then(function (r) {
        if (!r.ok || !r.body || !Array.isArray(r.body.items)) {
          show(box, tr("console.js.account.followers.load_failed") + " " + errorText(r.status, r.body), true);
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
      show(box, tr("console.js.common.network_error"), true);
    });
    if (more) {
      more.addEventListener("click", function () {
        if (!nextMaxId) return;
        more.disabled = true;
        loadPage(nextMaxId).catch(function () {
          show(box, tr("console.js.common.network_error"), true);
        }).then(function () {
          more.disabled = false;
        });
      });
    }
    box.addEventListener("click", function (ev) {
      var btn = ev.target.closest("[data-follower-remove]");
      if (!btn) return;
      var actorIri = btn.getAttribute("data-follower-remove") || "";
      if (!window.confirm(tr("console.js.account.followers.remove_confirm"))) return;
      btn.disabled = true;
      request("DELETE", "/api/federation/followers", { actor_iri: actorIri }).then(function (r) {
        // An unknown follower means that the row left the list in the meantime. The list is read again in the two cases.
        if (r.ok || (r.body && r.body.error === "follower_not_found")) {
          show(box, r.ok ? tr("console.js.account.followers.removed") : errorText(r.status, r.body), !r.ok);
          return loadPage(null).then(refreshStatus);
        }
        show(box, errorText(r.status, r.body), true);
        btn.disabled = false;
      }).catch(function () {
        show(box, tr("console.js.common.network_error"), true);
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
      [tr("console.js.account.bundle.file"), info.filename],
      [tr("console.js.account.bundle.entities"), info.entities_count],
      [tr("console.js.account.bundle.relations"), info.relations_count],
      [tr("console.js.account.bundle.documents"), info.documents_count],
      [tr("console.js.account.bundle.size"), info.size_bytes],
      [tr("console.js.account.bundle.expires"), info.expires_at]
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
      a.appendChild(document.createTextNode(" " + tr("console.js.account.bundle.download")));
      out.appendChild(a);
    }
  }

  // --- form handlers ---

  var handlers = {
    "display-name": function (form) {
      var value = form.elements.namedItem("display_name").value;
      return request("PATCH", "/api/me", { display_name: value }).then(function (r) {
        show(form, r.ok ? tr("console.js.common.saved") : errorText(r.status, r.body), !r.ok);
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
        show(form, r.ok ? tr("console.js.account.reauth.done") : errorText(r.status, r.body), !r.ok);
      });
    },
    "password": function (form) {
      var current = form.elements.namedItem("current_password");
      var next = form.elements.namedItem("new_password");
      var confirm = form.elements.namedItem("new_password_confirm");
      if (next.value !== confirm.value) {
        show(form, tr("console.js.account.password.mismatch"), true);
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
          show(form, tr("console.js.account.password.saved", { count: n }), false);
          var dd = document.querySelector("[data-account-field='password']");
          if (dd) dd.textContent = tr("console.js.account.password.set");
        } else {
          show(form, errorText(r.status, r.body), true);
        }
      });
    },
    "bundle-export": function (form) {
      var section = form.closest("section") || form;
      var theme = form.elements.namedItem("theme").value;
      if (!theme) {
        show(section, tr("console.js.account.bundle.select_theme"), true);
        return Promise.resolve();
      }
      show(section, tr("console.js.account.bundle.making"), false);
      // Design Section 5.2: the path selects the knowledge base (the server sends 308 to the path of the owner).
      return request("POST", "/kb/" + encodeURIComponent(theme) + "/graph/bundle", {}).then(function (r) {
        if (r.ok && r.body) {
          show(section, tr("console.js.account.bundle.made"), false);
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
      var value = field.value;
      field.value = "";
      var payload = { provider: provider, api_key: value };
      if (modelEl && modelEl.value) payload.model = modelEl.value;
      return request("PUT", "/api/me/keys", payload).then(function (r) {
        value = "";
        show(form, r.ok ? tr("console.js.account.keys.saved") : errorText(r.status, r.body), !r.ok);
        if (r.ok) return refreshKeys();
      });
    },
    "provider-key-verify": function (form) {
      var provider = form.elements.namedItem("provider").value;
      return request("POST", "/api/me/keys/verify", { provider: provider }).then(function (r) {
        if (r.ok && r.body) {
          show(form, (r.body.ok ? tr("console.js.account.keys.valid") + " " : tr("console.js.account.keys.rejected") + " ") + (r.body.message || ""), !r.body.ok);
          return refreshKeys();
        }
        show(form, errorText(r.status, r.body), true);
      });
    },
    "provider-key-delete": function (form) {
      var provider = form.elements.namedItem("provider").value;
      if (!window.confirm(tr("console.js.account.keys.delete_confirm"))) return Promise.resolve();
      return request("DELETE", "/api/me/keys?provider=" + encodeURIComponent(provider)).then(function (r) {
        if (r.ok) {
          show(form, tr("console.js.account.keys.deleted"), false);
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
          show(scope, tr("console.js.common.network_error"), true);
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
      title.textContent = tr("console.js.account.stepup.title");
      var text = document.createElement("p");
      text.className = "hint";
      text.textContent = tr("console.js.account.stepup.text");
      var label = document.createElement("label");
      label.textContent = tr("console.js.account.stepup.password");
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
      confirm.textContent = tr("console.js.account.stepup.confirm");
      var cancel = document.createElement("button");
      cancel.type = "button";
      cancel.className = "pref-btn";
      cancel.setAttribute("data-step-up-cancel", "");
      cancel.textContent = tr("common.action.cancel");
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
          show(form, tr("console.js.common.network_error"), true);
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
              show(section, tr("console.js.common.saved"), false);
              var dd = document.querySelector("[data-account-field='profile_public']");
              if (dd) dd.textContent = checked ? tr("console.js.account.profile.public") : tr("console.js.account.profile.private");
            } else {
              input.checked = !checked;
              show(section, errorText(r.status, r.body), true);
            }
          });
        }).catch(function () {
          input.checked = !checked;
          show(section, tr("console.js.common.network_error"), true);
        }).then(function () {
          input.disabled = false;
        });
      });
    });

    // Schema v37: the sources of the documents of the personal knowledge base for other readers. No step-up: the change shows or hides links only.
    var sourceToggle = document.querySelector("input[data-settings-toggle='personal-source-public']");
    if (sourceToggle) {
      sourceToggle.addEventListener("change", function () {
        var section = sourceToggle.closest("[data-source-public]") || sourceToggle.parentElement;
        var checked = sourceToggle.checked;
        sourceToggle.disabled = true;
        request("PATCH", "/api/me", { personal_source_public: checked }).then(function (r) {
          if (r.ok) {
            show(section, tr("console.js.common.saved"), false);
          } else {
            sourceToggle.checked = !checked;
            show(section, errorText(r.status, r.body), true);
          }
        }).catch(function () {
          sourceToggle.checked = !checked;
          show(section, tr("console.js.common.network_error"), true);
        }).then(function () {
          sourceToggle.disabled = false;
        });
      });
    }

    // The option "Delete my activity records" (shared knowledge base design Section 13.2) is a field of the purge action of the admin (PATCH /api/admin/accounts, "delete_activity_records"; console_admin.js). An account has no request that deletes itself, thus this page has no control for the option.
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
        show(section, tr("console.js.account.locale_error"), true);
      }).catch(function () {
        select.value = saved;
        select.disabled = false;
        show(section, tr("console.js.account.locale_error"), true);
      });
    });
  }

  // The entity language selector (ontology.label_language). The setting changes API responses only, thus the page does not load again.
  function bindEntityLanguageSelect() {
    var select = document.querySelector("select[data-settings-entity-language]");
    if (!select) return;
    var saved = select.value;
    select.addEventListener("change", function () {
      var section = select.closest("section") || select.parentElement;
      var fail = function () {
        select.value = saved;
        show(section, tr("console.js.account.entity_language_error"), true);
      };
      select.disabled = true;
      request("PATCH", "/api/me", { entity_language: select.value || null }).then(function (r) {
        if (r.ok) {
          saved = select.value;
          show(section, tr("console.js.common.saved"), false);
        } else {
          fail();
        }
      }).catch(fail).then(function () {
        select.disabled = false;
      });
    });
  }

  function bindExtractionModeSelect() {
    var select = document.querySelector("select[data-settings-extraction-mode]");
    if (!select) return;
    var saved = select.value;
    select.addEventListener("change", function () {
      var section = select.closest("section") || select.parentElement;
      var fail = function () {
        select.value = saved;
        show(section, tr("console.js.account.extraction_mode_error"), true);
      };
      select.disabled = true;
      request("PATCH", "/api/me", { extraction_mode: select.value }).then(function (r) {
        if (r.ok) {
          saved = select.value;
          var field = document.querySelector('[data-account-field="extraction_mode"]');
          var chosen = select.options && select.selectedIndex >= 0 ? select.options[select.selectedIndex] : null;
          if (field && chosen) field.textContent = chosen.textContent;
          var guide = document.querySelector("[data-extraction-client-guide]");
          if (guide) guide.hidden = select.value !== "client";
          show(section, tr("console.js.common.saved"), false);
        } else {
          fail();
        }
      }).catch(fail).then(function () {
        select.disabled = false;
      });
    });
  }

  function initManagers(section) {
    var panels = window.ClaireManagePanels || {};
    // The console page has no app.js apiUrl/rootUrl. Unprefixed /tokens resolves to the caller's own account (N3). The page /settings/kb (console_kb.js) has the function of the old /settings/themes.
    if (section === "tokens") {
      var tokHost = document.getElementById("settings-tokens-host");
      if (tokHost && typeof panels.renderTokenManager === "function") {
        panels.renderTokenManager(tokHost, { showClose: false, canWrite: true, isAdmin: false, rootUrl: "/" });
      }
    }
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
      bindEntityLanguageSelect();
      bindExtractionModeSelect();
    } else if (section === "federation") {
      bindFederation();
    } else if (section === "keys") {
      if (extra.keys_mode === "byok") {
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
