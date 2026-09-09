# Reverse Engineering: Chill 68 (GemLogin) — Pipeline Đăng Video Shopee

> Tài liệu tổng hợp từ phân tích động (Frida hook `SSL_write`/`SSL_read` + hook native DLL `tls-client-64.dll`)
> trên tiến trình `node.exe` thực thi module `mrtung` của Chill 68 (`Auto Đăng Video Shopee Siêu Tốc`).
> Mục đích: tham khảo để tự xây dựng pipeline tương tự, dùng license Chill 68 hiện có để ký request.
>
> Liên quan: [`RE_PLAN.md`](RE_PLAN.md) — nghiên cứu độc lập về header `x-sap-*`/`af-ac-enc-*` của
> `affiliate.shopee.ph`. Cùng hệ thống anti-bot/risk-control với phần `precheck`/`create` dưới đây — kết luận
> ở đó (VM bytecode, khó tái tạo offline) giải thích **vì sao Chill 68 tự outsource phần ký ra server ngoài**
> thay vì tính toán local.

## 0. Kiến trúc tổng thể

```
Chill 68.exe (Electron main)
  ├─ Chromium riêng (.Chill 68\browser\141\Chrome-bin\chrome.exe, điều khiển qua CDP --remote-debugging-port)
  │    → chỉ dùng hiển thị dashboard UI + có thể lấy cookie phiên, KHÔNG tự gọi API upload
  └─ spawn cmd.exe → node.exe hệ thống (KHÔNG phải Node bundle theo Chill68)
       chạy: %APPDATA%\gemlogin\MrTung\temp1.js  (wrapper)
       và:   %APPDATA%\gemlogin\mrtung\.sys_cache_<hash>.js  (script thật, tải về + cache tạm, tự xoá sau khi chạy)
       → TOÀN BỘ logic upload/API thật nằm ở đây, dùng axios + node-tls-client (Go DLL giả TLS fingerprint)
```

**Vì sao khó bắt traffic:**
- Không đi qua browser → mitmproxy/system-proxy vô dụng.
- `node-tls-client` dùng DLL Go riêng (`tls-client-64.dll`, tải về `%TEMP%`), **không tin cậy CA cert tự cài** → MITM qua proxy thất bại (`certificate unknown`).
- Cách bắt được: **Frida hook trực tiếp** vào:
  - Export `request()` của `tls-client-64.dll` (gọi qua FFI `koffi`) — bắt request/response của các call dùng TLS giả browser.
  - `SSL_write`/`SSL_read` export ngay trong `node.exe` (Node có export 2 symbol này) — bắt được **mọi** traffic TLS thường của Node (axios, googleapis, S3 SDK...), **không quan tâm cert gì cả** vì đọc plaintext trước khi mã hoá / sau khi giải mã.
- `node.exe` của mrtung là tiến trình **tạm thời** — chỉ sống trong lúc chạy job, cần watcher polling (WMI event trace `Win32_ProcessStartTrace` KHÔNG hoạt động trên máy Windows 10 test) để bắt kịp và attach Frida ngay khi nó xuất hiện.

## 1. Định dạng Cookie

Xem file mẫu: [`get_cookie/thpihace29_0909.txt`](get_cookie/thpihace29_0909.txt)

Các field bắt buộc: `csrftoken`, `SPC_F`, `SPC_SI`, `SPC_U`, `SPC_ST`, `SPC_STK`, `SPC_R_T_ID`, `SPC_R_T_IV`,
`SPC_T_ID`, `SPC_T_IV`, `AC_CERT_D`, `SPC_SEC_SI`, `language`, `SPC_RNBV`, `shopee_app_version`,
`shopee_rn_bundle_version`, `shopee_rn_version`.

## 2. Cấu hình theo quốc gia (`QUOCGIA_MAP`)

Xác nhận được cho **Thailand**:

