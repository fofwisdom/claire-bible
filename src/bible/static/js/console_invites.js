/* Claire Invitations Settings controller (/settings/invites). */
(function () {
  "use strict";

  var tr = function (key, params) { return window.ClaireI18n ? window.ClaireI18n.t(key, params) : key; };

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

  function showResult(el, message, isError) {
    if (!el) return;
    el.textContent = message || "";
    if (!message) {
      el.removeAttribute("data-state");
      return;
    }
    el.setAttribute("data-state", isError ? "error" : "ok");
  }

  function statusBadgeHtml(status) {
    var s = String(status || "");
    var cls = "badge";
    if (s === "active") cls = "badge badge-enabled";
    else if (s === "revoked") cls = "badge badge-disabled";
    var known = s === "active" || s === "revoked" || s === "exhausted" || s === "expired";
    var label = known ? tr("console.js.invites.status." + s) : s;
    return '<span class="' + cls + '" data-invite-status="' + escHtml(s) + '">' + escHtml(label) + "</span>";
  }

  function fallbackExecCopy(text) {
    return new Promise(function (resolve, reject) {
      var field = document.createElement("input");
      field.type = "text";
      field.readOnly = true;
      field.className = "sr-only";
      field.setAttribute("aria-hidden", "true");
      field.tabIndex = -1;
      field.value = text;
      document.body.appendChild(field);
      try {
        field.focus();
        field.select();
        if (typeof field.setSelectionRange === "function") field.setSelectionRange(0, text.length);
        if (document.execCommand("copy")) resolve();
        else reject(new Error("execCommand_failed"));
      } catch (err) {
        reject(err);
      } finally {
        field.remove();
      }
    });
  }

  function toRelativeApiUrl(rawUrl) {
    if (!rawUrl) return "";
    try {
      var u = new URL(String(rawUrl), window.location.origin);
      return u.pathname + u.search;
    } catch (_err) {
      return String(rawUrl);
    }
  }

  function initInvitesSection(bootstrap) {
    var tbody = document.getElementById("invites-tbody");
    var form = document.getElementById("invites-create-form");
    var resultEl = document.getElementById("invites-result");
    var paginationEl = document.getElementById("invites-pagination");
    var loadMoreBtn = document.getElementById("invites-load-more");
    var listSection = document.getElementById("invites-list-section");
    if (!tbody || !listSection) return;

    var headerLabels = Array.prototype.map.call(listSection.querySelectorAll("thead th"), function (th) {
      return (th.textContent || "").trim();
    });
    var isAdmin = Boolean(bootstrap.is_admin);
    var colCount = headerLabels.length || (isAdmin ? 7 : 6);
    var loadedItems = [];
    var nextUrl = "";

    function cellHtml(idx, inner) {
      var label = headerLabels[idx] || "";
      var attr = label ? ' data-label="' + escHtml(label) + '"' : "";
      return "<td" + attr + ">" + inner + "</td>";
    }

    function renderRows() {
      if (loadedItems.length === 0) {
        tbody.innerHTML = '<tr><td colspan="' + colCount + '" style="color:var(--muted);text-align:center;">' + escHtml(tr("console.js.invites.empty")) + "</td></tr>";
        return;
      }
      tbody.innerHTML = loadedItems.map(function (inv) {
        var code = String(inv.code || "");
        var inviteUrl = inv.url ? String(inv.url) : (window.location.origin + "/invite/" + encodeURIComponent(code));
        var usesText = String(inv.uses_count == null ? 0 : inv.uses_count) + " / " + String(inv.max_uses == null ? 1 : inv.max_uses);
        var reservedText = inv.reserved_username ? String(inv.reserved_username) : "—";
        var noteText = (inv.note != null && String(inv.note).trim() !== "") ? String(inv.note) : "—";
        var canRevoke = inv.status === "active" && inv.can_revoke !== false;
        var copyBtn = '<button type="button" class="pref-btn" data-invite-copy="' + escHtml(code) + '" data-invite-url="' + escHtml(inviteUrl) + '">' + escHtml(tr("console.js.invites.copy")) + "</button>";
        var revokeBtn = canRevoke ? '<button type="button" class="pref-btn pref-btn-danger" data-invite-revoke="' + escHtml(code) + '">' + escHtml(tr("console.js.invites.revoke")) + "</button>" : "";
        var actionsInner = '<div style="display:flex;gap:6px;flex-wrap:wrap;justify-content:flex-end">' + copyBtn + revokeBtn + "</div>";

        var cells = [
          "<code>" + escHtml(code) + "</code>",
          statusBadgeHtml(inv.status),
          escHtml(usesText),
        ];
        if (isAdmin) {
          var issuerText = inv.issuer_username ? String(inv.issuer_username) : (inv.is_mine ? tr("console.js.invites.issuer_me") : (inv.created_by ? "#" + String(inv.created_by) : "—"));
          cells.push(escHtml(issuerText));
        }
        cells.push(escHtml(reservedText));
        cells.push(escHtml(noteText));
        cells.push(actionsInner);

        var tds = cells.map(function (inner, idx) {
          return cellHtml(idx, inner);
        }).join("");
        return '<tr data-invite-code="' + escHtml(code) + '">' + tds + "</tr>";
      }).join("");
    }

    function updatePagination() {
      if (!paginationEl) return;
      paginationEl.hidden = !nextUrl;
    }

    function mergeItems(newItems, append) {
      if (!append) {
        loadedItems = newItems.slice();
        return;
      }
      var seen = Object.create(null);
      for (var i = 0; i < loadedItems.length; i++) {
        seen[String(loadedItems[i].code || "")] = true;
      }
      for (var j = 0; j < newItems.length; j++) {
        var code = String(newItems[j].code || "");
        if (!seen[code]) {
          seen[code] = true;
          loadedItems.push(newItems[j]);
        }
      }
    }

    function loadInvites(url, append) {
      if (loadMoreBtn && append) loadMoreBtn.disabled = true;
      return fetch(url || "/api/invites?page=true", {
        method: "GET",
        credentials: "same-origin",
        cache: "no-store",
        headers: { "Accept": "application/json" }
      }).then(function (resp) {
        return resp.json().catch(function () { return {}; }).then(function (body) {
          return { status: resp.status, body: body };
        });
      }).then(function (res) {
        if (res.status !== 200 || !res.body || !Array.isArray(res.body.orderedItems)) {
          if (!append) {
            tbody.innerHTML = '<tr><td colspan="' + colCount + '" style="color:var(--muted);text-align:center;">' + escHtml(tr("console.js.invites.load_failed")) + "</td></tr>";
          }
          return;
        }
        mergeItems(res.body.orderedItems, append);
        nextUrl = res.body.next ? toRelativeApiUrl(res.body.next) : "";
        renderRows();
        updatePagination();
      }).catch(function () {
        if (!append) {
          tbody.innerHTML = '<tr><td colspan="' + colCount + '" style="color:var(--muted);text-align:center;">' + escHtml(tr("console.js.invites.load_failed")) + "</td></tr>";
        }
      }).then(function () {
        if (loadMoreBtn) loadMoreBtn.disabled = false;
      });
    }

    function copyInviteUrl(code, url) {
      var tryWrite = (navigator.clipboard && typeof navigator.clipboard.writeText === "function")
        ? navigator.clipboard.writeText(url)
        : Promise.reject(new Error("clipboard_unavailable"));
      tryWrite.catch(function () {
        return fallbackExecCopy(url);
      }).then(function () {
        var msg = tr("console.js.invites.copied", { code: code });
        showResult(resultEl, msg, false);
        if (window.ClaireUi && typeof window.ClaireUi.toast === "function") {
          window.ClaireUi.toast(msg, "ok");
        }
      }).catch(function () {
        try {
          window.prompt(tr("console.js.invites.copy_prompt"), url);
        } catch (_err) {
          // Ignore prompt errors.
        }
        var msg = tr("console.js.invites.copy_manual", { url: url });
        showResult(resultEl, msg, false);
        if (window.ClaireUi && typeof window.ClaireUi.toast === "function") {
          window.ClaireUi.toast(msg, "info");
        }
      });
    }

    if (loadMoreBtn) {
      loadMoreBtn.addEventListener("click", function () {
        if (!nextUrl) return;
        loadInvites(nextUrl, true);
      });
    }

    tbody.addEventListener("click", function (ev) {
      var copyBtn = ev.target.closest("[data-invite-copy]");
      if (copyBtn) {
        var copyCode = copyBtn.getAttribute("data-invite-copy") || "";
        var copyUrl = copyBtn.getAttribute("data-invite-url") || (window.location.origin + "/invite/" + encodeURIComponent(copyCode));
        copyInviteUrl(copyCode, copyUrl);
        return;
      }

      var revokeBtn = ev.target.closest("[data-invite-revoke]");
      if (!revokeBtn || revokeBtn.disabled) return;
      var code = revokeBtn.getAttribute("data-invite-revoke") || "";
      if (!code) return;
      revokeBtn.disabled = true;
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
          for (var i = 0; i < loadedItems.length; i++) {
            if (String(loadedItems[i].code || "") === code) {
              loadedItems[i].status = "revoked";
              loadedItems[i].can_revoke = false;
            }
          }
          renderRows();
          var okMsg = tr("console.js.invites.revoked", { code: code });
          showResult(resultEl, okMsg, false);
          if (window.ClaireUi && typeof window.ClaireUi.toast === "function") {
            window.ClaireUi.toast(okMsg, "ok");
          }
          return;
        }
        revokeBtn.disabled = false;
        showResult(resultEl, tr("console.js.invites.revoke_failed") + " " + ((res.body && (res.body.detail || res.body.error)) || res.status), true);
      }).catch(function () {
        revokeBtn.disabled = false;
        showResult(resultEl, tr("console.js.invites.revoke_network_error"), true);
      });
    });

    if (form) {
      form.addEventListener("submit", function (ev) {
        ev.preventDefault();
        var submitBtn = form.querySelector("button[type='submit']");
        if (submitBtn) submitBtn.disabled = true;
        var fd = new FormData(form);
        var rawMax = parseInt(String(fd.get("max_uses") || "1"), 10);
        var maxUses = Number.isFinite(rawMax) ? rawMax : 1;
        var reservedUsername = String(fd.get("reserved_username") || "").trim();
        var note = String(fd.get("note") || "").trim();
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
          if ((res.status === 200 || res.status === 201) && res.body && res.body.code) {
            var created = res.body;
            loadedItems = [created].concat(loadedItems.filter(function (it) {
              return String(it.code || "") !== String(created.code || "");
            }));
            renderRows();
            form.reset();
            showResult(resultEl, tr("console.js.invites.issued", { code: created.code }), false);
            return;
          }
          if (res.status === 403 && res.body && res.body.error === "invite_issuance_disabled") {
            showResult(resultEl, tr("console.js.invites.issuance_disabled"), true);
            return;
          }
          showResult(resultEl, tr("console.js.invites.issue_failed") + " " + ((res.body && (res.body.detail || res.body.error)) || res.status), true);
        }).catch(function () {
          showResult(resultEl, tr("console.js.invites.issue_network_error"), true);
        }).then(function () {
          if (submitBtn) submitBtn.disabled = false;
        });
      });
    }

    loadInvites("/api/invites?page=true", false);
  }

  function init() {
    var bootstrap = parseBootstrap();
    if (!bootstrap || bootstrap.area !== "settings" || bootstrap.section !== "invites") return;
    if (bootstrap.mode !== "network") return;
    initInvitesSection(bootstrap);
  }

  init();
})();
