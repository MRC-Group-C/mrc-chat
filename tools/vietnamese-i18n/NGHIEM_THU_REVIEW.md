# Đánh giá nghiệm thu bản dịch tiếng Việt

## Kết luận: chưa nghiệm thu được

Công cụ và quy trình (K1–K8, glossary, VH-10) đã có trên nhánh. Artifact đang theo dõi đã hết lỗi validate trên 8.9.0. Điều kiện sản phẩm — P2 đã duyệt, 10 kịch bản TC-VH, login/email MRC, đóng góp upstream — chưa có bằng chứng đạt.

- **3/8** tiêu chí đạt
- **2/8** tiêu chí đạt một phần

Hai con số này lấy từ bản đánh giá trước và chưa được chấm lại.

## Phạm vi cập nhật

Chạy lại ngày 2026-11-05 trên commit `9db05fd2`, bằng cùng các lệnh của CI `vietnamese-i18n.yml` nhưng chạy tại máy, chưa chạy trên GitHub Actions.

| Kiểm tra | Kết quả |
| --- | --- |
| `gen_vi.py validate --source-git-ref 8.9.0` | Trước khi sửa: 0 lỗi, 107 cảnh báo (94 K6, 13 K7). **Sau khi sửa: exit 0, 0 lỗi, 89 cảnh báo (89 K6, 0 K7)** |
| Unit test công cụ | 15/15 đạt |
| Đối chiếu với starter 229 | Không mất khóa nào; cả 229 giá trị giống hệt starter (đối chiếu trước khi sửa) |

### Đã sửa trong artifact (chưa commit)

- **13 cảnh báo K7:** viết hoa chữ đầu theo bản gốc (`Analytics`, `Dashboard`, `Error`, `Saturday`, `Sunday`…). Đồng thời đổi `Wednesday` từ «Thứ 4» thành «Thứ tư» cho thống nhất với các thứ khác.
- **8 bản dịch K6 sai nghĩa:** `Livestream_live_now` («Hãy sống ngay bây giờ!»), `Chat_taken`, `Setup_Wizard` («Thuật sĩ…»), `Talk_to_sales`, `Job_Title`, `Stats_Total_Direct_Messages`, `No_Referrer`, `User_muted`.
- **34 khóa P2 còn thiếu** nay đã có (Federation, AI Search, Use_RC_SDK, trạng thái đang phát/ghi âm/tải lên…). Đây là bản **nháp do máy dịch, chưa có người duyệt**, đặt `review_status` là pending ở mức quy trình.
- **Không** nới ngưỡng K6. 89 cảnh báo còn lại chủ yếu là nhãn ngắn dài hơn gấp đôi do đặc điểm tiếng Việt (ví dụ `Emoji`, `Assets`, `Uptime`) và cần người duyệt UI quyết định.

### Kiểm thử trên localhost:3000 (Rocket.Chat 8.9.0, Docker cục bộ, không phải staging)

| Mục | Kết quả |
| --- | --- |
| Trang chủ, menu tạo mới, modal tạo kênh, menu phòng, tab thành viên, Danh bạ | Đã tiếng Việt, đúng thuật ngữ glossary |
| Ngày giờ (TC-VH-04) | Đúng dạng `04/11/2026` trên tin hệ thống và Danh bạ |
| Layout 390px (TC-VH-05) | Không vỡ trên Trang chủ và Danh bạ; chưa xem hết các màn |
| Quét chữ Latin còn sót trên Tùy chọn và Hồ sơ | Sạch. Chỉ còn "Starter" (tên gói giấy phép, không nằm trong file i18n nên Custom_Translations không dịch được) và các thẻ `<option>` ẩn chứa giá trị nội bộ, người dùng không thấy |
| Trang đăng nhập với trình duyệt English (TC-VH-01, 10) | **Không đạt, và không sửa được trong nhánh này**: client chọn `ngôn ngữ trình duyệt` trước setting `Language`, nên trình duyệt English luôn thấy trang đăng nhập tiếng Anh (hành vi của Rocket.Chat). Khung điều khoản đã tiếng Việt. Không có nút MRC vì dịch vụ Custom OAuth `Keycloak` chưa được tạo (T-603) |
| TC-VH-06, 07 | **Không test được nếu không đổi ngôn ngữ của tài khoản**: người dùng đã đăng nhập có ngôn ngữ trong hồ sơ ghi đè `localStorage`. Cần tài khoản thử riêng |
| TC-VH-09 | Chưa test (cần SMTP và email thật) |