```js
QUOCGIA_MAP['th'] = {
  host:     'shopee.co.th',
  sv:       'sv.shopee.co.th',            // API precheck/create
  api_mms:  'api-quic.mms.shopee.co.th',  // Media Management Service (preupload/report)
  wscloud:  'up-ws-th.vod.susercontent.com', // Upload video/cover thật (WSCloud mode)
  timezone: 'Asia/Bangkok',
  language: 'th', // suy ra
}
```

> Chưa xác nhận VN/PH cụ thể — nhưng theo pattern trên, đoán hợp lý: `sv.shopee.vn`, `up-ws-vn.vod.susercontent.com`,
> `sv.shopee.ph`, `up-ws-ph.vod.susercontent.com`. Cần bắt lại 1 lần với account/profile chạy market VN hoặc PH
> để xác nhận (dùng lại watcher trong mục 6).

## 3. Toàn bộ pipeline (đúng thứ tự thực thi)

### Bước 0 — Check license/credit
```
GET https://checkngaytest.ngothanhtung7762.workers.dev/?taikhoan={username}&hwid={hwid}
Headers: User-Agent: axios/1.19.0
→ 200 OK, body gzip/brotli (JSON creditInfo: endDate, daysRemaining, hwid, server1Url/server2Url/server3Url, apiKey...)
```
Cloudflare Worker riêng của dev (tài khoản `ngothanhtung7762`). `hwid` sinh bằng `node-machine-id`.

### Bước 1 — Đọc dữ liệu từ Google Sheets
```
GET https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values/{QUOCGIA}-{profileNum}
Authorization: Bearer {oauth_access_token}
User-Agent: google-api-nodejs-client/8.0.3 (gzip)
```
Tên sheet theo pattern `{quốc gia}-{số hiệu profile}` (ví dụ `TH-00123`). Trả về hàng dữ liệu: **tên video** (tên file
local để tìm), **link tiếp thị** (item_id/shop_id các sản phẩm gắn vào video), **caption**.

### Bước 2 — Lấy metadata video local
```js
getVideoMetadata(fullVideoPath)  // ffmpeg: width, height, duration, bitrate, size
md5(file)
```

### Bước 3 — Upload cover ảnh
```
POST https://sv.shopee.co.th/api/v2/biz/file/image
(~140-150KB, đây là ảnh cover/thumbnail video)
```

### Bước 4 — VOD Preupload (xin slot + credential upload)
```
POST https://{api_mms}/uploadapi/api/v1/vod/preupload
Headers:
  User-Agent: okhttp/3.12.4 app_type=1 platform=native_android os_ver={osVer} appver={appVer}
  Cookie: {shopeeCookie}
  x-csrftoken: {csrfToken}
  x-api-source: rn
  x-shopee-client-timezone: {timezone}
Body: {
  biz: 124, ver: 3,
  fingerprint_info: { fsize, md5 },
  reportdata: { sdkversion:"1.0", appversion, ostype:"0", osversion, token_type:0, userid, reporttime },
  mediatype: 1,
  cover_fingerprint_info: { fsize, md5 }
}
→ 200 OK: { data: { vid, services: [ { s3:bool, slicesize, uploaddomain, access_key, secret_key, bucket } | { token } ] } }
```
`vid` = video ID cấp phát. `services[]` chứa **1 trong 2** kiểu: AWS-S3-compatible (có `access_key`/`secret_key`
tạm) hoặc WSCloud (có `token`).

### Bước 5-6 — Upload video thật (2 chế độ, tool tự chọn/fallback theo `MODE_UPLOAD`)

**Chế độ A — AWS S3** (ưu tiên khi `svc.s3 === true`):
```js
s3 = createS3ClientVN(svc.uploaddomain, svc.access_key, svc.secret_key)   // AWS SDK S3Client chuẩn
s3MultipartUploadVN(s3, svc.bucket, `${vid}.mp4`, videoPath, svc.slicesize)
uploadCoverToS3VN(coverBuffer, svc, vodData)
```

