// =============================================================================
// shopee_captcha_solver.mjs — module giai Shopee slide-captcha, san sang tai su dung
// =============================================================================
//
// CACH DUNG NHANH:
//
//   import { solveShopeeCaptcha } from './shopee_captcha_solver.mjs';
//
//   const result = await solveShopeeCaptcha({
//     port: 62505,                    // CDP remote-debugging port cua tab (tu antidetect.py start_profile)
//     url: 'https://shopee.ph/',      // navigate toi day de kich hoat gate; TRUYEN '' de GAN VAO
//                                     // TAB DANG CO SAN (khi captcha da hien san, khong navigate)
//     maxAttempts: 6,                 // so lan thu toi da (Shopee tu sinh captcha moi sau moi fail)
//   });
//   // result = { pass, attempts: [...], lastHref }
//
// YEU CAU MOI TRUONG:
//   - 1 tab Chrome that (qua GPM/antidetect hoac bat ky Chromium co --remote-debugging-port)
//     DANG o mien shopee.* (hoac se navigate toi 1 URL shopee.*).
//   - BAT BUOC bien moi truong OMOCAPTCHA_API_KEY (vd `setx OMOCAPTCHA_API_KEY "..."` tren
//     Windows) - file nay duoc PUSH LEN GIT (khac phan con lai cua re_work/, xem .gitignore),
//     nen KHONG duoc hardcode key that o day (2026-09-12: da go DEFAULT_OMO_KEY hardcode cu vi
//     suyt bi commit len GitHub). thieu bien nay se khien solveShopeeCaptcha() throw ngay -
//     cdp_login_shopee.mjs.trySolveCaptcha() da bat loi nay va coi nhu "khong giai duoc"
//     (khong lam crash tien trinh chinh).
//
// NHUNG DIEU DA HOC DUOC (**QUAN TRONG** - doc truoc khi sua doi logic ben duoi):
//
//   1. captcha_type CUA SHOPEE (5/10/11...) KHONG lien quan gi toi `typeCaptcha` can gui cho
//      omocaptcha. Da doc duoc chinh source cua omocaptcha's own browser extension
//      (shopee-content.bundle.js) va xac nhan: voi widget dang CANVAS (dung loai Shopee dung
//      cho type 5/10), LUON dung typeCaptcha:'rotate' bat ke captcha_type Shopee tra ve la gi.
//
//   2. Toa do `point.x` omocaptcha tra ve la vi tri TUYET DOI mong muon cua TAM manh ghep
//      (khong phai canh trai). Manh ghep bat dau o translateX=0 (canh trai khop x=0 cua bg
//      image), nen: translateX_can_dat = point.x - pieceWidth/2. THIEU buoc tru nay se gay
//      KEO QUA DA mot luong co dinh = pieceWidth/2 (da xac nhan qua nguoi dung quan sat truc
//      tiep: "kéo lệch quá vị trí đúng" truoc khi fix, "chỉ lệch 1-2px" (binh thuong) sau fix).
//
//   3. TI LE "handle keo bao nhieu px -> manh ghep di chuyen bao nhieu px" KHONG CO DINH giua
//      cac captcha (do thuc nghiem: 1.6071 va 1.0165 o 2 lan khac nhau tren CUNG 1 loai
//      widget) - rat co the la co che chong bot co y (randomize he so nhay). VI VAY khong
//      dung cong thuc scale co dinh (dragPx = x * usablePx/natW) - PHAI dung KEO CLOSED-LOOP
//      (xem closed_loop_dom_drag.mjs): vua keo vua doc lai translateX THAT cua manh ghep (qua
//      CSS transform style - KHONG phai canvas pixel, vi canvas TU NHIEU/animate lien tuc du
//      khong thao tac gi, da xac nhan qua test_noop_refresh.mjs) de tu hieu chinh toi khi hoi
//      tu ve dung target.
//
//   4. KHONG duoc goi CDP `Runtime.enable` - day la ky thuat chong-detect automation da biet
//      rong rai (Puppeteer/Playwright-stealth): bat domain Runtime gay side-effect do duoc
//      qua timing trong V8 (anh huong lich trinh Promise/microtask), la 1 vector cac SDK
//      chong-bot (vd "Sense" SDK cua Shopee, xem RE_PLAN.md/captcha_findings.md) co the dung
//      de phat hien tu dong hoa qua CDP. Da xac nhan qua thuc te: SAU KHI bo Runtime.enable +
//      tang mat do mousemove, ty le pass tang vot (~4-6% -> ~40-100% tren profile co lich su).
//      `Runtime.evaluate` VAN hoat dong binh thuong ma KHONG can goi enable truoc.
//
//   5. Mousemove can MAT DO CAO (moi 4-10ms, khong phai 15-40ms) de giong tan so bao cao cua
//      chuot phan cung that hon (~125-1000Hz).
//
//   6. Kiem tra pass/fail qua `location.href` PHAI POLL nhieu lan (khong doc 1 lan duy nhat
//      sau 1 khoang cho co dinh) - vi sau khi captcha duoc CHAP NHAN, trang chuyen tiep qua
//      1 buoc trung gian "/verify/traffic" TRUOC KHI ve dich cuoi cung, co the mat vai giay.
//      Doc href qua som se bat nham luc dang o "/verify/traffic" va bao FAIL SAI cho 1 lan
//      PASS THAT. Nguoc lai, khong nen doi qua lau (vd 8s) cho truong hop FAIL that (href
//      khong bao gio doi) - da giam xuong con ~3s toi da.
//
//   7. captcha_type 11 ("Drag the missing piece to restore the puzzle") dung KIEN TRUC KHAC
//      HAN: TOAN BO (nen + manh ghep + handle) ve chung trong 1 canvas DUY NHAT, KHONG co DOM
//      rieng (khong co [style*="translateX"]/[style*="translateY"]) nhu type 5/10. Pipeline
//      HIEN TAI CHUA HO TRO giai truc tiep loai nay (findWidgetGeometry/closedLoopDrag deu
//      dua tren DOM, se that bai voi 'dom_not_found'). Khi gap, code TU DONG dieu huong VE
//      TRANG CHU (navigate, KHONG PHAI reload - Page.reload() giu nguyen anti_bot_tracking_id
//      nen se tra ve DUNG type 11 lai, da xac nhan qua thuc te 10 lan lien tiep) de xin
//      tracking_id/gate MOI, hy vong nhan duoc type 5/10 ma pipeline da ho tro tot.
//
//   8. YEU TO "PROFILE TRUST": profile/tai khoan da co lich su hoat dong that (vd cac profile
//      "SHOPEE 00X" hoac profile da dung nhieu trong phien) co ty le pass CAO HON HAN (gan
//      100% khi gap type 5/10) so voi profile hoan toan moi tao (~8-10%) DU CUNG 1 ky thuat,
//      DO CHINH XAC <2px NHU NHAU o ca 2 loai profile. Neu can ty le pass on dinh, uu tien
//      dung profile da co lich su thay vi profile vua tao.
//
//   9. Truyen `--url ""` (chuoi rong) de GAN VAO TAB HIEN CO (khong navigate) can parse argv
//      dung `!== undefined` chu KHONG phai truthiness - chuoi rong la falsy trong JS, kiem
//      tra `x ? x : d` se sai lam roi ve default thay vi nhan chuoi rong.
//
// FILE LIEN QUAN:
//   - closed_loop_dom_drag.mjs : logic keo closed-loop chi tiet (import lai o day).
//   - solve_live.mjs           : CLI wrapper mong, dung module nay, de test nhanh tu dong lenh.
//   - solve_results.jsonl      : log JSONL 1 dong/lan thu (pass lan fail), gom du lieu qua
//                                nhieu lan chay de phan tich thong ke sau nay.
//   - captcha_findings.md      : nhat ky dieu tra day du (boi canh, cac huong da thu, ket luan).
//
// =============================================================================

