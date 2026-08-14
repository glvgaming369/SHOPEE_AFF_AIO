// ==UserScript==
// @name         Shopee Product Link Collector
// @namespace    http://tampermonkey.net/
// @version      1.4.0
// @description  Thu thập link sản phẩm Shopee tự động với tính năng cuộn trang thông minh, tự động chuyển trang SPA, tự nhận diện domain quốc gia, xuất dữ liệu và đẩy thẳng vào DB (root) của dashboard affiliate offer scraper.
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
// @connect      127.0.0.1
// @connect      localhost
// @updateURL    http://127.0.0.1:8877/userscripts/shopee_collector.user.js
// @downloadURL  http://127.0.0.1:8877/userscripts/shopee_collector.user.js
// ==/UserScript==

(function () {
  'use strict';

  const SCRIPT_VERSION = 'v1.4.0';
  const STORAGE_KEY = 'shopee_collected_links';
  const RUNNING_STATE_KEY = 'shopee_collector_is_running';
  const AUTO_PAGE_KEY = 'shopee_collector_auto_page';
  const SERVER_URL_KEY = 'shopee_collector_server_url';
  const SERVER_URL_DEFAULT = 'http://127.0.0.1:8877';

  let isRunning = false;
  let autoPage = false;
  let scrollInterval = null;
  let scanInterval = null;
  let isNavigating = false;

  let lastScrollY = -1;
  let sameScrollCount = 0;

  // Lấy danh sách link đã lưu
  function getStoredLinks() {
    try {
      const data = localStorage.getItem(STORAGE_KEY);
      return data ? JSON.parse(data) : [];
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

  // Quét các link trên trang hiện tại
  function scanLinks() {
    const anchors = Array.from(document.querySelectorAll('a[href*="-i."]'));
    let currentLinks = getStoredLinks();
    let initialCount = currentLinks.length;

    anchors.forEach((a) => {
      const rawHref = a.getAttribute('href');
      const cleanUrl = normalizeShopeeUrl(rawHref);
      if (cleanUrl && !currentLinks.includes(cleanUrl)) {
        currentLinks.push(cleanUrl);
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
    const content = links.join('\n');
    downloadFile(content, 'shopee_links.txt', 'text/plain');
  }

  // Xuất file JSON
  function exportJSON() {
    const links = getStoredLinks();
    if (links.length === 0) return alert('Chưa có link nào được thu thập!');
    const content = JSON.stringify(links, null, 2);
    downloadFile(content, 'shopee_links.json', 'application/json');
  }

  // Xuất file CSV (mở được bằng Excel / XLSX)
  function exportCSV() {
    const links = getStoredLinks();
    if (links.length === 0) return alert('Chưa có link nào được thu thập!');
    const content = '﻿URL\n' + links.map((l) => `"${l}"`).join('\n');
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

  // Day toan bo link da thu thap thang vao DB (bang 'products', lam root) qua
  // affiliate_scrape_server.py - KHAC origin voi shopee.* nen bat buoc GM_xmlhttpRequest
  // de ne CORS (server khong bat CORS, xem affiliate_scrape_server.py).
  function pushToDb() {
    const links = getStoredLinks();
    if (links.length === 0) return alert('Chưa có link nào để đẩy vào DB!');
    const serverUrl = getServerUrl();
    GM_xmlhttpRequest({
      method: 'POST',
      url: serverUrl + '/api/roots/import',
      headers: { 'Content-Type': 'application/json' },
      data: JSON.stringify({ links }),
      onload: (resp) => {
        try {
          const json = JSON.parse(resp.responseText);
          if (resp.status >= 200 && resp.status < 300) {
            alert(`Đã đẩy vào DB: ${json.added} root mới (${links.length - json.added} đã có sẵn/không hợp lệ).`);
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
        <div class="sc-status-box">
          <div class="sc-status-title">Link đã thu thập</div>
          <div class="sc-status-count" id="sc-link-count">0</div>
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
    document.getElementById('sc-start-btn').addEventListener('click', startCollecting);
    document.getElementById('sc-stop-btn').addEventListener('click', stopCollecting);
    document.getElementById('sc-clear-btn').addEventListener('click', clearStorage);
    document.getElementById('sc-exp-txt').addEventListener('click', exportTXT);
    document.getElementById('sc-exp-json').addEventListener('click', exportJSON);
    document.getElementById('sc-exp-csv').addEventListener('click', exportCSV);
    document.getElementById('sc-db-url').addEventListener('change', (e) => setServerUrl(e.target.value.trim()));
    document.getElementById('sc-push-db-btn').addEventListener('click', pushToDb);

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