**Chế độ B — WSCloud** (fallback, hoặc khi service không có S3):
```
POST https://{wscloud}/file/upload
Content-Type: multipart/form-data; boundary=...
User-Agent: WCS-Android-SDK-1.6.8

--boundary
Content-Disposition: form-data; name="token"

{key}:{sign}:{policy_base64}
--boundary
Content-Disposition: form-data; name="key"

{vid}.mp4
--boundary
Content-Disposition: form-data; name="file"; filename="{vid}.mp4"
Content-Type: video/mp4

<raw video bytes>
```
`token` là **định dạng Qiniu Cloud Storage** (`accessKey:sign:policyBase64`). Giải base64 phần policy ra:
```json
{"scope":"garena-video","deadline":"1788993607000","overwrite":0,"fsizeLimit":0}
```
(`scope` cố định `"garena-video"` — hạ tầng lưu trữ dùng chung của Sea Group/Garena). `token` lấy qua
`getUploadToken()` (gọi MMS) hoặc fallback `services[0].token`.

> CDN thật đứng sau là **Wangsu/ChinaNetCenter** (xác nhận qua chứng chỉ TLS default khi kết nối trực tiếp IP,
> ASN 54994 "Meteverse Limited", Hong Kong).

### Bước 7 — Report upload xong
```
POST https://{api_mms}/uploadapi/api/v1/vod/reportupload   (chế độ WSCloud)
Body: {
  ver:0, extendid:vid, code:0, vid, biz:124,
  cover_md5, fsize,
  videourl: `{VIDEO_CDN}/{vid}.mp4`,
  reportdata: { ostype:"0", app_id:"com.shopee.{market}" },
  serviceid: "wscloud",
  fileinfos: { duration, vbitrate:0, abitrate:0, width, height, fps:0, mediatype:1 }
}
```
hoặc `reportUploadAWS(vodData, svc, cookie, userId, ...)` cho chế độ S3 (thân bài chưa capture chi tiết, nhưng
cùng MMS host, cùng ý nghĩa — báo hoàn tất để Shopee bắt đầu transcode).

Sau report: **đợi cứng 10 giây** trước khi qua bước tiếp (đợi Shopee xử lý/transcode xong).

### Bước 8 — Precheck
```
POST https://{sv}/api/v2/biz/post/precheck
Headers: {headers ký từ signing server} (xem mục 4)
Body: {
  content: {
    from_source: "creator_id={userId}&pre_source=shopee_video&scene_code=my_profile_create_button",
    caption: "",
    video: { url:"", video_id:"", cover:"", width, height, size, duration, watermark_cover_url:"", skip_cover_check:true, is_ugc_cover:false },
    music: { music_id:"", type:3, title:"", url:"", original:true, start:0, cover, duration, author_name, soundtracks:[] },
    mentions:[], hashtags:[],
    post_attr: { share_to_friends:false },
    content_source:0, images:[], content_type:0
  },
  app_info: { system_os:"Android", system_version, app_version, device_model }
}
→ 200 OK: { code:0, data: { extra_context: "<token cần cho bước create>" } }
```

### Bước 9 — Create post (đăng thật)
```
POST https://{sv}/api/v2/biz/post/create?os_type=2&system_version={osVer}&sdk_version=1.61.2&model={model}&android_performance=802
Headers: {headers ký từ signing server}
Body: {
  content: {
    from_source: "creator_id={userId}&pre_source=content_merge_tab",
    caption: "{caption thật từ Sheet}",
    video: { url:"{VIDEO_CDN}/{vid}.mp4", video_id:vid, cover:"{cover CDN url}", width, height, size, duration, watermark_cover_url, skip_cover_check:true, is_ugc_cover:true },
    music: {...}, mentions:[], hashtags:[],
    products: [ { custom_name:"", item_id, shop_id, source_tab:1, mcn_campaign_token:"", free_sample_info:{...} }, ... ],  // ← LINK TIẾP THỊ
    post_attr: { share_to_friends:false },
    content_source:0, images:[], content_type:0,
    is_creator_claim_aigc:false,
    extra_context: "{từ bước 8}",
    allow_info: { allow_stitch:false, allow_duet:false }
  },
  app_info: {...},
  media_sdk_info: { camera:{...}, edit:[...], effect_ids:[], game_info:"", cover_text_info:"", game_magic_type:1, ug_reward_context_value:"", use_product_clip:false }
}
→ 200 OK: { code:0, data: { post_id } }
```
**Quan sát thực tế:** lần đầu gọi thường bị chặn (`status:418`, `error:90309999` — anti-bot). Tool tự động
**retry** ngay và lần 2 thường qua (200 OK). Nên implement sẵn retry logic này.

