/* Claire Admin Console controller (/admin/*). */
(function () {
  "use strict";

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
            errEl.textContent = "비밀번호를 입력해 주세요.";
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
            errEl.textContent = (res.body && (res.body.detail || res.body.error)) ? String(res.body.detail || res.body.error) : "재인증에 실패했습니다.";
            errEl.hidden = false;
          }
        }).catch(function () {
          if (errEl) {
            errEl.textContent = "네트워크 오류가 발생했습니다.";
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
        url.searchParams.set("theme", val);
      } else {
        url.searchParams.delete("theme");
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
              showStatusBanner(statusEl, "계정 상태가 업데이트되었습니다: " + publicId + " (" + action + ")", false);
              window.location.reload();
              return;
            }
            if (res.status === 401 && res.body && res.body.error === "reauth_required") {
              openReauthModal(performPatch);
              return;
            }
            showStatusBanner(statusEl, "계정 작업 실패: " + ((res.body && (res.body.detail || res.body.error)) || res.status), true);
          }).catch(function () {
            showStatusBanner(statusEl, "네트워크 오류로 계정 작업을 완료하지 못했습니다.", true);
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
          invitesTbody.innerHTML = '<tr><td colspan="7" class="empty">발급된 초대 코드가 없습니다.</td></tr>';
          return;
        }
        invitesTbody.innerHTML = res.body.items.map(function (inv) {
          var statusBadge = inv.status === "active" ? '<span class="badge badge-ok">active</span>' : '<span class="badge badge-muted">' + escHtml(inv.status) + "</span>";
          var revokeBtn = inv.status === "active" ? '<button type="button" class="pref-btn pref-btn-danger" data-invite-revoke="' + escHtml(inv.code) + '">폐기</button>' : "—";
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
            showStatusBanner(inviteStatusEl, "초대 코드가 폐기되었습니다: " + code, false);
            loadInvites();
            return;
          }
          showStatusBanner(inviteStatusEl, "초대 코드 폐기 실패: " + ((res.body && (res.body.detail || res.body.error)) || res.status), true);
        }).catch(function () {
          showStatusBanner(inviteStatusEl, "네트워크 오류로 초대 코드를 폐기하지 못했습니다.", true);
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
            showStatusBanner(inviteStatusEl, "초대 코드 발급 완료: " + (res.body.code || ""), false);
            inviteForm.reset();
            loadInvites();
            return;
          }
          showStatusBanner(inviteStatusEl, "초대 코드 발급 실패: " + ((res.body && (res.body.detail || res.body.error)) || res.status), true);
        }).catch(function () {
          showStatusBanner(inviteStatusEl, "네트워크 오류로 초대 코드를 발급하지 못했습니다.", true);
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
        showStatusBanner(usernameResult, "조치할 사용자 이름을 입력해 주세요.", true);
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
            var label = applyFlag ? "해제 완료: " : "미리보기 결과: ";
            showStatusBanner(usernameResult, label + JSON.stringify(res.body), false);
            return;
          }
          if (res.status === 401 && res.body && res.body.error === "reauth_required") {
            openReauthModal(execRelease);
            return;
          }
          showStatusBanner(usernameResult, "사용자 이름 해제 실패: " + ((res.body && (res.body.detail || res.body.error)) || res.status), true);
        }).catch(function () {
          showStatusBanner(usernameResult, "네트워크 오류가 발생했습니다.", true);
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
          tbody.innerHTML = '<tr><td colspan="7" style="color:var(--muted);text-align:center;">등록된 공급자가 없습니다.</td></tr>';
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
          loginPreview.innerHTML = '<span style="color:var(--muted);font-size:0.85rem;">활성화된 외부 IdP 버튼이 없습니다.</span>';
        } else {
          loginPreview.innerHTML = enabledProviders.map(function (p) {
            return '<a class="pref-login-btn" href="/auth/oidc/' + escHtml(p.id) + '/start">' + escHtml(p.display_name) + "로 계속하기</a>";
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
          showStatusBanner(statusEl, msg || "외부 IdP 설정이 저장되었습니다.", false);
          return;
        }
        showStatusBanner(statusEl, "IdP 저장 실패: " + ((res.body && (res.body.detail || res.body.error)) || res.status), true);
      }).catch(function () {
        showStatusBanner(statusEl, "네트워크 오류로 IdP 설정을 저장하지 못했습니다.", true);
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
          saveProviders(next, "공급자 활성 상태가 변경되었습니다: " + id);
          return;
        }
        var signupInput = ev.target.closest("[data-idp-signup]");
        if (signupInput) {
          var sid = signupInput.getAttribute("data-idp-signup");
          var nextSignup = providers.map(function (p) {
            if (p.id === sid) return Object.assign({}, p, { allow_signup: Boolean(signupInput.checked) });
            return p;
          });
          saveProviders(nextSignup, "공급자 초대 가입 정책이 변경되었습니다: " + sid);
        }
      });
      tbody.addEventListener("click", function (ev) {
        var removeBtn = ev.target.closest("[data-idp-remove]");
        if (removeBtn) {
          var rid = removeBtn.getAttribute("data-idp-remove");
          var nextRem = providers.filter(function (p) { return p.id !== rid; });
          saveProviders(nextRem, "공급자가 삭제되었습니다: " + rid);
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
          showStatusBanner(statusEl, "공급자 ID와 Client ID를 입력해 주세요.", true);
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
        saveProviders(filtered, "새 IdP 공급자가 등록되었습니다: " + pname);
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
          auditTbody.innerHTML = '<tr><td colspan="7" style="color:var(--muted);text-align:center;">조건에 일치하는 MCP 호출 감사 기록이 없습니다.</td></tr>';
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
        webmcpHint.textContent = webmcpToggle.checked ? "현재 상태: 활성 (테마별 상세 노출 설정은 /settings/webmcp에서 구성합니다)." : "현재 상태: 비활성 (document.modelContext 등록 차단).";
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
              bundleResult.innerHTML = '지원 번들이 준비되었습니다 (' + escHtml(res.body.size_bytes) + ' bytes): <a href="/support/bundle?token=' + encodeURIComponent(res.body.token) + '">다운로드 (.tar.gz)</a>';
            }
            return;
          }
          showStatusBanner(bundleResult, "지원 번들 생성 실패: " + ((res.body && (res.body.detail || res.body.error)) || res.status), true);
        }).catch(function () {
          showStatusBanner(bundleResult, "네트워크 오류로 지원 번들을 생성하지 못했습니다.", true);
        });
      });
    }
  }

  function initAdminSection(bootstrap) {
    var panels = window.ClaireManagePanels || {};
    var section = bootstrap.section;
    if (section === "providers") {
      var provHost = document.getElementById("admin-providers-host");
      if (provHost && typeof panels.renderProviderManager === "function") {
        panels.renderProviderManager(provHost, { showClose: false, canWrite: true, isAdmin: true });
      }
    } else if (section === "limits") {
      var limHost = document.getElementById("admin-limits-host");
      if (limHost && typeof panels.renderLimitManager === "function") {
        panels.renderLimitManager(limHost, { showClose: false, canWrite: true, isAdmin: true });
      }
    } else if (section === "themes") {
      var themeHost = document.getElementById("admin-themes-host");
      if (themeHost && typeof panels.renderThemeManager === "function") {
        panels.renderThemeManager(themeHost, { showClose: false, isOwner: true, isAdmin: true, apiUrl: "/", rootUrl: "/" });
      }
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
