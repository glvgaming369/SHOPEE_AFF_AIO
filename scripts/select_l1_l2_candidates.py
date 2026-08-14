"""Sau khi 1 link goc ('root') da duoc cao du lieu (root + cac 'related' cung nhom, luu
vao SQLite qua scripts/shopee_db.py), script nay BAO CAO toan bo san pham tuong tu
('related') DA DAT CHUAN trong cung nhom, theo 3 dieu kien (mac dinh, co the tuy chinh
qua tham so - da chot voi nguoi dung):

    affiliate_promoted_last_7days < 1000   (qua nhieu KOL da lam thi khong tot)
    sold                          > 100    (luot ban tot - LUU Y: sold khac historical_sold)
    seller_commission (VND)       > 1000   (so tien hoa hong thuc te, KHONG PHAI ty le)

Ca root va tung ung vien related deu duoc kiem tra qua cung 3 dieu kien nay (root khong
duoc mien). Pipeline cao (xem shopee_db.claim_pending/finish_root) da cao/cham diem HET
moi ung vien related (khong con gioi han so luong/dung som) - script nay CHI con vai tro
BAO CAO, tham so `top_n` chi con y nghia "hien thi toi da N dong dau" (uu tien sold cao),
KHONG con anh huong gi toi viec cao.

Doc TRUC TIEP tu SQLite (khong con doc file JSON rieng nhu ban truoc) - DB da co san
affiliate_promoted_last_7days/sold/seller_commission da parse san cho moi item qua
shopee_db.map_v2_data_to_row(), chi can query theo groupid + link_type='related'.

Chay:
    python scripts/select_l1_l2_candidates.py 8217776149
    python scripts/select_l1_l2_candidates.py 8217776149 --top-n 5 --sold-min 200
"""
import argparse
import sqlite3

import shopee_db

TOP_N_DEFAULT = 5
PROMOTED_7D_MAX_DEFAULT = 1000       # < nguong nay
SOLD_MIN_DEFAULT = 100               # > nguong nay
SELLER_COMMISSION_VND_MIN_DEFAULT = 1000  # > nguong nay (so tien thuc te, khong phai %)


def _row_metrics(row):
    return {
        "affiliate_promoted_last_7days": row["affiliate_promoted_last_7days"] or 0,
        "sold": row["sold"] or 0,
        "seller_commission_vnd": row["seller_commission"] or 0,
    }


def passes_criteria(metrics, promoted_7d_max=PROMOTED_7D_MAX_DEFAULT, sold_min=SOLD_MIN_DEFAULT,
                     seller_commission_vnd_min=SELLER_COMMISSION_VND_MIN_DEFAULT):
    return (
        metrics["affiliate_promoted_last_7days"] < promoted_7d_max
        and metrics["sold"] > sold_min
        and metrics["seller_commission_vnd"] > seller_commission_vnd_min
    )


def select_qualified_related(db_path, root_item_id, top_n=TOP_N_DEFAULT,
                              promoted_7d_max=PROMOTED_7D_MAX_DEFAULT, sold_min=SOLD_MIN_DEFAULT,
                              seller_commission_vnd_min=SELLER_COMMISSION_VND_MIN_DEFAULT):
    conn = shopee_db.init_db(db_path)
    conn.row_factory = sqlite3.Row
    try:
        root_row = conn.execute(
            "select * from products where itemid = ?", (str(root_item_id),)
        ).fetchone()
        if not root_row:
            print(f"Khong tim thay link goc item_id={root_item_id} trong DB.")
            return None

        root_metrics = _row_metrics(root_row)
        root_passes = passes_criteria(root_metrics, promoted_7d_max, sold_min, seller_commission_vnd_min)
        print(f"Link goc {root_item_id}: {root_metrics} -> DAT CHUAN={root_passes}")
        if not root_passes:
            print("  LUU Y: link goc nay KHONG dat chuan theo 3 dieu kien - pipeline van cao/xac "
                  "thuc san pham tuong tu cua no binh thuong (khong con luat 'goc phai dat chuan "
                  "moi lay lien quan').")

        candidate_rows = conn.execute(
            "select * from products where groupid = ? and link_type = 'related'",
            (str(root_item_id),),
        ).fetchall()

        qualified = []
        for row in candidate_rows:
            metrics = _row_metrics(row)
            if passes_criteria(metrics, promoted_7d_max, sold_min, seller_commission_vnd_min):
                qualified.append((metrics, row))

        qualified.sort(key=lambda x: x[0]["sold"], reverse=True)
        top = qualified[:top_n]

        print(f"So ung vien related DAT CHUAN: {len(qualified)}/{len(candidate_rows)}.")
        if len(top) < len(qualified):
            print(f"(chi hien thi {len(top)}/{len(qualified)} dong dau - top_n={top_n}, "
                  "khong anh huong toi viec da cao het.)")

        result = {
            "root_item_id": root_item_id,
            "root_product_link": root_row["product_link"],
            "root_metrics": root_metrics,
            "root_passes_criteria": root_passes,
            "criteria": {
                "affiliate_promoted_last_7days_lt": promoted_7d_max,
                "sold_gt": sold_min,
                "seller_commission_vnd_gt": seller_commission_vnd_min,
            },
            "candidates_total": len(candidate_rows),
            "candidates_qualified": len(qualified),
            "related_selected": [
                {
                    "rank": i + 1,
                    "item_id": row["itemid"],
                    "product_link": row["product_link"],
                    **metrics,
                }
                for i, (metrics, row) in enumerate(top)
            ],
        }

        print(f"Hien thi {len(top)} san pham lien quan dat chuan cho link goc {root_item_id}.")
        for r in result["related_selected"]:
            print(f"  #{r['rank']}: {r['item_id']} - promoted_7d={r['affiliate_promoted_last_7days']} "
                  f"sold={r['sold']} seller_commission_vnd={r['seller_commission_vnd']}")
        return result
    finally:
        conn.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root_item_id")
    ap.add_argument("--db-path", default=shopee_db.DB_PATH_DEFAULT)
    ap.add_argument("--top-n", type=int, default=TOP_N_DEFAULT)
    ap.add_argument("--promoted-7d-max", type=int, default=PROMOTED_7D_MAX_DEFAULT)
    ap.add_argument("--sold-min", type=int, default=SOLD_MIN_DEFAULT)
    ap.add_argument("--seller-commission-vnd-min", type=int, default=SELLER_COMMISSION_VND_MIN_DEFAULT)
    args = ap.parse_args()
    select_qualified_related(args.db_path, args.root_item_id, args.top_n,
                              args.promoted_7d_max, args.sold_min, args.seller_commission_vnd_min)


if __name__ == "__main__":
    main()
