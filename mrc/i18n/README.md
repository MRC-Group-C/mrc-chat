# Phân Hệ Bản Dịch & Việt Hóa MRC-Chat (i18n)

> **Tuân thủ Clean Upstream Invariant**: Toàn bộ script và từ điển mở rộng nằm tại `mrc-chat/mrc/i18n/`. Không sửa trực tiếp mã nguồn gốc Rocket.Chat trừ khi có chỉ định rõ ràng.

## 1. Cấu trúc thư mục
- `scripts/translate_locales.py`: Script quét trực tiếp các bản dịch từ `packages/i18n/src/locales/`, tự động xác định ngôn ngữ gốc có số lượng khóa lớn nhất (`en.i18n.json` với 7.518 khóa), tổng hợp các khóa thiếu và dịch tự động mà **không phải quét AST/grep toàn bộ dự án**.
- `locales/`:
  - `vi-VN.i18n.json`: Bản dịch đầy đủ (7.518 khóa).
  - `custom-translations.vi-VN.json`: Tệp JSON định dạng `{"vi-VN": {...}}` dùng để nạp động qua Rocket.Chat REST API (`Custom_Translations`).
- `translations_cache.json`: Bộ nhớ đệm các chuỗi đã dịch để tái sử dụng, tăng tốc và tránh gọi lặp lại.

## 2. Các quy tắc chất lượng (K1 - K8) & Bảng thuật ngữ MRC
Script cài đặt sẵn cơ chế kiểm tra và chuẩn hóa theo tài liệu kỹ thuật **MRC-CHT-HD-01**:
1. **Bảo vệ biến suy diễn**: Che chắn an toàn các biến `{{variable}}`, `%s`, `%d`, thẻ HTML `<1></1>`, markdown `**bold**` trước khi dịch.
2. **Chuẩn hóa thuật ngữ (Luật K5 & Mục 6.4)**:
   - "Message" -> "Tin nhắn" (cấm dùng "Thông điệp").
   - "Team" -> "Nhóm" (cấm dùng "Đội").
   - "File/Upload" -> "Tệp / Tải lên" (cấm dùng "tập tin").
   - "Cancel" -> "Huỷ" (viết hoa chữ đầu, kiểu bỏ dấu mới).
   - "Private channel" -> "Kênh riêng tư" (tránh nhầm với Team).
3. **Chuẩn hóa Unicode NFC & Viết hoa chữ đầu**: Đảm bảo hiển thị chính xác và đồng nhất.

## 3. Hướng dẫn sử dụng `translate_locales.py`

### 3.1. Chạy mô phỏng / kiểm tra thống kê (Dry-run)
```powershell
python mrc/i18n/scripts/translate_locales.py --dry-run
```

### 3.2. Dịch theo mức độ ưu tiên (P1: Giao diện người dùng cốt lõi)
```powershell
python mrc/i18n/scripts/translate_locales.py --priority P1
```

### 3.3. Dịch thử nghiệm một số lượng khóa cụ thể
```powershell
python mrc/i18n/scripts/translate_locales.py --limit 50
```

### 3.4. Dịch toàn bộ và nạp trực tiếp qua Rocket.Chat REST API
```powershell
$env:RC_URL="http://localhost:3000"
$env:RC_UID="<user-id-admin-hoac-svc>"
$env:RC_TOKEN="<personal-access-token>"
python mrc/i18n/scripts/translate_locales.py --upload-api
```

### 3.5. Chạy kiểm thử tự động
```powershell
pytest mrc/tests/test_translate_locales.py
```
