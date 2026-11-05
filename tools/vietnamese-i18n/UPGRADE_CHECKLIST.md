# Quy trình Đối chiếu Bản dịch khi Nâng cấp (VH-10)

Tài liệu này là checklist bắt buộc cho Phòng CNTT và QA/PO mỗi khi nâng cấp Rocket.Chat (từ bản vá `8.9.x` hoặc chuẩn bị cho bản `9.x`).

---

## 1. Mục đích và Nguyên tắc

* **Mục đích:** Đảm bảo bản dịch tùy biến (`Custom_Translations`) không bị lỗi thời, không chứa khóa rác, không ghi đè vô ích khi bản gốc đã hoàn thiện, và không bỏ sót các khóa mới.
* **Nguyên tắc:** 
  1. Phiếu yêu cầu thay đổi (RFC) nâng cấp chỉ được duyệt khi có báo cáo đối chiếu VH-10.
  2. Bắt buộc nghiệm thu đạt các kịch bản kiểm thử giao diện `TC-VH-01` đến `TC-VH-05` trên môi trường Staging trước khi nâng cấp Production.

---

## 2. Checklist các bước thực hiện

### Bước 1: Thu thập phiên bản và trích xuất dữ liệu gốc
- [ ] Ghi nhận thẻ phiên bản mục tiêu (Target Ref), ví dụ: `8.9.1` hoặc `9.0.0-rc.4`.
- [ ] Tải `en.i18n.json` và `vi-VN.i18n.json` của phiên bản mới từ kho mã nguồn gốc:
  ```powershell
  git fetch origin tag <TARGET_TAG> --no-tags
  ```

### Bước 2: Chạy công cụ đối chiếu tự động
- [ ] Thực thi lệnh `upgrade` của công cụ `gen_vi.py`:
  ```powershell
  python tools/vietnamese-i18n/gen_vi.py --repo . upgrade `
    --old-source-ref 8.9.0 `
    --new-source-ref <TARGET_TAG> `
    --custom tools/vietnamese-i18n/translations/MRC_RC_Custom-Translations_vi-VN.json `
    --output build/vietnamese-i18n/VH-10-report.json
  ```

### Bước 3: Đánh giá 3 danh sách đối chiếu
- [ ] **Danh sách 1: Khóa bị xóa ở bản mới (`removed_keys`):**
  - Loại bỏ các khóa này khỏi `MRC_RC_Custom-Translations_vi-VN.json` để tránh phình dung lượng CSDL và nạp cấu hình rác.
- [ ] **Danh sách 2: Khóa đã được bản gốc dịch (`custom_keys_now_in_upstream_vi`):**
  - So sánh bản dịch gốc của Rocket.Chat với bản dịch của MRC.
  - Nếu bản gốc đã chuẩn theo `glossary.csv`: Loại bỏ khỏi Custom Translations để giảm tải bộ nhớ client.
  - Nếu bản dịch MRC tốt hơn: Tiếp tục giữ lại trong Custom Translations.
- [ ] **Danh sách 3: Khóa mới xuất hiện (`new_keys`):**
  - Phân loại khóa theo mức ưu tiên (P1: Giao diện cốt lõi, P2: Người dùng cuối, P3: Quản trị, P4: Ngoài phạm vi).
  - Đưa các khóa P1, P2 vào hàng đợi dịch trước khi mở phiên bản mới cho người dùng.

### Bước 4: Kiểm tra tính toàn vẹn (K1–K8)
- [ ] Chạy kiểm tra chất lượng tệp bản dịch sau khi cập nhật:
  ```powershell
  python tools/vietnamese-i18n/gen_vi.py --repo . validate `
    --source-git-ref <TARGET_TAG> `
    --translation tools/vietnamese-i18n/translations/MRC_RC_Custom-Translations_vi-VN.json `
    --policy tools/vietnamese-i18n/policy.json
  ```
- [ ] Đạt điều kiện: **0 Lỗi (Errors = 0)**. Mọi cảnh báo K6, K7 phải được người duyệt xác nhận.

### Bước 5: Nạp Staging và Kiểm thử hồi quy
- [ ] Nạp bản dịch mới lên môi trường Staging qua REST API:
  ```powershell
  $env:RC_USER_ID = "svc-rc-admin-id"
  $env:RC_AUTH_TOKEN = "svc-token"
  python tools/vietnamese-i18n/gen_vi.py --repo . upload `
    --source-git-ref <TARGET_TAG> `
    --translation tools/vietnamese-i18n/translations/MRC_RC_Custom-Translations_vi-VN.json `
    --url https://chat-staging.miraclegroup.vn `
    --apply
  ```
- [ ] Thực hiện 5 kịch bản kiểm thử nghiệm thu hồi quy (đính kèm ảnh chụp màn hình):
  - [ ] **TC-VH-01:** Mở trang đăng nhập bằng trình duyệt English -> Hiển thị tiếng Việt.
  - [ ] **TC-VH-02:** Duyệt các màn hình chính -> Không còn chuỗi tiếng Anh P1/P2.
  - [ ] **TC-VH-03:** Tạo phòng, kênh, nhóm, thảo luận -> Phân biệt đúng loại phòng.
  - [ ] **TC-VH-04:** Nội suy biến `{{...}}`, định dạng ngày `DD/MM/YYYY`.
  - [ ] **TC-VH-05:** Không vỡ layout, không tràn chữ trên viewport 1366×768 và Mobile 390px.

---

## 3. Phê duyệt chuyển đổi Production

- **Người lập báo cáo:** Quân (Kỹ sư i18n) - Ngày: ....................
- **Người kiểm thử Staging:** QA Lead - Ngày: ....................
- **Phê duyệt nâng cấp Production:** PO / IT Lead - Ngày: ....................