Lỗi tìm thấy khi test và đã sửa:

- **Plural không chạy trên runtime:** client nạp khóa plural dạng phẳng `key_other`, còn `upload`, `export-env` và `update_mongo.py` đẩy dạng lồng `{"other": ...}`, nên i18next trả về object (28 khóa bị ảnh hưởng). Nay `runtime_translation()` làm phẳng ở bước nạp; artifact vẫn giữ dạng lồng để K1/K4 đối chiếu với English. Đã xác nhận: danh sách thành viên đọc "Tìm thấy 1 kết quả", không còn chuỗi lỗi.
- **Vi phạm glossary:** 14 khóa Thread dịch "Chủ đề"/"chuỗi" nay là "Luồng"; 13 khóa Private group dịch "Nhóm riêng tư" nay là "Kênh riêng tư"; "đề tài" nay là "Chủ đề"; `Platform_Windows` nay là "Windows". Các khóa Team giữ "Nhóm" vì đúng glossary.
- **Id nút MRC sai trong `deployment-settings.example.json`:** `Accounts_OAuth_Custom_Keycloak_button_label_text` tạo setting mồ côi và không bao giờ hiện nút. Đã đổi thành `Accounts_OAuth_Custom-Keycloak-button_label_text`, thêm test chống tái diễn và ghi chú trong README (setting này không tự tạo dịch vụ OAuth). Mongo cục bộ vẫn còn setting mồ côi cũ, chưa dọn.
- **Còn lại, chưa xử lý:** chính tả "xóa" chưa thống nhất thành "xoá" theo glossary (K5 chỉ cấm "Xóa bỏ").

Chưa kiểm tra lại: trạng thái workbook, các quan sát từ ảnh ở mục TC-VH, các điểm lệch so với HD-01 (trừ số khóa). Hai commit `8388cfc0` và `9db05fd2` có thể đã thay đổi một phần giao diện mà các ảnh đó ghi lại.

## Ba lớp artifact đang lệch nhau

| Lớp | Số lượng | Đơn vị | Tình trạng |
| --- | --- | --- | --- |
| Starter (Downloads) | 229 | Khóa `Custom_Translations` | Validate 8.9.0: 0 lỗi, 4 cảnh báo K6. Placeholder đúng `{{date}}` / `{{users}}` / `{{rooms}}`. Đây là mức tài liệu gọi là «khởi điểm». |
| Nhánh `feature/vietnamese-i18n` | 7370 | Khóa tracked | Validate 8.9.0: 0 lỗi, 89 cảnh báo K6, 0 cảnh báo K7. Đủ 229 khóa starter, đúng biến. Trên baseline 7518 khóa còn thiếu 148 (đều không thuộc P2); khoảng 237 giá trị giống hệt English (số đo trước khi sửa; một phần là tên riêng như `API`, `Bot`, chưa rà hết). |
| Workbook P2 / chất lượng | 2480 / 252 | P2 / hàng rà soát | Vẫn pending. Máy có thể đã đổ bản nháp vào JSON, nhưng không có trạng thái reviewed. PDF yêu cầu người đọc theo ngữ cảnh trước khi nạp. |

## Lỗi K2 — đã xử lý

Nguồn: `8.9.0:packages/i18n/src/locales/en.i18n.json` so với translations trên nhánh. Bản 4671 khóa trước đây đổi tên biến; artifact hiện tại đã trở về đúng giá trị của starter.

