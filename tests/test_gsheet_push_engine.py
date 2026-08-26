from gsheet_push_engine import allocate_sequential, filter_unused, to_sheet_row
from gsheet_video_scanner import ProductRow


def _row(sp_id: str) -> ProductRow:
    return ProductRow(sp_id=sp_id, product_name=f"P{sp_id}", shopee_url=f"url{sp_id}", merge_links=f"merge{sp_id}")


def test_filter_unused_excludes_previously_pushed_ids():
    pool = [_row("1"), _row("2"), _row("3")]

    result = filter_unused(pool, used_sp_ids={"2"})

    assert [row.sp_id for row in result] == ["1", "3"]


def test_allocate_sequential_no_overlap_between_accounts():
    pool = [_row(str(i)) for i in range(1, 6)]

    allocations = allocate_sequential(pool, [("acc_a", 2), ("acc_b", 2)])

    assert [row.sp_id for row in allocations[0].rows] == ["1", "2"]
    assert [row.sp_id for row in allocations[1].rows] == ["3", "4"]


def test_allocate_sequential_reports_shortfall_when_pool_runs_out():
    pool = [_row("1")]

    allocations = allocate_sequential(pool, [("acc_a", 3)])

    assert allocations[0].fulfilled == 1
    assert allocations[0].shortfall == 2


def test_allocate_sequential_leaves_later_accounts_empty_once_pool_is_exhausted():
    pool = [_row("1"), _row("2")]

    allocations = allocate_sequential(pool, [("acc_a", 2), ("acc_b", 2)])

    assert allocations[0].fulfilled == 2
    assert allocations[1].fulfilled == 0
    assert allocations[1].shortfall == 2


def test_to_sheet_row_maps_columns_per_spec():
    row = to_sheet_row(_row("999"), video_folder="D:/videos")

    assert row == ["", "999", "", "", "", "merge999", "P999", "", "", "", "", "D:/videos"]
    # B = SP ID, F = merge links, G = product name, L = folder (dedup only); A/C/D/E/H/I/J/K blank
