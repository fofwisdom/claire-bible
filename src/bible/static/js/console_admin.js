/* Claire Admin Console controller (/admin/*, /moderation/oversight, /moderation/themes). */
(function () {
  "use strict";

  // Each text of this script is a catalog key (ClaireI18n, static/locales/{en,ko}.json).
  var tr = function (key, params) { return window.ClaireI18n.t(key, params); };

  function parseBootstrap() {
    var el = document.getElementById("console-bootstrap");
    if (!el || !el.textContent) return null;
    try {
      return JSON.parse(el.textContent);
    } catch (_err) {
      return null;
    }
  }

  function escHtml(val) {
    return String(val == null ? "" : val).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  function formatTs(ts) {
    var n = Number(ts);
    if (!Number.isFinite(n) || n <= 0) return "—";
    try {
      return new Date(n * 1000).toISOString().replace("T", " ").slice(0, 19) + " UTC";
    } catch (_e) {
      return String(ts);
    }
  }

  var pendingReauthCallback = null;

  function openReauthModal(retryCallback) {
    var modal = document.getElementById("console-reauth-modal");
    var errEl = document.getElementById("console-reauth-result") || document.getElementById("console-reauth-error");
    var pwInput = document.getElementById("console-reauth-password");
    if (!modal) return;
    pendingReauthCallback = typeof retryCallback === "function" ? retryCallback : null;
    if (errEl) {
      errEl.hidden = true;
      errEl.textContent = "";
    }
    if (pwInput) pwInput.value = "";
    modal.hidden = false;
    if (pwInput) pwInput.focus();
  }

  function closeReauthModal() {
    var modal = document.getElementById("console-reauth-modal");
    var pwInput = document.getElementById("console-reauth-password");
    if (pwInput) pwInput.value = "";
    if (modal) modal.hidden = true;
    pendingReauthCallback = null;
  }

  function bindReauthModal() {
    var modal = document.getElementById("console-reauth-modal");
    if (!modal) return;
    var form = document.getElementById("console-reauth-form");
    var errEl = document.getElementById("console-reauth-result") || document.getElementById("console-reauth-error");
    var pwInput = document.getElementById("console-reauth-password");
    var closeButtons = modal.querySelectorAll("[data-console-reauth-close], [data-console-reauth-cancel]");
    for (var i = 0; i < closeButtons.length; i++) {
      closeButtons[i].addEventListener("click", function () {
        closeReauthModal();
      });
    }
    if (form) {
      form.addEventListener("submit", function (ev) {
        ev.preventDefault();
        var password = pwInput ? pwInput.value : "";
        if (!password) {
          if (errEl) {
            errEl.textContent = tr("console.js.admin.reauth.password_needed");
            errEl.hidden = false;
          }
          return;
        }
        fetch("/api/auth/reauth", {
          method: "POST",
          credentials: "same-origin",
          headers: { "Content-Type": "application/json", "Accept": "application/json" },
          body: JSON.stringify({ password: password })
        }).then(function (resp) {
          return resp.json().catch(function () { return {}; }).then(function (body) {
            return { status: resp.status, body: body };
          });
        }).then(function (res) {
          if (res.status === 200) {
            var cb = pendingReauthCallback;
            closeReauthModal();
            if (cb) cb();
            return;
          }
          if (errEl) {
            // POST /api/auth/reauth gives an error code (403 "invalid_password", 409 "password_not_set", 429 "too_many_attempts", 403 "step_up_requires_session"): the dialog shows the catalog text of the code, and the general text for a code without text.
            var code = res.body && typeof res.body.error === "string" ? res.body.error : "";
            var codeText = "";
            if (/^[a-z0-9_]+$/.test(code)) {
              ["console.js.common.error.", "console.js.account.error."].forEach(function (prefix) {
                var text = tr(prefix + code);
                if (!codeText && text !== prefix + code) codeText = text;
              });
            }
            errEl.textContent = codeText || tr("console.js.admin.reauth.failed");
            errEl.hidden = false;
          }
        }).catch(function () {
          if (errEl) {
            errEl.textContent = tr("console.js.admin.network_error");
            errEl.hidden = false;
          }
        });
      });
    }
  }

  function bindThemeSelector() {
    var sel = document.getElementById("console-theme-select");
    if (!sel) return;
    sel.addEventListener("change", function () {
      var val = sel.value;
      var url = new URL(window.location.href);
      if (val) {
        url.searchParams.set("kb", val);
      } else {
        url.searchParams.delete("kb");
      }
      window.location.assign(url.toString());
    });
  }

  function showStatusBanner(el, message, isError) {
    if (!el) return;
    el.hidden = false;
    el.className = isError ? "result err" : "result ok";
    el.setAttribute("data-state", isError ? "error" : "ok");
    el.textContent = message;
  }

  function initAccountsSection(bootstrap) {
    var statusEl = document.getElementById("admin-accounts-result") || document.getElementById("admin-accounts-status");
    var table = document.getElementById("admin-accounts-tbody") || document.getElementById("admin-members-section");
    if (table) {
      table.addEventListener("click", function (ev) {
        var btn = ev.target.closest("[data-account-action]");
        if (!btn) return;
        var publicId = btn.getAttribute("data-public-id") || "";
        var action = btn.getAttribute("data-account-action") || "";
        if (!publicId || !action) return;
        var payload = { public_id: publicId, action: action };
        var performPatch = function () {
          fetch("/api/admin/accounts", {
            method: "PATCH",
            credentials: "same-origin",
            headers: { "Content-Type": "application/json", "Accept": "application/json" },
            body: JSON.stringify(payload)
          }).then(function (resp) {
            return resp.json().catch(function () { return {}; }).then(function (body) {
              return { status: resp.status, body: body };
            });
          }).then(function (res) {
            if (res.status === 200) {
              showStatusBanner(statusEl, tr("console.js.admin.accounts.updated") + " " + publicId + " (" + action + ")", false);
              window.location.reload();
              return;
            }
            if (res.status === 401 && res.body && res.body.error === "reauth_required") {
              openReauthModal(performPatch);
              return;
            }
            showStatusBanner(statusEl, tr("console.js.admin.accounts.failed") + " " + ((res.body && (res.body.detail || res.body.error)) || res.status), true);
          }).catch(function () {
            showStatusBanner(statusEl, tr("console.js.admin.accounts.network_error"), true);
          });
        };
        // The purge confirmation has the option "Delete the activity records" (shared knowledge base design Section 13.2): the contributions of the account stay in the shared knowledge bases without the credit in each case; with the option, the submissions and the reason texts that the account wrote also go.
        if (action === "purge" && window.ClaireUi) {
          var ui = window.ClaireUi;
          ui.openDialog({
            title: tr("console.js.admin.accounts.purge_title"),
            icon: "trash",
            danger: true,
            bodyHtml: '<div class="claire-note danger"><span>' + ui.esc(tr("console.js.admin.accounts.purge_warn", { account: publicId })) + "</span></div>" +
              "<p>" + ui.esc(tr("console.js.admin.accounts.purge_contributions_stay")) + "</p>" +
              '<div class="pref-row"><div class="pref-row-label"><label for="admin-purge-activity"><strong>' + ui.esc(tr("console.js.admin.accounts.purge_activity_label")) + "</strong></label><span>" + ui.esc(tr("console.js.admin.accounts.purge_activity_hint")) + '</span></div><input id="admin-purge-activity" type="checkbox" switch/></div>',
            submitLabel: tr("console.js.admin.accounts.purge_submit"),
            onSubmit: function (_dlg, body) {
              var box = body.querySelector("#admin-purge-activity");
              if (box && box.checked) payload.delete_activity_records = true;
              performPatch();
              return true;
            }
          });
          return;
        }
        performPatch();
      });
    }

    var previewBtn = document.getElementById("admin-retired-preview-btn");
    var releaseForm = document.getElementById("admin-retired-release-form");
    var retiredTbody = document.getElementById("admin-retired-tbody");
    var usernameInput = document.getElementById("admin-retired-username-input");
    var usernameResult = document.getElementById("admin-retired-result");

    function runUsernameRelease(uname, applyFlag) {
      var clean = String(uname || "").trim();
      if (!clean) {
        showStatusBanner(usernameResult, tr("console.js.admin.usernames.needed"), true);
        return;
      }
      var execRelease = function () {
        fetch("/api/admin/usernames/release", {
          method: "POST",
          credentials: "same-origin",
          headers: { "Content-Type": "application/json", "Accept": "application/json" },
          body: JSON.stringify({ username: clean, apply: Boolean(applyFlag) })
        }).then(function (resp) {
          return resp.json().catch(function () { return {}; }).then(function (body) {
            return { status: resp.status, body: body };
          });
        }).then(function (res) {
          if (res.status === 200 && res.body) {
            var label = applyFlag ? tr("console.js.admin.usernames.released") + " " : tr("console.js.admin.usernames.preview") + " ";
            showStatusBanner(usernameResult, label + JSON.stringify(res.body), false);
            return;
          }
          if (res.status === 401 && res.body && res.body.error === "reauth_required") {
            openReauthModal(execRelease);
            return;
          }
          showStatusBanner(usernameResult, tr("console.js.admin.usernames.failed") + " " + ((res.body && (res.body.detail || res.body.error)) || res.status), true);
        }).catch(function () {
          showStatusBanner(usernameResult, tr("console.js.admin.network_error"), true);
        });
      };
      execRelease();
    }

    if (previewBtn) {
      previewBtn.addEventListener("click", function () {
        runUsernameRelease(usernameInput ? usernameInput.value : "", false);
      });
    }
    if (releaseForm) {
      releaseForm.addEventListener("submit", function (ev) {
        ev.preventDefault();
        runUsernameRelease(usernameInput ? usernameInput.value : "", true);
      });
    }
    if (retiredTbody) {
      retiredTbody.addEventListener("click", function (ev) {
        var pBtn = ev.target.closest("[data-retired-preview]");
        if (pBtn) {
          runUsernameRelease(pBtn.getAttribute("data-retired-preview") || "", false);
          return;
        }
        var rBtn = ev.target.closest("[data-retired-release]");
        if (rBtn) {
          runUsernameRelease(rBtn.getAttribute("data-retired-release") || "", true);
        }
      });
    }

    var traceFilter = document.getElementById("admin-signup-trace-filter");
    var traceTree = document.getElementById("admin-signup-trace-tree");
    var traceEmpty = document.getElementById("admin-signup-trace-empty");
    if (traceFilter && traceTree) {
      var applyTraceFilter = function () {
        var q = String(traceFilter.value || "").trim().toLowerCase();
        var rootLis = Array.prototype.filter.call(traceTree.children, function (el) {
          return el.tagName === "LI";
        });
        var visitNode = function (li) {
          var childUl = null;
          for (var i = 0; i < li.children.length; i++) {
            if (li.children[i].tagName === "UL") {
              childUl = li.children[i];
              break;
            }
          }
          var childMatch = false;
          if (childUl) {
            for (var j = 0; j < childUl.children.length; j++) {
              if (childUl.children[j].tagName === "LI" && visitNode(childUl.children[j])) {
                childMatch = true;
              }
            }
          }
          var hay = (
            (li.getAttribute("data-trace-username") || "") + " " +
            (li.getAttribute("data-trace-invite-code") || "") + " " +
            (li.getAttribute("data-trace-path") || "") + " " +
            (li.getAttribute("data-trace-account-id") || "") + " " +
            (li.firstElementChild ? (li.firstElementChild.textContent || "") : "")
          ).toLowerCase();
          var selfMatch = !q || hay.indexOf(q) !== -1;
          var visible = selfMatch || childMatch;
          li.hidden = !visible;
          return visible;
        };
        var anyVisible = false;
        for (var k = 0; k < rootLis.length; k++) {
          if (visitNode(rootLis[k])) anyVisible = true;
        }
        if (traceEmpty) traceEmpty.hidden = anyVisible;
      };
      traceFilter.addEventListener("input", applyTraceFilter);
    }
  }

  function initIdpSection(bootstrap) {
    var providers = Array.isArray(bootstrap.idp_providers) ? bootstrap.idp_providers.slice() : [];
    var origin = bootstrap.origin || window.location.origin;
    var cardsContainer = document.getElementById("admin-idp-cards") || document.getElementById("admin-idp-summary");
    var tbody = document.getElementById("pref-idp-tbody") || document.getElementById("pref-idp-providers-tbody");
    var loginPreview = document.getElementById("pref-idp-login-preview");
    var statusEl = document.getElementById("admin-idp-result") || document.getElementById("admin-idp-status");
    var addForm = document.getElementById("pref-idp-add-form");

    function renderIdpViews() {
      if (cardsContainer) {
        if (providers.length === 0) {
          cardsContainer.innerHTML = '<p class="hint">' + escHtml(tr("console.js.admin.idp.empty")) + "</p>";
        } else {
          cardsContainer.innerHTML = providers.map(function (p) {
            return '<section class="console-subcard" aria-label="' + escHtml(p.display_name) + '"><h2>' + escHtml(p.display_name) + " (" + escHtml(p.id) + ')</h2><dl class="console-kv"><dt>Type</dt><dd>' + escHtml(p.type) + "</dd><dt>Client ID</dt><dd>" + escHtml(p.client_id) + "</dd></dl></section>";
          }).join("");
        }
      }
      if (tbody) {
        if (providers.length === 0) {
          tbody.innerHTML = '<tr><td colspan="7" style="color:var(--muted);text-align:center;">' + tr("console.js.admin.idp.empty") + '</td></tr>';
        } else {
          tbody.innerHTML = providers.map(function (p) {
            var cbUri = origin.replace(/\/$/, "") + "/auth/oidc/" + p.id + "/callback";
            return '<tr data-idp-id="' + escHtml(p.id) + '"><td><input type="checkbox" switch data-idp-toggle="' + escHtml(p.id) + '"' + (p.enabled ? " checked" : "") + "/></td><td><strong>" + escHtml(p.display_name) + "</strong> (<code>" + escHtml(p.id) + '</code>)</td><td><span class="pref-mono">' + escHtml(p.issuer || "https://github.com") + '</span></td><td><code class="pref-mono">' + escHtml(p.client_id) + '</code></td><td><code class="pref-mono" style="white-space:normal">' + escHtml(cbUri) + '</code></td><td><input type="checkbox" switch data-idp-signup="' + escHtml(p.id) + '"' + (p.allow_signup ? " checked" : "") + '/></td><td><button type="button" class="pref-btn pref-btn-danger pref-btn-sm" data-idp-remove="' + escHtml(p.id) + '">' + escHtml(tr("common.action.delete")) + "</button></td></tr>";
          }).join("");
        }
      }
      if (loginPreview) {
        var enabledProviders = providers.filter(function (p) { return p.enabled; });
        if (enabledProviders.length === 0) {
          loginPreview.innerHTML = '<span style="color:var(--muted);font-size:0.85rem;">' + tr("console.js.admin.idp.preview_empty") + '</span>';
        } else {
          loginPreview.innerHTML = enabledProviders.map(function (p) {
            return '<a class="pref-login-btn" href="/auth/oidc/' + escHtml(p.id) + '/start">' + escHtml(tr("console.js.admin.idp.continue_with", { name: p.display_name })) + "</a>";
          }).join(" ");
        }
      }
    }

    function saveProviders(nextProviders, msg) {
      fetch("/api/admin/idp", {
        method: "PUT",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json", "Accept": "application/json" },
        body: JSON.stringify({ providers: nextProviders })
      }).then(function (resp) {
        return resp.json().catch(function () { return {}; }).then(function (body) {
          return { status: resp.status, body: body };
        });
      }).then(function (res) {
        if (res.status === 200) {
          providers = nextProviders;
          renderIdpViews();
          showStatusBanner(statusEl, msg || tr("console.js.admin.idp.saved"), false);
          return;
        }
        showStatusBanner(statusEl, tr("console.js.admin.idp.save_failed") + " " + ((res.body && (res.body.detail || res.body.error)) || res.status), true);
      }).catch(function () {
        showStatusBanner(statusEl, tr("console.js.admin.idp.save_network_error"), true);
      });
    }

    if (tbody) {
      tbody.addEventListener("change", function (ev) {
        var toggleInput = ev.target.closest("[data-idp-toggle]");
        if (toggleInput) {
          var id = toggleInput.getAttribute("data-idp-toggle");
          var next = providers.map(function (p) {
            if (p.id === id) return Object.assign({}, p, { enabled: Boolean(toggleInput.checked) });
            return p;
          });
          saveProviders(next, tr("console.js.admin.idp.toggled") + " " + id);
          return;
        }
        var signupInput = ev.target.closest("[data-idp-signup]");
        if (signupInput) {
          var sid = signupInput.getAttribute("data-idp-signup");
          var nextSignup = providers.map(function (p) {
            if (p.id === sid) return Object.assign({}, p, { allow_signup: Boolean(signupInput.checked) });
            return p;
          });
          saveProviders(nextSignup, tr("console.js.admin.idp.signup_changed") + " " + sid);
        }
      });
      tbody.addEventListener("click", function (ev) {
        var removeBtn = ev.target.closest("[data-idp-remove]");
        if (removeBtn) {
          var rid = removeBtn.getAttribute("data-idp-remove");
          var nextRem = providers.filter(function (p) { return p.id !== rid; });
          saveProviders(nextRem, tr("console.js.admin.idp.removed") + " " + rid);
        }
      });
    }

    if (addForm) {
      addForm.addEventListener("submit", function (ev) {
        ev.preventDefault();
        var idEl = document.getElementById("new-idp-id");
        var nameEl = document.getElementById("new-idp-name");
        var typeEl = document.getElementById("new-idp-type");
        var issuerEl = document.getElementById("new-idp-issuer");
        var clientEl = document.getElementById("new-idp-client");
        var pid = idEl ? idEl.value.trim().toLowerCase() : "";
        var pname = nameEl && nameEl.value.trim() ? nameEl.value.trim() : pid;
        var ptype = typeEl ? typeEl.value : "oidc";
        var pissuer = issuerEl ? issuerEl.value.trim() : "";
        var pclient = clientEl ? clientEl.value.trim() : "";
        if (!pid || !pclient) {
          showStatusBanner(statusEl, tr("console.js.admin.idp.ids_needed"), true);
          return;
        }
        var filtered = providers.filter(function (p) { return p.id !== pid; });
        filtered.push({
          id: pid,
          type: ptype,
          display_name: pname,
          client_id: pclient,
          issuer: pissuer,
          enabled: true,
          allow_signup: true,
          scopes: ["openid", "email", "profile"],
          secret_configured: false
        });
        saveProviders(filtered, tr("console.js.admin.idp.added") + " " + pname);
        addForm.reset();
      });
    }

    var simBtn = document.getElementById("btn-idp-sim-run");
    var simOut = document.getElementById("pref-idp-sim-output");
    if (simBtn && simOut) {
      simBtn.addEventListener("click", function () {
        var personaEl = document.getElementById("pref-idp-persona");
        var modeEl = document.getElementById("pref-idp-mode");
        var provEl = document.getElementById("pref-idp-provisioning");
        var verifiedEl = document.getElementById("pref-idp-verified");
        var persona = personaEl && personaEl.value ? personaEl.value : "alice_google";
        var mode = modeEl ? modeEl.value : "telegram_and_idp";
        var provisioning = provEl ? provEl.value : "invite";
        var reqVerified = verifiedEl ? verifiedEl.checked : true;
        simOut.textContent = "[IdP Flow Simulation] persona=" + persona + " | mode=" + mode + " | provisioning=" + provisioning + " | email_verified=" + reqVerified;
      });
    }
  }

  // --- /admin/audit: the audit records in tabs (administrator actions, MCP calls). The server renders the administrator actions; the MCP calls come from GET /api/admin/mcp/telemetry. ---
  function initAuditSection() {
    var auditTabs = document.getElementById("admin-audit-tabs");
    if (auditTabs && window.ClaireUi) {
      window.ClaireUi.enhance(auditTabs);
    }
    var auditTbody = document.getElementById("pref-mcp-audit-tbody");
    var statusFilter = document.getElementById("admin-mcp-filter-status");
    var toolFilter = document.getElementById("admin-mcp-filter-tool");
    var limitFilter = document.getElementById("admin-mcp-filter-limit");
    var refreshBtn = document.getElementById("admin-mcp-refresh-btn");

    function loadMcpTelemetry() {
      if (!auditTbody) return;
      var params = new URLSearchParams();
      var limitVal = limitFilter && limitFilter.value ? limitFilter.value : "50";
      params.set("limit", limitVal);
      if (statusFilter && statusFilter.value) params.set("status", statusFilter.value);
      if (toolFilter && toolFilter.value.trim()) params.set("tool_name", toolFilter.value.trim());

      fetch("/api/admin/mcp/telemetry?" + params.toString(), {
        method: "GET",
        credentials: "same-origin",
        headers: { "Accept": "application/json" }
      }).then(function (resp) {
        return resp.json().catch(function () { return {}; }).then(function (body) {
          return { status: resp.status, body: body };
        });
      }).then(function (res) {
        if (res.status !== 200 || !res.body || !Array.isArray(res.body.records)) return;
        if (res.body.records.length === 0) {
          auditTbody.innerHTML = '<tr><td colspan="7" style="color:var(--muted);text-align:center;">' + tr("console.js.admin.mcp.audit_empty") + '</td></tr>';
          return;
        }
        auditTbody.innerHTML = res.body.records.map(function (r) {
          return "<tr><td><code>" + escHtml(r.ts || "—") + "</code></td><td><code>" + escHtml(r.call_type || "—") + "</code></td><td><code>" + escHtml(r.tool_name || "—") + '</code></td><td><span class="badge">' + escHtml(r.status) + "</span></td><td>" + escHtml(r.duration_ms) + " ms</td><td>" + escHtml(r.response_bytes || 0) + " B</td><td><code>" + escHtml(r.client_id || "—") + "</code></td></tr>";
        }).join("");
      }).catch(function () {});
    }

    loadMcpTelemetry();
    if (refreshBtn) {
      refreshBtn.addEventListener("click", function () {
        loadMcpTelemetry();
      });
    }
    if (statusFilter) {
      statusFilter.addEventListener("change", function () {
        loadMcpTelemetry();
      });
    }
  }

  function initMcpSection() {
    var bundleForm = document.getElementById("admin-support-bundle-form");
    var bundleResult = document.getElementById("admin-support-bundle-result");
    if (bundleForm) {
      bundleForm.addEventListener("submit", function (ev) {
        ev.preventDefault();
        var daysEl = document.getElementById("admin-support-bundle-days");
        var days = daysEl ? parseInt(daysEl.value, 10) || 7 : 7;
        fetch("/support/bundle", {
          method: "POST",
          credentials: "same-origin",
          headers: { "Content-Type": "application/json", "Accept": "application/json" },
          body: JSON.stringify({ days: days })
        }).then(function (resp) {
          return resp.json().catch(function () { return {}; }).then(function (body) {
            return { status: resp.status, body: body };
          });
        }).then(function (res) {
          if (res.status === 200 && res.body && res.body.token) {
            if (bundleResult) {
              bundleResult.hidden = false;
              bundleResult.className = "result ok";
              bundleResult.innerHTML = tr("console.js.admin.bundle.ready") + ' (' + escHtml(res.body.size_bytes) + ' bytes): <a href="/support/bundle?token=' + encodeURIComponent(res.body.token) + '">' + tr("console.js.admin.bundle.download") + '</a>';
            }
            return;
          }
          showStatusBanner(bundleResult, tr("console.js.admin.bundle.failed") + " " + ((res.body && (res.body.detail || res.body.error)) || res.status), true);
        }).catch(function () {
          showStatusBanner(bundleResult, tr("console.js.admin.bundle.network_error"), true);
        });
      });
    }
  }

  // --- /admin/storage: object storage settings and the connection test (FEDERATION_REMOTE_READING_DESIGN.md Section 9.2, unit FP4-1). The server renders the form with the values and the texts; only the changed values go to the server. ---

  function initStorageSection() {
    var form = document.getElementById("admin-storage-form");
    var result = document.getElementById("admin-storage-result");
    var testBtn = document.getElementById("admin-storage-test");
    var testState = document.getElementById("admin-storage-test-state");
    if (!form) return;

    function msg(name) { return form.getAttribute("data-msg-" + name) || ""; }

    function setResult(text, isError) {
      if (!result) return;
      result.textContent = text;
      result.setAttribute("data-state", isError ? "error" : "ok");
    }

    function send(method, url, payload) {
      var init = { method: method, credentials: "same-origin", headers: { "Accept": "application/json" } };
      if (payload !== undefined) {
        init.headers["Content-Type"] = "application/json";
        init.body = JSON.stringify(payload);
      }
      return fetch(url, init).then(function (resp) {
        return resp.json().catch(function () { return null; }).then(function (body) {
          return { ok: resp.ok, status: resp.status, body: body };
        });
      });
    }

    // Sends a change. When the server asks for recent authentication, the shared dialog opens and the change is sent again after it.
    function sendChange(method, url, payload, onDone) {
      var attempt = function () {
        send(method, url, payload).then(function (res) {
          if (res.status === 401 && res.body && res.body.error === "reauth_required") {
            setResult(msg("reauth"), true);
            openReauthModal(attempt);
            return;
          }
          onDone(res);
        }).catch(function () {
          setResult(msg("network"), true);
        });
      };
      attempt();
    }

    form.addEventListener("submit", function (ev) {
      ev.preventDefault();
      var update = {};
      var fields = form.querySelectorAll("[data-storage-initial]");
      Array.prototype.forEach.call(fields, function (field) {
        if (field.disabled || String(field.value) === field.getAttribute("data-storage-initial")) return;
        update[field.name] = field.value;
      });
      if (!Object.keys(update).length) {
        setResult(msg("nochange"), false);
        return;
      }
      sendChange("PUT", "/api/admin/storage", update, function (res) {
        if (!res.ok) {
          var detail = res.body && typeof res.body.detail === "string" ? " " + res.body.detail : "";
          setResult(msg("failed") + detail, true);
          return;
        }
        Array.prototype.forEach.call(fields, function (field) {
          if (field.name === "secret_key") {
            if (field.value) field.setAttribute("placeholder", "••••••••");
            field.value = "";
            return;
          }
          field.setAttribute("data-storage-initial", String(field.value));
        });
        setResult(msg("saved"), false);
      });
    });

    if (testBtn) {
      testBtn.addEventListener("click", function () {
        testBtn.disabled = true;
        setResult(msg("testing"), false);
        sendChange("POST", "/api/admin/storage/test", {}, function (res) {
          testBtn.disabled = false;
          if (res.status === 409) {
            setResult(msg("not_configured"), true);
            return;
          }
          var test = res.body && res.body.test;
          if (!res.ok || !test) {
            setResult(msg("failed"), true);
            return;
          }
          if (testState) {
            testState.textContent = "";
            var badge = document.createElement("span");
            badge.className = test.ok ? "badge badge-enabled" : "badge badge-disabled";
            badge.setAttribute("data-storage-test-state", test.ok ? "ok" : "failed");
            badge.textContent = test.ok ? msg("test_ok") : msg("test_failed") + " " + String(test.failed_operation || "");
            testState.appendChild(badge);
          }
          if (test.ok) setResult(msg("test_ok"), false);
          else setResult(msg("test_failed") + " " + String(test.failed_operation || "") + (test.detail ? " (" + test.detail + ")" : ""), true);
        });
      });
    }
  }

  // --- /admin/plugins: switch plugins on and off (docs/implementation/INGESTION.md section 2.2). The server renders the table, the texts, and the dialog content of each plugin; switching on sends the digest that the administrator saw and the enabled plugins of the overlap group (replace). ---

  function initPluginsSection() {
    var card = document.getElementById("admin-plugins-card");
    var dialog = document.getElementById("admin-plugin-dialog");
    var result = document.getElementById("admin-plugins-result");
    if (!card) return;

    function msg(name) { return card.getAttribute("data-msg-" + name.replace(/_/g, "-")) || ""; }

    function setResult(text) {
      if (result) result.textContent = text;
    }

    function put(id, payload, onOk, onFail) {
      var attempt = function () {
        fetch("/api/admin/plugins/" + encodeURIComponent(id), {
          method: "PUT",
          credentials: "same-origin",
          headers: { "Accept": "application/json", "Content-Type": "application/json" },
          body: JSON.stringify(payload)
        }).then(function (resp) {
          return resp.json().catch(function () { return null; }).then(function (body) {
            if (resp.status === 401 && body && body.error === "reauth_required") {
              setResult(msg("reauth"));
              openReauthModal(attempt);
              return;
            }
            if (!resp.ok) {
              var code = body && body.error ? String(body.error) : "";
              setResult(msg(code) || msg("failed"));
              onFail();
              return;
            }
            onOk();
          });
        }).catch(function () {
          setResult(msg("network"));
          onFail();
        });
      };
      attempt();
    }

    function askToSwitchOn(input) {
      var id = input.getAttribute("data-plugin-switch");
      var template = card.querySelector('template[data-plugin-dialog-body="' + CSS.escape(id) + '"]');
      var title = document.getElementById("admin-plugin-dialog-title");
      var body = document.getElementById("admin-plugin-dialog-body");
      var confirmBtn = document.getElementById("admin-plugin-dialog-confirm");
      if (!dialog || !template || !title || !body || !confirmBtn) return;
      var titleTemplate = dialog.getAttribute("data-title-template") || "{name}";
      title.textContent = titleTemplate.replace("{name}", input.getAttribute("data-plugin-name") || id);
      body.textContent = "";
      body.appendChild(template.content.cloneNode(true));
      confirmBtn.textContent = input.getAttribute("data-plugin-confirm") || "";
      var replaceNode = body.querySelector("[data-plugin-replace]");
      var replace = replaceNode ? JSON.parse(replaceNode.getAttribute("data-plugin-replace") || "[]") : [];
      dialog.returnValue = "";
      dialog.onclose = function () {
        if (dialog.returnValue !== "confirm") return;
        input.disabled = true;
        put(id, { enabled: true, digest: input.getAttribute("data-plugin-digest"), replace: replace }, function () {
          window.location.reload();
        }, function () {
          input.disabled = false;
        });
      };
      dialog.showModal();
    }

    Array.prototype.forEach.call(card.querySelectorAll("input[data-plugin-switch]"), function (input) {
      input.addEventListener("change", function () {
        var id = input.getAttribute("data-plugin-switch");
        if (input.checked) {
          input.checked = false;
          askToSwitchOn(input);
          return;
        }
        input.disabled = true;
        put(id, { enabled: false }, function () {
          window.location.reload();
        }, function () {
          input.checked = true;
          input.disabled = false;
        });
      });
    });
  }

  // --- /admin/federation: readiness checks, sidecar heartbeat, domain blocks (Fedify sidecar design Sections 9.2, 11, and 12). Federation has no switch. ---

  function initFederationSection() {
    var FED_ERROR_TEXT = {
      step_up_requires_session: tr("console.js.common.error.step_up_requires_session"),
      forbidden: tr("console.js.admin.error.forbidden"),
      invalid_domain: tr("console.js.admin.federation.error.invalid_domain"),
      invalid_note: tr("console.js.admin.federation.error.invalid_note"),
      invalid_request: tr("console.js.common.error.invalid_request"),
      domain_block_not_found: tr("console.js.admin.federation.error.domain_block_not_found")
    };
    var readinessTbody = document.getElementById("admin-federation-readiness-tbody");
    var readinessResult = document.getElementById("admin-federation-readiness-result");
    var refreshBtn = document.getElementById("admin-federation-refresh");
    var heartbeatDl = document.getElementById("admin-federation-heartbeat");
    var blocksTbody = document.getElementById("admin-federation-domain-blocks-tbody");
    var blocksResult = document.getElementById("admin-federation-domain-blocks-result");
    var blockForm = document.getElementById("admin-federation-domain-block-form");

    function setResult(el, text, isError) {
      if (!el) return;
      el.textContent = text;
      el.setAttribute("data-state", isError ? "error" : "ok");
    }

    function clear(node) {
      while (node && node.firstChild) node.removeChild(node.firstChild);
    }

    function cell(text) {
      var td = document.createElement("td");
      td.textContent = text == null ? "" : String(text);
      return td;
    }

    function messageRow(tbody, colSpan, text, marker) {
      var row = document.createElement("tr");
      if (marker) row.setAttribute(marker, "");
      var td = cell(text);
      td.colSpan = colSpan;
      td.style.color = "var(--muted)";
      td.style.textAlign = "center";
      row.appendChild(td);
      tbody.appendChild(row);
    }

    function send(method, url, payload) {
      var init = { method: method, credentials: "same-origin", headers: { "Accept": "application/json" } };
      if (payload !== undefined) {
        init.headers["Content-Type"] = "application/json";
        init.body = JSON.stringify(payload);
      }
      return fetch(url, init).then(function (resp) {
        return resp.json().catch(function () { return null; }).then(function (body) {
          return { ok: resp.ok, status: resp.status, body: body };
        });
      });
    }

    function errorText(res) {
      var body = res.body;
      if (body && typeof body.error === "string" && FED_ERROR_TEXT[body.error]) return FED_ERROR_TEXT[body.error];
      if (body && typeof body.detail === "string" && body.detail) return body.detail;
      return tr("console.js.common.http_error", { status: res.status });
    }

    // Sends a change. When the server asks for recent authentication, the shared dialog opens and the change is sent again after it.
    function sendChange(method, url, payload, resultEl, onDone) {
      var attempt = function () {
        send(method, url, payload).then(function (res) {
          if (res.status === 401 && res.body && res.body.error === "reauth_required") {
            setResult(resultEl, tr("console.js.admin.federation.reauth_needed"), true);
            openReauthModal(attempt);
            return;
          }
          onDone(res);
        }).catch(function () {
          setResult(resultEl, tr("console.js.admin.federation.network_error"), true);
        });
      };
      attempt();
    }

    function readinessGroupSummary(groupItems) {
      var reqTotal = 0;
      var reqPassed = 0;
      var reqFailed = 0;
      var warnFailed = 0;
      var skipped = 0;
      groupItems.forEach(function (item) {
        if (item.applies === false) {
          skipped += 1;
          return;
        }
        if (item.level === "warning") {
          if (!item.passed) warnFailed += 1;
          return;
        }
        reqTotal += 1;
        if (item.passed) reqPassed += 1;
        else reqFailed += 1;
      });
      var parts = [tr("console.js.admin.readiness.group_summary.required", { passed: reqPassed, total: reqTotal })];
      if (reqFailed > 0) parts.push(tr("console.js.admin.readiness.group_summary.failed", { count: reqFailed }));
      if (warnFailed > 0) parts.push(tr("console.js.admin.readiness.group_summary.warnings", { count: warnFailed }));
      if (skipped > 0) parts.push(tr("console.js.admin.readiness.group_summary.skipped", { count: skipped }));
      return parts.join(" · ");
    }

    function readinessText(item) {
      var nameKey = "console.js.admin.readiness.item." + String(item.id || "").toLowerCase() + ".name";
      var nameText = tr(nameKey);
      if (nameText === nameKey) nameText = item.name;
      var detailText = item.detail;
      if (item.code) {
        var codeKey = "console.js.admin.readiness.code." + item.code;
        var localized = tr(codeKey, item.values || {});
        if (localized !== codeKey) {
          detailText = localized;
          // A failed item whose code comes from the party of the answer also tells all failed paths (GF8), the signature form (GF9), or the media type of the request (GF10).
          var ownText = { paths_not_routed: true, signed_request_failed: true };
          if (!item.passed && item.values && !ownText[item.code]) {
            ["paths", "forms", "media_type"].forEach(function (name) {
              if (item.values[name]) detailText += " " + tr("console.js.admin.readiness.suffix." + name, item.values);
            });
          }
        }
      }
      return { name: nameText, detail: detailText };
    }

    function renderReadiness(readiness) {
      if (!readinessTbody) return;
      clear(readinessTbody);
      var items = (readiness && readiness.items) || [];
      if (!items.length) {
        messageRow(readinessTbody, 5, tr("console.js.admin.readiness.empty"));
        setResult(readinessResult, tr("console.js.admin.readiness.some_failed"), true);
        return;
      }
      var groups = ["account", "knowledge", "operation"];
      var ungrouped = items.filter(function (item) { return groups.indexOf(item.group) === -1; });
      var renderGroup = function (groupId, groupItems) {
        if (!groupItems.length) return;
        if (groupId) {
          var headRow = document.createElement("tr");
          headRow.setAttribute("data-readiness-group", groupId);
          headRow.style.background = "var(--surface-2, rgba(128,128,128,0.08))";
          var th = document.createElement("th");
          th.colSpan = 5;
          th.style.textAlign = "left";
          var titleSpan = document.createElement("strong");
          titleSpan.textContent = tr("console.js.admin.readiness.group." + groupId + ".title");
          var summarySpan = document.createElement("span");
          summarySpan.setAttribute("data-readiness-group-summary", groupId);
          summarySpan.style.marginLeft = "10px";
          summarySpan.style.fontWeight = "400";
          summarySpan.style.color = "var(--muted)";
          summarySpan.textContent = readinessGroupSummary(groupItems);
          th.appendChild(titleSpan);
          th.appendChild(summarySpan);
          headRow.appendChild(th);
          readinessTbody.appendChild(headRow);
        }
        groupItems.forEach(function (item) {
          var applies = item.applies !== false;
          var level = item.level || "required";
          var texts = readinessText(item);
          var row = document.createElement("tr");
          row.setAttribute("data-readiness-item", String(item.id));
          if (groupId) row.setAttribute("data-readiness-group-of", groupId);
          row.setAttribute("data-readiness-level", level);
          row.setAttribute("data-readiness-applies", applies ? "1" : "0");
          row.setAttribute("data-readiness-passed", item.passed ? "1" : "0");
          var tdId = document.createElement("td");
          var code = document.createElement("code");
          code.textContent = String(item.id);
          tdId.appendChild(code);
          var tdLevel = document.createElement("td");
          var levelTag = document.createElement("span");
          levelTag.className = level === "warning" ? "claire-tag warn" : "claire-tag";
          levelTag.textContent = tr("console.js.admin.readiness.level." + level);
          tdLevel.appendChild(levelTag);
          var tdState = document.createElement("td");
          var pill = document.createElement("span");
          if (!applies) {
            pill.className = "badge";
            pill.textContent = tr("console.js.admin.readiness.not_applicable");
          } else {
            pill.className = item.passed ? "badge badge-enabled" : (level === "warning" ? "claire-tag warn" : "badge badge-disabled");
            pill.textContent = item.passed ? tr("console.js.admin.readiness.passed") : tr("console.js.admin.readiness.failed");
          }
          tdState.appendChild(pill);
          [tdId, cell(texts.name), tdLevel, tdState, cell(texts.detail)].forEach(function (td) { row.appendChild(td); });
          readinessTbody.appendChild(row);
        });
      };
      groups.forEach(function (g) {
        renderGroup(g, items.filter(function (item) { return item.group === g; }));
      });
      if (ungrouped.length) renderGroup("", ungrouped);
      var warnFailed = items.some(function (item) { return item.applies !== false && item.level === "warning" && !item.passed; });
      if (readiness && readiness.passed) {
        setResult(readinessResult, tr(warnFailed ? "console.js.admin.readiness.required_passed_with_warning" : "console.js.admin.readiness.all_passed"), false);
      } else {
        setResult(readinessResult, tr("console.js.admin.readiness.some_failed"), true);
      }
    }

    function formatLoopState(loop) {
      if (!loop) return tr("console.js.admin.sidecar.loop_unavailable");
      var progressText;
      if (loop.progress_at == null) {
        progressText = tr("console.js.admin.sidecar.loop_no_progress");
      } else {
        var ageSec = Math.max(0, Math.round((Date.now() / 1000) - Number(loop.progress_at)));
        progressText = tr("console.js.admin.sidecar.loop_progress_age", { seconds: ageSec });
      }
      var errText = loop.error ? fedCodeText(loop.error) : tr("console.js.admin.sidecar.loop_error_none");
      return tr("console.js.admin.sidecar.loop_summary", { progress: progressText, error: errText });
    }

    function renderHeartbeat(heartbeat) {
      if (!heartbeatDl) return;
      clear(heartbeatDl);
      var rows;
      if (!heartbeat) {
        rows = [[tr("console.js.admin.sidecar.heartbeat"), tr("console.js.admin.sidecar.no_heartbeat")]];
      } else {
        var check = heartbeat.self_check || {};
        var loops = heartbeat.loops || {};
        var host = heartbeat.public_host || {};
        var hostText = (host.addresses || []).map(function (entry) { return entry.address + " (" + entry["class"] + ")"; }).join(", ") || tr("console.js.admin.sidecar.public_host_unresolved");
        var traffic = heartbeat.traffic;
        var inboundText = tr("console.js.admin.sidecar.traffic_unavailable");
        var deliveryText = inboundText;
        if (traffic && traffic.inbound_verified && traffic.inbound_rejected && traffic.deliveries) {
          var failedTries = 0;
          Object.keys(traffic.deliveries.failed || {}).forEach(function (name) { failedTries += Number(traffic.deliveries.failed[name]) || 0; });
          inboundText = tr("console.js.admin.sidecar.traffic_inbound_summary", { count: traffic.inbound_verified.count, hosts: traffic.inbound_verified.hosts, rejected: traffic.inbound_rejected.count });
          deliveryText = tr("console.js.admin.sidecar.traffic_deliveries_summary", { succeeded: traffic.deliveries.succeeded, failed: failedTries });
        }
        rows = [
          [tr("console.js.admin.sidecar.last_heartbeat"), formatTs(heartbeat.received_at)],
          [tr("console.js.admin.sidecar.contract_version"), heartbeat.contract_version],
          [tr("console.js.admin.sidecar.sidecar_version"), heartbeat.sidecar_version],
          [tr("console.js.admin.sidecar.queue_depth"), heartbeat.queue_depth],
          [tr("console.js.admin.sidecar.self_check"), check.ok === true ? tr("console.js.admin.readiness.passed") : (check.ok === false ? tr("console.js.admin.readiness.failed") : tr("console.js.admin.sidecar.self_check_pending"))],
          [tr("console.js.admin.sidecar.loop_knowledge_reads"), formatLoopState(loops.knowledge_reads), "knowledge_reads"],
          [tr("console.js.admin.sidecar.loop_knowledge_exchange"), formatLoopState(loops.knowledge_exchange), "knowledge_exchange"],
          [tr("console.js.admin.sidecar.public_host"), hostText, null, "public_host"],
          [tr("console.js.admin.sidecar.traffic_inbound"), inboundText, null, "traffic_inbound"],
          [tr("console.js.admin.sidecar.traffic_deliveries"), deliveryText, null, "traffic_deliveries"]
        ];
      }
      heartbeatDl.setAttribute("data-heartbeat", heartbeat ? "present" : "absent");
      rows.forEach(function (row) {
        var dt = document.createElement("dt");
        dt.textContent = row[0];
        var dd = document.createElement("dd");
        if (row[2]) dd.setAttribute("data-sidecar-loop", row[2]);
        if (row[3]) dd.setAttribute("data-sidecar-row", row[3]);
        dd.textContent = row[1] == null ? "-" : String(row[1]);
        heartbeatDl.appendChild(dt);
        heartbeatDl.appendChild(dd);
      });
    }

    // Reading settings of the server (FEDERATION_REMOTE_READING_DESIGN.md Section 10, console fields of unit FP4-5). The server renders the form with the values and the texts; only the changed values go to the server.
    var readingForm = document.getElementById("admin-federation-reading-form");
    var readingResult = document.getElementById("admin-federation-reading-result");
    if (readingForm) {
      readingForm.addEventListener("submit", function (ev) {
        ev.preventDefault();
        var update = {};
        var fields = readingForm.querySelectorAll("[data-reading-initial]");
        Array.prototype.forEach.call(fields, function (field) {
          if (String(field.value) === field.getAttribute("data-reading-initial")) return;
          var scale = Number(field.getAttribute("data-reading-scale") || "0");
          update[field.name] = scale ? Math.round(Number(field.value) * scale) : field.value;
        });
        if (!Object.keys(update).length) {
          setResult(readingResult, readingForm.getAttribute("data-msg-nochange"), false);
          return;
        }
        sendChange("PUT", "/api/admin/federation/reading", update, readingResult, function (res) {
          if (!res.ok) {
            var detail = res.body && typeof res.body.detail === "string" ? " " + res.body.detail : "";
            setResult(readingResult, readingForm.getAttribute("data-msg-failed") + detail, true);
            return;
          }
          Array.prototype.forEach.call(fields, function (field) { field.setAttribute("data-reading-initial", String(field.value)); });
          setResult(readingResult, readingForm.getAttribute("data-msg-saved"), false);
        });
      });
    }

    function loadState() {
      if (refreshBtn) refreshBtn.disabled = true;
      return send("GET", "/api/admin/federation").then(function (res) {
        if (!res.ok || !res.body) {
          setResult(readinessResult, tr("console.js.admin.federation.load_failed") + " " + errorText(res), true);
          return;
        }
        renderReadiness(res.body.readiness);
        renderHeartbeat(res.body.heartbeat);
      }).catch(function () {
        setResult(readinessResult, tr("console.js.admin.federation.load_network_error"), true);
      }).then(function () {
        if (refreshBtn) refreshBtn.disabled = false;
      });
    }

    function renderBlocks(blocks) {
      if (!blocksTbody) return;
      clear(blocksTbody);
      if (!blocks.length) {
        messageRow(blocksTbody, 4, tr("console.js.admin.federation.blocks_empty"), "data-domain-block-empty");
        return;
      }
      blocks.forEach(function (block) {
        var row = document.createElement("tr");
        row.setAttribute("data-domain-block", String(block.domain));
        var tdDomain = document.createElement("td");
        var code = document.createElement("code");
        code.className = "pref-mono";
        code.textContent = String(block.domain);
        tdDomain.appendChild(code);
        var tdBtn = document.createElement("td");
        var btn = document.createElement("button");
        btn.type = "button";
        btn.className = "pref-btn pref-btn-danger";
        btn.textContent = tr("console.js.admin.federation.unblock");
        btn.setAttribute("data-domain-block-remove", String(block.domain));
        tdBtn.appendChild(btn);
        [tdDomain, cell(block.note), cell(formatTs(block.created_at)), tdBtn].forEach(function (td) { row.appendChild(td); });
        blocksTbody.appendChild(row);
      });
    }

    function loadBlocks() {
      return send("GET", "/api/admin/federation/domain-blocks").then(function (res) {
        if (res.ok && res.body && Array.isArray(res.body.domain_blocks)) renderBlocks(res.body.domain_blocks);
      }).catch(function () {});
    }

    if (refreshBtn) {
      refreshBtn.addEventListener("click", function () {
        setResult(readinessResult, tr("console.js.admin.readiness.in_work"), false);
        loadState();
      });
    }

    if (blockForm) {
      blockForm.addEventListener("submit", function (ev) {
        ev.preventDefault();
        var domainInput = blockForm.elements.namedItem("domain");
        var noteInput = blockForm.elements.namedItem("note");
        var payload = { domain: domainInput.value.trim(), note: noteInput.value };
        if (!payload.domain) return;
        sendChange("POST", "/api/admin/federation/domain-blocks", payload, blocksResult, function (res) {
          if (res.ok && res.body) {
            setResult(blocksResult, res.body.created ? tr("console.js.admin.federation.blocked") + " " + res.body.domain_block.domain : tr("console.js.admin.federation.already_blocked") + " " + res.body.domain_block.domain, false);
            domainInput.value = "";
            noteInput.value = "";
            loadBlocks();
            // The state of a known server comes from the domain blocks: the tab of the known servers reads again.
            if (loadKnown) loadKnown();
            return;
          }
          setResult(blocksResult, errorText(res), true);
        });
      });
    }

    if (blocksTbody) {
      blocksTbody.addEventListener("click", function (ev) {
        var btn = ev.target.closest("[data-domain-block-remove]");
        if (!btn) return;
        var domain = btn.getAttribute("data-domain-block-remove") || "";
        if (!window.confirm(tr("console.js.admin.federation.unblock_confirm") + " " + domain)) return;
        sendChange("DELETE", "/api/admin/federation/domain-blocks", { domain: domain }, blocksResult, function (res) {
          if (res.ok) {
            setResult(blocksResult, tr("console.js.admin.federation.unblocked") + " " + domain, false);
          } else {
            setResult(blocksResult, errorText(res), true);
          }
          loadBlocks();
          if (loadKnown) loadKnown();
        });
      });
    }

    loadState();

    // The tabs of the section: switches and state, known servers, domain blocks, inbox journal (knowledge federation design Section 6.5).
    var fedTabs = document.getElementById("admin-federation-tabs");
    if (fedTabs && window.ClaireUi) {
      window.ClaireUi.enhance(fedTabs);
    }

    var autoToggle = document.getElementById("admin-fed-auto-contrib-toggle");
    if (autoToggle && window.ClaireUi) {
      // GET and PUT /api/admin/auto-contribution: {"state": "on" | "off"} is the server switch of the automatic contribution (design Section 9.9).
      window.ClaireUi.request("/api/admin/auto-contribution").then(function (r) {
        if (r.ok && r.data) autoToggle.checked = r.data.state === "on";
        else autoToggle.disabled = true;
      });
      autoToggle.addEventListener("change", function () {
        var next = autoToggle.checked;
        window.ClaireUi.request("/api/admin/auto-contribution", { method: "PUT", body: { state: next ? "on" : "off" } }).then(function (r) {
          if (!r.ok) {
            autoToggle.checked = !next;
            window.ClaireUi.toast(window.ClaireUi.errorText(r), "danger");
            return;
          }
          autoToggle.checked = r.data.state === "on";
          window.ClaireUi.toast(tr("console.js.admin.themes.saved"), "ok");
        });
      });
    }

    var fedTime = function (value) {
      // A time of the known servers is a number of seconds; a time of the journal is an ISO 8601 text.
      if (value == null || value === "") return "—";
      var date = typeof value === "number" ? new Date(value * 1000) : new Date(String(value));
      return isNaN(date.getTime()) ? String(value) : date.toLocaleString(document.documentElement.lang || undefined);
    };
    // The catalog text of a code (a state, a source); a code without text shows as the code.
    var fedCodeLabel = function (prefix, code) {
      var key = prefix + code;
      var text = tr(key);
      return text === key ? String(code == null ? "" : code) : text;
    };
    // The catalog text of an error or refusal code of the knowledge federation (a journal row, a read of a known server): the text for this page ("console.js.admin.federation.code.<code>"), else the shared error text ("kb.err.<code>", "kb.reason.<code>"), else the general text with the code. The page never shows a code alone.
    var fedCodeText = function (code) {
      var c = String(code == null ? "" : code);
      if (/^[a-z0-9_]+$/.test(c)) {
        var keys = ["console.js.admin.federation.code." + c, "kb.err." + c, "kb.reason." + c];
        for (var i = 0; i < keys.length; i++) {
          var text = tr(keys[i]);
          if (text !== keys[i]) return text;
        }
      }
      return tr("console.js.admin.federation.code_unknown", { code: c });
    };

    var loadKnown = null;
    var knownHost = document.getElementById("admin-federation-known-host");
    if (knownHost && window.ClaireUi) {
      var selectedDomain = "";
      loadKnown = function () {
        var ui = window.ClaireUi;
        // GET /api/admin/federation/known-servers gives {"servers": [...], "switches": {...}}. One server is "known_server_view": the fields of known_servers.server_view, "kbs" (remote_kbs.view of each record), "kb_count", and "kb_read".
        ui.request("/api/admin/federation/known-servers").then(function (r) {
          var servers = (r.ok && r.data && r.data.servers) || [];
          if (!servers.some(function (s) { return s.domain === selectedDomain; })) selectedDomain = servers.length ? String(servers[0].domain) : "";
          var cur = servers.find(function (s) { return s.domain === selectedDomain; }) || null;
          var addHtml = '<form id="admin-known-add-form" class="console-subcard claire-card no-split"><div class="console-form-row">' +
            '<label class="console-field claire-field" for="admin-known-domain" style="flex:1;min-width:220px">' + ui.esc(tr("console.js.admin.federation.known.add_label")) + '<input id="admin-known-domain" class="pref-input claire-input" placeholder="other.example.org" maxlength="253" autocomplete="off" spellcheck="false" required/></label>' +
            '<button type="submit" class="pref-btn pref-btn-accent claire-btn pri">' + ui.esc(tr("console.js.admin.federation.known.add_btn")) + "</button></div></form>";
          if (!r.ok) {
            knownHost.innerHTML = addHtml + '<div class="claire-note warn"><span>' + ui.esc(ui.errorText(r)) + "</span></div>";
          } else {
            var statusTag = function (s) { return '<span class="claire-tag ' + (s.status === "blocked" ? "danger" : "ok") + '">' + ui.esc(fedCodeLabel("console.js.admin.federation.known.status.", s.status)) + "</span>"; };
            var left = '<div class="claire-list-col">' + (servers.length ? servers.map(function (s) {
              return '<button type="button" class="claire-list-item' + (cur && cur.domain === s.domain ? " active" : "") + '" data-pick-known="' + ui.esc(s.domain) + '">' +
                '<div class="claire-list-item-row"><strong>' + ui.esc(s.domain) + "</strong>" + statusTag(s) + "</div>" +
                '<div class="claire-list-item-sub"><span>' + ui.esc(s.software_name ? s.software_name + (s.software_version ? " " + s.software_version : "") : "—") + " · " + ui.esc(s.kb_count == null ? tr("console.js.admin.federation.known.not_read") : ui.t("kb.pub.kb_count", { count: s.kb_count })) + "</span></div></button>";
            }).join("") : '<div class="console-subcard claire-card"><p class="hint claire-hint">' + ui.esc(tr("console.js.admin.federation.known.empty")) + "</p></div>") + "</div>";
            var right = '<div class="claire-detail-col"><div class="console-subcard claire-card">';
            if (cur) {
              var kbRead = cur.kb_read || null;
              var fields = [
                ["software", cur.software_name || "—"],
                ["version", cur.software_version || "—"],
                ["source", fedCodeLabel("console.js.admin.federation.known.source.", cur.source)],
                ["created", fedTime(cur.created_at)],
                ["last_contact", fedTime(cur.last_contact_at)],
                ["kb_count", cur.kb_count == null ? "—" : String(cur.kb_count)],
                ["last_read", fedTime(cur.last_read_at)],
                ["kb_read", fedTime(kbRead && kbRead.last_read_at) + (kbRead && kbRead.last_read_at ? " (" + tr("console.js.admin.federation.known.kb_read_result", { stored: kbRead.stored, refused: kbRead.refused }) + ")" : "")],
                ["errors", String((cur.error_count || 0) + ((kbRead && kbRead.error_count) || 0))]
              ];
              var errors = [];
              // Each error is [text, code]: the note shows the catalog text of the code, and keeps the code in "data-known-code".
              if (cur.last_error) errors.push([tr("console.js.admin.federation.known.error_server", { time: fedTime(cur.last_error_at), error: fedCodeText(cur.last_error) }), cur.last_error]);
              if (kbRead && kbRead.last_error) errors.push([tr("console.js.admin.federation.known.error_kb_read", { time: fedTime(kbRead.last_error_at), error: fedCodeText(kbRead.last_error) }), kbRead.last_error]);
              if (kbRead && kbRead.refused_code) errors.push([tr("console.js.admin.federation.known.error_refused", { count: kbRead.refused, code: fedCodeText(kbRead.refused_code) }), kbRead.refused_code]);
              right += '<div class="console-card-head claire-card-head"><h3>' + ui.esc(cur.domain) + " " + statusTag(cur) + '</h3><div class="console-actions claire-actions">' +
                (cur.status === "blocked" ? "" : '<button type="button" class="pref-btn pref-btn-danger pref-btn-sm claire-btn danger claire-btn-sm" data-block-known="' + ui.esc(cur.domain) + '">' + ui.esc(tr("console.js.admin.federation.known.block_btn")) + "</button>") +
                // DELETE /api/admin/federation/known-servers removes the server and what this server keeps of it (KNOWLEDGE_FEDERATION.md Section 14.4). A blocked server can be removed too: the domain block stays.
                '<button type="button" class="pref-btn pref-btn-sm claire-btn sec claire-btn-sm" data-remove-known="' + ui.esc(cur.domain) + '">' + ui.esc(tr("console.js.admin.federation.known.remove_btn")) + "</button></div></div>" +
                (cur.status === "blocked" ? '<div class="claire-note warn"><span>' + ui.esc(tr("console.js.admin.federation.known.blocked_by", { domain: cur.blocked_by || cur.domain })) + "</span></div>" : "") +
                '<dl class="console-kv" data-known-read>' + fields.map(function (f) {
                  return "<dt>" + ui.esc(tr("console.js.admin.federation.known.field." + f[0])) + '</dt><dd data-known-field="' + ui.esc(f[0]) + '">' + ui.esc(f[1]) + "</dd>";
                }).join("") + "</dl>" +
                errors.map(function (pair) { return '<div class="claire-note warn" data-known-error data-known-code="' + ui.esc(pair[1]) + '"><span>' + ui.esc(pair[0]) + "</span></div>"; }).join("") +
                // The shared knowledge bases of the server from the store of remote knowledge bases ("kbs"). The name is the link to the page on that server.
                ui.renderTable({
                  caption: tr("console.js.admin.federation.known.kbs_caption", { domain: cur.domain }),
                  emptyText: tr("console.js.admin.federation.known.kbs_empty"),
                  columns: [
                    { label: ui.t("kb.dlg.col_kb"), render: function (k) { return /^https?:\/\//i.test(String(k.url || "")) ? '<a href="' + ui.esc(k.url) + '" target="_blank" rel="noopener noreferrer">' + ui.esc(k.label) + "</a>" : ui.esc(k.label); } },
                    { label: ui.t("kb.knowledge_type.label"), render: function (k) { return ui.esc(ui.t(k.knowledge_type === "fictional" ? "kb.knowledge_type.fictional" : "kb.knowledge_type.real")); } },
                    { label: ui.t("kb.dlg.col_docs"), render: function (k) { return ui.esc(k.documents == null ? "—" : k.documents); } },
                    { label: ui.t("kb.dlg.col_entities"), render: function (k) { return ui.esc(k.entities == null ? "—" : k.entities); } },
                    { label: ui.t("kb.dlg.col_relations"), render: function (k) { return ui.esc(k.relations == null ? "—" : k.relations); } }
                  ],
                  rows: cur.kbs || []
                });
            } else {
              right += '<p class="hint claire-hint">' + ui.esc(tr("console.js.admin.federation.known.empty")) + "</p>";
            }
            right += "</div></div>";
            knownHost.innerHTML = addHtml + '<div class="claire-split">' + left + right + "</div>";
          }
          ui.enhance(knownHost);
          var form = knownHost.querySelector("#admin-known-add-form");
          if (form) form.addEventListener("submit", function (ev) {
            ev.preventDefault();
            var d = knownHost.querySelector("#admin-known-domain").value.trim();
            if (!d) return;
            // POST gives {"server": {...}, "created": true | false}: 201 for a new server, 200 for a server that is known.
            ui.request("/api/admin/federation/known-servers", { method: "POST", body: { domain: d } }).then(function (pr) {
              if (!pr.ok) { ui.toast(ui.errorText(pr), "danger"); return; }
              var added = (pr.data && pr.data.server && pr.data.server.domain) || d;
              selectedDomain = String(added);
              ui.toast(tr(pr.data && pr.data.created ? "console.js.admin.federation.known.added" : "console.js.admin.federation.known.already", { domain: added }), "ok");
              loadKnown();
            });
          });
          knownHost.querySelectorAll("[data-pick-known]").forEach(function (b) {
            b.addEventListener("click", function () { selectedDomain = b.getAttribute("data-pick-known"); loadKnown(); });
          });
          knownHost.querySelectorAll("[data-block-known]").forEach(function (b) {
            b.addEventListener("click", function () {
              var domain = b.getAttribute("data-block-known");
              if (!window.confirm(tr("console.js.admin.federation.known.block_confirm", { domain: domain }))) return;
              b.disabled = true;
              // POST /api/admin/federation/domain-blocks: {"domain", "note"}. The route needs a recent sign-in; the request helper opens the re-authentication dialog.
              ui.request("/api/admin/federation/domain-blocks", { method: "POST", body: { domain: domain, note: tr("console.js.admin.federation.known.block_note") } }).then(function (br) {
                if (!br.ok) {
                  b.disabled = false;
                  ui.toast(ui.errorText(br), "danger");
                  return;
                }
                ui.toast(tr("console.js.admin.federation.known.block_done", { domain: domain }), "ok");
                loadBlocks();
                loadKnown();
              });
            });
          });
          knownHost.querySelectorAll("[data-remove-known]").forEach(function (b) {
            b.addEventListener("click", function () {
              var domain = b.getAttribute("data-remove-known");
              var listHtml = function (attr, keys) {
                return "<ul " + attr + ' style="margin:4px 0 10px;padding-left:20px">' + keys.map(function (k) { return "<li>" + ui.esc(tr("console.js.admin.federation.known.remove_" + k)) + "</li>"; }).join("") + "</ul>";
              };
              // The confirmation tells what is deleted and what stays (the table of KNOWLEDGE_FEDERATION.md Section 14.4).
              var bodyHtml = '<p style="margin:0 0 8px">' + ui.esc(tr("console.js.admin.federation.known.remove_intro", { domain: domain })) + "</p>" +
                "<strong>" + ui.esc(tr("console.js.admin.federation.known.remove_deleted_head")) + "</strong>" +
                listHtml("data-remove-deleted", ["deleted.server", "deleted.kbs", "deleted.documents", "deleted.followers", "deleted.tasks", "deleted.payloads"]) +
                "<strong>" + ui.esc(tr("console.js.admin.federation.known.remove_stays_head")) + "</strong>" +
                listHtml("data-remove-stays", ["stays.copies", "stays.follows", "stays.journal", "stays.block"]) +
                '<p class="claire-hint" style="margin:0">' + ui.esc(tr("console.js.admin.federation.known.remove_again")) + "</p>";
              ui.openDialog({
                title: tr("console.js.admin.federation.known.remove_title", { domain: domain }),
                bodyHtml: bodyHtml,
                danger: true,
                submitLabel: tr("console.js.admin.federation.known.remove_submit")
              }).then(function (choice) {
                if (choice !== "confirm") return;
                b.disabled = true;
                // DELETE with the body {"domain"} (one key). The route needs the same origin and a recent sign-in: for 401 "reauth_required" the request helper opens the re-authentication dialog and sends the request again. 200 gives {"removed": true, "domain", "deleted": {"knowledge_bases", "listed_documents", "followers", "outbox_tasks", "journal_payloads"}}.
                ui.request("/api/admin/federation/known-servers", { method: "DELETE", body: { domain: domain } }).then(function (dr) {
                  if (!dr.ok) {
                    b.disabled = false;
                    ui.toast(ui.errorText(dr), "danger");
                    // 404 "known_server_not_found": the list is not current.
                    if (dr.status === 404) loadKnown();
                    return;
                  }
                  var deleted = (dr.data && dr.data.deleted) || {};
                  var count = function (key) { return deleted[key] == null ? 0 : deleted[key]; };
                  if (selectedDomain === domain) selectedDomain = "";
                  ui.toast(tr("console.js.admin.federation.known.remove_done", {
                    domain: (dr.data && dr.data.domain) || domain,
                    knowledge_bases: count("knowledge_bases"),
                    listed_documents: count("listed_documents"),
                    followers: count("followers"),
                    outbox_tasks: count("outbox_tasks"),
                    journal_payloads: count("journal_payloads")
                  }), "ok");
                  loadKnown();
                });
              });
            });
          });
        });
      };
      loadKnown();
    }

    var journalHost = document.getElementById("admin-federation-journal-host");
    if (journalHost && window.ClaireUi) {
      // The states of journal.STATES. The filter "all" sends no parameter "status".
      var JOURNAL_STATES = ["received", "processed", "failed", "refused"];
      var jStatus = "all";
      var jRows = [];
      var jNext = null;
      var jError = null;
      var drawJournal = function () {
        var ui = window.ClaireUi;
        var stateTone = { failed: "danger", processed: "ok" };
        journalHost.innerHTML = '<section class="console-subcard claire-card"><div class="console-card-head claire-card-head"><h2><span data-icon="list"></span> ' + ui.esc(tr("console.js.admin.federation.journal.title")) + "</h2>" +
          ui.renderChips({
            name: "fed-journal-status",
            label: tr("console.js.admin.federation.journal.filter_label"),
            value: jStatus,
            options: [{ value: "all", label: tr("console.js.admin.federation.journal.state.all") }].concat(JOURNAL_STATES.map(function (state) { return { value: state, label: tr("console.js.admin.federation.journal.state." + state) }; }))
          }) + "</div>" +
          (jError ? '<div class="claire-note warn"><span>' + ui.esc(ui.errorText(jError)) + "</span></div>" : "") +
          ui.renderTable({
            caption: tr("console.js.admin.federation.journal.title"),
            emptyText: tr("console.js.admin.federation.journal.empty"),
            columns: [
              { label: tr("console.js.admin.federation.journal.col_activity"), render: function (e) { return ui.esc(e.activity_type) + (e.kind ? ' <span class="claire-hint">' + ui.esc(e.kind) + "</span>" : ""); } },
              { label: tr("console.js.admin.federation.journal.col_actor"), render: function (e) { return '<span style="overflow-wrap:anywhere">' + ui.esc(e.actor) + "</span>"; } },
              { label: tr("console.js.admin.federation.journal.col_state"), render: function (e) {
                return '<span class="claire-tag ' + (stateTone[e.status] || "warn") + '">' + ui.esc(fedCodeLabel("console.js.admin.federation.journal.state.", e.status)) + "</span>" +
                  // The error code shows as its catalog text; the code is in "title" and "data-journal-code".
                  (e.error_code ? ' <span data-journal-code="' + ui.esc(e.error_code) + '" title="' + ui.esc(e.error_code) + '">' + ui.esc(fedCodeText(e.error_code)) + "</span>" : "") +
                  (e.error_detail ? ' <span class="claire-hint" style="overflow-wrap:anywhere">' + ui.esc(e.error_detail) + "</span>" : "");
              } },
              { label: tr("console.js.admin.federation.journal.col_attempts"), render: function (e) { return ui.esc(e.attempts == null ? "—" : e.attempts); } },
              { label: tr("console.js.admin.federation.journal.col_time"), render: function (e) { return ui.esc(fedTime(e.received_at)); } },
              // "replayable" is true for a row whose state is in journal.REPLAY_STATES ("failed", "received").
              { label: "", actions: true, render: function (e) { return e.replayable ? '<button type="button" class="pref-btn pref-btn-sm claire-btn claire-btn-sm" data-replay-j="' + ui.esc(e.id) + '">' + ui.esc(tr("console.js.admin.federation.journal.replay")) + "</button>" : "—"; } }
            ],
            rows: jRows
          }) +
          (jNext != null ? '<div class="console-actions claire-actions"><button type="button" class="pref-btn claire-btn sec" data-journal-more>' + ui.esc(tr("console.js.admin.federation.journal.more")) + "</button></div>" : "") + "</section>";
        journalHost.querySelectorAll('input[name="fed-journal-status"]').forEach(function (inp) {
          inp.addEventListener("change", function () { jStatus = inp.value; loadJournal(false); });
        });
        var moreBtn = journalHost.querySelector("[data-journal-more]");
        if (moreBtn) moreBtn.addEventListener("click", function () { moreBtn.disabled = true; loadJournal(true); });
        journalHost.querySelectorAll("[data-replay-j]").forEach(function (b) {
          b.addEventListener("click", function () {
            var entryId = b.getAttribute("data-replay-j");
            b.disabled = true;
            // POST …/journal/{id}/replay takes an empty object and gives {"entry": {...}} with the new state of the row.
            ui.request("/api/admin/federation/journal/" + encodeURIComponent(entryId) + "/replay", { method: "POST", body: {} }).then(function (rr) {
              if (!rr.ok) {
                b.disabled = false;
                ui.toast(ui.errorText(rr), "danger");
                return;
              }
              var entry = rr.data && rr.data.entry;
              if (entry) {
                jRows = jRows.map(function (row) { return String(row.id) === String(entry.id) ? entry : row; });
                ui.toast(tr("console.js.admin.federation.journal.replay_done", { state: fedCodeLabel("console.js.admin.federation.journal.state.", entry.status) }), entry.status === "processed" ? "ok" : "danger");
              }
              drawJournal();
            });
          });
        });
      };
      // GET /api/admin/federation/journal?status=&max_id= gives {"entries": [...], "next_max_id": number | null}, the newest first. "more" is true for the next page.
      var loadJournal = function (more) {
        var ui = window.ClaireUi;
        var query = [];
        if (jStatus !== "all") query.push("status=" + encodeURIComponent(jStatus));
        if (more && jNext != null) query.push("max_id=" + encodeURIComponent(jNext));
        ui.request("/api/admin/federation/journal" + (query.length ? "?" + query.join("&") : "")).then(function (r) {
          var entries = (r.ok && r.data && r.data.entries) || [];
          jError = r.ok ? null : r;
          if (r.ok) {
            jRows = more ? jRows.concat(entries) : entries;
            jNext = r.data && r.data.next_max_id != null ? r.data.next_max_id : null;
          } else if (!more) {
            jRows = [];
            jNext = null;
          }
          drawJournal();
        });
      };
      loadJournal(false);
    }
  }

  // The embedding model of each shared knowledge base, with the mark "re-embed required" (GET /kb as the admin gives "embed_provider", "embed_model", and "reembed_required").
  function initProvidersEmbedCard() {
    var host = document.getElementById("admin-providers-embed-host");
    var ui = window.ClaireUi;
    if (!host || !ui) return;
    ui.request("/kb").then(function (r) {
      var kbs = (r.ok && r.data && r.data.knowledge_bases) || [];
      var counts = {};
      var reembed = {};
      kbs.forEach(function (k) {
        var m = k.embed_model ? (k.embed_provider ? k.embed_provider + " / " : "") + k.embed_model : tr("console.js.admin.providers.embed_none");
        counts[m] = (counts[m] || 0) + 1;
        if (k.reembed_required) reembed[m] = (reembed[m] || 0) + 1;
      });
      var rows = Object.keys(counts).map(function (m) { return { model: m, kbs: counts[m], reembed: reembed[m] || 0 }; });
      host.innerHTML = '<section class="console-subcard claire-card" aria-label="' + ui.esc(tr("console.js.admin.providers.embed_card")) + '"><h2><span data-icon="database"></span> ' + ui.esc(tr("console.js.admin.providers.embed_card")) + "</h2>" +
        ui.renderTable({
          caption: tr("console.js.admin.providers.embed_card"),
          columns: [
            { label: tr("console.js.admin.providers.embed_model"), render: function (x) { return '<code class="pref-mono">' + ui.esc(x.model) + "</code>"; } },
            { label: ui.t("kb.pub.nav_kbs"), key: "kbs" },
            { label: ui.t("kb.pub.reembed_badge"), render: function (x) { return x.reembed ? '<span class="claire-tag danger">' + ui.esc(x.reembed) + "</span>" : '<span class="claire-tag ok">0</span>'; } }
          ],
          rows: rows
        }) + "</section>";
      ui.enhance(host);
    });
  }

  // /moderation/oversight (design Section 10.3): all shared knowledge bases, those without an active moderator, moderator assignment, failed merge jobs. There is no admin list route: the list is GET /kb as the admin, which has "active_moderators" and "reembed_required".
  function initAdminKbSection() {
    var host = document.getElementById("admin-kb-host");
    var ui = window.ClaireUi;
    var dlgs = window.ClaireKbDialogs;
    if (!host || !ui || !dlgs) return;
    var filterVal = "all";
    var items = [];
    var failed = {};
    var loadError = null;

    function load() {
      ui.request("/kb").then(function (r) {
        items = (r.ok && r.data && r.data.knowledge_bases) || [];
        loadError = r.ok ? null : r;
        // The failed merge jobs: the contributions with the state "failed" of each shared knowledge base.
        return Promise.all(items.map(function (k) {
          return ui.request("/kb/" + encodeURIComponent(k.number) + "/contributions?state=failed").then(function (cr) {
            failed[String(k.number)] = (cr.ok && cr.data && cr.data.contributions) || [];
          });
        }));
      }).then(draw);
    }

    function failures(k) { return failed[String(k.number)] || []; }

    function draw() {
      var filtered = items.filter(function (k) {
        if (filterVal === "warn") return !k.active_moderators || !!k.reembed_required || failures(k).length > 0;
        return true;
      });
      // The knowledge bases without an active moderator come first (design Section 10.2). The sort is stable: the sequence of the server stays in each of the two groups.
      filtered.sort(function (a, b) { return (a.active_moderators > 0 ? 1 : 0) - (b.active_moderators > 0 ? 1 : 0); });
      host.innerHTML = '<section class="console-subcard claire-card" aria-label="' + ui.esc(tr("console.js.admin.kb.title")) + '">' +
        '<div class="console-card-head claire-card-head"><h2><span data-icon="folder"></span> ' + ui.esc(tr("console.js.admin.kb.title")) + "</h2>" +
        ui.renderChips({
          name: "admin-kb-filter",
          label: tr("console.js.admin.kb.filter_label"),
          value: filterVal,
          options: [
            { value: "all", label: tr("console.js.admin.kb.filter_all") },
            { value: "warn", label: tr("console.js.admin.kb.filter_warn") }
          ]
        }) + "</div>" +
        (loadError ? '<div class="claire-note warn"><span>' + ui.esc(ui.errorText(loadError)) + "</span></div>" : "") +
        ui.renderTable({
          caption: tr("console.js.admin.kb.title"),
          columns: [
            { label: ui.t("kb.dlg.col_kb"), render: function (k) { return '<a href="/kb/' + ui.esc(k.number) + '/"><strong>' + ui.esc(k.label) + '</strong></a> <code class="pref-mono">' + ui.esc(k.number) + "</code>"; } },
            { label: ui.t("kb.knowledge_type.label"), render: function (k) { return dlgs.knowledgeTypeTag(k.knowledge_type); } },
            { label: ui.t("kb.pub.col_mods"), render: function (k) {
              return k.active_moderators > 0 ? ui.esc(k.active_moderators) : '<span class="claire-tag warn">' + ui.esc(ui.t("kb.pub.no_mod_warn")) + "</span>";
            } },
            { label: ui.t("kb.dlg.col_state"), render: function (k) {
              var h = '<span class="claire-tag ' + (k.state === "archived" ? "warn" : "ok") + '">' + ui.esc(dlgs.stateLabel(k.state)) + "</span>";
              if (k.reembed_required) h += ' <span class="claire-tag danger">' + ui.esc(ui.t("kb.pub.reembed_badge")) + "</span>";
              if (failures(k).length) h += ' <span class="claire-tag danger">' + ui.esc(tr("console.js.admin.kb.failed_count", { count: failures(k).length })) + "</span>";
              return h;
            } },
            { label: ui.t("kb.pub.col_actions"), actions: true, render: function (k) {
              var menu = [
                { href: "/moderation/kb/" + encodeURIComponent(k.number), label: ui.t("kb.pub.kb_review_manage"), icon: "inbox" },
                { action: "assign_mod", value: k.number, label: tr("console.js.admin.kb.assign_mod"), icon: "user" },
                { action: "change_type", value: k.number, label: tr("kb.knowledge_type.change"), icon: "edit" }
              ];
              if (failures(k).length) menu.push({ action: "retry_merge", value: k.number, label: tr("console.js.admin.kb.retry_merge"), icon: "restore" });
              menu.push({ sep: true });
              menu.push({ action: "delete_kb", value: k.number, label: tr("console.js.admin.kb.delete_kb"), icon: "trash", danger: true });
              return ui.renderRowMenu(menu, k.label || k.number);
            } }
          ],
          rows: filtered
        }) + "</section>";
      ui.enhance(host);
      host.querySelectorAll('input[name="admin-kb-filter"]').forEach(function (inp) {
        inp.addEventListener("change", function () { filterVal = inp.value; draw(); });
      });
    }

    host.addEventListener("click", function (ev) {
      var mi = ev.target && ev.target.closest ? ev.target.closest("[data-menu-action]") : null;
      if (!mi) return;
      var act = mi.getAttribute("data-menu-action");
      var kbNum = mi.getAttribute("data-menu-value");
      var kb = items.find(function (k) { return String(k.number) === String(kbNum); }) || {};
      if (act === "assign_mod") {
        ui.openDialog({
          title: tr("console.js.admin.kb.assign_mod"),
          icon: "user",
          bodyHtml: '<label class="console-field claire-field" for="admin-assign-acc">' + ui.esc(tr("console.js.admin.kb.assign_prompt_label")) + '<input id="admin-assign-acc" class="pref-input claire-input" inputmode="numeric" pattern="[0-9]+" required/></label>',
          onSubmit: function (_d, body) {
            var acc = body.querySelector("#admin-assign-acc").value.trim();
            if (!acc) return false;
            // PUT /api/admin/kb/{number}/moderators/{account}: the account is its public number. The route needs a recent sign-in; the request helper opens the re-authentication dialog.
            return ui.request("/api/admin/kb/" + encodeURIComponent(kbNum) + "/moderators/" + encodeURIComponent(acc), { method: "PUT" }).then(function (res) {
              if (!res.ok) { ui.toast(ui.errorText(res), "danger"); return false; }
              load();
              return true;
            });
          }
        });
      } else if (act === "change_type") {
        dlgs.openKnowledgeTypeDialog({ kbNumber: kbNum, kbLabel: kb.label, current: kb.knowledge_type, onChanged: function () { load(); } });
      } else if (act === "retry_merge") {
        // POST /kb/{number}/contributions/{contribution}/retry for each failed contribution.
        Promise.all(failures(kb).map(function (c) {
          return ui.request("/kb/" + encodeURIComponent(kbNum) + "/contributions/" + encodeURIComponent(c.number) + "/retry", { method: "POST", body: {} });
        })).then(function (results) {
          var bad = results.filter(function (res) { return !res.ok || (res.data && res.data.contribution && res.data.contribution.state === "failed"); });
          if (bad.length) ui.toast(ui.errorText(bad[0].ok ? { error: bad[0].data.contribution.reason } : bad[0]), "danger");
          load();
        });
      } else if (act === "delete_kb") {
        dlgs.openDeleteKbDialog({ kbNumber: kbNum, kbLabel: kb.label, onDeleted: function () { load(); } });
      }
    });
    load();
  }

  // The review of the shadow records of the same-subject source merge (docs/implementation/EXPANSION.md §3.1): a person gives each record a verdict, and the report compares the model with the verdicts. Only this WebUI gives verdicts; the CLI of the agents reads only.
  function initExpansionReview(host) {
    var ui = window.ClaireUi;
    if (!host || !ui) return;
    var pendingOnly = true;

    function percent(value) { return value == null ? tr("console.js.admin.expansion_review.no_data") : (Math.round(Number(value) * 1000) / 10) + "%"; }
    function score(value) { return value == null ? "—" : Number(value).toFixed(2); }
    function link(title, url, fallback) {
      var text = ui.esc(title || fallback);
      return url ? '<a href="' + ui.esc(url) + '" target="_blank" rel="noopener noreferrer">' + text + "</a>" : text;
    }

    function render(data) {
      var report = data.report || {};
      var records = data.records || [];
      var h = '<section class="console-subcard claire-card no-split" aria-label="' + ui.esc(tr("console.js.admin.expansion_review.title")) + '">';
      h += '<h2><span data-icon="merge"></span> ' + ui.esc(tr("console.js.admin.expansion_review.title")) + "</h2>";
      h += '<p class="hint">' + ui.esc(tr("console.js.admin.expansion_review.desc")) + "</p>";
      if (data.mode !== "shadow") h += '<div class="claire-note warn" data-review-mode-off><span>' + ui.esc(tr("console.js.admin.expansion_review.mode_off")) + "</span></div>";
      h += '<div class="pref-stat-row" data-review-report>';
      h += '<div class="pref-stat"><b>' + ui.esc(report.records || 0) + "</b><span>" + ui.esc(tr("console.js.admin.expansion_review.records")) + "</span></div>";
      h += '<div class="pref-stat"><b>' + ui.esc(report.judged || 0) + "</b><span>" + ui.esc(tr("console.js.admin.expansion_review.judged")) + "</span></div>";
      h += '<div class="pref-stat"><b>' + ui.esc(report.pending || 0) + "</b><span>" + ui.esc(tr("console.js.admin.expansion_review.pending")) + "</span></div>";
      h += '<div class="pref-stat"><b data-metric="precision">' + ui.esc(percent(report.precision)) + "</b><span>" + ui.esc(tr("console.js.admin.expansion_review.precision")) + "</span></div>";
      h += '<div class="pref-stat"><b data-metric="recall">' + ui.esc(percent(report.recall)) + "</b><span>" + ui.esc(tr("console.js.admin.expansion_review.recall")) + "</span></div>";
      h += '<div class="pref-stat"><b data-metric="accuracy">' + ui.esc(percent(report.accuracy)) + "</b><span>" + ui.esc(tr("console.js.admin.expansion_review.accuracy")) + "</span></div>";
      h += "</div>";
      h += '<p class="hint">' + ui.esc(tr("console.js.admin.expansion_review.matrix", { tp: report.tp || 0, fp: report.fp || 0, fn: report.fn || 0, tn: report.tn || 0 })) + "</p>";
      h += '<div class="pref-row"><div class="pref-row-label"><label for="admin-expansion-pending-only"><strong>' + ui.esc(tr("console.js.admin.expansion_review.pending_only")) + '</strong></label></div><input id="admin-expansion-pending-only" type="checkbox" switch data-review-pending ' + (pendingOnly ? "checked" : "") + "/></div>";
      if (!records.length) {
        h += '<p class="hint" data-review-empty>' + ui.esc(tr("console.js.admin.expansion_review.empty")) + "</p>";
      }
      h += '<ul class="claire-review-list" data-review-list>';
      records.forEach(function (r) {
        var model = r.same_subject ? tr("console.js.admin.expansion_review.model_same") : tr("console.js.admin.expansion_review.model_different");
        var verdict = r.verdict ? tr("console.js.admin.expansion_review.verdict_" + r.verdict) : tr("console.js.admin.expansion_review.verdict_none");
        h += '<li data-record="' + ui.esc(r.id) + '">';
        h += "<div><strong>#" + ui.esc(r.id) + "</strong> " + link(r.parent.title, r.parent.url, r.parent.document_id) + " → " + link(r.child.title, r.child.url, r.child.url) + "</div>";
        h += '<div class="hint">' + ui.esc(tr("console.js.admin.expansion_review.model", { model: model, relevance: score(r.relevance), quality: score(r.quality) })) + "</div>";
        if (r.reason) h += '<div class="hint">' + ui.esc(tr("console.js.admin.expansion_review.reason", { reason: r.reason })) + "</div>";
        h += '<div data-review-verdict>' + ui.esc(tr("console.js.admin.expansion_review.verdict", { verdict: verdict })) + (r.verdict_note ? " — " + ui.esc(r.verdict_note) : "") + "</div>";
        h += '<div class="console-actions claire-actions">';
        h += '<input type="text" class="pref-input claire-input" maxlength="500" data-review-note placeholder="' + ui.esc(tr("console.js.admin.expansion_review.note")) + '" value="' + ui.esc(r.verdict_note || "") + '"/>';
        h += '<button type="button" class="pref-btn pref-btn-sm" data-verdict="same">' + ui.esc(tr("console.js.admin.expansion_review.set_same")) + "</button>";
        h += '<button type="button" class="pref-btn pref-btn-sm" data-verdict="different">' + ui.esc(tr("console.js.admin.expansion_review.set_different")) + "</button>";
        if (r.verdict) h += '<button type="button" class="pref-btn pref-btn-danger pref-btn-sm" data-verdict="clear">' + ui.esc(tr("console.js.admin.expansion_review.clear")) + "</button>";
        h += "</div></li>";
      });
      h += "</ul></section>";
      host.innerHTML = h;
      ui.enhance(host);
      host.querySelector("[data-review-pending]").addEventListener("change", function (ev) {
        pendingOnly = ev.target.checked;
        load();
      });
      host.querySelectorAll("[data-verdict]").forEach(function (btn) {
        btn.addEventListener("click", function () {
          var item = btn.closest("[data-record]");
          var value = btn.getAttribute("data-verdict");
          var note = item.querySelector("[data-review-note]").value;
          ui.request("/api/admin/expansion/shadow/" + encodeURIComponent(item.getAttribute("data-record")) + "/verdict", {
            method: "PUT",
            body: { verdict: value === "clear" ? null : value, note: value === "clear" ? "" : note }
          }).then(function (res) {
            if (!res.ok) { ui.toast(ui.errorText(res), "danger"); return; }
            ui.toast(tr("console.js.admin.expansion_review.saved"), "success");
            load();
          });
        });
      });
    }

    function load() {
      ui.request("/api/admin/expansion/shadow?limit=100" + (pendingOnly ? "&pending=1" : "")).then(function (res) {
        if (!res.ok) {
          host.innerHTML = '<section class="console-subcard claire-card"><h2><span data-icon="merge"></span> ' + ui.esc(tr("console.js.admin.expansion_review.title")) + '</h2><div class="claire-note warn"><span>' + ui.esc(ui.errorText(res)) + "</span></div></section>";
          ui.enhance(host);
          return;
        }
        render(res.data || {});
      });
    }
    load();
  }

  // /moderation/themes (design Section 10.3): the themes, the last judgement run, the thresholds, the reputation numbers, the adjustments of Section 8.3, and the agreement of decisions.
  function initAdminThemesV2(host) {
    var ui = window.ClaireUi;
    var dlgs = window.ClaireKbDialogs;
    if (!host || !ui || !dlgs) return;
    var activeTab = "judge";
    var selectedThemeNum = "";
    var lastReport = null;

    // The catalog text of a setting key or of a report field; a key without text shows as the key.
    function settingLabel(prefix, name) {
      var key = prefix + name;
      var text = tr(key);
      return text === key ? String(name) : text;
    }

    // "min" is the lowest value that the server takes; the default is 0.
    function numberField(id, label, value, step, min) {
      return '<label class="console-field claire-field" for="' + id + '">' + ui.esc(label) + '<input id="' + id + '" type="number" class="pref-input claire-input" min="' + (min || "0") + '" step="' + step + '" value="' + ui.esc(value == null ? "" : value) + '"/></label>';
    }
    function percent(value) { return value == null ? "—" : Math.round(Number(value) * 100) + "%"; }
    function fail(res) { ui.toast(ui.errorText(res), "danger"); }

    function load() {
      Promise.all([
        ui.request("/api/admin/themes"),
        ui.request("/api/admin/reputations"),
        ui.request("/api/admin/auto-contribution")
      ]).then(function (all) {
        var thRes = all[0], repRes = all[1], autoRes = all[2];
        var data = (thRes.ok && thRes.data) || {};
        var themes = data.themes || [];
        var cfg = data.settings || {};
        var agreement = data.decision_agreement || [];
        var repSettings = (repRes.ok && repRes.data && repRes.data.settings) || {};
        var accounts = (repRes.ok && repRes.data && repRes.data.accounts) || [];
        var autoSettings = (autoRes.ok && autoRes.data && autoRes.data.settings) || {};
        var levels = { good: 0, limited: 0, low: 0 };
        accounts.forEach(function (a) { if (levels[a.level] != null) levels[a.level] += 1; });
        if (!themes.some(function (x) { return String(x.number) === selectedThemeNum; })) selectedThemeNum = themes.length ? String(themes[0].number) : "";
        var curTh = themes.find(function (x) { return String(x.number) === selectedThemeNum; }) || null;
        var lastRun = data.last_run_at ? new Date(Number(data.last_run_at) * 1000).toLocaleString(document.documentElement.lang || undefined) : "—";

        var judgeHtml = '<section class="console-subcard claire-card">' +
          '<div class="console-card-head claire-card-head"><h2><span data-icon="bolt"></span> ' + ui.esc(tr("console.js.admin.themes.tab_judge")) + "</h2>" +
          '<button type="button" class="pref-btn pref-btn-accent claire-btn pri" id="adm-th-run-now">' + ui.esc(tr("console.js.admin.themes.run_now")) + "</button></div>" +
          (thRes.ok ? "" : '<div class="claire-note warn"><span>' + ui.esc(ui.errorText(thRes)) + "</span></div>") +
          '<div class="pref-stat-row claire-kpis">' +
          '<div class="pref-stat claire-kpi"><strong>' + ui.esc(themes.filter(function (x) { return x.state === "active"; }).length) + "</strong><span>" + ui.esc(tr("console.js.admin.themes.kpi_themes")) + "</span></div>" +
          '<div class="pref-stat claire-kpi"><strong>' + ui.esc(levels.good) + " / " + ui.esc(levels.limited) + " / " + ui.esc(levels.low) + "</strong><span>" + ui.esc(tr("console.js.admin.themes.kpi_levels")) + "</span></div>" +
          '<div class="pref-stat claire-kpi"><strong>' + ui.esc(lastRun) + "</strong><span>" + ui.esc(tr("console.js.admin.themes.kpi_last_run")) + "</span></div>" +
          "</div>" +
          (lastReport ? '<div class="claire-note ok"><span>' + ui.esc(tr("console.js.admin.themes.run_done")) + " " + ui.esc(Object.keys(lastReport).map(function (k) { return settingLabel("console.js.admin.themes.report.", k) + ": " + (typeof lastReport[k] === "object" ? JSON.stringify(lastReport[k]) : lastReport[k]); }).join(" · ")) + "</span></div>" : "") +
          '<form id="adm-th-thresh-form" class="console-form-grid">' +
          numberField("th-tm", tr("console.js.admin.themes.th_theme_match"), cfg.theme_match_threshold, "0.01") +
          numberField("th-dm", tr("console.js.admin.themes.th_doc_match"), cfg.document_match_threshold, "0.01") +
          numberField("th-sm", tr("console.js.admin.themes.th_seed_match"), cfg.seed_match_threshold, "0.01") +
          numberField("th-am", tr("console.js.admin.themes.th_auto_min"), autoSettings.auto_match_threshold, "0.01") +
          numberField("th-ar", tr("console.js.admin.themes.th_auto_run_documents"), autoSettings.auto_run_documents, "1") +
          // The keys of contributions.LIMITS are integers of 1 or more; "remote_revision_min_seconds" is 0 or more (contributions._LIMIT_MINIMUM).
          numberField("th-sx", tr("console.js.admin.themes.th_submission_max_documents"), cfg.submission_max_documents, "1", "1") +
          // The limits for contributions that accounts of other servers offer (KNOWLEDGE_FEDERATION_DESIGN.md KS2, KS3): keys of PUT /api/admin/themes/settings.
          numberField("th-ra", tr("console.js.admin.themes.th_remote_account_pending_limit"), cfg.remote_account_pending_limit, "1", "1") +
          numberField("th-rd", tr("console.js.admin.themes.th_remote_domain_pending_limit"), cfg.remote_domain_pending_limit, "1", "1") +
          numberField("th-ro", tr("console.js.admin.themes.th_remote_domain_daily_offers"), cfg.remote_domain_daily_offers, "1", "1") +
          // The limits of the knowledge federation for each other server (KNOWLEDGE_FEDERATION.md Section 14.1): keys of PUT /api/admin/themes/settings.
          numberField("th-da", tr("console.js.admin.themes.th_remote_domain_daily_activities"), cfg.remote_domain_daily_activities, "1", "1") +
          numberField("th-db", tr("console.js.admin.themes.th_remote_domain_daily_bytes"), cfg.remote_domain_daily_bytes, "1", "1") +
          numberField("th-kf", tr("console.js.admin.themes.th_kb_followers_limit"), cfg.kb_followers_limit, "1", "1") +
          numberField("th-df", tr("console.js.admin.themes.th_remote_domain_followers_limit"), cfg.remote_domain_followers_limit, "1", "1") +
          numberField("th-rm", tr("console.js.admin.themes.th_remote_revision_min_seconds"), cfg.remote_revision_min_seconds, "1", "0") +
          numberField("th-dv", tr("console.js.admin.themes.th_remote_domain_daily_revisions"), cfg.remote_domain_daily_revisions, "1", "1") +
          '<div class="console-actions claire-actions" style="grid-column:1 / -1"><button type="submit" class="pref-btn pref-btn-accent claire-btn pri">' + ui.esc(tr("console.js.admin.themes.save_thresholds")) + "</button></div></form></section>";

        var adjLeft = '<div class="claire-list-col">' +
          '<button type="button" class="pref-btn pref-btn-sm claire-btn claire-btn-sm" id="adm-th-create">' + ui.esc(tr("console.js.admin.themes.create_theme")) + "</button>" +
          (themes.length ? themes.map(function (th) {
            return '<button type="button" class="claire-list-item' + (curTh && String(curTh.number) === String(th.number) ? " active" : "") + '" data-pick-th="' + ui.esc(th.number) + '">' +
              '<div class="claire-list-item-row"><strong>' + ui.esc(th.label) + "</strong>" + dlgs.knowledgeTypeTag(th.knowledge_type) + "</div>" +
              '<div class="claire-list-item-sub"><span>' + ui.esc(th.number) + " · " + ui.esc(tr("console.js.admin.themes.state_" + th.state)) + (th.label_locked ? " · " + ui.esc(tr("console.js.admin.themes.label_locked")) : "") + "</span></div></button>";
          }).join("") : '<div class="console-subcard claire-card"><p class="hint claire-hint">' + ui.esc(ui.t("kb.ui.empty_list")) + "</p></div>") + "</div>";

        var adjRight = '<div class="claire-detail-col"><div class="console-subcard claire-card">' + (curTh ? (
          '<div class="console-card-head claire-card-head"><h3><a href="/themes/' + ui.esc(curTh.number) + '/">' + ui.esc(curTh.label) + '</a></h3><div class="console-actions claire-actions">' +
          (curTh.label_locked ? '<button type="button" class="pref-btn pref-btn-sm claire-btn sec claire-btn-sm" id="adm-th-unlock">' + ui.esc(tr("console.js.admin.themes.unlock_label")) + "</button>" : "") +
          (curTh.state !== "merged" ? '<button type="button" class="pref-btn pref-btn-danger pref-btn-sm claire-btn danger claire-btn-sm" id="adm-th-merge">' + ui.esc(tr("console.js.admin.themes.merge_btn")) + "</button>" : "") +
          "</div></div>" +
          '<form id="adm-th-rename-form" class="console-form-row"><label class="console-field claire-field" for="adm-th-label" style="flex:1;min-width:220px">' + ui.esc(tr("console.js.admin.themes.rename_label")) + '<input id="adm-th-label" class="pref-input claire-input" value="' + ui.esc(curTh.label) + '" required/></label><button type="submit" class="pref-btn claire-btn sec">' + ui.esc(tr("console.js.admin.themes.rename_btn")) + "</button></form>" +
          ui.renderTable({
            caption: curTh.label,
            columns: [
              // A shared knowledge base of another server ("remote" true) has the link to its origin and the domain of its server; its adjustments use "remote_id".
              { label: ui.t("kb.dlg.col_kb"), render: function (k) {
                if (k.remote) return '<a href="' + ui.esc(k.url) + '" target="_blank" rel="noopener noreferrer">' + ui.esc(k.label) + '</a> <span class="claire-hint">' + ui.esc(k.server) + "</span>";
                return '<a href="/kb/' + ui.esc(k.number) + '/">' + ui.esc(k.label) + '</a> <span class="claire-hint">' + ui.esc(ui.t(k.is_personal ? "themes.kb_personal" : "themes.kb_shared")) + "</span>";
              } },
              { label: tr("console.js.admin.themes.col_membership"), render: function (k) { return '<span class="claire-tag' + (k.excluded ? " danger" : (k.pinned ? " ok" : "")) + '">' + ui.esc(tr(k.excluded ? "console.js.admin.themes.member_excluded" : (k.pinned ? "console.js.admin.themes.member_pinned" : "console.js.admin.themes.member_computed"))) + "</span>" + (k.score != null ? ' <span class="claire-hint">' + ui.esc(Number(k.score).toFixed(2)) + "</span>" : ""); } },
              { label: ui.t("kb.pub.col_actions"), actions: true, render: function (k) {
                // A merged theme takes no adjustment (409 "theme_merged"): it has no membership menu.
                if (curTh.state === "merged") return "—";
                var menu = [];
                var ref = k.remote ? "remote:" + k.remote_id : k.number;
                if (!k.pinned) menu.push({ action: "th_pin", value: ref, label: tr("console.js.admin.themes.pin_kb"), icon: "plus" });
                if (!k.excluded) menu.push({ action: "th_exclude", value: ref, label: tr("console.js.admin.themes.exclude_kb"), icon: "block", danger: true });
                if (k.pinned || k.excluded) menu.push({ action: "th_computed", value: ref, label: tr("console.js.admin.themes.reset_kb"), icon: "restore" });
                return ui.renderRowMenu(menu, k.label);
              } }
            ],
            rows: curTh.knowledge_bases || []
          }) +
          (curTh.state !== "merged" ? '<form id="adm-th-pin-form" class="console-form-row"><label class="console-field claire-field" for="adm-th-pin-kb" style="flex:1;min-width:220px">' + ui.esc(tr("console.js.admin.themes.pin_number_label")) + '<input id="adm-th-pin-kb" class="pref-input claire-input" inputmode="numeric" pattern="[0-9]+" required/></label><button type="submit" class="pref-btn claire-btn sec">' + ui.esc(tr("console.js.admin.themes.pin_kb")) + "</button></form>" : "")
        ) : '<p class="hint claire-hint">' + ui.esc(ui.t("kb.ui.empty_list")) + "</p>") + "</div></div>";

        var agreeHtml = '<section class="console-subcard claire-card"><h2><span data-icon="chart"></span> ' + ui.esc(tr("console.js.admin.themes.tab_agree")) + "</h2>" +
          '<p class="hint claire-hint">' + ui.esc(tr("console.js.admin.themes.agree_hint")) + "</p>" + ui.renderTable({
          caption: tr("console.js.admin.themes.tab_agree"),
          columns: [
            { label: ui.t("kb.dlg.col_kb"), render: function (a) { return a.knowledge_base ? '<a href="/kb/' + ui.esc(a.knowledge_base) + '/">' + ui.esc(a.label || a.knowledge_base) + "</a>" : "—"; } },
            { label: tr("console.js.admin.themes.col_decisions"), render: function (a) { return ui.esc(a.decisions); } },
            { label: tr("console.js.admin.themes.col_agreement"), render: function (a) { return ui.esc(percent(a.agreement)); } }
          ],
          rows: agreement
        }) + "</section>";

        var repHtml = '<section class="console-subcard claire-card"><h2><span data-icon="users"></span> ' + ui.esc(tr("console.js.admin.themes.tab_rep")) + "</h2>" +
          '<form id="adm-rep-form" class="console-form-grid">' + Object.keys(repSettings).map(function (key) {
          return '<label class="console-field claire-field" for="rep-' + ui.esc(key) + '">' + ui.esc(settingLabel("console.js.admin.themes.rep.", key)) + '<input id="rep-' + ui.esc(key) + '" data-rep-key="' + ui.esc(key) + '" type="number" class="pref-input claire-input" min="0" step="any" value="' + ui.esc(repSettings[key]) + '"/></label>';
        }).join("") + '<div class="console-actions claire-actions" style="grid-column:1 / -1"><button type="submit" class="pref-btn pref-btn-accent claire-btn pri">' + ui.esc(tr("console.js.admin.themes.rep_save")) + "</button></div></form>" +
          ui.renderTable({
            caption: tr("console.js.admin.themes.tab_rep"),
            columns: [
              { label: ui.t("kb.dlg.col_account"), render: function (a) { return ui.esc(a.display_name || a.account) + ' <span class="claire-hint">' + ui.esc(a.account) + "</span>"; } },
              { label: tr("console.js.admin.themes.col_level"), render: function (a) { return '<span class="claire-tag ' + (a.level === "good" ? "ok" : (a.level === "limited" ? "warn" : "danger")) + '">' + ui.esc(ui.t("kb.level." + a.level)) + "</span>"; } },
              { label: ui.t("console.js.kb.rep_fiction_share"), render: function (a) { return ui.esc(percent(a.fiction_share)) + ' <span class="claire-hint">' + ui.esc(a.fiction_like_documents || 0) + " / " + ui.esc(a.visible_documents || 0) + "</span>"; } },
              { label: ui.t("console.js.kb.rep_approval_180"), render: function (a) { return ui.esc(percent(a.approval_rate)) + ' <span class="claire-hint">' + ui.esc(a.decisions || 0) + "</span>"; } }
            ],
            rows: accounts
          }) + "</section>";

        host.innerHTML = ui.renderTabs({
          id: "admin-themes-tabs",
          ariaLabel: tr("console.js.admin.themes.title"),
          active: activeTab,
          tabs: [
            { id: "judge", label: tr("console.js.admin.themes.tab_judge"), icon: "bolt", html: judgeHtml },
            { id: "adj", label: tr("console.js.admin.themes.tab_adj"), icon: "folder", html: '<div class="claire-split">' + adjLeft + adjRight + "</div>" },
            { id: "agree", label: tr("console.js.admin.themes.tab_agree"), icon: "chart", html: agreeHtml },
            { id: "rep", label: tr("console.js.admin.themes.tab_rep"), icon: "users", html: repHtml }
          ]
        });
        ui.enhance(host);

        var tRoot = host.querySelector("#admin-themes-tabs");
        if (tRoot) tRoot.addEventListener("claire:tabchange", function (ev) { activeTab = ev.detail.tab; });
        var runBtn = host.querySelector("#adm-th-run-now");
        if (runBtn) runBtn.addEventListener("click", function () {
          runBtn.disabled = true;
          ui.request("/api/admin/themes/judge", { method: "POST", body: {} }).then(function (rr) {
            if (!rr.ok) { runBtn.disabled = false; fail(rr); return; }
            lastReport = (rr.data && rr.data.report) || {};
            load();
          });
        });
        var tForm = host.querySelector("#adm-th-thresh-form");
        if (tForm) tForm.addEventListener("submit", function (ev) {
          ev.preventDefault();
          // The three thresholds of the judgement and each key of contributions.LIMITS are keys of PUT /api/admin/themes/settings; "auto_match_threshold" and "auto_run_documents" are keys of PUT /api/admin/auto-contribution. An empty field is not sent: the server keeps its value.
          var put = function (body, id, key) {
            var raw = host.querySelector(id).value;
            if (raw !== "") body[key] = Number(raw);
          };
          var themeBody = {}, autoBody = {};
          put(themeBody, "#th-tm", "theme_match_threshold");
          put(themeBody, "#th-dm", "document_match_threshold");
          put(themeBody, "#th-sm", "seed_match_threshold");
          put(themeBody, "#th-sx", "submission_max_documents");
          put(themeBody, "#th-ra", "remote_account_pending_limit");
          put(themeBody, "#th-rd", "remote_domain_pending_limit");
          put(themeBody, "#th-ro", "remote_domain_daily_offers");
          put(themeBody, "#th-da", "remote_domain_daily_activities");
          put(themeBody, "#th-db", "remote_domain_daily_bytes");
          put(themeBody, "#th-kf", "kb_followers_limit");
          put(themeBody, "#th-df", "remote_domain_followers_limit");
          put(themeBody, "#th-rm", "remote_revision_min_seconds");
          put(themeBody, "#th-dv", "remote_domain_daily_revisions");
          put(autoBody, "#th-am", "auto_match_threshold");
          put(autoBody, "#th-ar", "auto_run_documents");
          Promise.all([
            Object.keys(themeBody).length ? ui.request("/api/admin/themes/settings", { method: "PUT", body: themeBody }) : Promise.resolve({ ok: true }),
            Object.keys(autoBody).length ? ui.request("/api/admin/auto-contribution", { method: "PUT", body: autoBody }) : Promise.resolve({ ok: true })
          ]).then(function (results) {
            var bad = results.find(function (rr) { return !rr.ok; });
            if (bad) { fail(bad); return; }
            ui.toast(tr("console.js.admin.themes.saved"), "ok");
            load();
          });
        });
        var repForm = host.querySelector("#adm-rep-form");
        if (repForm) repForm.addEventListener("submit", function (ev) {
          ev.preventDefault();
          var body = {};
          repForm.querySelectorAll("[data-rep-key]").forEach(function (input) {
            var key = input.getAttribute("data-rep-key");
            if (input.value !== "" && Number(input.value) !== Number(repSettings[key])) body[key] = Number(input.value);
          });
          if (!Object.keys(body).length) return;
          ui.request("/api/admin/reputations/settings", { method: "PUT", body: body }).then(function (rr) {
            if (!rr.ok) { fail(rr); return; }
            ui.toast(tr("console.js.admin.themes.saved"), "ok");
            load();
          });
        });
        host.querySelectorAll("[data-pick-th]").forEach(function (b) {
          b.addEventListener("click", function () { selectedThemeNum = b.getAttribute("data-pick-th"); load(); });
        });
        var cBtn = host.querySelector("#adm-th-create");
        if (cBtn) cBtn.addEventListener("click", function () {
          ui.openDialog({
            title: tr("console.js.admin.themes.create_theme"),
            icon: "plus",
            bodyHtml: '<p class="hint claire-hint">' + ui.esc(tr("console.js.admin.themes.create_hint")) + "</p>" +
              '<label class="console-field claire-field" for="new-ct-lbl">' + ui.esc(tr("console.js.admin.themes.rename_label")) + '<input id="new-ct-lbl" class="pref-input claire-input" required/></label>' +
              '<label class="console-field claire-field" for="new-ct-knowledge-type">' + ui.esc(ui.t("kb.knowledge_type.label")) + '<select id="new-ct-knowledge-type" class="pref-select claire-select"><option value="real">' + ui.esc(ui.t("kb.knowledge_type.real")) + '</option><option value="fictional">' + ui.esc(ui.t("kb.knowledge_type.fictional")) + "</option></select></label>",
            onSubmit: function (_d, body) {
              var lbl = body.querySelector("#new-ct-lbl").value.trim();
              if (!lbl) return false;
              return ui.request("/api/admin/themes", { method: "POST", body: { label: lbl, knowledge_type: body.querySelector("#new-ct-knowledge-type").value } }).then(function (rr) {
                if (!rr.ok) { fail(rr); return false; }
                if (rr.data && rr.data.theme) selectedThemeNum = String(rr.data.theme.number);
                load();
                return true;
              });
            }
          });
        });
        function patchTheme(body) {
          ui.request("/api/admin/themes/" + encodeURIComponent(curTh.number), { method: "PATCH", body: body }).then(function (rr) {
            if (!rr.ok) { fail(rr); return; }
            load();
          });
        }
        function setMembership(kb, membership) {
          // "remote:<id>" is a shared knowledge base of another server (the row menu makes this value).
          var member = String(kb).indexOf("remote:") === 0 ? "/remote-knowledge-bases/" + encodeURIComponent(String(kb).slice(7)) : "/knowledge-bases/" + encodeURIComponent(kb);
          ui.request("/api/admin/themes/" + encodeURIComponent(curTh.number) + member, { method: "PUT", body: { membership: membership } }).then(function (rr) {
            if (!rr.ok) { fail(rr); return; }
            load();
          });
        }
        var unlockBtn = host.querySelector("#adm-th-unlock");
        if (unlockBtn && curTh) unlockBtn.addEventListener("click", function () { patchTheme({ label_locked: false }); });
        var renameForm = host.querySelector("#adm-th-rename-form");
        if (renameForm && curTh) renameForm.addEventListener("submit", function (ev) {
          ev.preventDefault();
          var label = host.querySelector("#adm-th-label").value.trim();
          if (label) patchTheme({ label: label });
        });
        var pinForm = host.querySelector("#adm-th-pin-form");
        if (pinForm && curTh) pinForm.addEventListener("submit", function (ev) {
          ev.preventDefault();
          var kb = host.querySelector("#adm-th-pin-kb").value.trim();
          if (kb) setMembership(kb, "pinned");
        });
        var mergeBtn = host.querySelector("#adm-th-merge");
        if (mergeBtn && curTh) mergeBtn.addEventListener("click", function () {
          dlgs.openMergeThemeDialog({ sourceTheme: curTh, themes: themes, onMerged: function (merged) { selectedThemeNum = merged && merged.theme ? String(merged.theme.number) : ""; load(); } });
        });
        host.querySelectorAll("[data-menu-action]").forEach(function (mi) {
          mi.addEventListener("click", function () {
            if (!curTh) return;
            var act = mi.getAttribute("data-menu-action");
            setMembership(mi.getAttribute("data-menu-value"), act === "th_pin" ? "pinned" : (act === "th_exclude" ? "excluded" : "computed"));
          });
        });
      });
    }
    load();
  }

  function initServerSection() {
    var toggle = document.getElementById("admin-member-invites-toggle");
    var resultEl = document.getElementById("server-invites-policy-result");
    if (!toggle || !window.ClaireUi) return;
    toggle.addEventListener("change", function () {
      var next = Boolean(toggle.checked);
      toggle.disabled = true;
      window.ClaireUi.request("/api/admin/server/invites", {
        method: "POST",
        body: { member_invites_enabled: next }
      }).then(function (r) {
        toggle.disabled = false;
        if (!r.ok || !r.data) {
          toggle.checked = !next;
          var errMsg = r.error === "reauth_cancelled" ? tr("console.js.admin.server.invites_reauth_cancelled") : (window.ClaireUi.errorText(r) || tr("console.js.admin.server.invites_failed"));
          showStatusBanner(resultEl, errMsg, true);
          return;
        }
        var saved = Boolean(r.data.member_invites_enabled);
        toggle.checked = saved;
        var okMsg = tr(saved ? "console.js.admin.server.invites_enabled" : "console.js.admin.server.invites_disabled");
        showStatusBanner(resultEl, okMsg, false);
        window.ClaireUi.toast(okMsg, "ok");
      });
    });
  }

  // /admin/webmcp: the server policy switch of WebMCP. Only this browser keeps the state (localStorage); the server side is not implemented.
  function initWebmcpSection() {
    var webmcpToggle = document.getElementById("admin-webmcp-enabled");
    var webmcpHint = document.getElementById("admin-webmcp-policy-hint");
    if (!webmcpToggle) return;
    try {
      var saved = window.localStorage.getItem("claire_admin_webmcp_enabled");
      if (saved === "0") webmcpToggle.checked = false;
    } catch (_e) {}
    var syncHint = function () {
      if (!webmcpHint) return;
      webmcpHint.textContent = webmcpToggle.checked ? tr("console.js.admin.webmcp.on") : tr("console.js.admin.webmcp.off");
    };
    syncHint();
    webmcpToggle.addEventListener("change", function () {
      try {
        window.localStorage.setItem("claire_admin_webmcp_enabled", webmcpToggle.checked ? "1" : "0");
      } catch (_e) {}
      syncHint();
    });
  }

  function initAdminSection(bootstrap) {
    var panels = window.ClaireManagePanels || {};
    var section = bootstrap.section;
    if (section === "server") {
      initServerSection();
    } else if (section === "providers") {
      var provHost = document.getElementById("admin-providers-host");
      if (provHost && typeof panels.renderProviderManager === "function") {
        panels.renderProviderManager(provHost, { showClose: false, canWrite: true, isAdmin: true });
      }
      initProvidersEmbedCard();
    } else if (section === "limits") {
      var limHost = document.getElementById("admin-limits-host");
      if (limHost && typeof panels.renderLimitManager === "function") {
        panels.renderLimitManager(limHost, { showClose: false, canWrite: true, isAdmin: true });
      }
      initExpansionReview(document.getElementById("admin-expansion-review-host"));
    } else if (section === "themes") {
      var themeHost = document.getElementById("admin-themes-host");
      if (themeHost) initAdminThemesV2(themeHost);
    } else if (section === "kb") {
      initAdminKbSection();
    } else if (section === "tokens") {
      var tokHost = document.getElementById("admin-tokens-host");
      if (tokHost && typeof panels.renderTokenManager === "function") {
        panels.renderTokenManager(tokHost, { showClose: false, canWrite: true, isAdmin: true });
      }
    } else if (section === "accounts") {
      initAccountsSection(bootstrap);
    } else if (section === "idp") {
      initIdpSection(bootstrap);
    } else if (section === "audit") {
      initAuditSection();
    } else if (section === "mcp") {
      initMcpSection();
    } else if (section === "webmcp") {
      initWebmcpSection();
    } else if (section === "federation") {
      initFederationSection();
    } else if (section === "storage") {
      initStorageSection();
    } else if (section === "plugins") {
      initPluginsSection();
    }
  }

  function init() {
    bindThemeSelector();
    bindReauthModal();
    var bootstrap = parseBootstrap();
    if (!bootstrap || (bootstrap.area !== "admin" && bootstrap.area !== "moderation")) return;
    initAdminSection(bootstrap);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
