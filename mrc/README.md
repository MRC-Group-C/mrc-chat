# MRC-Chat: Enterprise Chat & Collaboration Infrastructure

Phân hệ Chat Doanh nghiệp thuộc Hệ sinh thái Công nghệ Miracle (MRC), xây dựng trên **Rocket.Chat 8.9.0 LTS**, **MongoDB 8.0 Replica Set** (`rs0`) và đăng nhập một lần (SSO) qua **Keycloak** (`miracle-sso`).

Tài liệu đặc tả: MRC-CHT-HD-01. Tiến độ: `docs/MRC-Appchat-plan.xlsx`.

---

## 1. Nguyên tắc cách ly mã nguồn gốc

Toàn bộ cấu hình triển khai, kịch bản, mã tích hợp, tài liệu và kiểm thử của MRC nằm trong `mrc/`.

- Không sửa tệp nào thuộc Rocket.Chat gốc (`apps/`, `packages/`, `package.json`, `yarn.lock`, v.v.) để cập nhật được các bản vá 8.9.x mà không xung đột.
- **Ngoại lệ duy nhất:** `.github/workflows/mrc-ci.yml`, vì GitHub bắt buộc đặt workflow ở đó. Tệp mới này không gây xung đột khi nâng cấp.
- Việt hoá đi qua điểm mở rộng chính thức là thiết lập `Custom_Translations`, không sửa tệp ngôn ngữ của Rocket.Chat.
- Không đưa bí mật vào git: giá trị thật nằm trong `deploy/.env` (đã ignore) và kho bí mật. Compose và script dừng lại nếu thiếu.

---

## 2. Cấu trúc thư mục

```
mrc/
├── deploy/
│   ├── compose.yaml            # Rocket.Chat 8.9.0 + MongoDB 8.0 RS
│   ├── compose.dev.yaml        # Chỉ dùng trên máy dev: nối tới Keycloak chạy trên host
│   ├── compose.debezium.yaml   # Debezium Server 3.7
│   ├── .env.example
│   └── caddy/Caddyfile.snippet
├── infrastructure/
│   ├── mongodb/                # init-replica.sh, backup_mongo.sh
│   └── debezium/               # conf/application.properties, sql/setup_crm_publication.sql
├── integration/
│   ├── rc-sync/                # Lõi đồng bộ Keycloak -> Rocket.Chat (xem README riêng)
│   └── scripts/                # setup_sso.py, setup_service_account.py, healthcheck.py
├── i18n/                       # Script kiểm tra, nạp bản dịch và các tệp bản dịch
├── docs/                       # setup.md, crm-schema-mapping.md, kế hoạch
├── tests/                      # pytest
└── requirements-dev.txt
```

Kế hoạch gọi hai thư mục `debezium/` và `rc-sync/`; trong repo chúng nằm ở `infrastructure/debezium/` và `integration/rc-sync/`.

---

## 3. Thành phần hạ tầng

| Dịch vụ | Image | Cổng | Mục đích |
| :--- | :--- | :--- | :--- |
| `mongodb` | `mongodb/mongodb-community-server:8.0-ubi8` | `127.0.0.1:27017` | CSDL replica set `rs0` |
| `mongo-init-replica` | `mongodb/mongodb-community-server:8.0-ubi8` | Nội bộ | Khởi tạo `rs0` một lần, chờ `PRIMARY` |
| `rocketchat` | `registry.rocket.chat/rocketchat/rocket.chat:8.9.0` | `127.0.0.1:3000` | Máy chủ chat, Custom OAuth Keycloak |
| `debezium-crm` | `quay.io/debezium/server:3.7.0.Final` | Không mở | Đọc WAL của CRM, gửi tới `iam-sync` |
| Caddy | Caddy v2 (snippet) | `80 / 443` | HTTPS, WebSocket, giới hạn `/admin` (SEC-CHT-06) |

---

## 4. Khởi động nhanh

### Bước 1: Biến môi trường

```bash
cp mrc/deploy/.env.example mrc/deploy/.env
```

Điền `KC_CLIENT_SECRET`, `ADM_LOCAL_PASSWORD`. Thiếu một trong hai thì compose báo lỗi và không chạy.

### Bước 2: Khởi chạy

```bash
docker compose --env-file mrc/deploy/.env -f mrc/deploy/compose.yaml up -d
```

Trên máy dev, khi Keycloak chạy trên host:

```bash
docker compose --env-file mrc/deploy/.env -f mrc/deploy/compose.yaml -f mrc/deploy/compose.dev.yaml up -d
```

### Bước 3: Kiểm tra

```bash
docker compose --env-file mrc/deploy/.env -f mrc/deploy/compose.yaml ps
```

```bash
curl -s http://127.0.0.1:3000/livez
```

### Bước 4: Cấu hình Keycloak và tài khoản dịch vụ

Các script đọc bí mật từ biến môi trường và không tự đọc tệp `.env`.

```bash
python mrc/integration/scripts/setup_sso.py --keycloak-claims
```

```bash
python mrc/integration/scripts/setup_service_account.py --env-file mrc/deploy/.env
```

### Bước 5: Sao lưu

```bash
bash mrc/infrastructure/mongodb/backup_mongo.sh
```

---

## 5. Kiểm thử

```bash
python -m venv mrc/.venv
```

```bash
mrc/.venv/Scripts/python -m pip install -r mrc/requirements-dev.txt
```

```bash
mrc/.venv/Scripts/python -m pytest mrc/tests -q
```

Test về realm đọc `miracle-sso/keycloak/import/mrc-realm.json` (hoặc đường dẫn trong `MRC_REALM_JSON`) và tự bỏ qua khi không có tệp này.
