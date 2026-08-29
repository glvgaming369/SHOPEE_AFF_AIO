// ==UserScript==
// @name         Shopee Product Link Collector
// @namespace    http://tampermonkey.net/
// @version      1.13.2
// @description  Thu thập link sản phẩm Shopee tự động với tính năng cuộn trang thông minh, tự động chuyển trang SPA, tự nhận diện domain quốc gia, tùy chọn tự bấm sắp xếp "Top Sales" trước khi cào, tùy chọn lọc theo lượt bán tối thiểu, cào theo danh sách từ khoá (tự khử trùng, bắt buộc chọn danh mục để không bị "mồ côi"), xuất dữ liệu và đẩy thẳng vào DB (root) của dashboard affiliate offer scraper. Gán cat_id riêng cho từng link ngay lúc cào, đảm bảo đúng danh mục kể cả khi cào nhiều danh mục trước khi đẩy vào DB.
// @author       Antigravity
// @match        https://shopee.vn/*
// @match        https://shopee.ph/*
// @match        https://shopee.co.th/*
// @match        https://shopee.tw/*
// @match        https://shopee.sg/*
// @match        https://shopee.co.id/*
// @match        https://shopee.com.my/*
// @match        https://shopee.cl/*
// @match        https://shopee.com.br/*
// @match        https://shopee.com.mx/*
// @match        https://shopee.com.co/*
// @grant        GM_setValue
// @grant        GM_getValue
// @grant        GM_registerMenuCommand
// @grant        GM_xmlhttpRequest
// @grant        GM_info
// @connect      127.0.0.1
// @connect      localhost
// @updateURL    http://127.0.0.1:8877/userscripts/shopee_collector.user.js
// @downloadURL  http://127.0.0.1:8877/userscripts/shopee_collector.user.js
// ==/UserScript==

