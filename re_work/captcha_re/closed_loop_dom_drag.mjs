// KEO CLOSED-LOOP dua tren TRANSLATEX THAT cua manh ghep (doc qua CSS transform style -
// KHONG phai canvas pixel, nen KHONG bi nhieu). Ly do can thiet: da do THUC NGHIEM (xem
// calibrate_scale.mjs) ti le "handle keo bao nhieu px -> manh ghep di chuyen bao nhieu px"
// KHONG CO DINH giua cac captcha (do duoc 1.6071 va 1.0165 o 2 lan khac nhau) - rat co the la
// co che chong bot co y (random hoa he so nhay moi lan) khien MOI cong thuc scale co dinh deu
// sai mot cach khong nhat quan. Giai phap: vua keo vua DO LAI vi tri THAT cua manh ghep, tu
// dieu chinh toi khi hoi tu ve dung target - khong can biet truoc ti le.
export async function findPieceAndHandle(cdp) {
  const EXPR = `(() => {
    const all = Array.from(document.querySelectorAll('[style*="translateX"]')).map(el => {
      const style = el.getAttribute('style') || '';
      const r = el.getBoundingClientRect();
      const mX = style.match(/translateX\\(([-\\d.]+)px\\)/);
      const mY = style.match(/translateY\\(([-\\d.]+)px\\)/);
      // DIV wrapper cua piece thuong CO KICH THUOC 0x0 (chinh no khong ve gi), canvas/img
      // 44x44 THAT nam BEN TRONG lam con - phai lay kich thuoc tu con neu wrapper rong 0.
      let w = r.width, h = r.height;
      if (w === 0 && h === 0) {
        const child = el.querySelector('canvas, img');
        if (child) { const cr = child.getBoundingClientRect(); w = cr.width; h = cr.height; }
      }
      return { w: Math.round(w), h: Math.round(h), x: Math.round(r.left), y: Math.round(r.top), tx: mX ? parseFloat(mX[1]) : null, ty: mY ? parseFloat(mY[1]) : null };
    });
    const piece = all.find(o => o.ty !== null);
    const handle = all.find(o => o.ty === null && o.w >= 32 && o.w <= 55);
    return JSON.stringify({ piece, handle });
  })();`;
  const r = await cdp.send('Runtime.evaluate', { expression: EXPR, returnByValue: true });
  return JSON.parse(r.result.value);
}

async function readPieceTx(cdp) {
  const EXPR = `(() => {
    const all = Array.from(document.querySelectorAll('[style*="translateX"]'));
    for (const el of all) {
      const style = el.getAttribute('style') || '';
      if (style.includes('translateY')) {
        const mX = style.match(/translateX\\(([-\\d.]+)px\\)/);
        return mX ? parseFloat(mX[1]) : null;
      }
    }
    return null;
  })();`;
  const r = await cdp.send('Runtime.evaluate', { expression: EXPR, returnByValue: true });
  return r.result.value;
}

/**
 * Keo closed-loop. `targetCenterX` la vi tri TAM manh ghep MONG MUON, theo dung quy uoc cua
 * chinh omocaptcha's own reference extension (shopee-content.bundle.js, ham N()): ho so khop
 * TAM cua piece element (`t.left+t.width/2`) voi target, KHONG PHAI canh trai/translateX truc
 * tiep. Da xac nhan bang thuc te: dung translateX=x truc tiep (bo qua offset nay) gay KEO QUA
 * DA co dinh dung bang piece.width/2 (~22px voi piece 44px) - nguoi dung xac nhan truc quan
 * "van dang keo lech qua vi tri dung" o moi lan test truoc khi fix nay duoc ap dung.
 * Vi piece bat dau tai translateX=0 (canh trai piece khop x=0 cua bg image), tam piece luc
 * nghi = piece.width/2. Suy ra: translateX can dat = targetCenterX - piece.width/2.
 */
