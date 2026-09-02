// ==UserScript==
// @name         Shopee Affiliate Offer Group Scraper
// @namespace    shopee-crawl
// @version      0.16
// @description  Tu 1 link goc (root), xac thuc du 3 tieu chi (aff_7days/sold/seller_commission) qua DUY NHAT 1 request that toi Shopee. Root DAT thi xet toi 5 san pham tuong tu co san trong CHINH response cua root (similar_product_offers - KHONG goi them request that nao khac) dat 2 tieu chi (sold/seller_commission) cho du nhom 6 link; root KHONG DAT thi loai luon. Dong bo qua local server (affiliate_scrape_server.py) de gan group nguyen tu khi chay nhieu Chrome profile song song.
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

  const SCRIPT_VERSION = '0.16'; // khop @version o header - doi ca 2 cho khi sua script
  const ENDPOINT_MARKER = '/api/v3/offer/product';
  // GROUP_TARGET=6: 1 root (dat du 3 tieu chi) + 5 related (chi can dat 2 tieu chi
  // sold/seller_commission - xem select_l1_l2_candidates.passes_criteria_related() ben
  // server). Doi tu 60->6 theo yeu cau nguoi dung 2026-08-29 - video chi con tao tu link
  // root (xem list_video_push_candidates()), nen khong con can gom nhieu related nhu truoc.
  const GROUP_TARGET = 6;
  // Gia tri MAC DINH (dung khi nguoi dung chua tung nhap tren panel, hoac nhap gia tri
  // khong hop le) - CHINH tra ve dung luc chay lay tu GM_getValue qua getRootDelayRangeMs()
  // ben duoi, cho phep nguoi dung tu chinh ngay tren panel (o "aog-root-delay-min/max") ma
  // khong can sua code/cap nhat script - xem yeu cau nguoi dung 2026-08-29 (de anti-captcha
  // tuy may/tai khoan/thoi diem khac nhau ma khong phai fix cung 1 gia tri cho tat ca).
  // Delay GIUA 2 ROOT khac nhau. Sau khi bo han request that cho tung san pham tuong tu
  // (theo yeu cau nguoi dung 2026-08-31 - da xac nhan similar_product_offers.list tra ve
  // TU response cua root da co san day du du lieu can (batch_item_for_item_card_full +
  // commission_rate), KHONG can goi lai offer/product rieng cho tung candidate nua, xem
  // expandFrom() ben duoi), 1 root gio CHI CON DUY NHAT 1 request that toi Shopee (chinh
  // request xac thuc root). Neu nhieu root lien tiep deu roi vao truong hop nay (thuong gap
  // khi danh muc/nguon root chat luong thap), request that toi Shopee se ban ra LIEN TUC
  // gan nhu khong nghi - de bi Shopee nghi ngo/chan captcha hon nhieu so voi truoc day, nen
  // delay giua 2 root la hang phong thu DUY NHAT con lai (khong con CALL_DELAY giua 2
  // candidate nua vi khong con request that nao o buoc do de can gian cach). Don vi GIAY -
  // de nguoi dung nhap tren panel de doc hon voi khoang gia tri lon nay.
  const ROOT_DELAY_MIN_DEFAULT_S = 2.5;
  const ROOT_DELAY_MAX_DEFAULT_S = 6;
  const POLL_ASSIGNMENT_MS = 4000; // khoang cach hoi server "co viec chua" luc dang ranh

  const SERVER_URL_KEY = 'aog_server_url';
  const DEVICE_KEY_KEY = 'aog_device_key';
  const ROOT_DELAY_MIN_KEY = 'aog_root_delay_min_s';
  const ROOT_DELAY_MAX_KEY = 'aog_root_delay_max_s';
  const STOP_KEY = 'aog_stop';
  // Ghi lai request that GAN NHAT toi callOfferApi() (truoc/sau khi goi) qua GM_setValue -
  // KHONG dung bien module-level thuong vi neu Shopee dieu huong (navigate) that su sang
  // "Page Unavailable" ngay giua luc dang cho response, toan bo JS context (bien trong RAM)
  // mat sach ngay lap tuc, chi con GM storage (rieng cua Tampermonkey, song sot qua dieu
  // huong/tai trang) la con giu duoc "dang xu ly item nao luc do" de dieu tra sau (xem
  // recordCallBefore/recordCallAfter, setupPageUnavailableWatcher() ben duoi - dieu tra thuc
  // te 2026-08-31: script goi callOfferApi binh thuong (code=0) nhung tab van bi chuyen sang
  // "Page Unavailable" GIUA LUC dang chay that (khong tai hien duoc khi test rieng le tu
  // DevTools Console), nen can bang chung tu chinh lan chay that gay loi).
  const LAST_CALL_KEY = 'aog_last_api_call';
  const RECENT_CALLS_KEY = 'aog_recent_api_calls';
  const RECENT_CALLS_MAX = 8;
  const PAGE_UNAVAILABLE_INCIDENT_KEY = 'aog_page_unavailable_incident';
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

  // Chuan hoa 1 cap min/max nguoi dung tu nhap tren panel: gia tri khong hop le (rong, NaN,
  // am) -> fallback ve default TUONG UNG (rieng cho min/max, khong dung chung 1 default cho
  // ca 2), min>max -> tu hoan doi (tranh randomDelay() nhan khoang am, ket qua vo nghia) thay
  // vi bat nguoi dung tu sua lai thu tu. Doc lai MOI LAN goi (khong cache) de thay doi tren
  // panel co hieu luc ngay tu request tiep theo, khong can bam Start lai.
  function sanitizeDelayRange(rawMin, rawMax, defaultMin, defaultMax) {
    let min = parseFloat(rawMin);
    let max = parseFloat(rawMax);
    if (isNaN(min) || min < 0) min = defaultMin;
    if (isNaN(max) || max < 0) max = defaultMax;
    if (min > max) { const t = min; min = max; max = t; }
    return { min, max };
  }

  // Delay giua 2 ROOT khac nhau (nguoi dung nhap tren panel theo GIAY cho de doc, xem
  // "aog-root-delay-min/max") - tra ve da quy doi sang ms de dung thang voi randomDelay().
  function getRootDelayRangeMs() {
    const { min, max } = sanitizeDelayRange(
      GM_getValue(ROOT_DELAY_MIN_KEY, ROOT_DELAY_MIN_DEFAULT_S),
      GM_getValue(ROOT_DELAY_MAX_KEY, ROOT_DELAY_MAX_DEFAULT_S),
      ROOT_DELAY_MIN_DEFAULT_S, ROOT_DELAY_MAX_DEFAULT_S
    );
    return { min: min * 1000, max: max * 1000 };
  }

  // Ghi lai TRUOC khi ban request that (khong doi response) - de neu Shopee dieu huong
  // ngay giua luc cho fetch(), van con dau vet "dang xu ly item nao" trong GM storage.
  function recordCallBefore(itemId, phase) {
    try {
      GM_setValue(LAST_CALL_KEY, JSON.stringify({
        itemId, phase, market: currentMarket, href: location.href, ts: Date.now(),
      }));
    } catch (e) { /* khong lam gian doan luong chinh vi loi ghi log dieu tra */ }
  }

  // Ghi lai SAU khi nhan response (status/code/msg - KHONG luu ca rawText de tranh GM
  // storage phinh to) vao 1 hang doi cap RECENT_CALLS_MAX phan tu, dung de xem lai "vai
  // request truoc do la gi" khi dieu tra su co Page Unavailable.
  function recordCallAfter(itemId, phase, status, code, msg) {
    try {
      let list = [];
      try { list = JSON.parse(GM_getValue(RECENT_CALLS_KEY, '[]')) || []; } catch (e) { list = []; }
      list.push({ itemId, phase, status, code, msg, ts: Date.now() });
      while (list.length > RECENT_CALLS_MAX) list.shift();
      GM_setValue(RECENT_CALLS_KEY, JSON.stringify(list));
    } catch (e) { /* khong lam gian doan luong chinh vi loi ghi log dieu tra */ }
  }

  // ---- Goi Shopee that: cung origin voi trang (affiliate.shopee.*) nen fetch() thuong
  // la du, cookie tu dinh kem, khong can GM_xmlhttpRequest o day (khac phan goi local
  // server ben duoi - do la KHAC origin nen bat buoc phai qua GM_xmlhttpRequest de ne CORS).
  // phase: 'root' (xac thuc chinh root) hoac 'candidate' (san pham tuong tu) - chi de ghi
  // log dieu tra (recordCallBefore/After), khong anh huong logic goi API. ----
  async function callOfferApi(itemId, phase) {
    recordCallBefore(itemId, phase || 'candidate');
    const url = new URL(location.origin + ENDPOINT_MARKER);
    url.searchParams.set('item_id', String(itemId));
    // QUAN TRONG: unsafeWindow.fetch (khong phai fetch thuong cua sandbox Tampermonkey) -
    // da xac nhan qua thuc te la fetch thuong bi Shopee tra 403 ngay lan goi dau (thieu
    // gi do o request that su di ra, du credentials:'include' - unsafeWindow.fetch khop
    // dung request cua chinh trang, giong het probe script da test OK truoc do).
    //
    // 2 header duoi day BAT BUOC phai co - dieu tra 2026-08-31 (so sanh cURL request THAT
    // cua trang vs request cua script qua DevTools): thieu 'affiliate-program-type' VA
    // 'accept' sai gia tri (chi '*/*' thay vi dung 'application/json, text/plain, */*' nhu
    // trang that gui) la 2 khac biet TINH duy nhat tim thay (cac header con lai deu khop,
    // ke ca cookie/token dong af-ac-enc-*/x-sap-*). Day rat co the la dau hieu "cung" de
    // WAF cua Shopee nhan dien request KHONG phai do JS cua chinh trang phat ra, khac voi
    // token dong (thay doi moi request, ca 2 phia deu co) - nghi van chinh cho viec tab bi
    // chuyen sang shopee.<market>/verify/traffic/error sau vai request lien tiep.
    const resp = await unsafeWindow.fetch(url.toString(), {
      method: 'GET',
      credentials: 'include',
      headers: {
        'accept': 'application/json, text/plain, */*',
        'affiliate-program-type': '1',
      },
    });
    const rawText = await resp.text();
    let json = null;
    try {
      json = JSON.parse(rawText);
    } catch (e) {
      // khong phai JSON - co the la trang chan/captcha/loi HTML
    }
    recordCallAfter(itemId, phase || 'candidate', resp.status, json && json.code, json && json.msg);
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

  // Lay dieu kien loc (promoted_7d_max/sold_min/seller_commission_vnd_min) tu CHINH server
  // dang dung de xac minh that (khong hardcode o day) - dung nguon + nhan chu y het tab
  // "Vận hành" tren dashboard (xem section "Điều kiện lọc sản phẩm" trong index.html) de
  // nguoi van hanh doi chieu dung, khong can doan/nho lai tu dong log rai rac. Cap nhat ca
  // khoi hien thi #aog-filter-content (thay trong luc dang render) LAN goi khi bam Start
  // (runLoopInner) - tra ve settings de tai su dung cho soldMin, tranh goi API 2 lan.
  async function loadFilterSettings() {
    const box = document.getElementById('aog-filter-content');
    try {
      const settings = await serverRequest('GET', '/api/settings');
      if (box) {
        box.innerHTML =
          `Số lượng KOL quảng bá 7 ngày &lt; <b>${settings.promoted_7d_max}</b><br>` +
          `Số đã bán trong 30 ngày &gt; <b>${settings.sold_min}</b><br>` +
          `Hoa hồng nhà bán hàng &gt; <b>${settings.seller_commission_vnd_min}</b>`;
      }
      return settings;
    } catch (e) {
      if (box) box.textContent = 'Không lấy được điều kiện lọc: ' + e.message;
      return null;
    }
  }

  // ---- Xu ly chinh cho 1 root: xac thuc root (DUY NHAT 1 request that), neu dat thi xet
  // toi 5 san pham tuong tu CO SAN trong chinh response cua root (similar_product_offers) -
  // KHONG con goi them request that nao khac cho tung candidate (xem expandFrom() ben duoi) ----
  async function runBfsForRoot(root, log, soldMin) {
    const groupid = root.itemid;
    let memberCount = 0;

    log(`=== Bat dau root ${groupid} (${root.product_link || ''}) ===`);
    await heartbeat('working', groupid);

    // Buoc 1: xac thuc chinh root - DUY NHAT request that cua ca ham nay tu sau khi bo goi
    // rieng cho tung candidate (xem ghi chu ROOT_DELAY_MIN_DEFAULT_S).
    const rootResp = await callOfferApi(groupid, 'root');
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
    if (!rootVerify.passes) {
      // Root KHONG dat 3 tieu chi -> LOAI LUON, khong lay san pham tuong tu (theo yeu cau
      // nguoi dung 2026-08-29: khac truoc day - truoc day van BFS tiep qua related de co
      // gang gom du 60 du root rot). finish_root() se tu tinh merged_link (rong vi khong
      // co root/member nao dat) nen nhom nay se khong tao video.
      log(`Root KHONG dat chuan -> loai luon, khong lay san pham tuong tu.`);
      await serverRequest('POST', '/api/roots/finish', { itemid: groupid, market: root.market });
      return { finished: true, reason: 'root_rejected', memberCount: 0 };
    }
    // QUAN TRONG: memberCount CHI phan anh so 'member' THAT tu server (count_group_members()
    // KHONG tinh root) - da tung co bug o day: gan memberCount=1 cho root roi bi verify.group_member_count
    // ghi de mat ngay vong lap dau, khien BFS chay toi du GROUP_TARGET MEMBER + 1 root thay
    // vi GROUP_TARGET tong nhu thiet ke. Tach rieng rootCountsAsOne, LUON cong vao khi so sanh/hien thi.
    const rootCountsAsOne = 1;
    log(`Root DAT chuan -> tinh 1/${GROUP_TARGET}, can them ${GROUP_TARGET - 1} san pham tuong tu.`);

    // Hang doi uu tien theo sold giam dan (mang phang, sort lai moi lan pop - du nhanh
    // voi quy mo vai tram phan tu, khong can cau truc heap rieng).
    let queue = [];
    const seenLocal = new Set([groupid]); // tranh tu xep hang doi lai chinh no/trung trong 1 lan chay

    // QUAN TRONG (dieu tra 2026-08-31 - nguoi dung xac nhan): moi phan tu trong
    // similar_product_offers.list la CUNG 1 kieu du lieu "OfferItem" nhu chinh root (co san
    // item_id/product_link/batch_item_for_item_card_full/commission_rate), TUC LA DA DU
    // truong can de shopee_db.map_v2_data_to_row() tinh sold/seller_commission ma KHONG can
    // goi lai offer/product rieng cho tung candidate. Truoc day ham nay goi callOfferApi()
    // cho tung candidate de lay offer_data day du - do la nguon phat sinh them (toi 5) request
    // that/root, va dieu tra thuc te cho thay chinh o buoc do da xay ra su co Shopee tu chuyen
    // tab sang "Page Unavailable" giua luc chay that (khong tai hien duoc khi goi rieng le tu
    // DevTools Console). Bo han buoc goi rieng nay: dung THANG item trong similar_product_offers
    // lam offer_data gui cho /api/items/verify.
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
      let missingCommission = 0;
      for (const item of similar) {
        const itemId = item.item_id;
        if (!itemId || seenLocal.has(itemId)) continue;
        if (!claimedSet.has(itemId)) { skippedTaken++; continue; } // da thuoc nhom khac
        const sold = (item.batch_item_for_item_card_full || {}).sold || 0;
        if (sold <= soldMin) { skippedSold++; continue; } // loc mien phi truoc (khop dieu kien loc dang cau hinh o server)
        if (!item.commission_rate) missingCommission++; // xem canh bao duoi - phong truong hop du lieu thuc te khac gia dinh
        seenLocal.add(itemId);
        queue.push({ item_id: itemId, sold, offerData: item });
        added++;
      }
      log(`  Nhan ${similar.length} ung vien: +${added} vao hang doi, ${skippedTaken} da thuoc nhom khac, ${skippedSold} bi loai vi sold<=${soldMin}.`);
      if (missingCommission > 0) {
        log(`  !!! CANH BAO: ${missingCommission} ung vien thieu 'commission_rate' trong du lieu similar_product_offers - se tinh hoa hong=0 nen de bi loai oan. Bao lai neu nhom hay bi thieu related bat thuong.`);
      }
    }

    // CHI xet ung vien tu DUY NHAT 1 lan goi root (similar_product_offers cua chinh root) -
    // theo yeu cau nguoi dung 2026-08-29: KHONG con BFS da tang (khong goi expandFrom() tiep
    // tren du lieu cua tung related nhu truoc). Neu danh sach nay khong du 5 ung vien dat
    // chuan thi CHAP NHAN so luong hien co roi ket thuc luon (xem cuoi ham) - khong tim tiep.
    await expandFrom(rootData);

    while (memberCount + rootCountsAsOne < GROUP_TARGET && queue.length > 0) {
      if (isStopped()) {
        log('Da dung theo yeu cau nguoi dung.');
        return { finished: false, reason: 'stopped', memberCount };
      }
      queue.sort((a, b) => b.sold - a.sold);
      const next = queue.shift();

      // KHONG con request that o day - verify THANG bang du lieu co san tu similar_product_offers.
      const verify = await serverRequest('POST', '/api/items/verify', { groupid, offer_data: next.offerData });
      if (verify.outcome === 'assigned' || verify.outcome === 'already_member') {
        memberCount = verify.group_member_count;
        log(`  ${next.item_id}: DAT -> ${memberCount + rootCountsAsOne}/${GROUP_TARGET}`);
      } else {
        log(`  ${next.item_id}: ${verify.outcome}`);
      }
    }

    // Het ung vien tu danh sach tuong tu cua root (hoac da du GROUP_TARGET) -> KET THUC
    // luon, KHONG tim them (khong con khai niem "chua du 6/6 thi tiep tuc tim" - du chi
    // gom duoc 0-4 related van finish binh thuong, chi la group nho hon).
    await serverRequest('POST', '/api/roots/finish', { itemid: groupid, market: root.market });
    if (memberCount + rootCountsAsOne >= GROUP_TARGET) {
      log(`=== XONG root ${groupid}: ${memberCount + rootCountsAsOne}/${GROUP_TARGET}. ===`);
      return { finished: true, reason: 'target_reached', memberCount };
    }
    log(`=== XONG root ${groupid}: het ung vien tu danh sach tuong tu cua root, chi duoc ${memberCount + rootCountsAsOne}/${GROUP_TARGET} - chap nhan, khong tim them. ===`);
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
    const settings = await loadFilterSettings();
    if (!settings) {
      log('Khong lay duoc dieu kien loc tu server - kiem tra lai URL server roi bam Start lai.');
      return;
    }
    log(`Dieu kien loc dang ap dung: aff7d<${settings.promoted_7d_max}, sold>${settings.sold_min}, seller_commission_vnd>${settings.seller_commission_vnd_min}`);
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
      // Nghi giua 2 root (xem ghi chu ROOT_DELAY_MIN_DEFAULT_S/MAX_DEFAULT_S, gia tri thuc te
      // nguoi dung tu nhap tren panel qua getRootDelayRangeMs()) truoc khi hoi/xu ly root ke
      // tiep - bo qua neu nguoi dung vua bam Stop de Stop phan hoi ngay, khong phai cho them.
      if (!isStopped()) {
        const rootDelay = getRootDelayRangeMs();
        await randomDelay(rootDelay.min, rootDelay.max);
      }
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

  // ---- Phat hien + ghi lai su co Shopee tu chuyen tab sang "Page Unavailable / Sorry,
  // something went wrong" GIUA LUC dang chay that (da xac nhan qua thuc te 2026-08-31: goi
  // callOfferApi() rieng le tu DevTools Console tra ve code=0 binh thuong, KHONG tai hien
  // duoc loi nay - nghia la su co chi xay ra trong dieu kien chay that cua vong lap, can bat
  // qua tai cho luc no xay ra thay vi doan). Dung GM storage (khong phai bien RAM) vi neu day
  // la dieu huong that (khong phai SPA render lai tai cho), toan bo JS context mat ngay khi
  // dieu huong bat dau - chi con GM storage la song sot duoc de xem lai sau. ----
  function pageLooksUnavailable() {
    const body = document.body;
    if (!body) return false;
    const text = body.innerText || '';
    return /page unavailable/i.test(text) && /something went wrong/i.test(text);
  }

  function recordPageUnavailableIncident(trigger) {
    try {
      // Chi ghi 1 lan/1 phut - tranh MutationObserver ban lien tuc de mat du lieu "lastCall"
      // goc (ghi de lien tuc se mat dau vet request THUC SU gay ra su co).
      let existing = null;
      try { existing = JSON.parse(GM_getValue(PAGE_UNAVAILABLE_INCIDENT_KEY, 'null')); } catch (e) { existing = null; }
      if (existing && Date.now() - (existing.ts || 0) < 60000) return;

      let lastCall = null;
      let recentCalls = [];
      try { lastCall = JSON.parse(GM_getValue(LAST_CALL_KEY, 'null')); } catch (e) { /* bo qua */ }
      try { recentCalls = JSON.parse(GM_getValue(RECENT_CALLS_KEY, '[]')) || []; } catch (e) { /* bo qua */ }

      const incident = { ts: Date.now(), trigger, href: location.href, market: currentMarket, lastCall, recentCalls };
      GM_setValue(PAGE_UNAVAILABLE_INCIDENT_KEY, JSON.stringify(incident));
      console.error('[offer-group-scraper] !!! PHAT HIEN "Page Unavailable" - da luu chi tiet su co (bam nut "Xem sự cố Page Unavailable" tren panel de xem lai, ke ca sau khi tai trang). Chi tiet:', incident);
      requestStop(); // dung vong lap ngay - tranh tiep tuc chay tren 1 tab da vo, gay them request vo ich
    } catch (e) {
      console.error('[offer-group-scraper] Loi ghi lai su co Page Unavailable: ' + e.message);
    }
  }

  function setupPageUnavailableWatcher() {
    if (pageLooksUnavailable()) recordPageUnavailableIncident('phat_hien_ngay_luc_tai_trang');
    if (!document.body) return;
    const mo = new MutationObserver(() => {
      if (pageLooksUnavailable()) recordPageUnavailableIncident('phat_hien_qua_MutationObserver');
    });
    mo.observe(document.body, { childList: true, subtree: true });
  }

  function showLastPageUnavailableIncident() {
    let raw = GM_getValue(PAGE_UNAVAILABLE_INCIDENT_KEY, '');
    if (!raw) {
      alert('Chua ghi nhan su co "Page Unavailable" nao.');
      return;
    }
    let incident;
    try { incident = JSON.parse(raw); } catch (e) { incident = null; }
    const text = incident ? JSON.stringify(incident, null, 2) : raw;
    log('=== Chi tiet su co "Page Unavailable" gan nhat (copy gui lai de kiem tra) ===');
    log(text);
    log('=== Het chi tiet su co ===');
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
        <button id="aog-show-incident-btn" style="padding:4px 8px;font-size:10px;font-weight:600;cursor:pointer;background:#f2f2f2;color:#222;border:1px solid #ccc;border-radius:4px;" title="Xem chi tiet lan gan nhat Shopee chuyen tab nay sang 'Page Unavailable'">⚠️ Xem sự cố Page Unavailable</button>
        <span id="aog-update-badge" style="display:none;font-size:10px;font-weight:700;color:#d8431f;background:#fff5f2;border:1px solid #ee4d2d;padding:4px 8px;border-radius:4px;cursor:pointer;"></span>
      </div>
      <div id="aog-filter-box" style="background:#f8f9fa;border:1px solid #eee;border-radius:4px;padding:6px 8px;margin-bottom:6px;font-size:11px;line-height:1.6;color:#444;">
        <div style="font-weight:700;color:#333;margin-bottom:2px;">Điều kiện lọc đang áp dụng (theo tab "Vận hành"):</div>
        <div id="aog-filter-content">Đang tải...</div>
      </div>
      <div style="display:flex;gap:6px;margin-bottom:4px;">
        <input id="aog-server" type="text" placeholder="Local server URL"
          style="flex:1;padding:4px 6px;border:1px solid #ccc;border-radius:4px;">
      </div>
      <div style="display:flex;gap:6px;margin-bottom:6px;">
        <input id="aog-device" type="text" placeholder="Device key (ten tai khoan/profile)"
          style="flex:1;padding:4px 6px;border:1px solid #ccc;border-radius:4px;">
      </div>
      <div style="background:#f8f9fa;border:1px solid #eee;border-radius:4px;padding:6px 8px;margin-bottom:6px;font-size:11px;color:#444;">
        <div style="font-weight:700;color:#333;margin-bottom:4px;">Delay chống captcha (để trống = mặc định):</div>
        <div style="display:flex;align-items:center;gap:4px;flex-wrap:wrap;">
          <span style="min-width:132px;">Giữa 2 root (giây):</span>
          <input id="aog-root-delay-min" type="number" min="0" step="0.5" placeholder="${ROOT_DELAY_MIN_DEFAULT_S}"
            style="width:64px;padding:3px 5px;border:1px solid #ccc;border-radius:4px;">
          <span>-</span>
          <input id="aog-root-delay-max" type="number" min="0" step="0.5" placeholder="${ROOT_DELAY_MAX_DEFAULT_S}"
            style="width:64px;padding:3px 5px;border:1px solid #ccc;border-radius:4px;">
        </div>
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

    // Delay chong captcha - de trong (khong nhap gi) = dung mac dinh (xem placeholder o
    // input, sanitizeDelayRange() cung fallback tuong tu neu lo nhap gia tri khong hop le
    // nhu am/chu). Luu ngay khi rai input (change) - lan goi randomDelay() TIEP THEO se ap
    // dung gia tri moi ngay, khong can bam Start lai (xem getRootDelayRangeMs(), doc lai
    // GM_getValue moi lan goi thay vi cache).
    const rootDelayMinInput = document.getElementById('aog-root-delay-min');
    const rootDelayMaxInput = document.getElementById('aog-root-delay-max');
    const storedRootMin = GM_getValue(ROOT_DELAY_MIN_KEY, null);
    const storedRootMax = GM_getValue(ROOT_DELAY_MAX_KEY, null);
    if (storedRootMin !== null) rootDelayMinInput.value = storedRootMin;
    if (storedRootMax !== null) rootDelayMaxInput.value = storedRootMax;
    rootDelayMinInput.addEventListener('change', () => {
      if (rootDelayMinInput.value === '') GM_setValue(ROOT_DELAY_MIN_KEY, null);
      else GM_setValue(ROOT_DELAY_MIN_KEY, parseFloat(rootDelayMinInput.value));
    });
    rootDelayMaxInput.addEventListener('change', () => {
      if (rootDelayMaxInput.value === '') GM_setValue(ROOT_DELAY_MAX_KEY, null);
      else GM_setValue(ROOT_DELAY_MAX_KEY, parseFloat(rootDelayMaxInput.value));
    });

    document.getElementById('aog-start-btn').addEventListener('click', () => runLoop(log));
    document.getElementById('aog-stop-btn').addEventListener('click', () => {
      requestStop();
      log('(Da bam Stop - se dung sau khi xu ly xong item hien tai.)');
    });
    document.getElementById('aog-check-update-btn').addEventListener('click', () => checkForUpdate(true));
    document.getElementById('aog-show-incident-btn').addEventListener('click', showLastPageUnavailableIncident);
    document.getElementById('aog-update-badge').addEventListener('click', openUpdatePage);
    maybeAutoCheckForUpdate();
    loadFilterSettings(); // hien dieu kien loc NGAY luc mo panel, khong can doi bam Start
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
      injectPanel();
      setupPageUnavailableWatcher();
    });
  } else {
    injectPanel();
    setupPageUnavailableWatcher();
  }

  unsafeWindow.__offerGroupScraper = { runLoop: () => runLoop(log), stop: requestStop };
})();