(function () {
  'use strict';

  const SCRIPT_VERSION = 'v1.13.2';
  const STORAGE_KEY = 'shopee_collected_links';
  const RUNNING_STATE_KEY = 'shopee_collector_is_running';
  const AUTO_PAGE_KEY = 'shopee_collector_auto_page';
  const TOP_SALES_KEY = 'shopee_collector_top_sales';
  const SOLD_FILTER_ENABLED_KEY = 'shopee_collector_sold_filter_enabled';
  const SOLD_FILTER_MIN_KEY = 'shopee_collector_sold_filter_min';
  const SOLD_FILTER_MIN_DEFAULT = 100;
  const KEYWORDS_KEY = 'shopee_collector_keywords';
  const KEYWORD_MODE_KEY = 'shopee_collector_keyword_mode_running';
  const KEYWORD_INDEX_KEY = 'shopee_collector_keyword_index';
  const KEYWORD_CAT_ID_KEY = 'shopee_collector_keyword_cat_id';
  const KEYWORD_CAT_NAME_KEY = 'shopee_collector_keyword_cat_name';
  // Delay giua 2 tu khoa (dieu huong sang URL search MOI, la 1 lan tai trang THAT SU chu
  // khong phai SPA route change nhu Next Page) - can nghi 1 chut tranh dieu huong lien tuc
  // qua nhieu URL search khac nhau trong thoi gian ngan, giong tinh than delay chong
  // captcha da them o tampermonkey_affiliate_group_scraper.user.js.
  const KEYWORD_DELAY_MIN_MS = 2000;
  const KEYWORD_DELAY_MAX_MS = 5000;
  const SERVER_URL_KEY = 'shopee_collector_server_url';
  const CAT_ID_KEY = 'shopee_collector_cat_id';
  const CAT_NAME_KEY = 'shopee_collector_cat_name';
  const SERVER_URL_DEFAULT = 'http://127.0.0.1:8877';
  const OWN_SCRIPT_FILE = 'shopee_collector.user.js';
  const LAST_UPDATE_CHECK_KEY = 'shopee_collector_last_update_check';
  const UPDATE_CHECK_INTERVAL_MS = 6 * 60 * 60 * 1000; // 6 gio - tranh spam server moi lan load trang

  let isRunning = false;
  let autoPage = false;
  let topSalesSort = false;
  let soldFilterEnabled = false;
  let soldMinThreshold = SOLD_FILTER_MIN_DEFAULT;
  let keywordCategories = []; // [{cat_id, cat_name}, ...] cua market hien tai - nap tu server luc mo panel
  let scrollInterval = null;
  let scanInterval = null;
  let isNavigating = false;

  let lastScrollY = -1;
  let sameScrollCount = 0;

  // Cache trong bo nho cua danh sach link + Set url tuong ung - TRANH JSON.parse() toan bo
  // localStorage + dung lai Set tu dau MOI LAN goi getStoredLinks()/scanLinks() (scanLinks
  // chay ~1.7 lan/giay tu 2 interval cong lai, xem startCollecting()). Voi phien cao dai
  // (hang tram/nghin link), lam vay MOI TICK gay lag that su cho toan bo trang (nguoi dung
  // bao cac nut Stop/Xoa bo nho dem/Dat lai tien trinh phan hoi cham - 2026-08-31) vi JS 1
  // luong, click phai cho tick dang chay xong moi duoc xu ly. linksCacheLoaded rieng voi
  // kiem tra "linksCache !== null" de phan biet dung "chua tung nap" voi "da nap va la mang
  // rong that" (khi CHUA co link nao duoc luu).
  let linksCache = null;
  let existingUrlsCache = null;
  let linksCacheLoaded = false;

  function ensureLinksCacheLoaded() {
    if (linksCacheLoaded) return;
    try {
      const data = localStorage.getItem(STORAGE_KEY);
      const parsed = data ? JSON.parse(data) : [];
      // Tu dong migrate dinh dang cu (mang string thuan, ban script <1.7.0) sang
      // {url, catId: null} khi doc, tranh vo du lieu dang cao do.
      linksCache = parsed.map((item) => (typeof item === 'string' ? { url: item, catId: null } : item));
    } catch (e) {
      linksCache = [];
    }
    existingUrlsCache = new Set(linksCache.map((item) => item.url));
    linksCacheLoaded = true;
  }

  // Lay danh sach link da luu - moi phan tu la {url, catId}, catId gan NGAY luc quet
  // (scanLinks) theo danh muc dang cao tai thoi diem do, KHONG con dung 1 cat_id chung cho
  // ca phien nua - xem ghi chu o pushToDb(). Tra ve THANG reference cache (khong copy) -
  // cac noi CHI DOC (export/pushToDb/updateUI) khong duoc mutate mang tra ve.
  function getStoredLinks() {
    ensureLinksCacheLoaded();
    return linksCache;
  }

  // Lấy trạng thái cài đặt
  function loadSettings() {
    isRunning = localStorage.getItem(RUNNING_STATE_KEY) === 'true';
    autoPage = localStorage.getItem(AUTO_PAGE_KEY) === 'true';
    topSalesSort = localStorage.getItem(TOP_SALES_KEY) === 'true';
    soldFilterEnabled = localStorage.getItem(SOLD_FILTER_ENABLED_KEY) === 'true';
    const storedMin = parseInt(localStorage.getItem(SOLD_FILTER_MIN_KEY), 10);
    soldMinThreshold = isNaN(storedMin) || storedMin < 0 ? SOLD_FILTER_MIN_DEFAULT : storedMin;
  }

  function setRunningState(state) {
    isRunning = state;
    localStorage.setItem(RUNNING_STATE_KEY, state ? 'true' : 'false');
  }

  function setAutoPageState(state) {
    autoPage = state;
    localStorage.setItem(AUTO_PAGE_KEY, state ? 'true' : 'false');
  }

  function setTopSalesSortState(state) {
    topSalesSort = state;
    localStorage.setItem(TOP_SALES_KEY, state ? 'true' : 'false');
  }

  function setSoldFilterEnabledState(state) {
    soldFilterEnabled = state;
    localStorage.setItem(SOLD_FILTER_ENABLED_KEY, state ? 'true' : 'false');
  }

  function setSoldMinThreshold(rawValue) {
    const n = parseInt(rawValue, 10);
    soldMinThreshold = isNaN(n) || n < 0 ? SOLD_FILTER_MIN_DEFAULT : n;
    localStorage.setItem(SOLD_FILTER_MIN_KEY, String(soldMinThreshold));
  }

  function sleep(ms) {
    return new Promise((r) => setTimeout(r, ms));
  }
  function randomDelay(min, max) {
    return sleep(min + Math.random() * (max - min));
  }

  // Chuan hoa danh sach tu khoa (moi dong 1 tu khoa): cat khoang trang dau/cuoi tung dong,
  // bo dong rong, KHU TRUNG khong phan biet hoa/thuong (giu lai dong XUAT HIEN DAU TIEN,
  // theo dung thu tu nguoi dung nhap) - dung ca luc rai khoi o nhap (blur, xem createUI())
  // lan luc xay danh sach that su de chay (getKeywordList(), phong truong hop text luu san
  // trong storage tu ban script cu chua tung duoc khu trung). Tra ve { text, list, removed }.
  function dedupeKeywordsText(rawText) {
    const seen = new Set();
    const list = [];
    let removed = 0;
    (rawText || '').split('\n').forEach((rawLine) => {
      const line = rawLine.trim();
      if (!line) return;
      const key = line.toLowerCase();
      if (seen.has(key)) { removed++; return; }
      seen.add(key);
      list.push(line);
    });
    return { text: list.join('\n'), list, removed };
  }

  function saveKeywordsText(text) {
    localStorage.setItem(KEYWORDS_KEY, text);
  }

  function getKeywordList() {
    return dedupeKeywordsText(localStorage.getItem(KEYWORDS_KEY) || '').list;
  }

  // QUAN TRONG: dung sessionStorage (KHONG PHAI localStorage nhu cac setting khac trong
  // file nay) cho 2 gia tri nay - day la trang thai CUA 1 LAN CHAY cu the, khong phai cai
  // dat nguoi dung. localStorage dung chung cho MOI tab CUNG market (vd 2 tab shopee.ph
  // cung mo) - da gap bug thuc te: 1 tab shopee.ph KHAC (dung dang xem trang bat ky, khong
  // lien quan) cung doc duoc keywordModeRunning=true tu localStorage, tu "tuong" no phai
  // tiep tuc cao, khong tim thay nut Next Page tren trang no dang xem (vi khong phai trang
  // ket qua tim kiem) roi TU Y nhay sang tu khoa ke tiep - lam hong tien trinh cua tab
  // CHINH dang cao that su. sessionStorage rieng cho TUNG tab (nhung van song sot qua F5/
  // dieu huong that trong CUNG 1 tab, va qua "mo lai tab vua dong" cua trinh duyet - dung
  // nhu can cho luong dieu huong sang tu khoa ke tiep cua chinh no).
  function getKeywordModeState() {
    return sessionStorage.getItem(KEYWORD_MODE_KEY) === 'true';
  }
  function setKeywordModeState(state) {
    sessionStorage.setItem(KEYWORD_MODE_KEY, state ? 'true' : 'false');
  }

  function getKeywordIndex() {
    return parseInt(sessionStorage.getItem(KEYWORD_INDEX_KEY) || '0', 10) || 0;
  }
  function setKeywordIndex(n) {
    sessionStorage.setItem(KEYWORD_INDEX_KEY, String(n));
  }

  // URL trang ket qua tim kiem cua CHINH market dang mo (dung location.origin thay vi
  // hardcode domain - tu dong dung cho ca 11 market @match ma khong can liet ke rieng tung
  // domain, xem trao doi voi nguoi dung 2026-08-30).
  function buildSearchUrl(keyword) {
    return `${window.location.origin}/search?keyword=${encodeURIComponent(keyword)}`;
  }

  // Danh muc da chon cho che do "Cao theo tu khoa" - luu ca cat_id LAN cat_name (khong chi
  // cat_id) de gan NGAY khi bam Start ma khong can goi lai server tra ten (da co san tu
  // luc nap dropdown, xem loadKeywordCategories()). Luu ben ngoai 1 lan chon se nho lai cho
  // lan cao theo tu khoa ke tiep, khong phai chon lai tu dau.
  function getKeywordCatSelection() {
    return {
      catId: localStorage.getItem(KEYWORD_CAT_ID_KEY) || '',
      catName: localStorage.getItem(KEYWORD_CAT_NAME_KEY) || '',
    };
  }
  function setKeywordCatSelection(catId, catName) {
    if (catId) {
      localStorage.setItem(KEYWORD_CAT_ID_KEY, catId);
      localStorage.setItem(KEYWORD_CAT_NAME_KEY, catName || '');
    } else {
      localStorage.removeItem(KEYWORD_CAT_ID_KEY);
      localStorage.removeItem(KEYWORD_CAT_NAME_KEY);
    }
  }

  // Nap danh sach danh muc cap 1 CUA DUNG MARKET tab nay dang mo (server tu suy market qua
  // location.href, dung chung ham voi resolveCatName() - xem /api/categories/list). Dung
  // cho dropdown "Cao theo tu khoa": nguoi dung yeu cau KHONG muon link cao tu tu khoa bi
  // "mo coi" khong co danh muc, nen bat buoc chon 1 danh muc truoc khi bam Start (tru khi
  // market nay hoan toan CHUA co danh sach danh muc nao trong cat-db - vd tw/cl/br/mx/co).
  // keywordCategoriesLoaded: PHAN BIET "chua tai xong" (chua biet market co danh muc hay
  // khong) voi "da tai xong nhung market khong co danh muc nao" (keywordCategories=[] trong
  // ca 2 truong hop, khong the dua vao length de phan biet) - nut "Bat dau cao theo tu
  // khoa" bi khoa (disabled) cho toi khi co gia tri nay = true, tranh nguoi dung bam Start
  // dung luc XHR chua kip tra ve ket qua, vo tinh bo qua yeu cau bat buoc chon danh muc.
  let keywordCategoriesLoaded = false;

  function loadKeywordCategories() {
    const sel = document.getElementById('sc-keyword-cat-sel');
    const startBtn = document.getElementById('sc-keyword-start-btn');
    if (!sel) return;
    const serverUrl = getServerUrl();
    GM_xmlhttpRequest({
      method: 'GET',
      url: serverUrl + '/api/categories/list?url=' + encodeURIComponent(location.href),
      onload: (resp) => {
        let json;
        try {
          json = JSON.parse(resp.responseText);
        } catch (e) {
          sel.innerHTML = '<option value="">-- Lỗi tải danh mục (phản hồi không hợp lệ) --</option>';
          keywordCategoriesLoaded = true;
          if (startBtn) startBtn.disabled = false;
          return;
        }
        // QUAN TRONG: server (affiliate_scrape_server.py) co 1 error handler TOAN CUC bien
        // MOI loi (ke ca 404 route khong ton tai - vd server cu chua duoc restart sau khi
        // them endpoint nay) thanh JSON hop le dang {"error": "..."}. Neu chi kiem tra
        // JSON.parse() thanh cong roi doc thang json.categories, 1 phan hoi loi se bi hieu
        // NHAM thanh "danh sach rong that" (json.categories undefined -> || [] -> length=0)
        // - da gap bug thuc te dung nhu vay (nguoi dung bao "market nay chua co danh sach
        // danh muc" trong khi dang dung dung shopee.ph, ly do that la server chua restart).
        // Phai kiem tra status/json.error TRUOC de phan biet loi that voi danh sach rong.
        if (resp.status < 200 || resp.status >= 300 || json.error) {
          sel.innerHTML = `<option value="">-- Lỗi tải danh mục: ${json.error || ('HTTP ' + resp.status)} --</option>`;
          keywordCategoriesLoaded = true;
          if (startBtn) startBtn.disabled = false;
          return;
        }
        keywordCategories = json.categories || [];
        keywordCategoriesLoaded = true;
        if (startBtn) startBtn.disabled = false;
        const { catId } = getKeywordCatSelection();
        if (keywordCategories.length === 0) {
          sel.innerHTML = '<option value="">-- Thị trường này chưa có danh sách danh mục --</option>';
          return;
        }
        const options = ['<option value="">-- Chọn danh mục --</option>']
          .concat(keywordCategories.map((c) =>
            `<option value="${c.cat_id}" ${String(c.cat_id) === catId ? 'selected' : ''}>${c.cat_name}</option>`
          ));
        sel.innerHTML = options.join('');
      },
      onerror: () => {
        sel.innerHTML = '<option value="">-- Không kết nối được server --</option>';
        keywordCategoriesLoaded = true;
        if (startBtn) startBtn.disabled = false;
      },
    });
  }

  // Chuyển đổi href thành định dạng URL chuẩn: https://domain/product/shopId/itemId
  function normalizeShopeeUrl(href) {
    if (!href) return null;
    try {
      const origin = window.location.origin;
      const match = href.match(/-i\.(\d+)\.(\d+)/);
      if (match && match[1] && match[2]) {
        return `${origin}/product/${match[1]}/${match[2]}`;
      }
    } catch (e) {
      console.error('Lỗi parse URL Shopee:', e);
    }
    return null;
  }

  // Lay text hien thi luot ban (vd "1K+ sold", "10K+ Sold/Month") cua 1 the san pham - tim
  // trong PHAM VI the <a> tuong ung (khong quet toan trang, tranh khop nham sang the khac)
  // bang XPath tuong doi (context node = chinh anchor), giu dung dieu kien
  // contains(text(),"old") nguoi dung yeu cau (khop ca "sold" va "Sold" vi ca 2 deu chua
  // chuoi con "old", KHONG khop "SOLD" viet hoa toan bo - chap nhan theo yeu cau). Tra ve
  // null neu the nay khong co phan tu luot ban (vd qua cang, hoac Shopee doi cau truc).
  function getSoldTextFromAnchor(anchor) {
    const xpathResult = document.evaluate(
      './/div[contains(text(),"old")]',
      anchor,
      null,
      XPathResult.FIRST_ORDERED_NODE_TYPE,
      null
    );
    const node = xpathResult && xpathResult.singleNodeValue;
    return node ? node.textContent.trim() : null;
  }

  // Doi text luot ban Shopee sang so tu nhien. Cac dang da xac nhan (theo yeu cau nguoi
  // dung): "1 sold"->1, "1K+ sold"->1000, "10K+ sold"->10000, "10K+ Sold/Month"->10000,
  // "2K+ Sold/Month"->2000, "1 Sold/Month"->1, "1000k+ sold"->1000000. Logic: lay SO DAU
  // TIEN trong chuoi (co the co phan thap phan), neu ngay sau so (co the cach 1 khoang
  // trang) la k/K thi nhan 1000 - moi ky tu con lai (+, sold, Sold/Month...) deu bi bo qua.
  // Khong tim thay so nao (text rong/null/dang la) -> tra ve 0 (coi nhu chua ban, se bi loc
  // neu nguoi dung dat nguong > 0).
  function parseSoldCount(text) {
    if (!text) return 0;
    const m = text.match(/(\d+(?:\.\d+)?)\s*([kK])?/);
    if (!m) return 0;
    let n = parseFloat(m[1]);
    if (m[2]) n *= 1000;
    return Math.round(n);
  }

  // Phat hien cat_id (danh muc cap 1) tu URL hien tai - Shopee dung dinh dang
  // "<ten>-cat.<id>" hoac "<ten>-cat.<id>.<subId>" cho trang danh muc, LUON lay SO DAU
  // TIEN sau "-cat." (vd .../Overseas-Sim-Cards-cat.11013350.11013470 -> 11013350, da xac
  // nhan voi nguoi dung 2026-08-21). Tra ve null neu khong dang o tab danh muc nao (dung
  // nhu thiet ke - root cao o tab khac se khong co cat_id, xem pushToDb()).
  function detectCatIdFromUrl() {
    const m = location.href.match(/-cat\.(\d+)/);
    return m ? m[1] : null;
  }

  // Doan ten danh muc THO tu chinh slug trong URL (vd ".../Pets-cat.11044947" -> "Pets") -
  // Shopee LUON nhung slug ten danh muc ngay truoc "-cat.<id>", ke ca voi danh muc CON (cap
  // 2+) ma artifacts/cat-db/*.xlsx chua co (file do chi liet ke 25 danh muc cap 1 moi thi
  // truong - xem shopee_categories.py). Dung lam ten hien thi NGAY LAP TUC (khong can cho
  // server) va cho MOI cat_id, khong chi 25 danh muc cap 1 - resolveCatName() se ghi de bang
  // ten "dep" hon tu cat-db (co dau &, dau phay dung chuan) NEU server tra duoc, con khong
  // thi giu nguyen ten tu URL nay thay vi hien cat_id tho. Chi thay "-" bang khoang trang nen
  // co the khac 1 chut so voi ten hien thi that cua Shopee (vd mat dau &) nhung van de doc.
  function detectCatNameFromUrl() {
    const m = location.href.match(/\/([^/?#]+)-cat\.\d+/);
    if (!m) return null;
    try {
      const slug = decodeURIComponent(m[1]).replace(/-/g, ' ').trim();
      return slug || null;
    } catch (e) {
      return null;
    }
  }

  // cat_id cua PHIEN thu thap hien tai - CHI duoc dat lai khi nguoi dung bam nut Start THAT
  // (khong phai luc script tu goi lai startCollecting() de tiep tuc sau khi tu chuyen trang
  // hoac tu resume luc load lai trang) - xem gan su kien nut Start ben duoi. Duoc GAN NGAY
  // vao TUNG link moi ngay luc quet (scanLinks()) thay vi ap dung chung cho ca lo luc bam
  // "Đẩy vào DB" - dam bao link cao tu danh muc A khong bi "an theo" cat_id cua danh muc B
  // neu nguoi dung cao nhieu danh muc lien tiep truoc khi day vao DB 1 lan (xem pushToDb()).
  function getSessionCatId() {
    return localStorage.getItem(CAT_ID_KEY) || null;
  }
  function setSessionCatId(catId) {
    if (catId) localStorage.setItem(CAT_ID_KEY, catId);
    else localStorage.removeItem(CAT_ID_KEY);
    // cat_name cu (neu co) thuoc ve cat_id TRUOC do - xoa ngay de khong hien nham ten sai
    // trong luc cho ket qua tra cuu moi (xem resolveCatName()).
    setSessionCatName(null);
  }

  // Ten hien thi (vd "Pets") cua cat_id phien hien tai - tra cuu tu server (cat-db xlsx,
  // xem shopee_categories.py), khong the tu tra tren trinh duyet vi du lieu nam o server.
  function getSessionCatName() {
    return localStorage.getItem(CAT_NAME_KEY) || null;
  }
  function setSessionCatName(catName) {
    if (catName) localStorage.setItem(CAT_NAME_KEY, catName);
    else localStorage.removeItem(CAT_NAME_KEY);
  }

  // Goi server tra cat_name cho catId hien tai - cap nhat localStorage + UI khi co ket qua.
  // Am tham bo qua neu loi/khong tim thay ten (panel se fallback hien "cat_id <id>" - xem
  // updateUI()), khong lam gian doan luong cao link chinh.
  function resolveCatName(catId) {
    if (!catId) return;
    const serverUrl = getServerUrl();
    GM_xmlhttpRequest({
      method: 'GET',
      url: serverUrl + '/api/categories/name?url=' + encodeURIComponent(location.href) + '&cat_id=' + encodeURIComponent(catId),
      onload: (resp) => {
        try {
          const json = JSON.parse(resp.responseText);
          // Phong truong hop nguoi dung da bam Start o danh muc KHAC trong luc cho phan hoi
          // - chi ap dung ket qua neu van dang la catId minh vua hoi. CHI ghi de khi server
          // THAT SU co ten (cat-db co dau &, dau phay dung chuan hon) - neu server khong co
          // (vd danh muc con chua duoc liet ke trong cat-db), GIU NGUYEN ten fallback tu URL
          // slug da hien tu luc bam Start (xem detectCatNameFromUrl()), khong xoa ve rong.
          if (resp.status >= 200 && resp.status < 300 && getSessionCatId() === String(catId) && json.cat_name) {
            setSessionCatName(json.cat_name);
            updateUI();
          }
        } catch (e) {
          // Bo qua - giu nguyen ten fallback tu URL (neu co) hoac cat_id tho.
        }
      },
      onerror: () => {},
    });
  }

  // Quét các link trên trang hiện tại - gán catId NGAY cho link MOI theo danh muc phien hien
  // tai (getSessionCatId(), dat luc bam Start - xem detectCatIdFromUrl()). Lam vay de link cao
  // tu danh muc A giu dung cat_id A ngay ca khi sau do nguoi dung chuyen sang danh muc B va
  // bam Start lai (ghi de session cat_id) roi moi bam "Đẩy vao DB" 1 lan cho ca 2 danh muc.
  //
  // Neu bat "Loc luot ban toi thieu" (soldFilterEnabled): CHI luu link co luot ban >=
  // soldMinThreshold - kiem tra NGAY luc phat hien link MOI (khong luu link duoi nguong vao
  // bo nho dem, khac voi cach reset/xoa sau nay - da chot voi nguoi dung: bat filter CHI anh
  // huong link quet TU LUC BAT tro di, khong loc lai link da co san trong bo nho dem).
  function scanLinks() {
    ensureLinksCacheLoaded(); // linksCache/existingUrlsCache - tranh dung lai Set moi tick, xem ghi chu o tren
    const anchors = document.querySelectorAll('a[href*="-i."]');
    const initialCount = linksCache.length;
    const catId = getSessionCatId();

    anchors.forEach((a) => {
      const rawHref = a.getAttribute('href');
      const cleanUrl = normalizeShopeeUrl(rawHref);
      if (!cleanUrl || existingUrlsCache.has(cleanUrl)) return;

      if (soldFilterEnabled) {
        const soldCount = parseSoldCount(getSoldTextFromAnchor(a));
        if (soldCount < soldMinThreshold) return;
      }

      linksCache.push({ url: cleanUrl, catId });
      existingUrlsCache.add(cleanUrl);
    });

    if (linksCache.length !== initialCount) {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(linksCache));
      updateUI();
    }
  }

  // Tim nut sap xep "Top Sales" (CHUA duoc bat, aria-pressed="false") - Shopee dung <button
  // aria-pressed="true|false"> chua <span> ten bo loc sap xep, span chua text truc tiep nen
  // dung XPath text()="Top Sales" thay vi contains() de tranh khop nham nut khac. Tra ve
  // null neu khong tim thay (trang khong co bo loc nay, hoac da dang bat san - aria-pressed
  // da la "true") - goi noi tu quyet dinh bo qua, cao binh thuong.
  function getTopSalesButton() {
    const xpathResult = document.evaluate(
      '//button[@aria-pressed="false"]//span[text()="Top Sales"]',
      document,
      null,
      XPathResult.FIRST_ORDERED_NODE_TYPE,
      null
    );
    const span = xpathResult && xpathResult.singleNodeValue;
    if (!span) return null;
    return span.closest('button[aria-pressed="false"]') || span.closest('button');
  }

  // So lan thu lai TIM nut "Top Sales" truoc khi ket luan "khong co nut nay" - CAN THIET
  // giong het ly do cua NEXT_PAGE_RETRY_COUNT (xem goToNextPage()): ham nay thuong duoc goi
  // ngay sau 1 lan TAI LAI TRANG THAT SU (dac biet trong che do "Cao theo tu khoa" - moi tu
  // khoa la 1 lan dieu huong URL that, KHONG PHAI SPA), nut sap xep cua Shopee co the CHUA
  // KIP RENDER tai thoi diem kiem tra dau tien. Da gap bug thuc te (nguoi dung bao
  // 2026-08-31): bat "Cao theo Top Sales" trong che do tu khoa nhung khong thay tool bam -
  // truoc day ham nay chi kiem tra DUY NHAT 1 LAN (khac goToNextPage() da duoc sua co thu
  // lai), dan toi ket luan nham + bo qua ngay khi nut chua kip xuat hien.
  const TOP_SALES_RETRY_COUNT = 4;
  const TOP_SALES_RETRY_DELAY_MS = 1000;

  // Bam "Top Sales" (neu tim thay + tuy chon dang bat) roi moi bat dau cao - goi lai moi
  // lan trang MOI duoc tai that su (Start, hoac resume sau F5/dieu huong tu khoa - xem
  // createUI()), KHONG goi lai giua cac lan chuyen trang SPA cung 1 danh muc (Shopee giu
  // nguyen tieu chi sap xep xuyen suot, xem goToNextPage()). Khong tim thay nut sau khi da
  // thu lai het (khong co bo loc nay, hoac da bat san tu truoc) -> bo qua, cao binh thuong,
  // KHONG bao loi (dung nhu yeu cau nguoi dung).
  function startWithOptionalTopSalesSort(retriesLeft) {
    if (!topSalesSort) {
      startCollecting();
      return;
    }
    if (retriesLeft === undefined) retriesLeft = TOP_SALES_RETRY_COUNT;
    const btn = getTopSalesButton();
    if (!btn) {
      if (retriesLeft > 0) {
        console.log(`[Shopee Collector] Chưa thấy nút "Top Sales" - thử lại (còn ${retriesLeft} lần)...`);
        setTimeout(() => startWithOptionalTopSalesSort(retriesLeft - 1), TOP_SALES_RETRY_DELAY_MS);
        return;
      }
      console.log('[Shopee Collector] Không thấy nút "Top Sales" (chưa bật, hoặc trang không có bộ lọc này) - bỏ qua, cào bình thường.');
      startCollecting();
      return;
    }
    console.log('[Shopee Collector] Tìm thấy nút "Top Sales" -> bấm để sắp xếp trước khi cào...');
    btn.click();
    window.scrollTo(0, 0);
    // Cho Shopee tai lai danh sach da sap xep (giong thoi gian cho sau goToNextPage()) roi
    // moi bat dau cuon/quet, tranh quet trung du lieu cu chua kip sap xep lai.
    setTimeout(() => {
      window.scrollTo(0, 0);
      startCollecting();
    }, 2000);
  }

  // Tìm nút Next Page theo XPath hoặc CSS Selector
  function getNextPageButton() {
    // 1. XPath chính xác nút Next Page hoạt động
    const xpathResult = document.evaluate(
      '//*[@class="shopee-icon-button shopee-icon-button--right" and @aria-disabled="false"]',
      document,
      null,
      XPathResult.FIRST_ORDERED_NODE_TYPE,
      null
    );

    if (xpathResult && xpathResult.singleNodeValue) {
      return xpathResult.singleNodeValue;
    }

    // 2. Fallback CSS Selector nút Shopee icon right không bị disabled
    const cssBtn = document.querySelector('.shopee-icon-button--right:not([aria-disabled="true"])');
    if (cssBtn) return cssBtn;

    // 3. Fallback button element
    const svgNextBtn = document.querySelector('button.shopee-icon-button--right');
    if (svgNextBtn && svgNextBtn.getAttribute('aria-disabled') !== 'true') {
      return svgNextBtn;
    }

    return null;
  }

  // So lan thu lai TIM nut Next Page truoc khi ket luan chac chan "het trang" (khong con
  // nut nao ca). CAN THIET vi ngay sau 1 lan TAI LAI TRANG THAT SU (F5, mo lai tab vua
  // dong, hoac dieu huong sang tu khoa moi trong che do "Cao theo tu khoa") - KHAC voi luc
  // chuyen trang binh thuong trong 1 phien dang chay on dinh (SPA, DOM da render xong tu
  // truoc) - nut phan trang cua Shopee co the CHUA KIP RENDER tai thoi diem kiem tra dau
  // tien. Da gap bug thuc te (nguoi dung bao 2026-08-31): dong tab giua chung 1 tu khoa 17
  // trang (moi xong 2/17), mo lai tab thi bi ket luan nham "het trang" ngay lap tuc roi
  // nhay sang tu khoa ke tiep, mat toan bo 15 trang con lai - vi goToNextPage() TRUOC DAY
  // chi kiem tra DUY NHAT 1 LAN, khong co co che thu lai nao.
  const NEXT_PAGE_RETRY_COUNT = 4;
  const NEXT_PAGE_RETRY_DELAY_MS = 1000;

  // Chuyển sang trang tiếp theo và reset trạng thái cuộn. QUAN TRONG: isNavigating duoc
  // khoa (true) NGAY tu lan goi DAU TIEN (retriesLeft con undefined), giu nguyen suot qua
  // trinh thu lai - tranh scrollInterval (van dang tick moi 700ms trong luc cho thu lai)
  // goi chong 1 lan goToNextPage() khac de len (isAtBottom() tra ve false khi isNavigating
  // true, xem ham do). Cac lan goi DE QUY (retriesLeft co gia tri) bo qua kiem tra nay vi
  // da "so huu" khoa tu lan goi dau.
  function goToNextPage(retriesLeft) {
    if (retriesLeft === undefined) {
      if (isNavigating) return;
      isNavigating = true;
      retriesLeft = NEXT_PAGE_RETRY_COUNT;
    }
    const nextBtn = getNextPageButton();

    if (nextBtn) {
      console.log('[Shopee Collector] Đã cuộn tới cuối trang -> Tiến hành bấm nút Next Page...');

      // Bấm nút chuyển trang
      nextBtn.click();

      // Dừng vòng lặp cuộn hiện tại
      if (scrollInterval) clearInterval(scrollInterval);
      scrollInterval = null;

      // Cuộn ngay lên đầu trang mới và chờ dữ liệu tải xong
      window.scrollTo(0, 0);

      // Chờ 2 giây cho Shopee render xong trang mới rồi khởi động lại vòng lặp cuộn
      setTimeout(() => {
        window.scrollTo(0, 0);
        lastScrollY = -1;
        sameScrollCount = 0;
        isNavigating = false;

        if (isRunning) {
          console.log('[Shopee Collector] Trang mới đã sẵn sàng -> Tiếp tục cuộn & quét link...');
          startCollecting();
        }
      }, 2000);
    } else if (retriesLeft > 0) {
      // Chua chac da THAT SU het trang - co the DOM chua kip render nut phan trang (xem
      // ghi chu NEXT_PAGE_RETRY_COUNT o tren). Thu lai sau 1 khoang thay vi ket luan ngay.
      console.log(`[Shopee Collector] Chưa thấy nút Next Page - thử lại (còn ${retriesLeft} lần)...`);
      setTimeout(() => goToNextPage(retriesLeft - 1), NEXT_PAGE_RETRY_DELAY_MS);
    } else {
      console.log('[Shopee Collector] Không tìm thấy nút Next Page hoặc đã ở trang cuối cùng!');
      if (getKeywordModeState()) {
        advanceToNextKeywordOrStop();
      } else {
        stopCollecting();
      }
    }
  }

  // Het trang cho tu khoa hien tai (khong con nut Next Page) trong che do "Cao theo tu
  // khoa" - chuyen sang tu khoa KE TIEP (dieu huong URL search MOI, sau 1 khoang delay
  // ngan chong captcha) neu con, hoac dung han neu da het danh sach. Luon doc lai danh sach
  // TU STORAGE (khong dung bien nho tam) vi ham nay co the chay o 1 lan tai trang HOAN TOAN
  // moi (sau khi dieu huong sang tu khoa truoc do) - xem createUI() (khoi "Tu dong chay
  // tiep") la noi thuc su goi lai startCollecting() sau khi dieu huong, KHONG PHAI o day.
  function advanceToNextKeywordOrStop() {
    if (!isRunning) return; // nguoi dung vua bam Stop giua chung - khong dieu huong tiep
    const list = getKeywordList();
    const nextIndex = getKeywordIndex() + 1;
    if (nextIndex >= list.length) {
      console.log(`[Shopee Collector] Đã cào xong tất cả ${list.length} từ khoá.`);
      setKeywordModeState(false);
      stopCollecting();
      return;
    }
    setKeywordIndex(nextIndex);
    const nextKeyword = list[nextIndex];
    console.log(`[Shopee Collector] Hết trang cho từ khoá hiện tại -> chuyển sang từ khoá kế tiếp (${nextIndex + 1}/${list.length}): "${nextKeyword}"`);
    if (scrollInterval) clearInterval(scrollInterval);
    if (scanInterval) clearInterval(scanInterval);
    scrollInterval = null;
    scanInterval = null;
    isNavigating = true;
    randomDelay(KEYWORD_DELAY_MIN_MS, KEYWORD_DELAY_MAX_MS).then(() => {
      window.location.href = buildSearchUrl(nextKeyword);
    });
  }

  // Kiểm tra xem đã chạm đáy trang chưa
  function isAtBottom() {
    if (isNavigating) return false;

    const scrollHeight = Math.max(
      document.body.scrollHeight,
      document.documentElement.scrollHeight,
      document.body.offsetHeight,
      document.documentElement.offsetHeight
    );
    const currentPosition = window.innerHeight + window.scrollY;

    // Chạm mức cách đáy <= 150px
    if (currentPosition >= scrollHeight - 150) {
      return true;
    }

    // Kiểm tra nếu vị trí cuộn không đổi sau 3 lần cuộn liên tiếp (đã kịch khung cuộn)
    if (Math.abs(window.scrollY - lastScrollY) < 10) {
      sameScrollCount++;
    } else {
      sameScrollCount = 0;
    }
    lastScrollY = window.scrollY;

    if (sameScrollCount >= 3) {
      console.log('[Shopee Collector] Đã kịch khung cuộn (vị trí cuộn không đổi).');
      return true;
    }

    return false;
  }

  // Bắt đầu cuộn trang tự động và quét link
  function startCollecting() {
    setRunningState(true);
    updateUI();

    if (scrollInterval) clearInterval(scrollInterval);
    if (scanInterval) clearInterval(scanInterval);

    lastScrollY = window.scrollY;
    sameScrollCount = 0;

    // Vòng lặp cuộn từ từ xuống cuối trang
    scrollInterval = setInterval(() => {
      if (!isRunning || isNavigating) return;

      const scrollStep = 450;
      window.scrollBy({
        top: scrollStep,
        behavior: 'smooth'
      });

      // Quét liên tục khi cuộn
      scanLinks();

      // Kiểm tra trạng thái chạm đáy
      if (isAtBottom()) {
        scanLinks(); // Quét nốt lần cuối ở cuối trang

        // Che do "Cao theo tu khoa" LUON tu dong chuyen het cac trang cua tung tu khoa
        // (khong phu thuoc checkbox "Tu dong chuyen trang") - da chot voi nguoi dung
        // 2026-08-30, vi muc dich la lay DU ket qua cho tung tu khoa truoc khi chuyen
        // tu khoa tiep theo.
        if (autoPage || getKeywordModeState()) {
          goToNextPage();
        } else {
          stopCollecting();
        }
      }
    }, 700);

    // Quét bổ sung định kỳ
    scanInterval = setInterval(scanLinks, 1000);
  }

  // Dừng thu thập
  function stopCollecting() {
    setRunningState(false);
    if (scrollInterval) clearInterval(scrollInterval);
    if (scanInterval) clearInterval(scanInterval);
    scrollInterval = null;
    scanInterval = null;
    isNavigating = false;
    sameScrollCount = 0;
    lastScrollY = -1;
    updateUI();
  }

  // Xóa bộ nhớ đệm
  function clearStorage() {
    if (confirm('Bạn có chắc chắn muốn xóa tất cả link đã thu thập không?')) {
      stopCollecting();
      localStorage.removeItem(STORAGE_KEY);
      // Phai xoa CA cache trong bo nho (linksCache/existingUrlsCache) - khong thi
      // getStoredLinks()/scanLinks() sau do van tra ve du lieu CU (cache khong tu biet
      // localStorage vua bi xoa tu ben ngoai chinh no).
      linksCache = [];
      existingUrlsCache = new Set();
      linksCacheLoaded = true;
      updateUI();
    }
  }

  // Xuất file TXT
  function exportTXT() {
    const links = getStoredLinks();
    if (links.length === 0) return alert('Chưa có link nào được thu thập!');
    const content = links.map((item) => item.url).join('\n');
    downloadFile(content, 'shopee_links.txt', 'text/plain');
  }

  // Xuất file JSON - giu ca catId de doi chieu dung danh muc khi can
  function exportJSON() {
    const links = getStoredLinks();
    if (links.length === 0) return alert('Chưa có link nào được thu thập!');
    const content = JSON.stringify(links, null, 2);
    downloadFile(content, 'shopee_links.json', 'application/json');
  }

  // Xuất file CSV (mở được bằng Excel / XLSX) - co them cot cat_id de doi chieu
  function exportCSV() {
    const links = getStoredLinks();
    if (links.length === 0) return alert('Chưa có link nào được thu thập!');
    const content = '﻿URL,cat_id\n' + links.map((item) => `"${item.url}",${item.catId || ''}`).join('\n');
    downloadFile(content, 'shopee_links.csv', 'text/csv;charset=utf-8;');
  }

  // Doc URL server tu localStorage (khong dung GM_getValue de dong bo voi cach script nay
  // da luu cac setting khac - GM_setValue/getValue van co the doc chung localStorage cua
  // Tampermonkey binh thuong, nhung giu nguyen kieu localStorage truc tiep cho dong bo voi
  // phan con lai cua file nay).
  function getServerUrl() {
    return localStorage.getItem(SERVER_URL_KEY) || SERVER_URL_DEFAULT;
  }
  function setServerUrl(url) {
    localStorage.setItem(SERVER_URL_KEY, url);
  }

  // So sanh 2 chuoi version dang "1.6.0"/"v1.6.0" - tra ve >0 neu a moi hon b, <0 neu cu
  // hon, 0 neu bang nhau. Tach tung thanh phan so, so sanh tu trai qua phai (chuan semver
  // don gian, du dung cho ca 2 script trong du an nay).
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

  // Uu tien doc version THAT dang cai (GM_info.script.version - Tampermonkey tu dien day
  // du khi cai qua URL) - chinh xac hon hang so SCRIPT_VERSION trong truong hop nguoi dung
  // dang chay 1 ban cu hon file nguon hien tai ma chua kip cap nhat.
  function getLocalVersion() {
    if (typeof GM_info !== 'undefined' && GM_info.script && GM_info.script.version) {
      return GM_info.script.version;
    }
    return SCRIPT_VERSION;
  }

  // Mo trang .user.js cua chinh script nay - Tampermonkey tu bat duoc dieu huong toi URL
  // dang .user.js va hien man hinh Cai dat/Cap nhat cua CHINH NO (giong het cach lien ket
  // trong tab "Cai dat / Cap nhat Script" cua dashboard hoat dong) - khong co API JS nao de
  // 1 userscript tu cap nhat chinh no ma khong qua man hinh nay cua trinh duyet/extension.
  function openUpdatePage() {
    window.open(getServerUrl() + '/userscripts/' + OWN_SCRIPT_FILE, '_blank');
  }

  function showUpdateAvailable(remoteVersion) {
    const badge = document.getElementById('sc-update-badge');
    if (!badge) return;
    badge.textContent = `🆕 Có bản mới v${remoteVersion} - Bấm để cập nhật`;
    badge.style.display = 'inline-block';
  }

  function hideUpdateAvailable() {
    const badge = document.getElementById('sc-update-badge');
    if (badge) badge.style.display = 'none';
  }

  // Doc version MOI NHAT server dang co qua /api/userscripts (JSON co san, dashboard cung
  // dung chinh endpoint nay) - tranh phai tu quet regex '// @version' tu noi dung file .user.js
  // tho. manual=true: co bao loi/thong bao "da moi nhat" ro rang (nguoi dung tu bam nut);
  // manual=false: kiem tra am tham luc load trang, chi hien badge khi THAT SU co ban moi.
  function checkForUpdate(manual) {
    const serverUrl = getServerUrl();
    GM_xmlhttpRequest({
      method: 'GET',
      url: serverUrl + '/api/userscripts',
      onload: (resp) => {
        let json;
        try {
          json = JSON.parse(resp.responseText);
        } catch (e) {
          if (manual) alert('Server trả về không đọc được khi kiểm tra cập nhật.');
          return;
        }
        const entry = (json.userscripts || []).find((u) => u.file === OWN_SCRIPT_FILE);
        const remoteVersion = entry && entry.version;
        if (!remoteVersion) {
          if (manual) alert('Không tìm thấy thông tin phiên bản trên server.');
          return;
        }
        const localVersion = getLocalVersion();
        if (compareVersions(remoteVersion, localVersion) > 0) {
          showUpdateAvailable(remoteVersion);
          if (manual) {
            if (confirm(`Có bản mới v${remoteVersion} (đang dùng v${localVersion}). Mở trang cập nhật ngay?`)) {
              openUpdatePage();
            }
          }
        } else {
          hideUpdateAvailable();
          if (manual) alert(`Đang dùng bản mới nhất (v${localVersion}).`);
        }
      },
      onerror: () => {
        if (manual) alert('Không kết nối được server (' + serverUrl + ') để kiểm tra cập nhật.');
      },
    });
  }

  // Chi tu kiem tra ngam moi UPDATE_CHECK_INTERVAL_MS/lan (khong phai moi lan load trang) -
  // nguoi dung thuong mo rat nhieu trang san pham lien tuc, goi API nay moi trang la thua.
  function maybeAutoCheckForUpdate() {
    const last = parseInt(localStorage.getItem(LAST_UPDATE_CHECK_KEY) || '0', 10);
    if (Date.now() - last < UPDATE_CHECK_INTERVAL_MS) return;
    localStorage.setItem(LAST_UPDATE_CHECK_KEY, String(Date.now()));
    checkForUpdate(false);
  }

  // Day toan bo link da thu thap thang vao DB (bang 'products', lam root) qua
  // affiliate_scrape_server.py - KHAC origin voi shopee.* nen bat buoc GM_xmlhttpRequest
  // de ne CORS (server khong bat CORS, xem affiliate_scrape_server.py).
  //
  // Gui cat_ids RIENG cho TUNG link (khong con gui 1 cat_id chung cho ca lo) - vi bo nho dem
  // co the chua link tu NHIEU danh muc khac nhau (cao danh muc A, chuyen sang danh muc B bam
  // Start lai, roi moi bam "Đẩy vao DB" 1 lan). Moi link da duoc gan dung cat_id rieng ngay
  // luc quet (xem scanLinks()), nen gui song song mang cat_ids la du de server luu dung, khong
  // can gop/nhom lai o day. Xem affiliate_scrape_server.py (/api/roots/import) va
  // shopee_db.import_roots_as_pending().
  function pushToDb() {
    const links = getStoredLinks();
    if (links.length === 0) return alert('Chưa có link nào để đẩy vào DB!');
    const serverUrl = getServerUrl();
    const urls = links.map((item) => item.url);
    const catIds = links.map((item) => item.catId || null);
    GM_xmlhttpRequest({
      method: 'POST',
      url: serverUrl + '/api/roots/import',
      headers: { 'Content-Type': 'application/json' },
      data: JSON.stringify({ links: urls, cat_ids: catIds }),
      onload: (resp) => {
        try {
          const json = JSON.parse(resp.responseText);
          if (resp.status >= 200 && resp.status < 300) {
            const distinctCats = Array.from(new Set(catIds.filter(Boolean)));
            const catMsg = distinctCats.length > 0
              ? ` (${distinctCats.length} danh mục: cat_id ${distinctCats.join(', ')})`
              : ' (không có danh mục)';
            alert(`Đã đẩy vào DB: ${json.added} root mới (${links.length - json.added} đã có sẵn/không hợp lệ)${catMsg}.`);
          } else {
            alert('Server báo lỗi: ' + (json.error || resp.responseText));
          }
        } catch (e) {
          alert('Server trả về không đọc được: ' + resp.responseText.slice(0, 200));
        }
      },
      onerror: () => alert('Không kết nối được server (' + serverUrl + ') - server đã chạy chưa? Kiểm tra ô URL server ở panel.'),
    });
  }

  // Helper tải file xuống
  function downloadFile(content, fileName, mimeType) {
    const blob = new Blob([content], { type: mimeType });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = fileName;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }

  // Xây dựng giao diện UI (Floating Panel)
  function createUI() {
    loadSettings();

    // Tránh tạo trùng UI
    if (document.getElementById('shopee-collector-widget')) return;

    const container = document.createElement('div');
    container.id = 'shopee-collector-widget';
    container.innerHTML = `
      <style>
        #shopee-collector-widget {
          position: fixed;
          bottom: 20px;
          right: 20px;
          z-index: 999999;
          font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
          background: #ffffff;
          border-radius: 12px;
          box-shadow: 0 8px 30px rgba(0,0,0,0.18);
          border: 1px solid #ee4d2d;
          width: 280px;
          overflow: hidden;
          transition: all 0.3s ease;
        }
        #shopee-collector-header {
          background: linear-gradient(135deg, #ee4d2d, #ff7337);
          color: white;
          padding: 10px 14px;
          font-weight: bold;
          font-size: 14px;
          display: flex;
          justify-content: space-between;
          align-items: center;
          cursor: pointer;
        }
        .sc-header-title {
          display: flex;
          align-items: center;
          gap: 4px;
        }
        .sc-version-tag {
          font-size: 10px;
          background: rgba(255, 255, 255, 0.25);
          padding: 2px 6px;
          border-radius: 10px;
          font-weight: normal;
        }
        #shopee-collector-body {
          padding: 14px;
        }
        .sc-update-row {
          display: flex;
          align-items: center;
          gap: 6px;
          margin-bottom: 10px;
          flex-wrap: wrap;
        }
        .sc-btn-check-update {
          flex: none;
          background: #f1f3f5;
          color: #333;
          border: 1px solid #ced4da;
          border-radius: 6px;
          padding: 5px 10px;
          font-size: 11px;
          font-weight: 600;
          cursor: pointer;
        }
        .sc-btn-check-update:hover { background: #e9ecef; }
        .sc-update-badge {
          display: none;
          font-size: 11px;
          font-weight: 700;
          color: #d8431f;
          background: #fff5f2;
          border: 1px solid #ee4d2d;
          padding: 5px 8px;
          border-radius: 6px;
          cursor: pointer;
        }
        .sc-update-badge:hover { background: #ffe8e0; }
        .sc-status-box {
          background: #f8f9fa;
          border-radius: 8px;
          padding: 10px;
          text-align: center;
          margin-bottom: 12px;
          border: 1px solid #e9ecef;
        }
        .sc-status-title {
          font-size: 12px;
          color: #6c757d;
          margin-bottom: 4px;
        }
        .sc-status-count {
          font-size: 24px;
          font-weight: bold;
          color: #ee4d2d;
        }
        .sc-cat-line {
          font-size: 11px;
          color: #6c757d;
          margin-top: 4px;
        }
        .sc-cat-line b {
          color: #333;
        }
        .sc-option-box {
          display: flex;
          align-items: center;
          gap: 8px;
          margin-bottom: 12px;
          font-size: 13px;
          color: #333;
          user-select: none;
          cursor: pointer;
        }
        .sc-option-box input {
          width: 16px;
          height: 16px;
          cursor: pointer;
          accent-color: #ee4d2d;
        }
        .sc-btn-group {
          display: flex;
          gap: 8px;
          margin-bottom: 10px;
        }
        .sc-btn {
          flex: 1;
          padding: 8px;
          border: none;
          border-radius: 6px;
          font-size: 13px;
          font-weight: 600;
          cursor: pointer;
          transition: background 0.2s, transform 0.1s;
        }
        .sc-btn:active {
          transform: scale(0.97);
        }
        .sc-btn-start {
          background: #28a745;
          color: white;
        }
        .sc-btn-start:hover { background: #218838; }
        .sc-btn-stop {
          background: #dc3545;
          color: white;
        }
        .sc-btn-stop:hover { background: #c82333; }
        .sc-btn-clear {
          background: #6c757d;
          color: white;
          width: 100%;
          margin-bottom: 10px;
        }
        .sc-btn-clear:hover { background: #5a6268; }
        .sc-btn-top {
          background: #495057;
          color: white;
          width: 100%;
          margin-bottom: 10px;
        }
        .sc-btn-top:hover { background: #343a40; }
        .sc-export-title {
          font-size: 12px;
          color: #495057;
          margin-bottom: 6px;
          font-weight: 600;
        }
        .sc-export-group {
          display: flex;
          gap: 6px;
        }
        .sc-btn-export {
          flex: 1;
          background: #f1f3f5;
          color: #333;
          border: 1px solid #ced4da;
          padding: 6px 0;
          font-size: 11px;
          font-weight: 600;
        }
        .sc-btn-export:hover {
          background: #e9ecef;
          border-color: #adb5bd;
        }
        .sc-db-title {
          font-size: 12px;
          color: #495057;
          margin: 10px 0 6px;
          font-weight: 600;
        }
        .sc-db-url {
          width: 100%;
          box-sizing: border-box;
          padding: 6px 8px;
          border: 1px solid #ced4da;
          border-radius: 6px;
          font-size: 11px;
          margin-bottom: 6px;
        }
        .sc-keywords-select {
          width: 100%;
          box-sizing: border-box;
          padding: 6px 8px;
          border: 1px solid #ced4da;
          border-radius: 6px;
          font-size: 11px;
          margin-bottom: 6px;
          background: #fff;
        }
        .sc-keywords-ta {
          width: 100%;
          box-sizing: border-box;
          padding: 6px 8px;
          border: 1px solid #ced4da;
          border-radius: 6px;
          font-size: 11px;
          margin-bottom: 4px;
          resize: vertical;
          min-height: 60px;
          font-family: inherit;
        }
        .sc-keyword-note {
          font-size: 10px;
          color: #ee4d2d;
          min-height: 12px;
          margin-bottom: 6px;
        }
        .sc-btn-push {
          width: 100%;
          background: #ee4d2d;
          color: white;
        }
        .sc-btn-push:hover { background: #d8431f; }
        .sc-badge {
          display: inline-block;
          width: 8px;
          height: 8px;
          border-radius: 50%;
          margin-right: 6px;
        }
        .sc-badge-active { background: #00ff66; box-shadow: 0 0 8px #00ff66; }
        .sc-badge-inactive { background: #ff4d4d; }
      </style>
      <div id="shopee-collector-header">
        <span class="sc-header-title">
          <span id="sc-status-indicator" class="sc-badge sc-badge-inactive"></span>
          Shopee Collector
          <span class="sc-version-tag">${SCRIPT_VERSION}</span>
        </span>
        <span id="sc-toggle-btn" style="font-size: 12px;">▼</span>
      </div>
      <div id="shopee-collector-body">
        <div class="sc-update-row">
          <button class="sc-btn-check-update" id="sc-check-update-btn">🔄 Kiểm tra cập nhật</button>
          <span class="sc-update-badge" id="sc-update-badge"></span>
        </div>
        <div class="sc-status-box">
          <div class="sc-status-title">Link đã thu thập</div>
          <div class="sc-status-count" id="sc-link-count">0</div>
          <div class="sc-cat-line" id="sc-cat-line"></div>
        </div>

        <label class="sc-option-box">
          <input type="checkbox" id="sc-auto-page-cb" ${autoPage ? 'checked' : ''}>
          <span>Tự động chuyển trang</span>
        </label>

        <label class="sc-option-box">
          <input type="checkbox" id="sc-top-sales-cb" ${topSalesSort ? 'checked' : ''}>
          <span>Cào theo Top Sales</span>
        </label>

        <div class="sc-option-box" style="justify-content:space-between;cursor:default;">
          <label style="display:flex;align-items:center;gap:8px;cursor:pointer;">
            <input type="checkbox" id="sc-sold-filter-cb" ${soldFilterEnabled ? 'checked' : ''}>
            <span>Lọc lượt bán tối thiểu</span>
          </label>
          <input type="number" id="sc-sold-filter-min" min="0" step="1" value="${soldMinThreshold}"
            style="width:70px;padding:3px 6px;border:1px solid #ccc;border-radius:4px;" ${soldFilterEnabled ? '' : 'disabled'}>
        </div>

        <div class="sc-btn-group">
          <button class="sc-btn sc-btn-start" id="sc-start-btn">Cào tiêu chuẩn</button>
          <button class="sc-btn sc-btn-stop" id="sc-stop-btn" style="display:none;">Stop</button>
        </div>
        <button class="sc-btn sc-btn-top" id="sc-scroll-top-btn">⬆ Lên đầu trang</button>
        <button class="sc-btn sc-btn-clear" id="sc-clear-btn">Xóa bộ nhớ đệm</button>
        <div class="sc-db-title">Cào theo từ khoá (mỗi dòng 1 từ khoá):</div>
        <select id="sc-keyword-cat-sel" class="sc-keywords-select">
          <option value="">-- Đang tải danh mục... --</option>
        </select>
        <textarea id="sc-keywords-ta" class="sc-keywords-ta" rows="4" placeholder="VD:&#10;ao thun nam&#10;quan jean nu"></textarea>
        <div class="sc-keyword-note" id="sc-keyword-note"></div>
        <button class="sc-btn sc-btn-top" id="sc-keyword-start-btn" style="margin-bottom:6px;" disabled>🔍 Bắt đầu cào theo từ khoá</button>
        <button class="sc-btn sc-btn-clear" id="sc-keyword-reset-btn" style="margin-bottom:10px;">↺ Đặt lại tiến trình từ khoá</button>
        <div class="sc-export-title">Xuất dữ liệu:</div>
        <div class="sc-export-group">
          <button class="sc-btn sc-btn-export" id="sc-exp-txt">.TXT</button>
          <button class="sc-btn sc-btn-export" id="sc-exp-json">.JSON</button>
          <button class="sc-btn sc-btn-export" id="sc-exp-csv">.CSV (Excel)</button>
        </div>
        <div class="sc-db-title">Đẩy vào DB (root, dashboard affiliate offer scraper):</div>
        <input type="text" class="sc-db-url" id="sc-db-url" placeholder="URL server, vd http://127.0.0.1:8877" value="${getServerUrl()}">
        <button class="sc-btn sc-btn-push" id="sc-push-db-btn">Đẩy vào DB</button>
      </div>
    `;

    document.body.appendChild(container);

    // Gán sự kiện checkbox auto page
    const autoPageCb = document.getElementById('sc-auto-page-cb');
    autoPageCb.addEventListener('change', (e) => {
      setAutoPageState(e.target.checked);
    });

    const topSalesCb = document.getElementById('sc-top-sales-cb');
    topSalesCb.addEventListener('change', (e) => {
      setTopSalesSortState(e.target.checked);
    });

    const soldFilterCb = document.getElementById('sc-sold-filter-cb');
    const soldFilterMinInput = document.getElementById('sc-sold-filter-min');
    soldFilterCb.addEventListener('change', (e) => {
      setSoldFilterEnabledState(e.target.checked);
      soldFilterMinInput.disabled = !e.target.checked;
    });
    soldFilterMinInput.addEventListener('change', (e) => {
      setSoldMinThreshold(e.target.value);
      e.target.value = soldMinThreshold; // phan anh lai gia tri da chuan hoa (vd am/rong -> mac dinh)
    });

    // Tu khoa: nap lai text da luu, tu dong khu trung luc RAI khoi o nhap (blur) - khong
    // khu trung ngay tren tung phim go (input) de tranh nhay con tro giua luc dang go.
    const keywordsTa = document.getElementById('sc-keywords-ta');
    const keywordNote = document.getElementById('sc-keyword-note');
    keywordsTa.value = localStorage.getItem(KEYWORDS_KEY) || '';
    keywordsTa.addEventListener('blur', () => {
      const { text, removed } = dedupeKeywordsText(keywordsTa.value);
      keywordsTa.value = text;
      saveKeywordsText(text);
      keywordNote.textContent = removed > 0 ? `Đã tự động loại ${removed} từ khoá trùng.` : '';
    });

    // Dropdown danh muc cho tu khoa - nap tu server (dung cho market cua CHINH tab nay),
    // ghi nho lua chon lan truoc (getKeywordCatSelection()) de khong phai chon lai moi lan.
    const keywordCatSel = document.getElementById('sc-keyword-cat-sel');
    loadKeywordCategories();
    keywordCatSel.addEventListener('change', () => {
      const catId = keywordCatSel.value;
      const chosen = keywordCategories.find((c) => String(c.cat_id) === catId);
      setKeywordCatSelection(catId, chosen ? chosen.cat_name : '');
    });

    document.getElementById('sc-keyword-start-btn').addEventListener('click', () => {
      const { text, list, removed } = dedupeKeywordsText(keywordsTa.value);
      keywordsTa.value = text;
      saveKeywordsText(text);
      keywordNote.textContent = removed > 0 ? `Đã tự động loại ${removed} từ khoá trùng.` : '';
      if (list.length === 0) {
        alert('Chưa nhập từ khoá nào (mỗi dòng 1 từ khoá).');
        return;
      }
      // Bat buoc chon danh muc TRUOC khi cao (yeu cau nguoi dung 2026-08-30: khong muon link
      // cao tu tu khoa bi "mo coi" khong co danh muc) - CHI mien neu market nay hoan toan
      // chua co danh sach danh muc nao (dropdown rong, khong co gi de chon - xem
      // loadKeywordCategories()).
      const { catId, catName } = getKeywordCatSelection();
      if (keywordCategories.length > 0 && !catId) {
        alert('Vui lòng chọn 1 danh mục cho từ khoá trước khi bắt đầu (tránh link bị "mồ côi" không có danh mục).');
        return;
      }
      // Gan danh muc da chon (hoac null neu market khong co danh sach danh muc nao) cho
      // TOAN BO link se cao trong lan chay nay - ghi de session cat_id/cat_name CU (neu co,
      // vd tu lan cao danh muc truoc do) de khong gan nham (xem setSessionCatId()).
      setSessionCatId(catId || null);
      if (catId) setSessionCatName(catName);
      setKeywordModeState(true);
      setKeywordIndex(0);
      setRunningState(true);
      updateUI();
      console.log(`[Shopee Collector] Bắt đầu cào theo ${list.length} từ khoá (danh mục: ${catName || 'không có'}), bắt đầu từ: "${list[0]}"`);
      window.location.href = buildSearchUrl(list[0]);
    });

    // "Dat lai tien trinh tu khoa" - xoa trang thai "dang o tu khoa thu may" (KHONG xoa
    // danh sach tu khoa/link da thu thap) - dung khi nghi ngo tien trinh bi sai lech (vd
    // sau 1 lan dong tab giua chung, hoac tab khac vo tinh lam nhay tu khoa - xem ghi chu
    // NEXT_PAGE_RETRY_COUNT), de lan bam "Bat dau cao theo tu khoa" tiep theo chac chan
    // chay lai TU DAU danh sach (du nut Start cung da tu dat lai index=0 moi lan bam, nut
    // nay them 1 lop an tam thu cong + dung dung han ca khi khong dinh bam Start ngay).
    document.getElementById('sc-keyword-reset-btn').addEventListener('click', () => {
      setKeywordIndex(0);
      setKeywordModeState(false);
      alert('Đã đặt lại tiến trình từ khoá về từ khoá đầu tiên. Bấm "Bắt đầu cào theo từ khoá" để chạy lại (danh sách từ khoá và link đã thu thập không bị ảnh hưởng).');
    });

    // Gán sự kiện nút
    // Chi phat hien lai cat_id luc bam Start THAT (khong phai luc startCollecting() tu goi
    // lai de tiep tuc sau auto-page/resume) - xem detectCatIdFromUrl()/setSessionCatId().
    document.getElementById('sc-start-btn').addEventListener('click', () => {
      const catId = detectCatIdFromUrl();
      setSessionCatId(catId); // xoa ten cu (thuoc cat_id truoc do), xem setSessionCatId()
      // Hien NGAY ten doan tu slug URL (khong can cho server) - resolveCatName() se ghi de
      // bang ten "dep" hon tu cat-db NEU co, con khong thi giu nguyen ten nay.
      if (catId) setSessionCatName(detectCatNameFromUrl());
      updateUI();
      resolveCatName(catId);
      startWithOptionalTopSalesSort();
    });
    document.getElementById('sc-stop-btn').addEventListener('click', () => {
      // Bam Stop nghia la dung HET, ke ca dang o giua chung che do "Cao theo tu khoa" -
      // khong de advanceToNextKeywordOrStop() tiep tuc dieu huong sang tu khoa ke tiep.
      setKeywordModeState(false);
      stopCollecting();
    });
    document.getElementById('sc-scroll-top-btn').addEventListener('click', () => {
      window.scrollTo({ top: 0, behavior: 'smooth' });
    });
    document.getElementById('sc-clear-btn').addEventListener('click', clearStorage);
    document.getElementById('sc-exp-txt').addEventListener('click', exportTXT);
    document.getElementById('sc-exp-json').addEventListener('click', exportJSON);
    document.getElementById('sc-exp-csv').addEventListener('click', exportCSV);
    document.getElementById('sc-db-url').addEventListener('change', (e) => setServerUrl(e.target.value.trim()));
    document.getElementById('sc-push-db-btn').addEventListener('click', pushToDb);
    document.getElementById('sc-check-update-btn').addEventListener('click', () => checkForUpdate(true));
    document.getElementById('sc-update-badge').addEventListener('click', openUpdatePage);

    // Toggle Ẩn/Hiện Panel
    const header = document.getElementById('shopee-collector-header');
    const body = document.getElementById('shopee-collector-body');
    const toggleBtn = document.getElementById('sc-toggle-btn');

    header.addEventListener('click', (e) => {
      if (e.target.closest('#sc-toggle-btn') || e.target === header || e.target.parentElement === header || e.target.closest('.sc-header-title')) {
        if (body.style.display === 'none') {
          body.style.display = 'block';
          toggleBtn.innerText = '▼';
        } else {
          body.style.display = 'none';
          toggleBtn.innerText = '▲';
        }
      }
    });

    updateUI();
    maybeAutoCheckForUpdate();

    // Bu lai cat_name neu trang vua duoc tai lai (vd sau auto-page) ma cat_id phien van con
    // nhung chua co ten hien thi (vd lan resolve truoc do loi mang) - khong goi lai neu da co
    // san ten, tranh goi API du thua moi lan chuyen trang.
    const pendingCatId = getSessionCatId();
    if (pendingCatId && !getSessionCatName()) {
      resolveCatName(pendingCatId);
    }

    // Tự động chạy tiếp nếu trước đó đang ở trạng thái IsRunning - xay ra sau 1 lan TAI LAI
    // TRANG THAT SU (vd F5 thu cong, hoac dieu huong sang tu khoa moi trong che do "Cao
    // theo tu khoa" - xem advanceToNextKeywordOrStop()/nut "Bat dau cao theo tu khoa").
    // Dung startWithOptionalTopSalesSort() (khong goi thang startCollecting()) de MOI trang
    // MOI nay cung duoc kiem tra/bam "Top Sales" lai neu tuy chon dang bat - can thiet cho
    // che do tu khoa vi moi tu khoa la 1 PHIEN tim kiem RIENG, KHONG giu nguyen tieu chi sap
    // xep nhu khi chuyen trang cung 1 danh muc qua SPA (xem goToNextPage(), truong hop do
    // KHONG di qua createUI() nen khong bi anh huong o day). Neu tuy chon dang TAT thi ham
    // nay chi goi thang startCollecting(), hoan toan giong hanh vi cu.
    if (isRunning) {
      setTimeout(() => {
        startWithOptionalTopSalesSort();
      }, 1500);
    }
  }

  // Cập nhật giao diện
  function updateUI() {
    const countEl = document.getElementById('sc-link-count');
    const startBtn = document.getElementById('sc-start-btn');
    const stopBtn = document.getElementById('sc-stop-btn');
    const indicator = document.getElementById('sc-status-indicator');

    if (countEl) {
      const links = getStoredLinks();
      countEl.innerText = links.length;
    }

    const catLineEl = document.getElementById('sc-cat-line');
    if (catLineEl) {
      let sessionMsg;
      if (getKeywordModeState()) {
        // Che do "Cao theo tu khoa" - hien tu khoa dang xu ly thay vi danh muc (tim kiem
        // khong co danh muc, xem setSessionCatId(null) luc bam nut bat dau).
        const list = getKeywordList();
        const idx = getKeywordIndex();
        sessionMsg = `Đang cào từ khoá: <b>${list[idx] || '?'}</b> (${idx + 1}/${list.length})`;
      } else {
        const sessionCatId = getSessionCatId();
        const sessionCatName = getSessionCatName();
        // Uu tien hien ten (vd "Pets") - fallback ve cat_id tho neu chua tra duoc ten (dang
        // cho resolveCatName() phan hoi, hoac khong tim thay ten trong cat-db).
        sessionMsg = sessionCatId
          ? `Đang cào: <b>${sessionCatName || 'cat_id ' + sessionCatId}</b>`
          : 'Đang cào: <b>không có danh mục</b>';
      }
      const links = getStoredLinks();
      const distinctCount = new Set(links.map((item) => item.catId || null)).size;
      const totalMsg = links.length > 0 ? ` · Đã lưu <b>${distinctCount}</b> danh mục` : '';
      catLineEl.innerHTML = sessionMsg + totalMsg;
    }

    if (startBtn && stopBtn && indicator) {
      if (isRunning) {
        startBtn.style.display = 'none';
        stopBtn.style.display = 'block';
        indicator.className = 'sc-badge sc-badge-active';
      } else {
        startBtn.style.display = 'block';
        stopBtn.style.display = 'none';
        indicator.className = 'sc-badge sc-badge-inactive';
      }
    }
  }

  // Khởi chạy khi DOM sẵn sàng
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', createUI);
  } else {
    createUI();
  }
})();
