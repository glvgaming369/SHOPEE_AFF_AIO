// ==UserScript==
// @name         Shopee Affiliate Offer Group Scraper
// @namespace    shopee-crawl
// @version      0.9
// @description  BFS tu 1 link goc (root) qua "san pham tuong tu" cua offer/product, gom du 60 san pham dat 3 tieu chi (aff_7days/sold/seller_commission) cho 1 group_id, dong bo qua local server (affiliate_scrape_server.py) de gan group nguyen tu khi chay nhieu Chrome profile song song.
// @match        https://affiliate.shopee.vn/*
// @match        https://affiliate.shopee.sg/*
// @match        https://affiliate.shopee.ph/*
// @match        https://affiliate.shopee.co.th/*
// @match        https://affiliate.shopee.com.my/*
// @updateURL    http://127.0.0.1:8877/userscripts/tampermonkey_affiliate_group_scraper.user.js
// @downloadURL  http://127.0.0.1:8877/userscripts/tampermonkey_affiliate_group_scraper.user.js
// @grant        GM_xmlhttpRequest
// @grant        GM_setValue
// @grant        GM_getValue
// @grant        GM_info
// @grant        unsafeWindow
// @connect      127.0.0.1
// @connect      localhost
// @run-at       document-idle
// ==/UserScript==

