#!/usr/bin/env node
'use strict';

/* Chay pool dang video qua Node.js thay vi vong lap fetch() trong trinh duyet - yeu cau nguoi
 * dung 2026-09-12 "chạy qua node js" sau khi do thuc te xac nhan: goi thang API post_next() qua
 * PowerShell (khong qua Chrome) o 50 request dong thoi hoan toan on (0 timeout/mat ket noi),
 * trong khi CUNG luong do qua tab Chrome that bi ket o buoc "Initial connection" (DevTools
 * Timing bao 2 phut) - nghi van do Chrome chia se lop mang voi ~43 process Chrome GPM
 * automation khac dang chay tren may, hoac gioi han ket noi/origin cua rieng Chrome. Node dung
 * client HTTP rieng, khong dung chung lop mang voi Chrome nen tranh duoc van de nay.
 *
 * Logic pool/claim/delay port GAN NHU NGUYEN VAN tu postVideoClaimReadyAccount()/
 * postVideoPoolWorker() trong templates/index.html (2 ham do von KHONG phu thuoc DOM) - CHI
 * khac o cho: (1) goi truc tiep qua fetch() cua Node thay vi qua nhieu VIDEO PORT round-robin
 * y het, (2) bao trang thai qua FILE JSON (statusFilePath) thay vi goi callback cap nhat UI
 * truc tiep, vi tien trinh nay chay doc lap, khong co quyen truy cap DOM cua dashboard.
 *
 * Dieu khien dung: dashboard (backend Python, xem /api/video_sources/<id>/node_pool/stop) GHI
 * 1 file rong tai stopFilePath - script nay POLL file do moi giay thay vi dua vao tin hieu OS
 * (SIGTERM qua taskkill tren Windows khong dang tin cay bang polling file don gian, cung mo
 * hinh voi cach dashboard cu dung session.stopRequested).
 *
 * Goi: node post_video_node_worker.js <duong-dan-file-config.json>
 */

const fs = require('fs');

const configPath = process.argv[2];
if (!configPath) {
  console.error('Thieu duong dan file config (argv[2])');
  process.exit(1);
}
// Bo BOM (U+FEFF) neu file config duoc ghi boi PowerShell 'Set-Content -Encoding utf8' (mac
// dinh Windows PowerShell 5.1 ghi kem BOM dau file) - JSON.parse() khong tu bo qua duoc ky tu
// nay, se bao loi "Unexpected token" ngay dong dau.
const config = JSON.parse(fs.readFileSync(configPath, 'utf8').replace(/^﻿/, ''));
const {
  mainOrigin, videoPorts, sourceId, accountIds, threads,
  perAccountTarget, minDelay, maxDelay, statusFilePath, stopFilePath,
  isAiGenerated,
} = config;

const REQUEST_TIMEOUT_MS = 200000; // dong bo voi API_TIMEOUT ben dashboard (xem templates/index.html)
const RAMP_WINDOW_MS = 10000; // rai deu thoi diem bat dau cac luong, dong bo voi POSTVIDEO_RAMP_WINDOW_MS

function sleepMs(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

let portRoundRobinIdx = 0;
function nextVideoPort() {
  if (!Array.isArray(videoPorts) || !videoPorts.length) return null;
  const port = videoPorts[portRoundRobinIdx % videoPorts.length];
  portRoundRobinIdx++;
  return port;
}

async function callPostNext(accountId) {
  const port = nextVideoPort();
  const url = port
    ? `http://127.0.0.1:${port}/api/video_sources/${sourceId}/post_next`
    : `${mainOrigin}/api/video_sources/${sourceId}/post_next`;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  try {
    const resp = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ account_ids: [accountId], is_ai_generated: !!isAiGenerated }),
      signal: controller.signal,
    });
    const json = await resp.json();
    if (!resp.ok) throw new Error(json.error || 'Loi khong xac dinh tu server');
    return json;
  } catch (e) {
    if (e.name === 'AbortError') throw new Error(`Server khong phan hoi sau ${REQUEST_TIMEOUT_MS / 1000}s (timeout).`);
    throw e;
  } finally {
    clearTimeout(timer);
  }
}

// accountState/accountStatus port nguyen y nghia tu accountState trong postVideoPoolWorker() -
// done/nextAt/active/stopped giu dung ban chat cooldown rieng tung tai khoan + dung han khi
// gap loi khong the khac phuc (unrecoverable).
const accountState = new Map(accountIds.map((id) => [id, { done: 0, nextAt: 0, active: false, stopped: false }]));
const accountStatus = new Map(accountIds.map((id) => [id, { text: '—', color: null }]));
const stats = { posted: 0, ok: 0, fail: 0 };
const logLines = [];
let stopRequested = false;

function log(msg) {
  const line = `[${new Date().toLocaleTimeString('vi-VN')}] ${msg}`;
  logLines.unshift(line);
  if (logLines.length > 300) logLines.length = 300;
  console.log(line);
}

function setStatus(accountId, text, color) {
  accountStatus.set(accountId, { text, color: color || null });
}

let writeTimer = null;
function scheduleWriteStatus() {
  // Debounce nhe (100ms) - hang chuc luong co the cung goi ham nay gan nhu dong thoi, gop lai
  // thanh 1 lan ghi file thay vi ghi lien tuc tung cai (tranh I/O thua, van du "gan real-time").
  if (writeTimer) return;
  writeTimer = setTimeout(() => {
    writeTimer = null;
    writeStatusNow();
  }, 100);
}

