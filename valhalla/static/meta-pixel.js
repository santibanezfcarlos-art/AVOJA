/* Valhalla Office · Meta Pixel 1916480605982090.
 * Only loads third-party tracking after express marketing consent.
 * Never sends names, emails, RUTs, bookings or payment tokens.
 */
(() => {
  'use strict';
  const PIXEL_ID = '1916480605982090';
  const CONSENT_KEY = 'valhalla-meta-consent-v1';
  const PURCHASE_PREFIX = 'valhalla-meta-purchase-v1-';
  let enabled = false;
  let loaded = false;
  let pendingPurchase = null;
  let banner = null;

  function getConsent() {
    try { return localStorage.getItem(CONSENT_KEY); } catch { return null; }
  }
  function storeConsent(value) {
    try { localStorage.setItem(CONSENT_KEY, value); } catch { /* session-only */ }
  }
  function validAmount(value) {
    return typeof value === 'number' && Number.isSafeInteger(value) && value > 0;
  }
  function loadPixel() {
    if (!enabled || loaded) return;
    loaded = true;
    /* Standard Meta Pixel bootstrap, intentionally delayed until opt-in. */
    !function(f,b,e,v,n,t,s)
    {if(f.fbq)return;n=f.fbq=function(){n.callMethod?
    n.callMethod.apply(n,arguments):n.queue.push(arguments)};
    if(!f._fbq)f._fbq=n;n.push=n;n.loaded=!0;n.version='2.0';
    n.queue=[];t=b.createElement(e);t.async=!0;
    t.src=v;s=b.getElementsByTagName(e)[0];
    s.parentNode.insertBefore(t,s)}(window, document,'script',
    'https://connect.facebook.net/en_US/fbevents.js');
    if (typeof window.fbq === 'function') {
      window.fbq('init', PIXEL_ID);
      window.fbq('track', 'PageView');
    }
    flushPurchase();
  }
  function wasSent(id) {
    try { return localStorage.getItem(PURCHASE_PREFIX + id) === 'yes'; } catch { return false; }
  }
  function markSent(id) {
    try { localStorage.setItem(PURCHASE_PREFIX + id, 'yes'); } catch { /* best effort */ }
  }
  function flushPurchase() {
    if (!enabled || !pendingPurchase || typeof window.fbq !== 'function') return;
    const { id, amount, hours } = pendingPurchase;
    if (wasSent(id)) { pendingPurchase = null; return; }
    window.fbq('track', 'Purchase',
      { currency:'CLP', value:amount, content_type:'product', content_ids:['hours-' + hours], num_items:1 },
      { eventID:'valhalla-production-purchase-' + id });
    markSent(id);
    pendingPurchase = null;
  }
  function trackApprovedPayment(payment) {
    if (!payment || payment.state !== 'approved' || payment.environment !== 'production' ||
        !Number.isSafeInteger(payment.id) || payment.id <= 0 || !validAmount(payment.amount) ||
        !Number.isSafeInteger(payment.hours) || payment.hours < 1 || wasSent(payment.id)) return;
    pendingPurchase = { id:payment.id, amount:payment.amount, hours:payment.hours };
    flushPurchase();
  }
  function trackApprovedReturn(data, result) {
    if (result !== 'approved' || !Array.isArray(data?.payments)) return;
    let id = 0;
    try { id = Number(sessionStorage.getItem('valhalla-webpay-checkout')); } catch { return; }
    if (!Number.isSafeInteger(id) || id <= 0) return;
    trackApprovedPayment(data.payments.find(payment => payment.id === id));
  }
  function trackCheckout(amount, hours) {
    if (!enabled || typeof window.fbq !== 'function' || !validAmount(amount) ||
        !Number.isSafeInteger(hours) || hours < 1) return;
    window.fbq('track', 'InitiateCheckout',
      { currency:'CLP', value:amount, content_type:'product', content_ids:['hours-' + hours], num_items:1 });
  }
  function clearMetaCookies() {
    for (const name of ['_fbp','_fbc']) {
      const hosts = [undefined, location.hostname, '.' + location.hostname,
        location.hostname.replace(/^www\./, ''), '.' + location.hostname.replace(/^www\./, '')];
      for (const host of hosts) {
        document.cookie = name + '=; Max-Age=0; path=/; SameSite=Lax' + (host ? '; domain=' + host : '');
      }
    }
  }
  function choice(value) {
    const wasEnabled = enabled;
    storeConsent(value);
    enabled = value === 'yes';
    if (banner) { banner.remove(); banner = null; }
    if (enabled) loadPixel();
    else {
      pendingPurchase = null;
      clearMetaCookies();
      if (wasEnabled) location.reload(); // Unload any previously activated Meta scripts.
    }
  }
  function showPreferences() {
    if (banner) { banner.querySelector('[data-meta-accept]')?.focus(); return; }
    banner = document.createElement('section');
    banner.className = 'meta-consent-banner';
    banner.setAttribute('role','region');
    banner.setAttribute('aria-label','Preferencias de medición');
    banner.innerHTML = '<div class="meta-consent-inner"><div><strong>¿Nos permites medir nuestros anuncios?</strong>' +
      '<p>Con tu autorización usamos el píxel de Meta para medir visitas y compras. ' +
      'Puedes rechazarlo y seguir reservando normalmente. ' +
      '<a href="/privacidad">Política de privacidad</a>.</p></div>' +
      '<div class="meta-consent-actions"><button type="button" data-meta-reject>Rechazar</button>' +
      '<button type="button" data-meta-accept>Aceptar</button></div></div>';
    banner.querySelector('[data-meta-reject]').addEventListener('click', () => choice('no'));
    banner.querySelector('[data-meta-accept]').addEventListener('click', () => choice('yes'));
    document.body.appendChild(banner);
  }
  window.valhallaMeta = Object.freeze({ trackCheckout, trackApprovedPayment, trackApprovedReturn });
  document.getElementById('meta-cookie-settings')?.addEventListener('click', event => {
    event.preventDefault();
    showPreferences();
  });
  if (getConsent() === 'yes') { enabled = true; loadPixel(); }
  else if (getConsent() !== 'no') showPreferences();
})();