import { appendFileSync } from 'node:fs';
import { closedLoopDrag } from './closed_loop_dom_drag.mjs';

const RESULTS_LOG_DEFAULT = 'D:/Shopee_PH/re_work/captcha_re/solve_results.jsonl';
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// -----------------------------------------------------------------------------
// CDP WebSocket helper toi gian (khong dung Puppeteer/Playwright - raw JSON-RPC qua
// WebSocket global co san trong Node 22+).
// -----------------------------------------------------------------------------
export function connectCDP(wsUrl) {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(wsUrl); const pending = new Map(); let id = 0;
    ws.onopen = () => resolve({
      send(m, p = {}) { return new Promise((res, rej) => { const mid = ++id; pending.set(mid, { res, rej }); ws.send(JSON.stringify({ id: mid, method: m, params: p })); }); },
      close() { try { ws.close(); } catch (e) {} },
    });
    ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.id && pending.has(msg.id)) { const p = pending.get(msg.id); pending.delete(msg.id); msg.error ? p.rej(new Error(msg.error.message)) : p.res(msg.result); }
    };
    ws.onerror = () => reject(new Error('ws err'));
  });
}

/** Tim tab "page" that thuoc mien shopee.* qua CDP /json/list - tranh chon nham
 * devtools://Ichrome://newtab/ khi co nhieu tab "page" cung luc. */
