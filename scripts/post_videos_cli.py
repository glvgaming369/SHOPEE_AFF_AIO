"""CLI chạy post_videos_from_folder() (shopee_video_post.py) từ dòng lệnh.

Ví dụ chạy thử AN TOÀN trước (không gọi Shopee, chỉ xem match được gì):
    python post_videos_cli.py --folder "D:\\Shopee_PH\\upload\\video-demo" ^
        --cookie-file "D:\\Shopee_PH\\upload\\cookie.txt" --dry-run

Chạy thật (CẢNH BÁO: gọi server ký + upload video thật lên Shopee, không thể hoàn tác):
    python post_videos_cli.py --folder "D:\\Shopee_PH\\upload\\video-demo" ^
        --cookie-file "D:\\Shopee_PH\\upload\\cookie.txt" ^
        --server2-api-key "<key thật gắn theo license Chill68 - KHÔNG commit>" ^
        --token-api-key "<key thật gắn theo license Chill68 - KHÔNG commit>" ^
        --limit 1
"""
from __future__ import annotations

import argparse
import sys

import shopee_db
from gsheet_video_scanner import build_matched_pool
from shopee_video_post import SigningConfig, post_videos_from_folder


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--folder", required=True, help="Thư mục chứa <sp_id>.mp4 + đúng 1 file *_results.xlsx")
    p.add_argument("--cookie-file", required=True, help="File chứa cookie Shopee (định dạng 'a=1; b=2; ...')")
    p.add_argument("--market", default="th", help="Mã market (mặc định 'th' - chỉ market này đã xác nhận cấu hình)")
    p.add_argument("--db-path", default=shopee_db.DB_PATH_DEFAULT, help="Đường dẫn SQLite log kết quả")
    p.add_argument("--limit", type=int, default=None, help="Chỉ đăng tối đa N video (bỏ trống = đăng hết) - nên dùng =1 cho lần chạy thử đầu tiên")
    p.add_argument("--no-skip-posted", action="store_true", help="Đăng lại cả SP ID đã có log thành công trước đó (mặc định tự bỏ qua)")
    p.add_argument("--min-delay", type=float, default=8.0, help="Nghỉ tối thiểu (giây) giữa các video, ngẫu nhiên trong [min,max] (mặc định 8)")
    p.add_argument("--max-delay", type=float, default=20.0, help="Nghỉ tối đa (giây) giữa các video (mặc định 20). Đặt cả 2 =0 để tắt hẳn nghỉ giữa video")
    p.add_argument(
        "--dry-run", action="store_true",
        help="CHỈ hiển thị danh sách video sẽ được xử lý (khớp thư mục+xlsx), KHÔNG gọi Shopee/server ký - dùng để kiểm tra trước khi chạy thật",
    )
    p.add_argument("--server2-url", default="https://creditmls2026video.toolshopee.vn/api/sign")
    p.add_argument("--server2-api-key", default="", help="Bắt buộc trừ khi --dry-run (xem CHILL68_VIDEO_UPLOAD_RE.md mục 4)")
    p.add_argument("--token-url", default="https://sigkey.videoshopee.com/generate_token")
    p.add_argument("--token-api-key", default="", help="Bắt buộc trừ khi --dry-run (xem CHILL68_VIDEO_UPLOAD_RE.md mục 4b)")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)

    rows = build_matched_pool(args.folder)
    if args.limit is not None:
        rows = rows[: args.limit]

    print(f"Khớp được {len(rows)} video (thư mục + xlsx giao nhau):")
    for row in rows:
        print(f"  - {row.sp_id}.mp4 | caption={row.product_name!r} | links={row.merge_links.count('|') + 1 if row.merge_links else 0}")

    if args.dry_run:
        print("\n--dry-run: dừng ở đây, KHÔNG gọi Shopee/server ký.")
        return 0

    if not rows:
        print("Không có video nào để đăng.")
        return 0

    if not args.server2_api_key or not args.token_api_key:
        print("LỖI: thiếu --server2-api-key hoặc --token-api-key (bắt buộc khi không --dry-run).", file=sys.stderr)
        return 1

    with open(args.cookie_file, encoding="utf-8") as f:
        cookie_str = f.read().strip()

    signing = SigningConfig(
        server2_url=args.server2_url, server2_api_key=args.server2_api_key,
        token_url=args.token_url, token_api_key=args.token_api_key,
    )

    print(f"\nBắt đầu đăng thật ({len(rows)} video, market={args.market})...")
    results = post_videos_from_folder(
        folder=args.folder, cookie_str=cookie_str, signing=signing, market=args.market,
        db_path=args.db_path, skip_already_posted=not args.no_skip_posted, limit=args.limit,
        min_delay_seconds=args.min_delay, max_delay_seconds=args.max_delay,
    )

    ok = sum(1 for _, r in results if r.success)
    print(f"\nXong: {ok}/{len(results)} thành công.")
    for row, result in results:
        status = f"post_id={result.post_id}" if result.success else f"LỖI: {result.error}"
        print(f"  - {row.sp_id}: {status}")
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
