/* Shared modern UI primitives (tabs, popover row menus, dialogs, validation, requests). */
(function (root) {
  "use strict";

  var seq = 0;
  function uid(prefix) {
    seq += 1;
    return (prefix || "claire-ui") + "-" + seq;
  }

  function t(key, params, fallback) {
    if (root.ClaireI18n && typeof root.ClaireI18n.t === "function") {
      var val = root.ClaireI18n.t(key, params);
      if (val !== key || !fallback) return val;
    }
    if (!fallback) return key;
    if (!params) return fallback;
    return String(fallback).replace(/\{(\w+)\}/g, function (_, k) {
      return Object.prototype.hasOwnProperty.call(params, k) ? String(params[k]) : "{" + k + "}";
    });
  }

  function esc(v) {
    if (v == null) return "";
    return String(v).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  function icon(name) {
    if (root.ClaireIcons && typeof root.ClaireIcons.svg === "function") {
      var s = root.ClaireIcons.svg(name);
      if (s) return s;
    }
    return '<span data-icon="' + esc(name) + '"></span>';
  }

  function hydrateIcons(scope) {
    if (root.ClaireIcons && typeof root.ClaireIcons.hydrate === "function") {
      root.ClaireIcons.hydrate(scope || document);
    }
  }

  function ensureAnnouncer() {
    var el = document.getElementById("claire-sr-announcer");
    if (el) return el;
    el = document.createElement("div");
    el.id = "claire-sr-announcer";
    el.className = "sr-only";
    el.setAttribute("role", "status");
    el.setAttribute("aria-live", "polite");
    el.setAttribute("aria-atomic", "true");
    document.body.appendChild(el);
    return el;
  }

  var announceTimer = null;
  function announce(message) {
    if (!message) return;
    clearTimeout(announceTimer);
    announceTimer = setTimeout(function () {
      var el = ensureAnnouncer();
      el.textContent = "";
      requestAnimationFrame(function () {
        el.textContent = String(message);
      });
    }, 500);
  }

  function toast(message, tone) {
    if (!message) return;
    var wrap = document.getElementById("claire-toast-wrap");
    if (!wrap) {
      wrap = document.createElement("div");
      wrap.id = "claire-toast-wrap";
      wrap.className = "claire-toast-wrap";
      wrap.setAttribute("aria-live", "polite");
      document.body.appendChild(wrap);
    }
    var item = document.createElement("div");
    item.className = "claire-toast" + (tone ? " " + tone : "");
    var span = document.createElement("span");
    span.textContent = String(message);
    var closeBtn = document.createElement("button");
    closeBtn.type = "button";
    closeBtn.className = "claire-btn claire-btn-sm";
    closeBtn.textContent = t("common.action.close", null, "Close");
    closeBtn.addEventListener("click", function () { item.remove(); });
    item.appendChild(span);
    item.appendChild(closeBtn);
    wrap.appendChild(item);
    setTimeout(function () {
      if (item.parentNode) item.remove();
    }, 4500);
  }

  function applyClosedByFallback(dlg) {
    if (!dlg || dlg.dataset.closedbyBound === "1") return;
    dlg.dataset.closedbyBound = "1";
    var downOutside = false;
    dlg.addEventListener("pointerdown", function (ev) {
      if (dlg.getAttribute("closedby") !== "any") { downOutside = false; return; }
      var rect = dlg.getBoundingClientRect();
      downOutside = ev.clientX < rect.left || ev.clientX > rect.right || ev.clientY < rect.top || ev.clientY > rect.bottom;
    });
    dlg.addEventListener("pointerup", function (ev) {
      if (!downOutside || dlg.getAttribute("closedby") !== "any") return;
      downOutside = false;
      var rect = dlg.getBoundingClientRect();
      var upOutside = ev.clientX < rect.left || ev.clientX > rect.right || ev.clientY < rect.top || ev.clientY > rect.bottom;
      if (upOutside && dlg.open) {
        dlg.close("backdrop");
      }
    });
  }

  function openDialog(opts) {
    var titleId = uid("dlg-title");
    var dlg = document.createElement("dialog");
    dlg.className = "claire-dlg";
    dlg.setAttribute("closedby", opts && opts.closedBy ? opts.closedBy : "any");
    dlg.setAttribute("aria-labelledby", titleId);
    if (opts && opts.width) dlg.style.width = "min(" + opts.width + ", calc(100vw - 24px))";

    var head = document.createElement("div");
    head.className = "claire-dlg-head";
    var h2 = document.createElement("h2");
    h2.id = titleId;
    h2.innerHTML = (opts && opts.icon ? icon(opts.icon) + " " : "") + esc((opts && opts.title) || "");
    var xBtn = document.createElement("button");
    xBtn.type = "button";
    xBtn.className = "claire-icon-btn";
    xBtn.setAttribute("aria-label", t("common.action.close", null, "Close"));
    xBtn.innerHTML = icon("close");
    xBtn.addEventListener("click", function () { dlg.close("cancel"); });
    head.appendChild(h2);
    head.appendChild(xBtn);

    var body = document.createElement("div");
    body.className = "claire-dlg-body";
    if (opts && opts.bodyEl) {
      body.appendChild(opts.bodyEl);
    } else if (opts && opts.bodyHtml) {
      body.innerHTML = opts.bodyHtml;
    }

    var foot = document.createElement("div");
    foot.className = "claire-dlg-foot";
    var cancelBtn = document.createElement("button");
    cancelBtn.type = "button";
    cancelBtn.className = "claire-btn sec";
    cancelBtn.textContent = (opts && opts.cancelLabel) || t("common.action.cancel", null, "Cancel");
    if (opts && opts.danger) cancelBtn.setAttribute("autofocus", "");
    cancelBtn.addEventListener("click", function () { dlg.close("cancel"); });

    var submitBtn = null;
    if (!opts || opts.submitLabel !== null) {
      submitBtn = document.createElement("button");
      submitBtn.type = "button";
      submitBtn.className = "claire-btn " + (opts && opts.danger ? "danger" : "pri");
      submitBtn.textContent = (opts && opts.submitLabel) || t("kb.ui.confirm", null, "Confirm");
      submitBtn.addEventListener("click", function () {
        if (opts && typeof opts.onSubmit === "function") {
          Promise.resolve(opts.onSubmit(dlg, body, submitBtn)).then(function (keepOpen) {
            if (keepOpen !== false && dlg.open) dlg.close("confirm");
          });
        } else {
          dlg.close("confirm");
        }
      });
    }

    if (opts && opts.extraFooterButtons) {
      opts.extraFooterButtons.forEach(function (btnEl) { foot.appendChild(btnEl); });
    }
    foot.appendChild(cancelBtn);
    if (submitBtn) foot.appendChild(submitBtn);

    dlg.appendChild(head);
    dlg.appendChild(body);
    dlg.appendChild(foot);
    document.body.appendChild(dlg);

    if (!("closedBy" in HTMLDialogElement.prototype)) {
      applyClosedByFallback(dlg);
    }

    return new Promise(function (resolve) {
      dlg.addEventListener("close", function () {
        var val = dlg.returnValue || "";
        dlg.returnValue = "";
        dlg.remove();
        resolve(val);
      }, { once: true });
      enhance(dlg);
      dlg.showModal();
      if (opts && typeof opts.onOpen === "function") opts.onOpen(dlg, body, submitBtn);
    });
  }

  function stepUp() {
    var verified = false;
    var errEl = null;
    var pwInput = null;
    var body = document.createElement("form");
    body.className = "claire-field";
    body.innerHTML =
      '<p class="claire-hint">' + esc(t("kb.ui.step_up_hint", null, "Confirm your current session or enter your account password to authorize this sensitive action.")) + "</p>" +
      '<div class="claire-field">' +
      '<label class="claire-field-label" for="claire-stepup-pw">' + esc(t("kb.ui.step_up_password", null, "Account password (leave empty if passwordless session)")) + "</label>" +
      '<input id="claire-stepup-pw" name="password" type="password" class="claire-input" autocomplete="current-password" aria-invalid="false" required/>' +
      '<span class="claire-error" id="claire-stepup-err" hidden></span>' +
      "</div>";
    pwInput = body.querySelector("#claire-stepup-pw");
    errEl = body.querySelector("#claire-stepup-err");

    return openDialog({
      title: t("kb.ui.step_up_title", null, "Recent authentication required"),
      icon: "lock",
      bodyEl: body,
      submitLabel: t("kb.ui.step_up_submit", null, "Verify"),
      onOpen: function (dlg, _b, submitBtn) {
        body.addEventListener("submit", function (ev) {
          ev.preventDefault();
          if (submitBtn) submitBtn.click();
        });
        if (pwInput) pwInput.focus();
      },
      onSubmit: function (_dlg, _b, submitBtn) {
        if (submitBtn) submitBtn.disabled = true;
        errEl.hidden = true;
        pwInput.setAttribute("aria-invalid", "false");
        // POST /api/auth/reauth takes the password of the account as a string.
        var payload = { password: pwInput.value };
        return fetch("/api/auth/reauth", {
          method: "POST",
          credentials: "same-origin",
          headers: { "Accept": "application/json", "Content-Type": "application/json" },
          body: JSON.stringify(payload)
        }).then(function (resp) {
          return resp.json().catch(function () { return {}; }).then(function (data) {
            if (resp.ok) {
              verified = true;
              return true;
            }
            if (submitBtn) submitBtn.disabled = false;
            // POST /api/auth/reauth gives 403 "invalid_password", or 409 "password_not_set" for an account that has no password; each other code gets the text of errorText.
            var code = String((data && (data.error || data.detail)) || ("HTTP " + resp.status));
            var msg = code === "invalid_password" ? t("kb.ui.step_up_failed") : (code === "password_not_set" ? t("kb.ui.step_up_no_password") : errorText({ error: code }));
            // No setCustomValidity here: a field with a custom validity message stops the submit event of the form, and the Enter key then sends nothing after the first failure.
            pwInput.setAttribute("aria-invalid", "true");
            pwInput.setAttribute("aria-describedby", errEl.id);
            errEl.textContent = msg;
            errEl.hidden = false;
            announce(msg);
            return false;
          });
        }).catch(function () {
          if (submitBtn) submitBtn.disabled = false;
          var msg = t("kb.ui.network_error", null, "Network request failed.");
          errEl.textContent = msg;
          errEl.hidden = false;
          return false;
        });
      }
    }).then(function () {
      return verified;
    });
  }

  function request(url, opts) {
    var options = opts || {};
    var headers = Object.assign({ "Accept": "application/json" }, options.headers || {});
    var init = {
      method: options.method || "GET",
      credentials: "same-origin",
      headers: headers
    };
    if (options.body !== undefined) {
      if (typeof options.body === "string") {
        init.body = options.body;
      } else {
        headers["Content-Type"] = "application/json";
        init.body = JSON.stringify(options.body);
      }
    }
    return fetch(url, init).then(function (resp) {
      return resp.json().catch(function () { return null; }).then(function (data) {
        // A route that needs a recent sign-in answers 401 "reauth_required" for an old session (design Section 6.8): the re-authentication dialog opens, then the request goes one more time.
        if (resp.status === 401 && data && data.error === "reauth_required" && !options._retried) {
          return stepUp().then(function (ok) {
            if (!ok) return { ok: false, status: 401, data: data, error: "reauth_cancelled" };
            return request(url, Object.assign({}, options, { _retried: true }));
          });
        }
        return {
          ok: resp.ok,
          status: resp.status,
          data: data,
          error: !resp.ok ? ((data && (data.error || data.detail)) || ("HTTP " + resp.status)) : null
        };
      });
    }).catch(function () {
      return { ok: false, status: 0, data: null, error: "network_error" };
    });
  }

  // The text of a failed request or of a reason code: the catalog text of the code ("kb.err.<code>", or "kb.reason.<code>" for a reason code of a decision) when there is one, else the general text with the code.
  function errorText(res) {
    var code = String((res && res.error) || "");
    // The knowledge base routes give 404 with the text "theme not found" for a knowledge base that is not there or that the requester cannot read.
    if (code === "theme not found") code = "kb_not_found";
    if (/^[a-z0-9_]+$/.test(code)) {
      var keys = ["kb.err." + code, "kb.reason." + code];
      for (var i = 0; i < keys.length; i++) {
        var text = t(keys[i]);
        if (text !== keys[i]) return text;
      }
    }
    return t("kb.err.failed", { error: code });
  }

  function enhanceTabs(rootNode) {
    var scope = rootNode || document;
    scope.querySelectorAll(".claire-tabs").forEach(function (container) {
      if (container.dataset.tabsBound === "1") return;
      container.dataset.tabsBound = "1";
      var wrap = container.querySelector(".claire-tablist-wrap");
      var list = container.querySelector('[role="tablist"]');
      if (!list) return;
      var tabs = Array.prototype.slice.call(list.querySelectorAll('[role="tab"]'));
      var panels = Array.prototype.slice.call(container.querySelectorAll(':scope > .claire-tabpanels > [role="tabpanel"]'));

      function syncFade() {
        if (!wrap) return;
        var canLeft = list.scrollLeft > 4;
        var canRight = list.scrollWidth - list.clientWidth - list.scrollLeft > 4;
        wrap.classList.toggle("can-scroll-left", canLeft);
        wrap.classList.toggle("can-scroll-right", canRight);
      }

      function activate(tabId, focusTab) {
        tabs.forEach(function (tab, idx) {
          var selected = (tab.getAttribute("data-tab") || String(idx)) === tabId;
          tab.setAttribute("aria-selected", selected ? "true" : "false");
          if (selected) tab.setAttribute("focusgroupstart", "");
          else tab.removeAttribute("focusgroupstart");
          if (!("focusGroup" in HTMLElement.prototype)) {
            tab.tabIndex = selected ? 0 : -1;
          }
          if (selected && focusTab) tab.focus();
        });
        panels.forEach(function (panel, idx) {
          var pid = panel.getAttribute("data-tabpanel") || String(idx);
          if (pid === tabId) {
            panel.removeAttribute("hidden");
          } else {
            panel.setAttribute("hidden", "until-found");
          }
        });
        container.dispatchEvent(new CustomEvent("claire:tabchange", { detail: { tab: tabId }, bubbles: true }));
      }

      tabs.forEach(function (tab, idx) {
        var tabId = tab.getAttribute("data-tab") || String(idx);
        tab.addEventListener("click", function () { activate(tabId, false); });
      });

      if (!("focusGroup" in HTMLElement.prototype)) {
        list.addEventListener("keydown", function (ev) {
          var curIdx = tabs.indexOf(document.activeElement);
          if (curIdx < 0) return;
          var nextIdx = -1;
          if (ev.key === "ArrowRight") nextIdx = (curIdx + 1) % tabs.length;
          else if (ev.key === "ArrowLeft") nextIdx = (curIdx - 1 + tabs.length) % tabs.length;
          else if (ev.key === "Home") nextIdx = 0;
          else if (ev.key === "End") nextIdx = tabs.length - 1;
          if (nextIdx >= 0) {
            ev.preventDefault();
            var nextId = tabs[nextIdx].getAttribute("data-tab") || String(nextIdx);
            activate(nextId, true);
          }
        });
      }

      panels.forEach(function (panel, idx) {
        panel.addEventListener("beforematch", function () {
          var pid = panel.getAttribute("data-tabpanel") || String(idx);
          activate(pid, false);
        });
      });

      list.addEventListener("scroll", syncFade, { passive: true });
      if (typeof ResizeObserver === "function") {
        new ResizeObserver(syncFade).observe(list);
      }
      syncFade();
    });
  }

  function positionFallbackPopover(trigger, pop) {
    if (typeof CSS !== "undefined" && typeof CSS.supports === "function" && CSS.supports("position-area", "block-end")) {
      return;
    }
    var trRect = trigger.getBoundingClientRect();
    var popW = pop.offsetWidth || 200;
    var popH = pop.offsetHeight || 160;
    var left = Math.min(window.innerWidth - popW - 8, Math.max(8, trRect.right - popW));
    var top = trRect.bottom + 4;
    if (top + popH > window.innerHeight - 8 && trRect.top - popH - 4 >= 8) {
      top = trRect.top - popH - 4;
    }
    pop.style.left = Math.round(left) + "px";
    pop.style.top = Math.round(top) + "px";
  }

  function enhanceMenus(rootNode) {
    var scope = rootNode || document;
    var hasInvokers = "commandForElement" in HTMLButtonElement.prototype;
    scope.querySelectorAll('button[commandfor][popover], button[commandfor][data-menu-pop], .claire-menu-trigger[commandfor]').forEach(function (btn) {
      if (btn.dataset.menuBound === "1") return;
      btn.dataset.menuBound = "1";
      var targetId = btn.getAttribute("commandfor");
      var pop = targetId ? document.getElementById(targetId) : null;
      if (!pop) return;

      var pointerActivated = false;
      btn.addEventListener("pointerdown", function () { pointerActivated = true; });
      btn.addEventListener("keydown", function (ev) {
        pointerActivated = false;
        if (ev.key === "ArrowDown") {
          ev.preventDefault();
          if (typeof pop.showPopover === "function") pop.showPopover();
        }
      });

      if (!hasInvokers) {
        btn.addEventListener("click", function () {
          if (typeof pop.togglePopover === "function") {
            pop.togglePopover();
          } else {
            var open = pop.classList.toggle(":popover-open");
            btn.setAttribute("aria-expanded", open ? "true" : "false");
            if (open) positionFallbackPopover(btn, pop);
          }
        });
      }

      pop.addEventListener("toggle", function (ev) {
        var open = ev.newState === "open";
        btn.setAttribute("aria-expanded", open ? "true" : "false");
        if (open) {
          positionFallbackPopover(btn, pop);
          var firstItem = pop.querySelector('[role="menuitem"]:not(:disabled), a[role="menuitem"]');
          if (!pointerActivated && firstItem) {
            firstItem.focus();
          } else if (pointerActivated) {
            var menu = pop.querySelector('[role="menu"]');
            if (menu && typeof menu.focus === "function") {
              try { menu.focus({ focusVisible: false }); } catch (_) {}
            }
          }
          pointerActivated = false;
        }
      });

      pop.addEventListener("click", function (ev) {
        var item = ev.target && ev.target.closest ? ev.target.closest('[role="menuitem"]') : null;
        if (item && !item.disabled && typeof pop.hidePopover === "function") {
          pop.hidePopover();
        }
      });

      if (!("focusGroup" in HTMLElement.prototype)) {
        pop.addEventListener("keydown", function (ev) {
          var items = Array.prototype.slice.call(pop.querySelectorAll('[role="menuitem"]:not(:disabled)'));
          if (!items.length) return;
          var idx = items.indexOf(document.activeElement);
          if (ev.key === "ArrowDown") {
            ev.preventDefault();
            items[(idx + 1) % items.length].focus();
          } else if (ev.key === "ArrowUp") {
            ev.preventDefault();
            items[(idx - 1 + items.length) % items.length].focus();
          } else if (ev.key === "Home") {
            ev.preventDefault();
            items[0].focus();
          } else if (ev.key === "End") {
            ev.preventDefault();
            items[items.length - 1].focus();
          }
        });
      }
    });
  }

  function enhanceValidation(rootNode) {
    var scope = rootNode || document;
    scope.querySelectorAll(".claire-input[data-validate], .claire-select[data-validate], .claire-textarea[data-validate]").forEach(function (ctrl) {
      if (ctrl.dataset.valBound === "1") return;
      ctrl.dataset.valBound = "1";
      if (!ctrl.hasAttribute("aria-invalid")) ctrl.setAttribute("aria-invalid", "false");
      var errId = ctrl.getAttribute("data-error-id");
      var errEl = errId ? document.getElementById(errId) : null;

      function sync(announceError) {
        var invalid = !ctrl.checkValidity();
        ctrl.setAttribute("aria-invalid", invalid ? "true" : "false");
        if (errEl) {
          if (invalid) {
            errEl.textContent = ctrl.validationMessage || t("kb.ui.invalid_input", null, "Please check this field.");
            errEl.hidden = false;
            ctrl.setAttribute("aria-describedby", errEl.id);
            if (announceError) announce(errEl.textContent);
          } else {
            errEl.textContent = "";
            errEl.hidden = true;
            if (ctrl.getAttribute("aria-describedby") === errEl.id) ctrl.removeAttribute("aria-describedby");
          }
        }
      }

      ctrl.addEventListener("blur", function () { sync(true); });
      ctrl.addEventListener("invalid", function () { sync(true); });
      ctrl.addEventListener("input", function () {
        if (ctrl.getAttribute("aria-invalid") === "true") sync(false);
      });
    });
  }

  function enhance(rootNode) {
    var scope = rootNode || document;
    hydrateIcons(scope);
    enhanceTabs(scope);
    enhanceMenus(scope);
    enhanceValidation(scope);
  }

  function renderChips(opts) {
    var name = opts.name || uid("chips");
    var html = '<fieldset class="claire-chips" aria-label="' + esc(opts.label || "") + '">';
    if (opts.label) html += '<legend class="sr-only">' + esc(opts.label) + "</legend>";
    (opts.options || []).forEach(function (opt) {
      var checked = String(opt.value) === String(opts.value) ? " checked" : "";
      html += '<label class="claire-chip"><input type="radio" name="' + esc(name) + '" value="' + esc(opt.value) + '"' + checked + "/><span>" + esc(opt.label) + "</span></label>";
    });
    html += "</fieldset>";
    return html;
  }

  function renderTabs(opts) {
    var tabs = opts.tabs || [];
    var active = String(opts.active || (tabs[0] && tabs[0].id) || "");
    var h = '<div class="claire-tabs" id="' + esc(opts.id || uid("tabs")) + '">';
    h += '<div class="claire-tablist-wrap"><div class="claire-tablist" role="tablist" focusgroup="tablist nomemory" aria-label="' + esc(opts.ariaLabel || "") + '">';
    tabs.forEach(function (tab) {
      var sel = String(tab.id) === active;
      var badge = tab.badge != null ? '<span class="claire-tab-badge">' + esc(tab.badge) + "</span>" : "";
      h += '<button type="button" class="claire-tab" role="tab" data-tab="' + esc(tab.id) + '" aria-selected="' + (sel ? "true" : "false") + '"' + (sel ? " focusgroupstart" : "") + ">";
      if (tab.icon) h += icon(tab.icon);
      h += "<span>" + esc(tab.label) + "</span>" + badge + "</button>";
    });
    h += '</div></div><div class="claire-tabpanels">';
    tabs.forEach(function (tab) {
      var sel = String(tab.id) === active;
      var hid = sel ? "" : ' hidden="until-found"';
      h += '<div role="tabpanel" data-tabpanel="' + esc(tab.id) + '" aria-label="' + esc(tab.label) + '"' + hid + '><div class="claire-panel-inner">' + (tab.html || "") + "</div></div>";
    });
    h += "</div></div>";
    return h;
  }

  function renderTable(opts) {
    var captionId = uid("tbl");
    var cols = opts.columns || [];
    var rows = opts.rows || [];
    var h = '<div class="claire-tw" tabindex="0" role="region" aria-labelledby="' + captionId + '">';
    h += '<table class="claire-table"><caption id="' + captionId + '" class="sr-only">' + esc(opts.caption || "") + "</caption><thead><tr>";
    cols.forEach(function (c) { h += "<th>" + esc(c.label) + "</th>"; });
    h += "</tr></thead><tbody>";
    if (!rows.length) {
      h += '<tr><td colspan="' + Math.max(1, cols.length) + '" style="text-align:center;color:var(--muted);padding:16px">' + esc(opts.emptyText || t("kb.ui.empty_list", null, "No items yet.")) + "</td></tr>";
    } else {
      rows.forEach(function (row) {
        h += "<tr>";
        cols.forEach(function (c, i) {
          var cellHtml = typeof c.render === "function" ? c.render(row) : esc(row[c.key]);
          var cls = c.actions ? ' class="claire-cell-actions"' : "";
          var dl = c.label ? ' data-label="' + esc(c.label) + '"' : "";
          h += "<td" + cls + dl + ">" + (i >= 0 ? cellHtml : "") + "</td>";
        });
        h += "</tr>";
      });
    }
    h += "</tbody></table></div>";
    return h;
  }

  function renderRowMenu(items, ariaLabel) {
    var id = uid("popmenu");
    var btnId = id + "-btn";
    var h = '<button type="button" id="' + btnId + '" class="claire-icon-btn claire-menu-trigger" commandfor="' + id + '" command="toggle-popover" aria-haspopup="menu" aria-expanded="false" aria-label="' + esc(ariaLabel || t("kb.ui.row_actions", null, "More actions")) + '">' + icon("more") + "</button>";
    h += '<div id="' + id + '" popover="auto" class="claire-menu-pop"><div focusgroup="menu nomemory" role="menu" aria-labelledby="' + btnId + '" class="claire-menu-list">';
    (items || []).forEach(function (it) {
      if (it.sep) {
        h += '<div class="claire-menu-sep" role="separator"></div>';
        return;
      }
      var cls = "claire-menu-item" + (it.danger ? " danger" : "");
      var attrs = ' role="menuitem"' + (it.action ? ' data-menu-action="' + esc(it.action) + '"' : "") + (it.value != null ? ' data-menu-value="' + esc(it.value) + '"' : "") + (it.disabled ? " disabled" : "");
      if (it.href) {
        h += '<a class="' + cls + '" href="' + esc(it.href) + '"' + attrs + ">" + (it.icon ? icon(it.icon) : "") + "<span>" + esc(it.label) + "</span></a>";
      } else {
        h += '<button type="button" class="' + cls + '"' + attrs + ">" + (it.icon ? icon(it.icon) : "") + "<span>" + esc(it.label) + "</span></button>";
      }
    });
    h += "</div></div>";
    return h;
  }

  root.ClaireUi = {
    t: t,
    esc: esc,
    icon: icon,
    hydrateIcons: hydrateIcons,
    announce: announce,
    toast: toast,
    openDialog: openDialog,
    stepUp: stepUp,
    request: request,
    errorText: errorText,
    enhance: enhance,
    renderChips: renderChips,
    renderTabs: renderTabs,
    renderTable: renderTable,
    renderRowMenu: renderRowMenu
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () { enhance(document); });
  } else {
    enhance(document);
  }
})(window);
