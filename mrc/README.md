# MRC-Chat: Enterprise Chat & Collaboration Infrastructure

Phân hệ Chat Doanh nghiệp thuộc Hệ sinh thái Công nghệ Miracle (MRC), được xây dựng trên nền tảng **Rocket.Chat 8.9.0 LTS** kết hợp cơ sở dữ liệu **MongoDB 8.0 Replica Set** (`rs0`) và tích hợp xác thực một lần (SSO) qua **Keycloak 26.5 OIDC** (`miracle-sso`).

---

## 1. Clean Upstream Invariant (Nguyên tắc Cách ly Mã nguồn Gốc)

Toàn bộ cấu hình triển khai, kịch bản vận hành, mã nguồn tích hợp, tài liệu và kiểm thử của giải pháp MRC được đặt **100% trong thư mục `mrc/`**.
- Không can thiệp, chỉnh sửa hay xóa bất kỳ tệp tin nào thuộc kho mã nguồn gốc upstream của Rocket.Chat (`apps/`, `packages/`, `package.json`, `yarn.lock`, v.v.).
- Đảm bảo khả năng cập nhật các bản vá bảo mật định kỳ 8.9.x từ upstream mà không xảy ra xung đột mã nguồn (merge conflicts).
- Loại trừ 100% các hạng mục can thiệp bản dịch giao diện / i18n theo định hướng tối ưu hóa hiệu năng và độ ổn định lâu dài.

---

## 2. Cấu trúc Thư mục Giải pháp (`mrc/`)

```
mrc/
├── deploy/                            # Hạ tầng triển khai container
│   ├── compose.yaml                   # Cụm chính: Rocket.Chat 8.9.0 LTS + MongoDB 8.0 RS
│   ├── .env.example                   # Mẫu biến môi trường chuẩn hóa
│   └── caddy/
│       └── Caddyfile.snippet          # Reverse proxy HTTPS & WebSocket chat.miraclegroup.vn
├── infrastructure/                    # Quản trị dịch vụ nền tảng
│   ├── mongodb/
│   │   ├── init-replica.sh            # Script khởi tạo tự động Replica Set rs0
│   │   └── backup_mongo.sh            # Script sao lưu mongodump định kỳ (oplog + gzip)
│   └── debezium/                      # Cấu hình CDC Pipeline (hoãn lại theo R4)
├── integration/                       # Module và script tích hợp
│   └── scripts/                       # Scripts quản trị, setup SSO, và kiểm tra sức khỏe
├── docs/                              # Tài liệu kỹ thuật, kiến trúc và vận hành
├── tests/                             # Bộ kiểm thử tự động (pytest)
├── .gitignore                         # Loại trừ tệp bí mật (.env), data, log, backup
└── README.md                          # Tài liệu tổng quan (tệp này)
```

---

## 3. Thành phần Hạ tầng (Phase 1 / T-601)

| Dịch vụ | Image / Phiên bản | Cổng nội bộ / Host | Mục đích |
| :--- | :--- | :--- | :--- |
| **`mongodb`** | `mongodb/mongodb-community-server:8.0-ubi8` | `127.0.0.1:27017` | Cơ sở dữ liệu chính chế độ Replica Set `rs0`, hỗ trợ Oplog và Multi-doc Transactions |
| **`mongo-init-replica`** | `mongodb/mongodb-community-server:8.0-ubi8` | Container nội bộ | Dịch vụ one-shot thực thi `init-replica.sh` đảm bảo `rs0` chuyển trạng thái `PRIMARY` trước khi Rocket.Chat kết nối |
| **`rocketchat`** | `registry.rocket.chat/rocketchat/rocket.chat:8.9.0` | `127.0.0.1:3000` | Máy chủ Rocket.Chat 8.9.0 LTS cấu hình sẵn Custom OAuth Keycloak và khóa an toàn người dùng |
| **`caddy` (Proxy)** | Caddy v2 (Snippet) | `80 / 443` | Reverse proxy HTTPS cho `chat.miraclegroup.vn`, hỗ trợ WebSockets và khóa IP trang `/admin` (SEC-CHT-06) |

---

## 4. Hướng dẫn Khởi động Nhanh

### Bước 1: Thiết lập Biến Môi trường
```bash
cp mrc/deploy/.env.example mrc/deploy/.env
# Chỉnh sửa mrc/deploy/.env để điền KC_CLIENT_SECRET và cấu hình phù hợp
```

### Bước 2: Khởi chạy Cụm Dịch vụ
```bash
# Thực thi từ thư mục gốc dự án (chỉ định rõ file cấu hình môi trường):
docker compose --env-file mrc/deploy/.env -f mrc/deploy/compose.yaml up -d
```
> **Lưu ý**: Trong Docker Compose v2, khi chạy từ thư mục gốc, tham số `--env-file mrc/deploy/.env` là bắt buộc để Docker Compose đọc đúng các giá trị cấu hình thay vì tìm file `.env` ở thư mục hiện tại. Ngoài ra, bạn cũng có thể chuyển trực tiếp vào thư mục deploy:
> ```bash
> cd mrc/deploy && docker compose up -d
> ```

### Bước 3: Kiểm tra Trạng thái Dịch vụ
```bash
# Xem trạng thái các container
docker compose --env-file mrc/deploy/.env -f mrc/deploy/compose.yaml ps

# Kiểm tra trạng thái Replica Set MongoDB
docker compose --env-file mrc/deploy/.env -f mrc/deploy/compose.yaml exec mongodb mongosh --eval "rs.status().members[0].stateStr" --quiet

# Kiểm tra endpoint kiểm tra sức khỏe Rocket.Chat
curl -s http://127.0.0.1:3000/livez
```

### Bước 4: Sao lưu Cơ sở Dữ liệu Định kỳ
```bash
bash mrc/infrastructure/mongodb/backup_mongo.sh
```
