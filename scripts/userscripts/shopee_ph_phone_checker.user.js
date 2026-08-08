// ==UserScript==
// @name         Shopee PH Phone Checker (SMSPool + 5sim + dongvanfb Mail)
// @namespace    shopee-crawl
// @version      1.7
// @description  1 script, 2 vai tro theo domain dang mo. Tren bat ky trang nao cua shopee.ph: lay so PH tu SMSPool/5sim qua API key hoac mua mail Hotmail/Outlook TRUSTED (dongvanfb), kiem tra check_phone_exist, tu huy so da ton tai; tu dong lang nghe + check ho neu co tab 5sim.net dang mo (khong can bam gi). Tren 5sim.net: mua/huy so bang chinh session trinh duyet (khong can API key, ne rate limit rieng), gui thang sang tab shopee.ph qua GM_addValueChangeListener (cung 1 script, KHONG qua server trung gian) roi tu quyet dinh huy/giu.
// @match        https://shopee.ph/*
// @match        https://5sim.net/*
// @updateURL    http://127.0.0.1:8877/userscripts/shopee_ph_phone_checker.user.js
// @downloadURL  http://127.0.0.1:8877/userscripts/shopee_ph_phone_checker.user.js
// @grant        GM_xmlhttpRequest
// @grant        GM_setValue
// @grant        GM_getValue
// @grant        GM_addValueChangeListener
// @grant        GM_removeValueChangeListener
// @grant        unsafeWindow
// @connect      api.smspool.net
// @connect      5sim.net
// @connect      api.dongvanfb.net
// @connect      tools.dongvanfb.net
// @run-at       document-idle
// ==/UserScript==

