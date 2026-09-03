// ==UserScript==
// @name         Shopee Affiliate Root Navigator
// @namespace    shopee-crawl-nav
// @version      0.2
// @description  Chay tung root bang DIEU HUONG TRANG THAT (thay vi tu fetch API): moi root, tab tu load trang offer/product_offer/<item_id> nhu nguoi dung mo link - Shopee tu chay report (df.infra) + tu goi /api/v3/offer/product voi token hop le (1 report chi dung duoc 1 lan, nen KHONG duoc tu fetch offer thu 2 trong cung 1 trang). Script chi HOOK fetch tu document-start de CHUP response offer roi day server local xu ly (verify/seed/gan group/finish) - khong goi them request that nao toi Shopee.
// @match        https://affiliate.shopee.vn/*
// @match        https://affiliate.shopee.sg/*
// @match        https://affiliate.shopee.ph/*
// @match        https://affiliate.shopee.co.th/*
// @match        https://affiliate.shopee.com.my/*
// @run-at       document-start
// @updateURL    http://127.0.0.1:8877/userscripts/tampermonkey_affiliate_root_navigator.user.js
// @downloadURL  http://127.0.0.1:8877/userscripts/tampermonkey_affiliate_root_navigator.user.js
// @grant        GM_xmlhttpRequest
// @grant        GM_setValue
// @grant        GM_getValue
// @grant        GM_info
// @connect      127.0.0.1
// @connect      localhost
// ==/UserScript==

