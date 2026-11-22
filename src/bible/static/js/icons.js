/* Line icons for all pages (docs/design/HOME_OVERVIEW_AND_CONTEXT_PANEL_DESIGN.md, HD4).
 * Each icon is an inline SVG on a 16 x 16 grid. It has no fill and its stroke is `currentColor`, thus it follows the text color of its parent.
 * Use `ClaireIcons.svg(name)` in a script, or an empty element with the `data-icon` attribute in markup. This module fills such elements, also those that a script adds later.
 */
(function () {
  "use strict";
  if (window.ClaireIcons) return;

  var c55 = '<circle cx="8" cy="8" r="5.5"/>';
  var graph = '<circle cx="4" cy="4" r="1.8"/><circle cx="12" cy="6" r="1.8"/><circle cx="7" cy="12" r="1.8"/><path d="M5.7 4.5 10.3 5.6M11 7.6 8.2 10.7M4.6 5.7 6.4 10.3"/>';
  var ICONS = {
    home: '<path d="M2.5 8 8 3l5.5 5M4 7v6h3v-3.5h2V13h3V7"/>',
    doc: '<path d="M4 2.5h5l3 3v8H4zM9 2.5v3h3M6 8.5h4M6 11h4"/>',
    graph: graph,
    screen: '<path d="M2.5 3.5h11v7h-11zM8 10.5v3M5.5 13.5h5"/>',
    list: '<path d="M3 4h10M3 8h10M3 12h6"/>',
    menu: '<path d="M2.5 4h11M2.5 8h11M2.5 12h11"/>',
    plus: '<path d="M8 3v10M3 8h10"/>',
    close: '<path d="m4 4 8 8M12 4l-8 8"/>',
    inbox: '<path d="m2.5 9.5 2-6h7l2 6v3h-11zM2.5 9.5h3.2l.8 1.5h3l.8-1.5h3.2"/>',
    settings: '<path d="M2.5 5h6M11.5 5h2M2.5 11h2M7.5 11h6"/><circle cx="10" cy="5" r="1.5"/><circle cx="6" cy="11" r="1.5"/>',
    broadcast: '<circle cx="8" cy="8" r="1.2"/><path d="M5.2 5.2a4 4 0 0 0 0 5.6M10.8 5.2a4 4 0 0 1 0 5.6M3.2 3.2a6.8 6.8 0 0 0 0 9.6M12.8 3.2a6.8 6.8 0 0 1 0 9.6"/>',
    user: '<circle cx="8" cy="5.5" r="2.5"/><path d="M3 13.5c.6-2.6 2.5-4 5-4s4.4 1.4 5 4"/>',
    users: '<circle cx="6" cy="5.5" r="2.2"/><path d="M2 13c.5-2.3 2-3.5 4-3.5s3.5 1.2 4 3.5M10.5 3.6a2.2 2.2 0 0 1 0 3.9M11.8 9.8c1.2.5 1.9 1.6 2.2 3.2"/>',
    globe: c55 + '<path d="M2.5 8h11M8 2.5c-2 1.8-2 9.2 0 11M8 2.5c2 1.8 2 9.2 0 11"/>',
    lock: '<path d="M4 7.5h8v6H4zM5.5 7.5v-2a2.5 2.5 0 0 1 5 0v2"/>',
    key: '<circle cx="5" cy="10.5" r="2.5"/><path d="M7 8.8l6-6M11 4.8l1.7 1.7M9.3 6.5l1.4 1.4"/>',
    folder: '<path d="M2.5 4.5h4L8 6h5.5v6.5h-11z"/>',
    database: '<path d="M3 4.5c0-1.1 2.2-2 5-2s5 .9 5 2v7c0 1.1-2.2 2-5 2s-5-.9-5-2zM3 4.5c0 1.1 2.2 2 5 2s5-.9 5-2M3 8c0 1.1 2.2 2 5 2s5-.9 5-2"/>',
    bot: '<path d="M3.5 6h9v6.5h-9zM8 6V3.5M6.2 9v.6M9.8 9v.6"/><circle cx="8" cy="3" r=".6"/>',
    package: '<path d="M8 2.5 13.5 5v6L8 13.5 2.5 11V5zM2.5 5 8 7.5 13.5 5M8 7.5v6"/>',
    save: '<path d="M3 3h8l2 2v8H3zM5.5 3v3.5h4V3M5.5 13V9.5h5V13"/>',
    book: '<path d="M8 4.5C6.5 3.5 4.5 3.3 2.5 3.5V12c2-.2 4 0 5.5 1 1.5-1 3.5-1.2 5.5-1V3.5c-2-.2-4 0-5.5 1zM8 4.5V13"/>',
    plug: '<path d="M6 2.5v3M10 2.5v3M4.5 5.5h7V8a3.5 3.5 0 0 1-7 0zM8 11.5v2"/>',
    tag: '<path d="M2.5 2.5h5l6 6-5 5-6-6z"/><circle cx="5.3" cy="5.3" r=".8"/>',
    bolt: '<path d="M9 2 4 9h3.5L7 14l5-7H8.5z"/>',
    repeat: '<path d="M3 7V6a1.5 1.5 0 0 1 1.5-1.5H12M10 2.5l2 2-2 2M13 9v1a1.5 1.5 0 0 1-1.5 1.5H4M6 13.5l-2-2 2-2"/>',
    puzzle: '<path d="M3 5.5h3.2a1.6 1.6 0 1 1 3.2 0h3.1v3.1a1.6 1.6 0 1 0 0 3.2v1.7H3z"/>',
    server: '<path d="M2.5 3h11v4h-11zM2.5 9h11v4h-11zM5 5h.01M5 11h.01"/>',
    id: '<path d="M2 3.5h12v9H2zM3.8 10.5c.3-1 1-1.4 1.7-1.4s1.4.4 1.7 1.4M9.5 6.5H12M9.5 9H12"/><circle cx="5.5" cy="7" r="1.3"/>',
    ruler: '<path d="M2 5.5h12v5H2zM5 5.5v2M8 5.5V8M11 5.5v2"/>',
    pulse: '<path d="M2 8h3l1.5-4 3 8L11 8h3"/>',
    restore: '<path d="M3 8a5 5 0 1 0 1.5-3.5M3 3v2.5h2.5M8 5.5V8l1.8 1.2"/>',
    chart: '<path d="M2.5 13.5h11M4.5 13.5v-4M8 13.5v-8M11.5 13.5v-6"/>',
    shield: '<path d="M8 2.5 13 4v4c0 2.8-2 4.6-5 5.5-3-.9-5-2.7-5-5.5V4z"/>',
    compass: c55 + '<path d="m10.3 5.7-1.4 3.2-3.2 1.4 1.4-3.2z"/>',
    mail: '<path d="M2.5 4h11v8h-11zM2.5 4.5 8 9l5.5-4.5"/>',
    trash: '<path d="M3 4.5h10M6.5 4.5v-2h3v2M4.5 4.5l.5 9h6l.5-9M6.8 7v4M9.2 7v4"/>',
    download: '<path d="M8 2.5V10M5 7l3 3 3-3M3 13h10"/>',
    upload: '<path d="M8 10V2.5M5 5.5l3-3 3 3M3 13h10"/>',
    play: '<path d="M5 3.5v9l7-4.5z"/>',
    wrench: '<path d="M10.5 2.5a3 3 0 0 0-3.7 3.9L2.5 10.7v2.8h2.8l4.3-4.3a3 3 0 0 0 3.9-3.7l-2 2-1.8-.3-.3-1.8z"/>',
    search: '<circle cx="7" cy="7" r="4"/><path d="m10 10 3.5 3.5"/>',
    link: '<path d="M6.5 9.5a2.5 2.5 0 0 0 3.5 0l2.5-2.5A2.5 2.5 0 0 0 9 3.5l-1 1M9.5 6.5a2.5 2.5 0 0 0-3.5 0L3.5 9A2.5 2.5 0 0 0 7 12.5l1-1"/>',
    clock: c55 + '<path d="M8 5v3l2 1.5"/>',
    "check-circle": c55 + '<path d="m5.5 8 1.8 1.8 3.2-3.6"/>',
    block: c55 + '<path d="m4.1 4.1 7.8 7.8"/>',
    circle: c55,
    alert: c55 + '<path d="M8 5v3.5M8 11h.01"/>',
    info: c55 + '<path d="M8 7.5V11M8 5h.01"/>',
    dot: '<circle cx="8" cy="8" r="3"/>',
    pin: '<path d="M8 14V9.5M5 9.5h6L10 6V3H6v3z"/>',
    quote: '<path d="M6.5 4.5c-2 .6-3 2-3 4v3h3.5V8H5c0-1 .5-1.7 1.5-2zM12.5 4.5c-2 .6-3 2-3 4v3H13V8h-2c0-1 .5-1.7 1.5-2z"/>',
    bell: '<path d="M4 11.5V7.5a4 4 0 0 1 8 0v4l1 1.5H3zM6.7 13.5a1.4 1.4 0 0 0 2.6 0"/>',
    megaphone: '<path d="M3 6.5v3h2l5 3v-9l-5 3zM12 6.5a2 2 0 0 1 0 3M5.5 9.5l1 3.5"/>',
    paperclip: '<path d="M11.5 7 7 11.5a2.1 2.1 0 0 1-3-3l5-5a1.4 1.4 0 0 1 2 2l-4.8 4.8"/>',
    comment: '<path d="M2.5 3.5h11V11H7l-3 2.5V11H2.5z"/>',
    warning: '<path d="M8 2.5 14 13H2zM8 6.5v3M8 11.2h.01"/>',
    moon: '<path d="M12.5 9.5a5.5 5.5 0 0 1-6-6 5.5 5.5 0 1 0 6 6z"/>',
    sun: '<circle cx="8" cy="8" r="2.8"/><path d="M8 1.5V3M8 13v1.5M1.5 8H3M13 8h1.5M3.4 3.4l1.1 1.1M11.5 11.5l1.1 1.1M3.4 12.6l1.1-1.1M11.5 4.5l1.1-1.1"/>',
    clipboard: '<path d="M4 3.5h8v10H4zM6 3.5v-1h4v1M6 7h4M6 9.5h4"/>',
    mic: '<path d="M6.5 3.5a1.5 1.5 0 0 1 3 0V8a1.5 1.5 0 0 1-3 0zM4 8a4 4 0 0 0 8 0M8 12v2"/>',
    camera: '<path d="M2.5 5H5l1-1.5h4L11 5h2.5v7.5h-11z"/><circle cx="8" cy="8.5" r="2.2"/>',
    target: c55 + '<circle cx="8" cy="8" r="2.5"/><path d="M8 8h.01"/>',
    image: '<path d="M2.5 3.5h11v9h-11zM2.5 10.5l3-3L8 10l2-2 3.5 3.5"/><circle cx="10.5" cy="6" r=".9"/>',
    scissors: '<circle cx="4.5" cy="4.5" r="1.7"/><circle cx="4.5" cy="11.5" r="1.7"/><path d="M6 5.5l7.5 6.5M6 10.5 13.5 4"/>',
    captions: '<path d="M2 4.5h12v7H2zM7 7a1.5 1.5 0 1 0 0 2M11.5 7a1.5 1.5 0 1 0 0 2"/>',
    terminal: '<path d="M2.5 3.5h11v9h-11zM5 6.5l2 2-2 2M8.5 10.5H11"/>',
    eye: '<path d="M1.5 8c1.6-2.8 3.8-4.2 6.5-4.2s4.9 1.4 6.5 4.2c-1.6 2.8-3.8 4.2-6.5 4.2S3.1 10.8 1.5 8z"/><circle cx="8" cy="8" r="2"/>',
    film: '<path d="M2.5 3.5h11v9h-11zM5.5 3.5v9M10.5 3.5v9M2.5 8h3M10.5 8h3"/>',
    star: '<path d="M8 2.2l1.8 3.7 4 .6-2.9 2.8.7 4L8 11.4l-3.6 1.9.7-4-2.9-2.8 4-.6z"/>',
    flame: '<path d="M8 2c.5 2.5 3.5 3.8 3.5 7a3.5 3.5 0 0 1-7 0c0-1.2.5-2.2 1.2-3 .2 1 .8 1.6 1.5 1.8C6.8 6 7 3.8 8 2z"/>',
    merge: '<path d="M4 3v3.5a3 3 0 0 0 3 3h5M10 7.5l2 2-2 2M4 6.5V13"/>',
    edit: '<path d="M3 13l.5-2.8L10.8 3 13 5.2l-7.2 7.3zM9.5 4.3l2.2 2.2"/>',
    heart: '<path d="M8 13.2 3.2 8.6a2.9 2.9 0 0 1 4.1-4.1l.7.7.7-.7a2.9 2.9 0 0 1 4.1 4.1z"/>',
    fork: '<circle cx="4.5" cy="3.5" r="1.5"/><circle cx="11.5" cy="3.5" r="1.5"/><circle cx="8" cy="12.5" r="1.5"/><path d="M4.5 5v1.5a2 2 0 0 0 2 2h3a2 2 0 0 0 2-2V5M8 8.5V11"/>',
    check: '<path d="M3.5 8.5 6.5 11.5 12.5 4.5"/>',
    chev: '<path d="M6 4l4 4-4 4"/>',
    more: '<circle cx="3.5" cy="8" r="1"/><circle cx="8" cy="8" r="1"/><circle cx="12.5" cy="8" r="1"/>'
  };

  function svg(name, cls) {
    var body = ICONS[name];
    if (!body) return "";
    return '<svg class="lineicon' + (cls ? " " + cls : "") + '" viewBox="0 0 16 16" aria-hidden="true" focusable="false">' + body + "</svg>";
  }

  function fill(el) {
    var name = el.getAttribute("data-icon");
    if (el.getAttribute("data-icon-set") === name) return;
    el.setAttribute("data-icon-set", name);
    el.innerHTML = svg(name);
  }

  function hydrate(root) {
    var scope = root || document;
    if (scope.nodeType === 1 && scope.hasAttribute("data-icon")) fill(scope);
    if (scope.querySelectorAll) Array.prototype.forEach.call(scope.querySelectorAll("[data-icon]"), fill);
  }

  window.ClaireIcons = { svg: svg, hydrate: hydrate, has: function (name) { return Object.prototype.hasOwnProperty.call(ICONS, name); } };

  function start() {
    hydrate(document);
    if (!window.MutationObserver || !document.body) return;
    new MutationObserver(function (records) {
      records.forEach(function (rec) {
        if (rec.type === "attributes") { fill(rec.target); return; }
        Array.prototype.forEach.call(rec.addedNodes, function (node) { if (node.nodeType === 1) hydrate(node); });
      });
    }).observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ["data-icon"] });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();