(function () {
  'use strict';

  const SCRIPT_VERSION = '1.7'; // khop @version o header - doi ca 2 cho khi sua script
  const NS = 'phchk';

  // 1 script, 2 vai tro theo domain dang mo - xem cuoi file (bootstrap).
  const IS_5SIM_HOST = /(^|\.)5sim\.net$/.test(location.hostname);
  const IS_SHOPEE_HOST = /(^|\.)shopee\.ph$/.test(location.hostname);

  const SHOPEE_CHECK_ENDPOINT = '/api/v4/account/basic/check_phone_exist';
  const BALANCE_POLL_MS = 30000; // "real-time" o day = tu lam moi dinh ky, khong co push/socket

  // Gia tri da xac nhan trong check_phone_exist/shopeePH.txt - KHONG doan, dung dung so nay.
  const SMSPOOL_SERVICE_ID = 823; // Shopee
  const SMSPOOL_COUNTRY_ID = 12; // Philippines
  const SMSPOOL_POOLS = [
    { id: '', name: 'Auto (SMSPool tu chon)' },
    { id: '3', name: 'Charlie' },
    { id: '7', name: 'Foxtrot' },
    { id: '12', name: 'Mike' },
    { id: '16', name: 'Romeo' },
    { id: '17', name: 'Sierra' },
    { id: '19', name: 'Tango' },
  ];

  // 5sim: country/product da xac nhan qua test that (2026-08-06) bang chinh key trong
  // check_phone_exist/api_key_5sim.txt - "philippines"/"shopee" ton tai, con hang lon.
  const FIVESIM_COUNTRY = 'philippines';
  const FIVESIM_PRODUCT = 'shopee';

  // OUT_OF_STOCK/BALANCE_ERROR/PRICE_NOT_FOUND se LAP LAI moi lan neu khong dung batch -
  // da tung gap dung loai bug nay (lap vo han khi loi that su) o script khac trong du an,
  // bat buoc phai dung han thay vi bo qua roi thu tiep. Dung CHUNG 1 bo nhan dien loi cho
  // ca 2 nha cung cap - moi provider tu quy doi loi cua minh ve dung cac ten nay (xem
  // smspoolOrder()/fivesimOrder()) de phan logic runBatch/runUntilAvailable dung chung.
  const STOP_BATCH_ORDER_ERRORS = ['OUT_OF_STOCK', 'BALANCE_ERROR', 'PRICE_NOT_FOUND'];

  const K = {
    provider: `${NS}_provider`,
    apikeySmspool: `${NS}_apikey_smspool`,
    apikeyFivesim: `${NS}_apikey_5sim`,
    pool: `${NS}_pool`,
    operatorFivesim: `${NS}_operator_5sim`,
    quantity: `${NS}_quantity`,
    delayMin: `${NS}_delay_min`,
    delayMax: `${NS}_delay_max`,
    autoCancel: `${NS}_auto_cancel`,
    results: `${NS}_results`, // luu lich su qua reload trang
    apikeyDongvanfb: `${NS}_apikey_dongvanfb`,
    mailAccountType: `${NS}_mail_account_type`,
    mailQuantity: `${NS}_mail_quantity`,
    mailResults: `${NS}_mail_results`, // lich su mail da mua, doc lap voi 'results' (SDT)
    // Vai tro "mua tren 5sim.net" (session, khong qua API key) - K rieng, doc lap voi cac
    // key o tren (nhung KHONG can dung neu chi dung 1 vai tro tren 1 tab, do la binh thuong).
    buyerOperator: `${NS}_buyer_operator`,
    buyerQuantity: `${NS}_buyer_quantity`,
    buyerDelayMin: `${NS}_buyer_delay_min`,
    buyerDelayMax: `${NS}_buyer_delay_max`,
    buyerAutoCancel: `${NS}_buyer_auto_cancel`,
    buyerResults: `${NS}_buyer_results`,
  };

  let running = false;
  let stopRequested = false;

  function sleep(ms) { return new Promise((r) => setTimeout(r, ms)); }
  function randomDelay(minSec, maxSec) {
    const ms = (minSec + Math.random() * Math.max(0, maxSec - minSec)) * 1000;
    return sleep(ms);
  }
  function num(v) { const n = Number(v); return isNaN(n) ? 0 : n; }

  function getCookieVal(name) {
    const m = document.cookie.match(new RegExp('(?:^|; )' + name + '=([^;]*)'));
    return m ? decodeURIComponent(m[1]) : '';
  }

  // Dang nhap Shopee? check_phone_exist can phien dang nhap that (cookie SPC_EC/SPC_U) -
  // khong dang nhap se luon loi/bi chan.
  function isLoggedIn() {
    if (getCookieVal('SPC_EC')) return true;
    const u = getCookieVal('SPC_U');
    return !!(u && u !== '-1');
  }

  function currentProviderId() {
    const id = GM_getValue(K.provider, 'smspool');
    return PROVIDERS[id] ? id : 'smspool';
  }
  function apiKeyFor(providerId) {
    const key = providerId === 'fivesim' ? K.apikeyFivesim : K.apikeySmspool;
    return (GM_getValue(key, '') || '').trim();
  }

  // ============================================================
  // NHA CUNG CAP 1: SMSPool - POST form-urlencoded, ket qua LUON JSON, xac thuc = field
  // 'key' trong body (KHONG phai header). KHAC origin voi trang -> bat buoc GM_xmlhttpRequest.
  // ============================================================
  const SMSPOOL_BASE = 'https://api.smspool.net';

  function smsPoolRequest(url, data) {
    return new Promise((resolve, reject) => {
      const params = new URLSearchParams(data).toString();
      GM_xmlhttpRequest({
        method: 'POST',
        url,
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        data: params,
        onload: (resp) => {
          let json;
          try { json = JSON.parse(resp.responseText); }
          catch (e) { reject(new Error('SMSPool tra ve khong phai JSON: ' + resp.responseText.slice(0, 200))); return; }
          resolve({ status: resp.status, json });
        },
        onerror: () => reject(new Error('Khong ket noi duoc SMSPool (kiem tra mang/API key).')),
        ontimeout: () => reject(new Error('SMSPool timeout.')),
      });
    });
  }

  async function smspoolOrder(apiKey, poolId) {
    const data = { key: apiKey, country: SMSPOOL_COUNTRY_ID, service: SMSPOOL_SERVICE_ID };
    if (poolId) data.pool = poolId;
    const resp = await smsPoolRequest(SMSPOOL_BASE + '/purchase/sms', data);
    const o = resp.json;
    if (o && o.success === 1) {
      return { success: true, phone: String(o.number), orderId: String(o.order_id), cost: num(o.cost), label: `pool ${o.pool}` };
    }
    return { success: false, errorType: o && o.type, rawMessage: (o && (o.type || o.message)) || 'khong ro loi' };
  }

  async function smspoolCancel(apiKey, orderId) {
    const resp = await smsPoolRequest(SMSPOOL_BASE + '/sms/cancel', { key: apiKey, orderid: orderId });
    const j = resp.json;
    if (j && j.success === 1) return { ok: true, locked: false, message: 'Da huy.' };
    const msg = (j && j.message) || '';
    return { ok: false, locked: /cannot be cancelled yet/i.test(msg), message: msg || 'khong ro loi' };
  }

  async function smspoolBalance(apiKey) {
    const resp = await smsPoolRequest(SMSPOOL_BASE + '/request/balance', { key: apiKey });
    return resp.json && resp.json.balance;
  }

  async function smspoolListActive(apiKey) {
    const resp = await smsPoolRequest(SMSPOOL_BASE + '/request/active', { key: apiKey });
    const list = Array.isArray(resp.json) ? resp.json : [];
    return list.map((o) => ({ orderId: o.order_code, phone: o.phonenumber, status: o.status, timeLeftSec: num(o.time_left) }));
  }

  // ============================================================
  // NHA CUNG CAP 2: 5sim - GET + header 'Authorization: Bearer <token>'. QUAN TRONG (khac
  // han SMSPool, da xac nhan qua test that 2026-08-06): loi tra ve dang TEXT THUAN (vd "not
  // enough user balance", "no free phones", "order not found"), KHONG PHAI JSON - phai tu
  // doc rawText khi JSON.parse that bai. So dien thoai tra ve co dau '+' o dau (vd
  // "+639703059918") - phai bo di truoc khi gui cho check_phone_exist cua Shopee. Huy
  // (cancel) THANH CONG NGAY LAP TUC, KHONG co khoa thoi gian dau nhu SMSPool (da test that:
  // mua roi huy ngay, hoan tien du 100%) - "locked" gan nhu khong bao gio xay ra voi 5sim.
  // ============================================================
  const FIVESIM_BASE = 'https://5sim.net/v1';

  function fiveSimRequest(path, apiKey) {
    return new Promise((resolve, reject) => {
      GM_xmlhttpRequest({
        method: 'GET',
        url: FIVESIM_BASE + path,
        headers: { Authorization: `Bearer ${apiKey}`, Accept: 'application/json' },
        onload: (resp) => {
          let json = null;
          try { json = JSON.parse(resp.responseText); } catch (e) { /* loi 5sim thuong la text thuan, khong phai bug */ }
          resolve({ status: resp.status, json, rawText: resp.responseText });
        },
        onerror: () => reject(new Error('Khong ket noi duoc 5sim (kiem tra mang/API key).')),
        ontimeout: () => reject(new Error('5sim timeout.')),
      });
    });
  }

  function fivesimErrorType(rawMessage) {
    if (/not enough user balance/i.test(rawMessage)) return 'BALANCE_ERROR';
    if (/no free phones/i.test(rawMessage)) return 'OUT_OF_STOCK';
    return null;
  }

  async function fivesimOrder(apiKey, operator) {
    const op = operator || 'any';
    const resp = await fiveSimRequest(`/user/buy/activation/${FIVESIM_COUNTRY}/${op}/${FIVESIM_PRODUCT}`, apiKey);
    if (resp.status === 200 && resp.json && resp.json.id) {
      const phone = String(resp.json.phone || '').replace(/^\+/, '');
      return { success: true, phone, orderId: String(resp.json.id), cost: num(resp.json.price), label: resp.json.operator };
    }
    const raw = resp.rawText || (resp.json ? JSON.stringify(resp.json) : '') || '';
    return { success: false, errorType: fivesimErrorType(raw), rawMessage: raw.slice(0, 200) || 'khong ro loi' };
  }

  async function fivesimCancel(apiKey, orderId) {
    const resp = await fiveSimRequest(`/user/cancel/${orderId}`, apiKey);
    if (resp.status === 200 && resp.json && resp.json.status === 'CANCELED') {
      return { ok: true, locked: false, message: 'Da huy.' };
    }
    const raw = resp.rawText || (resp.json ? JSON.stringify(resp.json) : '') || '';
    return { ok: false, locked: false, message: raw.slice(0, 200) || 'khong ro loi' };
  }

  async function fivesimBalance(apiKey) {
    const resp = await fiveSimRequest('/user/profile', apiKey);
    return resp.json && resp.json.balance;
  }

  // Danh sach operator + gia/ton kho/ty le nhan SMS THAT cho philippines/shopee - endpoint
  // public (khong can API key). Da test that 2026-08-07: virtual21/virtual34/virtual4/
  // virtual51/virtual58 - gia va ton kho co the doi theo thoi gian nen lay dong, KHONG
  // hardcode con so.
  function fetchFivesimOperators() {
    return new Promise((resolve) => {
      GM_xmlhttpRequest({
        method: 'GET',
        url: `${FIVESIM_BASE}/guest/prices?country=${FIVESIM_COUNTRY}&product=${FIVESIM_PRODUCT}`,
        headers: { Accept: 'application/json' },
        onload: (resp) => {
          try {
            const json = JSON.parse(resp.responseText);
            const ops = (json[FIVESIM_COUNTRY] && json[FIVESIM_COUNTRY][FIVESIM_PRODUCT]) || {};
            resolve(Object.keys(ops).map((name) => ({ name, ...ops[name] })));
          } catch (e) { resolve([]); }
        },
        onerror: () => resolve([]),
        ontimeout: () => resolve([]),
      });
    });
  }

  async function fivesimListActive(apiKey) {
    const resp = await fiveSimRequest('/user/orders?category=activation&limit=50&order=id&reverse=true', apiKey);
    const list = (resp.json && resp.json.Data) || [];
    const ACTIVE_STATUSES = new Set(['PENDING', 'RECEIVED']);
    return list.filter((o) => ACTIVE_STATUSES.has(o.status)).map((o) => ({
      orderId: String(o.id),
      phone: o.phone,
      status: o.status,
      timeLeftSec: Math.max(0, Math.round((new Date(o.expires).getTime() - Date.now()) / 1000)),
    }));
  }

  // ---- Dispatch chung: phan con lai cua script chi goi qua PROVIDERS[id], khong biet
  // (va khong can biet) dang dung SMSPool hay 5sim. ----
  const PROVIDERS = {
    smspool: { name: 'SMSPool', order: smspoolOrder, cancel: smspoolCancel, balance: smspoolBalance, listActive: smspoolListActive },
    fivesim: { name: '5sim', order: fivesimOrder, cancel: fivesimCancel, balance: fivesimBalance, listActive: fivesimListActive },
  };

  // ============================================================
  // VAI TRO "MUA TREN 5sim.net" (chi chay khi IS_5SIM_HOST - xem bootstrap cuoi file).
  // Goi 5sim bang unsafeWindow.fetch + cookie SESSION cua trinh duyet (dang nhap web binh
  // thuong, KHONG can API key) thay vi Authorization: Bearer <api_key> qua GM_xmlhttpRequest
  // - ne rate limit rieng cua API doi tac, da xac nhan qua request that bat duoc tu trang
  // web: GET /v1/user/buy|cancel/... + header 'x-xsrf-token' = gia tri cookie XSRF-TOKEN
  // (khac API doi tac dung header Authorization). Check tren Shopee van phai qua tab
  // shopee.ph (khac domain, khong the unsafeWindow.fetch cheo domain) - gui yeu cau qua
  // RELAY o tren (requestPhoneCheckViaRelay/waitForRelayResult), KHONG qua server nao.
  // ============================================================
  async function fiveSimSiteRequest(path) {
    const xsrf = getCookieVal('XSRF-TOKEN');
    const resp = await unsafeWindow.fetch('https://5sim.net' + path, {
      method: 'GET',
      credentials: 'include',
      headers: {
        accept: 'application/json',
        ...(xsrf ? { 'x-xsrf-token': xsrf } : {}),
      },
    });
    const rawText = await resp.text();
    let json = null;
    try { json = JSON.parse(rawText); } catch (e) { /* loi co the la text thuan */ }
    return { status: resp.status, json, rawText };
  }

  function buySiteNumber(operator) {
    return fiveSimSiteRequest(`/v1/user/buy/activation/${FIVESIM_COUNTRY}/${operator || 'any'}/${FIVESIM_PRODUCT}`);
  }
  function cancelSiteOrder(orderId) {
    return fiveSimSiteRequest(`/v1/user/cancel/${orderId}`);
  }
  function siteBalance() {
    return fiveSimSiteRequest('/v1/user/profile');
  }
  function siteActiveOrders() {
    return fiveSimSiteRequest('/v1/user/orders?category=activation&limit=50&order=id&reverse=true');
  }
  function siteOperatorPrices() {
    return fiveSimSiteRequest(`/v1/guest/prices?country=${FIVESIM_COUNTRY}&product=${FIVESIM_PRODUCT}`);
  }
  // Check 1 don hang cu the - da xac nhan qua request that (5sim.txt/check.txt): tra ve
  // {status, sms:[{code,text,sender,date}], ...}. Dung cho nut "Lay ma" tung dong.
  function checkSiteOrder(orderId) {
    return fiveSimSiteRequest(`/v1/user/check/${orderId}`);
  }
  // Danh sach TOAN BO don hang (khong loc active nhu siteActiveOrders) - da xac nhan qua
  // request that (5sim.txt/order.txt): don da nhan SMS co status='FINISHED' va
  // sms=[{code:"289902", text:"SHOPEE: Use OTP code 289902..."}]. Dung cho nut "Lam moi
  // hang loat" - 1 lan goi cap nhat duoc CA LOAT dong dang 'pending' thay vi goi rieng
  // tung don (uu tien hang loat theo dung phan hoi cua nguoi dung tu truoc).
  function siteAllOrders() {
    return fiveSimSiteRequest('/v1/user/orders?category=activation&limit=100&order=id&reverse=true');
  }
  // sms[].code co the rong (hiem) - text luon co, dung lam phuong an du phong.
  function extractSiteSmsCode(smsArr) {
    if (!smsArr || !smsArr.length) return null;
    const last = smsArr[smsArr.length - 1];
    return last.code || last.text || null;
  }

  function loadBuyerResults() { return GM_getValue(K.buyerResults, []); }
  function saveBuyerResults(arr) { GM_setValue(K.buyerResults, arr); }
  function upsertBuyerResult(row) {
    const arr = loadBuyerResults();
    const idx = arr.findIndex((r) => r.orderId === row.orderId);
    if (idx >= 0) arr[idx] = row; else arr.unshift(row);
    if (arr.length > 500) arr.length = 500;
    saveBuyerResults(arr);
    renderBuyerResults();
  }

  async function refreshBuyerBalance() {
    const el = document.getElementById(`${NS}-buyer-balance`);
    if (!el) return;
    try {
      const resp = await siteBalance();
      const bal = resp.json && resp.json.balance;
      if (bal == null) { el.textContent = 'loi'; el.style.color = '#e67e22'; return; }
      el.textContent = `$${bal}`;
      el.style.color = num(bal) < 1 ? '#e67e22' : '#4caf50';
    } catch (e) {
      el.textContent = 'loi'; el.style.color = '#e67e22';
    }
  }

  // Mua 1 so -> gui yeu cau check qua RELAY -> CHO ket qua (co tran an toan RELAY_TIMEOUT_MS
  // - phong truong hop chua mo tab shopee.ph nao, tranh giu so thue vo thoi han) -> quyet
  // dinh huy/giu.
  async function buyOneAndCheck(operator, log) {
    log('Dang mua so tren 5sim (session, khong qua API key)...');
    let buyResp;
    try { buyResp = await buySiteNumber(operator); }
    catch (e) { log('Loi ket noi 5sim: ' + e.message); return { stopBatch: false, error: true }; }

    if (buyResp.status !== 200 || !buyResp.json || !buyResp.json.id) {
      const raw = buyResp.rawText || '';
      const errorType = fivesimErrorType(raw);
      log('Mua that bai: ' + raw.slice(0, 200));
      if (STOP_BATCH_ORDER_ERRORS.includes(errorType)) {
        log(`!!! Dung: ${errorType} (${errorType === 'BALANCE_ERROR' ? 'het so du' : 'het hang'}).`);
        return { stopBatch: true, error: true, balanceError: errorType === 'BALANCE_ERROR' };
      }
      return { stopBatch: false, error: true };
    }

    const phone = String(buyResp.json.phone || '').replace(/^\+/, '');
    const orderId = String(buyResp.json.id);
    log(`Da mua so ${phone} (order ${orderId}, gia $${buyResp.json.price}).`);

    // 4 trang thai hien thi cho nguoi dung (theo yeu cau): pending (con dung duoc, cho
    // dang ky+lay ma) / fail (da ton tai tren Shopee, vo dung) / done (da nhan duoc ma
    // OTP) / cancel (nguoi dung tu huy so pending). 'checking'/'timeout'/'error'/
    // 'blocked'/'unknown' la cac trang thai trung gian/loi rieng cua qua trinh check.
    const row = {
      phone, orderId, cost: num(buyResp.json.price),
      time: new Date().toLocaleString('vi-VN'), status: 'checking',
      code: null, username: null, cancelAttempted: false,
    };
    upsertBuyerResult(row);
    refreshBuyerBalance();

    const reqId = requestPhoneCheckViaRelay(phone);
    log('  Da gui yeu cau check sang tab shopee.ph, dang cho...');
    let lastTickLogged = 0;
    const result = await waitForRelayResult(reqId, RELAY_TIMEOUT_MS, (elapsedMs) => {
      // Log tien do moi ~15s de biet dang thuc su cho, khong phai bi treo.
      if (elapsedMs - lastTickLogged >= 15000) {
        lastTickLogged = elapsedMs;
        log(`  ... van dang cho ket qua tu tab shopee.ph (${Math.round(elapsedMs / 1000)}s/${Math.round(RELAY_TIMEOUT_MS / 1000)}s)`);
      }
    });

    if (!result) {
      log('  !!! Khong nhan duoc ket qua - tab shopee.ph co dang mo va co cai script nay khong? Giu nguyen so, tu kiem tra tay.');
      row.status = 'timeout'; upsertBuyerResult(row);
      return { stopBatch: false, error: true, row };
    }

    row.username = result.username || null;

    if (result.result === 'exists') {
      row.status = 'fail';
      upsertBuyerResult(row);
      log(`  -> DA TON TAI tren Shopee (username: ${row.username || '?'}) - danh dau 'fail', dang huy so tren 5sim...`);
      await autoCancelFailRow(row, log);
    } else if (result.result === 'available') {
      row.status = 'pending';
      upsertBuyerResult(row);
      log('  -> CON DUNG DUOC tren Shopee - chuyen "pending", dung nut "Lay ma" sau khi da dang ky Shopee bang so nay.');
    } else {
      row.status = result.result;
      upsertBuyerResult(row);
      log(`  -> Ket qua: ${result.result}${result.detail ? ' (' + result.detail + ')' : ''}`);
    }

    return { stopBatch: false, error: false, row };
  }

  // Huy 1 don tren 5sim vi da xac dinh "fail" (so da ton tai tren Shopee, vo dung).
  // Dung chung cho ca luong tu dong (ngay sau khi phat hien exists) lan luong phuc hoi
  // (cancelPendingBuyerFails, khi het so du). cancelAttempted tranh goi huy lap lai vo ich.
  async function autoCancelFailRow(row, log) {
    if (row.cancelAttempted) return;
    row.cancelAttempted = true;
    try {
      const cancelResp = await cancelSiteOrder(row.orderId);
      if (cancelResp.json && cancelResp.json.status === 'CANCELED') {
        log('  Da huy so tren 5sim.');
        refreshBuyerBalance();
      } else {
        log('  Huy chua thanh cong: ' + (cancelResp.rawText || '').slice(0, 200));
      }
    } catch (e) {
      log('  Loi huy: ' + e.message);
    }
    upsertBuyerResult(row);
  }

  async function cancelPendingBuyerFails(log) {
    const list = loadBuyerResults().filter((r) => r.status === 'fail' && !r.cancelAttempted);
    if (!list.length) return;
    log(`Dang huy ${list.length} so 'fail' chua huy...`);
    for (const row of list) {
      await autoCancelFailRow(row, log);
      await sleep(400);
    }
    refreshBuyerBalance();
  }

  async function runBuyerBatch(log) {
    if (running) { log('Dang chay roi - bam Dung truoc.'); return; }
    const operator = GM_getValue(K.buyerOperator, '');
    const quantity = Math.max(1, num(GM_getValue(K.buyerQuantity, 10)));
    const delayMin = Math.max(0, num(GM_getValue(K.buyerDelayMin, 3)));
    const delayMax = Math.max(delayMin, num(GM_getValue(K.buyerDelayMax, 6)));
    const autoCancel = !!GM_getValue(K.buyerAutoCancel, true);

    running = true; stopRequested = false;
    setBuyerButtons(true);
    log(`=== Bat dau: muc tieu ${quantity} so (operator=${operator || 'auto'}). ===`);

    let done = 0, fail = 0, pending = 0, errors = 0;
    for (let i = 0; i < quantity; i++) {
      if (stopRequested) { log('Da dung theo yeu cau.'); break; }
      const result = await buyOneAndCheck(operator, log);
      done++;
      if (result.row) {
        if (result.row.status === 'fail') fail++;
        else if (result.row.status === 'pending') pending++;
      }
      if (result.error) errors++;
      updateBuyerStats({ done, fail, pending, errors, target: quantity });

      if (result.balanceError) {
        if (autoCancel) await cancelPendingBuyerFails(log);
        log('Dung batch: het so du.');
        break;
      }
      if (result.stopBatch) { log('=== Dung som (xem ly do o tren). ==='); break; }
      if (i < quantity - 1 && !stopRequested) await randomDelay(delayMin, delayMax);
    }
    log(`=== XONG: ${done}/${quantity} - fail ${fail}, pending ${pending}, loi ${errors}. ===`);
    running = false;
    setBuyerButtons(false);
  }

  async function runBuyerUntilAvailable(log) {
    if (running) { log('Dang chay roi - bam Dung truoc.'); return; }
    const operator = GM_getValue(K.buyerOperator, '');
    const delayMin = Math.max(0, num(GM_getValue(K.buyerDelayMin, 3)));
    const delayMax = Math.max(delayMin, num(GM_getValue(K.buyerDelayMax, 6)));

    running = true; stopRequested = false;
    setBuyerButtons(true);
    log('=== Bat dau che do "toi khi co so dung duoc": chay lien tuc, DUNG HAN khi co 1 so con dung duoc. ===');

    let attempts = 0;
    let waitRounds = 0;
    const MAX_WAIT_ROUNDS = 20;

    while (!stopRequested) {
      attempts++;
      const result = await buyOneAndCheck(operator, log);

      if (result.row && result.row.status === 'pending') {
        log(`=== DA TIM THAY so con dung duoc: ${result.row.phone} - DUNG HAN. ===`);
        break;
      }
      if (result.balanceError) {
        waitRounds++;
        if (waitRounds > MAX_WAIT_ROUNDS) {
          log(`!!! Da thu phuc hoi so du ${MAX_WAIT_ROUNDS} lan van khong du - DUNG.`);
          break;
        }
        log(`Het so du (lan cho ${waitRounds}/${MAX_WAIT_ROUNDS}) - huy cac so 'fail' de hoan tien, doi 15s roi thu lai...`);
        await cancelPendingBuyerFails(log);
        if (stopRequested) break;
        await sleep(15000);
        continue;
      }
      if (result.stopBatch) { log('=== Dung han (loi khong tu phuc hoi duoc). ==='); break; }

      waitRounds = 0;
      if (!stopRequested) await randomDelay(delayMin, delayMax);
    }
    log(`=== KET THUC: da thu ${attempts} lan. ===`);
    running = false;
    setBuyerButtons(false);
  }

  function exportBuyerAvailable() {
    const arr = loadBuyerResults().filter((r) => r.status === 'pending');
    if (!arr.length) { buyerLog('Chua co so nao "con dung duoc" de xuat.'); return; }
    const text = arr.map((r) => r.phone).join('\n');
    const blob = new Blob([text], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = `5sim_available_${Date.now()}.txt`;
    a.click();
    URL.revokeObjectURL(url);
  }

  async function refreshBuyerActive(log) {
    const box = document.getElementById(`${NS}-buyer-active-box`);
    box.innerHTML = '<div style="color:#888;">Dang tai...</div>';
    let resp;
    try { resp = await siteActiveOrders(); }
    catch (e) { box.innerHTML = `<div style="color:#e67e22;">Loi: ${e.message}</div>`; return; }
    const list = (resp.json && resp.json.Data) || [];
    const active = list.filter((o) => o.status === 'PENDING' || o.status === 'RECEIVED');
    if (!active.length) { box.innerHTML = '<div style="color:#888;">Khong co so nao dang thue.</div>'; return; }
    box.innerHTML = active.map((o) => {
      const left = Math.max(0, Math.round((new Date(o.expires).getTime() - Date.now()) / 60000));
      return `
      <div style="display:flex;justify-content:space-between;gap:6px;padding:3px 0;border-bottom:1px solid #333;">
        <span>${o.phone} (${o.status}, con ${left} phut)</span>
        <button data-order="${o.id}" class="${NS}-buyer-cancel-active-btn" style="font-size:10px;padding:2px 6px;background:#ee4d2d;color:#fff;border:none;border-radius:3px;cursor:pointer;">Huy</button>
      </div>`;
    }).join('');
    box.querySelectorAll(`.${NS}-buyer-cancel-active-btn`).forEach((btn) => {
      btn.addEventListener('click', async () => {
        const orderId = btn.getAttribute('data-order');
        btn.disabled = true; btn.textContent = '...';
        try { await cancelSiteOrder(orderId); } catch (e) { /* bo qua */ }
        refreshBuyerActive(log);
        refreshBuyerBalance();
      });
    });
  }

  function buyerStatusBadge(status) {
    const map = {
      pending: ['#2980b9', 'Pending'],
      fail: ['#c0392b', 'Fail'],
      done: ['#27ae60', 'Done'],
      cancel: ['#888', 'Cancel'],
      checking: ['#888', 'Dang cho check...'],
      timeout: ['#e67e22', 'Het gio cho'],
      error: ['#e67e22', 'Loi'],
      blocked: ['#c0392b', 'Bi chan'],
      unknown: ['#e67e22', 'Khong xac dinh'],
    };
    const pair = map[status] || ['#888', status];
    return `<span style="color:${pair[0]};font-weight:600;">${pair[1]}</span>`;
  }

  // Lay ma OTP cho 1 don dang 'pending' - vong lap 60s, moi lan cach 5s (theo yeu cau):
  // goi /v1/user/check/{id} ngay lap tuc, neu chua co ma thi doi 5s roi goi lai, DUNG
  // han khi (a) nhan duoc ma -> 'done', hoac (b) het 60s van chua co -> giu nguyen
  // 'pending' de nguoi dung tu bam lai sau. Doc lai row.status moi vong lap (khong dung
  // bien cache) de tu dung neu trang thai da doi noi khac (vd bam Huy trong luc dang cho).
  const GETCODE_POLL_INTERVAL_MS = 5000;
  const GETCODE_POLL_TIMEOUT_MS = 60000;

  async function pollBuyerRowCode(orderId, log, onTick) {
    const startTs = Date.now();
    for (;;) {
      const arr = loadBuyerResults();
      const row = arr.find((r) => r.orderId === orderId);
      if (!row || row.status !== 'pending') return false;

      let resp = null;
      try {
        resp = await checkSiteOrder(orderId);
      } catch (e) {
        if (log) log(`  Loi check ma ${row.phone}: ${e.message}`);
      }

      if (resp) {
        const sms = (resp.json && resp.json.sms) || [];
        if (sms.length) {
          row.code = extractSiteSmsCode(sms);
          row.status = 'done';
          saveBuyerResults(arr);
          renderBuyerResults();
          if (log) log(`  Da nhan duoc ma cho ${row.phone}: ${row.code}`);
          return true;
        }
        if (resp.json && (resp.json.status === 'CANCELED' || resp.json.status === 'TIMEOUT')) {
          row.status = 'cancel';
          saveBuyerResults(arr);
          renderBuyerResults();
          return false;
        }
      }

      const elapsed = Date.now() - startTs;
      if (onTick) onTick(elapsed);
      if (elapsed >= GETCODE_POLL_TIMEOUT_MS) return false;
      await sleep(Math.min(GETCODE_POLL_INTERVAL_MS, GETCODE_POLL_TIMEOUT_MS - elapsed));
    }
  }

  async function cancelBuyerRow(orderId, log) {
    const arr = loadBuyerResults();
    const row = arr.find((r) => r.orderId === orderId);
    if (!row) return;
    try {
      const resp = await cancelSiteOrder(orderId);
      if (resp.json && resp.json.status === 'CANCELED') {
        row.status = 'cancel';
        saveBuyerResults(arr);
        renderBuyerResults();
        refreshBuyerBalance();
      } else {
        (log || buyerLog)('Huy chua thanh cong (' + row.phone + '): ' + (resp.rawText || '').slice(0, 200));
      }
    } catch (e) {
      (log || buyerLog)('Loi huy ' + row.phone + ': ' + e.message);
    }
  }

  // Goi ý 4 (lam moi hang loat): 1 lan goi /v1/user/orders lay TOAN BO don, doi chieu voi
  // tung dong 'pending' de cap nhat ma/huy cho CA LOAT thay vi bam tung dong - dung dung
  // dinh dang that trong 5sim.txt/order.txt (status/sms[].code).
  async function refreshAllPendingCodes(log) {
    const arr = loadBuyerResults();
    const pendingRows = arr.filter((r) => r.status === 'pending');
    if (!pendingRows.length) { (log || buyerLog)('Khong co so nao dang pending.'); return; }
    const btn = document.getElementById(`${NS}-buyer-refresh-codes-btn`);
    if (btn) { btn.disabled = true; btn.textContent = 'Dang lam moi...'; }
    try {
      const resp = await siteAllOrders();
      const orders = (resp.json && resp.json.Data) || [];
      const byId = new Map(orders.map((o) => [String(o.id), o]));
      let doneCount = 0, cancelCount = 0;
      for (const row of pendingRows) {
        const o = byId.get(row.orderId);
        if (!o) continue;
        if (o.sms && o.sms.length) {
          row.code = extractSiteSmsCode(o.sms);
          row.status = 'done';
          doneCount++;
        } else if (o.status === 'CANCELED' || o.status === 'TIMEOUT') {
          row.status = 'cancel';
          cancelCount++;
        }
      }
      saveBuyerResults(arr);
      renderBuyerResults();
      if (log) log(`Da lam moi hang loat: ${doneCount} so nhan duoc ma, ${cancelCount} so da huy/het han.`);
    } catch (e) {
      (log || buyerLog)('Loi lam moi hang loat: ' + e.message);
    } finally {
      if (btn) { btn.disabled = false; btn.textContent = 'Lam moi tat ca (lay ma hang loat)'; }
    }
  }

  function renderBuyerResults() {
    const arr = loadBuyerResults();
    const counts = { pending: 0, fail: 0, done: 0, cancel: 0 };
    arr.forEach((r) => { if (counts[r.status] != null) counts[r.status]++; });
    const summaryEl = document.getElementById(`${NS}-buyer-summary`);
    if (summaryEl) summaryEl.textContent = `Tong: ${counts.pending} pending - ${counts.fail} fail - ${counts.done} done - ${counts.cancel} cancel`;

    const tbody = document.getElementById(`${NS}-buyer-tbody`);
    if (!tbody) return;
    // So 'fail' (da ton tai tren Shopee, vo dung, da tu huy tren 5sim) chi con y nghia
    // thong ke (van dem trong dong Tong o tren) - khong hien trong danh sach de tranh
    // ray ray so vo dung, chi giu lai cac so con thao tac duoc/co ket qua that.
    tbody.innerHTML = arr.filter((r) => r.status !== 'fail').slice(0, 100).map((r) => {
      let actionCell;
      if (r.status === 'pending') {
        actionCell = `<button data-order="${r.orderId}" class="${NS}-buyer-getcode-btn" style="font-size:10px;padding:2px 6px;margin-right:3px;background:#ee4d2d;color:#fff;border:none;border-radius:3px;cursor:pointer;">Lay ma</button><button data-order="${r.orderId}" class="${NS}-buyer-cancelrow-btn" style="font-size:10px;padding:2px 6px;background:#ee4d2d;color:#fff;border:none;border-radius:3px;cursor:pointer;">Huy</button>`;
      } else if (r.status === 'done') {
        actionCell = `<b style="color:#27ae60;">${r.code || '?'}</b>`;
      } else {
        actionCell = '-';
      }
      return `
      <tr>
        <td>${r.phone}</td>
        <td>${buyerStatusBadge(r.status)}</td>
        <td>${actionCell}</td>
        <td style="font-size:10px;color:#888;white-space:nowrap;">${r.time}</td>
      </tr>`;
    }).join('') || '<tr><td colspan="4" style="color:#888;padding:8px;">Chua mua so nao.</td></tr>';

    tbody.querySelectorAll(`.${NS}-buyer-getcode-btn`).forEach((btn) => {
      btn.addEventListener('click', async () => {
        const orderId = btn.getAttribute('data-order');
        btn.disabled = true; btn.textContent = 'Dang cho 0s/60s';
        const found = await pollBuyerRowCode(orderId, buyerLog, (elapsedMs) => {
          if (document.body.contains(btn)) btn.textContent = `Dang cho ${Math.round(elapsedMs / 1000)}s/60s`;
        });
        if (document.body.contains(btn)) {
          btn.disabled = false; btn.textContent = 'Lay ma';
          if (!found) {
            const row = loadBuyerResults().find((r) => r.orderId === orderId);
            if (row && row.status === 'pending') buyerLog(`Chua nhan duoc ma sau 60s cho so ${row.phone}. Ban co the bam "Lay ma" lai.`);
          }
        }
      });
    });
    tbody.querySelectorAll(`.${NS}-buyer-cancelrow-btn`).forEach((btn) => {
      btn.addEventListener('click', async () => {
        btn.disabled = true; btn.textContent = '...';
        await cancelBuyerRow(btn.getAttribute('data-order'), buyerLog);
        if (document.body.contains(btn)) { btn.disabled = false; btn.textContent = 'Huy'; }
      });
    });
  }

  function setBuyerButtons(isRunning) {
    const start = document.getElementById(`${NS}-buyer-start-btn`);
    const untilAvail = document.getElementById(`${NS}-buyer-until-available-btn`);
    const stop = document.getElementById(`${NS}-buyer-stop-btn`);
    if (start) { start.disabled = isRunning; start.style.opacity = isRunning ? '0.5' : '1'; }
    if (untilAvail) { untilAvail.disabled = isRunning; untilAvail.style.opacity = isRunning ? '0.5' : '1'; }
    if (stop) stop.disabled = !isRunning;
  }

  function updateBuyerStats(s) {
    const el = document.getElementById(`${NS}-buyer-stats`);
    if (el) el.textContent = `Da xu ly ${s.done}/${s.target} - Fail: ${s.fail} - Pending: ${s.pending} - Loi: ${s.errors}`;
  }

  function buyerLog(msg) {
    console.log('[5sim-buyer] ' + msg);
    const area = document.getElementById(`${NS}-buyer-log`);
    if (area) { area.value += msg + '\n'; area.scrollTop = area.scrollHeight; }
  }

  function injectBuyerPanel() {
    if (document.getElementById(PANEL_ID) || !document.body) return;

    const panel = document.createElement('div');
    panel.id = PANEL_ID;
    panel.style.cssText = [
      'position:fixed', 'top:60px', 'right:16px', 'z-index:2147483647',
      'background:#1a1a1a', 'color:#fff', 'border:1px solid #ee4d2d', 'border-radius:8px',
      'padding:12px 14px', 'font:12px/1.5 system-ui,-apple-system,sans-serif',
      'box-shadow:0 4px 15px rgba(0,0,0,.5)', 'width:440px', 'max-height:92vh', 'overflow-y:auto',
    ].join(';');

    panel.innerHTML = `
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
        <strong style="color:#ee4d2d;">5sim Buyer (session) <span style="color:#888;font-weight:400;font-size:10px;">v${SCRIPT_VERSION}</span></strong>
        <span id="${NS}-close" style="cursor:pointer;color:#888;">&times;</span>
      </div>
      <div style="background:#2b2b2b;border-radius:4px;padding:6px 8px;margin-bottom:8px;font-size:11px;color:#ffb74d;">
        Dang nhap 5sim.net binh thuong tren tab nay (khong can API key). Mo them 1 tab
        bat ky trang nao cua shopee.ph (cung cai script nay, da dang nhap) de tu dong
        check ho - 2 tab cung 1 script se tu bao nhau, khong can cau hinh gi them.
      </div>

      <div style="display:flex;justify-content:space-between;align-items:center;background:#2b2b2b;border-radius:4px;padding:6px 8px;margin-bottom:8px;">
        <span style="font-size:11px;color:#aaa;">So du 5sim</span>
        <span id="${NS}-buyer-balance" style="font-weight:bold;color:#4caf50;">--</span>
      </div>

      <div style="display:flex;gap:6px;margin-bottom:6px;">
        <label style="flex:2;font-size:11px;">Operator:
          <select id="${NS}-buyer-operator" style="width:100%;padding:4px;border-radius:4px;border:1px solid #444;background:#2b2b2b;color:#fff;font-size:11px;">
            <option value="">Auto (5sim tu chon)</option>
          </select>
        </label>
        <label style="width:70px;font-size:11px;">So luong:
          <input type="number" id="${NS}-buyer-quantity" min="1" value="10" style="width:100%;padding:4px;border-radius:4px;border:1px solid #444;background:#2b2b2b;color:#fff;font-size:11px;">
        </label>
      </div>

      <div style="display:flex;gap:6px;align-items:center;margin-bottom:6px;">
        <label style="font-size:11px;">Delay (giay):</label>
        <input type="number" id="${NS}-buyer-delay-min" min="0" value="3" style="width:50px;padding:4px;border-radius:4px;border:1px solid #444;background:#2b2b2b;color:#fff;font-size:11px;">
        <span style="color:#888;">-</span>
        <input type="number" id="${NS}-buyer-delay-max" min="0" value="6" style="width:50px;padding:4px;border-radius:4px;border:1px solid #444;background:#2b2b2b;color:#fff;font-size:11px;">
        <label style="margin-left:10px;font-size:11px;display:flex;align-items:center;gap:4px;">
          <input type="checkbox" id="${NS}-buyer-auto-cancel" checked> Tu huy khi het so du
        </label>
      </div>

      <div style="display:flex;gap:6px;margin-bottom:6px;">
        <button id="${NS}-buyer-start-btn" style="flex:1;padding:7px 0;cursor:pointer;background:#ee4d2d;color:#fff;border:none;border-radius:4px;font-weight:bold;">Bat dau</button>
        <button id="${NS}-buyer-stop-btn" disabled style="flex:1;padding:7px 0;cursor:pointer;background:#555;color:#fff;border:none;border-radius:4px;font-weight:bold;">Dung</button>
        <button id="${NS}-buyer-export-btn" style="padding:7px 10px;cursor:pointer;background:#2b2b2b;color:#ccc;border:1px solid #444;border-radius:4px;font-size:11px;">Xuat TXT</button>
      </div>
      <div style="margin-bottom:8px;">
        <button id="${NS}-buyer-until-available-btn" style="width:100%;padding:6px 0;cursor:pointer;background:#27ae60;color:#fff;border:none;border-radius:4px;font-size:11px;font-weight:bold;">Chay toi khi co 1 so dung duoc</button>
      </div>

      <div id="${NS}-buyer-stats" style="font-size:11px;color:#4caf50;margin-bottom:6px;">Chua chay lan nao.</div>
      <div id="${NS}-buyer-summary" style="font-size:11px;color:#aaa;margin-bottom:6px;">Tong: 0 pending - 0 fail - 0 done - 0 cancel</div>

      <div style="margin-bottom:6px;">
        <button id="${NS}-buyer-refresh-codes-btn" style="width:100%;padding:6px 0;cursor:pointer;background:#2b2b2b;color:#ccc;border:1px solid #444;border-radius:4px;font-size:11px;">Lam moi tat ca (lay ma hang loat)</button>
      </div>

      <div style="max-height:180px;overflow-y:auto;border:1px solid #333;border-radius:4px;margin-bottom:8px;">
        <table style="width:100%;border-collapse:collapse;font-size:11px;">
          <thead style="position:sticky;top:0;background:#2b2b2b;">
            <tr><th style="padding:4px;text-align:left;">SDT</th><th style="padding:4px;text-align:left;">Trang thai</th><th style="padding:4px;text-align:left;">Ma / Hanh dong</th><th style="padding:4px;text-align:left;">Thoi gian</th></tr>
          </thead>
          <tbody id="${NS}-buyer-tbody"></tbody>
        </table>
      </div>

      <div style="border:1px solid #333;border-radius:4px;margin-bottom:8px;">
        <div id="${NS}-buyer-active-toggle" style="background:#2b2b2b;padding:6px 10px;font-weight:bold;cursor:pointer;display:flex;justify-content:space-between;border-radius:4px 4px 0 0;">
          <span>So dang thue tren 5sim</span><span id="${NS}-buyer-active-arrow">v</span>
        </div>
        <div id="${NS}-buyer-active-fields" style="padding:8px;display:none;background:#1f1f1f;font-size:11px;max-height:150px;overflow-y:auto;">
          <div id="${NS}-buyer-active-box" style="color:#888;">Bam de tai danh sach.</div>
        </div>
      </div>

      <textarea id="${NS}-buyer-log" readonly style="width:100%;height:130px;box-sizing:border-box;font:11px/1.4 monospace;border:1px solid #333;border-radius:4px;padding:4px;background:#0c0c0c;color:#ccc;"></textarea>
    `;
    document.body.appendChild(panel);

    document.getElementById(`${NS}-close`).addEventListener('click', () => { panel.style.display = 'none'; });

    const operatorSel = document.getElementById(`${NS}-buyer-operator`);
    const qtyInput = document.getElementById(`${NS}-buyer-quantity`);
    const delayMinInput = document.getElementById(`${NS}-buyer-delay-min`);
    const delayMaxInput = document.getElementById(`${NS}-buyer-delay-max`);
    const autoCancelChk = document.getElementById(`${NS}-buyer-auto-cancel`);

    qtyInput.value = GM_getValue(K.buyerQuantity, 10);
    delayMinInput.value = GM_getValue(K.buyerDelayMin, 3);
    delayMaxInput.value = GM_getValue(K.buyerDelayMax, 6);
    autoCancelChk.checked = !!GM_getValue(K.buyerAutoCancel, true);

    operatorSel.addEventListener('change', () => GM_setValue(K.buyerOperator, operatorSel.value));
    qtyInput.addEventListener('change', () => GM_setValue(K.buyerQuantity, Math.max(1, num(qtyInput.value) || 10)));
    delayMinInput.addEventListener('change', () => GM_setValue(K.buyerDelayMin, Math.max(0, num(delayMinInput.value))));
    delayMaxInput.addEventListener('change', () => GM_setValue(K.buyerDelayMax, Math.max(0, num(delayMaxInput.value))));
    autoCancelChk.addEventListener('change', () => GM_setValue(K.buyerAutoCancel, autoCancelChk.checked));

    document.getElementById(`${NS}-buyer-start-btn`).addEventListener('click', () => runBuyerBatch(buyerLog));
    document.getElementById(`${NS}-buyer-until-available-btn`).addEventListener('click', () => runBuyerUntilAvailable(buyerLog));
    document.getElementById(`${NS}-buyer-stop-btn`).addEventListener('click', () => {
      stopRequested = true;
      buyerLog('(Da bam Dung - se dung sau khi xu ly xong so hien tai.)');
    });
    document.getElementById(`${NS}-buyer-export-btn`).addEventListener('click', exportBuyerAvailable);
    document.getElementById(`${NS}-buyer-refresh-codes-btn`).addEventListener('click', () => refreshAllPendingCodes(buyerLog));
    document.getElementById(`${NS}-buyer-active-toggle`).addEventListener('click', () => {
      const f = document.getElementById(`${NS}-buyer-active-fields`);
      const a = document.getElementById(`${NS}-buyer-active-arrow`);
      const open = f.style.display === 'none';
      f.style.display = open ? 'block' : 'none';
      a.textContent = open ? '^' : 'v';
      if (open) refreshBuyerActive(buyerLog);
    });

    siteOperatorPrices().then((resp) => {
      const ops = (resp.json && resp.json[FIVESIM_COUNTRY] && resp.json[FIVESIM_COUNTRY][FIVESIM_PRODUCT]) || {};
      const saved = GM_getValue(K.buyerOperator, '');
      const opts = [{ name: '', label: 'Auto (5sim tu chon)' }].concat(
        Object.keys(ops).map((name) => {
          const o = ops[name];
          return { name, label: `${name} - $${o.cost}${o.count != null ? ', con ' + o.count : ''}${o.rate != null ? ', ty le ' + o.rate + '%' : ''}` };
        })
      );
      operatorSel.innerHTML = opts.map((o) => `<option value="${o.name}" ${o.name === saved ? 'selected' : ''}>${o.label}</option>`).join('');
    }).catch(() => { /* giu "Auto" mac dinh neu loi */ });

    renderBuyerResults();
    refreshBuyerBalance();
  }

  // ============================================================
  // MUA MAIL (dongvanfb.net) - TINH NANG DOC LAP voi phan check SDT o tren (khong dung
  // chung du lieu/luong chay). Chi ho tro 4 account_type theo yeu cau: 5/6 (Hotmail/
  // Outlook TRUSTED [GRAPH API]), 59/60 (Hotmail/Outlook TRUSTED [IMAP/POP3/GRAPH API]).
  // Da test that 2026-08-07 bang chinh key trong check_phone_exist/api_key_dongvanfb.txt:
  //   - GET /user/buy CAN THEM tham so 'quantity' (khong co trong docs cung cap, thieu se
  //     loi E_INVALID_QUANTITY - da xac nhan qua test that).
  //   - Doc ma/hop thu: CHI dung API "Graph" (graph_code/graph_messages), KHONG dung ban
  //     "OAuth2/IMAP" (get_code_oauth2/get_messages_oauth2) - vi id 5/6 chi ghi ho tro
  //     [GRAPH API], con 59/60 ho tro ca 2 nhung Graph van dung binh thuong (da test that
  //     tren mail mua tu id 59) -> dung 1 API path chung cho ca 4 loai, khong can re nhanh.
  //   - list_data tra ve dang text "email|password|refresh_token|client_id" moi dong.
  // ============================================================
  const DONGVANFB_BASE = 'https://api.dongvanfb.net';
  const DONGVANFB_TOOLS_BASE = 'https://tools.dongvanfb.net/api';
  const MAIL_ACCOUNT_TYPES = [
    { id: '5', name: 'Hotmail TRUSTED [GRAPH API]' },
    { id: '6', name: 'Outlook TRUSTED [GRAPH API]' },
    { id: '59', name: 'Hotmail TRUSTED [IMAP/POP3/GRAPH API]' },
    { id: '60', name: 'Outlook TRUSTED [IMAP/POP3/GRAPH API]' },
  ];

  function dongvanfbGet(url) {
    return new Promise((resolve, reject) => {
      GM_xmlhttpRequest({
        method: 'GET',
        url,
        headers: { Accept: 'application/json' },
        onload: (resp) => {
          let json;
          try { json = JSON.parse(resp.responseText); }
          catch (e) { reject(new Error('dongvanfb tra ve khong phai JSON: ' + resp.responseText.slice(0, 200))); return; }
          resolve(json);
        },
        onerror: () => reject(new Error('Khong ket noi duoc dongvanfb (kiem tra mang/API key).')),
        ontimeout: () => reject(new Error('dongvanfb timeout.')),
      });
    });
  }

  function dongvanfbToolsPost(path, body) {
    return new Promise((resolve, reject) => {
      GM_xmlhttpRequest({
        method: 'POST',
        url: DONGVANFB_TOOLS_BASE + path,
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        data: JSON.stringify(body),
        onload: (resp) => {
          let json;
          try { json = JSON.parse(resp.responseText); }
          catch (e) { reject(new Error('dongvanfb tools tra ve khong phai JSON: ' + resp.responseText.slice(0, 200))); return; }
          resolve(json);
        },
        onerror: () => reject(new Error('Khong ket noi duoc dongvanfb tools (kiem tra mang).')),
        ontimeout: () => reject(new Error('dongvanfb tools timeout.')),
      });
    });
  }

  async function dongvanfbBalance(apiKey) {
    const json = await dongvanfbGet(`${DONGVANFB_BASE}/user/balance?apikey=${encodeURIComponent(apiKey)}`);
    return json && json.status ? json.balance : null;
  }

  async function buyMail(apiKey, accountType, quantity) {
    const url = `${DONGVANFB_BASE}/user/buy?apikey=${encodeURIComponent(apiKey)}&account_type=${accountType}&quality=0&quantity=${quantity}&type=full`;
    return dongvanfbGet(url);
  }

  // Tach "email|password|refresh_token|client_id". Chi email/client_id (UUID, luon o dau/
  // cuoi) la CHAC CHAN khong chua '|' - de an toan neu refresh_token vo tinh chua ky tu
  // '|' (chua gap trong thuc te nhung khong loai tru), lay client_id la phan SAU dau '|'
  // CUOI CUNG, phan giua con lai (sau password) la refresh_token, thay vi split('|') cung
  // theo dung 4 phan.
  function parseMailLine(line) {
    const firstPipe = line.indexOf('|');
    if (firstPipe === -1) return null;
    const email = line.slice(0, firstPipe);
    const secondPipe = line.indexOf('|', firstPipe + 1);
    if (secondPipe === -1) return null;
    const password = line.slice(firstPipe + 1, secondPipe);
    const lastPipe = line.lastIndexOf('|');
    if (lastPipe <= secondPipe) return null;
    const refreshToken = line.slice(secondPipe + 1, lastPipe);
    const clientId = line.slice(lastPipe + 1);
    if (!email || !clientId) return null;
    return { email, password, refreshToken, clientId };
  }

  async function getShopeeCode(email, refreshToken, clientId) {
    return dongvanfbToolsPost('/graph_code', { email, refresh_token: refreshToken, client_id: clientId, type: 'shopee' });
  }

  async function getMailboxMessages(email, refreshToken, clientId) {
    return dongvanfbToolsPost('/graph_messages', { email, refresh_token: refreshToken, client_id: clientId, list_mail: 'all' });
  }

  // Tu tach ma OTP tu noi dung tho khi dongvanfb TRA VE RONG - da gap thuc te (2026-08-07):
  // email that "Shopee: Use OTP To Verify Your Identity" tu info@mail.shopee.ph CO ma
  // (746881, ngay sau "Your Shopee OTP Code is:") nhung CA graph_code LAN field 'code'
  // trong graph_messages cua chinh dongvanfb deu tra ve rong cho DUNG mau email nay - gioi
  // han/loi o phia ho, khong phai do goi sai. QUAN TRONG: phai BOC THE HTML truoc roi moi
  // regex tren text thuan - thu regex thang tren HTML tho that bai (co hang chuc the
  // </td></tr><tr><td...> chen giua "OTP Code is:" va con so, khong the liet ke het cac
  // kieu the co the gap), da xac nhan qua test that voi dung noi dung email tren.
  function stripHtml(html) {
    return String(html || '').replace(/<[^>]*>/g, ' ').replace(/&nbsp;/g, ' ').replace(/\s+/g, ' ').trim();
  }
  function extractOtpCode(text) {
    if (!text) return null;
    const plain = stripHtml(text);
    const patterns = [
      /(?:OTP|verification)\s*Code\s*is:?\s*(\d{4,8})/i,
      /(?:OTP|verification)\s*code:?\s*(\d{4,8})/i,
      /\b(\d{4,8})\s*(?:is your|la ma)\b/i,
    ];
    for (const re of patterns) {
      const m = plain.match(re);
      if (m) return m[1];
    }
    return null;
  }

  function loadMailResults() { return GM_getValue(K.mailResults, []); }
  function saveMailResults(arr) { GM_setValue(K.mailResults, arr); }

  async function refreshMailBalance() {
    const el = document.getElementById(`${NS}-mail-balance`);
    if (!el) return;
    const apiKey = (GM_getValue(K.apikeyDongvanfb, '') || '').trim();
    if (!apiKey) { el.textContent = '(chua nhap key)'; el.style.color = '#888'; return; }
    try {
      const bal = await dongvanfbBalance(apiKey);
      if (bal == null) { el.textContent = 'loi'; el.style.color = '#e67e22'; return; }
      el.textContent = String(bal);
      el.style.color = '#4caf50';
    } catch (e) {
      el.textContent = 'loi'; el.style.color = '#e67e22';
    }
  }

  async function buyMailBatch() {
    const apiKey = (GM_getValue(K.apikeyDongvanfb, '') || '').trim();
    if (!apiKey) { log('Nhap dongvanfb API key truoc.'); return; }
    const accountType = document.getElementById(`${NS}-mail-account-type`).value;
    const quantity = Math.max(1, num(document.getElementById(`${NS}-mail-quantity`).value) || 1);
    const btn = document.getElementById(`${NS}-mail-buy-btn`);
    btn.disabled = true; btn.textContent = 'Dang mua...';
    try {
      const result = await buyMail(apiKey, accountType, quantity);
      if (!result || result.status !== true) {
        log('Mua mail that bai: ' + (result && result.message ? result.message : 'khong ro loi'));
        return;
      }
      const lines = (result.data && result.data.list_data) || [];
      const arr = loadMailResults();
      let added = 0;
      for (const line of lines) {
        const parsed = parseMailLine(line);
        if (!parsed) continue;
        arr.unshift({
          ...parsed, accountType, orderCode: result.data.order_code,
          time: new Date().toLocaleString('vi-VN'), shopeeCode: null, checkedAt: null, accountName: '',
        });
        added++;
      }
      if (arr.length > 500) arr.length = 500;
      saveMailResults(arr);
      renderMailResults();
      log(`Da mua ${added} mail (het ${result.data.total_amount}, so du con ${result.data.balance}).`);
    } catch (e) {
      log('Loi mua mail: ' + e.message);
    } finally {
      btn.disabled = false; btn.textContent = 'Mua mail';
      refreshMailBalance();
    }
  }

  function downloadTextFile(text, filename) {
    const blob = new Blob([text], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
  }

  function exportMailList() {
    const arr = loadMailResults();
    if (!arr.length) { log('Chua mua mail nao de xuat.'); return; }
    const text = arr.map((r) => `${r.email}|${r.password}|${r.refreshToken}|${r.clientId}`).join('\n');
    downloadTextFile(text, `dongvanfb_mail_export_${Date.now()}.txt`);
  }

  // Xuat rieng "email|ten tai khoan" (KHONG kem mat khau/token) - dung de doi chieu mail
  // da gan voi tai khoan Shopee nao, tach biet voi ban xuat day du credential o tren (van
  // giu nguyen de con dung lai doc hop thu sau nay). Ten tai khoan bo trong neu chua nhap.
  function exportMailAccountPairs() {
    const arr = loadMailResults();
    if (!arr.length) { log('Chua mua mail nao de xuat.'); return; }
    const text = arr.map((r) => `${r.email}|${r.accountName || ''}`).join('\n');
    downloadTextFile(text, `dongvanfb_mail_accounts_${Date.now()}.txt`);
  }

  function clearMailResults() {
    const arr = loadMailResults();
    if (!arr.length) { log('Chua co lich su mail nao de xoa.'); return; }
    if (!confirm(`Xoa toan bo ${arr.length} mail da mua khoi lich su? Hanh dong nay khong the hoan tac.`)) return;
    saveMailResults([]);
    renderMailResults();
    log(`Da xoa toan bo ${arr.length} mail khoi lich su.`);
  }

  function copyMailAddress(email) {
    navigator.clipboard.writeText(email).then(
      () => { /* im lang, khong lam phien bang thong bao cho thao tac hay dung */ },
      () => log(`Khong copy tu dong duoc cho ${email} (trinh duyet chan clipboard) - tu bam chon email va Ctrl+C.`)
    );
  }

  function renderMailResults() {
    const arr = loadMailResults();
    const tbody = document.getElementById(`${NS}-mail-tbody`);
    if (!tbody) return;
    tbody.innerHTML = arr.slice(0, 100).map((r, idx) => `
      <tr>
        <td>${r.email}</td>
        <td><span class="ellipsis" style="max-width:80px;" title="${r.password}">${r.password}</span></td>
        <td><input type="text" data-idx="${idx}" class="${NS}-mail-account-input" value="${(r.accountName || '').replace(/"/g, '&quot;')}" placeholder="(chua gan)" style="width:80px;padding:3px 4px;border-radius:3px;border:1px solid #444;background:#2b2b2b;color:#fff;font-size:11px;"></td>
        <td>${r.shopeeCode ? `<b style="color:#27ae60;">${r.shopeeCode}</b>` : (r.checkedAt ? '<span style="color:#888;">chua co</span>' : '-')}</td>
        <td style="white-space:nowrap;">
          <button data-idx="${idx}" class="${NS}-mail-getcode-btn" style="font-size:10px;padding:2px 6px;background:#ee4d2d;color:#fff;border:none;border-radius:3px;cursor:pointer;">Lay code Shopee</button>
          <button data-idx="${idx}" class="${NS}-mail-inbox-btn" style="font-size:10px;padding:2px 6px;background:#ee4d2d;color:#fff;border:none;border-radius:3px;cursor:pointer;">Xem hop thu</button>
          <button data-idx="${idx}" class="${NS}-mail-copy-btn" style="font-size:10px;padding:2px 6px;background:#ee4d2d;color:#fff;border:none;border-radius:3px;cursor:pointer;">Copy mail</button>
        </td>
        <td style="font-size:10px;color:#888;white-space:nowrap;">${r.time}</td>
      </tr>
    `).join('') || '<tr><td colspan="6" style="color:#888;padding:8px;">Chua mua mail nao.</td></tr>';

    tbody.querySelectorAll(`.${NS}-mail-account-input`).forEach((input) => {
      input.addEventListener('change', () => {
        const idx = Number(input.getAttribute('data-idx'));
        const arr2 = loadMailResults();
        const row = arr2[idx];
        if (!row) return;
        row.accountName = input.value.trim();
        saveMailResults(arr2);
      });
    });

    tbody.querySelectorAll(`.${NS}-mail-copy-btn`).forEach((btn) => {
      btn.addEventListener('click', () => {
        const idx = Number(btn.getAttribute('data-idx'));
        const row = loadMailResults()[idx];
        if (row) copyMailAddress(row.email);
      });
    });

    tbody.querySelectorAll(`.${NS}-mail-getcode-btn`).forEach((btn) => {
      btn.addEventListener('click', async () => {
        const idx = Number(btn.getAttribute('data-idx'));
        const arr2 = loadMailResults();
        const row = arr2[idx];
        if (!row) return;
        btn.disabled = true; btn.textContent = '...';
        try {
          const result = await getShopeeCode(row.email, row.refreshToken, row.clientId);
          let code = result && result.status ? result.code : null;
          // dongvanfb doi luc tra ve rong ngay ca khi hop thu THAT SU co ma (da gap thuc
          // te: mau email "Shopee: Use OTP To Verify Your Identity" tu info@mail.shopee.ph -
          // xem extractOtpCode()) - tu quet lai toan bo hop thu, uu tien thu tu Shopee,
          // roi tu trich ma tu noi dung tho lam phuong an du phong.
          if (!code) {
            const msgsResp = await getMailboxMessages(row.email, row.refreshToken, row.clientId);
            const msgs = (msgsResp && msgsResp.messages) || [];
            const shopeeMsgs = msgs.filter((m) => /shopee/i.test(m.from || '') || /shopee/i.test(m.subject || ''));
            for (const m of shopeeMsgs) {
              const found = extractOtpCode(m.message) || extractOtpCode(m.subject);
              if (found) { code = found; log(`  (Lay ma ${found} qua tu quet hop thu - dongvanfb tra ve rong cho email "${m.subject}")`); break; }
            }
          }
          row.shopeeCode = code || null;
          row.checkedAt = new Date().toLocaleString('vi-VN');
          saveMailResults(arr2);
          renderMailResults();
          if (!row.shopeeCode) log(`Chua co ma Shopee trong hop thu ${row.email}.`);
        } catch (e) {
          log(`Loi lay code cho ${row.email}: ` + e.message);
        } finally {
          btn.disabled = false; btn.textContent = 'Lay code Shopee';
        }
      });
    });

    tbody.querySelectorAll(`.${NS}-mail-inbox-btn`).forEach((btn) => {
      btn.addEventListener('click', async () => {
        const idx = Number(btn.getAttribute('data-idx'));
        const arr2 = loadMailResults();
        const row = arr2[idx];
        if (!row) return;
        btn.disabled = true; btn.textContent = '...';
        try {
          const result = await getMailboxMessages(row.email, row.refreshToken, row.clientId);
          const msgs = (result && result.messages) || [];
          const summary = msgs.length
            ? msgs.map((m) => `- [${m.from}] ${m.subject}${m.code ? ' (code: ' + m.code + ')' : ''}`).join('\n')
            : '(Hop thu trong)';
          log(`Hop thu ${row.email} (${msgs.length} thu):\n${summary.slice(0, 1500)}`);
        } catch (e) {
          log(`Loi xem hop thu ${row.email}: ` + e.message);
        } finally {
          btn.disabled = false; btn.textContent = 'Xem hop thu';
        }
      });
    });
  }

  // ---- Shopee: CUNG origin (dang chay tren shopee.ph) - unsafeWindow.fetch (KHONG phai
  // fetch sandbox cua Tampermonkey) de request duoc SAP ky dung nhu request that cua trang -
  // fetch() thuong da xac nhan qua thuc te se bi 403 ngay (xem tampermonkey_affiliate_group_scraper.user.js). ----
  async function checkPhoneExist(phone) {
    const csrf = getCookieVal('csrftoken');
    const resp = await unsafeWindow.fetch(location.origin + SHOPEE_CHECK_ENDPOINT, {
      method: 'POST',
      credentials: 'include',
      headers: {
        accept: 'application/json, text/plain, */*',
        'content-type': 'application/json',
        ...(csrf ? { 'x-csrftoken': csrf } : {}),
      },
      body: JSON.stringify({ phone: String(phone) }),
    });
    const rawText = await resp.text();
    let json = null;
    try { json = JSON.parse(rawText); } catch (e) { /* khong phai JSON */ }
    return { status: resp.status, json, rawText };
  }

  // Phat hien bat thuong tong quat (giong het heuristic da dung o tampermonkey_affiliate_group_scraper.user.js).
  function looksBlocked(status, json) {
    if (status === 403 || status === 429 || status >= 500) return true;
    if (json === null) return true;
    return false;
  }

  // ============================================================
  // RELAY GIUA 2 VAI TRO CUA CUNG 1 SCRIPT (shopee.ph <-> 5sim.net) - qua
  // GM_setValue/GM_addValueChangeListener CUA CHINH TAMPERMONKEY, KHONG qua server nao ca.
  // Ly do dung duoc du 2 tab o 2 DOMAIN KHAC NHAU: GM storage duoc luu theo TUNG SCRIPT
  // (nhan dien qua @name/UUID), KHONG theo domain - 2 tab cung cai 1 script nay (1 tab mo
  // shopee.ph, 1 tab mo 5sim.net) tu dong CHIA SE chung 1 kho GM_setValue/GM_getValue va
  // nhan duoc thong bao ngay khi tab kia ghi gia tri moi (tham so 'remote'=true). Don gian
  // hon han cach cu (queue qua server local): khong can chay/giu server, khong can polling
  // HTTP, khong can 'device_key'.
  // ============================================================
  const RELAY_REQUEST_KEY = `${NS}_relay_request`;
  const RELAY_RESPONSE_KEY = `${NS}_relay_response`;
  const RELAY_TIMEOUT_MS = 90000; // ~1.5 phut - qua lau nghia la chua co tab shopee.ph nao dang mo/lang nghe

  // Goi tu vai tro 5sim.net: xin check 1 so, tra ve id de doi ket qua qua waitForRelayResult().
  function requestPhoneCheckViaRelay(phone) {
    const id = `${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
    GM_setValue(RELAY_REQUEST_KEY, { id, phone, ts: Date.now() });
    return id;
  }

  // Cho ket qua tu vai tro shopee.ph. Listener la tin hieu CHINH (tuc thi), nhung THEM
  // luoi an toan tu kiem tra GM_getValue dinh ky (RELAY_POLL_FALLBACK_MS, chi doc storage
  // noi bo - KHONG goi mang, gan nhu mien phi) - da gap thuc te (2026-08-08): co truong
  // hop cho mai khong thay ket qua du tab shopee.ph van dang chay dung, nghi listener
  // khong bao kip trong 1 so tinh huong that (tab bi trinh duyet throttle...). Neu chi
  // dua vao listener ma no khong bao, script se treo vo han - luoi an toan nay dam bao du
  // sao cung phat hien duoc ket qua trong toi da RELAY_POLL_FALLBACK_MS.
  const RELAY_POLL_FALLBACK_MS = 3000;

  function waitForRelayResult(id, timeoutMs, onTick) {
    return new Promise((resolve) => {
      let done = false;
      let listenerId = null;
      let pollTimer = null;
      const effectiveTimeout = timeoutMs || RELAY_TIMEOUT_MS;
      const startedAt = Date.now();
      const timer = setTimeout(() => finish(null), effectiveTimeout);
      function finish(val) {
        if (done) return;
        done = true;
        clearTimeout(timer);
        if (pollTimer != null) clearInterval(pollTimer);
        if (listenerId != null) GM_removeValueChangeListener(listenerId);
        resolve(val);
      }
      function checkNow() {
        const existing = GM_getValue(RELAY_RESPONSE_KEY, null);
        if (existing && existing.id === id) finish(existing);
        else if (onTick) onTick(Date.now() - startedAt);
      }
      checkNow();
      if (done) return;
      listenerId = GM_addValueChangeListener(RELAY_RESPONSE_KEY, (name, oldVal, newVal) => {
        if (newVal && newVal.id === id) finish(newVal);
      });
      pollTimer = setInterval(checkNow, RELAY_POLL_FALLBACK_MS);
    });
  }

  // Vai tro shopee.ph: lang nghe yeu cau tu tab 5sim.net, tu check_phone_exist() ho, tra
  // ket qua ve - LUON BAT (khong can bam Start), vi day chi la 1 listener nhe, khong phai
  // vong lap polling ton tai nguyen. Tai dung DUNG checkPhoneExist()/looksBlocked(), khong
  // viet lai logic check.
  let lastRelayHandledId = null;
  async function handleRelayRequest(reqData) {
    if (!reqData || reqData.id === lastRelayHandledId) return;
    lastRelayHandledId = reqData.id;
    const statusEl = document.getElementById(`${NS}-relay-status`);
    if (statusEl) statusEl.textContent = `dang check ${reqData.phone}...`;
    log(`[Relay] Nhan yeu cau check so ${reqData.phone} tu tab 5sim.net...`);

    let checkResp;
    try { checkResp = await checkPhoneExist(reqData.phone); }
    catch (e) {
      log('[Relay]   Loi goi Shopee: ' + e.message);
      GM_setValue(RELAY_RESPONSE_KEY, { id: reqData.id, result: 'error', detail: e.message });
      if (statusEl) statusEl.textContent = 'dang cho yeu cau moi...';
      return;
    }

    let result, username = null, detail = null;
    if (looksBlocked(checkResp.status, checkResp.json)) {
      result = 'blocked'; detail = `HTTP ${checkResp.status}: ${checkResp.rawText.slice(0, 200)}`;
      log(`[Relay]   !!! Nghi bi chan/captcha (HTTP ${checkResp.status}) - tu kiem tra tab nay.`);
    } else {
      const data = checkResp.json && checkResp.json.data;
      if (checkResp.json && checkResp.json.error === 0 && data && data.exist === true) {
        result = 'exists';
        username = (data.user && data.user.username) || null;
        log(`[Relay]   -> DA TON TAI (username: ${username || '?'}).`);
      } else if (checkResp.json && checkResp.json.error === 0 && data && data.exist === false) {
        result = 'available';
        log('[Relay]   -> CHUA TON TAI (con dung duoc).');
      } else {
        result = 'unknown'; detail = JSON.stringify(checkResp.json);
        log('[Relay]   -> Response khong nhu ky vong: ' + detail);
      }
    }
    GM_setValue(RELAY_RESPONSE_KEY, { id: reqData.id, result, username, detail });
    if (statusEl) statusEl.textContent = 'dang cho yeu cau moi...';
  }

  function startRelayListener() {
    const statusEl = document.getElementById(`${NS}-relay-status`);
    if (statusEl) statusEl.textContent = 'dang cho yeu cau tu tab 5sim.net...';
    // Xu ly ngay request co san (phong truong hop tab 5sim.net da ghi TRUOC khi tab nay
    // mo/dang ky listener - listener chi bat su kien THAY DOI sau khi dang ky, khong hoi
    // cuu duoc gia tri da ton tai tu truoc).
    const pending = GM_getValue(RELAY_REQUEST_KEY, null);
    if (pending) handleRelayRequest(pending);
    GM_addValueChangeListener(RELAY_REQUEST_KEY, (name, oldVal, newVal, remote) => {
      if (remote && newVal) handleRelayRequest(newVal);
    });
    // Luoi an toan giong waitForRelayResult() - tu kiem tra lai dinh ky (chi doc GM storage
    // noi bo, khong goi mang) phong truong hop listener khong bao kip trong thuc te.
    setInterval(() => {
      const cur = GM_getValue(RELAY_REQUEST_KEY, null);
      if (cur) handleRelayRequest(cur);
    }, RELAY_POLL_FALLBACK_MS);
  }

  // ---- Luu ket qua ben GM_setValue - song sot qua reload trang ----
  function loadResults() { return GM_getValue(K.results, []); }
  function saveResults(arr) { GM_setValue(K.results, arr); }
  function upsertResult(row) {
    const arr = loadResults();
    const idx = arr.findIndex((r) => r.orderId === row.orderId && r.provider === row.provider);
    if (idx >= 0) arr[idx] = row; else arr.unshift(row);
    if (arr.length > 500) arr.length = 500; // gioi han tranh phinh GM storage vo han
    saveResults(arr);
    renderResults();
  }

  // 1 lan thu duy nhat, KHONG lap lai cho. providerId+apiKey lay TU DONG (khong phai tu o
  // dang chon hien tai) - vi lich su co the con lan cac don hang mua tu nha cung cap KHAC
  // voi nha cung cap dang chon trong panel (nguoi dung co the da doi qua lai). Voi SMSPool
  // da xac nhan qua thuc te: khoa thoi gian dau rental keo dai LAU HON han 15s (3 lan retry
  // x 5s ban dau van luon "cannot be cancelled yet") - lap lai chi ton thoi gian + chan vong
  // lap batch vo ich, nen KHONG retry o day, chi thu 1 lan roi de nut "Huy cac so da ton
  // tai" (cancelInvalidNumbers) xu ly lai sau. Voi 5sim thi hau nhu luon thanh cong ngay
  // (khong co khoa thoi gian).
  async function tryCancelOnce(providerId, apiKey, orderId, log) {
    const provider = PROVIDERS[providerId] || PROVIDERS.smspool;
    let result;
    try { result = await provider.cancel(apiKey, orderId); }
    catch (e) { log('  Loi huy (' + provider.name + '): ' + e.message); return { ok: false, locked: false }; }
    if (result.ok) { log(`  Da huy so tren ${provider.name}.`); refreshBalance(); return result; }
    log(`  Huy chua thanh cong (${provider.name}): ${result.message}${result.locked ? ' (con khoa thoi gian dau rental - thu lai sau bang nut "Huy cac so da ton tai")' : ''}`);
    return result;
  }

  // Huy HANG LOAT tat ca so 'exists' chua huy duoc (moi dong tu dung dung nha cung cap +
  // API key cua chinh no, KHONG phai nha cung cap dang chon hien tai) - goi tung so 1 lan
  // (khong lap), nghi nhe giua cac lan de khong spam server. silent=true: dung khi goi TU
  // DONG tu 1 vong lap (cuoi runBatch, hoac phuc hoi so du trong runUntilAvailable). Khong
  // dung alert() o ham nay (chi log) de khong lam gian doan/treo vong lap tu dong.
  async function cancelInvalidNumbers(log, silent) {
    const pending = loadResults().filter((r) => r.status === 'exists' && !r.cancelled);
    if (!pending.length) {
      log(silent ? '(Khong co so nao can huy.)' : 'Khong co so nao can huy (chua co so "da ton tai" hoac da huy het).');
      return { okCount: 0, lockedCount: 0, failCount: 0 };
    }

    const btn = document.getElementById(`${NS}-cancel-invalid-btn`);
    if (btn) { btn.disabled = true; btn.textContent = 'Dang huy...'; }
    log(`=== Huy${silent ? ' (tu dong)' : ' tay'}: ${pending.length} so dang cho huy ===`);
    let okCount = 0, lockedCount = 0, failCount = 0;
    for (const row of pending) {
      const rowApiKey = row.apiKey || apiKeyFor(row.provider);
      if (!rowApiKey) { log(`  Bo qua ${row.phone}: khong co API key (${row.provider}).`); failCount++; continue; }
      const result = await tryCancelOnce(row.provider, rowApiKey, row.orderId, log);
      row.cancelled = result.ok;
      if (result.ok) okCount++;
      else if (result.locked) lockedCount++;
      else failCount++;
      upsertResult(row);
      await sleep(600); // nghi nhe giua cac lan goi cancel, tranh dong dap server
    }
    if (btn) { btn.disabled = false; btn.textContent = 'Huy cac so da ton tai'; }
    log(`=== XONG huy${silent ? ' (tu dong)' : ' tay'}: thanh cong ${okCount}, con khoa thoi gian ${lockedCount}, loi khac ${failCount}. ===`);
    return { okCount, lockedCount, failCount };
  }

  async function checkOne(providerId, apiKey, extraParam, log) {
    const provider = PROVIDERS[providerId];
    log(`Dang lay so tu ${provider.name}...`);
    let orderResult;
    try { orderResult = await provider.order(apiKey, extraParam); }
    catch (e) { log(`Loi ket noi ${provider.name}: ` + e.message); return { stopBatch: false, error: true }; }
    refreshBalance(); // du thanh cong hay khong, so du co the vua doi (tru tien don hang)

    if (!orderResult.success) {
      log(`Lay so that bai: ${orderResult.rawMessage}`);
      if (STOP_BATCH_ORDER_ERRORS.includes(orderResult.errorType)) {
        const isBalance = orderResult.errorType === 'BALANCE_ERROR';
        log(`!!! Dung: ${orderResult.errorType} (${isBalance ? 'het so du' : 'het hang/khong tim duoc gia'})${isBalance ? ' - che do "toi khi co so dung duoc" se tu huy so cho de hoan tien roi thu lai.' : ' - kiem tra tay truoc khi chay tiep.'}`);
        return { stopBatch: true, error: true, balanceError: isBalance };
      }
      return { stopBatch: false, error: true };
    }

    const phone = orderResult.phone;
    const orderId = orderResult.orderId;
    log(`Da lay so ${phone} (order ${orderId}, ${provider.name}${orderResult.label ? ', ' + orderResult.label : ''}, gia $${orderResult.cost}).`);

    const row = {
      phone, orderId, provider: providerId, apiKey,
      cost: orderResult.cost, pool: orderResult.label,
      time: new Date().toLocaleString('vi-VN'),
      status: 'checking', username: null, detail: null, cancelled: false,
    };
    upsertResult(row);

    let checkResp;
    try { checkResp = await checkPhoneExist(phone); }
    catch (e) {
      row.status = 'error'; row.detail = 'Loi goi Shopee: ' + e.message;
      upsertResult(row);
      log('Loi goi check_phone_exist: ' + e.message);
      return { stopBatch: false, error: true, row };
    }

    if (looksBlocked(checkResp.status, checkResp.json)) {
      row.status = 'blocked'; row.detail = `HTTP ${checkResp.status}: ${checkResp.rawText.slice(0, 200)}`;
      upsertResult(row);
      log(`!!! Nghi bi chan/captcha tu Shopee (HTTP ${checkResp.status}). Dung batch de tu kiem tra tab nay.`);
      return { stopBatch: true, error: true, row };
    }

    const data = checkResp.json && checkResp.json.data;
    if (checkResp.json && checkResp.json.error === 0 && data && data.exist === true) {
      row.status = 'exists';
      row.username = (data.user && data.user.username) || null;
      log(`  -> DA TON TAI tren Shopee (username: ${row.username || '?'}) - so nay khong dung duoc, se cho huy (xem nut "Huy cac so da ton tai").`);
    } else if (checkResp.json && checkResp.json.error === 0 && data && data.exist === false) {
      row.status = 'available';
      log('  -> CHUA TON TAI tren Shopee (so con dung duoc).');
    } else {
      // Response khac ky vong - hien thi NGUYEN VAN de nguoi dung tu xem, khong doan bua.
      row.status = 'unknown';
      row.detail = JSON.stringify(checkResp.json);
      log('  -> Response khong nhu ky vong, noi dung nhan duoc: ' + row.detail);
    }
    upsertResult(row);
    return { stopBatch: false, error: false, row };
  }

  function extraParamFor(providerId) {
    // SMSPool: ma Pool nguoi dung chon ('' = auto). 5sim: ten operator nguoi dung chon tu
    // dropdown (fetchFivesimOperators, du lieu that) - '' = 'any' (de 5sim tu chon).
    if (providerId === 'smspool') return GM_getValue(K.pool, '');
    return GM_getValue(K.operatorFivesim, '') || 'any';
  }

  async function runBatch(log) {
    if (running) { log('Dang chay roi - bam Dung truoc neu muon doi cau hinh.'); return; }
    const providerId = currentProviderId();
    const apiKey = apiKeyFor(providerId);
    if (!apiKey) { log(`Nhap API key cho ${PROVIDERS[providerId].name} truoc.`); return; }
    if (!isLoggedIn()) { log('Ban chua dang nhap Shopee tren tab nay - dang nhap roi thu lai.'); return; }

    const extraParam = extraParamFor(providerId);
    const quantity = Math.max(1, num(GM_getValue(K.quantity, 10)));
    const delayMin = Math.max(0, num(GM_getValue(K.delayMin, 3)));
    const delayMax = Math.max(delayMin, num(GM_getValue(K.delayMax, 6)));
    const autoCancel = !!GM_getValue(K.autoCancel, true);

    running = true; stopRequested = false;
    setButtons(true);
    log(`=== Bat dau (${PROVIDERS[providerId].name}): muc tieu ${quantity} so - so da ton tai se KHONG tu huy trong luc chay, cho gom lai roi huy hang loat luc ket thuc${autoCancel ? '' : ' (dang TAT - phai tu bam nut Huy)'}. ===`);

    let done = 0, exists = 0, available = 0, errors = 0;
    for (let i = 0; i < quantity; i++) {
      if (stopRequested) { log('Da dung theo yeu cau.'); break; }
      const result = await checkOne(providerId, apiKey, extraParam, log);
      done++;
      if (result.row) {
        if (result.row.status === 'exists') exists++;
        else if (result.row.status === 'available') available++;
      }
      if (result.error) errors++;
      updateStats({ done, exists, available, errors, target: quantity });
      if (result.stopBatch) { log('=== Dung som (xem ly do o tren truoc khi chay tiep). ==='); break; }
      if (i < quantity - 1 && !stopRequested) await randomDelay(delayMin, delayMax);
    }
    log(`=== XONG: ${done}/${quantity} - da ton tai ${exists}, con dung duoc ${available}, loi ${errors}. ===`);
    running = false;
    setButtons(false);
    // Doi den cuoi batch moi tu huy (neu bat) - luc nay cac don hang dau tien da du thoi
    // gian de qua khoa "cannot be cancelled yet" (rieng SMSPool - 5sim khong bi khoa nay),
    // ty le thanh cong cao hon nhieu so voi thu ngay luc vua phat hien 'exists'.
    if (autoCancel && exists > 0) {
      await cancelInvalidNumbers(log, true);
    }
  }

  // Che do rieng: chay LIEN TUC (khong gioi han so luong) toi khi co DUNG 1 so "con dung
  // duoc" thi DUNG HAN toan bo (khong lay them). Neu giua chung het so du (BALANCE_ERROR),
  // KHONG dung han nhu runBatch - thay vao do tu huy cac so 'exists' dang cho (hoan tien),
  // doi 15s roi thu lay so lai, lap toi khi thanh cong hoac vuot qua so lan thu toi da.
  async function runUntilAvailable(log) {
    if (running) { log('Dang chay roi - bam Dung truoc neu muon doi cau hinh.'); return; }
    const providerId = currentProviderId();
    const apiKey = apiKeyFor(providerId);
    if (!apiKey) { log(`Nhap API key cho ${PROVIDERS[providerId].name} truoc.`); return; }
    if (!isLoggedIn()) { log('Ban chua dang nhap Shopee tren tab nay - dang nhap roi thu lai.'); return; }

    const extraParam = extraParamFor(providerId);
    const delayMin = Math.max(0, num(GM_getValue(K.delayMin, 3)));
    const delayMax = Math.max(delayMin, num(GM_getValue(K.delayMax, 6)));

    running = true; stopRequested = false;
    setButtons(true);
    log(`=== Bat dau che do "toi khi co so dung duoc" (${PROVIDERS[providerId].name}): chay lien tuc, DUNG HAN ngay khi co 1 so con dung duoc. ===`);

    let attempts = 0;
    let waitRounds = 0;
    const MAX_WAIT_ROUNDS = 20; // tran an toan - tranh treo vo han that su neu khong the phuc hoi duoc so du

    while (!stopRequested) {
      attempts++;
      const result = await checkOne(providerId, apiKey, extraParam, log);

      if (result.row && result.row.status === 'available') {
        log(`=== DA TIM THAY so con dung duoc: ${result.row.phone} - DUNG HAN (khong lay them so nao nua). ===`);
        break;
      }

      if (result.balanceError) {
        waitRounds++;
        if (waitRounds > MAX_WAIT_ROUNDS) {
          log(`!!! Da thu phuc hoi so du ${MAX_WAIT_ROUNDS} lan van khong du - DUNG. Kiem tra tay/nap them tien.`);
          break;
        }
        log(`Het so du (lan cho ${waitRounds}/${MAX_WAIT_ROUNDS}) - dang huy cac so da ton tai de hoan tien...`);
        await cancelInvalidNumbers(log, true);
        await refreshBalance();
        if (stopRequested) break;
        log('Doi 15s roi thu lay so lai...');
        await sleep(15000);
        continue;
      }

      if (result.stopBatch) {
        log('=== Dung han (loi khong tu phuc hoi duoc - xem ly do o tren). ===');
        break;
      }

      waitRounds = 0;
      if (!stopRequested) await randomDelay(delayMin, delayMax);
    }

    log(`=== KET THUC che do "toi khi co so dung duoc": da thu ${attempts} lan. ===`);
    running = false;
    setButtons(false);
  }

  function exportAvailable() {
    const arr = loadResults().filter((r) => r.status === 'available');
    if (!arr.length) { log('Chua co so nao "con dung duoc" de xuat.'); return; }
    const text = arr.map((r) => r.phone).join('\n');
    const blob = new Blob([text], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = `shopee_ph_available_${Date.now()}.txt`;
    a.click();
    URL.revokeObjectURL(url);
  }

  async function refreshActive(log) {
    const providerId = currentProviderId();
    const apiKey = apiKeyFor(providerId);
    if (!apiKey) { log(`Nhap API key cho ${PROVIDERS[providerId].name} truoc.`); return; }
    const box = document.getElementById(`${NS}-active-box`);
    box.innerHTML = '<div style="color:#888;">Dang tai...</div>';
    let list;
    try { list = await PROVIDERS[providerId].listActive(apiKey); }
    catch (e) { box.innerHTML = `<div style="color:#e67e22;">Loi: ${e.message}</div>`; return; }
    if (!list.length) { box.innerHTML = '<div style="color:#888;">Khong co so nao dang thue.</div>'; return; }
    box.innerHTML = list.map((o) => `
      <div style="display:flex;justify-content:space-between;gap:6px;padding:3px 0;border-bottom:1px solid #333;">
        <span>${o.phone} (${o.status}, con ${Math.round(o.timeLeftSec / 60)} phut)</span>
        <button data-order="${o.orderId}" class="${NS}-cancel-active-btn" style="font-size:10px;padding:2px 6px;">Huy</button>
      </div>
    `).join('');
    box.querySelectorAll(`.${NS}-cancel-active-btn`).forEach((btn) => {
      btn.addEventListener('click', async () => {
        const orderId = btn.getAttribute('data-order');
        btn.disabled = true; btn.textContent = '...';
        await tryCancelOnce(providerId, apiKey, orderId, log);
        refreshActive(log);
      });
    });
  }

  // ---- Panel UI ----
  const PANEL_ID = `${NS}-panel`;

  function log(msg) {
    console.log('[phone-checker] ' + msg);
    const area = document.getElementById(`${NS}-log`);
    if (area) {
      area.value += msg + '\n';
      area.scrollTop = area.scrollHeight;
    }
  }

  function setButtons(isRunning) {
    const start = document.getElementById(`${NS}-start-btn`);
    const untilAvail = document.getElementById(`${NS}-until-available-btn`);
    const stop = document.getElementById(`${NS}-stop-btn`);
    if (start) { start.disabled = isRunning; start.style.opacity = isRunning ? '0.5' : '1'; }
    if (untilAvail) { untilAvail.disabled = isRunning; untilAvail.style.opacity = isRunning ? '0.5' : '1'; }
    if (stop) stop.disabled = !isRunning;
  }

  function updateStats(s) {
    const el = document.getElementById(`${NS}-stats`);
    if (el) el.textContent = `Da xu ly ${s.done}/${s.target} - Da ton tai: ${s.exists} - Con dung duoc: ${s.available} - Loi: ${s.errors}`;
  }

  async function refreshBalance() {
    const el = document.getElementById(`${NS}-balance`);
    const labelEl = document.getElementById(`${NS}-balance-label`);
    if (!el) return;
    const providerId = currentProviderId();
    if (labelEl) labelEl.textContent = `So du (${PROVIDERS[providerId].name})`;
    const apiKey = apiKeyFor(providerId);
    if (!apiKey) { el.textContent = '(chua nhap key)'; el.style.color = '#888'; return; }
    try {
      const bal = await PROVIDERS[providerId].balance(apiKey);
      if (bal == null) { el.textContent = 'loi'; el.style.color = '#e67e22'; return; }
      const n = num(bal);
      el.textContent = `$${bal}`;
      el.style.color = n < 1 ? '#e67e22' : '#4caf50'; // canh bao mau khi so du thap (<$1)
    } catch (e) {
      el.textContent = 'loi'; el.style.color = '#e67e22';
    }
  }

  function statusBadge(status) {
    const map = {
      exists: ['#c0392b', 'Da ton tai'],
      available: ['#27ae60', 'Con dung duoc'],
      checking: ['#888', 'Dang kiem tra...'],
      error: ['#e67e22', 'Loi'],
      blocked: ['#c0392b', 'Bi chan'],
      unknown: ['#e67e22', 'Khong xac dinh'],
    };
    const pair = map[status] || ['#888', status];
    return `<span style="color:${pair[0]};font-weight:600;">${pair[1]}</span>`;
  }

  function renderResults() {
    const arr = loadResults();
    const tbody = document.getElementById(`${NS}-tbody`);
    if (!tbody) return;
    tbody.innerHTML = arr.slice(0, 100).map((r) => `
      <tr>
        <td>${r.phone}</td>
        <td style="font-size:10px;color:#888;">${PROVIDERS[r.provider] ? PROVIDERS[r.provider].name : (r.provider || '?')}</td>
        <td>${statusBadge(r.status)}</td>
        <td>${r.username || '-'}</td>
        <td>${r.cancelled ? 'Da huy' : (r.status === 'exists' ? `<button data-order="${r.orderId}" data-provider="${r.provider}" class="${NS}-cancel-btn" style="font-size:10px;padding:2px 6px;">Huy</button>` : '-')}</td>
        <td style="font-size:10px;color:#888;white-space:nowrap;">${r.time}</td>
      </tr>
    `).join('') || '<tr><td colspan="6" style="color:#888;padding:8px;">Chua co du lieu.</td></tr>';

    tbody.querySelectorAll(`.${NS}-cancel-btn`).forEach((btn) => {
      btn.addEventListener('click', async () => {
        const orderId = btn.getAttribute('data-order');
        const providerId = btn.getAttribute('data-provider') || currentProviderId();
        const arr2 = loadResults();
        const row = arr2.find((r) => r.orderId === orderId && r.provider === providerId);
        const apiKey = (row && row.apiKey) || apiKeyFor(providerId);
        btn.disabled = true; btn.textContent = '...';
        const result = await tryCancelOnce(providerId, apiKey, orderId, log);
        if (row) { row.cancelled = result.ok; saveResults(arr2); }
        renderResults();
      });
    });
  }

  function injectPanel() {
    if (document.getElementById(PANEL_ID) || !document.body) return;

    const poolOpts = SMSPOOL_POOLS.map((p) => `<option value="${p.id}">${p.name}</option>`).join('');

    const panel = document.createElement('div');
    panel.id = PANEL_ID;
    panel.style.cssText = [
      'position:fixed', 'top:60px', 'right:16px', 'z-index:2147483647',
      'background:#1a1a1a', 'color:#fff', 'border:1px solid #ee4d2d', 'border-radius:8px',
      'padding:12px 14px', 'font:12px/1.5 system-ui,-apple-system,sans-serif',
      'box-shadow:0 4px 15px rgba(0,0,0,.5)', 'width:480px', 'max-height:92vh', 'overflow-y:auto',
    ].join(';');

    panel.innerHTML = `
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
        <strong style="color:#ee4d2d;">Shopee PH Phone Checker <span style="color:#888;font-weight:400;font-size:10px;">v${SCRIPT_VERSION}</span></strong>
        <span id="${NS}-close" style="cursor:pointer;color:#888;">&times;</span>
      </div>

      <div style="border:1px solid #333;border-radius:4px;margin-bottom:8px;">
        <div id="${NS}-buyapi-toggle" style="background:#2b2b2b;padding:6px 10px;font-weight:bold;cursor:pointer;display:flex;justify-content:space-between;border-radius:4px 4px 0 0;">
          <span>Mua so qua API (SMSPool / 5sim)</span><span id="${NS}-buyapi-arrow">^</span>
        </div>
        <div id="${NS}-buyapi-fields" style="padding:10px;display:block;background:#1f1f1f;">
      <label style="display:block;font-size:11px;margin-bottom:2px;">Nha cung cap so</label>
      <select id="${NS}-provider" style="width:100%;padding:5px;margin-bottom:6px;border-radius:4px;border:1px solid #444;background:#2b2b2b;color:#fff;font-size:11px;">
        <option value="smspool">SMSPool</option>
        <option value="fivesim">5sim</option>
      </select>

      <div style="display:flex;justify-content:space-between;align-items:center;background:#2b2b2b;border-radius:4px;padding:6px 8px;margin-bottom:8px;">
        <span id="${NS}-balance-label" style="font-size:11px;color:#aaa;">So du</span>
        <span>
          <span id="${NS}-balance" style="font-weight:bold;color:#4caf50;">--</span>
          <span id="${NS}-balance-refresh" title="Lam moi so du" style="cursor:pointer;color:#888;margin-left:6px;">&#8635;</span>
        </span>
      </div>

      <label style="display:block;font-size:11px;margin-bottom:2px;">SMSPool API key</label>
      <input type="password" id="${NS}-apikey-smspool" placeholder="api key" style="width:95%;padding:5px;margin-bottom:6px;border-radius:4px;border:1px solid #444;background:#2b2b2b;color:#fff;font-size:11px;">

      <label style="display:block;font-size:11px;margin-bottom:2px;">5sim API key (Bearer token)</label>
      <input type="password" id="${NS}-apikey-fivesim" placeholder="eyJhbGciOi..." style="width:95%;padding:5px;margin-bottom:6px;border-radius:4px;border:1px solid #444;background:#2b2b2b;color:#fff;font-size:11px;">

      <div style="display:flex;gap:6px;margin-bottom:6px;">
        <div id="${NS}-pool-wrap" style="flex:1;">
          <label style="font-size:11px;">Pool (chi SMSPool):
            <select id="${NS}-pool" style="width:100%;padding:4px;border-radius:4px;border:1px solid #444;background:#2b2b2b;color:#fff;font-size:11px;">${poolOpts}</select>
          </label>
        </div>
        <div id="${NS}-operator-wrap" style="flex:1;display:none;">
          <label style="font-size:11px;">Operator (chi 5sim):
            <select id="${NS}-operator" style="width:100%;padding:4px;border-radius:4px;border:1px solid #444;background:#2b2b2b;color:#fff;font-size:11px;">
              <option value="">Auto (5sim tu chon)</option>
            </select>
          </label>
        </div>
        <label style="width:70px;font-size:11px;">So luong:
          <input type="number" id="${NS}-quantity" min="1" value="10" style="width:100%;padding:4px;border-radius:4px;border:1px solid #444;background:#2b2b2b;color:#fff;font-size:11px;">
        </label>
      </div>

      <div style="display:flex;gap:6px;align-items:center;margin-bottom:6px;">
        <label style="font-size:11px;">Delay (giay):</label>
        <input type="number" id="${NS}-delay-min" min="0" value="3" style="width:50px;padding:4px;border-radius:4px;border:1px solid #444;background:#2b2b2b;color:#fff;font-size:11px;">
        <span style="color:#888;">-</span>
        <input type="number" id="${NS}-delay-max" min="0" value="6" style="width:50px;padding:4px;border-radius:4px;border:1px solid #444;background:#2b2b2b;color:#fff;font-size:11px;">
        <label style="margin-left:10px;font-size:11px;display:flex;align-items:center;gap:4px;" title="Tu dong bam nut Huy cac so da ton tai NGAY SAU KHI batch chay xong (khong huy ngay trong luc chay - khoa thoi gian dau rental cua SMSPool khien huy ngay hau nhu luon that bai; 5sim thi khong bi khoa nay).">
          <input type="checkbox" id="${NS}-auto-cancel" checked> Tu huy khi xong batch
        </label>
      </div>

      <div style="display:flex;gap:6px;margin-bottom:6px;">
        <button id="${NS}-start-btn" style="flex:1;padding:7px 0;cursor:pointer;background:#ee4d2d;color:#fff;border:none;border-radius:4px;font-weight:bold;">Bat dau</button>
        <button id="${NS}-stop-btn" disabled style="flex:1;padding:7px 0;cursor:pointer;background:#555;color:#fff;border:none;border-radius:4px;font-weight:bold;">Dung</button>
        <button id="${NS}-export-btn" style="padding:7px 10px;cursor:pointer;background:#2b2b2b;color:#ccc;border:1px solid #444;border-radius:4px;font-size:11px;">Xuat TXT</button>
      </div>
      <div style="margin-bottom:6px;">
        <button id="${NS}-until-available-btn" title="Chay lien tuc (khong gioi han so luong), tu huy+doi khi het so du, DUNG HAN ngay khi co 1 so con dung duoc" style="width:100%;padding:6px 0;cursor:pointer;background:#27ae60;color:#fff;border:none;border-radius:4px;font-size:11px;font-weight:bold;">Chay toi khi co 1 so dung duoc</button>
      </div>
      <div style="margin-bottom:8px;">
        <button id="${NS}-cancel-invalid-btn" style="width:100%;padding:6px 0;cursor:pointer;background:#c0392b;color:#fff;border:none;border-radius:4px;font-size:11px;font-weight:bold;">Huy cac so da ton tai</button>
      </div>

      <div id="${NS}-stats" style="font-size:11px;color:#4caf50;margin-bottom:6px;">Chua chay lan nao.</div>

      <div style="max-height:200px;overflow-y:auto;border:1px solid #333;border-radius:4px;margin-bottom:8px;">
        <table style="width:100%;border-collapse:collapse;font-size:11px;">
          <thead style="position:sticky;top:0;background:#2b2b2b;">
            <tr><th style="padding:4px;text-align:left;">SDT</th><th style="padding:4px;text-align:left;">NCC</th><th style="padding:4px;text-align:left;">Trang thai</th><th style="padding:4px;text-align:left;">Username</th><th style="padding:4px;text-align:left;">Huy</th><th style="padding:4px;text-align:left;">Thoi gian</th></tr>
          </thead>
          <tbody id="${NS}-tbody"></tbody>
        </table>
      </div>
        </div>
      </div>

      <div style="border:1px solid #333;border-radius:4px;margin-bottom:8px;">
        <div id="${NS}-active-toggle" style="background:#2b2b2b;padding:6px 10px;font-weight:bold;cursor:pointer;display:flex;justify-content:space-between;border-radius:4px 4px 0 0;">
          <span>So dang thue (nha cung cap dang chon)</span><span id="${NS}-active-arrow">v</span>
        </div>
        <div id="${NS}-active-fields" style="padding:8px;display:none;background:#1f1f1f;font-size:11px;max-height:150px;overflow-y:auto;">
          <div id="${NS}-active-box" style="color:#888;">Bam de tai danh sach.</div>
        </div>
      </div>

      <div style="border:1px solid #333;border-radius:4px;margin-bottom:8px;">
        <div id="${NS}-mail-toggle" style="background:#2b2b2b;padding:6px 10px;font-weight:bold;cursor:pointer;display:flex;justify-content:space-between;border-radius:4px 4px 0 0;">
          <span>Mua Mail (dongvanfb) - doc lap voi SDT</span><span id="${NS}-mail-arrow">v</span>
        </div>
        <div id="${NS}-mail-fields" style="padding:10px;display:none;background:#1f1f1f;">
          <div style="display:flex;justify-content:space-between;align-items:center;background:#2b2b2b;border-radius:4px;padding:6px 8px;margin-bottom:8px;">
            <span style="font-size:11px;color:#aaa;">So du dongvanfb</span>
            <span>
              <span id="${NS}-mail-balance" style="font-weight:bold;color:#4caf50;">--</span>
              <span id="${NS}-mail-balance-refresh" title="Lam moi so du" style="cursor:pointer;color:#888;margin-left:6px;">&#8635;</span>
            </span>
          </div>
          <label style="display:block;font-size:11px;margin-bottom:2px;">dongvanfb API key</label>
          <input type="password" id="${NS}-mail-apikey" placeholder="api key" style="width:95%;padding:5px;margin-bottom:6px;border-radius:4px;border:1px solid #444;background:#2b2b2b;color:#fff;font-size:11px;">
          <div style="display:flex;gap:6px;margin-bottom:8px;align-items:flex-end;">
            <label style="flex:2;font-size:11px;">Loai mail:
              <select id="${NS}-mail-account-type" style="width:100%;padding:4px;border-radius:4px;border:1px solid #444;background:#2b2b2b;color:#fff;font-size:11px;">
                ${MAIL_ACCOUNT_TYPES.map((t) => `<option value="${t.id}">${t.name}</option>`).join('')}
              </select>
            </label>
            <label style="width:70px;font-size:11px;">So luong:
              <input type="number" id="${NS}-mail-quantity" min="1" value="1" style="width:100%;padding:4px;border-radius:4px;border:1px solid #444;background:#2b2b2b;color:#fff;font-size:11px;">
            </label>
            <button id="${NS}-mail-buy-btn" style="padding:6px 12px;cursor:pointer;background:#ee4d2d;color:#fff;border:none;border-radius:4px;font-size:11px;font-weight:bold;">Mua mail</button>
          </div>
          <div style="max-height:200px;overflow-y:auto;border:1px solid #333;border-radius:4px;margin-bottom:8px;">
            <table style="width:100%;border-collapse:collapse;font-size:11px;">
              <thead style="position:sticky;top:0;background:#2b2b2b;">
                <tr><th style="padding:4px;text-align:left;">Email</th><th style="padding:4px;text-align:left;">Mat khau</th><th style="padding:4px;text-align:left;">Tai khoan</th><th style="padding:4px;text-align:left;">Ma Shopee</th><th style="padding:4px;text-align:left;">Hanh dong</th><th style="padding:4px;text-align:left;">Thoi gian</th></tr>
              </thead>
              <tbody id="${NS}-mail-tbody"></tbody>
            </table>
          </div>
          <div style="display:flex;gap:6px;">
            <button id="${NS}-mail-export-btn" style="flex:1;padding:6px 0;cursor:pointer;background:#2b2b2b;color:#ccc;border:1px solid #444;border-radius:4px;font-size:11px;">Xuat day du (TXT)</button>
            <button id="${NS}-mail-export-accounts-btn" style="flex:1;padding:6px 0;cursor:pointer;background:#2b2b2b;color:#ccc;border:1px solid #444;border-radius:4px;font-size:11px;">Xuat email|Tai khoan (TXT)</button>
            <button id="${NS}-mail-clear-btn" style="flex:1;padding:6px 0;cursor:pointer;background:#3a1f1f;color:#e74c3c;border:1px solid #6b2c2c;border-radius:4px;font-size:11px;">Xoa tat ca</button>
          </div>
        </div>
      </div>

      <div style="font-size:11px;color:#4caf50;margin-bottom:8px;background:#2b2b2b;border-radius:4px;padding:6px 8px;">
        Tu dong check ho tab 5sim.net (khong can bam gi): <span id="${NS}-relay-status" style="color:#ffb74d;">dang cho...</span>
      </div>

      <textarea id="${NS}-log" readonly style="width:100%;height:120px;box-sizing:border-box;font:11px/1.4 monospace;border:1px solid #333;border-radius:4px;padding:4px;background:#0c0c0c;color:#ccc;"></textarea>
    `;
    document.body.appendChild(panel);

    document.getElementById(`${NS}-close`).addEventListener('click', () => { panel.style.display = 'none'; });

    const providerSel = document.getElementById(`${NS}-provider`);
    const apikeySmspoolInput = document.getElementById(`${NS}-apikey-smspool`);
    const apikeyFivesimInput = document.getElementById(`${NS}-apikey-fivesim`);
    const poolWrap = document.getElementById(`${NS}-pool-wrap`);
    const poolSel = document.getElementById(`${NS}-pool`);
    const operatorWrap = document.getElementById(`${NS}-operator-wrap`);
    const operatorSel = document.getElementById(`${NS}-operator`);
    const qtyInput = document.getElementById(`${NS}-quantity`);
    const delayMinInput = document.getElementById(`${NS}-delay-min`);
    const delayMaxInput = document.getElementById(`${NS}-delay-max`);
    const autoCancelChk = document.getElementById(`${NS}-auto-cancel`);

    function applyProviderVisibility() {
      const isSmspool = providerSel.value === 'smspool';
      poolWrap.style.display = isSmspool ? 'block' : 'none';
      operatorWrap.style.display = isSmspool ? 'none' : 'block';
    }

    async function loadOperatorOptions() {
      const current = GM_getValue(K.operatorFivesim, '');
      operatorSel.innerHTML = '<option value="">Dang tai...</option>';
      const ops = await fetchFivesimOperators();
      const opts = [{ name: '', label: 'Auto (5sim tu chon)' }].concat(
        ops.map((o) => ({
          name: o.name,
          label: `${o.name} - $${o.cost}${o.count != null ? ', con ' + o.count : ''}${o.rate != null ? ', ty le ' + o.rate + '%' : ''}`,
        }))
      );
      operatorSel.innerHTML = opts.map((o) => `<option value="${o.name}" ${o.name === current ? 'selected' : ''}>${o.label}</option>`).join('');
    }

    providerSel.value = currentProviderId();
    apikeySmspoolInput.value = GM_getValue(K.apikeySmspool, '');
    apikeyFivesimInput.value = GM_getValue(K.apikeyFivesim, '');
    poolSel.value = GM_getValue(K.pool, '');
    qtyInput.value = GM_getValue(K.quantity, 10);
    delayMinInput.value = GM_getValue(K.delayMin, 3);
    delayMaxInput.value = GM_getValue(K.delayMax, 6);
    autoCancelChk.checked = !!GM_getValue(K.autoCancel, true);
    applyProviderVisibility();
    loadOperatorOptions();

    providerSel.addEventListener('change', () => { GM_setValue(K.provider, providerSel.value); applyProviderVisibility(); refreshBalance(); });
    apikeySmspoolInput.addEventListener('change', () => { GM_setValue(K.apikeySmspool, apikeySmspoolInput.value.trim()); refreshBalance(); });
    apikeyFivesimInput.addEventListener('change', () => { GM_setValue(K.apikeyFivesim, apikeyFivesimInput.value.trim()); refreshBalance(); });
    poolSel.addEventListener('change', () => GM_setValue(K.pool, poolSel.value));
    operatorSel.addEventListener('change', () => GM_setValue(K.operatorFivesim, operatorSel.value));
    qtyInput.addEventListener('change', () => GM_setValue(K.quantity, Math.max(1, num(qtyInput.value) || 10)));
    delayMinInput.addEventListener('change', () => GM_setValue(K.delayMin, Math.max(0, num(delayMinInput.value))));
    delayMaxInput.addEventListener('change', () => GM_setValue(K.delayMax, Math.max(0, num(delayMaxInput.value))));
    autoCancelChk.addEventListener('change', () => GM_setValue(K.autoCancel, autoCancelChk.checked));

    document.getElementById(`${NS}-start-btn`).addEventListener('click', () => runBatch(log));
    document.getElementById(`${NS}-until-available-btn`).addEventListener('click', () => runUntilAvailable(log));
    document.getElementById(`${NS}-stop-btn`).addEventListener('click', () => {
      stopRequested = true;
      log('(Da bam Dung - se dung sau khi xu ly xong so hien tai.)');
    });
    document.getElementById(`${NS}-export-btn`).addEventListener('click', exportAvailable);
    document.getElementById(`${NS}-cancel-invalid-btn`).addEventListener('click', () => cancelInvalidNumbers(log));
    document.getElementById(`${NS}-balance-refresh`).addEventListener('click', refreshBalance);

    document.getElementById(`${NS}-buyapi-toggle`).addEventListener('click', () => {
      const f = document.getElementById(`${NS}-buyapi-fields`);
      const a = document.getElementById(`${NS}-buyapi-arrow`);
      const open = f.style.display === 'none';
      f.style.display = open ? 'block' : 'none';
      a.textContent = open ? '^' : 'v';
    });

    document.getElementById(`${NS}-active-toggle`).addEventListener('click', () => {
      const f = document.getElementById(`${NS}-active-fields`);
      const a = document.getElementById(`${NS}-active-arrow`);
      const open = f.style.display === 'none';
      f.style.display = open ? 'block' : 'none';
      a.textContent = open ? '^' : 'v';
      if (open) refreshActive(log);
    });

    // ---- Mua Mail (dongvanfb) - doc lap, tu load/luu GM_setValue rieng ----
    const mailApikeyInput = document.getElementById(`${NS}-mail-apikey`);
    const mailAccountTypeSel = document.getElementById(`${NS}-mail-account-type`);
    const mailQtyInput = document.getElementById(`${NS}-mail-quantity`);
    mailApikeyInput.value = GM_getValue(K.apikeyDongvanfb, '');
    mailAccountTypeSel.value = GM_getValue(K.mailAccountType, '59');
    mailQtyInput.value = GM_getValue(K.mailQuantity, 1);
    mailApikeyInput.addEventListener('change', () => { GM_setValue(K.apikeyDongvanfb, mailApikeyInput.value.trim()); refreshMailBalance(); });
    mailAccountTypeSel.addEventListener('change', () => GM_setValue(K.mailAccountType, mailAccountTypeSel.value));
    mailQtyInput.addEventListener('change', () => GM_setValue(K.mailQuantity, Math.max(1, num(mailQtyInput.value) || 1)));
    document.getElementById(`${NS}-mail-buy-btn`).addEventListener('click', buyMailBatch);
    document.getElementById(`${NS}-mail-export-btn`).addEventListener('click', exportMailList);
    document.getElementById(`${NS}-mail-export-accounts-btn`).addEventListener('click', exportMailAccountPairs);
    document.getElementById(`${NS}-mail-clear-btn`).addEventListener('click', clearMailResults);
    document.getElementById(`${NS}-mail-balance-refresh`).addEventListener('click', refreshMailBalance);
    document.getElementById(`${NS}-mail-toggle`).addEventListener('click', () => {
      const f = document.getElementById(`${NS}-mail-fields`);
      const a = document.getElementById(`${NS}-mail-arrow`);
      const open = f.style.display === 'none';
      f.style.display = open ? 'block' : 'none';
      a.textContent = open ? '^' : 'v';
      if (open) refreshMailBalance();
    });
    renderMailResults();

    renderResults();
    refreshBalance();
    // "Real-time" o day = tu lam moi dinh ky (khong co push/socket) - du de theo doi so du
    // giam dan trong luc chay batch ma khong can nguoi dung tu bam.
    setInterval(refreshBalance, BALANCE_POLL_MS);

    // Luon bat - khong can bam Start (chi la 1 listener nhe, khong phai vong lap polling
    // ton tai nguyen) - xem RELAY o tren.
    startRelayListener();
  }

  // Bootstrap: 1 script, 2 vai tro theo domain dang mo (xem IS_5SIM_HOST/IS_SHOPEE_HOST o
  // dau file).
  function bootstrap() {
    if (IS_5SIM_HOST) injectBuyerPanel();
    else if (IS_SHOPEE_HOST) injectPanel();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', bootstrap);
  } else {
    bootstrap();
  }
})();