(function () {
  'use strict';

  const SCRIPT_VERSION = '0.9'; // khop @version o header - doi ca 2 cho khi sua script
  const ENDPOINT_MARKER = '/api/v3/offer/product';
  const GROUP_TARGET = 60;
  const CALL_CAP_PER_ROOT = 500;
  const CALL_DELAY_MIN_MS = 400;
  const CALL_DELAY_MAX_MS = 900;
  const POLL_ASSIGNMENT_MS = 4000; // khoang cach hoi server "co viec chua" luc dang ranh

  const SERVER_URL_KEY = 'aog_server_url';
  const DEVICE_KEY_KEY = 'aog_device_key';
  const STOP_KEY = 'aog_stop';
  const SERVER_URL_DEFAULT = 'http://127.0.0.1:8877';
  const OWN_SCRIPT_FILE = 'tampermonkey_affiliate_group_scraper.user.js';
  const LAST_UPDATE_CHECK_KEY = 'aog_last_update_check';
  const UPDATE_CHECK_INTERVAL_MS = 6 * 60 * 60 * 1000; // 6 gio - tranh spam server moi lan mo tab

  // Market cua CHINH tab nay - suy tu hostname luc load (khop danh sach @match o tren).
  // Server dung gia tri nay de dam bao tab chi bao gio duoc giao root DUNG market no dang
  // mo (tab tren affiliate.shopee.co.th goi API that qua location.origin, giao nham root
  // market khac se lam MOI request that toi Shopee that bai vi sai catalog/session).
  const MARKET_BY_AFFILIATE_HOST = {
    'affiliate.shopee.vn': 'vn',
    'affiliate.shopee.sg': 'sg',
    'affiliate.shopee.ph': 'ph',
    'affiliate.shopee.co.th': 'th',
    'affiliate.shopee.com.my': 'my',
  };
  const currentMarket = MARKET_BY_AFFILIATE_HOST[location.hostname] || null;

  // Dat 1 lan luc Start (runLoop) - dung cho heartbeat() goi tu nhieu noi (runBfsForRoot)
  // ma khong phai truyen deviceKey qua tung tham so ham.
  let activeDeviceKey = null;

  async function heartbeat(status, currentRoot) {
    if (!activeDeviceKey) return;
    try {
      await serverRequest('POST', '/api/workers/heartbeat', {
        device_key: activeDeviceKey, status, current_root: currentRoot || null,
        market: currentMarket,
      });
    } catch (e) {
      // Loi mang tam thoi luc bao trang thai - KHONG lam gian doan BFS vi viec nay.
    }
  }

  function isStopped() {
    return GM_getValue(STOP_KEY, false);
  }
  function requestStop() {
    GM_setValue(STOP_KEY, true);
  }
  function clearStop() {
    GM_setValue(STOP_KEY, false);
  }
  function sleep(ms) {
    return new Promise((r) => setTimeout(r, ms));
  }
  function randomDelay(min, max) {
    return sleep(min + Math.random() * (max - min));
  }

  // ---- Goi Shopee that: cung origin voi trang (affiliate.shopee.*) nen fetch() thuong
  // la du, cookie tu dinh kem, khong can GM_xmlhttpRequest o day (khac phan goi local
  // server ben duoi - do la KHAC origin nen bat buoc phai qua GM_xmlhttpRequest de ne CORS). ----
  async function callOfferApi(itemId) {
    const url = new URL(location.origin + ENDPOINT_MARKER);
    url.searchParams.set('item_id', String(itemId));
    // QUAN TRONG: unsafeWindow.fetch (khong phai fetch thuong cua sandbox Tampermonkey) -
    // da xac nhan qua thuc te la fetch thuong bi Shopee tra 403 ngay lan goi dau (thieu
    // gi do o request that su di ra, du credentials:'include' - unsafeWindow.fetch khop
    // dung request cua chinh trang, giong het probe script da test OK truoc do).
    const resp = await unsafeWindow.fetch(url.toString(), { method: 'GET', credentials: 'include' });
    const rawText = await resp.text();
    let json = null;
    try {
      json = JSON.parse(rawText);
    } catch (e) {
      // khong phai JSON - co the la trang chan/captcha/loi HTML
    }
    return { status: resp.status, json, rawText };
  }

  // Phat hien bat thuong tong quat - CHI coi la "bi chan/captcha" khi 403/429 (dau hieu
  // anti-bot ro rang cua Shopee) hoac response KHONG phai JSON (trang HTML/captcha thuc
  // su). KHONG con coi status>=500 la chan - da gap bug thuc te: item sai/khong ton tai
  // tra ve HTTP 599 KEM JSON HOP LE (vd {"code":599,"msg":"getProductDetail
  // error|itemBassSpexService"}), truoc day bi heuristic nay bat nham thanh "bi chan" nen
  // dung ca vong lap cho toi khi nguoi dung tu bam Start lai, thay vi danh dau 'fail' roi
  // tu chuyen sang root/candidate khac nhu cac loi API that su khac (xem nhanh
  // "!rootResp.json || rootResp.json.code !== 0" ngay duoi day). Co JSON hop le (du HTTP
  // status la gi) nghia la Shopee DA XU LY request va tra loi that su - khong phai bi chan.
  function looksBlocked(status, json) {
    if (status === 403 || status === 429) return true;
    if (json === null) return true;
    return false;
  }

  // ---- Goi local server: KHAC origin (localhost khac affiliate.shopee.*) nen bat buoc
  // GM_xmlhttpRequest de ne CORS (server khong bat CORS - xem affiliate_scrape_server.py). ----
  function serverRequest(method, path, body) {
    const serverUrl = GM_getValue(SERVER_URL_KEY, SERVER_URL_DEFAULT);
    return new Promise((resolve, reject) => {
      GM_xmlhttpRequest({
        method,
        url: serverUrl + path,
        headers: { 'Content-Type': 'application/json' },
        data: body ? JSON.stringify(body) : undefined,
        onload: (resp) => {
          try {
            resolve(JSON.parse(resp.responseText));
          } catch (e) {
            reject(new Error('Server tra ve khong phai JSON: ' + resp.responseText.slice(0, 200)));
          }
        },
        onerror: () => reject(new Error('Khong ket noi duoc local server (' + serverUrl + ') - server co dang chay khong?')),
      });
    });
  }

  // ---- Kiem tra/mo trang cap nhat script - xem shopee_collector.user.js de biet ly do
  // khong the tu cap nhat qua GM_xmlhttpRequest thong thuong (phai qua man hinh
  // Cai dat/Cap nhat cua chinh Tampermonkey). ----
  function compareVersions(a, b) {
    const pa = String(a).replace(/^v/i, '').split('.').map((n) => parseInt(n, 10) || 0);
    const pb = String(b).replace(/^v/i, '').split('.').map((n) => parseInt(n, 10) || 0);
    const len = Math.max(pa.length, pb.length);
    for (let i = 0; i < len; i++) {
      const diff = (pa[i] || 0) - (pb[i] || 0);
      if (diff !== 0) return diff;
    }
    return 0;
  }

  function getLocalVersion() {
    if (typeof GM_info !== 'undefined' && GM_info.script && GM_info.script.version) {
      return GM_info.script.version;
    }
    return SCRIPT_VERSION;
  }

  function openUpdatePage() {
    const serverUrl = GM_getValue(SERVER_URL_KEY, SERVER_URL_DEFAULT);
    window.open(serverUrl + '/userscripts/' + OWN_SCRIPT_FILE, '_blank');
  }

  function showUpdateAvailable(remoteVersion) {
    const badge = document.getElementById('aog-update-badge');
    if (!badge) return;
    badge.textContent = `🆕 Có bản mới v${remoteVersion} - Bấm để cập nhật`;
    badge.style.display = 'inline-block';
  }

  function hideUpdateAvailable() {
    const badge = document.getElementById('aog-update-badge');
    if (badge) badge.style.display = 'none';
  }

  async function checkForUpdate(manual) {
    try {
      const json = await serverRequest('GET', '/api/userscripts');
      const entry = (json.userscripts || []).find((u) => u.file === OWN_SCRIPT_FILE);
      const remoteVersion = entry && entry.version;
      if (!remoteVersion) {
        if (manual) alert('Không tìm thấy thông tin phiên bản trên server.');
        return;
      }
      const localVersion = getLocalVersion();
      if (compareVersions(remoteVersion, localVersion) > 0) {
        showUpdateAvailable(remoteVersion);
        if (manual && confirm(`Có bản mới v${remoteVersion} (đang dùng v${localVersion}). Mở trang cập nhật ngay?`)) {
          openUpdatePage();
        }
      } else {
        hideUpdateAvailable();
        if (manual) alert(`Đang dùng bản mới nhất (v${localVersion}).`);
      }
    } catch (e) {
      if (manual) alert('Không kiểm tra được cập nhật: ' + e.message);
    }
  }

  function maybeAutoCheckForUpdate() {
    const last = parseInt(GM_getValue(LAST_UPDATE_CHECK_KEY, '0'), 10);
    if (Date.now() - last < UPDATE_CHECK_INTERVAL_MS) return;
    GM_setValue(LAST_UPDATE_CHECK_KEY, String(Date.now()));
    checkForUpdate(false);
  }

  // ---- BFS chinh cho 1 root ----
  async function runBfsForRoot(root, log, soldMin) {
    const groupid = root.itemid;
    let calls = 0;
    let memberCount = 0;

    log(`=== Bat dau root ${groupid} (${root.product_link || ''}) ===`);
    await heartbeat('working', groupid);

    // Buoc 1: xac thuc chinh root
    calls++;
    const rootResp = await callOfferApi(groupid);
    if (looksBlocked(rootResp.status, rootResp.json)) {
      log(`!!! Nghi bi chan/captcha khi goi root (HTTP ${rootResp.status}). Noi dung tra ve: ${rootResp.rawText.slice(0, 300)}`);
      log('Dung lai, tu kiem tra tab nay roi bam Start lai de tiep tuc.');
      await heartbeat('blocked', groupid);
      return { finished: false, reason: 'blocked' };
    }
    if (!rootResp.json || rootResp.json.code !== 0) {
      const errMsg = `code=${rootResp.json && rootResp.json.code} msg=${rootResp.json && rootResp.json.msg}`;
      log(`Root loi: ${errMsg}`);
      // QUAN TRONG: PHAI danh dau 'fail' de nha claim - neu khong root van 'pending' voi
      // assigned_key con nguyen, worker se nhan lai DUNG root loi nay o lan poll ke tiep,
      // lap vo han (da gap bug thuc te: cung 1 root bao loi lien tuc khong dung).
      await serverRequest('POST', `/api/roots/${encodeURIComponent(groupid)}/fail`, {
        reason: errMsg, market: root.market,
      });
      log(`Da danh dau root ${groupid} la 'fail', chuyen sang root khac.`);
      return { finished: true, reason: 'root_error', memberCount };
    }
    const rootData = rootResp.json.data;
    const rootVerify = await serverRequest('POST', '/api/roots/verify', { offer_data: rootData });
    // QUAN TRONG: memberCount CHI phan anh so 'member' THAT tu server (count_group_members()
    // KHONG tinh root) - da tung co bug o day: gan memberCount=1 cho root roi bi verify.group_member_count
    // ghi de mat ngay vong lap dau, khien BFS chay toi du 60 MEMBER + 1 root = 61 tong thay vi
    // 60 tong nhu thiet ke. Tach rieng rootCountsAsOne, LUON cong vao khi so sanh/hien thi.
    const rootCountsAsOne = rootVerify.passes ? 1 : 0;
    if (rootVerify.passes) {
      log(`Root DAT chuan -> tinh 1/${GROUP_TARGET}.`);
    } else {
      log(`Root KHONG dat chuan -> can du ${GROUP_TARGET} tu san pham tuong tu.`);
    }

    // Hang doi uu tien theo sold giam dan (mang phang, sort lai moi lan pop - du nhanh
    // voi quy mo vai tram phan tu, khong can cau truc heap rieng).
    let queue = [];
    const seenLocal = new Set([groupid]); // tranh tu xep hang doi lai chinh no/trung trong 1 lan chay

    async function expandFrom(offerData) {
      const similar = (offerData.similar_product_offers && offerData.similar_product_offers.list) || [];
      if (!similar.length) {
        log('  (similar_product_offers rong - Shopee khong tra ve san pham tuong tu nao cho item nay)');
        return;
      }
      const claimedIds = await serverRequest('POST', '/api/candidates/seed', { groupid, items: similar });
      const claimedSet = new Set(claimedIds.claimed_item_ids || []);
      let added = 0;
      let skippedTaken = 0;
      let skippedSold = 0;
      for (const item of similar) {
        const itemId = item.item_id;
        if (!itemId || seenLocal.has(itemId)) continue;
        if (!claimedSet.has(itemId)) { skippedTaken++; continue; } // da thuoc nhom khac
        const sold = (item.batch_item_for_item_card_full || {}).sold || 0;
        if (sold <= soldMin) { skippedSold++; continue; } // loc mien phi truoc (khop dieu kien loc dang cau hinh o server), khoi ton request that
        seenLocal.add(itemId);
        queue.push({ item_id: itemId, sold });
        added++;
      }
      log(`  Nhan ${similar.length} ung vien: +${added} vao hang doi, ${skippedTaken} da thuoc nhom khac, ${skippedSold} bi loai vi sold<=${soldMin}.`);
    }

    await expandFrom(rootData);

    while (memberCount + rootCountsAsOne < GROUP_TARGET && queue.length > 0 && calls < CALL_CAP_PER_ROOT) {
      if (isStopped()) {
        log('Da dung theo yeu cau nguoi dung.');
        return { finished: false, reason: 'stopped', memberCount };
      }
      queue.sort((a, b) => b.sold - a.sold);
      const next = queue.shift();
      await heartbeat('working', groupid);

      calls++;
      const resp = await callOfferApi(next.item_id);
      if (looksBlocked(resp.status, resp.json)) {
        log(`!!! Nghi bi chan/captcha luc xu ly ${next.item_id} (HTTP ${resp.status}, request thu ${calls}). Noi dung tra ve: ${resp.rawText.slice(0, 300)}`);
        log('Dung lai, tu kiem tra tab nay roi bam Start lai de tiep tuc.');
        queue.unshift(next); // giu lai, khong mat ung vien nay khi resume
        await heartbeat('blocked', groupid);
        return { finished: false, reason: 'blocked', memberCount };
      }
      if (!resp.json || resp.json.code !== 0) {
        log(`  ${next.item_id}: loi API, bo qua.`);
        continue;
      }
      const data = resp.json.data;
      const verify = await serverRequest('POST', '/api/items/verify', { groupid, offer_data: data });
      if (verify.outcome === 'assigned' || verify.outcome === 'already_member') {
        memberCount = verify.group_member_count;
        log(`  [${calls}/${CALL_CAP_PER_ROOT}] ${next.item_id}: DAT -> ${memberCount + rootCountsAsOne}/${GROUP_TARGET}`);
      } else {
        log(`  [${calls}/${CALL_CAP_PER_ROOT}] ${next.item_id}: ${verify.outcome}`);
      }

      await expandFrom(data);
      await randomDelay(CALL_DELAY_MIN_MS, CALL_DELAY_MAX_MS);
    }

    if (memberCount + rootCountsAsOne >= GROUP_TARGET) {
      await serverRequest('POST', '/api/roots/finish', { itemid: groupid, market: root.market });
      log(`=== XONG root ${groupid}: ${memberCount + rootCountsAsOne}/${GROUP_TARGET}. ===`);
      return { finished: true, reason: 'target_reached', memberCount };
    }
    if (calls >= CALL_CAP_PER_ROOT) {
      log(`=== DUNG root ${groupid}: cham tran ${CALL_CAP_PER_ROOT} request, chi duoc ${memberCount + rootCountsAsOne}/${GROUP_TARGET}. Coi la khong du, bo do. ===`);
    } else {
      log(`=== DUNG root ${groupid}: het ung vien truoc khi du, chi duoc ${memberCount + rootCountsAsOne}/${GROUP_TARGET}. Coi la khong du, bo do. ===`);
    }
    await serverRequest('POST', '/api/roots/finish', { itemid: groupid, market: root.market });
    return { finished: true, reason: 'insufficient', memberCount };
  }

  // Chan bam Start nhieu lan tao ra nhieu runLoop() chay chong len nhau (da gap thuc te -
  // cung 1 root bi xu ly 2 lan song song, ton gap doi request that toi Shopee vo ich).
  let loopRunning = false;

  // ---- Vong lap ngoai: CHO duoc giao viec tu dashboard (khong tu claim nua) - poll
  // /api/workers/<device_key>/assigned_root dinh ky luc ranh, bao heartbeat de dashboard
  // biet tab nay dang online/ranh/lam viec/bi chan. ----
  async function runLoop(log) {
    if (loopRunning) {
      log('Da co 1 vong lap dang chay trong tab nay roi - bo qua lan bam Start nay (bam Stop truoc neu muon dung).');
      return;
    }
    loopRunning = true;
    try {
      await runLoopInner(log);
    } finally {
      loopRunning = false;
    }
  }

  async function runLoopInner(log) {
    clearStop();
    const deviceKey = GM_getValue(DEVICE_KEY_KEY, '').trim();
    if (!deviceKey) {
      log('Chua nhap ten tai khoan/profile (o o "Device key") - nhap roi bam Start lai.');
      return;
    }
    if (!currentMarket) {
      log(`Khong nhan dien duoc market cho hostname "${location.hostname}" - kiem tra lai danh sach MARKET_BY_AFFILIATE_HOST trong script.`);
      return;
    }
    activeDeviceKey = deviceKey;
    let settings;
    try {
      settings = await serverRequest('GET', '/api/settings');
      log(`Dieu kien loc dang ap dung: aff7d<${settings.promoted_7d_max}, sold>${settings.sold_min}, seller_commission_vnd>${settings.seller_commission_vnd_min}`);
    } catch (e) {
      log('Khong lay duoc dieu kien loc tu server: ' + e.message);
      return;
    }
    log(`Da vao trang thai cho viec (device_key="${deviceKey}") - giao root cho tai khoan nay tren dashboard.`);
    await heartbeat('idle');
    while (!isStopped()) {
      let assignedResp;
      try {
        assignedResp = await serverRequest(
          'GET',
          `/api/workers/${encodeURIComponent(deviceKey)}/assigned_root?market=${encodeURIComponent(currentMarket)}`
        );
      } catch (e) {
        log('Loi hoi viec tu server: ' + e.message + ' - thu lai sau ' + POLL_ASSIGNMENT_MS + 'ms.');
        await sleep(POLL_ASSIGNMENT_MS);
        continue;
      }
      if (!assignedResp.root) {
        await heartbeat('idle');
        await sleep(POLL_ASSIGNMENT_MS);
        continue;
      }
      const result = await runBfsForRoot(assignedResp.root, log, settings.sold_min);
      if (result.reason === 'blocked') break;
      await heartbeat('idle');
    }
    log('=== Vong lap ket thuc (bam Start lai de tiep tuc cho viec). ===');
  }

  // ---- Panel UI ----
  const PANEL_ID = 'aog-scraper-panel';
  function log(msg) {
    console.log('[offer-group-scraper] ' + msg);
    const area = document.getElementById('aog-log');
    if (area) {
      area.value += msg + '\n';
      area.scrollTop = area.scrollHeight;
    }
  }

  function injectPanel() {
    if (document.getElementById(PANEL_ID) || !document.body) return;

    const panel = document.createElement('div');
    panel.id = PANEL_ID;
    panel.style.cssText = [
      'position:fixed', 'top:70px', 'right:16px', 'z-index:2147483647',
      'background:#fff', 'border:1px solid #ee4d2d', 'border-radius:8px',
      'padding:10px 12px', 'font:12px/1.5 system-ui,-apple-system,sans-serif',
      'color:#222', 'box-shadow:0 2px 10px rgba(0,0,0,.25)', 'width:420px',
    ].join(';');

    panel.innerHTML = `
      <div style="font-weight:700;color:#ee4d2d;margin-bottom:6px;">Affiliate Offer Group Scraper <span style="font-weight:400;color:#888;font-size:11px;">v${SCRIPT_VERSION}</span></div>
      <div style="font-size:11px;color:#666;margin-bottom:6px;">Market: <b>${currentMarket || 'KHONG NHAN DIEN DUOC'}</b></div>
      <div style="display:flex;align-items:center;gap:6px;margin-bottom:6px;flex-wrap:wrap;">
        <button id="aog-check-update-btn" style="padding:4px 8px;font-size:10px;font-weight:600;cursor:pointer;background:#f2f2f2;color:#222;border:1px solid #ccc;border-radius:4px;">🔄 Kiểm tra cập nhật</button>
        <span id="aog-update-badge" style="display:none;font-size:10px;font-weight:700;color:#d8431f;background:#fff5f2;border:1px solid #ee4d2d;padding:4px 8px;border-radius:4px;cursor:pointer;"></span>
      </div>
      <div style="display:flex;gap:6px;margin-bottom:4px;">
        <input id="aog-server" type="text" placeholder="Local server URL"
          style="flex:1;padding:4px 6px;border:1px solid #ccc;border-radius:4px;">
      </div>
      <div style="display:flex;gap:6px;margin-bottom:6px;">
        <input id="aog-device" type="text" placeholder="Device key (ten tai khoan/profile)"
          style="flex:1;padding:4px 6px;border:1px solid #ccc;border-radius:4px;">
      </div>
      <div style="display:flex;gap:6px;margin-bottom:6px;">
        <button id="aog-start-btn" style="flex:1;padding:5px 0;cursor:pointer;background:#ee4d2d;color:#fff;border:none;border-radius:4px;">Start</button>
        <button id="aog-stop-btn" style="flex:1;padding:5px 0;cursor:pointer;background:#f2f2f2;color:#222;border:1px solid #ccc;border-radius:4px;">Stop</button>
      </div>
      <textarea id="aog-log" readonly style="width:100%;height:220px;box-sizing:border-box;font:11px/1.4 monospace;border:1px solid #ccc;border-radius:4px;padding:4px;"></textarea>
    `;
    document.body.appendChild(panel);

    const serverInput = document.getElementById('aog-server');
    const deviceInput = document.getElementById('aog-device');
    serverInput.value = GM_getValue(SERVER_URL_KEY, SERVER_URL_DEFAULT);
    deviceInput.value = GM_getValue(DEVICE_KEY_KEY, '');
    serverInput.addEventListener('change', () => GM_setValue(SERVER_URL_KEY, serverInput.value.trim()));
    deviceInput.addEventListener('change', () => GM_setValue(DEVICE_KEY_KEY, deviceInput.value.trim()));

    document.getElementById('aog-start-btn').addEventListener('click', () => runLoop(log));
    document.getElementById('aog-stop-btn').addEventListener('click', () => {
      requestStop();
      log('(Da bam Stop - se dung sau khi xu ly xong item hien tai.)');
    });
    document.getElementById('aog-check-update-btn').addEventListener('click', () => checkForUpdate(true));
    document.getElementById('aog-update-badge').addEventListener('click', openUpdatePage);
    maybeAutoCheckForUpdate();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', injectPanel);
  } else {
    injectPanel();
  }

  unsafeWindow.__offerGroupScraper = { runLoop: () => runLoop(log), stop: requestStop };
})();
