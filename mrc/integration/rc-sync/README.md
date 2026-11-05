# Module Đồng Bộ Nhân Sự (rc-sync)

Mô-đun nhận sự kiện CDC (`vn.mrc.iam.*`) từ Debezium Server / `iam-sync`, chuyển đổi dữ liệu nhân sự CRM và gọi REST API của Rocket.Chat để tạo, cập nhật, điều chuyển hoặc khóa tài khoản nhân viên.

## Cấu trúc chuẩn bị (Giai đoạn 2):
- `worker.py`: Tiến trình nhận sự kiện webhook và điều phối hàng đợi.
- `rc_client.py`: Client tương tác với REST API Rocket.Chat (quản lý user, channel, role).
- `mapping/`: Logic ánh xạ dữ liệu nhân sự CRM (`pos_name`, `employee_code`, `org_unit`) sang Custom Fields của Rocket.Chat.
- `reconciliation/`: Kịch bản đối soát đối chiếu CSDL định kỳ hàng đêm lúc 02:00 sáng.