function writeStatusNow() {
  const payload = {
    running: !stopRequested,
    stats,
    accounts: Object.fromEntries(
      accountIds.map((id) => {
        const st = accountState.get(id);
        const s = accountStatus.get(id);
        return [id, { done: st.done, stopped: st.stopped, status: s.text, color: s.color }];
      })
    ),
    log: logLines.slice(0, 50),
    updatedAt: Date.now(),
  };
  try {
    fs.writeFileSync(statusFilePath, JSON.stringify(payload));
  } catch (e) {
    // im lang - lan ghi ke tiep se thu lai, 1 lan ghi loi khong duoc lam sap worker
  }
}

// Poll file "stop" moi giay - xem giai thich o dau file.
const stopPoll = setInterval(() => {
  if (!stopRequested && fs.existsSync(stopFilePath)) {
    stopRequested = true;
    log('Nhận tín hiệu Dừng từ dashboard.');
  }
}, 1000);

function claimReadyAccount() {
  const now = Date.now();
  let soonestAt = Infinity;
  let anyPending = false;
  let bestId = null;
  let bestNextAt = Infinity;
  for (const id of accountIds) {
    const st = accountState.get(id);
    if (st.done >= perAccountTarget || st.stopped) continue;
    anyPending = true;
    if (st.active) continue;
    if (st.nextAt <= now) {
      if (st.nextAt < bestNextAt) { bestNextAt = st.nextAt; bestId = id; }
    } else if (st.nextAt < soonestAt) {
      soonestAt = st.nextAt;
    }
  }
  if (bestId != null) {
    accountState.get(bestId).active = true;
    return { id: bestId, anyPending: true };
  }
  return { id: null, anyPending, soonestAt };
}

async function poolWorker(startDelayMs) {
  if (startDelayMs > 0) await sleepMs(startDelayMs);
  while (!stopRequested) {
    const claim = claimReadyAccount();
    if (!claim.anyPending) break;
    if (claim.id == null) {
      const waitMs = Number.isFinite(claim.soonestAt) ? Math.max(50, Math.min(claim.soonestAt - Date.now(), 1000)) : 1000;
      await sleepMs(waitMs);
      continue;
    }
    const accountId = claim.id;
    const st = accountState.get(accountId);
    setStatus(accountId, '⏳ Đang đăng...', null);
    scheduleWriteStatus();
    let res;
    try {
      res = await callPostNext(accountId);
    } catch (e) {
      log(`✗ LỖI GỌI SERVER (tài khoản ${accountId}): ${e.message} - 1 luồng dừng.`);
      st.active = false;
      scheduleWriteStatus();
      break;
    }
    if (res.done) { log('✓ Luồng dừng - nguồn đã hết video pending.'); st.active = false; break; }
    if (res.blocked) { log(`⚠ Luồng dừng: ${res.message}`); st.active = false; break; }
    if (res.retry) { st.active = false; await sleepMs(300); continue; }
    stats.posted++;
    if (res.success) stats.ok++; else stats.fail++;
    st.done++;
    st.active = false;
    const delaySec = minDelay + Math.random() * (maxDelay - minDelay);
    st.nextAt = Date.now() + delaySec * 1000;
    if (!res.success && res.unrecoverable) {
      st.stopped = true;
      log(`⛔ DỪNG tài khoản ${res.account_label} - lỗi không thể khắc phục (sẽ KHÔNG thử lại tài khoản này nữa): ${res.error}`);
      setStatus(accountId, `⛔ Đã dừng - ${String(res.error || '').slice(0, 60)}`, '--danger-text');
    } else {
      log(`${res.success ? '✓' : '✗'} ${res.sp_id} (${res.account_label}: ${st.done}/${perAccountTarget}) - ${res.success ? (res.video_link || 'post_id=' + res.post_id) : 'LỖI: ' + res.error} | còn ${res.pending_remaining} pending`);
      setStatus(accountId, res.success ? `✓ ${st.done}/${perAccountTarget}` : `✗ ${st.done}/${perAccountTarget} - Lỗi: ${String(res.error || '').slice(0, 60)}`, res.success ? '--success-text' : '--danger-text');
    }
    scheduleWriteStatus();
  }
}

async function main() {
  log(`Bắt đầu (Node) - ${accountIds.length} tài khoản dùng chung pool ${threads} luồng song song, MỖI tài khoản đăng tối đa ${perAccountTarget} video, nghỉ ${minDelay}-${maxDelay}s giữa 2 video cùng tài khoản.`);
  writeStatusNow();
  await Promise.all(
    Array.from({ length: threads }, (_, i) => poolWorker(Math.floor((i / threads) * RAMP_WINDOW_MS)))
  );
  clearInterval(stopPoll);
  stopRequested = true;
  log(`Đã dừng. Tổng kết phiên: ${stats.posted} video (${stats.ok} thành công, ${stats.fail} lỗi).`);
  writeStatusNow();
  process.exit(0);
}

main().catch((e) => {
  log(`LỖI NGHIÊM TRỌNG: ${e.message}`);
  stopRequested = true;
  writeStatusNow();
  process.exit(1);
});