## 4. Header chống anti-bot — KHÔNG tự sinh, PHẢI gọi server ký ngoài

```js
async function signServer2(url, rawBody, socksAgent) {
  const res = await axios.post(creditInfo.server2Url, { url, body: rawBody }, {
    headers: { 'Content-Type': 'application/json', 'X-API-Key': creditInfo.server2ApiKey },
    timeout: 30000,
  });
  return res.data.data;  // → object headers để merge vào request thật
}
```

### `creditInfo` thật (bắt được nguyên vẹn từ response Bước 0, đã decompress Brotli)

```json
{
  "serverUrl": "http://104.234.195.132:3000/generate",
  "server2Url": "https://creditmls2026video.toolshopee.vn/api/sign",
  "server2ApiKey": "<REDACTED - key thật gắn theo license Chill68, KHÔNG commit - xem file .env/local riêng của bạn>",
  "tokenWorkerUrl": "https://uptoken-worker.ngothanhtung7762.workers.dev",
  "vpsUrl": "http://34.177.87.65:3000",
  "tokenServer2Url": "https://sigkey.videoshopee.com/generate_token",
  "tokenServer2ApiKey": "<REDACTED - key thật gắn theo license Chill68, KHÔNG commit - xem file .env/local riêng của bạn>",
  "phSharedKey": "ShopeeServerRequestAPI-Fixed-Shared-Key-2026",
  "xuatLog": 0,
  "s3KeyId": "shopee_vod_00124",
  "s3KeyIv": "1234567887654321",
  "phServer1Url": "http://157.66.24.236:3004",
  "phServer2Url": "http://157.66.24.236:3004",
  "phConcurrency": 20,
  "signMode": "3",
  "signModePH": "3",
  "modeUpload": 2,
  "end_date": "2026-09-24",
  "days_remaining": 15,
  "expired": false,
  "encrypt": 0,
  "encKey": null
}
```

**Ánh xạ với code:**
- `serverUrl` → dùng trong `signServer1(url, b64Body)` (endpoint `/generate`, nhận **base64 body** khác với server2 — chưa bắt được request thật vì account này dùng `signMode:"3"` = ưu tiên server2, server1 không được gọi).
- `server2Url` + `server2ApiKey` → dùng trong `signServer2`, endpoint `/api/sign`, nhận `{url, body}` JSON thô. **Đã bắt được request+response thật** (mục 3, Bước 8-9).
- `phServer1Url`/`phServer2Url` → cùng 1 IP (`157.66.24.236:3004`) dùng riêng cho market Philippines, `phConcurrency:20` giới hạn số luồng song song.
- `tokenServer2Url` (`sigkey.videoshopee.com/generate_token`) → rất có thể là nơi cấp `uploadToken` cho WSCloud (hàm `getUploadToken()`), **khác** với server ký header — cần bắt riêng để xác nhận request/response.
- `s3KeyId` / `s3KeyIv` → cặp key/IV kiểu AES, mục đích chưa rõ (có thể mã hoá field nào đó trong request, hoặc liên quan tới `encrypt`/`encKey` — hiện `encrypt:0` nên không dùng trong lần capture này).
- `signMode` / `signModePH`: `"3"` — tool có ít nhất 3 chế độ ký khác nhau theo config; giá trị `days_remaining`/`end_date` chính là hạn credit hiển thị trên UI Chill 68.

