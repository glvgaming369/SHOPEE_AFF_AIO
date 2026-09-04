"""Maintenance: danh muc keyword bi "cung ten - khac cat_id" (bug cu cua keyword_page_done:
cat_id that cua item khong nam trong cat-db (id leaf) nhung lai duoc gan cat_name cua LO
keyword -> nhieu leaf-id khac nhau cung 1 ten). Chuan hoa: voi nhung dong ma (market,
cat_name) giai duoc ra DUNG 1 cat_id chuan trong cat-db, chuyen tat ca dong dang lech ve
cat_id chuan do (id+ten di doi). Chay preview (khong doi DB) / --apply de ghi.

    python scripts/fix_keyword_cat_identity.py          # chi xem
    python scripts/fix_keyword_cat_identity.py --apply   # ghi vao DB
"""
import argparse
import sqlite3
import sys
from collections import defaultdict

sys.path.insert(0, "scripts")
import shopee_categories  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="artifacts/db/shopee.db")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "select market, cat_name, cat_id, count(*) n from products "
        "where cat_name is not null group by market, cat_name, cat_id"
    ).fetchall()
    cache = shopee_categories._get_cache()  # market -> {cat_id: name}

    # Ten -> danh sach cat_id chuan trong cat-db (de biet ten nao la duy nhat)
    canon_by_market = {m: defaultdict(list) for m in cache}
    for m, d in cache.items():
        for cid, name in d.items():
            if name:
                canon_by_market[m][str(name).strip().lower()].append(cid)

    plan = []  # (market, name, old_cat_id, new_cat_id, n)
    for r in rows:
        market, name, cid = r["market"], r["cat_name"], r["cat_id"]
        key = str(name).strip().lower()
        canon = canon_by_market.get(market, {}).get(key, [])
        if len(canon) != 1:
            continue  # ten khong phan giai duy nhat -> khong dong cham
        new_id = canon[0]
        if cid == new_id:
            continue
        known = set(cache.get(market, {}).keys())
        if cid is not None and cid in known:
            continue  # id da co trong cat-db, khong phai dang lech keyword
        plan.append((market, name, cid, new_id, r["n"]))

    total = sum(x[4] for x in plan)
    print(f"Se sua {len(plan)} nhom ({total} dong products) de 'cung ten = cung cat_id':")
    for market, name, old, new, n in sorted(plan, key=lambda x: -x[4]):
        print(f"  {n:>5} dong | {market.upper():>2} | {name!r} cat_id {old} -> {new}")
    print("Tong:", total, "dong")
    if not args.apply:
        print("\n(day la PREVIEW - chua ghi gi. Chay lai voi --apply de ap dung.)")
        return

    for market, name, old, new, n in plan:
        if old is None:
            conn.execute(
                "update products set cat_id=? where market=? and cat_id is null and "
                "cat_name=? and lower(trim(cat_name))=?",
                (new, market, name, str(name).strip().lower()),
            )
        else:
            conn.execute(
                "update products set cat_id=? where market=? and cat_id=? and cat_name=? "
                "and lower(trim(cat_name))=?",
                (new, market, old, name, str(name).strip().lower()),
            )
    conn.commit()
    conn.close()
    print("Da ap dung.")


if __name__ == "__main__":
    main()
