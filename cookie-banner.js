/**
 * cookie-banner.js — Caricature.online
 * Include on every page: <script src="cookie-banner.js"></script>
 * Injects banner HTML, manages consent in localStorage.
 */
(function () {
  'use strict';

  const KEY = 'co_cookie_consent';
  const VER = '2025-05';

  // ── Helpers ──────────────────────────────────────────────
  function getConsent() {
    try { return JSON.parse(localStorage.getItem(KEY) || 'null'); }
    catch { return null; }
  }

  function saveConsent(prefs) {
    localStorage.setItem(KEY, JSON.stringify({
      ...prefs, version: VER, saved_at: new Date().toISOString()
    }));
    // Dispatch event so page can react (e.g. load analytics)
    window.dispatchEvent(new CustomEvent('cookieConsentUpdated', { detail: prefs }));
  }

  function hideBanner() {
    const b = document.getElementById('_co_banner');
    if (b) b.style.transform = 'translateY(110%)';
    setTimeout(() => { if (b) b.remove(); }, 400);
  }

  // ── Inject CSS ────────────────────────────────────────────
  const style = document.createElement('style');
  style.textContent = `
    #_co_banner {
      position:fixed;bottom:0;left:0;right:0;z-index:9999;
      background:#0f0d0a;border-top:1px solid rgba(255,255,255,.08);
      padding:18px 24px;display:flex;align-items:center;
      gap:16px;flex-wrap:wrap;
      transition:transform .4s cubic-bezier(.4,0,.2,1);
      font-family:'Outfit',sans-serif;
    }
    #_co_banner .cb-text {
      flex:1;font-size:13px;color:rgba(248,240,227,.65);
      line-height:1.55;min-width:200px;
    }
    #_co_banner .cb-text a { color:#c8792a; }
    #_co_banner .cb-actions { display:flex;gap:8px;flex-wrap:wrap;flex-shrink:0; }
    #_co_banner button {
      padding:9px 18px;border-radius:8px;font-size:13px;font-weight:600;
      cursor:pointer;border:none;transition:all .2s;white-space:nowrap;
      font-family:'Outfit',sans-serif;
    }
    #_co_banner .cb-accept { background:#c8792a;color:#fff; }
    #_co_banner .cb-accept:hover { background:#e8932e; }
    #_co_banner .cb-manage { background:rgba(255,255,255,.08);color:rgba(248,240,227,.8);border:1px solid rgba(255,255,255,.12); }
    #_co_banner .cb-manage:hover { background:rgba(255,255,255,.14); }
    #_co_banner .cb-reject { background:transparent;color:rgba(248,240,227,.45);border:1px solid rgba(255,255,255,.08); }
    #_co_banner .cb-reject:hover { color:rgba(248,240,227,.75); }
  `;
  document.head.appendChild(style);

  // ── Inject HTML ───────────────────────────────────────────
  function showBanner() {
    const div = document.createElement('div');
    div.id = '_co_banner';
    div.innerHTML = `
      <div class="cb-text">
        We use cookies for payments, language preferences, and anonymised analytics.
        <a href="cookies.html">Cookie Policy</a> · <a href="privacy.html">Privacy Policy</a>
      </div>
      <div class="cb-actions">
        <button class="cb-reject" id="_co_reject">Reject optional</button>
        <button class="cb-manage" onclick="window.location='cookies.html#manage'">Manage</button>
        <button class="cb-accept" id="_co_accept">Accept all</button>
      </div>
    `;
    document.body.appendChild(div);

    document.getElementById('_co_accept').onclick = function () {
      saveConsent({ functional:true, analytics:true, marketing:true });
      loadAnalytics();
      hideBanner();
    };
    document.getElementById('_co_reject').onclick = function () {
      saveConsent({ functional:false, analytics:false, marketing:false });
      hideBanner();
    };
  }

  // ── Load Google Analytics (if analytics consent given) ────
  function loadAnalytics() {
    if (window._co_ga_loaded) return;
    window._co_ga_loaded = true;
    // Replace G-XXXXXXXX with your real GA4 measurement ID
    const GA_ID = 'G-XXXXXXXX';
    const s = document.createElement('script');
    s.src = `https://www.googletagmanager.com/gtag/js?id=${GA_ID}`;
    s.async = true;
    document.head.appendChild(s);
    window.dataLayer = window.dataLayer || [];
    window.gtag = function(){ window.dataLayer.push(arguments); };
    window.gtag('js', new Date());
    window.gtag('config', GA_ID, { anonymize_ip: true });
  }

  // ── Init ──────────────────────────────────────────────────
  function init() {
    const consent = getConsent();

    if (!consent) {
      // First visit — show banner after short delay
      setTimeout(showBanner, 1200);
    } else {
      // Returning visitor — apply saved prefs
      if (consent.analytics) loadAnalytics();
    }
  }

  // Run after DOM ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