**Request thật đã bắt (Bước 8 — ký cho `precheck`):**
```
POST https://creditmls2026video.toolshopee.vn/api/sign HTTP/1.1
Content-Type: application/json
X-API-Key: <REDACTED - server2ApiKey thật, xem ghi chú redact ở mục creditInfo phía trên>
User-Agent: axios/1.19.0

{"url":"https://sv.shopee.co.th/api/v2/biz/post/precheck","body":"<precheck body JSON đã stringify>"}
```
Response: `200 OK`, backend IIS (`X-Powered-By: ARR/3.0`) đứng sau Cloudflare, body brotli+chunked chứa
`{"data": {...headers ký...}}` merge trực tiếp vào request thật gửi `sv.shopee.co.th` (không giải mã được toàn
bộ response — SSL_read hook chỉ bắt theo từng syscall read, response bị chia nhiều TLS record nên brotli stream
không đủ để decompress; nhưng **không cần thiết** vì header cuối cùng đã thấy nguyên trong request `precheck`/
`create` thật ở mục 3).

**Cùng pattern y hệt cho Bước 9 (ký cho `create`)** — chỉ khác `url`+`body` trong payload gửi lên.

- **Không có logic tự tính HMAC/crypto nội bộ nào** cho các header `1013e40f`, `3143e9a8`, `35d88d62`, `52dd5a34`,
  `6b7e8dd6`, `8e46538a`, `fd16cc4d`, `x-sap-ri` v.v. — 100% đến từ response của signing server.
- **Kết luận:** Muốn dùng lại pipeline này, **request `precheck`/`create` PHẢI đi qua `creditmls2026video.toolshopee.vn/api/sign`
  với `X-API-Key` hợp lệ** (license Chill 68 hiện có cấp key này qua Bước 0) — không thể tự tính header ở local.
  Xem [`RE_PLAN.md`](RE_PLAN.md) để hiểu tại sao (thuật toán nằm trong VM bytecode của Shopee, cực khó tái tạo offline).

## 5. Header cố định cần có trên MỌI request tới `sv.shopee.co.th`

```
User-Agent: okhttp/3.12.4 app_type=1
Cookie: {shopeeCookie}
X-CSRFToken: {csrftoken}
X-SAP-Type: 1
X-Shopee-Client-Timezone: {timezone}
SHOPEE_HTTP_DNS_MODE: 1
Host: {sv host}
language: {th|vi|...}
referer: https://{host}/
client-info: device_id={base64};device_model=SM-G991B;os=0;os_version=34;client_version={ver};network=1;platform=1;rn_version=6.97.5;api_source=na;cpu_model=;live_device_model=samsung+o1s
```

Nếu gọi qua `node-tls-client` (để giả TLS fingerprint như app thật), cần thêm:
```json
{
  "tlsClientIdentifier": "okhttp4_android_13",
  "withRandomTLSExtensionOrder": true,
  "forceHttp1": false
}
```

## 6. Công cụ/kỹ thuật dùng để reverse (tham khảo khi cần bắt lại)

1. **Tìm tiến trình `node.exe` mrtung**: polling `wmic process where "Name='node.exe'"`, lọc `CommandLine`
   chứa `gemlogin`/`MrTung`. WMI event trace (`Win32_ProcessStartTrace`) KHÔNG bắt được trên máy test —
   phải polling (~300ms/lần).
2. **Frida hook `SSL_write`/`SSL_read`** trong `node.exe` (export tồn tại sẵn, không cần tìm module khác):
   ```js
   var w = Module.findExportByName(null, "SSL_write"); // hoặc lặp qua Process.enumerateModules()
   var r = Module.findExportByName(null, "SSL_read");
   Interceptor.attach(w, { onEnter(args){ /* args[1]=buf, args[2]=len */ } });
   Interceptor.attach(r, { onEnter(args){ this.buf=args[1]; }, onLeave(retval){ /* retval=bytes read */ } });
   ```
   → đọc được **plaintext trước khi mã hoá / sau khi giải mã**, không quan tâm cert/trust store.