| Khóa | English 8.9.0 | Bản 4671 (lỗi) | Artifact hiện tại |
| --- | --- | --- | --- |
| `Search_filter_after_date` | `Messages after {{date}}` | `Tin nhắn sau {{value}}` | `Tin nhắn sau {{date}}` |
| `Search_filter_before_date` | `Messages before {{date}}` | `Tin nhắn trước {{value}}` | `Tin nhắn trước {{date}}` |
| `Search_filter_from_users` | `Messages from {{users}}` | `Tin nhắn từ @{{value}}` | `Tin nhắn của {{users}}` |
| `Search_filter_in_rooms` | `Messages in {{rooms}}` | `Tin nhắn trong #{{value}}` | `Tin nhắn trong {{rooms}}` |

## TC-VH trên bằng chứng ảnh

| Mã | Trạng thái tài liệu | Quan sát từ ảnh / repo |
| --- | --- | --- |
| TC-VH-01 | Chờ Staging | Phụ thuộc iam-sync `language=vi-VN` — ngoài commit i18n này |
| TC-VH-02 | Chờ Staging | Còn English trên Home, tab thành viên, menu tạo, menu phòng |
| TC-VH-03 | Chờ Staging | Sidebar: Kênh / Nhóm / Thảo luận. Menu tạo còn Category, Outbound message. Không thấy modal 5 loại phòng |
| TC-VH-04 | Chờ Staging | Tin hệ thống: «đã tham gia kênh» + «ngày 4 tháng 11 năm 2026». Định dạng DD/MM/YYYY chưa thấy trên tin |
| TC-VH-05 | Chờ Staging | Không có ảnh 390px. K6 còn 89 cảnh báo trên artifact lớn |
| TC-VH-06 | Chờ Staging | Preferences đã Việt; không có ảnh đổi English rồi quay lại |
| TC-VH-07 | Chờ Staging | Không có bằng chứng `locale=km` |
| TC-VH-08 | DB in-sync, chờ UI | `verify_sync.py` / `update_mongo.py` đi Mongo trực tiếp, không phải REST công bố |
| TC-VH-09 | Chờ Staging | Có mẫu `Email_Header`/`Footer` trong example JSON; không có email thật |
| TC-VH-10 | Chờ Staging | `screenshot_login.png` hoàn toàn English, branding rocket.chat, không nút MRC |

## Vì sao công cụ đạt nhưng sản phẩm chưa

### Đã làm đúng

- Pipeline chuẩn thư viện: validate, extract, workbook-queue, merge, upgrade, upload dry-run (không đọc credential), chặn nạp khi lỗi.
- Policy cấm «Thông điệp / Đội / tập tin».
- Baseline ghim 8.9.0.
- Checklist VH-10 có 3 danh sách đối chiếu.
- Glossary khớp HD-01 mục 6.4: Workspace, Team, Thread, Group được chốt và ghi người duyệt.

### Lệch so với HD-01

- PDF: dịch P2 ~2700 rồi người duyệt; P4 (Omnichannel/VoIP) không dịch. Nhánh đổ 7370/7518 khóa (98,0%, gồm ABAC/Livechat) mà chưa có bằng chứng hàng đợi đã được duyệt. Khoảng lệch này rộng hơn so với lúc còn 4671 khóa.
- PDF K4 là cảnh báo, code K4 là lỗi.
- PDF K6 160% nhãn ngắn; code ratio 2.0 với ngưỡng độ dài.
- T-634 yêu cầu gửi bản dịch web + mobile — chưa có yêu cầu nào.

## Việc cần làm trước khi ký nghiệm thu

1. Người duyệt UI rà 89 cảnh báo K6 còn lại. Phần khôi phục khóa starter và đưa artifact về 0 lỗi K1–K5/K8 trên thẻ 8.9.0 đã xong.
2. Tách P2 reviewed khỏi bản máy dịch P3/P4; đánh dấu 252 mục chất lượng trong workbook.
3. Nạp REST (không chỉ Mongo) lên staging; áp `Language`, `Layout_Login_Terms`, nút SSO, email.
4. Chạy đủ TC-VH-01..10 với ảnh 1366×768 và 390px; đặc biệt login MRC và email offline DM.