export async function findShopeeTab(port) {
  const list = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
  return list.find((x) => x.type === 'page' && /shopee/i.test(x.url)) || list.find((x) => x.type === 'page');
}

// -----------------------------------------------------------------------------
// HOOK bat captcha_body: ghi de JSON.parse de bat response cua get_config/generate
// (chua bg_img/puzzle_img) - hoat dong voi MOI shape response, khong doan ten field.
// -----------------------------------------------------------------------------
export const CAPTURE_HOOK = `(() => {
  if (window.__CAPT_ARMED) return; window.__CAPT_ARMED = true;
  window.__DEC = null;
  const looks = (o) => { try { return /captcha_body|bg_img|puzzle_img/.test(typeof o==='string'?o:JSON.stringify(o)); } catch(e){ return false; } };
  const oParse = JSON.parse;
  JSON.parse = function(t){ const r = oParse.apply(this, arguments); try{ if(looks(r)) window.__DEC = { ...r, __capturedAt: Date.now(), __href: location.href }; }catch(e){} return r; };
})();`;

// -----------------------------------------------------------------------------
// Tim canvas nen + track + handle BANG DAC DIEM HINH HOC (khong hardcode class/id -
// CSS module class doi giua cac lan build cua Shopee, khong co class/id on dinh nao
// chua tu khoa slider/drag/handle).
// -----------------------------------------------------------------------------
const FIND_GEOMETRY_EXPR = `(() => {
  let all = [];
  function walk(root){ let els; try{els=root.querySelectorAll('*');}catch(e){return;} for(const el of els){ if(el.shadowRoot) walk(el.shadowRoot); const r=el.getBoundingClientRect(); if(r.width>0&&r.height>0) all.push({el, tag:el.tagName, w:r.width, h:r.height, x:r.left, y:r.top}); } }
  walk(document);
  const bgCand = all.filter(o => (o.tag==='CANVAS'||o.tag==='IMG') && o.w>150 && o.h>80);
  bgCand.sort((a,b)=> a.w-b.w);
  const bg = bgCand[0];
  if (!bg) return JSON.stringify({ bg:null, track:null, handle:null });
  let natW = bg.tag==='IMG' ? bg.el.naturalWidth : bg.el.width;
  const sceneCanvas = bgCand.find(o => o!==bg && o.tag==='CANVAS' && Math.abs(o.x-bg.x)<6 && Math.abs(o.y-bg.y)<6 && o.el.width>natW);
  if (sceneCanvas) natW = sceneCanvas.el.width;
  const trackCand = all.filter(o => o.tag==='DIV' && o.y > bg.y+bg.h-10 && o.y < bg.y+bg.h+250 && o.w > bg.w*0.6 && o.w < bg.w*1.4 && o.h>=30 && o.h<=70);
  trackCand.sort((a,b)=> a.y-b.y);
  const track = trackCand.find(t => {
    const inside = all.filter(o => o.x>=t.x-2 && o.x<t.x+t.w && o.y>=t.y-2 && o.y<t.y+t.h+2 && Math.abs(o.w-o.h)<8 && o.w>=32 && o.w<=55);
    return inside.length>0;
  }) || trackCand[0];
  let handle = null;
  if (track) {
    const inside = all.filter(o => o.x>=track.x-5 && o.x<track.x+track.w && o.y>=track.y-5 && o.y<track.y+track.h+5 && Math.abs(o.w-o.h)<8 && o.w>=32 && o.w<=55);
    inside.sort((a,b)=> a.x-b.x);
    handle = inside[0];
  }
  return JSON.stringify({
    bg: { w:Math.round(bg.w), h:Math.round(bg.h), x:Math.round(bg.x), y:Math.round(bg.y), natW },
    track: track?{w:Math.round(track.w),h:Math.round(track.h),x:Math.round(track.x),y:Math.round(track.y)}:null,
    handle: handle?{w:Math.round(handle.w),h:Math.round(handle.h),x:Math.round(handle.x),y:Math.round(handle.y),cx:Math.round(handle.x+handle.w/2),cy:Math.round(handle.y+handle.h/2)}:null,
  });
})();`;

