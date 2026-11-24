/* The ordered language lists of the console (console_language_list in console_ui.py): the preferred languages of /account/profile and the server default language of /admin/server (ACCOUNT_PREFERRED_LANGUAGES_DESIGN.md).
 * Each text is a catalog key (ClaireI18n). The names of the languages come from Intl.DisplayNames in the interface language.
 */
(function () {
  "use strict";

  var tr = function (key, params) { return window.ClaireI18n.t(key, params); };
  // A language tag (BCP 47, lowercase): the same rule as accounts/languages.py.
  var TAG_RE = /^[a-z]{2,3}(?:-[a-z0-9]{2,8})*$/;
  var MAX_LANGUAGES = 10;

  function languageName(tag) {
    try {
      var names = new Intl.DisplayNames([document.documentElement.lang || "en"], { type: "language", fallback: "none" });
      return names.of(tag) || "";
    } catch (e) {
      return "";
    }
  }

  function tags(list) {
    return Array.prototype.map.call(list.querySelectorAll("li[data-lang]"), function (li) { return li.getAttribute("data-lang"); });
  }

  function showResult(form, state, text) {
    var out = form.querySelector("[data-language-result]");
    if (!out) return;
    out.setAttribute("data-state", state);
    out.textContent = text;
  }

  function nameItems(form) {
    form.querySelectorAll("[data-lang-name]").forEach(function (el) {
      var name = languageName(el.getAttribute("data-lang-name"));
      if (name) el.textContent = name;
    });
    form.querySelectorAll("datalist option").forEach(function (opt) {
      var name = languageName(opt.value);
      if (name) opt.label = name;
    });
  }

  // The first item cannot go up and the last item cannot go down.
  function syncButtons(list) {
    var items = list.querySelectorAll("li[data-lang]");
    items.forEach(function (li, idx) {
      var up = li.querySelector("[data-lang-up]");
      var down = li.querySelector("[data-lang-down]");
      if (up) up.disabled = idx === 0;
      if (down) down.disabled = idx === items.length - 1;
    });
  }

  function itemFor(list, tag, fixed) {
    var template = list.querySelector("li[data-lang]");
    var li = template.cloneNode(true);
    li.setAttribute("data-lang", tag);
    var name = li.querySelector("[data-lang-name]");
    name.setAttribute("data-lang-name", tag);
    name.textContent = languageName(tag) || tag;
    li.querySelector("code").textContent = tag;
    li.querySelector("[data-lang-remove]").disabled = tag === fixed;
    return li;
  }

  function bind(form) {
    if (form.dataset.languageBound === "1") return;
    form.dataset.languageBound = "1";
    var list = form.querySelector("[data-language-list]");
    var input = form.querySelector("[data-lang-input]");
    var fixed = form.getAttribute("data-fixed-language") || "en";
    nameItems(form);
    syncButtons(list);

    function add() {
      var tag = String(input.value || "").trim().replace(/_/g, "-").toLowerCase();
      if (!TAG_RE.test(tag)) { showResult(form, "error", tr("console.js.languages.invalid")); input.focus(); return; }
      if (tags(list).indexOf(tag) >= 0) { showResult(form, "error", tr("console.js.languages.duplicate")); input.focus(); return; }
      if (tags(list).length >= MAX_LANGUAGES) { showResult(form, "error", tr("console.js.languages.too_many", { max: MAX_LANGUAGES })); return; }
      // A new language goes before the fixed language, thus the order of the other items stays.
      var fixedItem = list.querySelector('li[data-lang="' + fixed + '"]');
      list.insertBefore(itemFor(list, tag, fixed), fixedItem);
      input.value = "";
      showResult(form, "", "");
      syncButtons(list);
    }

    form.addEventListener("click", function (ev) {
      var btn = ev.target.closest("button");
      if (!btn || !form.contains(btn)) return;
      var li = btn.closest("li[data-lang]");
      if (btn.hasAttribute("data-lang-up") && li && li.previousElementSibling) {
        list.insertBefore(li, li.previousElementSibling);
      } else if (btn.hasAttribute("data-lang-down") && li && li.nextElementSibling) {
        list.insertBefore(li.nextElementSibling, li);
      } else if (btn.hasAttribute("data-lang-remove") && li && li.getAttribute("data-lang") !== fixed) {
        li.remove();
      } else if (btn.hasAttribute("data-lang-add")) {
        add();
        return;
      } else if (btn.hasAttribute("data-lang-follow")) {
        save(null, btn);
        return;
      } else {
        return;
      }
      syncButtons(list);
      if (li && li.isConnected) btn.focus();
    });

    input.addEventListener("keydown", function (ev) {
      if (ev.key === "Enter") { ev.preventDefault(); add(); }
    });

    function save(value, button) {
      var buttons = Array.prototype.slice.call(form.querySelectorAll("button"));
      buttons.forEach(function (b) { b.disabled = true; });
      showResult(form, "busy", tr("console.js.common.saving"));
      var body = {};
      body[form.getAttribute("data-field")] = value;
      fetch(form.getAttribute("data-endpoint"), {
        method: form.getAttribute("data-method") || "PUT",
        credentials: "same-origin",
        headers: { "Accept": "application/json", "Content-Type": "application/json" },
        body: JSON.stringify(body)
      }).then(function (res) {
        return res.json().catch(function () { return null; }).then(function (data) { return { ok: res.ok, data: data }; });
      }).then(function (r) {
        if (r.ok && form.hasAttribute("data-reload-on-save")) {
          // The interface language follows the list: the page loads again in the new language.
          window.ClaireI18n.reload();
          return;
        }
        buttons.forEach(function (b) { b.disabled = false; });
        syncButtons(list);
        var code = r.data && r.data.error;
        showResult(form, r.ok ? "ok" : "error", r.ok ? tr("console.js.languages.saved") : tr("console.js.languages.save_failed", { error: code || "" }));
        if (button) button.focus();
      }).catch(function () {
        buttons.forEach(function (b) { b.disabled = false; });
        syncButtons(list);
        showResult(form, "error", tr("console.js.languages.save_failed", { error: "network" }));
      });
    }

    form.addEventListener("submit", function (ev) {
      ev.preventDefault();
      save(tags(list), ev.submitter);
    });
  }

  function init() {
    document.querySelectorAll("form[data-language-form]").forEach(bind);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