(function () {
  'use strict';

  // ---- Hang so / key GM storage (doc lap voi script cu 0.16) ----
  const SCRIPT_VERSION = '0.2';
  const SERVER_URL_KEY = 'arn_server_url';
  const DEVICE_KEY_KEY = 'arn_device_key';
  const RUN_KEY = 'arn_run';               // '1' = dang chay (song sot qua reload)
  const STATUS_KEY = 'arn_status';
  const LOG_KEY = 'arn_log';
  const ROOT_DELAY_MIN_KEY = 'arn_root_delay_min_s';
  const ROOT_DELAY_MAX_KEY = 'arn_root_delay_max_s';
  const ROOT_DELAY_MIN_DEFAULT_S = 2.5;
  const ROOT_DELAY_MAX_DEFAULT_S = 6;
  const SERVER_URL_DEFAULT = 'http://127.0.0.1:8877';
  const POLL_MS = 4000;                    // hoi server "co root nao cho minh khong"
  const CAPTURE_TIMEOUT_MS = 20000;        // cho trang tu goi offer/product sau khi load
  const NAV_FAIL_MAX = 2;                  // so lan reload lien tiep cung 1 root khong chup duoc offer -> danh fail
  const OFFER_MARKER = '/api/v3/offer/product';

  const MARKET_BY_AFFILIATE_HOST = {
    'affiliate.shopee.vn': 'vn',
    'affiliate.shopee.sg': 'sg',
    'affiliate.shopee.ph': 'ph',
    'affiliate.shopee.co.th': 'th',
    'affiliate.shopee.com.my': 'my',
  };
  const currentMarket = MARKET_BY_AFFILIATE_HOST[location.hostname] || null;

  // ---- Trang thai trong RAM (reset moi lan load trang) ----
  let activeDeviceKey = null;
  let lastCaptured = null;      // { itemid, offerData } - offer do CHINH TRANG goi
  let processing = false;       // chong xu ly trung khi ca fetch + XHR cung bat duoc
  let captureTimer = null;

  // ================= Utilities =================
  function getVal(key, def) { try { return GM_getValue(key, def); } catch (e) { return def; } }
  function setVal(key, val) { try { GM_setValue(key, val); } catch (e) {} }
  function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

  function pageItemId() {
    const m = location.pathname.match(/\/offer\/product_offer\/(\d+)/);
    return m ? m[1] : null;
  }

  function logLine(msg) {
    try {
      let list = [];
      try { list = JSON.parse(GM_getValue(LOG_KEY, '[]')) || []; } catch (e) { list = []; }
      const line = `[${new Date().toLocaleTimeString('vi-VN')}] ${msg}`;
      console.log('[root-navigator] ' + msg);
      list.push(line);
      while (list.length > 60) list.shift();
      GM_setValue(LOG_KEY, JSON.stringify(list));
    } catch (e) { console.log('[root-navigator] ' + msg); }
    // cap nhat status box neu panel dang hien thi
    const el = document.getElementById('arn-status');
    if (el) el.textContent = msg;
  }
  function setStatus(msg) {
    setVal(STATUS_KEY, msg);
    logLine(msg);
  }

  function getDeviceKey() { return (getVal(DEVICE_KEY_KEY, '') || '').trim(); }

  function offerProductUrl(market, itemid) {
    const host = Object.keys(MARKET_BY_AFFILIATE_HOST).find(h => MARKET_BY_AFFILIATE_HOST[h] === market);
    if (!host) return null;
    return `https://${host}/offer/product_offer/${itemid}`;
  }

  function sanitizeDelayRange(rawMin, rawMax, dMin, dMax) {
    let min = parseFloat(rawMin), max = parseFloat(rawMax);
    if (isNaN(min) || min < 0) min = dMin;
    if (isNaN(max) || max < 0) max = dMax;
    if (min > max) { const t = min; min = max; max = t; }
    return { min, max };
  }
  function getRootDelayRangeMs() {
    const { min, max } = sanitizeDelayRange(
      getVal(ROOT_DELAY_MIN_KEY, ROOT_DELAY_MIN_DEFAULT_S),
      getVal(ROOT_DELAY_MAX_KEY, ROOT_DELAY_MAX_DEFAULT_S),
      ROOT_DELAY_MIN_DEFAULT_S, ROOT_DELAY_MAX_DEFAULT_S);
    return { min: min * 1000, max: max * 1000 };
  }

  // ================= Server local =================
  function serverRequest(method, path, body, timeoutMs) {
    const serverUrl = getVal(SERVER_URL_KEY, SERVER_URL_DEFAULT);
    return new Promise((resolve, reject) => {
      GM_xmlhttpRequest({
        method,
        url: serverUrl + path,
        headers: { 'Content-Type': 'application/json' },
        data: body ? JSON.stringify(body) : undefined,
        timeout: timeoutMs || 20000,
        onload: (resp) => {
          try { resolve(JSON.parse(resp.responseText)); }
          catch (e) { reject(new Error('Server tra ve khong phai JSON: ' + String(resp.responseText).slice(0, 200))); }
        },
        onerror: () => reject(new Error('Khong ket noi duoc local server (' + serverUrl + ')?')),
        ontimeout: () => reject(new Error('Timeout goi local server (' + method + ' ' + path + ')')),
      });
    });
  }

  async function heartbeat(status, currentRoot) {
    if (!activeDeviceKey) return;
    try {
      await serverRequest('POST', '/api/workers/heartbeat', {
        device_key: activeDeviceKey, status, current_root: currentRoot || null, market: currentMarket,
      }, 8000);
    } catch (e) { /* loi mang tam thoi - khong lam gian doan */ }
  }

  // ================= Hook chup offer response =================
  // PHẢI chạy document-start, trước khi sfu/engine của Shopee wrap fetch — khi đó chuỗi gọi là
  // app -> [sfu wrapper them header] -> hook cua ta -> fetch goc: ta thay request sau khi da co
  // du header + chup duoc response that (token hop le do chinh trang tao).
  function looksLikeOfferUrl(url) {
    if (!url) return false;
    try { return url.indexOf(OFFER_MARKER) !== -1; } catch (e) { return false; }
  }

  function installFetchHook() {
    if (window.__arnFetchHooked) return;
    window.__arnFetchHooked = true;
    const orig = window.fetch.bind(window);
    window.fetch = async function (input, init) {
      let url = null;
      try { url = typeof input === 'string' ? input : (input && input.url) || null; } catch (e) {}
      let resp;
      try { resp = await orig(input, init); } catch (e) { throw e; }
      try {
        if (url && looksLikeOfferUrl(url)) {
          const ct = (resp.headers && resp.headers.get && resp.headers.get('content-type')) || '';
          if (ct.indexOf('json') !== -1) {
            const clone = resp.clone();
            const text = await clone.text();
            let json = null;
            try { json = JSON.parse(text); } catch (e) {}
            if (json && json.code === 0 && json.data && json.data.item_id) {
              handleCapturedOffer(String(json.data.item_id), json.data);
            }
          }
        }
      } catch (e) { /* khong lam vo response */ }
      return resp;
    };
  }

  function installXhrHook() {
    if (window.__arnXhrHooked) return;
    window.__arnXhrHooked = true;
    const origOpen = XMLHttpRequest.prototype.open;
    XMLHttpRequest.prototype.open = function (method, url) {
      try { this.__arnUrl = String(url); } catch (e) {}
      return origOpen.apply(this, arguments);
    };
    const origSend = XMLHttpRequest.prototype.send;
    XMLHttpRequest.prototype.send = function () {
      const self = this;
      const url = this.__arnUrl || '';
      if (looksLikeOfferUrl(url)) {
        this.addEventListener('load', function () {
          try {
            if (self.status === 200) {
              const text = self.responseText || '';
              let json = null;
              try { json = JSON.parse(text); } catch (e) {}
              if (json && json.code === 0 && json.data && json.data.item_id) {
                handleCapturedOffer(String(json.data.item_id), json.data);
              }
            }
          } catch (e) {}
        });
      }
      return origSend.apply(this, arguments);
    };
  }

  // ================= Xu ly root (chi goi server local) =================
  function handleCapturedOffer(itemid, offerData) {
    // CHI LUU capture (co the chay truoc khi bootTick xac nhan day la root cua minh) -
    // viec xu ly do processCapturedIfMine() quyet dinh khi da chac chan root khop.
    if (lastCaptured && lastCaptured.itemid === itemid) return;
    lastCaptured = { itemid, offerData };
  }

  // Goi khi da xac nhan: root duoc giao == offer vua chup tren trang nay.
  // Tra ve true neu da xu ly (de tranh goi 2 lan).
  async function processCapturedIfMine(rootItemid, market) {
    if (processing) return false;
    if (getVal(RUN_KEY, '0') !== '1') return false;
    if (!lastCaptured || String(lastCaptured.itemid) !== String(rootItemid)) return false;
    // do thoi gian nghi tu luc xong root truoc den khi co offer cua root nay (gom delay + load trang)
    const tPrev = parseInt(getVal('arn_t_last_root', '0'), 10) || 0;
    if (tPrev) {
      const gapSec = Math.round((Date.now() - tPrev) / 1000);
      logLine('(Nghi tu root truoc: ' + gapSec + 's - gom delay cau hinh + thoi gian load trang)');
      setVal('arn_t_last_root', '0');
    }
    setVal('arn_navfail_' + rootItemid, '0'); // capture thanh cong -> reset bo dem reload loi
    processing = true;
    try {
      await processRoot(String(rootItemid), lastCaptured.offerData);
    } catch (e) {
      logLine('LOI xu ly root ' + rootItemid + ': ' + e.message);
    } finally {
      processing = false;
    }
    // sang root tiep theo (co the reload trang)
    await goNextRoot(true);
    return true;
  }

  async function processRoot(itemid, offerData) {
    logLine('=== Root ' + itemid + ': da chup offer tu trang (token that) - gui server xu ly ===');
    await heartbeat('working', itemid);
    const res = await serverRequest('POST', '/api/roots/nav_complete', { offer_data: offerData, market: currentMarket }, 60000);
    if (res && res.outcome === 'done') {
      logLine('=== XONG root ' + itemid + ' (' + (res.member_count + 1) + '/6) ===');
    } else if (res && res.outcome === 'rejected') {
      logLine('=== Root ' + itemid + ': khong dat chuan - finish (bo qua) ===');
    } else {
      logLine('!!! Root ' + itemid + ': server tra loi la: ' + JSON.stringify(res).slice(0, 200));
    }
    if (res && res.errors && res.errors.length) {
      logLine('  (server bao ' + res.errors.length + ' candidate loi - khong lam chet root)');
    }
  }

  function navigateToRootUrl(market, itemid) {
    const url = offerProductUrl(market, itemid);
    if (!url) { logLine('Khong dung duoc URL cho market=' + market); return false; }
    setVal('arn_current_item', String(itemid));
    logLine('Dieu huong toi root ' + itemid + ' ...');
    location.replace(url);
    return true;
  }

  async function goNextRoot(justFinished) {
    if (getVal(RUN_KEY, '0') !== '1') { logLine('Dung (khong nhan root moi).'); return; }
    const deviceKey = getDeviceKey();
    if (!deviceKey || !currentMarket) return;

    if (justFinished) {
      setVal('arn_t_last_root', String(Date.now())); // moc do thoi gian nghi giua 2 root
      // delay chong captcha giua 2 root truoc khi dieu huong
      const { min, max } = getRootDelayRangeMs();
      if (!isStoppedSoon()) await sleep(min + Math.random() * (max - min));
    }

    let res = null;
    try {
      res = await serverRequest('GET', '/api/workers/' + encodeURIComponent(deviceKey) + '/assigned_root?market=' + encodeURIComponent(currentMarket));
    } catch (e) {
      logLine('Loi hoi root: ' + e.message + ' - thu lai sau ' + POLL_MS + 'ms.');
      setTimeout(() => goNextRoot(false), POLL_MS);
      return;
    }

    const root = res && res.root;
    if (!root) {
      await heartbeat('idle');
      logLine('Chua co root duoc giao - cho ' + POLL_MS + 'ms...');
      setTimeout(() => goNextRoot(false), POLL_MS);
      return;
    }
    const rootItem = String(root.itemid);
    const pageItem = pageItemId();

    if (pageItem && pageItem === rootItem) {
      // Dang o dung trang cua root
      setVal('arn_current_item', rootItem);
      const done = await processCapturedIfMine(rootItem, root.market || currentMarket);
      if (done) return; // da xu ly xong + dang chuyen root tiep
      startCaptureWatch(rootItem, root.market || currentMarket); // chua co capture: cho them
      return;
    }
    // khong o dung trang -> dieu huong
    navigateToRootUrl(root.market || currentMarket, rootItem);
  }

  function isStoppedSoon() {
    return getVal(RUN_KEY, '0') !== '1';
  }

  function startCaptureWatch(itemid, market) {
    if (captureTimer) clearTimeout(captureTimer);
    const retryKey = 'arn_navfail_' + itemid;
    const attempts = parseInt(getVal(retryKey, '0'), 10) || 0;
    captureTimer = setTimeout(async () => {
      captureTimer = null;
      if (getVal(RUN_KEY, '0') !== '1') return;
      if (lastCaptured && String(lastCaptured.itemid) === String(itemid)) {
        await processCapturedIfMine(itemid, market); // co capture nhung chua xu ly -> xu ly ngay
        return;
      }
      if (pageLooksUnavailable()) {
        setStatus('!!! Trang bi chan ("Page Unavailable") khi mo root ' + itemid + ' - dung lai, kiem tra tab.');
        await heartbeat('blocked', itemid);
        setVal(RUN_KEY, '0');
        return;
      }
      // khong chup duoc offer sau CAPTURE_TIMEOUT -> thu reload 1 lan, qua NAV_FAIL_MAX lan thi danh fail
      const n = attempts + 1;
      setVal(retryKey, String(n));
      if (n > NAV_FAIL_MAX) {
        logLine('Root ' + itemid + ': khong chup duoc offer sau ' + NAV_FAIL_MAX + ' lan thu - danh fail.');
        try { await serverRequest('POST', '/api/roots/' + encodeURIComponent(itemid) + '/fail', { reason: 'no_offer_capture', market }); } catch (e) {}
        setVal(retryKey, '0');
        await goNextRoot(true);
      } else {
        logLine('Chua thay offer cua root ' + itemid + ' (lan ' + n + '/' + NAV_FAIL_MAX + ') - reload lai.');
        location.reload();
      }
    }, CAPTURE_TIMEOUT_MS);
  }

  function pageLooksUnavailable() {
    try {
      const body = document.body;
      if (!body) return false;
      const text = body.innerText || '';
      return /page unavailable/i.test(text) && /something went wrong/i.test(text);
    } catch (e) { return false; }
  }

  // ================= Vong dieu huong chinh =================
  async function bootTick() {
    if (getVal(RUN_KEY, '0') !== '1') return;
    if (!activeDeviceKey) activeDeviceKey = getDeviceKey();
    if (!activeDeviceKey || !currentMarket) {
      setStatus('Thieu device key hoac market - bam Start tren panel.');
      setVal(RUN_KEY, '0');
      return;
    }
    const pageItem = pageItemId();
    if (!pageItem) {
      // khong o trang san pham -> hoi root roi dieu huong
      await goNextRoot(false);
      return;
    }
    // o trang san pham: kiem tra co phai root duoc giao cho minh khong
    let res = null;
    try {
      res = await serverRequest('GET', '/api/workers/' + encodeURIComponent(activeDeviceKey) + '/assigned_root?market=' + encodeURIComponent(currentMarket));
    } catch (e) { /* de loop duoi xu ly */ }
    const root = res && res.root;
    const rootItem = root && String(root.itemid);
    if (rootItem && rootItem !== pageItem) {
      navigateToRootUrl(root.market || currentMarket, rootItem); // reload sang dung root
      return;
    }
    if (rootItem && rootItem === pageItem) {
      setVal('arn_current_item', rootItem);
      const done = await processCapturedIfMine(rootItem, root.market || currentMarket);
      if (!done) startCaptureWatch(rootItem, root.market || currentMarket);
      return;
    }
    // chua co root -> idle poll
    await heartbeat('idle');
    logLine('(boot) Chua co root duoc giao - poll lai...');
    setTimeout(bootTick, POLL_MS);
  }

  // ================= Panel UI =================
  function injectPanel() {
    if (document.getElementById('arn-panel') || !document.body) return;
    const panel = document.createElement('div');
    panel.id = 'arn-panel';
    panel.style.cssText = [
      'position:fixed', 'top:70px', 'right:16px', 'z-index:2147483647',
      'background:#fff', 'border:1px solid #ee4d2d', 'border-radius:8px',
      'padding:10px 12px', 'font:12px/1.5 system-ui,-apple-system,sans-serif',
      'color:#222', 'box-shadow:0 2px 10px rgba(0,0,0,.25)', 'width:420px',
    ].join(';');

    panel.innerHTML = `
      <div style="font-weight:700;color:#ee4d2d;margin-bottom:4px;">Root Navigator <span style="font-weight:400;color:#888;font-size:11px;">v${SCRIPT_VERSION} (navigation)</span></div>
      <div style="font-size:11px;color:#666;margin-bottom:6px;">Market: <b>${currentMarket || 'KHONG NHAN DIEN DUOC'}</b> | Moi root = 1 lan load trang that (tra report chong bot).</div>
      <div style="display:flex;gap:6px;margin-bottom:4px;">
        <input id="arn-server" type="text" placeholder="Local server URL" style="flex:1;padding:4px 6px;border:1px solid #ccc;border-radius:4px;">
      </div>
      <div style="display:flex;gap:6px;margin-bottom:4px;">
        <input id="arn-device" type="text" placeholder="Device key (ten tai khoan/profile)" style="flex:1;padding:4px 6px;border:1px solid #ccc;border-radius:4px;">
      </div>
      <div style="display:flex;align-items:center;gap:6px;margin-bottom:4px;flex-wrap:wrap;">
        <span style="min-width:110px;">Delay giua 2 root (s):</span>
        <input id="arn-dmin" type="number" min="0" step="0.5" style="width:64px;padding:3px 5px;border:1px solid #ccc;border-radius:4px;">
        <span>-</span>
        <input id="arn-dmax" type="number" min="0" step="0.5" style="width:64px;padding:3px 5px;border:1px solid #ccc;border-radius:4px;">
      </div>
      <div style="display:flex;gap:6px;margin-bottom:6px;">
        <button id="arn-start-btn" style="flex:1;padding:5px 0;cursor:pointer;background:#ee4d2d;color:#fff;border:none;border-radius:4px;">Start</button>
        <button id="arn-stop-btn" style="flex:1;padding:5px 0;cursor:pointer;background:#f2f2f2;color:#222;border:1px solid #ccc;border-radius:4px;">Stop</button>
      </div>
      <div id="arn-status" style="background:#f8f9fa;border:1px solid #eee;border-radius:4px;padding:5px 8px;margin-bottom:6px;font-size:11px;color:#333;word-break:break-word;"></div>
      <textarea id="arn-log" readonly style="width:100%;height:200px;box-sizing:border-box;font:11px/1.4 monospace;border:1px solid #ccc;border-radius:4px;padding:4px;"></textarea>
    `;
    document.body.appendChild(panel);

    const serverInput = document.getElementById('arn-server');
    const deviceInput = document.getElementById('arn-device');
    const dmin = document.getElementById('arn-dmin');
    const dmax = document.getElementById('arn-dmax');
    serverInput.value = getVal(SERVER_URL_KEY, SERVER_URL_DEFAULT);
    deviceInput.value = getVal(DEVICE_KEY_KEY, '');
    const rm = getVal(ROOT_DELAY_MIN_KEY, null);
    const rM = getVal(ROOT_DELAY_MAX_KEY, null);
    if (rm !== null) dmin.value = rm;
    if (rM !== null) dmax.value = rM;
    serverInput.addEventListener('change', () => setVal(SERVER_URL_KEY, serverInput.value.trim()));
    deviceInput.addEventListener('change', () => setVal(DEVICE_KEY_KEY, deviceInput.value.trim()));
    dmin.addEventListener('change', () => setVal(ROOT_DELAY_MIN_KEY, dmin.value === '' ? null : parseFloat(dmin.value)));
    dmax.addEventListener('change', () => setVal(ROOT_DELAY_MAX_KEY, dmax.value === '' ? null : parseFloat(dmax.value)));

    document.getElementById('arn-start-btn').addEventListener('click', () => startRun());
    document.getElementById('arn-stop-btn').addEventListener('click', () => {
      setVal(RUN_KEY, '0');
      if (captureTimer) { clearTimeout(captureTimer); captureTimer = null; }
      setStatus('Da bam Stop.');
      heartbeat('idle');
    });

    // hien trang thai + log cu
    const st = getVal(STATUS_KEY, '');
    const stEl = document.getElementById('arn-status');
    if (st) stEl.textContent = st;
    const logs = getVal(LOG_KEY, '[]');
    const box = document.getElementById('arn-log');
    try {
      const arr = JSON.parse(logs) || [];
      box.value = arr.join('\n');
      box.scrollTop = box.scrollHeight;
    } catch (e) {}
  }

  async function startRun() {
    const deviceKey = getDeviceKey();
    if (!deviceKey) { setStatus('Chua nhap Device key.'); return; }
    if (!currentMarket) { setStatus('Khong nhan dien duoc market cho hostname nay.'); return; }
    if (getVal(RUN_KEY, '0') === '1') { setStatus('Da dang chay roi.'); return; }
    activeDeviceKey = deviceKey;
    setVal(RUN_KEY, '1');
    setVal('arn_current_item', null);
    setStatus('Start - dang tim root...');
    await heartbeat('idle');
    bootTick();
  }

  // ================= Khoi dong =================
  installFetchHook();
  installXhrHook();

  document.addEventListener('DOMContentLoaded', () => {
    injectPanel();
    // neu dang chay (flag con song qua reload) -> tiep tuc vong dieu huong
    if (getVal(RUN_KEY, '0') === '1') {
      activeDeviceKey = getDeviceKey();
      setTimeout(bootTick, 80); // chi can so DOMContentLoaded xong la co the hoi server
    }
  }, { once: true });

  // truong hop trang da load xong truoc khi listener dang ky (hien rare o document-start)
  if (document.readyState !== 'loading') {
    setTimeout(() => {
      injectPanel();
      if (getVal(RUN_KEY, '0') === '1') bootTick();
    }, 80);
  }
})();
