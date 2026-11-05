# Lược đồ dữ liệu nhân sự của CRM

Kết quả xác minh theo mục 12.3 bước 5 của MRC-CHT-HD-01 (STT 3).

- **Nguồn đã đọc:** mã nguồn CRM `Byd/backend`, commit `5bf3166b`: `core/models.py`, `features/user`, `features/org`, `features/resignations`, `features/auth`.
- **Chưa đối chiếu với DB thật.** Nhiều cột được thêm bằng script `migrate_*` chứ không qua `create_all`, nên phải chạy `\d` trên từng bảng khi có bản sao CRM ở staging (STT 14).

## Thông tin kết nối

| Mục | Tài liệu giả định | Thực tế trong CRM |
|---|---|---|
| Tên database | `crm` | `byd` (`POSTGRES_DB`, mặc định `byd`) |
| Service trong compose | `crm-db` | `postgres` |
| Phiên bản | PostgreSQL 16 | `postgres:16-alpine` |

## Đối chiếu tên bảng

| Tên trong tài liệu | Bảng thật | Ghi chú |
|---|---|---|
| `users` | `users` | Đúng tên |
| `org_units` | `org_units` | Đúng tên |
| `user_org_units` | `user_org_membership` | Nhiều-nhiều, `is_primary` đánh dấu vị trí chính |
| `resignations` | Không tồn tại | Xem mục "Luồng nghỉ việc" |

## Cột của các bảng được theo dõi

Cột **in đậm** bị loại khỏi CDC.

| Bảng | Cột |
|---|---|
| `users` | `id`, `username`, `email`, **`password_hash`**, `full_name`, **`phone`**, `employee_code`, `pos_name`, **`date_of_birth`**, `employment_status`, `status`, **`token_version`**, **`must_change_password`**, **`temp_password_issued_at`**, `last_login_at`, `created_at`, `updated_at` |
| `org_units` | `id`, `parent_id`, `level`, `name`, `code`, `path`, `active`, `head_user_id`, `created_at`, `updated_at` |
| `user_org_membership` | `id`, `user_id`, `org_unit_id`, `is_primary`, `title`, `job_title_code`, `created_at` |
| `resignation_decisions` | `id`, `request_id`, `decision_no`, `year`, **`employee_name`**, `team_name`, `job_title`, **`reason`**, `effective_date`, **`extra_terms`**, `status`, `created_by`, `submitted_at`, `signer_id`, `signed_by`, `signed_at`, `returned_at`, **`return_reason`**, `revoked_at`, `revoked_by`, **`revoke_reason`**, `created_at`, `updated_at` |

## Cột nhạy cảm

| Cột | Lý do loại |
|---|---|
| `users.password_hash` | Băm mật khẩu |
| `users.token_version` | Phần của phiên đăng nhập: cookie ký chứa `uid` và `tv` (`features/auth/auth_service.py`) |
| `users.must_change_password`, `users.temp_password_issued_at` | Trạng thái mật khẩu |
| `users.date_of_birth`, `users.phone` | Dữ liệu cá nhân, chat không dùng |
| `resignation_decisions.reason`, `extra_terms`, `return_reason`, `revoke_reason`, `employee_name` | Nội dung nhân sự dạng tự do và họ tên |

- Tài liệu nêu `session_token`, `id_card_no`, nhưng **hai cột này không tồn tại**. CRM không có bảng phiên, và không tìm thấy cột CCCD trong mã nguồn hay `docs/DATABASE_SCHEMA.dbml`.
- `iam-sync` đọc lại bản ghi đầy đủ qua API (mục 12.5), nên CDC chỉ cần tín hiệu thay đổi và khoá.
- `column.exclude.list` chỉ lọc ở Debezium. WAL vẫn chứa các cột này và `dbz_crm` vẫn đọc được bảng, nên mật khẩu của `dbz_crm` phải được bảo vệ như thông tin truy cập CSDL nhân sự.

## Luồng nghỉ việc

Dữ liệu nằm ở ba bảng:

| Bảng | Vai trò | Theo dõi bằng CDC |
|---|---|---|
| `leave_requests` | Đơn, `request_type='RESIGN'`, có `employee_id` | **Không**, vì chứa cả đơn nghỉ phép thường kèm lý do và số điện thoại liên hệ |
| `resignation_approvals` | Các cấp duyệt | Không |
| `resignation_decisions` | Quyết định, `request_id` → `leave_requests.id`, `status`, `effective_date` | Có |