export async function findWidgetGeometry(cdp) {
  const r = await cdp.send('Runtime.evaluate', { expression: FIND_GEOMETRY_EXPR, returnByValue: true });
  return JSON.parse(r.result.value);
}

// -----------------------------------------------------------------------------
// omocaptcha API client toi gian - LUON dung typeCaptcha:'rotate' cho widget dang canvas
// (xem ghi chu #1 o dau file).
// -----------------------------------------------------------------------------
async function omoCreateAndPoll(task, apiKey) {
  const c = await (await fetch('https://api.omocaptcha.com/v2/createTask', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ clientKey: apiKey, task }) })).json();
  if (c.errorId || !c.taskId) throw new Error('createTask: ' + JSON.stringify(c));
  for (let i = 0; i < 12; i++) {
    const r = await (await fetch('https://api.omocaptcha.com/v2/getTaskResult', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ clientKey: apiKey, taskId: c.taskId }) })).json();
    if (r.status === 'ready') return r;
    if (r.status === 'fail' || r.errorId) throw new Error('FAIL:' + JSON.stringify(r));
    await sleep(800);
  }
  throw new Error('omo timeout');
}

export async function omoSolvePuzzle(puzzleB64, bgB64, apiKey) {
  const strip = (s) => s.startsWith('data:') ? s.split(',', 2)[1] : s;
  const task = { type: 'ShopeeSliderWebTask', imageBase64s: [strip(puzzleB64), strip(bgB64)], typeCaptcha: 'rotate' };
  const r = await omoCreateAndPoll(task, apiKey);
  const p = (r.solution && (r.solution.point || r.solution.end)) || {};
  return { x: p.x, y: p.y };
}

// -----------------------------------------------------------------------------
// 1 LAN THU day du: doi captcha_body MOI -> goi omocaptcha + do DOM song song -> re-check
// (captcha co the tu lam moi trong luc dang tinh) -> keo closed-loop -> poll href de biet
// pass/fail.
// -----------------------------------------------------------------------------
async function computePlan(cdp, dec, apiKey) {
  const body = (dec.data && dec.data.captcha_body) || dec.captcha_body;
  const [omoRes, geomRaw] = await Promise.all([
    omoSolvePuzzle(body.puzzle_img, body.bg_img, apiKey),
    findWidgetGeometry(cdp),
  ]);
  return { dec, omoRes, geometry: geomRaw };
}

