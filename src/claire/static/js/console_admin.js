/* Claire Admin Console controller (/admin/*). */
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
            errEl.textContent = (res.body && (res.body.detail || res.body.error)) ? String(res.body.detail || res.body.error) : tr("console.js.admin.reauth.failed");
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
        var performPatch = function () {
          fetch("/api/admin/accounts", {
            method: "PATCH",
            credentials: "same-origin",
            headers: { "Content-Type": "application/json", "Accept": "application/json" },
            body: JSON.stringify({ public_id: publicId, action: action })
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
        performPatch();
      });
    }

    var invitesTbody = document.getElementById("admin-invites-tbody");
    var inviteForm = document.getElementById("admin-invite-create-form");
    var inviteStatusEl = document.getElementById("admin-invite-result") || document.getElementById("admin-invite-status");

    function loadInvites() {
      if (!invitesTbody || bootstrap.mode !== "network") return;
      fetch("/api/invites?page=true", {
        method: "GET",
        credentials: "same-origin",
        headers: { "Accept": "application/json" }
      }).then(function (resp) {
        return resp.json().catch(function () { return {}; }).then(function (body) {
          return { status: resp.status, body: body };
        });
      }).then(function (res) {
        if (res.status !== 200 || !res.body || !Array.isArray(res.body.items)) return;
        if (res.body.items.length === 0) {
          invitesTbody.innerHTML = '<tr><td colspan="7" class="empty">' + tr("console.js.admin.invites.empty") + '</td></tr>';
          return;
        }
        invitesTbody.innerHTML = res.body.items.map(function (inv) {
          var statusBadge = inv.status === "active" ? '<span class="badge badge-ok">active</span>' : '<span class="badge badge-muted">' + escHtml(inv.status) + "</span>";
          var revokeBtn = inv.status === "active" ? '<button type="button" class="pref-btn pref-btn-danger" data-invite-revoke="' + escHtml(inv.code) + '">' + tr("console.js.admin.invites.revoke") + '</button>' : "—";
          return '<tr data-invite-code="' + escHtml(inv.code) + '"><td><code>' + escHtml(inv.public_id || "—") + "</code></td><td><code>" + escHtml(inv.code) + "</code></td><td>" + statusBadge + "</td><td>" + escHtml(inv.uses_count) + " / " + escHtml(inv.max_uses) + "</td><td>" + escHtml(inv.reserved_username || "—") + "</td><td>" + escHtml(inv.note || "—") + "</td><td>" + revokeBtn + "</td></tr>";
        }).join("");
      }).catch(function () {});
    }

    if (invitesTbody && bootstrap.mode === "network") {
      loadInvites();
      invitesTbody.addEventListener("click", function (ev) {
        var btn = ev.target.closest("[data-invite-revoke]");
        if (!btn) return;
        var code = btn.getAttribute("data-invite-revoke") || "";
        if (!code) return;
        fetch("/api/invites", {
          method: "PATCH",
          credentials: "same-origin",
          headers: { "Content-Type": "application/json", "Accept": "application/json" },
          body: JSON.stringify({ code: code, action: "revoke" })
        }).then(function (resp) {
          return resp.json().catch(function () { return {}; }).then(function (body) {
            return { status: resp.status, body: body };
          });
        }).then(function (res) {
          if (res.status === 200) {
            showStatusBanner(inviteStatusEl, tr("console.js.admin.invites.revoked") + " " + code, false);
            loadInvites();
            return;
          }
          showStatusBanner(inviteStatusEl, tr("console.js.admin.invites.revoke_failed") + " " + ((res.body && (res.body.detail || res.body.error)) || res.status), true);
        }).catch(function () {
          showStatusBanner(inviteStatusEl, tr("console.js.admin.invites.revoke_network_error"), true);
        });
      });
    }

    if (inviteForm) {
      inviteForm.addEventListener("submit", function (ev) {
        ev.preventDefault();
        var fd = new FormData(inviteForm);
        var maxUses = parseInt(String(fd.get("max_uses") || "1"), 10) || 1;
        var reservedUsername = String(fd.get("reserved_username") || "").trim() || null;
        var note = String(fd.get("note") || "").trim() || null;
        var payload = { max_uses: maxUses, note: note };
        if (reservedUsername) payload.reserved_username = reservedUsername;
        fetch("/api/invites", {
          method: "POST",
          credentials: "same-origin",
          headers: { "Content-Type": "application/json", "Accept": "application/json" },
          body: JSON.stringify(payload)
        }).then(function (resp) {
          return resp.json().catch(function () { return {}; }).then(function (body) {
            return { status: resp.status, body: body };
          });
        }).then(function (res) {
          if ((res.status === 200 || res.status === 201) && res.body) {
            showStatusBanner(inviteStatusEl, tr("console.js.admin.invites.issued") + " " + (res.body.code || ""), false);
            inviteForm.reset();
            loadInvites();
            return;
          }
          showStatusBanner(inviteStatusEl, tr("console.js.admin.invites.issue_failed") + " " + ((res.body && (res.body.detail || res.body.error)) || res.status), true);
        }).catch(function () {
          showStatusBanner(inviteStatusEl, tr("console.js.admin.invites.issue_network_error"), true);
        });
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
  }

  function initIdpSection(bootstrap) {
    var providers = Array.isArray(bootstrap.idp_providers) ? bootstrap.idp_providers.slice() : [];
    var origin = bootstrap.origin || window.location.origin;
    var cardsContainer = document.getElementById("admin-idp-cards");
    var tbody = document.getElementById("pref-idp-tbody") || document.getElementById("pref-idp-providers-tbody");
    var loginPreview = document.getElementById("pref-idp-login-preview");
    var statusEl = document.getElementById("admin-idp-result") || document.getElementById("admin-idp-status");
    var addForm = document.getElementById("pref-idp-add-form");

    function renderIdpViews() {
      if (cardsContainer) {
        if (providers.length === 0) {
          cardsContainer.innerHTML = '<p style="color:var(--muted);">No external IdP providers configured (`idp_providers.json`).</p>';
        } else {
          cardsContainer.innerHTML = providers.map(function (p) {
            return '<section class="card" aria-label="' + escHtml(p.display_name) + '"><h2>' + escHtml(p.display_name) + " (" + escHtml(p.id) + ")</h2><dl><dt>Type</dt><dd>" + escHtml(p.type) + "</dd><dt>Client ID</dt><dd>" + escHtml(p.client_id) + "</dd></dl></section>";
          }).join("");
        }
      }
      if (tbody) {
        if (providers.length === 0) {
          tbody.innerHTML = '<tr><td colspan="7" style="color:var(--muted);text-align:center;">' + tr("console.js.admin.idp.empty") + '</td></tr>';
        } else {
          tbody.innerHTML = providers.map(function (p) {
            var cbUri = origin.replace(/\/$/, "") + "/auth/oidc/" + p.id + "/callback";
            return '<tr data-idp-id="' + escHtml(p.id) + '"><td><input type="checkbox" switch data-idp-toggle="' + escHtml(p.id) + '"' + (p.enabled ? " checked" : "") + "/></td><td><strong>" + escHtml(p.display_name) + "</strong> (<code>" + escHtml(p.id) + '</code>)</td><td><span class="pref-mono">' + escHtml(p.issuer || "https://github.com") + '</span></td><td><code class="pref-mono">' + escHtml(p.client_id) + '</code></td><td><code class="pref-mono" style="white-space:normal">' + escHtml(cbUri) + '</code></td><td><input type="checkbox" switch data-idp-signup="' + escHtml(p.id) + '"' + (p.allow_signup ? " checked" : "") + '/></td><td><button type="button" class="pref-btn" data-idp-remove="' + escHtml(p.id) + '">삭제</button></td></tr>';
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

  function initMcpSection() {
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

    var webmcpToggle = document.getElementById("pref-webmcp-enabled");
    var webmcpHint = document.getElementById("admin-webmcp-policy-hint");
    if (webmcpToggle) {
      try {
        var saved = window.localStorage.getItem("claire_admin_webmcp_enabled");
        if (saved === "0") webmcpToggle.checked = false;
      } catch (_e) {}
      var syncHint = function () {
        if (!webmcpHint) return;
        webmcpHint.textContent = webmcpToggle.checked ? tr("console.js.admin.mcp.webmcp_on") : tr("console.js.admin.mcp.webmcp_off");
      };
      syncHint();
      webmcpToggle.addEventListener("change", function () {
        try {
          window.localStorage.setItem("claire_admin_webmcp_enabled", webmcpToggle.checked ? "1" : "0");
        } catch (_e) {}
        syncHint();
      });
    }

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

  // --- /admin/federation: switch, readiness checks, sidecar heartbeat, domain blocks (Fedify sidecar design Sections 9.2, 11, and 12) ---

  function initFederationSection() {
    var FED_ERROR_TEXT = {
      step_up_requires_session: tr("console.js.common.error.step_up_requires_session"),
      forbidden: tr("console.js.admin.error.forbidden"),
      invalid_domain: tr("console.js.admin.federation.error.invalid_domain"),
      invalid_note: tr("console.js.admin.federation.error.invalid_note"),
      invalid_request: tr("console.js.common.error.invalid_request"),
      domain_block_not_found: tr("console.js.admin.federation.error.domain_block_not_found")
    };
    var switchCard = document.getElementById("admin-federation-switch");
    var switchResult = document.getElementById("admin-federation-switch-result");
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
      var tr = document.createElement("tr");
      if (marker) tr.setAttribute(marker, "");
      var td = cell(text);
      td.colSpan = colSpan;
      td.style.color = "var(--muted)";
      td.style.textAlign = "center";
      tr.appendChild(td);
      tbody.appendChild(tr);
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
      if (body && body.error === "readiness_failed") {
        var fails = (body.failures || []).map(function (f) { return f.id + " (" + (f.detail || f.name) + ")"; });
        return tr("console.js.admin.mode.readiness_failed") + " " + (fails.join(", ") || body.detail || "");
      }
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

    function renderReadiness(readiness) {
      if (!readinessTbody) return;
      clear(readinessTbody);
      var items = (readiness && readiness.items) || [];
      items.forEach(function (item) {
        var tr = document.createElement("tr");
        tr.setAttribute("data-readiness-item", String(item.id));
        tr.setAttribute("data-readiness-passed", item.passed ? "1" : "0");
        var tdId = document.createElement("td");
        var code = document.createElement("code");
        code.textContent = String(item.id);
        tdId.appendChild(code);
        var tdState = document.createElement("td");
        var pill = document.createElement("span");
        pill.className = item.passed ? "badge badge-enabled" : "badge badge-disabled";
        pill.textContent = item.passed ? tr("console.js.admin.readiness.passed") : tr("console.js.admin.readiness.failed");
        tdState.appendChild(pill);
        [tdId, cell(item.name), tdState, cell(item.detail)].forEach(function (td) { tr.appendChild(td); });
        readinessTbody.appendChild(tr);
      });
      if (!items.length) messageRow(readinessTbody, 4, tr("console.js.admin.readiness.empty"));
      setResult(readinessResult, readiness && readiness.passed ? tr("console.js.admin.readiness.all_passed") : tr("console.js.admin.readiness.some_failed"), !(readiness && readiness.passed));
    }

    function renderHeartbeat(heartbeat) {
      if (!heartbeatDl) return;
      clear(heartbeatDl);
      var rows;
      if (!heartbeat) {
        rows = [[tr("console.js.admin.sidecar.heartbeat"), tr("console.js.admin.sidecar.no_heartbeat")]];
      } else {
        var check = heartbeat.self_check || {};
        rows = [
          [tr("console.js.admin.sidecar.last_heartbeat"), formatTs(heartbeat.received_at)],
          [tr("console.js.admin.sidecar.contract_version"), heartbeat.contract_version],
          [tr("console.js.admin.sidecar.sidecar_version"), heartbeat.sidecar_version],
          [tr("console.js.admin.sidecar.queue_depth"), heartbeat.queue_depth],
          [tr("console.js.admin.sidecar.self_check"), check.ok === true ? tr("console.js.admin.readiness.passed") : (check.ok === false ? tr("console.js.admin.readiness.failed") : tr("console.js.admin.sidecar.self_check_pending"))]
        ];
      }
      heartbeatDl.setAttribute("data-heartbeat", heartbeat ? "present" : "absent");
      rows.forEach(function (row) {
        var dt = document.createElement("dt");
        dt.textContent = row[0];
        var dd = document.createElement("dd");
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
        var tr = document.createElement("tr");
        tr.setAttribute("data-domain-block", String(block.domain));
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
        [tdDomain, cell(block.note), cell(formatTs(block.created_at)), tdBtn].forEach(function (td) { tr.appendChild(td); });
        blocksTbody.appendChild(tr);
      });
    }

    function loadBlocks() {
      return send("GET", "/api/admin/federation/domain-blocks").then(function (res) {
        if (res.ok && res.body && Array.isArray(res.body.domain_blocks)) renderBlocks(res.body.domain_blocks);
      }).catch(function () {});
    }

    if (switchCard) {
      switchCard.addEventListener("click", function (ev) {
        var btn = ev.target.closest("[data-federation-switch]");
        if (!btn) return;
        var target = btn.getAttribute("data-federation-switch");
        if (target === "off" && !window.confirm(tr("console.js.admin.federation.off_confirm"))) return;
        setResult(switchResult, tr("console.js.admin.in_work"), false);
        sendChange("POST", "/api/admin/federation", { federation: target }, switchResult, function (res) {
          if (res.ok) {
            setResult(switchResult, target === "on" ? tr("console.js.admin.federation.on_done") : tr("console.js.admin.federation.off_done"), false);
            window.location.reload();
            return;
          }
          setResult(switchResult, errorText(res), true);
        });
      });
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
        });
      });
    }

    loadState();

    // Shared Knowledge Base & Known Servers / Suggestions / Inbox Journal tabs
    var fedTabs = document.getElementById("admin-federation-tabs");
    if (fedTabs && window.ClaireUi) {
      window.ClaireUi.enhance(fedTabs);
    }

    var autoToggle = document.getElementById("admin-fed-auto-contrib-toggle");
    if (autoToggle && window.ClaireUi) {
      window.ClaireUi.request("/api/admin/auto-contribution").then(function (r) {
        if (r.ok && r.data) autoToggle.checked = !!(r.data.enabled !== undefined ? r.data.enabled : r.data.kb_auto_contribution);
        else if (r.unavailable) autoToggle.disabled = true;
      });
      autoToggle.addEventListener("change", function () {
        var next = autoToggle.checked;
        window.ClaireUi.request("/api/admin/auto-contribution", { method: "PUT", body: { enabled: next, kb_auto_contribution: next } }).then(function (r) {
          if (!r.ok) {
            autoToggle.checked = !next;
            window.ClaireUi.toast(r.error || "Failed", "danger");
            return;
          }
          window.ClaireUi.toast(tr("common.action.save", "Saved"), "ok");
        });
      });
    }

    var knownHost = document.getElementById("admin-federation-known-host");
    if (knownHost && window.ClaireUi) {
      var selectedDomain = "";
      var loadKnown = function () {
        window.ClaireUi.request("/api/admin/federation/known-servers").then(function (r) {
          var servers = (r.ok && r.data && (r.data.servers || r.data.orderedItems || r.data.items)) || [];
          if (!selectedDomain && servers.length) selectedDomain = servers[0].domain || "";
          var cur = servers.find(function (s) { return s.domain === selectedDomain; }) || servers[0] || null;
          var ui = window.ClaireUi;
          var addHtml = '<form id="admin-known-add-form" class="claire-card" style="flex-direction:row;align-items:flex-end;flex-wrap:wrap">' +
            '<div class="claire-field" style="flex:1;min-width:220px"><label class="claire-field-label" for="admin-known-domain">' + ui.esc(tr("console.admin.federation.known_add", "Add federated server domain")) + '</label><input id="admin-known-domain" class="claire-input" placeholder="other.example.org" required/></div>' +
            '<button type="submit" class="claire-btn pri">' + ui.esc(tr("console.admin.federation.known_add", "Add")) + "</button></form>";
          if (r.unavailable) {
            knownHost.innerHTML = addHtml + '<div class="claire-note warn"><span>' + ui.esc(ui.t("kb.dlg.backend_pending")) + "</span></div>";
          } else {
            var left = '<div class="claire-list-col">' + (servers.length ? servers.map(function (s) {
              return '<button type="button" class="claire-list-item' + (cur && cur.domain === s.domain ? " active" : "") + '" data-pick-known="' + ui.esc(s.domain) + '">' +
                '<div class="claire-list-item-row"><strong>' + ui.esc(s.domain) + '</strong><span class="claire-tag ' + (s.status === "blocked" ? "danger" : "ok") + '">' + ui.esc(s.status || "active") + "</span></div>" +
                '<div class="claire-list-item-sub"><span>KBs: ' + ui.esc(s.kb_count || (s.kbs && s.kbs.length) || 0) + "</span></div></button>";
            }).join("") : '<div class="claire-card"><p class="claire-hint">' + ui.esc(ui.t("kb.pub.servers_empty")) + "</p></div>") + "</div>";
            var right = '<div class="claire-detail-col"><div class="claire-card">' + (cur ? (
              '<div class="claire-card-head"><h3>' + ui.esc(cur.domain) + '</h3><button type="button" class="claire-btn danger claire-btn-sm" data-block-known="' + ui.esc(cur.domain) + '">' + ui.esc(tr("console.admin.federation.known_block", "Block")) + "</button></div>" +
              ui.renderTable({
                caption: cur.domain,
                columns: [
                  { label: ui.t("kb.dlg.col_kb"), render: function (k) { return '<a href="/kb/' + ui.esc(k.number || "") + '/">' + ui.esc(k.label || k.iri) + "</a>"; } },
                  { label: ui.t("kb.pub.col_type"), render: function (k) { return ui.esc(k.knowledge_type || "real"); } },
                  { label: ui.t("kb.dlg.col_docs"), render: function (k) { return ui.esc(k.documents || 0); } }
                ],
                rows: cur.kbs || []
              })
            ) : '<p class="claire-hint">' + ui.esc(ui.t("kb.ui.empty_list")) + "</p>") + "</div></div>";
            knownHost.innerHTML = addHtml + '<div class="claire-split">' + left + right + "</div>";
          }
          ui.enhance(knownHost);
          var form = knownHost.querySelector("#admin-known-add-form");
          if (form) form.addEventListener("submit", function (ev) {
            ev.preventDefault();
            var d = knownHost.querySelector("#admin-known-domain").value.trim();
            if (!d) return;
            ui.request("/api/admin/federation/known-servers", { method: "POST", body: { domain: d } }).then(function (pr) {
              if (!pr.ok) { ui.toast(pr.error || "Failed", "danger"); return; }
              loadKnown();
            });
          });
          knownHost.querySelectorAll("[data-pick-known]").forEach(function (b) {
            b.addEventListener("click", function () { selectedDomain = b.getAttribute("data-pick-known"); loadKnown(); });
          });
          knownHost.querySelectorAll("[data-block-known]").forEach(function (b) {
            b.addEventListener("click", function () {
              ui.request("/api/admin/federation/domain-blocks", { method: "POST", body: { domain: b.getAttribute("data-block-known"), note: "Known server block" } }).then(function () { loadBlocks(); loadKnown(); });
            });
          });
        });
      };
      loadKnown();
    }

    var suggHost = document.getElementById("admin-federation-suggestions-host");
    if (suggHost && window.ClaireUi) {
      window.ClaireUi.request("/api/admin/federation/block-suggestions").then(function (r) {
        var ui = window.ClaireUi;
        var items = (r.ok && r.data && (r.data.suggestions || r.data.items)) || [];
        suggHost.innerHTML = '<section class="card"><h3>' + ui.esc(tr("console.admin.federation.suggestions_title", "Blocked server suggestions")) + "</h3>" +
          ui.renderTable({
            caption: "Block suggestions",
            columns: [
              { label: "Domain", key: "domain" },
              { label: "Reason", key: "reason" },
              { label: "", actions: true, render: function (s) { return '<button type="button" class="claire-btn claire-btn-sm" data-add-sugg="' + ui.esc(s.domain) + '">' + ui.esc(tr("console.federation.blocks.add", "Block")) + "</button>"; } }
            ],
            rows: items
          }) + "</section>";
        suggHost.querySelectorAll("[data-add-sugg]").forEach(function (b) {
          b.addEventListener("click", function () {
            ui.request("/api/admin/federation/domain-blocks", { method: "POST", body: { domain: b.getAttribute("data-add-sugg"), note: "Suggested block" } }).then(function () { loadBlocks(); });
          });
        });
      });
    }

    var journalHost = document.getElementById("admin-federation-journal-host");
    if (journalHost && window.ClaireUi) {
      var jStatus = "all";
      var loadJournal = function () {
        var ui = window.ClaireUi;
        ui.request("/api/admin/federation/journal" + (jStatus !== "all" ? "?status=" + encodeURIComponent(jStatus) : "")).then(function (r) {
          var rows = (r.ok && r.data && (r.data.entries || r.data.orderedItems || r.data.items)) || [];
          journalHost.innerHTML = '<section class="card"><div class="claire-card-head"><h3>' + ui.esc(tr("console.admin.federation.tab_journal", "Inbox Journal")) + "</h3>" +
            ui.renderChips({
              name: "fed-journal-status",
              label: "Journal status",
              value: jStatus,
              options: [
                { value: "all", label: tr("console.admin.federation.journal_all", "All") },
                { value: "failed", label: tr("console.admin.federation.journal_failed", "Failed") },
                { value: "refused", label: tr("console.admin.federation.journal_refused", "Refused") },
                { value: "processed", label: tr("console.admin.federation.journal_processed", "Processed") }
              ]
            }) + "</div>" +
            ui.renderTable({
              caption: "Inbox journal",
              columns: [
                { label: "Activity", key: "activity_type" },
                { label: "Actor", key: "actor" },
                { label: "Status", render: function (e) { return '<span class="claire-tag ' + (e.status === "failed" ? "danger" : (e.status === "processed" ? "ok" : "warn")) + '">' + ui.esc(e.status || "") + "</span>"; } },
                { label: "Time", key: "received_at" },
                { label: "", actions: true, render: function (e) { return e.status === "failed" ? '<button type="button" class="claire-btn claire-btn-sm" data-replay-j="' + ui.esc(e.id || e.number) + '">' + ui.esc(tr("console.admin.federation.journal_replay", "Replay")) + "</button>" : "—"; } }
              ],
              rows: rows
            }) + "</section>";
          journalHost.querySelectorAll('input[name="fed-journal-status"]').forEach(function (inp) {
            inp.addEventListener("change", function () { jStatus = inp.value; loadJournal(); });
          });
          journalHost.querySelectorAll("[data-replay-j]").forEach(function (b) {
            b.addEventListener("click", function () {
              ui.request("/api/admin/federation/journal/" + encodeURIComponent(b.getAttribute("data-replay-j")) + "/replay", { method: "POST", body: {} }).then(function (rr) {
                if (!rr.ok) { ui.toast(rr.error || "Failed", "danger"); return; }
                loadJournal();
              });
            });
          });
        });
      };
      loadJournal();
    }
  }

  function initProvidersEmbedCard() {
    var host = document.getElementById("admin-providers-embed-host");
    var ui = window.ClaireUi;
    if (!host || !ui) return;
    ui.request("/kb?page=true&limit=50").then(function (r) {
      var kbs = (r.ok && r.data && (r.data.orderedItems || r.data.items)) || [];
      var counts = {};
      var failures = {};
      kbs.forEach(function (k) {
        var m = k.embedding_model || "server-default";
        counts[m] = (counts[m] || 0) + 1;
        if (k.reembed_required || (k.merge_failures && k.merge_failures > 0)) {
          failures[m] = (failures[m] || 0) + (k.merge_failures || 1);
        }
      });
      var rows = Object.keys(counts).map(function (m) {
        return { model: m, kbs: counts[m], failures: failures[m] || 0 };
      });
      host.innerHTML = '<h2><span data-icon="database"></span> ' + ui.esc(tr("console.js.admin.providers.embed_card", "Embedding models & KB merge status")) + "</h2>" +
        ui.renderTable({
          caption: "Embedding models",
          columns: [
            { label: ui.t("kb.dlg.field_embed_model"), render: function (x) { return '<code class="pref-mono">' + ui.esc(x.model) + "</code>"; } },
            { label: "KBs", key: "kbs" },
            { label: "Merge / Re-embed flags", render: function (x) { return x.failures ? '<span class="claire-tag danger">' + ui.esc(x.failures) + "</span>" : '<span class="claire-tag ok">0</span>'; } }
          ],
          rows: rows
        });
      ui.enhance(host);
    });
  }

  function initAdminKbSection() {
    var host = document.getElementById("admin-kb-host");
    var ui = window.ClaireUi;
    var dlgs = window.ClaireKbDialogs;
    if (!host || !ui) return;
    var filterVal = "all";

    function load() {
      ui.request("/api/admin/kb").then(function (r) {
        var items = (r.ok && r.data && (r.data.knowledge_bases || r.data.orderedItems || r.data.items)) || [];
        var draw = function () {
          var filtered = items.filter(function (k) {
            if (filterVal === "nomod") return !k.active_moderators && (!k.moderators || !k.moderators.length);
            if (filterVal === "fail") return !!k.reembed_required || Number(k.merge_failures || 0) > 0;
            return true;
          });
          host.innerHTML = '<div class="claire-panel-inner">' +
            ui.renderChips({
              name: "admin-kb-filter",
              label: "Filter shared KBs",
              value: filterVal,
              options: [
                { value: "all", label: tr("console.js.admin.kb.filter_all", "All") },
                { value: "nomod", label: tr("console.js.admin.kb.filter_nomod", "No moderator") },
                { value: "fail", label: tr("console.js.admin.kb.filter_fail", "Merge failure") }
              ]
            }) +
            (r.unavailable ? '<div class="claire-note warn"><span>' + ui.esc(ui.t("kb.dlg.backend_pending")) + "</span></div>" : "") +
            ui.renderTable({
              caption: "Shared KBs overview",
              columns: [
                { label: ui.t("kb.dlg.col_kb"), render: function (k) { return '<a href="/kb/' + ui.esc(k.number) + '/"><strong>' + ui.esc(k.label) + '</strong></a> <code class="pref-mono">#' + ui.esc(k.number) + "</code>"; } },
                { label: ui.t("kb.pub.col_type"), render: function (k) { return '<span class="claire-tag ' + (k.knowledge_type === "fictional" ? "fic" : "acc") + '">' + ui.esc(k.knowledge_type || "real") + "</span>"; } },
                { label: ui.t("kb.pub.col_mods"), render: function (k) {
                  var cnt = k.active_moderators != null ? k.active_moderators : ((k.moderators && k.moderators.length) || 0);
                  return cnt > 0 ? ui.esc(cnt) : '<span class="claire-tag warn">' + ui.esc(ui.t("kb.pub.no_mod_warn")) + "</span>";
                } },
                { label: "Status", render: function (k) {
                  var mf = Number(k.merge_failures || 0);
                  return (k.reembed_required || mf > 0) ? '<span class="claire-tag danger">embedding_model_unavailable (' + ui.esc(mf || 1) + ")</span>" : '<span class="claire-tag ok">' + ui.esc(k.state || "active") + "</span>";
                } },
                { label: ui.t("kb.pub.col_actions"), actions: true, render: function (k) {
                  return ui.renderRowMenu([
                    { action: "assign_mod", value: k.number, label: tr("console.js.admin.kb.assign_mod", "Assign moderator"), icon: "user" },
                    { action: "retry_merge", value: k.number, label: tr("console.js.admin.kb.retry_merge", "Retry failed merge"), icon: "restore" },
                    { sep: true },
                    { action: "delete_kb", value: k.number, label: tr("console.js.admin.kb.delete_kb", "Delete KB..."), icon: "trash", danger: true }
                  ], k.label || k.number);
                } }
              ],
              rows: filtered
            }) + "</div>";
          ui.enhance(host);
          host.querySelectorAll('input[name="admin-kb-filter"]').forEach(function (inp) {
            inp.addEventListener("change", function () { filterVal = inp.value; draw(); });
          });
        };
        draw();
      });
    }

    host.addEventListener("click", function (ev) {
      var mi = ev.target && ev.target.closest ? ev.target.closest("[data-menu-action]") : null;
      if (!mi) return;
      var act = mi.getAttribute("data-menu-action");
      var kbNum = mi.getAttribute("data-menu-value");
      if (act === "assign_mod") {
        ui.openDialog({
          title: tr("console.js.admin.kb.assign_mod", "Assign moderator"),
          icon: "user",
          bodyHtml: '<div class="claire-field"><label class="claire-field-label" for="admin-assign-acc">' + ui.esc(tr("console.js.admin.kb.assign_prompt_label", "Account # or @username")) + '</label><input id="admin-assign-acc" class="claire-input" required/></div>',
          onSubmit: function (_d, body) {
            var acc = body.querySelector("#admin-assign-acc").value.trim();
            if (!acc) return false;
            return ui.request("/api/admin/kb/" + encodeURIComponent(kbNum) + "/moderators/" + encodeURIComponent(acc), { method: "PUT", body: {} }).then(function (res) {
              if (!res.ok) { ui.toast(res.error || "Failed", "danger"); return false; }
              load();
              return true;
            });
          }
        });
      } else if (act === "retry_merge") {
        ui.request("/api/admin/kb/" + encodeURIComponent(kbNum) + "/retry-merge", { method: "POST", body: {} }).then(function (res) {
          if (!res.ok) { ui.toast(res.error || "Failed", "danger"); return; }
          load();
        });
      } else if (act === "delete_kb" && dlgs) {
        dlgs.openDeleteKbDialog({ kbNumber: kbNum, onDeleted: function () { load(); } });
      }
    });
    load();
  }

  function initAdminThemesV2(host) {
    var ui = window.ClaireUi;
    var dlgs = window.ClaireKbDialogs;
    if (!host || !ui) return;
    var activeTab = "judge";
    var selectedThemeNum = "";

    function load() {
      Promise.all([
        ui.request("/api/admin/themes"),
        ui.request("/api/admin/themes/settings"),
        ui.request("/api/admin/reputations"),
        ui.request("/api/admin/themes/agreement")
      ]).then(function (all) {
        var thRes = all[0], setRes = all[1], repRes = all[2], agRes = all[3];
        var themes = (thRes.ok && thRes.data && (thRes.data.themes || thRes.data.orderedItems || thRes.data.items)) || [];
        var cfg = (thRes.ok && thRes.data && thRes.data.settings) || (setRes.ok && setRes.data && (setRes.data.settings || setRes.data)) || {};
        var repSummary = (repRes.ok && repRes.data && (repRes.data.summary || repRes.data.counts)) || {};
        var agRows = (agRes.ok && agRes.data && (agRes.data.items || agRes.data.agreements)) || [];
        if (!selectedThemeNum && themes.length) selectedThemeNum = String(themes[0].number);
        var curTh = themes.find(function (x) { return String(x.number) === selectedThemeNum; }) || themes[0] || null;

        var judgeHtml = '<div class="claire-panel-inner">' +
          (thRes.unavailable ? '<div class="claire-note warn"><span>' + ui.esc(ui.t("kb.dlg.backend_pending")) + "</span></div>" : "") +
          '<div class="claire-kpis">' +
          '<div class="claire-kpi"><strong>' + ui.esc(themes.length) + "</strong><span>Themes</span></div>" +
          '<div class="claire-kpi"><strong>' + ui.esc(repSummary.good || 0) + " / " + ui.esc(repSummary.limited || 0) + " / " + ui.esc(repSummary.low || 0) + "</strong><span>Good / Limited / Low</span></div>" +
          '<div class="claire-kpi"><strong>' + ui.esc(cfg.last_judged_at || "—") + "</strong><span>Last judgement run</span></div>" +
          "</div>" +
          '<div class="claire-actions"><button type="button" class="claire-btn pri" id="adm-th-run-now">' + ui.esc(tr("console.js.admin.themes.run_now", "Run theme judgement now")) + "</button></div>" +
          '<form id="adm-th-thresh-form" class="claire-grid-2">' +
          '<div class="claire-field"><label class="claire-field-label" for="th-tm">' + ui.esc(tr("console.js.admin.themes.th_theme_match", "Theme match threshold")) + '</label><input id="th-tm" type="number" class="claire-input" min="0" max="1" step="0.05" value="' + ui.esc(cfg.theme_match_threshold != null ? cfg.theme_match_threshold : 0.65) + '"/></div>' +
          '<div class="claire-field"><label class="claire-field-label" for="th-dm">' + ui.esc(tr("console.js.admin.themes.th_doc_match", "Document match threshold")) + '</label><input id="th-dm" type="number" class="claire-input" min="0" max="1" step="0.05" value="' + ui.esc(cfg.document_match_threshold != null ? cfg.document_match_threshold : 0.6) + '"/></div>' +
          '<div class="claire-field"><label class="claire-field-label" for="th-sm">' + ui.esc(tr("console.js.admin.themes.th_seed_match", "Seed match threshold")) + '</label><input id="th-sm" type="number" class="claire-input" min="0" max="1" step="0.05" value="' + ui.esc(cfg.seed_match_threshold != null ? cfg.seed_match_threshold : 0.7) + '"/></div>' +
          '<div class="claire-field"><label class="claire-field-label" for="th-am">' + ui.esc(tr("console.js.admin.themes.th_auto_min", "Auto-contribution floor")) + '</label><input id="th-am" type="number" class="claire-input" min="0" max="1" step="0.05" value="' + ui.esc(cfg.auto_contribution_min_score != null ? cfg.auto_contribution_min_score : 0.75) + '"/></div>' +
          '<div class="claire-actions"><button type="submit" class="claire-btn pri">' + ui.esc(tr("console.js.admin.themes.save_thresholds", "Save thresholds")) + "</button></div></form></div>";

        var adjLeft = '<div class="claire-list-col">' +
          '<button type="button" class="claire-btn claire-btn-sm" id="adm-th-create">' + ui.esc(tr("console.js.admin.themes.create_theme", "Create computed theme")) + "</button>" +
          (themes.length ? themes.map(function (th) {
            return '<button type="button" class="claire-list-item' + (curTh && String(curTh.number) === String(th.number) ? " active" : "") + '" data-pick-th="' + ui.esc(th.number) + '">' +
              '<div class="claire-list-item-row"><strong>' + ui.esc(th.label) + '</strong><span class="claire-tag ' + (th.knowledge_type === "fictional" ? "fic" : "acc") + '">' + ui.esc(th.knowledge_type || "real") + "</span></div>" +
              '<div class="claire-list-item-sub"><span>#' + ui.esc(th.number) + (th.label_locked ? " · locked" : "") + "</span></div></button>";
          }).join("") : '<div class="claire-card"><p class="claire-hint">' + ui.esc(ui.t("kb.ui.empty_list")) + "</p></div>") + "</div>";

        var adjRight = '<div class="claire-detail-col"><div class="claire-card">' + (curTh ? (
          '<div class="claire-card-head"><h3>' + ui.esc(curTh.label) + '</h3><div class="claire-actions">' +
          '<button type="button" class="claire-btn sec claire-btn-sm" id="adm-th-toggle-lock">' + ui.esc(curTh.label_locked ? tr("console.js.admin.themes.unlock_label", "Unlock label") : tr("console.js.admin.themes.lock_label", "Lock label")) + "</button>" +
          '<button type="button" class="claire-btn danger claire-btn-sm" id="adm-th-merge">' + ui.esc(tr("console.js.admin.themes.merge_btn", "Merge into...")) + "</button>" +
          "</div></div>" +
          ui.renderTable({
            caption: curTh.label,
            columns: [
              { label: ui.t("kb.dlg.col_kb"), render: function (k) { return '<a href="/kb/' + ui.esc(k.number) + '/">' + ui.esc(k.label) + "</a>"; } },
              { label: "Membership", render: function (k) { return '<span class="claire-tag">' + ui.esc(k.membership || "computed") + "</span>"; } },
              { label: ui.t("kb.pub.col_actions"), actions: true, render: function (k) {
                return ui.renderRowMenu([
                  { action: "th_pin", value: k.number, label: tr("console.js.admin.themes.pin_kb", "Pin KB"), icon: "plus" },
                  { action: "th_exclude", value: k.number, label: tr("console.js.admin.themes.exclude_kb", "Exclude KB"), icon: "block", danger: true },
                  { action: "th_computed", value: k.number, label: tr("console.js.admin.themes.reset_kb", "Reset to computed"), icon: "restore" }
                ], k.label);
              } }
            ],
            rows: curTh.knowledge_bases || []
          })
        ) : '<p class="claire-hint">' + ui.esc(ui.t("kb.ui.empty_list")) + "</p>") + "</div></div>";

        var adjHtml = '<div class="claire-split">' + adjLeft + adjRight + "</div>";
        var agreeHtml = ui.renderTable({
          caption: tr("console.js.admin.themes.tab_agree", "Decision Agreement"),
          columns: [
            { label: "Document", render: function (a) { return ui.esc(a.title || a.document); } },
            { label: "KBs", render: function (a) { return ui.esc((a.decisions || []).map(function (d) { return (d.label || d.knowledge_base) + ":" + d.decision; }).join(", ")); } },
            { label: "Agreement", render: function (a) { return ui.esc(a.agreement != null ? Math.round(Number(a.agreement) * 100) + "%" : "—"); } }
          ],
          rows: agRows
        });

        host.innerHTML = ui.renderTabs({
          id: "admin-themes-tabs",
          ariaLabel: tr("console.section.admin.themes.title", "Theme Judgement"),
          active: activeTab,
          tabs: [
            { id: "judge", label: tr("console.js.admin.themes.tab_judge", "Judgement & Thresholds"), icon: "bolt", html: judgeHtml },
            { id: "adj", label: tr("console.js.admin.themes.tab_adj", "Adjudicate Themes"), icon: "folder", html: adjHtml },
            { id: "agree", label: tr("console.js.admin.themes.tab_agree", "Decision Agreement"), icon: "chart", html: agreeHtml }
          ]
        });
        ui.enhance(host);

        var tRoot = host.querySelector("#admin-themes-tabs");
        if (tRoot) tRoot.addEventListener("claire:tabchange", function (ev) { activeTab = ev.detail.tab; });
        var runBtn = host.querySelector("#adm-th-run-now");
        if (runBtn) runBtn.addEventListener("click", function () {
          ui.request("/api/admin/themes/judge", { method: "POST", body: {} }).then(function (rr) {
            if (!rr.ok) { ui.toast(rr.error || "Failed", "danger"); return; }
            load();
          });
        });
        var tForm = host.querySelector("#adm-th-thresh-form");
        if (tForm) tForm.addEventListener("submit", function (ev) {
          ev.preventDefault();
          ui.request("/api/admin/themes/settings", {
            method: "PUT",
            body: {
              theme_match_threshold: Number(host.querySelector("#th-tm").value),
              document_match_threshold: Number(host.querySelector("#th-dm").value),
              seed_match_threshold: Number(host.querySelector("#th-sm").value),
              auto_contribution_min_score: Number(host.querySelector("#th-am").value)
            }
          }).then(function (rr) {
            if (!rr.ok) { ui.toast(rr.error || "Failed", "danger"); return; }
            ui.toast(tr("common.action.save", "Saved"), "ok");
          });
        });
        host.querySelectorAll("[data-pick-th]").forEach(function (b) {
          b.addEventListener("click", function () { selectedThemeNum = b.getAttribute("data-pick-th"); load(); });
        });
        var cBtn = host.querySelector("#adm-th-create");
        if (cBtn) cBtn.addEventListener("click", function () {
          ui.openDialog({
            title: tr("console.js.admin.themes.create_theme", "Create computed theme"),
            icon: "plus",
            bodyHtml: '<div class="claire-field"><label class="claire-field-label" for="new-ct-lbl">Label</label><input id="new-ct-lbl" class="claire-input" required/></div>' +
              '<div class="claire-field"><label class="claire-field-label" for="new-ct-type">Type</label><select id="new-ct-type" class="claire-select"><option value="real">real</option><option value="fictional">fictional</option></select></div>',
            onSubmit: function (_d, body) {
              var lbl = body.querySelector("#new-ct-lbl").value.trim();
              if (!lbl) return false;
              return ui.request("/api/admin/themes", { method: "POST", body: { label: lbl, knowledge_type: body.querySelector("#new-ct-type").value } }).then(function (rr) {
                if (!rr.ok) { ui.toast(rr.error || "Failed", "danger"); return false; }
                load();
                return true;
              });
            }
          });
        });
        var lockBtn = host.querySelector("#adm-th-toggle-lock");
        if (lockBtn && curTh) lockBtn.addEventListener("click", function () {
          var body = curTh.label_locked ? { label_locked: false } : { label: curTh.label };
          ui.request("/api/admin/themes/" + encodeURIComponent(curTh.number), { method: "PATCH", body: body }).then(function () { load(); });
        });
        var mergeBtn = host.querySelector("#adm-th-merge");
        if (mergeBtn && curTh && dlgs) mergeBtn.addEventListener("click", function () {
          dlgs.openMergeThemeDialog({ sourceTheme: curTh, themes: themes, onMerged: function () { selectedThemeNum = ""; load(); } });
        });
        host.querySelectorAll("[data-menu-action]").forEach(function (mi) {
          mi.addEventListener("click", function () {
            if (!curTh) return;
            var act = mi.getAttribute("data-menu-action");
            var kb = mi.getAttribute("data-menu-value");
            var membership = act === "th_pin" ? "pinned" : (act === "th_exclude" ? "excluded" : "computed");
            ui.request("/api/admin/themes/" + encodeURIComponent(curTh.number) + "/knowledge-bases/" + encodeURIComponent(kb), { method: "PUT", body: { membership: membership } }).then(function () { load(); });
          });
        });
      });
    }
    load();
  }

  function initAdminSection(bootstrap) {
    var panels = window.ClaireManagePanels || {};
    var section = bootstrap.section;
    if (section === "providers") {
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
    } else if (section === "mcp") {
      initMcpSection();
    } else if (section === "federation") {
      initFederationSection();
    } else if (section === "storage") {
      initStorageSection();
    }
  }

  function init() {
    bindThemeSelector();
    bindReauthModal();
    var bootstrap = parseBootstrap();
    if (!bootstrap || bootstrap.area !== "admin") return;
    initAdminSection(bootstrap);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
