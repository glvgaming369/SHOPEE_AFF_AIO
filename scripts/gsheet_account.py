from dataclasses import dataclass


@dataclass
class AccountRow:
    profile: str
    selected: bool = True
    qty: int = 0
    # Số job (video) hiện có trên sheet của tài khoản này - chỉ để hiển thị, nạp lại khi
    # "Tải lại danh sách từ Sheet" và cập nhật cục bộ (không gọi thêm API) sau Push/Dọn dẹp.
    current_jobs: int = 0
    # Trong current_jobs: bao nhiêu đã có "Đăng thành công" ở cột J, bao nhiêu cột J còn
    # trống (pending). Cùng vòng đời cập nhật với current_jobs.
    completed_jobs: int = 0
    pending_jobs: int = 0