export async function closedLoopDrag(cdp, sleep, targetCenterX, opts = {}) {
  // (2026-09-09) SIET dung sai tu 2px xuong 0.5px va tang maxIters - nguoi dung quan sat
  // truc tiep cac lan FAIL van con "lech mot chut" - can loai tru HOAN TOAN sai so do THUC THI
  // con sot lai (khac voi sai so von co cua chinh cau tra loi AI-vision omocaptcha, cai ma
  // khong the sua duoc o buoc nay).
  const maxIters = opts.maxIters || 12;
  const tolerance = opts.tolerance || 0.5;

  const found = await findPieceAndHandle(cdp);
  if (!found.handle || !found.piece) throw new Error('khong tim thay piece/handle de keo closed-loop');
  const handle = found.handle;
  const pieceHalfW = found.piece.w / 2;
  const targetPieceTx = targetCenterX - pieceHalfW;
  const hy = handle.y + handle.h / 2;
  let handleX = handle.x + handle.w / 2; // vi tri chuot hien tai (tam handle)
  const startHandleX = handleX;
  let pieceTx = found.piece.tx || 0;
  const startPieceTx = pieceTx;

  await cdp.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: handleX, y: hy });
  await sleep(60 + Math.random() * 80);
  await cdp.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: handleX, y: hy, button: 'left', clickCount: 1 });
  await sleep(80 + Math.random() * 100);

  let estRatio = 1.3; // uoc luong khoi diem trung binh (giua 2 mau da do: 1.6071 va 1.0165)
  let handleMovedTotal = 0;
  const log = [];

  for (let iter = 0; iter < maxIters; iter++) {
    const remainingPieceDist = targetPieceTx - pieceTx;
    if (Math.abs(remainingPieceDist) <= tolerance) break;

    // Uoc luong buoc handle can di chuyen THEM dua tren ti le da biet/uoc luong hien tai.
    // Cac vong dau di CHUA HET (chi 55-70%) de con du lieu do lai va hieu chinh, tranh
    // overshoot lon do uoc luong ti le ban dau sai (giong "danh gia lai" thay vi "khoa cung").
    // (2026-09-09) GIAM aggressiveness o cac vong CUOI (thay vi day len 1.0) - voi dung sai
    // sIET CHAT (0.5px), di 100% moi vong de gay dao dong khong hoi tu (overshoot roi lai
    // overshoot nguoc lai vo tan) - giam dan giup "ha canh" muot thay vi "nay qua lai".
    const aggressiveness = iter === 0 ? 0.6 : (iter < 3 ? 0.8 : (iter < 6 ? 0.9 : 0.7));
    const handleStep = (remainingPieceDist / estRatio) * aggressiveness;
    const newHandleX = handleX + handleStep;

    // (2026-09-09) TANG MAT DO mousemove: chuot HW that bao su kien o ~125-1000Hz (moi 1-8ms),
    // trong khi ban truoc chi tao ~15-40ms/su kien (~30-65Hz) - qua thua so voi chuot that,
    // co the la 1 dau hieu de SDK chong bot phan biet voi input that. Giam khoang chia buoc
    // (2px/buoc thay vi 8px/buoc) va rut ngan delay (4-10ms thay vi 15-40ms) de tang so luong
    // su kien/giay, dong thoi tang tran so buoc toi da de khong bi cat bot voi quang duong lon.
    const microSteps = Math.max(5, Math.min(40, Math.round(Math.abs(handleStep) / 2)));
    for (let s = 1; s <= microSteps; s++) {
      const x = handleX + (handleStep * s) / microSteps;
      await cdp.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x, y: hy, button: 'left' });
      await sleep(4 + Math.random() * 6);
    }
    await sleep(40 + Math.random() * 60); // cho DOM/JS cua widget kip cap nhat transform

    const newPieceTx = await readPieceTx(cdp);
    if (newPieceTx === null) { handleX = newHandleX; continue; }

    const handleDelta = newHandleX - handleX;
    const pieceDelta = newPieceTx - pieceTx;
    if (Math.abs(handleDelta) > 0.5 && Math.abs(pieceDelta) > 0.01) {
      // Cap nhat uoc luong ti le TU DU LIEU THAT vua do (trung binh co trong so voi uoc
      // luong cu de on dinh hon, tranh nhay lung tung neu 1 phep do bi le/round-off).
      const measuredRatio = pieceDelta / handleDelta;
      estRatio = iter === 0 ? measuredRatio : (estRatio * 0.4 + measuredRatio * 0.6);
    }
    log.push({ iter, handleX: newHandleX, pieceTx: newPieceTx, estRatio });
    handleX = newHandleX;
    pieceTx = newPieceTx;
  }

  await sleep(80 + Math.random() * 150);
  await cdp.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: handleX, y: hy, button: 'left', clickCount: 1 });

  return {
    finalPieceTx: pieceTx, targetPieceTx, targetCenterX, pieceHalfW, finalError: pieceTx - targetPieceTx,
    finalHandleX: handleX, totalHandleDelta: handleX - startHandleX,
    startPieceTx, iterations: log.length, log,
  };
}
