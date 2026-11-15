/* Claire Bible — compose controls of the federation client in /syndication (FEDERATION_REMOTE_READING_DESIGN.md Sections 5.3 and 5.4, unit FP4-9).
 *
 * Rules of this file:
 * - The owner writes plain text. The server makes the HTML of the note and sanitizes it. This file never puts note HTML into the page: the body of a note shows in the sandbox frame of the reader, as each other post.
 * - The controls of an item are reply, favourite, and boost. An own note has edit and delete in place of favourite and boost, and shows its counters.
 * - Each text is a catalog key (ClaireI18n). Each value from the server goes through esc().
 * - syndication_client.js gives the item actions a place (actionsHtml), sends the clicks of the client panel here (handleClick), and gives its list functions (init).
 */
(function () {
  "use strict";

  const cfg = window.__CLAIRE_CONFIG || {};
  const I18n = window.ClaireI18n;
  if (!I18n) return;
  const ic = (name) => (window.ClaireIcons ? window.ClaireIcons.svg(name) : "");
  const tr = (key, params) => I18n.t(key, params);
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const $ = (id) => document.getElementById(id);

  const AVAILABLE = cfg.authScope === "owner" && cfg.instanceMode === "network";
  const KNOWN_ERRORS = ["not_actor", "empty_content", "content_too_long", "summary_too_long", "too_many_mentions", "too_many_tags", "invalid_language", "invalid_mention", "blocked_domain", "item_not_found", "note_not_found", "own_item"];
  // mode: "new", "reply", or "edit". item: the item of a reply or of an edit.
  const S = { mode: "new", item: null, busy: false };
  let hooks = { findItem: () => null, applyItem: () => {}, renderClient: () => {}, refresh: () => {}, removed: () => {}, ticker: () => {} };

  async function api(method, path, body) {
    const init = { method, headers: { Accept: "application/json" }, credentials: "same-origin" };
    if (body !== undefined) {
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(body);
    }
    const resp = await fetch(path, init);
    const data = await resp.json().catch(() => ({}));
    return { ok: resp.ok, status: resp.status, data };
  }

  function errorText(result) {
    const code = result.data && result.data.error;
    if (KNOWN_ERRORS.includes(code)) return tr(`synd.compose.error.${code}`, { limit: (result.data && result.data.limit) || "" });
    return tr("synd.error.request", { error: String(code || `HTTP ${result.status}`) });
  }

  // --- Controls of one item --------------------------------------------------

  function toggleButton(attr, item, on, icon, labelOn, labelOff, mini) {
    const label = tr(on ? labelOn : labelOff);
    return `<button type="button" class="${mini ? "tt-icon-btn" : "tt-btn"} synd-act ${on ? "is-on" : ""}" ${attr}="${esc(item.id)}" title="${esc(label)}" aria-label="${esc(label)}" aria-pressed="${on ? "true" : "false"}">${ic(icon)}${mini ? "" : ` ${esc(label)}`}</button>`;
  }

  function plainButton(attr, item, icon, key, mini) {
    const label = tr(key);
    return `<button type="button" class="${mini ? "tt-icon-btn" : "tt-btn"} synd-act" ${attr}="${esc(item.id)}" title="${esc(label)}" aria-label="${esc(label)}">${ic(icon)}${mini ? "" : ` ${esc(label)}`}</button>`;
  }

  // The controls of an item of the timeline. mini: icons only (the list); if not, icons with their text (the reader).
  function actionsHtml(item, mini) {
    if (!AVAILABLE || !item) return "";
    const parts = [plainButton("data-note-reply", item, "comment", "synd.compose.action.reply", mini)];
    if (item.note) {
      parts.push(plainButton("data-note-edit", item, "edit", "synd.compose.action.edit", mini));
      parts.push(plainButton("data-note-delete", item, "trash", "synd.compose.action.delete", mini));
      parts.push(`<span class="synd-note synd-note-counters">${esc(tr("synd.compose.counters", { likes: item.note.likes || 0, boosts: item.note.announces || 0 }))}${item.note.edited_at ? ` · ${esc(tr("synd.compose.edited"))}` : ""}</span>`);
    } else {
      parts.push(toggleButton("data-item-favourite", item, Boolean(item.favourited), "heart", "synd.compose.action.unfavourite", "synd.compose.action.favourite", mini));
      parts.push(toggleButton("data-item-boost", item, Boolean(item.boosted), "repeat", "synd.compose.action.unboost", "synd.compose.action.boost", mini));
    }
    return parts.join("");
  }

  // --- Dialog ---------------------------------------------------------------

  function updateCount() {
    const count = $("note-compose-count");
    if (count) count.textContent = tr("synd.compose.count", { count: $("note-compose-text").value.length });
  }

  // options: { mode, item, text }. A reply starts with the handle of the author of the parent, as a remote server shows a mention.
  function open(options) {
    const dialog = $("modal-note-compose");
    if (!AVAILABLE || !dialog) return false;
    const mode = (options && options.mode) || "new";
    const item = (options && options.item) || null;
    S.mode = mode;
    S.item = item;
    const note = mode === "edit" && item ? item.note : null;
    $("modal-note-compose-title").textContent = tr(`synd.compose.title.${mode}`);
    $("btn-note-compose").textContent = tr(mode === "edit" ? "synd.compose.submit_edit" : "synd.compose.submit");
    const context = $("note-compose-context");
    const author = item && item.author ? (item.author.name || item.author.handle || item.author.iri || "") : "";
    context.textContent = mode === "reply" ? tr("synd.compose.reply_to", { name: author }) : "";
    context.hidden = mode !== "reply";
    let text = (options && options.text) || "";
    if (note) text = note.content || "";
    else if (mode === "reply" && item && item.author && item.author.handle && !item.note) text = `${item.author.handle} `;
    $("note-compose-text").value = text;
    $("note-compose-summary").value = note ? note.summary || "" : "";
    $("note-compose-language").value = note ? note.language || "" : "";
    $("note-compose-msg").textContent = "";
    $("btn-note-compose").disabled = false;
    updateCount();
    dialog.showModal();
    const area = $("note-compose-text");
    area.focus();
    area.setSelectionRange(area.value.length, area.value.length);
    return true;
  }

  async function submit() {
    if (S.busy) return;
    const content = $("note-compose-text").value;
    const summary = $("note-compose-summary").value.trim();
    const language = $("note-compose-language").value.trim();
    const msg = $("note-compose-msg");
    const button = $("btn-note-compose");
    S.busy = true;
    button.disabled = true;
    msg.textContent = tr("synd.compose.sending");
    let result;
    if (S.mode === "edit" && S.item && S.item.note) {
      const body = { content, summary: summary || null };
      if (language) body.language = language;
      result = await api("PATCH", `/api/federation/notes/${encodeURIComponent(S.item.note.id)}`, body);
    } else {
      const body = { content };
      if (summary) body.summary = summary;
      if (language) body.language = language;
      if (S.mode === "reply" && S.item) body.in_reply_to = String(S.item.id);
      result = await api("POST", "/api/federation/notes", body);
    }
    S.busy = false;
    button.disabled = false;
    if (!result.ok) { msg.textContent = errorText(result); return; }
    $("modal-note-compose").close();
    hooks.ticker(tr(S.mode === "edit" ? "synd.compose.edited_done" : "synd.compose.sent"));
    hooks.refresh();
  }

  async function removeNote(item) {
    if (!item || !item.note || !window.confirm(tr("synd.compose.delete_confirm"))) return;
    const result = await api("DELETE", `/api/federation/notes/${encodeURIComponent(item.note.id)}`);
    if (!result.ok) { hooks.ticker(errorText(result)); return; }
    hooks.ticker(tr("synd.compose.deleted"));
    hooks.removed(item.id);
  }

  async function toggle(item, name, flag) {
    if (!item) return;
    const result = await api(item[flag] ? "DELETE" : "POST", `/api/federation/timeline/${encodeURIComponent(item.id)}/${name}`);
    if (!result.ok) { hooks.ticker(errorText(result)); return; }
    hooks.applyItem({ id: item.id, favourited: result.data.favourited, boosted: result.data.boosted });
    hooks.renderClient();
  }

  // A click in the client panel. Returns true when the click was a control of this file.
  function handleClick(e) {
    if (!AVAILABLE) return false;
    const pick = (name) => { const el = e.target.closest(`[${name}]`); return el ? el.getAttribute(name) : null; };
    const reply = pick("data-note-reply");
    if (reply) { open({ mode: "reply", item: hooks.findItem(reply) }); return true; }
    const edit = pick("data-note-edit");
    if (edit) { open({ mode: "edit", item: hooks.findItem(edit) }); return true; }
    const del = pick("data-note-delete");
    if (del) { removeNote(hooks.findItem(del)); return true; }
    const favourite = pick("data-item-favourite");
    if (favourite) { toggle(hooks.findItem(favourite), "favourite", "favourited"); return true; }
    const boost = pick("data-item-boost");
    if (boost) { toggle(hooks.findItem(boost), "boost", "boosted"); return true; }
    return false;
  }

  function init(given) {
    hooks = Object.assign(hooks, given || {});
    const button = $("btn-note-new");
    if (button) {
      button.hidden = !AVAILABLE;
      button.addEventListener("click", () => open({ mode: "new" }));
    }
    const form = $("form-note-compose");
    if (form) form.addEventListener("submit", (e) => { e.preventDefault(); submit(); });
    const area = $("note-compose-text");
    // A change of the text removes the message of the last attempt.
    if (area) area.addEventListener("input", () => { updateCount(); $("note-compose-msg").textContent = ""; });
  }

  window.ClaireSyndicationCompose = { available: AVAILABLE, init, actionsHtml, handleClick, open };
})();
