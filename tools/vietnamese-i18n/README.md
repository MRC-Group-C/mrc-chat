# Công cụ Việt hóa MRC-CHAT

Công cụ này trích chuỗi thiếu, chạy K1-K8, ghép `Custom_Translations`, tạo báo cáo nâng cấp VH-10 và chỉ nạp setting sau khi validation đạt. Công cụ không sửa logic authentication hoặc Keycloak.

## Baseline

`baseline.json` đang ghim Rocket.Chat `8.9.0` vì workbook và bản khởi điểm được lập theo phiên bản này. Khi đổi baseline, phải chạy lệnh `upgrade` và duyệt báo cáo trước khi thay ref.

## K1-K8

| Luật | Kiểm tra | Mức mặc định |
|---|---|---|
| K1 | Key tồn tại trong English baseline | Lỗi |
| K2 | `{{...}}`, `%s`, `%d` và biến có vị trí khớp | Lỗi |
| K3 | HTML tag và React tag như `<1></1>` giữ nguyên | Lỗi |
| K4 | Cấu trúc plural hợp lệ; tiếng Việt chỉ bắt buộc `other` | Lỗi |
| K5 | Không dùng thuật ngữ cấm trong `policy.json` | Lỗi |
| K6 | Tỷ lệ chiều dài vượt ngưỡng | Cảnh báo |
| K7 | Sai viết hoa chữ đầu so với nguồn | Cảnh báo |
| K8 | JSON hợp lệ, UTF-8 không BOM, không trùng key, Unicode NFC | Lỗi |

K6-K7 là heuristic và cần người duyệt UI. CI chặn lỗi; có thể thêm `--warnings-as-errors` khi một đợt dịch đã xử lý xong toàn bộ cảnh báo.

## Lệnh chính

Chạy test công cụ:

```powershell
python -m unittest discover tools/vietnamese-i18n/tests -v
```

Kiểm tra bản khởi điểm với baseline 8.9.0:

```powershell
python tools/vietnamese-i18n/gen_vi.py --repo . validate `
  --source-git-ref 8.9.0 `
  --translation C:\duong-dan\MRC_RC_Custom-Translations_vi-VN.json `
  --policy tools/vietnamese-i18n/policy.json `
  --report-json build/vietnamese-i18n/k1-k8.json `
  --report-md build/vietnamese-i18n/k1-k8.md
```

Trích toàn bộ chuỗi chưa có trong locale gốc và Custom Translations:

```powershell
python tools/vietnamese-i18n/gen_vi.py --repo . extract `
  --source-git-ref 8.9.0 `
  --current-git-ref 8.9.0 `
  --custom C:\duong-dan\MRC_RC_Custom-Translations_vi-VN.json `
  --output build\vietnamese-i18n\missing-queue.json
```

Output là hàng đợi có English, ô Vietnamese rỗng và trạng thái `pending`; không thể nạp trực tiếp.

Trích đúng P2 và 252 mục rà soát từ workbook, không cần cài thư viện Excel:

```powershell
python tools/vietnamese-i18n/gen_vi.py workbook-queue `
  --workbook C:\duong-dan\MRC_RC_Viet-hoa_Danh-muc-chuoi.xlsx `
  --priority P2 `
  --output build\vietnamese-i18n\p2-and-quality-queue.json
```

Ghép nhiều fragment theo thứ tự, fragment sau ghi đè fragment trước:

```powershell
python tools/vietnamese-i18n/gen_vi.py --repo . merge `
  --source-git-ref 8.9.0 `
  --input translations\starter.json `
  --input translations\p2-reviewed.json `
  --policy tools/vietnamese-i18n/policy.json `
  --output build\vietnamese-i18n\MRC_RC_Custom-Translations_vi-VN.json `
  --report-md build\vietnamese-i18n\k1-k8.md
```

Dry-run REST, không đọc credential và không gọi mạng:

```powershell
python tools/vietnamese-i18n/gen_vi.py --repo . upload `
  --source-git-ref 8.9.0 `
  --translation build\vietnamese-i18n\MRC_RC_Custom-Translations_vi-VN.json `
  --policy tools/vietnamese-i18n/policy.json `
  --url https://chat-staging.example.vn
```

Nạp thật bằng tài khoản dịch vụ. Công cụ GET và lưu backup trước khi POST:

```powershell
$env:RC_USER_ID = '<service-user-id>'
$env:RC_AUTH_TOKEN = '<service-auth-token>'
python tools/vietnamese-i18n/gen_vi.py --repo . upload `
  --source-git-ref 8.9.0 `
  --translation build\vietnamese-i18n\MRC_RC_Custom-Translations_vi-VN.json `
  --policy tools/vietnamese-i18n/policy.json `
  --url https://chat-staging.example.vn `
  --apply
```

Dự phòng bằng environment setting:

```powershell
python tools/vietnamese-i18n/gen_vi.py export-env `
  --translation build\vietnamese-i18n\MRC_RC_Custom-Translations_vi-VN.json `
  --output build\vietnamese-i18n\custom-translations.env
```

Không commit file `.env`, token hoặc backup setting.

## Quy trình dịch và duyệt

1. Phòng CNTT và một trưởng nhóm kinh doanh cập nhật cột `Approval` trong `glossary.csv`; đặc biệt duyệt Team, Thread và Group.
2. Dịch hàng đợi P2 theo ngữ cảnh. Máy dịch chỉ tạo nháp; người đọc phải chuyển trạng thái sang reviewed.
3. Rà lại 252 mục chất lượng trong workbook và tạo fragment riêng để dễ review.
4. Ghép starter → P2 reviewed → quality fixes, chạy K1-K8 và TC-VH-01..10.
5. Nạp staging bằng REST. Production chỉ dùng đúng artifact đã qua nghiệm thu.
6. Khi nâng cấp, tuân thủ nghiêm ngặt [UPGRADE_CHECKLIST.md](UPGRADE_CHECKLIST.md), chạy `upgrade`, loại key đã xoá và key đã được upstream dịch rồi đính kèm báo cáo vào phiếu nâng cấp. Chạy lại TC-VH-01..05 trên staging trước khi lên production.

## Phạm vi cấu hình

Nút «Đăng nhập bằng tài khoản MRC» chỉ hiện khi dịch vụ Custom OAuth tên `Keycloak` đã được tạo và bật (T-603). Setting `Accounts_OAuth_Custom-Keycloak-button_label_text` trong file bên dưới không tự tạo dịch vụ đó; nạp nó khi dịch vụ chưa có thì không có nút nào xuất hiện.

Ngôn ngữ trang đăng nhập do trình duyệt quyết định: client chọn `ngôn ngữ trình duyệt` trước setting `Language`. Trình duyệt English vì vậy luôn thấy trang đăng nhập tiếng Anh; chỉ sau khi đăng nhập, ngôn ngữ trong hồ sơ người dùng (do `iam-sync` gán `vi-VN`) mới áp dụng. Phần Việt hoá của trang đăng nhập là `Layout_Login_Terms` và nhãn nút SSO.

`deployment-settings.example.json` là bản đề xuất để người vận hành duyệt. Giá trị rỗng bắt buộc phải được biên tập trước khi nạp. Không dùng file này làm payload tự động và không đưa credential vào repository.
