"""Split the matched video pool sequentially across selected accounts and map columns."""
from __future__ import annotations

from dataclasses import dataclass, field

from gsheet_video_scanner import ProductRow


@dataclass
class AccountAllocation:
    profile: str
    requested: int
    rows: list[ProductRow] = field(default_factory=list)

    @property
    def fulfilled(self) -> int:
        return len(self.rows)

    @property
    def shortfall(self) -> int:
        return self.requested - self.fulfilled


def filter_unused(pool: list[ProductRow], used_sp_ids: set[str]) -> list[ProductRow]:
    """Drop rows whose SP ID was already pushed to any account sheet in a previous run."""
    return [row for row in pool if row.sp_id not in used_sp_ids]


def allocate_sequential(
    pool: list[ProductRow], accounts: list[tuple[str, int]]
) -> list[AccountAllocation]:
    """Consume `pool` top-to-bottom; each account gets its next `qty` videos, no overlap."""
    remaining = list(pool)
    allocations: list[AccountAllocation] = []
    for profile, qty in accounts:
        qty = max(0, qty)
        taken, remaining = remaining[:qty], remaining[qty:]
        allocations.append(AccountAllocation(profile=profile, requested=qty, rows=taken))
    return allocations


def to_sheet_row(row: ProductRow, video_folder: str) -> list[str]:
    """Map a matched product row onto the A..L columns of a per-account sheet.

    A(SP ID)->B, B(product name)->G, and P(merge links)->F are pushed; every other
    product-data column (A, C, D, E, H, I, J, K) stays blank. Column L is not part of
    that product-data mapping — it exists solely so
    gsheet_sheets_client.get_used_video_ids() can stay folder-scoped for dedup.
    """
    return [
        "",                   # A - Link Sản Phẩm làm Video (khong push)
        row.sp_id,            # B - Tên Sản Phẩm  <- SP ID
        "",                   # C - Tên Video
        "",                   # D - Nội Dung Video
        "",                   # E - Prompt Tạo Video Ai
        row.merge_links,      # F - Link Sản Phẩm Muốn Gắn Giỏ
        row.product_name,     # G - Caption Cho Sản Phẩm  <- Tên sản phẩm
        "",                   # H - Thứ Tự Ảnh
        "",                   # I - Report
        "",                   # J - Report 1
        "",                   # K - Report 2 (khong push)
        video_folder,          # L - Thư Mục Video (chi de chong trung theo thu muc)
    ]