export async function attemptOnce(cdp, { waitFromTs, apiKey, log = () => {}, logResultPath }) {
  const logResult = (obj) => { if (!logResultPath) return; try { appendFileSync(logResultPath, JSON.stringify({ ts: Date.now(), ...obj }) + '\n'); } catch (e) {} };

  let dec = null;
  for (let i = 0; i < 40; i++) {
    await sleep(500);
    const r = await cdp.send('Runtime.evaluate', { expression: 'window.__DEC && JSON.stringify(window.__DEC)', returnByValue: true });
    if (r.result && r.result.value) {
      const candidate = JSON.parse(r.result.value);
      if (candidate.__capturedAt && candidate.__capturedAt >= waitFromTs) { dec = candidate; break; }
    }
  }
  if (!dec) { const res = { pass: false, reason: 'no_new_image_timeout' }; logResult(res); return res; }

  const captchaType = dec.data && dec.data.captcha_type;
  const captchaId = dec.data && dec.data.captcha_id;
  log(`captcha_id: ${captchaId} | type: ${captchaType} | href: ${dec.__href}`);

  let plan = await computePlan(cdp, dec, apiKey);
  log(`omocaptcha x=${plan.omoRes.x} y=${plan.omoRes.y}`);

  // Captcha tu lam moi lien tuc ngay ca khong thao tac gi - re-check truoc khi keo, huy va
  // tinh lai neu co anh moi hon xuat hien trong luc vua goi omocaptcha/do DOM.
  for (let refresh = 0; refresh < 2; refresh++) {
    const rc = await cdp.send('Runtime.evaluate', { expression: 'window.__DEC && JSON.stringify(window.__DEC)', returnByValue: true });
    if (!rc.result || !rc.result.value) break;
    const latest = JSON.parse(rc.result.value);
    if (latest.__capturedAt > plan.dec.__capturedAt) {
      log('anh moi hon xuat hien - tinh lai...');
      plan = await computePlan(cdp, latest, apiKey);
      continue;
    }
    break;
  }

  const { omoRes, geometry } = plan;
  if (!geometry.bg || !geometry.track || !geometry.handle) {
    // captcha_type 11 ("Drag the missing piece to restore the puzzle") roi vao day - kien
    // truc canvas-only, chua ho tro (xem ghi chu #7 o dau file).
    const res = { pass: false, reason: 'dom_not_found', captchaId, captchaType };
    logResult(res); return res;
  }

  const dragResult = await closedLoopDrag(cdp, sleep, omoRes.x);
  log(`closed-loop targetCenter=${omoRes.x} final=${dragResult.finalPieceTx.toFixed(2)} error=${dragResult.finalError.toFixed(2)}px (${dragResult.iterations} vong lap)`);

  // Poll href (khong doc 1 lan duy nhat) - xem ghi chu #6 o dau file.
  let hrefNow = '';
  for (let i = 0; i < 15; i++) {
    await sleep(200);
    const vr = await cdp.send('Runtime.evaluate', { expression: 'JSON.stringify({href:location.href})', returnByValue: true });
    hrefNow = JSON.parse(vr.result.value).href;
    if (!/\/verify\/(captcha|traffic)/.test(hrefNow)) break;
  }
  const passed = !/\/verify\/(captcha|traffic)/.test(hrefNow);
  const result = {
    pass: passed, href: hrefNow, captchaId, captchaType,
    omoX: omoRes.x, omoY: omoRes.y, natW: geometry.bg.natW, trackW: geometry.track.w, handleW: geometry.handle.w,
    finalPieceTx: dragResult.finalPieceTx, finalError: dragResult.finalError, iterations: dragResult.iterations,
    totalHandleDelta: dragResult.totalHandleDelta,
  };
  logResult(result);
  return result;
}

