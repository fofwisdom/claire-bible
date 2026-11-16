(function(){
  'use strict';

  function tr(key, fallback, vars){
    let text = fallback || key;
    if(window.ClaireI18n && typeof window.ClaireI18n.t === 'function'){
      const resolved = window.ClaireI18n.t(key, vars);
      if(resolved && resolved !== key) text = resolved;
    }
    if(vars && text === fallback){
      Object.keys(vars).forEach(function(k){
        text = text.replace(new RegExp('\\{' + k + '\\}', 'g'), String(vars[k]));
      });
    }
    return text;
  }

  function storageScope(){
    const meta = document.querySelector('meta[name="claire-storage-key"]');
    return meta ? (meta.getAttribute('content') || 'anon') : 'anon';
  }

  function initGroupAccordions(){
    const storageKey = 'claireConsoleCollapsedGroups:' + storageScope();
    let collapsed = {};
    try{
      const saved = sessionStorage.getItem(storageKey);
      if(saved){
        const parsed = JSON.parse(saved);
        if(parsed && typeof parsed === 'object' && !Array.isArray(parsed)) collapsed = parsed;
      }
    }catch(_){}

    function persist(){
      try{ sessionStorage.setItem(storageKey, JSON.stringify(collapsed)); }catch(_){}
    }

    document.querySelectorAll('.console-nav-group[data-nav-group]').forEach(function(group){
      const groupId = group.getAttribute('data-nav-group') || '';
      const btn = group.querySelector('[data-nav-group-toggle]');
      const listId = btn ? btn.getAttribute('aria-controls') : '';
      const list = listId ? document.getElementById(listId) : group.querySelector('.console-nav-list');
      if(!btn || !list) return;

      const hasCurrent = group.classList.contains('has-active') || Boolean(group.querySelector('a[aria-current="page"]'));
      function applyState(expanded){
        btn.setAttribute('aria-expanded', expanded ? 'true' : 'false');
        group.classList.toggle('is-collapsed', !expanded);
        list.hidden = !expanded;
      }

      if(hasCurrent){
        delete collapsed[groupId];
        applyState(true);
      } else if(collapsed[groupId] === true){
        applyState(false);
      } else {
        applyState(true);
      }

      btn.addEventListener('click', function(){
        const nextExpanded = btn.getAttribute('aria-expanded') !== 'true';
        if(nextExpanded) delete collapsed[groupId];
        else collapsed[groupId] = true;
        persist();
        applyState(nextExpanded);
      });
    });
  }

  function initMobileDrawer(){
    const drawer = document.getElementById('console-nav-drawer');
    const drawerBody = document.getElementById('console-nav-drawer-body');
    const sidebarHost = document.getElementById('console-sidebar');
    const sidebarInner = document.getElementById('console-sidebar-inner');
    const openBtn = document.getElementById('console-mob-menu');
    const closeBtn = document.getElementById('console-drawer-close');
    const backdrop = drawer ? drawer.querySelector('.console-drawer-backdrop') : null;
    const sheet = drawer ? drawer.querySelector('.console-drawer-sheet') : null;
    if(!drawer || !drawerBody || !sidebarHost || !sidebarInner) return;

    document.documentElement.classList.add('console-drawer-ready');

    const mql = window.matchMedia('(max-width: 860px)');
    let ignoreSwipeUntil = 0;

    function syncSidebarMount(){
      if(mql.matches){
        if(sidebarInner.parentElement !== drawerBody) drawerBody.appendChild(sidebarInner);
      } else {
        if(drawer.open) drawer.close();
        if(sidebarInner.parentElement !== sidebarHost) sidebarHost.appendChild(sidebarInner);
      }
    }

    function openDrawer(){
      if(!mql.matches || drawer.open) return;
      if(sidebarInner.parentElement !== drawerBody) drawerBody.appendChild(sidebarInner);
      if(openBtn) openBtn.setAttribute('aria-expanded', 'true');
      ignoreSwipeUntil = Date.now() + 250;
      drawer.showModal();
      drawer.scrollLeft = 0;
      drawer.style.setProperty('--backdrop-alpha', '1');
      if(closeBtn) closeBtn.focus();
    }

    function closeDrawer(){
      if(drawer.open) drawer.close();
    }

    drawer.addEventListener('close', function(){
      if(openBtn){
        openBtn.setAttribute('aria-expanded', 'false');
        if(mql.matches && document.activeElement && drawer.contains(document.activeElement)){
          openBtn.focus();
        }
      }
      drawer.style.removeProperty('--backdrop-alpha');
    });

    if(openBtn) openBtn.addEventListener('click', openDrawer);
    if(closeBtn) closeBtn.addEventListener('click', closeDrawer);
    if(backdrop) backdrop.addEventListener('click', closeDrawer);

    sidebarInner.addEventListener('click', function(ev){
      if(drawer.open && ev.target && ev.target.closest('a.console-nav-link')){
        closeDrawer();
      }
    });

    if(sheet && typeof IntersectionObserver === 'function'){
      const io = new IntersectionObserver(function(entries){
        if(!drawer.open || Date.now() < ignoreSwipeUntil) return;
        entries.forEach(function(entry){
          if(entry.intersectionRatio < 0.08) closeDrawer();
        });
      }, { root: drawer, threshold: [0, 0.08, 0.5] });
      io.observe(sheet);
    }

    const supportsScrollTimeline = typeof CSS !== 'undefined' && typeof CSS.supports === 'function' && CSS.supports('animation-timeline: scroll()');
    if(!supportsScrollTimeline && sheet){
      drawer.addEventListener('scroll', function(){
        if(!drawer.open) return;
        const w = sheet.offsetWidth || 280;
        const alpha = Math.max(0, Math.min(1, 1 - (drawer.scrollLeft / w)));
        drawer.style.setProperty('--backdrop-alpha', alpha.toFixed(3));
      }, { passive: true });
    }

    if(typeof mql.addEventListener === 'function') mql.addEventListener('change', syncSidebarMount);
    else if(typeof mql.addListener === 'function') mql.addListener(syncSidebarMount);
    syncSidebarMount();
  }

  function flashTarget(el){
    if(!el) return;
    el.classList.remove('console-flash-hit');
    void el.offsetWidth;
    el.classList.add('console-flash-hit');
    window.setTimeout(function(){ el.classList.remove('console-flash-hit'); }, 1500);
  }

  function buildPageCommands(){
    const cmds = [];
    const seen = new Set();
    const body = document.getElementById('console-section-body');
    if(!body) return cmds;
    const pageMeta = tr('console.js.shell.group_page', 'Current page');
    body.querySelectorAll('h2, h3').forEach(function(heading, idx){
      const label = (heading.textContent || '').trim();
      if(!label || seen.has(label)) return;
      const hiddenParent = heading.closest('[hidden]');
      if(hiddenParent) return;
      seen.add(label);
      const anchor = heading.closest('section, form, .card, .console-subcard, .pref-card') || heading;
      if(!anchor.id){
        if(!heading.id) heading.id = 'console-heading-' + (idx + 1);
      }
      const targetEl = anchor.id ? anchor : heading;
      cmds.push({
        iconName: 'settings',
        label: label,
        meta: pageMeta,
        keywords: label,
        run: function(){
          targetEl.scrollIntoView({ behavior: 'smooth', block: 'start' });
          flashTarget(targetEl);
        }
      });
    });
    return cmds;
  }

  function initCommandPalette(){
    if(!window.ClaireCommandPalette || typeof window.ClaireCommandPalette.init !== 'function') return;

    function getNavCommands(){
      const cmds = [];
      document.querySelectorAll('#console-sidebar-inner a.console-nav-link[data-nav-section]').forEach(function(link){
        const groupEl = link.closest('.console-nav-group');
        const groupLabelEl = groupEl ? groupEl.querySelector('.console-nav-group-label') : null;
        const groupLabel = groupLabelEl ? (groupLabelEl.textContent || '').trim() : tr('console.js.shell.group_sections', 'Sections');
        const label = (link.textContent || '').trim();
        const href = link.getAttribute('href') || '';
        const slug = link.getAttribute('data-nav-section') || '';
        const iconSpan = link.querySelector('[data-icon]');
        const iconName = (iconSpan && iconSpan.getAttribute('data-icon')) || 'settings';
        if(!label || !href) return;
        cmds.push({
          iconName: iconName,
          label: label,
          meta: groupLabel,
          keywords: slug + ' ' + href + ' ' + groupLabel,
          run: function(){ window.location.assign(href); }
        });
      });
      return cmds;
    }

    window.ClaireCommandPalette.init({
      dialogId: 'cmdpalette',
      getCommands: function(){ return getNavCommands().concat(buildPageCommands()); },
      getEntities: function(){ return []; }
    });

    const dialog = document.getElementById('cmdpalette');
    const input = document.getElementById('cmdpalette-input');
    if(dialog && input){
      input.addEventListener('keydown', function(ev){
        if(ev.key === 'Escape' && dialog.open){
          ev.preventDefault();
          dialog.close();
        }
      });
    }
  }

  function isHintElement(el){
    if(!el) return false;
    return el.matches('p.hint, p.pref-desc, p.console-desc, .console-hint');
  }

  function hasInteractiveControls(root){
    return Boolean(root.querySelector('input, button, select, textarea, form, [role="status"], .console-kv, .pref-row, .console-actions, pre, code, ul, ol'));
  }

  function enhanceSplitCards(body){
    const candidates = body.querySelectorAll('.card, .console-subcard, .pref-card');
    candidates.forEach(function(card){
      if(card.dataset.splitEnhanced === '1' || card.classList.contains('is-split') || card.classList.contains('no-split')) return;
      card.dataset.splitEnhanced = '1';

      if(card.querySelector('table, .pref-table-wrap, .pref-stat-row, .pref-panel, [data-admin-panel-host], [data-settings-panel-host]')) return;
      if(card.querySelector('.card, .console-subcard, .pref-card')) return;
      if(!hasInteractiveControls(card)) return;

      const children = Array.from(card.children);
      if(children.length < 2) return;

      const first = children[0];
      let heading = null;
      if(first.matches('h2, h3')){
        heading = first;
      } else if(first.matches('.console-card-head') && first.querySelector('h2, h3')){
        heading = first;
      }
      if(!heading) return;

      const metaChildren = [heading];
      let idx = 1;
      while(idx < children.length && isHintElement(children[idx])){
        metaChildren.push(children[idx]);
        idx++;
      }
      const ctrlChildren = children.slice(idx);
      if(!ctrlChildren.length) return;

      const meta = document.createElement('div');
      meta.className = 'console-split-meta';
      const ctrl = document.createElement('div');
      ctrl.className = 'console-split-ctrl';
      metaChildren.forEach(function(el){ meta.appendChild(el); });
      ctrlChildren.forEach(function(el){ ctrl.appendChild(el); });

      card.appendChild(meta);
      card.appendChild(ctrl);
      card.classList.add('is-split');
    });
  }

  function enhanceTables(body){
    body.querySelectorAll('table').forEach(function(table){
      const headers = Array.from(table.querySelectorAll('thead th')).map(function(th){
        return (th.textContent || '').trim();
      });
      if(headers.length){
        table.querySelectorAll('tbody tr').forEach(function(trEl){
          Array.from(trEl.children).forEach(function(cell, colIdx){
            if(cell.tagName !== 'TD') return;
            if(!cell.hasAttribute('data-label') && headers[colIdx]){
              cell.setAttribute('data-label', headers[colIdx]);
            }
          });
        });
      }
      const wrap = table.closest('.pref-table-wrap');
      if(wrap && !wrap.hasAttribute('tabindex')){
        wrap.setAttribute('tabindex', '0');
        if(!wrap.hasAttribute('role')) wrap.setAttribute('role', 'region');
      }
    });
  }

  let tocObserver = null;

  function rebuildToc(body){
    const layout = document.getElementById('console-layout');
    const tocList = document.getElementById('console-toc-list');
    if(!layout || !tocList) return;

    if(tocObserver){
      tocObserver.disconnect();
      tocObserver = null;
    }

    const items = [];
    const seen = new Set();
    body.querySelectorAll('h2').forEach(function(heading, idx){
      if(heading.closest('[hidden]')) return;
      const text = (heading.textContent || '').trim();
      if(!text || seen.has(text)) return;
      seen.add(text);
      const card = heading.closest('section, form, .card, .console-subcard, .pref-card') || heading;
      if(!card.id && !heading.id) heading.id = 'console-sec-' + (idx + 1);
      const target = card.id ? card : heading;
      items.push({ id: target.id, label: text, target: target });
    });

    tocList.innerHTML = '';
    if(items.length < 3){
      layout.classList.remove('has-toc');
      return;
    }

    layout.classList.add('has-toc');
    const linkMap = new Map();
    items.forEach(function(item, idx){
      const li = document.createElement('li');
      const a = document.createElement('a');
      a.href = '#' + item.id;
      a.textContent = item.label;
      if(idx === 0) a.classList.add('is-active');
      a.addEventListener('click', function(){
        flashTarget(item.target);
      });
      li.appendChild(a);
      tocList.appendChild(li);
      linkMap.set(item.target, a);
    });

    if(typeof IntersectionObserver === 'function'){
      tocObserver = new IntersectionObserver(function(entries){
        const visible = entries.filter(function(e){ return e.isIntersecting; });
        if(!visible.length) return;
        visible.sort(function(a, b){ return a.boundingClientRect.top - b.boundingClientRect.top; });
        const activeTarget = visible[0].target;
        linkMap.forEach(function(a, target){
          a.classList.toggle('is-active', target === activeTarget);
        });
      }, { rootMargin: '-76px 0px -65% 0px', threshold: 0.05 });

      items.forEach(function(item){ tocObserver.observe(item.target); });
    }
  }

  function initSectionEnhancer(){
    const body = document.getElementById('console-section-body');
    if(!body) return;

    let applying = false;
    let rafId = 0;

    function run(){
      rafId = 0;
      applying = true;
      try{
        enhanceSplitCards(body);
        enhanceTables(body);
        rebuildToc(body);
      } finally {
        applying = false;
      }
    }

    run();

    if(typeof MutationObserver === 'function'){
      const mo = new MutationObserver(function(){
        if(applying) return;
        if(!rafId) rafId = window.requestAnimationFrame(run);
      });
      mo.observe(body, { childList: true, subtree: true });
    }
  }

  function initReauthEscape(){
    const modal = document.getElementById('console-reauth-modal');
    const cancelBtn = document.getElementById('console-reauth-cancel');
    if(!modal) return;
    document.addEventListener('keydown', function(ev){
      if(ev.key === 'Escape' && !modal.hidden){
        if(cancelBtn) cancelBtn.click();
        else modal.hidden = true;
      }
    });
  }

  function syncTopbarHeight(){
    const bar = document.querySelector('header.appbar');
    if(!bar) return;
    const h = Math.round(bar.getBoundingClientRect().height);
    if(h > 0) document.documentElement.style.setProperty('--console-topbar-h', h + 'px');
  }

  function boot(){
    syncTopbarHeight();
    window.addEventListener('resize', syncTopbarHeight, { passive: true });
    initGroupAccordions();
    initMobileDrawer();
    initCommandPalette();
    initSectionEnhancer();
    initReauthEscape();
  }

  if(document.readyState === 'loading'){
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
