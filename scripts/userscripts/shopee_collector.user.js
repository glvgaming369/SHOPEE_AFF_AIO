// ==UserScript==
// @name         Shopee Product Link Collector
// @namespace    http://tampermonkey.net/
// @version      1.8.1
// @description  Thu thập link sản phẩm Shopee tự động với tính năng cuộn trang thông minh, tự động chuyển trang SPA, tự nhận diện domain quốc gia, xuất dữ liệu và đẩy thẳng vào DB (root) của dashboard affiliate offer scraper. Gán cat_id riêng cho từng link ngay lúc cào, đảm bảo đúng danh mục kể cả khi cào nhiều danh mục trước khi đẩy vào DB.
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

  const SCRIPT_VERSION = 'v1.8.1';
  const STORAGE_KEY = 'shopee_collected_links';
  const RUNNING_STATE_KEY = 'shopee_collector_is_running';
  const AUTO_PAGE_KEY = 'shopee_collector_auto_page';
  const SERVER_URL_KEY = 'shopee_collector_server_url';
  const CAT_ID_KEY = 'shopee_collector_cat_id';
  const CAT_NAME_KEY = 'shopee_collector_cat_name';
  const SERVER_URL_DEFAULT = 'http://127.0.0.1:8877';
  const OWN_SCRIPT_FILE = 'shopee_collector.user.js';
  const LAST_UPDATE_CHECK_KEY = 'shopee_collector_last_update_check';
  const UPDATE_CHECK_INTERVAL_MS = 6 * 60 * 60 * 1000; // 6 gio - tranh spam server moi lan load trang

  let isRunning = false;
  let autoPage = false;
  let scrollInterval = null;
  let scanInterval = null;
  let isNavigating = false;

  let lastScrollY = -1;
  let sameScrollCount = 0;

  // Lấy danh sách link đã lưu - moi phan tu la {url, catId}, catId gan NGAY luc quet
  // (scanLinks) theo danh muc dang cao tai thoi diem do, KHONG con dung 1 cat_id chung cho
  // ca phien nua - xem ghi chu o pushToDb(). Tu dong migrate dinh dang cu (mang string thuan,
  // ban script <1.7.0) sang {url, catId: null} khi doc, tranh vo du lieu dang cao do.
  function getStoredLinks() {
    try {
      const data = localStorage.getItem(STORAGE_KEY);
      const parsed = data ? JSON.parse(data) : [];
      return parsed.map((item) => (typeof item === 'string' ? { url: item, catId: null } : item));
    } catch (e) {
      return [];
    }
  }

  // Lưu danh sách link
  function saveStoredLinks(links) {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(links));
    updateUI();
  }

  // Lấy trạng thái cài đặt
  function loadSettings() {
    isRunning = localStorage.getItem(RUNNING_STATE_KEY) === 'true';
    autoPage = localStorage.getItem(AUTO_PAGE_KEY) === 'true';
  }

  function setRunningState(state) {
    isRunning = state;
    localStorage.setItem(RUNNING_STATE_KEY, state ? 'true' : 'false');
  }

  function setAutoPageState(state) {
    autoPage = state;
    localStorage.setItem(AUTO_PAGE_KEY, state ? 'true' : 'false');
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
  function scanLinks() {
    const anchors = Array.from(document.querySelectorAll('a[href*="-i."]'));
    let currentLinks = getStoredLinks();
    let initialCount = currentLinks.length;
    const existingUrls = new Set(currentLinks.map((item) => item.url));
    const catId = getSessionCatId();

    anchors.forEach((a) => {
      const rawHref = a.getAttribute('href');
      const cleanUrl = normalizeShopeeUrl(rawHref);
      if (cleanUrl && !existingUrls.has(cleanUrl)) {
        currentLinks.push({ url: cleanUrl, catId });
        existingUrls.add(cleanUrl);
      }
    });

    if (currentLinks.length !== initialCount) {
      saveStoredLinks(currentLinks);
    }
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

  // Chuyển sang trang tiếp theo và reset trạng thái cuộn
  function goToNextPage() {
    if (isNavigating) return;
    const nextBtn = getNextPageButton();

    if (nextBtn) {
      isNavigating = true;
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
    } else {
      console.log('[Shopee Collector] Không tìm thấy nút Next Page hoặc đã ở trang cuối cùng!');
      stopCollecting();
    }
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

        if (autoPage) {
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

        <div class="sc-btn-group">
          <button class="sc-btn sc-btn-start" id="sc-start-btn">Start</button>
          <button class="sc-btn sc-btn-stop" id="sc-stop-btn" style="display:none;">Stop</button>
        </div>
        <button class="sc-btn sc-btn-clear" id="sc-clear-btn">Xóa bộ nhớ đệm</button>
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
      startCollecting();
    });
    document.getElementById('sc-stop-btn').addEventListener('click', stopCollecting);
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

    // Tự động chạy tiếp nếu trước đó đang ở trạng thái IsRunning (chuyển trang vừa xảy ra)
    if (isRunning) {
      setTimeout(() => {
        startCollecting();
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
      const sessionCatId = getSessionCatId();
      const sessionCatName = getSessionCatName();
      // Uu tien hien ten (vd "Pets") - fallback ve cat_id tho neu chua tra duoc ten (dang
      // cho resolveCatName() phan hoi, hoac khong tim thay ten trong cat-db).
      const sessionMsg = sessionCatId
        ? `Đang cào: <b>${sessionCatName || 'cat_id ' + sessionCatId}</b>`
        : 'Đang cào: <b>không có danh mục</b>';
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