`resignation_decisions` không có khoá trực tiếp tới người nghỉ. `iam-sync` phải tra `request_id` qua API CRM để biết `employee_id`.

**Hành vi `offboard()` (`features/resignations/decision_sign_service.py`):**
1. Đặt `users.status='disabled'`.
2. Tăng `token_version`.
3. **Xoá toàn bộ dòng `user_org_membership`** của người đó.
4. Ghi audit `user.offboard`.

**Hệ quả cho rc-sync:**
- `users.status` là tín hiệu khoá chính; dòng `users` không bao giờ bị xoá.
- `REPLICA IDENTITY FULL` trên `user_org_membership` là bắt buộc để biết người đó vừa rời đơn vị nào.
- Quyết định ký với ngày tương lai **chưa khoá**. `resignation_cron` chạy lúc 06:00 mới khoá khi tới ngày.
- Chỉ tiêu "15 phút từ thời điểm hiệu lực" của INT-CHT-04 vì vậy tính từ lúc CRM khoá, không phải từ 00:00 ngày hiệu lực. **Cần HCNS xác nhận cách hiểu này.**
- Thu hồi quyết định có thể mở lại tài khoản và dựng lại membership (`_restore()`), tức CHT-LU-07.

## Khác biệt khác so với tài liệu

| Nội dung | Tài liệu | Thực tế | Việc cần làm |
|---|---|---|---|
| `org_units.level` | Số 1 đến 5 | Chuỗi `division`, `department`, `team` | Tính cấp số từ `path`/`parent_id` khi nạp `iam.rc_org_map` (STT 27) |
| Mã đơn vị | `ORG-KT-CN` | Dạng `MIRACLE.BO`, `MIRACLE.MKT` | Dùng `org_units.code` làm `org_unit_code` |
| Mã nhân viên | `MRC-0142` | `users.employee_code`, dạng `BYD0001`, có thể NULL | `iam.rc_link.employee_id` phải xử lý trường hợp thiếu mã |
| Chức danh | Một giá trị trên người | Nằm trên từng dòng membership | Lấy từ dòng `is_primary` cho `customFields.job_title` |
| Username | `ngoc.tb` | Sinh theo `features/user/username.py`, dạng `anhtt` | Đều hợp lệ với kiểm tra MAP-07 |

## Lưu lượng sự kiện

Mỗi lần đăng nhập CRM cập nhật `users.last_login_at`, nên sinh một sự kiện CDC dù hồ sơ không đổi. Cửa sổ gom 10 giây của `iam-sync` và băm hồ sơ của rc-sync sẽ chặn các lần gọi API thừa.

## Kết quả diễn tập (05/11/2026)

Chạy cấu hình trong repo với Debezium Server 3.7.0.Final trên Postgres tạm, dùng bảng có cấu trúc như trên và dữ liệu giả. **Chưa chạy trên CRM thật hay bản sao của nó.**

| Kiểm tra | Kết quả |
|---|---|
| `setup_crm_publication.sql` chạy được, publication gồm đúng 4 bảng | Đạt |
| Chụp ban đầu đủ dữ liệu của 4 bảng (`op = r`) | Đạt |
| Không lộ cột nhạy cảm trong sự kiện thô (TC-CHT-11) | Đạt: 0 lần xuất hiện dấu đánh trong dữ liệu giả |
| `leave_requests` và bảng lương không được phát | Đạt |
| Sự kiện xoá `user_org_membership` có dòng cũ (`before`) | Đạt |
| Chữ ký Standard Webhooks, lệch giờ dưới 5 phút | Đạt: 22/22 sự kiện hợp lệ, secret dạng `whsec_<base64>` |
| Dừng Debezium, sửa 5 hồ sơ, chạy lại (bản rút gọn TC-CHT-10) | Đạt: nhận đủ 5 thay đổi |

**Lưu ý cho `iam-sync`:** có một sự kiện không có `source.table`, nhiều khả năng là heartbeat, nên điểm nhận phải bỏ qua loại này. Thời gian dừng khi diễn tập chỉ vài giây; kịch bản 2 giờ của TC-CHT-10 và quy trình chụp lại khi mất offset vẫn phải chạy trên staging (STT 18).
