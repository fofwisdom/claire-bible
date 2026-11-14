// Shared command palette module for Claire Bible workspace and syndication (HW-7, FP4-5).
(function(global){
  'use strict';

  const tr = (key, params) => (global.ClaireI18n ? global.ClaireI18n.t(key, params) : key);
  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

  let config = null;
  let dialogEl = null;
  let inputEl = null;
  let listEl = null;
  let items = [];
  let activeIndex = -1;
  let fetchSeq = 0;
  let fetchTimer = null;
  let globalBound = false;

  function getElements(){
    const id = (config && config.dialogId) || 'cmdpalette';
    dialogEl = document.getElementById(id);
    if(!dialogEl) return false;
    inputEl = dialogEl.querySelector('#cmdpalette-input') || dialogEl.querySelector('input[type="search"]');
    listEl = dialogEl.querySelector('#cmdpalette-list') || dialogEl.querySelector('[role="listbox"]');
    return Boolean(inputEl && listEl);
  }

  function isOpen(){
    if(!dialogEl) getElements();
    return Boolean(dialogEl && dialogEl.open);
  }

  function close(){
    if(fetchTimer){ clearTimeout(fetchTimer); fetchTimer = null; }
    fetchSeq++;
    if(dialogEl && dialogEl.open) dialogEl.close();
  }

  function setActive(index){
    const rows = listEl ? listEl.querySelectorAll('.cmd-palette-row') : [];
    if(!rows.length){
      activeIndex = -1;
      if(inputEl) inputEl.removeAttribute('aria-activedescendant');
      return;
    }
    const clamped = ((index % rows.length) + rows.length) % rows.length;
    activeIndex = clamped;
    rows.forEach((row, i) => {
      const sel = i === clamped;
      row.classList.toggle('is-active', sel);
      row.setAttribute('aria-selected', sel ? 'true' : 'false');
      if(sel){
        if(inputEl && row.id) inputEl.setAttribute('aria-activedescendant', row.id);
        if(listEl){
          const top = row.offsetTop;
          const bottom = top + row.offsetHeight;
          if(top < listEl.scrollTop) listEl.scrollTop = top;
          else if(bottom > listEl.scrollTop + listEl.clientHeight) listEl.scrollTop = bottom - listEl.clientHeight;
        }
      }
    });
  }

  function runItem(index){
    const item = items[index];
    if(!item || typeof item.run !== 'function') return;
    close();
    item.run();
  }

  function renderGroups(groups){
    if(!listEl) return;
    items = [];
    let html = '';
    let rowIdx = 0;
    groups.forEach(group => {
      if(!group.rows || !group.rows.length) return;
      html += '<div class="cmd-palette-group" role="presentation">';
      html += '<div class="cmd-palette-group-title">' + esc(group.title) + '</div>';
      group.rows.forEach(row => {
        const id = 'cmdpalette-opt-' + rowIdx;
        items.push(row);
        html += '<button type="button" id="' + id + '" class="cmd-palette-row" role="option" aria-selected="false" data-cmd-idx="' + rowIdx + '">';
        if(row.icon) html += '<span class="cmd-palette-icon" aria-hidden="true">' + esc(row.icon) + '</span>';
        html += '<span class="cmd-palette-label">' + esc(row.label) + '</span>';
        if(row.meta) html += '<span class="cmd-palette-meta">' + esc(row.meta) + '</span>';
        html += '</button>';
        rowIdx++;
      });
      html += '</div>';
    });
    if(!items.length){
      html = '<div class="cmd-palette-empty">' + esc(tr('nav.palette.empty')) + '</div>';
    }
    listEl.innerHTML = html;
    setActive(items.length ? 0 : -1);
  }

  function filterCommands(query){
    const raw = (config && typeof config.getCommands === 'function') ? config.getCommands() : [];
    const q = query.trim().toLowerCase();
    return raw.filter(cmd => {
      if(!q) return true;
      const hay = [cmd.label, cmd.meta, cmd.keywords].filter(Boolean).join(' ').toLowerCase();
      return hay.includes(q);
    }).map(cmd => ({
      icon: cmd.icon || '›',
      label: cmd.label,
      meta: cmd.meta || '',
      run: cmd.run
    }));
  }

  function filterEntities(query){
    const raw = (config && typeof config.getEntities === 'function') ? config.getEntities() : [];
    const q = query.trim().toLowerCase();
    const matched = [];
    for(let i = 0; i < raw.length; i++){
      const n = raw[i];
      if(!n || n.isHub) continue;
      if(q){
        const aliases = Array.isArray(n.aliases) ? n.aliases.join(' ') : '';
        const hay = [n.label, n.id, n.group, aliases].filter(Boolean).join(' ').toLowerCase();
        if(!hay.includes(q)) continue;
      }
      matched.push(n);
      if(matched.length >= 10) break;
    }
    return matched.map(n => ({
      icon: '●',
      label: n.label || n.id,
      meta: n.group || '',
      run: () => { if(config && typeof config.onSelectEntity === 'function') config.onSelectEntity(n); }
    }));
  }

  function mapDocRows(docs){
    return (docs || []).slice(0, 10).map(d => ({
      icon: '📄',
      label: d.title || tr('home.recent.untitled'),
      meta: d.source_type || '',
      run: () => { if(config && typeof config.onSelectDocument === 'function') config.onSelectDocument(d); }
    }));
  }

  function refreshResults(){
    if(!inputEl) return;
    const query = inputEl.value || '';
    const cmdRows = filterCommands(query);
    const entRows = filterEntities(query);
    const cachedDocs = (config && typeof config.getCachedDocuments === 'function') ? config.getCachedDocuments(query) : [];
    renderGroups([
      {title: tr('nav.palette.group_commands'), rows: cmdRows},
      {title: tr('nav.palette.group_documents'), rows: mapDocRows(cachedDocs)},
      {title: tr('nav.palette.group_entities'), rows: entRows}
    ]);

    if(config && typeof config.fetchDocuments === 'function'){
      if(fetchTimer) clearTimeout(fetchTimer);
      const seq = ++fetchSeq;
      const runFetch = () => {
        const listFilter = (!query.trim() && typeof config.getListFilter === 'function') ? (config.getListFilter() || '') : '';
        const effectiveQuery = query.trim() || listFilter;
        Promise.resolve(config.fetchDocuments(effectiveQuery)).then(docs => {
          if(seq !== fetchSeq || !isOpen()) return;
          renderGroups([
            {title: tr('nav.palette.group_commands'), rows: filterCommands(inputEl.value || '')},
            {title: tr('nav.palette.group_documents'), rows: mapDocRows(docs)},
            {title: tr('nav.palette.group_entities'), rows: filterEntities(inputEl.value || '')}
          ]);
        }).catch(() => {});
      };
      if(query.trim()){
        fetchTimer = setTimeout(runFetch, 120);
      } else {
        runFetch();
      }
    }
  }

  function open(){
    if(!getElements()) return;
    if(dialogEl.open) return;
    inputEl.value = '';
    dialogEl.showModal();
    inputEl.focus();
    refreshResults();
  }

  function bindDialogEvents(){
    if(!getElements() || dialogEl.dataset.paletteBound === '1') return;
    dialogEl.dataset.paletteBound = '1';

    inputEl.addEventListener('input', () => refreshResults());
    inputEl.addEventListener('keydown', e => {
      if(e.key === 'ArrowDown'){
        e.preventDefault();
        setActive(activeIndex + 1);
      } else if(e.key === 'ArrowUp'){
        e.preventDefault();
        setActive(activeIndex - 1);
      } else if(e.key === 'Enter' && activeIndex >= 0){
        e.preventDefault();
        runItem(activeIndex);
      }
    });

    listEl.addEventListener('click', e => {
      const btn = e.target.closest('.cmd-palette-row');
      if(!btn) return;
      const idx = Number(btn.dataset.cmdIdx);
      if(Number.isFinite(idx)) runItem(idx);
    });

    // Fallback for browsers without native closedby="any" support.
    dialogEl.addEventListener('click', e => {
      if(e.target !== dialogEl) return;
      const r = dialogEl.getBoundingClientRect();
      const inside = e.clientX >= r.left && e.clientX <= r.right && e.clientY >= r.top && e.clientY <= r.bottom;
      if(!inside) close();
    });
  }

  function init(options){
    config = options || {};
    bindDialogEvents();
    if(!globalBound){
      globalBound = true;
      document.addEventListener('keydown', e => {
        if((e.ctrlKey || e.metaKey) && !e.shiftKey && !e.altKey && String(e.key || '').toLowerCase() === 'k'){
          e.preventDefault();
          if(isOpen()) close(); else open();
        }
      });
    }
  }

  global.ClaireCommandPalette = {
    init,
    open,
    close,
    isOpen
  };
})(window);
