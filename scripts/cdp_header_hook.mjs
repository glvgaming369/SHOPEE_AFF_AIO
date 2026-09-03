// Hook header tu document-start (chay truoc MOI script cua trang): bat moi setRequestHeader /
// fetch co ten header x-sap*/af-ac*/sz*, luu kem call stack -> tim noi sinh x-sap-sec.
import { writeFileSync } from 'node:fs';
const arg = (n, d) => { const i = process.argv.indexOf(n); return i >= 0 && process.argv[i + 1] ? process.argv[i + 1] : d; };
const PORT = parseInt(arg('--port', '9333'), 10);
const ITEM = arg('--item', '41060972359');
const OUT = arg('--out', 'artifacts/cdp_header_hook.json');
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const HOOK = `(() => {
  try {
    window.__hdr = [];
    const push = (n, v) => {
      try {
        window.__hdr.push({ n: String(n), v: String(v).slice(0, 100), st: (new Error()).stack ? (new Error()).stack.split('\\n').slice(1, 12).join('\\n') : '' });
        if (window.__hdr.length > 600) window.__hdr.shift();
      } catch (e) {}
    };
    const proto = XMLHttpRequest.prototype.setRequestHeader;
    XMLHttpRequest.prototype.setRequestHeader = function (n, v) {
      try { if (/x-sap|af-ac|szdet|sz-token/i.test(String(n))) push(n, v); } catch (e) {}
      return proto.call(this, n, v);
    };
    const oOpen = XMLHttpRequest.prototype.open;
    XMLHttpRequest.prototype.open = function (m, u) {
      try { this.__u = String(u); } catch (e) {}
      return oOpen.apply(this, arguments);
    };
  } catch (e) {}
})();`;

const list = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json();
const target = list.find((x) => x.type === 'page' && /affiliate\.shopee/.test(x.url || ''));
if (!target) { console.log('Khong thay tab affiliate'); process.exit(1); }

const cdp = await new Promise((resolve, reject) => {
  const ws = new WebSocket(target.webSocketDebuggerUrl);
  const pending = new Map(); const listeners = new Set(); let id = 0;
  ws.onopen = () => resolve({
    send(m, p = {}) { return new Promise((res, rej) => { const mid = ++id; pending.set(mid, { res, rej }); ws.send(JSON.stringify({ id: mid, method: m, params: p })); }); },
    on(fn) { listeners.add(fn); }, close() { try { ws.close(); } catch (e) {} },
  });
  ws.onmessage = (ev) => { const msg = JSON.parse(ev.data); if (msg.id && pending.has(msg.id)) { const p = pending.get(msg.id); pending.delete(msg.id); msg.error ? p.rej(new Error(msg.error.message)) : p.res(msg.result); return; } if (msg.method) for (const fn of listeners) fn(msg); };
  ws.onerror = () => reject(new Error('ws err'));
});

await cdp.send('Page.enable');
await cdp.send('Runtime.enable');
await cdp.send('Page.addScriptToEvaluateOnNewDocument', { source: HOOK });

const url = `https://affiliate.shopee.ph/offer/product_offer/${ITEM}`;
console.log('Navigate 1...');
await cdp.send('Page.navigate', { url });
await sleep(10000);
console.log('Navigate 2 (de xem xoay vong)...');
await cdp.send('Page.navigate', { url: url + '?r=2' });
await sleep(10000);

const dump = await cdp.send('Runtime.evaluate', { expression: 'JSON.stringify(window.__hdr || [])', returnByValue: true });
const arr = JSON.parse(dump.result.value || '[]');
console.log('Tong header log:', arr.length);
const byName = {};
for (const h of arr) { byName[h.n] = (byName[h.n] || 0) + 1; }
console.log('Theo ten:', JSON.stringify(byName));
// xem 1-2 mau x-sap
for (const h of arr.filter((x) => /x-sap/i.test(x.n)).slice(0, 3)) {
  console.log('\n===== ' + h.n + ' =====');
  console.log('value:', h.v);
  console.log('stack:\n' + h.st);
}
writeFileSync(OUT, JSON.stringify(arr, null, 1), 'utf8');
console.log('\nDa luu:', OUT);
cdp.close();