3. **Frida hook `tls-client-64.dll`** (native Go DLL do `node-tls-client` tải về `%TEMP%`, load qua FFI `koffi`):
   ```js
   var mod = Process.findModuleByName("tls-client-64.dll");
   var req = mod.findExportByName("request"); // string in (JSON config) → string out (JSON response)
   Interceptor.attach(req, { onEnter(args){ args[0].readUtf8String(); }, onLeave(retval){ retval.readUtf8String(); } });
   ```
4. Script Python đầy đủ (poll + auto-attach): xem lịch sử conversation này hoặc yêu cầu Claude viết lại
   (`watch_poll_attach.py` — attach vào mọi `node.exe` mới có `gemlogin`/`MrTung` trong command line, hook cả 2
   kỹ thuật trên cùng lúc, cap output 6KB/write để tránh log phình to khi video 10MB+ đi qua).

## 7b. "400003 Post too many videos" — ĐÃ GIẢI, KHÔNG PHẢI rate-limit

> Giả thuyết rate-limit/device-fingerprint-quota ở version cũ của mục này là SAI — đã bị bác
> bỏ bằng bằng chứng thực tế (license Chill 68 đăng hàng nghìn video/ngày liên tục không lỗi
> gì) và bằng chứng đọc trực tiếp source code gốc bên dưới. Giữ lại đây để biết đã từng đi sai
> hướng nào, tránh lặp lại.

Đọc trực tiếp source code gốc (biến `createPostOnShopee_Server1/2/3` trong
`poll_attach_full.log`) cho thấy 400003 là **lỗi nghiệp vụ thật nhưng liên quan tới
`app_version`, không phải quota**:

```js
const createCookie  = shopeeCookie.replace(/shopee_app_version=\d+/, `shopee_app_version=${CREDIT_APP_VERSION}`);
const fallbackCookie = shopeeCookie.replace(/shopee_app_version=\d+/, `shopee_app_version=${FALLBACK_APP_VERSION}`); // = 37229 (literal, xác nhận trong source)
...
if (postRes.data?.code === 400003) {
  // ...thử cookie fallback
  continue;
}
```

Tool gốc **KHÔNG BAO GIỜ tin nguyên giá trị `shopee_app_version` có sẵn trong cookie** — nó
luôn ghi đè field này trong Cookie header bằng 1 hằng số tĩnh trước khi gọi `create()`, và
khi `create()` trả `400003` thì coi đó là tín hiệu "app_version bị từ chối — đổi version
khác" rồi thử lại **ngay lập tức trong cùng 1 lượt** (không delay) với 1 cookie mang
`shopee_app_version` khác. `FALLBACK_APP_VERSION = 37229` xác nhận bằng số literal trong
source; `SERVER1_APP_VERSION = 37125` (comment trong source, dùng cho flow S1).

**2 bug thật đã sửa trong `scripts/shopee_video_post.py` (xác nhận bằng post thành công
thật, `post_id: g4V40Gr_CQCseUZSAwAAAA==`, market TH, 2026-09-10):**

1. **Thiếu cơ chế 2 biến thể `shopee_app_version`** — `shopee_create_post()` giờ thử biến
   thể "default" (app_version lấy từ cookie người dùng đưa vào) trước; nếu response trả
   `code: 400003` thì thử ngay biến thể "fallback" (app_version = `37229`, ghi đè cả Cookie
   header lẫn `app_info.app_version`/`client-info`) — xem `_FALLBACK_APP_VERSION` +
   `_cookie_with_app_version()`.
2. **Query param `model` trong URL `create()` chưa URL-encode** — giá trị thật là
   `model=samsung%20SM-G991B` (dấu cách được percent-encode), code trước đó chèn thẳng
   `"samsung SM-G991B"` (dấu cách trần) vào URL → Shopee/gateway trả thẳng `HTTP 400` rỗng
   (không phải JSON, không tới được tầng logic trả `400003`). Sửa bằng
   `urllib.parse.quote()`.