// -----------------------------------------------------------------------------
// HAM CHINH - goi ham nay tu cac workflow khac. Tu ket noi CDP, gan hook, navigate (hoac
// gan vao tab hien co), lap lai attemptOnce toi khi PASS hoac het maxAttempts.
// -----------------------------------------------------------------------------
/**
 * @param {object} opts
 * @param {number} opts.port - CDP remote-debugging port cua tab (tu antidetect.py start_profile).
 * @param {string} opts.url - Navigate toi day de kich hoat gate captcha. Truyen '' de GAN VAO
 *   TAB HIEN CO (khong navigate) - dung khi captcha DA HIEN THI SAN.
 * @param {number} [opts.maxAttempts=6] - So lan thu toi da trong 1 lan goi.
 * @param {string} [opts.apiKey] - omocaptcha clientKey. Mac dinh: process.env.OMOCAPTCHA_API_KEY
 *   (BAT BUOC phai dat bien nay - throw ngay neu thieu, xem ghi chu dau file).
 * @param {(msg: string) => void} [opts.onLog] - callback nhan tung dong log (mac dinh: console.log).
 * @param {string|null} [opts.resultsLogPath] - duong dan file JSONL de ghi lai moi lan thu
 *   (mac dinh: solve_results.jsonl trong cung thu muc). Truyen null de tat ghi log.
 * @returns {Promise<{pass: boolean, attempts: object[], lastHref: string}>}
 */
export async function solveShopeeCaptcha(opts) {
  const {
    port, url, maxAttempts = 6,
    apiKey = process.env.OMOCAPTCHA_API_KEY,
    onLog = (msg) => console.log(msg),
    resultsLogPath = RESULTS_LOG_DEFAULT,
  } = opts;
  if (!apiKey) throw new Error('Thieu bien moi truong OMOCAPTCHA_API_KEY (khong con key hardcode mac dinh - xem ghi chu dau file).');

  const target = await findShopeeTab(port);
  if (!target) throw new Error(`Khong tim thay tab shopee.* nao tren port ${port}`);
  const cdp = await connectCDP(target.webSocketDebuggerUrl);

  // KHONG goi Runtime.enable - xem ghi chu #4 o dau file.
  await cdp.send('Page.enable');
  await cdp.send('Page.addScriptToEvaluateOnNewDocument', { source: CAPTURE_HOOK });

  let waitFromTs = Date.now();
  if (url) {
    onLog(`navigate -> ${url}`);
    await cdp.send('Page.navigate', { url });
  } else {
    onLog('gan vao tab hien co (khong navigate)...');
    await cdp.send('Runtime.evaluate', { expression: CAPTURE_HOOK });
    waitFromTs = 0;
  }

  const attempts = [];
  let passed = false;
  for (let i = 1; i <= maxAttempts; i++) {
    onLog(`--- lan thu ${i}/${maxAttempts} ---`);
    const res = await attemptOnce(cdp, { waitFromTs, apiKey, log: onLog, logResultPath: resultsLogPath });
    attempts.push(res);
    if (res.pass) { passed = true; onLog(`PASS! href: ${res.href}`); break; }
    onLog(`FAIL (${res.reason || 'reject'}${res.href ? ', href: ' + res.href.split('?')[0] : ''})`);

    if (res.reason === 'no_new_image_timeout' || res.reason === 'dom_not_found') {
      // Page.reload() giu nguyen anti_bot_tracking_id (session gate khong doi) nen se tra ve
      // DUNG captcha_type cu - PHAI navigate ve trang chu de xin tracking_id/gate MOI (xem
      // ghi chu #7 o dau file).
      const curHref = await cdp.send('Runtime.evaluate', { expression: 'location.origin', returnByValue: true });
      const homeUrl = (curHref.result && curHref.result.value ? curHref.result.value : (url || '').replace(/\/$/, '')) + '/';
      onLog(`${res.reason} - navigate ve trang chu (${homeUrl}) de lay tracking_id moi`);
      waitFromTs = Date.now();
      await cdp.send('Page.navigate', { url: homeUrl });
    } else {
      waitFromTs = Date.now();
    }
  }

  const lastHref = attempts.length ? attempts[attempts.length - 1].href || '' : '';
  cdp.close();
  return { pass: passed, attempts, lastHref };
}
