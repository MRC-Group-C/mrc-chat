# rc-sync

Đưa trạng thái người dùng từ Keycloak sang Rocket.Chat (MRC-CHT-HD-01 chương XII, XIII). Tài liệu đặt rc-sync là module trong `iam-sync`. Vì repo đó chưa có ở đây, phần lõi được viết độc lập để sau này chuyển vào nguyên trạng.

## Đã có

| Thành phần | Tệp | Việc |
|---|---|---|
| Quy tắc ánh xạ | `mapping/rocketchat.yaml` | MAP-01..MAP-18, sửa quy tắc tại đây |
| Bộ máy ánh xạ | `rc_sync/mapping.py` | Hàm thuần, không gọi mạng hay CSDL |
| Client REST | `rc_sync/client.py` | Thử lại lỗi mạng và 5xx, không thử lại 4xx |
| Chuẩn hoá username | `rc_sync/username.py` | MAP-07, `setup_sso.py` dùng chung |
| Bảng dữ liệu | `migrations/` | `iam.rc_link`, `iam.rc_org_map` |

## Chưa có

Các luồng cần sự kiện từ `iam-sync`: cấp phát, cập nhật, nghỉ việc, thu hồi phiên, đối soát (STT 22–26).

## Khác với tài liệu

- **Vai trò:** bản Community không tạo được vai trò tuỳ biến, nên `svc-rc-sync` dùng role `bot` (`integration/scripts/setup_service_account.py`) và `rc-auditor` chưa ánh xạ tới vai trò nào; `map_user` trả cảnh báo `role_not_mapped:rc-auditor`.
- **Quy tắc suy diễn:** YAML dùng `derive` gọi hàm có tên thay vì `expr`, nên tệp cấu hình không thể thực thi mã.
- **Giả định cần xác nhận với `iam-sync`:** mã đơn vị là đoạn cuối của đường dẫn nhóm Keycloak (`OrgMap.group_to_code`).

## Cách dùng

Cài môi trường và chạy test từ thư mục gốc repo:

```bash
python -m venv mrc/.venv
```

```bash
mrc/.venv/Scripts/python -m pip install -r mrc/requirements-dev.txt
```

```bash
mrc/.venv/Scripts/python -m pytest mrc/tests -q
```

Tạo bảng (chạy trong `mrc/integration/rc-sync`):

```bash
RC_SYNC_DATABASE_URL="postgresql+psycopg://user:pass@host/db" alembic upgrade head
```