Ngoài 2 bug trên, các bug đã sửa trước đó (đều đúng nhưng KHÔNG PHẢI nguyên nhân chính của
400003 — device_id ngẫu nhiên/tĩnh, domain `video_cdn`, `fsize`) vẫn giữ nguyên vì là hành vi
đúng khớp code gốc.

## 7. Chưa xác nhận / cần làm tiếp nếu cần

- [x] ~~"400003 Post too many videos"~~ → **ĐÃ GIẢI**, xem mục 7b — không phải rate-limit,
      là bug thiếu cơ chế 2 biến thể `shopee_app_version` + query param `model` chưa
      URL-encode. Xác nhận bằng post thành công thật.
- [x] ~~`server1Url`/`server3Url`~~ → thực chất trong code chỉ có `serverUrl` (server1) + `server2Url`. Đã xác
      nhận `serverUrl = http://104.234.195.132:3000/generate` (chưa thấy request thật vì account dùng `signMode:"3"`
      = luôn ưu tiên server2, chưa từng fallback về server1 trong các lần capture).
- [x] ~~Nội dung JSON response Bước 0~~ → đã decompress đầy đủ, xem `creditInfo` mục 4.
- [x] ~~Request/response `tokenServer2Url`~~ → **đã bắt đầy đủ**, xem mục 4b ngay dưới.
- [~] `reportUploadAWS()` body chi tiết + mục đích `s3KeyId`/`s3KeyIv` — **dừng điều tra, không cần cho việc build
      tool**: account hiện tại có `modeUpload:2` **cố định** (config từ signing server, không phải runtime), nên
      nhánh AWS S3 không bao giờ chạy thật — dù bắt được code cũng không xác minh được giá trị thật. Pipeline
      **WSCloud** (nhánh đang active, đã 100% đầy đủ ở mục 3 + 4b) là đủ để tự build lại.

## 4b. `sigkey.videoshopee.com/generate_token` — nơi cấp Upload Token (WSCloud)

Đây chính là implementation của `getUploadToken()` được gọi trước bước upload video (mục 3, Bước 5).

**Request:**
```
POST https://sigkey.videoshopee.com/generate_token HTTP/1.1
Content-Type: application/json
X-API-Key: {creditInfo.tokenServer2ApiKey}   # REDACTED - xem ghi chú redact ở mục creditInfo phía trên
User-Agent: axios/1.19.0

{}
```
Không cần body gì cả (`{}`) — chỉ cần đúng API key.

**Response:**
```
HTTP/1.1 200 OK
Server: nginx/1.18.0 (Ubuntu)     ← server tự host, KHÔNG qua Cloudflare (khác server2Url)
Content-Type: text/plain; charset=utf-8

d6e4b41e5f512916477077ff856e09f01df7bef5:{sign_base64}:{policy_base64}
```
`policy_base64` giải ra: `{"scope":"garena-video","deadline":"<unix_ms>","overwrite":0,"fsizeLimit":0}`
— **chính xác token này** được dùng trực tiếp trong field `form-data name="token"` của request
`POST https://{wscloud}/file/upload` ở Bước 5-6. `deadline` là hạn dùng token (mili-giây epoch, ngắn hạn —
phải gọi `generate_token` mới trước mỗi lần upload, không cache lâu được).
- [ ] `QUOCGIA_MAP` cho VN/PH (chỉ có TH) — map được truyền qua CDP (WebSocket cục bộ, không mã hoá TLS) nên
      hook SSL không thấy; cần hook CDP WebSocket riêng hoặc bắt lúc chạy profile market VN/PH.
- [ ] Giá trị field `1013e40f` v.v. cụ thể sinh ra như thế nào phía server ký (ngoài tầm — thuộc về Chill 68/bên thứ 3).
