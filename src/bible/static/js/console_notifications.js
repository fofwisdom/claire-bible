/* Claire console controller for /account/notifications (docs/design/PWA_NOTIFICATION_DESIGN.md PN4 rule 3).
 * APIs: /api/me/push, /api/me/push/subscriptions, /api/me/push/kinds/{kind}, /api/me/push/test.
 * Server text goes into the DOM only through textContent. No inline handlers.
 */
(function () {
  "use strict";

  var card = document.querySelector("[data-notify-device]");
  if (!card) return;

  var PUSH_PATH = "/api/me/push";
  var stateEl = card.querySelector("[data-notify-device-state]");
  var resultEl = card.querySelector("[data-notify-result]");
  var enableBtn = card.querySelector('[data-notify-action="enable"]');
  var disableBtn = card.querySelector('[data-notify-action="disable"]');
  var testBtn = card.querySelector('[data-notify-action="test"]');
  var devicesBody = document.getElementById("notify-devices-body");
  var devicesEmpty = document.querySelector("[data-notify-devices-empty]");
  var rowTemplate = document.getElementById("notify-device-row");
  var publicKey = card.getAttribute("data-notify-public-key") || "";

  function tr(key, params) {
    return window.ClaireI18n ? window.ClaireI18n.t(key, params) : key;
  }

  function supported() {
    return "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
  }

  function setResult(el, text, isError) {
    if (!el) return;
    el.textContent = text || "";
    el.setAttribute("data-state", isError ? "error" : "ok");
  }

  function errorText(code, status) {
    var key = "console.js.notify.error." + code;
    var text = tr(key);
    return text === key ? tr("console.js.notify.error.request", { status: status || code }) : text;
  }

  function api(method, path, body) {
    var options = { method: method, credentials: "same-origin", headers: { Accept: "application/json" } };
    if (body !== undefined) {
      options.headers["Content-Type"] = "application/json";
      options.body = JSON.stringify(body);
    }
    return fetch(path, options).then(function (res) {
      return res.json().catch(function () { return {}; }).then(function (data) {
        if (!res.ok) {
          var err = new Error(data.error || "http_" + res.status);
          err.code = data.error || "request";
          err.status = res.status;
          throw err;
        }
        return data;
      });
    });
  }

  function keyBytes(text) {
    var padded = text.replace(/-/g, "+").replace(/_/g, "/");
    while (padded.length % 4) padded += "=";
    var raw = atob(padded);
    var out = new Uint8Array(raw.length);
    for (var i = 0; i < raw.length; i += 1) out[i] = raw.charCodeAt(i);
    return out;
  }

  // A subscription that was made with a different server key cannot get messages of this server.
  function hasServerKey(sub) {
    var key = sub.options && sub.options.applicationServerKey;
    if (!key) return false;
    var have = new Uint8Array(key);
    var want = keyBytes(publicKey);
    if (have.length !== want.length) return false;
    for (var i = 0; i < have.length; i += 1) if (have[i] !== want[i]) return false;
    return true;
  }

  // The digest that the server gives for each device (the first 16 hexadecimal characters of the SHA-256 of the endpoint).
  function endpointRef(endpoint) {
    return crypto.subtle.digest("SHA-256", new TextEncoder().encode(endpoint)).then(function (buf) {
      return Array.prototype.map.call(new Uint8Array(buf), function (b) { return ("0" + b.toString(16)).slice(-2); }).join("").slice(0, 16);
    });
  }

  function registration() {
    return navigator.serviceWorker.getRegistration("/").then(function (reg) {
      return reg || navigator.serviceWorker.register("/sw.js", { scope: "/" });
    }).then(function () { return navigator.serviceWorker.ready; });
  }

  function currentSubscription() {
    return registration().then(function (reg) { return reg.pushManager.getSubscription(); });
  }

  function formatTs(seconds) {
    if (typeof seconds !== "number") return "-";
    return new Date(seconds * 1000).toISOString().slice(0, 19).replace("T", " ") + " UTC";
  }

  function renderDevices(subscriptions) {
    if (!devicesBody || !rowTemplate) return;
    devicesBody.textContent = "";
    subscriptions.forEach(function (sub) {
      var row = rowTemplate.content.firstElementChild.cloneNode(true);
      row.setAttribute("data-notify-sub", String(sub.id));
      row.setAttribute("data-notify-ref", sub.ref);
      row.querySelector("[data-notify-sub-label]").textContent = sub.label || row.getAttribute("data-notify-unknown") || "";
      row.querySelector("[data-notify-sub-added]").textContent = formatTs(sub.created_at);
      row.querySelector("[data-notify-sub-sent]").textContent = formatTs(sub.last_success_at);
      row.querySelector("[data-notify-remove]").setAttribute("data-notify-remove", String(sub.id));
      devicesBody.appendChild(row);
    });
    if (devicesEmpty) devicesEmpty.hidden = subscriptions.length > 0;
  }

  function reloadDevices() {
    return api("GET", PUSH_PATH).then(function (state) {
      publicKey = state.public_key || publicKey;
      renderDevices(Array.isArray(state.subscriptions) ? state.subscriptions : []);
    });
  }

  function showState(name, canEnable, canDisable) {
    if (stateEl) {
      stateEl.textContent = tr("console.js.notify.state." + name);
      stateEl.setAttribute("data-notify-device-state", name);
    }
    if (enableBtn) enableBtn.hidden = !canEnable;
    if (disableBtn) disableBtn.hidden = !canDisable;
  }

  function refresh() {
    if (!supported()) { showState("unsupported", false, false); return Promise.resolve(); }
    if (Notification.permission === "denied") { showState("denied", false, false); return Promise.resolve(); }
    return currentSubscription().then(function (sub) {
      if (!sub) { showState("off", true, false); return null; }
      return endpointRef(sub.endpoint).then(function (ref) {
        var mine = null;
        Array.prototype.forEach.call(devicesBody ? devicesBody.querySelectorAll("[data-notify-ref]") : [], function (row) {
          var pill = row.querySelector("[data-notify-this]");
          var match = row.getAttribute("data-notify-ref") === ref;
          if (pill) pill.hidden = !match;
          if (match) mine = row;
        });
        if (mine && hasServerKey(sub)) showState("on", false, true);
        else showState("other_account", true, false);
      });
    }).catch(function () { showState("off", true, false); });
  }

  function enable() {
    setResult(resultEl, "");
    return Notification.requestPermission().then(function (permission) {
      if (permission !== "granted") { var denied = new Error("permission"); denied.code = "permission"; throw denied; }
      return registration();
    }).then(function (reg) {
      return reg.pushManager.getSubscription().then(function (sub) {
        if (sub && !hasServerKey(sub)) return sub.unsubscribe().then(function () { return null; });
        return sub;
      }).then(function (sub) {
        if (sub) return sub;
        return reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes(publicKey) }).catch(function () {
          var failed = new Error("subscribe"); failed.code = "subscribe"; throw failed;
        });
      });
    }).then(function (sub) {
      return api("POST", PUSH_PATH + "/subscriptions", sub.toJSON());
    }).then(reloadDevices).then(refresh).then(function () {
      setResult(resultEl, tr("console.js.notify.enabled"));
    }).catch(function (err) {
      setResult(resultEl, errorText(err.code || "request", err.status), true);
      return refresh();
    });
  }

  function disable() {
    setResult(resultEl, "");
    return currentSubscription().then(function (sub) {
      if (!sub) return null;
      return api("DELETE", PUSH_PATH + "/subscriptions", { endpoint: sub.endpoint }).then(function () { return sub.unsubscribe(); });
    }).then(reloadDevices).then(refresh).then(function () {
      setResult(resultEl, tr("console.js.notify.disabled"));
    }).catch(function (err) {
      setResult(resultEl, errorText(err.code || "request", err.status), true);
    });
  }

  function sendTest() {
    setResult(resultEl, "");
    return api("POST", PUSH_PATH + "/test", {}).then(function () {
      var hasDevice = !!(devicesBody && devicesBody.querySelector("[data-notify-sub]"));
      setResult(resultEl, tr(hasDevice ? "console.js.notify.test_sent" : "console.js.notify.test_sent_no_device"));
      if (window.ClaireAppBar && window.ClaireAppBar.refreshNotifications) window.ClaireAppBar.refreshNotifications();
    }).catch(function (err) {
      setResult(resultEl, errorText(err.code || "request", err.status), true);
    });
  }

  function removeDevice(row) {
    var id = Number(row.getAttribute("data-notify-sub"));
    var ref = row.getAttribute("data-notify-ref");
    setResult(resultEl, "");
    return api("DELETE", PUSH_PATH + "/subscriptions", { id: id }).then(function () {
      // When the removed device is this browser, the browser also ends its subscription.
      if (!supported()) return null;
      return currentSubscription().then(function (sub) {
        if (!sub) return null;
        return endpointRef(sub.endpoint).then(function (mine) { return mine === ref ? sub.unsubscribe() : null; });
      }).catch(function () { return null; });
    }).then(reloadDevices).then(refresh).then(function () {
      setResult(resultEl, tr("console.js.notify.removed"));
    }).catch(function (err) {
      setResult(resultEl, errorText(err.code || "request", err.status), true);
    });
  }

  function saveKind(input) {
    var kind = input.getAttribute("data-notify-kind");
    var cardEl = input.closest("[data-notify-channel]");
    var result = cardEl ? cardEl.querySelector("[data-notify-kind-result]") : null;
    var wanted = input.checked;
    input.disabled = true;
    return api("PUT", PUSH_PATH + "/kinds/" + encodeURIComponent(kind), { push: wanted }).then(function () {
      setResult(result, tr("console.js.notify.kind_saved"));
    }).catch(function (err) {
      input.checked = !wanted;
      setResult(result, errorText(err.code || "request", err.status), true);
    }).then(function () { input.disabled = false; });
  }

  if (enableBtn) enableBtn.addEventListener("click", function () { enableBtn.disabled = true; enable().then(function () { enableBtn.disabled = false; }); });
  if (disableBtn) disableBtn.addEventListener("click", function () { disableBtn.disabled = true; disable().then(function () { disableBtn.disabled = false; }); });
  if (testBtn) testBtn.addEventListener("click", function () { testBtn.disabled = true; sendTest().then(function () { testBtn.disabled = false; }); });
  if (devicesBody) {
    devicesBody.addEventListener("click", function (ev) {
      var btn = ev.target && ev.target.closest ? ev.target.closest("[data-notify-remove]") : null;
      var row = btn ? btn.closest("[data-notify-sub]") : null;
      if (row) removeDevice(row);
    });
  }
  Array.prototype.forEach.call(document.querySelectorAll("[data-notify-kind]"), function (input) {
    input.addEventListener("change", function () { saveKind(input); });
  });

  refresh();
})();
