/**
 * ForgeLM site — user guide router & renderer.
 *
 * Reads the navigation tree from window.ForgeLMUserManualsIndex and the
 * per-language page content from window.ForgeLMUserManuals[<lang>], then
 * drives a single guide.html shell:
 *
 *   - sidebar (sections + pages)
 *   - hash-based routing (#/<section>/<page>)
 *   - on-this-page TOC (h2/h3 anchors with scrollspy)
 *   - reading progress bar
 *   - Cmd/Ctrl+K search overlay
 *   - prev / next pager
 *   - mermaid diagram initialisation
 *   - lazy loading of additional language data files
 *
 * Reacts to language changes by listening for the `lang` attribute on
 * <html> (set by js/i18n.js).
 */
(function () {
  'use strict';

  var GUIDE_BASE = 'guide.html';
  var GH_EDIT_BASE = 'https://github.com/HodeTech/ForgeLM/edit/main/docs/usermanuals';
  var DEFAULT_ROUTE = 'getting-started/introduction';

  var state = {
    lang: 'en',
    route: null,
    loadedLangs: { en: true },   // populated as we lazy-load others
    searchOpen: false,
  };

  // Render an HTML string into a live element without assigning to its
  // .innerHTML. The fragment is parsed via DOMParser — which produces
  // an inert Document where <script> tags do not execute — and the
  // parsed body's children are then moved into the target. Mirrors the
  // setHtml helper in js/i18n.js so guide-side rendering follows the
  // same defence-in-depth approach.
  function setHtml(el, html) {
    var doc = new DOMParser().parseFromString(html, 'text/html');
    while (el.firstChild) el.removeChild(el.firstChild);
    while (doc.body.firstChild) el.appendChild(doc.body.firstChild);
  }

  document.addEventListener('DOMContentLoaded', function () {
    if (!document.querySelector('.guide-layout')) return;

    state.lang = document.documentElement.lang || 'en';

    buildSidebar();
    initSearch();
    initProgressBar();
    initMobileDrawer();
    initLanguageWatcher();
    initMermaid();

    var initial = parseHash(window.location.hash) || DEFAULT_ROUTE;
    navigate(initial, /*replace*/ true);

    window.addEventListener('hashchange', function () {
      var route = parseHash(window.location.hash);
      if (route) navigate(route, /*replace*/ true);
    });
  });

  /* ──────────────────────────────────────────── routing */

  function parseHash(hash) {
    if (!hash) return null;
    var stripped = hash.replace(/^#\/?/, '');
    if (!stripped) return null;
    if (stripped.indexOf('/') === -1) return null;
    return stripped;
  }

  function navigate(route, replace) {
    state.route = route;
    var newHash = '#/' + route;
    if (window.location.hash !== newHash) {
      if (replace) {
        history.replaceState(null, '', newHash);
      } else {
        history.pushState(null, '', newHash);
      }
    }
    renderCurrentRoute();
    closeSearch();
    closeSidebar();
    window.scrollTo(0, 0);
  }

  /* ──────────────────────────────────────────── sidebar */

  function buildSidebar() {
    var nav = document.getElementById('guide-nav');
    if (!nav) return;
    var idx = window.ForgeLMUserManualsIndex;
    if (!idx) return;

    var lang = state.lang;
    while (nav.firstChild) nav.removeChild(nav.firstChild);

    idx.sections.forEach(function (section) {
      var wrap = document.createElement('div');
      wrap.className = 'guide-nav-section';
      wrap.dataset.sectionId = section.id;

      var h4 = document.createElement('h4');
      h4.textContent = section.titles[lang] || section.titles.en;
      wrap.appendChild(h4);

      var ul = document.createElement('ul');
      section.pages.forEach(function (page) {
        var li = document.createElement('li');
        var a = document.createElement('a');
        a.href = '#/' + section.id + '/' + page.id;
        a.dataset.route = section.id + '/' + page.id;
        a.textContent = page.titles[lang] || page.titles.en;
        a.addEventListener('click', function (e) {
          e.preventDefault();
          navigate(a.dataset.route, false);
        });
        li.appendChild(a);
        ul.appendChild(li);
      });
      wrap.appendChild(ul);
      nav.appendChild(wrap);
    });

    refreshSidebarActive();
    refreshSidebarFallbackMarks();
  }

  function refreshSidebarActive() {
    document.querySelectorAll('#guide-nav a').forEach(function (a) {
      a.classList.toggle('active', a.dataset.route === state.route);
    });
  }

  function refreshSidebarFallbackMarks() {
    // When the user-selected language has no bag at all (deferred
    // language per ``tools/build_usermanuals.py``), every page in the
    // sidebar is a fallback — the entire manual surface falls through
    // to English.  Per-page fallback flags on the bag's pages still
    // win for the supported languages (in case a single page is
    // missing from an otherwise-populated bag).
    var manuals = window.ForgeLMUserManuals || {};
    var nativeBag = manuals[state.lang];
    var langHasNoBag = !nativeBag && state.lang !== 'en';
    var bag = nativeBag || {};
    document.querySelectorAll('#guide-nav a').forEach(function (a) {
      var p = bag[a.dataset.route];
      var isFallback = langHasNoBag || !!(p && p.fallback);
      a.classList.toggle('fallback', isFallback);
    });
  }

  /* ──────────────────────────────────────────── render */

  function renderCurrentRoute() {
    var route = state.route;
    if (!route) return;

    ensureLangLoaded(state.lang).then(function () {
      var lang = state.lang;
      var manuals = window.ForgeLMUserManuals || {};
      var nativeBag = manuals[lang];
      var bag = nativeBag || manuals.en || {};
      var page = bag[route];

      if (!page) {
        renderNotFound(route);
        return;
      }

      // Mark the page as a translation-fallback when either the entire
      // language has no bag (deferred language — ``de`` / ``fr`` /
      // ``es`` / ``zh``) or the specific page is missing from the
      // language's bag and we used English as a fallback.  ``page`` is
      // shared across calls (cached in the global bag), so clone before
      // mutating to avoid cross-route leakage.
      var pageMissingInNative = !!nativeBag && !nativeBag[route];
      var langHasNoBag = !nativeBag && lang !== 'en';
      var isFallback = langHasNoBag || pageMissingInNative;
      if (isFallback) {
        page = Object.assign({}, page, { fallback: true });
      }

      renderPage(route, page);
    }).catch(function (err) {
      console.error('Failed to load language', state.lang, err);
      renderNotFound(route);
    });
  }

  function renderPage(route, page) {
    var parts = route.split('/');
    var sectionId = parts[0];
    var pageId = parts[1];
    var idx = window.ForgeLMUserManualsIndex || { sections: [] };
    var section = idx.sections.find(function (s) { return s.id === sectionId; });

    var content = document.getElementById('guide-content');
    if (!content) return;

    // Update document title.
    document.title = page.title + ' — ForgeLM';

    // Build breadcrumb.
    var crumbHtml =
      '<a href="index.html"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg></a>'
      + '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"/></svg>'
      + '<a href="' + GUIDE_BASE + '" data-i18n="guide.title">Guide</a>'
      + '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"/></svg>';
    if (section) {
      crumbHtml += '<span>' + escapeHtml(section.titles[state.lang] || section.titles.en) + '</span>'
        + '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"/></svg>';
    }
    crumbHtml += '<span class="current">' + escapeHtml(page.title) + '</span>';

    var fallbackBanner = '';
    if (page.fallback || page.missing) {
      fallbackBanner =
        '<div class="guide-fallback-banner">'
        + '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0zM12 9v4M12 17h.01"/></svg>'
        + '<span data-i18n="guide.fallback.banner">This page has not been translated yet — showing English.</span>'
        + '</div>';
    }

    var pager = buildPagerHtml(sectionId, pageId);
    var editUrl = GH_EDIT_BASE + '/' + state.lang + '/' + route + '.md';
    var editLink =
      '<a class="guide-edit" href="' + editUrl + '" target="_blank" rel="noopener">'
      + '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4z"/></svg>'
      + '<span data-i18n="guide.edit">Edit this page on GitHub</span>'
      + '</a>';

    var descriptionHtml = page.description
      ? '<p class="guide-page-description">' + escapeHtml(page.description) + '</p>'
      : '';

    setHtml(content,
      '<nav class="guide-breadcrumb">' + crumbHtml + '</nav>'
      + fallbackBanner
      + '<h1>' + escapeHtml(page.title) + '</h1>'
      + descriptionHtml
      + '<div class="guide-page-body">' + page.html + '</div>'
      + pager
      + editLink);

    // Re-translate any chrome that data-i18n updated nodes inside the content.
    if (window.ForgeLMi18n && window.ForgeLMi18n.setLanguage) {
      window.ForgeLMi18n.setLanguage(state.lang);
    }

    refreshSidebarActive();
    refreshSidebarFallbackMarks();
    buildOnPageToc(page.headings || []);
    updateMobileCrumb(page.title);

    if (window.mermaid && typeof window.mermaid.run === 'function') {
      try {
        window.mermaid.run({ querySelector: '.guide-content .mermaid' });
      } catch (e) { /* ignore */ }
    }

    // After DOM updates, trigger scrollspy refresh.
    setTimeout(observeHeadings, 50);
    updateProgressBar();
  }

  function renderNotFound(route) {
    var content = document.getElementById('guide-content');
    if (!content) return;
    setHtml(content,
      '<h1>Page not found</h1>'
      + '<p>The page <code>' + escapeHtml(route) + '</code> doesn\'t exist in this guide.</p>'
      + '<p><a href="#/' + DEFAULT_ROUTE + '">Return to the introduction</a></p>');
  }

  function buildPagerHtml(sectionId, pageId) {
    var idx = window.ForgeLMUserManualsIndex || { sections: [] };
    var flat = [];
    idx.sections.forEach(function (s) {
      s.pages.forEach(function (p) {
        flat.push({ section: s, page: p });
      });
    });
    var here = flat.findIndex(function (e) {
      return e.section.id === sectionId && e.page.id === pageId;
    });
    if (here === -1) return '';

    var prev = here > 0 ? flat[here - 1] : null;
    var next = here < flat.length - 1 ? flat[here + 1] : null;

    function card(item, dir) {
      if (!item) {
        return '<span class="guide-pager-card placeholder"></span>';
      }
      var href = '#/' + item.section.id + '/' + item.page.id;
      var labelKey = dir === 'prev' ? 'guide.prev' : 'guide.next';
      var labelDefault = dir === 'prev' ? 'Previous' : 'Next';
      var title = escapeHtml(item.page.titles[state.lang] || item.page.titles.en);
      return '<a class="guide-pager-card ' + dir + '" href="' + href + '">'
        + '<span class="label" data-i18n="' + labelKey + '">' + labelDefault + '</span>'
        + '<span class="title">' + title + '</span>'
        + '</a>';
    }
    return '<nav class="guide-pager" aria-label="page-navigation">' + card(prev, 'prev') + card(next, 'next') + '</nav>';
  }

  /* ──────────────────────────────────────────── on-page TOC */

  var observerInst = null;

  function buildOnPageToc(headings) {
    var toc = document.getElementById('guide-toc');
    if (!toc) return;
    if (!headings || headings.length === 0) {
      while (toc.firstChild) toc.removeChild(toc.firstChild);
      toc.style.display = 'none';
      return;
    }
    toc.style.display = '';
    var inner =
      '<div class="guide-toc-title" data-i18n="guide.toc.title">On this page</div>'
      + '<ul>';
    headings.forEach(function (h) {
      inner += '<li class="h' + h.level + '"><a href="#' + h.id + '" data-id="' + h.id + '">'
        + escapeHtml(h.text) + '</a></li>';
    });
    inner += '</ul>';
    setHtml(toc, inner);

    toc.querySelectorAll('a').forEach(function (a) {
      a.addEventListener('click', function (e) {
        e.preventDefault();
        var target = document.getElementById(a.dataset.id);
        if (target) {
          target.scrollIntoView({ behavior: 'smooth', block: 'start' });
          history.replaceState(null, '', '#/' + state.route + '#' + a.dataset.id);
        }
      });
    });
  }

  function observeHeadings() {
    if (observerInst) observerInst.disconnect();
    var headings = document.querySelectorAll('.guide-content h2, .guide-content h3');
    if (!headings.length) return;
    var tocLinks = {};
    document.querySelectorAll('#guide-toc a').forEach(function (a) {
      tocLinks[a.dataset.id] = a;
    });
    observerInst = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (!entry.target.id) return;
        var link = tocLinks[entry.target.id];
        if (!link) return;
        if (entry.isIntersecting) {
          // Mark this link active; clear others in same TOC.
          Object.values(tocLinks).forEach(function (l) { l.classList.remove('active'); });
          link.classList.add('active');
        }
      });
    }, { rootMargin: '-80px 0px -65% 0px', threshold: 0 });
    headings.forEach(function (h) { observerInst.observe(h); });
  }

  /* ──────────────────────────────────────────── reading progress */

  function initProgressBar() {
    window.addEventListener('scroll', updateProgressBar, { passive: true });
    window.addEventListener('resize', updateProgressBar);
  }

  function updateProgressBar() {
    var bar = document.getElementById('guide-progress');
    if (!bar) return;
    var scroll = window.pageYOffset || document.documentElement.scrollTop;
    var max = (document.documentElement.scrollHeight || document.body.scrollHeight) - window.innerHeight;
    var pct = max > 0 ? (scroll / max) * 100 : 0;
    bar.value = pct;
  }

  /* ──────────────────────────────────────────── search */

  function initSearch() {
    var btn = document.getElementById('guide-search-trigger');
    var overlay = document.getElementById('guide-search-overlay');
    var input = document.getElementById('guide-search-input');
    var results = document.getElementById('guide-search-results');
    if (!overlay || !input || !results) return;

    if (btn) btn.addEventListener('click', openSearch);

    document.addEventListener('keydown', function (e) {
      var modifier = e.metaKey || e.ctrlKey;
      if (modifier && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        openSearch();
        return;
      }
      if (e.key === 'Escape' && state.searchOpen) {
        e.preventDefault();
        closeSearch();
      }
      if (state.searchOpen && (e.key === 'ArrowDown' || e.key === 'ArrowUp' || e.key === 'Enter')) {
        e.preventDefault();
        handleSearchNav(e.key);
      }
    });

    overlay.addEventListener('click', function (e) {
      if (e.target === overlay) closeSearch();
    });

    // <dialog> closes natively on ESC and fires 'close' — keep state in sync
    // so the global keydown handler doesn't think the search is still open.
    overlay.addEventListener('close', function () { state.searchOpen = false; });
    overlay.addEventListener('cancel', function () { state.searchOpen = false; });

    input.addEventListener('input', function () { runSearch(input.value); });
  }

  function openSearch() {
    var overlay = document.getElementById('guide-search-overlay');
    var input = document.getElementById('guide-search-input');
    if (!overlay || !input) return;
    state.searchOpen = true;
    if (typeof overlay.showModal === 'function' && !overlay.open) {
      overlay.showModal();
    } else {
      overlay.setAttribute('open', '');
    }
    input.value = '';
    runSearch('');
    setTimeout(function () { input.focus(); }, 30);
  }

  function closeSearch() {
    state.searchOpen = false;
    var overlay = document.getElementById('guide-search-overlay');
    if (!overlay) return;
    if (typeof overlay.close === 'function' && overlay.open) {
      overlay.close();
    } else {
      overlay.removeAttribute('open');
    }
  }

  function runSearch(query) {
    var results = document.getElementById('guide-search-results');
    if (!results) return;
    var bag = (window.ForgeLMUserManuals || {})[state.lang] || (window.ForgeLMUserManuals || {}).en || {};
    var idx = window.ForgeLMUserManualsIndex || { sections: [] };
    var q = query.trim().toLocaleLowerCase(state.lang);

    // Build a flat list of {route, sectionTitle, pageTitle, snippet, score}.
    var hits = [];
    idx.sections.forEach(function (section) {
      var sectionTitle = section.titles[state.lang] || section.titles.en;
      section.pages.forEach(function (page) {
        var route = section.id + '/' + page.id;
        var pageTitle = page.titles[state.lang] || page.titles.en;
        var doc = bag[route];
        if (!doc) return;

        var titleLow = pageTitle.toLocaleLowerCase(state.lang);
        var headingsText = (doc.headings || []).map(function (h) { return h.text; }).join(' ');
        // Cache the plain-text version of the page on the doc record so
        // we don't re-parse the same HTML on every keystroke. The cache
        // lives only as long as the language bag itself; switching
        // languages loads a fresh bag and the cache rebuilds.
        if (doc._searchText === undefined) {
          doc._searchText = stripTags(doc.html).toLocaleLowerCase(state.lang);
        }
        var bodyTextLow = doc._searchText;
        var blob = (titleLow + ' ' + headingsText.toLocaleLowerCase(state.lang) + ' ' + bodyTextLow);

        var score = 0;
        if (!q) {
          score = 1; // show all when query is empty
        } else {
          if (titleLow === q) score += 100;
          if (titleLow.indexOf(q) !== -1) score += 50;
          if (headingsText.toLocaleLowerCase(state.lang).indexOf(q) !== -1) score += 20;
          if (blob.indexOf(q) !== -1) score += 5;
          // Multi-token: each token must appear somewhere
          var tokens = q.split(/\s+/).filter(Boolean);
          if (tokens.length > 1) {
            var allMatch = tokens.every(function (t) { return blob.indexOf(t) !== -1; });
            if (allMatch) score += 10;
          }
        }
        if (score === 0) return;

        hits.push({
          route: route,
          sectionTitle: sectionTitle,
          pageTitle: pageTitle,
          score: score,
        });
      });
    });

    hits.sort(function (a, b) { return b.score - a.score; });
    hits = hits.slice(0, 12);

    if (!hits.length) {
      setHtml(results, '<div class="guide-search-empty" data-i18n="guide.search.empty">No matches.</div>');
    } else {
      setHtml(results, hits.map(function (h, i) {
        return '<li><a href="#/' + h.route + '" data-route="' + h.route + '" class="' + (i === 0 ? 'focused' : '') + '">'
          + '<div class="section">' + escapeHtml(h.sectionTitle) + '</div>'
          + '<div class="title">' + highlight(h.pageTitle, q) + '</div>'
          + '</a></li>';
      }).join(''));
      // Re-translate after innerHTML update.
      if (window.ForgeLMi18n && window.ForgeLMi18n.setLanguage) {
        window.ForgeLMi18n.setLanguage(state.lang);
      }
      results.querySelectorAll('a').forEach(function (a) {
        a.addEventListener('click', function (e) {
          e.preventDefault();
          navigate(a.dataset.route, false);
        });
      });
    }
  }

  function handleSearchNav(key) {
    var results = document.getElementById('guide-search-results');
    if (!results) return;
    var links = Array.prototype.slice.call(results.querySelectorAll('a'));
    if (!links.length) return;
    var idx = links.findIndex(function (a) { return a.classList.contains('focused'); });
    if (key === 'ArrowDown') idx = Math.min(idx + 1, links.length - 1);
    if (key === 'ArrowUp')   idx = Math.max(idx - 1, 0);
    if (key === 'Enter') {
      if (idx >= 0) {
        var route = links[idx].dataset.route;
        if (route) navigate(route, false);
      }
      return;
    }
    links.forEach(function (a, i) { a.classList.toggle('focused', i === idx); });
    var fl = links[idx];
    if (fl) fl.scrollIntoView({ block: 'nearest' });
  }

  /* ──────────────────────────────────────────── lazy-load language data */

  function ensureLangLoaded(lang) {
    // Resolves once the page bag for *lang* is available, OR once we
    // know there is no bag and the caller should fall back to English.
    //
    // ``tools/build_usermanuals.py`` only emits JS bags for the
    // ``SUPPORTED_LANGUAGES`` set (currently ``en`` + ``tr``).  The
    // remaining picker entries (``de`` / ``fr`` / ``es`` / ``zh``) are
    // intentionally bag-less per the localization standard — operators
    // who pick those locales should see the English manual content with
    // a ``This page has not been translated yet`` banner.  The 404 from
    // ``js/usermanuals/<lang>.js`` is therefore an EXPECTED outcome for
    // deferred languages, not an error: we mark the lang as "loaded"
    // (with no bag) and resolve so the caller's bag-fall-through path
    // can pick up ``ForgeLMUserManuals.en`` and tag the page as a
    // fallback.
    return new Promise(function (resolve) {
      if (state.loadedLangs[lang]) { resolve(); return; }
      var existing = (window.ForgeLMUserManuals || {})[lang];
      if (existing) {
        state.loadedLangs[lang] = true;
        resolve();
        return;
      }
      var s = document.createElement('script');
      s.src = 'js/usermanuals/' + lang + '.js';
      s.onload = function () { state.loadedLangs[lang] = true; resolve(); };
      s.onerror = function () {
        // Deferred-language path: no bag emitted.  Mark as loaded so
        // we don't re-attempt the 404 on every navigation, and resolve
        // so the caller falls back to English with a banner.
        state.loadedLangs[lang] = true;
        resolve();
      };
      document.head.appendChild(s);
    });
  }

  /* ──────────────────────────────────────────── language watcher */

  function initLanguageWatcher() {
    var initial = document.documentElement.lang || 'en';
    state.lang = initial;
    var obs = new MutationObserver(function (muts) {
      muts.forEach(function (m) {
        if (m.attributeName === 'lang') {
          var newLang = document.documentElement.lang || 'en';
          if (newLang !== state.lang) {
            state.lang = newLang;
            ensureLangLoaded(newLang).then(function () {
              buildSidebar();
              renderCurrentRoute();
            });
          }
        }
      });
    });
    obs.observe(document.documentElement, { attributes: true });
  }

  /* ──────────────────────────────────────────── mobile drawer */

  function initMobileDrawer() {
    var btn = document.getElementById('guide-mobile-toggle');
    var sidebar = document.getElementById('guide-sidebar');
    if (!btn || !sidebar) return;
    btn.addEventListener('click', function () {
      sidebar.classList.toggle('open');
    });
  }

  function closeSidebar() {
    var sidebar = document.getElementById('guide-sidebar');
    if (sidebar) sidebar.classList.remove('open');
  }

  function updateMobileCrumb(title) {
    var el = document.getElementById('guide-mobile-crumb');
    if (el) el.textContent = title;
  }

  /* ──────────────────────────────────────────── mermaid */

  function initMermaid() {
    if (!window.mermaid) return;
    var theme = document.documentElement.getAttribute('data-theme') === 'light' ? 'default' : 'dark';
    try {
      window.mermaid.initialize({
        startOnLoad: false,
        theme: theme,
        securityLevel: 'strict',
        flowchart: { curve: 'basis', useMaxWidth: true },
        themeVariables: theme === 'dark' ? {
          background: '#161a24',
          primaryColor: '#1c2030',
          primaryBorderColor: '#f97316',
          primaryTextColor: '#e6e7ec',
          lineColor: '#9ea3b3',
          secondaryColor: '#11141c',
          tertiaryColor: '#0a0c12',
          fontFamily: 'Inter, sans-serif',
        } : {
          background: '#fafbfd',
          primaryColor: '#ffffff',
          primaryBorderColor: '#f97316',
          primaryTextColor: '#0c0f17',
          lineColor: '#4a5163',
          fontFamily: 'Inter, sans-serif',
        },
      });
    } catch (e) { /* mermaid older versions */ }

    // Re-init mermaid on theme toggle so diagrams pick up new colours.
    var themeObs = new MutationObserver(function () {
      var newTheme = document.documentElement.getAttribute('data-theme') === 'light' ? 'default' : 'dark';
      try {
        window.mermaid.initialize({ startOnLoad: false, theme: newTheme });
        renderCurrentRoute();
      } catch (e) { /* ignore */ }
    });
    themeObs.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
  }

  /* ──────────────────────────────────────────── helpers */

  // Shared escape helper from js/_shared.js; fallback handles the
  // ``s == null`` case the same way the original local version did.
  var escapeHtml = (window.ForgeLMShared && window.ForgeLMShared.escapeHtml) ||
    function (s) {
      if (s == null) return '';
      return String(s)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;')
        .replace(/>/g, '&gt;').replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
    };

  function stripTags(html) {
    // DOMParser yields an inert document — no scripts execute and no
    // resources load — so no innerHTML is needed on a live element.
    var doc = new DOMParser().parseFromString(html, 'text/html');
    return doc.body.textContent || '';
  }

  function highlight(text, query) {
    if (!query) return escapeHtml(text);
    var lower = text.toLocaleLowerCase(state.lang);
    var idx = lower.indexOf(query);
    if (idx === -1) return escapeHtml(text);
    var end = idx + query.length;
    return escapeHtml(text.slice(0, idx))
      + '<mark>' + escapeHtml(text.slice(idx, end)) + '</mark>'
      + escapeHtml(text.slice(end));
  }
})();
