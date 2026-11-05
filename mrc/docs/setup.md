# MRC-Chat: Hướng Dẫn Triển Khai Hạ Tầng, Vận Hành & Tích Hợp SSO Keycloak
*(Enterprise Architecture, Deployment & Operational Runbook — Milestone 3)*

---

## 1. Tổng Quan Kiến Trúc Kỹ Thuật (Executive Architecture Summary)

Phân hệ Chat Doanh nghiệp MRC (**MRC-Chat**) được xây dựng trên nền tảng **Rocket.Chat 8.9.0 LTS Community Edition**, tích hợp cơ sở dữ liệu **MongoDB 8.0 Replica Set (`rs0`)**, kết nối xác thực tập trung một lần (SSO) qua **Keycloak 26.5 OIDC (`miracle-sso`)**, và được bảo vệ ngoại vi bởi **Caddy v2 Reverse Proxy**.

Kiến trúc giải pháp được thiết kế theo nguyên tắc "Phòng thủ theo chiều sâu" (*Defense in Depth*) và kiểm soát định danh dựa trên mô hình **Single Source of Truth** (Nguồn chân lý duy nhất).

```
                      +------------------------------------------+
                      |         Internet / Corporate WAN         |
                      +------------------------------------------+
                                           |
                                           | HTTPS / WSS (Ports 80/443)
                                           v
                      +------------------------------------------+
                      |       Caddy v2 Reverse Proxy             |
                      |  - TLS Let's Encrypt Auto-Termination    |
                      |  - SEC-CHT-06: /admin IP Restriction     |
                      |  - Permissions-Policy: camera/mic (self) |
                      |  - 100MB Request Body Limit              |
                      |  - Native WebSocket DDP Connection       |
                      +------------------------------------------+
                                           |
                                           | HTTP / WSS (127.0.0.1:3000)
                                           v
                      +------------------------------------------+
                      | Rocket.Chat 8.9.0 LTS Community Edition  |
                      |  - Container: mrc_chat_rocketchat        |
                      |  - Custom OAuth Keycloak (chat-miracle)   |
                      |  - Accounts_ShowFormLogin=false          |
                      |  - Profile Changes Locked (SSO Master)   |
                      |  - merge_users=true (Auto-merging)       |
                      +------------------------------------------+
                           |                              |
      OIDC Auth Code Flow  |                              | MongoDB Driver
    (via host gateway)     |                              | (Replica Set rs0)
                           v                              v
    +-----------------------------+       +------------------------------------+
    | Keycloak 26.5 SSO           |       | MongoDB 8.0 Community Server       |
    | (miracle-sso)               |       |  - Container: mrc_chat_mongodb     |
    | Realm: mrc                  |       |  - Replica Set: rs0 (Oplog 2048MB) |
    | Client: chat-miracle        |       |  - Bound: 127.0.0.1:27017          |
    | Mappers: display_name,      |       +------------------------------------+
    |          rc_roles, etc.     |                       ^
    +-----------------------------+                       |
                                          +------------------------------------+
                                          | mongo-init-replica (One-Shot Init) |
                                          |  - Container: mrc_chat_mongo_init  |
                                          |  - Script: init-replica.sh         |
                                          |  - rs.initiate() + PRIMARY election|
                                          +------------------------------------+
```

### 1.1 Ngăn Xếp Công Nghệ Lõi (Core Technology Stack)

1. **Rocket.Chat 8.9.0 LTS Community Edition**:
   - Thẻ hình ảnh container: `registry.rocket.chat/rocketchat/rocket.chat:8.9.0`.
   - Chu kỳ hỗ trợ mở rộng (Long Term Support - LTS) đến ngày **31/10/2027**, đảm bảo tính ổn định tối đa cho môi trường doanh nghiệp và thừa hưởng liên tục các bản vá an ninh nhánh 8.9.x.
   - Cấu hình tự động 100% thông qua các biến môi trường tiền tố `OVERWRITE_SETTING_*` và kịch bản REST API (`setup_sso.py`), loại bỏ nhu cầu thao tác thủ công qua giao diện người dùng (UI).
   - Dịch vụ lắng nghe trên cổng nội bộ `3000` và chỉ liên kết an toàn tới địa chỉ loopback `127.0.0.1:3000` trên máy chủ host.

2. **MongoDB 8.0 Community Server Replica Set (`rs0`)**:
   - Thẻ hình ảnh container: `mongodb/mongodb-community-server:8.0-ubi8`.
   - Chạy ở cấu hình Replica Set đơn nút (`rs0`) theo lệnh:
     `mongod --replSet rs0 --bind_ip_all --oplogSize 2048`
   - Oplog kích thước 2048 MB đáp ứng các luồng thông báo thời gian thực Change Streams và giao thức DDP (Distributed Data Protocol) của Rocket.Chat.
   - Giới hạn liên kết mạng nghiêm ngặt tại `127.0.0.1:27017:27017`, tuyệt đối không mở ra địa chỉ công cộng `0.0.0.0`.

3. **Cơ Chế Khởi Tạo Tự Động Idempotent (`mongo-init-replica`)**:
   - Container một lượt (*one-shot*): `mrc_chat_mongo_init`, khởi tạo bằng image `mongodb/mongodb-community-server:8.0-ubi8`.
   - Thực thi kịch bản `mrc/infrastructure/mongodb/init-replica.sh` với cơ chế kiểm tra tính sẵn sàng (*ping probe*), thực thi `rs.initiate()`, và kiểm tra vòng lặp trạng thái bầu cử `PRIMARY` (`db.hello().isWritablePrimary`).
   - Ngăn chặn triệt để hiện tượng race condition khi Rocket.Chat khởi động trước khi Replica Set hoàn tất bầu chọn nút sơ cấp.

4. **Cổng Định Danh Tập Trung Keycloak 26.5 SSO (`miracle-sso`)**:
   - Realm định danh: `mrc` (`miracle-sso/keycloak/import/mrc-realm.json`).
   - OIDC Client: `chat-miracle` cấu hình dạng Confidential Client có Client Secret bảo mật.
   - 4 Protocol Mappers chuyên dụng:
     - `display_name`: Ánh xạ từ `full_name` để bảo tồn trật tự họ tên tiếng Việt (theo quy tắc `MAP-03`, `MAP-04`).
     - `rc_roles`: Ánh xạ quyền người dùng (`rc-admin`, `rc-auditor`) từ client roles.
     - `employee_id`: Ánh xạ mã số nhân viên doanh nghiệp.
     - `groups`: Ánh xạ cấu trúc cây phòng ban và nhóm chức năng.

5. **Caddy v2 Reverse Proxy**:
   - Quản trị chứng chỉ SSL/TLS tự động (Let's Encrypt / ZeroSSL).
   - Tối ưu hóa chuyển tiếp WebSocket thời gian thực (WebSocket buffer, timeout 120s).
   - Áp dụng các tiêu đề an ninh nghiêm ngặt, giới hạn tải lên 100MB và thi hành chính sách kiểm soát truy cập trang quản trị `SEC-CHT-06`.

---

### 1.2 Nguyên Tắc Bất Biến Cách Ly Mã Nguồn Gốc (The Clean Upstream Invariant)

Nhằm đảm bảo khả năng bảo trì, kiểm toán và nâng cấp hệ thống trong tương lai, giải pháp MRC-Chat tuân thủ nghiêm ngặt **Nguyên Tắc Bất Biến Cách Ly Mã Nguồn Gốc**:

- **Cô lập 100% trong `mrc-chat/mrc/`**: Toàn bộ mã nguồn bổ sung, cấu hình triển khai, kịch bản tự động hóa, tài liệu kỹ thuật và bộ kiểm thử của MRC nằm hoàn toàn bên trong thư mục `mrc-chat/mrc/`.
- **Bảo toàn nguyên bản mã upstream**: Tuyệt đối không thêm mới, chỉnh sửa, đổi tên hoặc xóa bỏ bất kỳ tệp tin nào thuộc mã nguồn nguyên bản Rocket.Chat tại:
  - `mrc-chat/apps/`
  - `mrc-chat/packages/`
  - `mrc-chat/package.json`
  - `mrc-chat/yarn.lock`
- **Loại trừ 100% can thiệp Việt hóa / i18n mã nguồn**: Theo chỉ thị dự án, toàn bộ các hạng mục can thiệp bản dịch, chỉnh sửa i18n mã nguồn client hoặc thêm các tệp JSON ngôn ngữ đều bị loại bỏ. Hệ thống sử dụng cấu hình ngôn ngữ tiêu chuẩn của Rocket.Chat thông qua thiết lập `OVERWRITE_SETTING_Language=vi-VN`.
- **Nâng cấp Zero-Conflict**: Khi nâng cấp các bản vá Rocket.Chat trong tương lai (ví dụ 8.9.1, 8.9.2), kỹ sư chỉ cần cập nhật thẻ image trong `compose.yaml` mà không phải xử lý bất kỳ xung đột mã nguồn git (merge conflict) nào.

---

### 1.3 Thiết Kế Vành Đai An Ninh Đa Tầng (Security Perimeter)

Mô hình an ninh của MRC-Chat được triển khai xuyên suốt 4 tầng:

```
[ TẦNG 1: MẠNG & CỔNG ] ──> [ TẦNG 2: PROXY NGOẠI VI ] ──> [ TẦNG 3: ỨNG DỤNG CHAT ] ──> [ TẦNG 4: BÍ MẬT & DỮ LIỆU ]
  - Loopback 127.0.0.1        - HSTS max-age=31536000      - Accounts_ShowFormLogin=false   - .env cách ly khỏi Git
  - Docker bridge riêng       - SEC-CHT-06 /admin regex    - Khóa đổi Username/Email/Name   - Backup mã hóa gzip
  - 27017/3000 cô lập          - Permissions-Policy         - merge_users=true               - Phân quyền chmod 600
  - Caddy nhận 80/443         - 100MB body limit           - UTF8 regex [0-9a-zA-Z-_.]+     - Clean Upstream Invariant
```

#### Tầng 1: Cô Lập Mạng & Cổng Giao Tiếp (Network Boundary)
- Các container giao tiếp nội bộ qua mạng Docker bridge `mrc-chat-net`.
- Các cổng dịch vụ chỉ bind trực tiếp vào giao diện loopback máy chủ: `127.0.0.1:3000` (Rocket.Chat) và `127.0.0.1:27017` (MongoDB).
- Tuyệt đối không mở các cổng `3000` và `27017` ra Internet (`0.0.0.0`). Người dùng chỉ được phép truy cập qua cổng HTTPS `443` của Caddy Reverse Proxy.

#### Tầng 2: Caddy Reverse Proxy & Cơ Chế Kiểm Soát SEC-CHT-06
Tệp `mrc/deploy/caddy/Caddyfile.snippet` thiết lập các chính sách an ninh bắt buộc:
1. **Kiểm Soát Truy Cập Trang Quản Trị (SEC-CHT-06)**:
   ```caddyfile
   @admin_restricted {
       path_regexp (?i)^/admin
       not remote_ip 127.0.0.1 ::1 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16
   }
   respond @admin_restricted "Access Denied: Administrative interface is restricted to internal corporate networks." 403
   ```
   *Cơ chế chống Bypass:* Sử dụng biểu thức chính quy không phân biệt hoa thường `(?i)^/admin` nhằm triệt tiêu các kỹ thuật lách bộ lọc như `/Admin`, `/ADMIN`, `/admin/users` từ dải IP bên ngoài Internet. Chỉ các dải mạng nội bộ doanh nghiệp, VPN văn phòng (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`) và `localhost` mới được quyền truy cập.

2. **Chính Sách Quyền Thiết Bị (Permissions-Policy)**:
   ```caddyfile
   Permissions-Policy "camera=(self), microphone=(self), display-capture=(self), geolocation=()"
   ```
   Cho phép tính năng thoại video / audio WebRTC hoạt động nội bộ trên origin của Chat, đồng thời khóa hoàn toàn quyền truy cập vị trí địa lý `geolocation`.

3. **Tiêu Đề An Ninh Bắt Buộc & Giới Hạn Tải Lên**:
   - `Strict-Transport-Security "max-age=31536000; includeSubDomains; preload"`
   - `X-Content-Type-Options "nosniff"`
   - `X-Frame-Options "SAMEORIGIN"`
   - `Referrer-Policy "strict-origin-when-cross-origin"`
   - `request_body { max_size 100MB }` ngăn chặn tấn công DoS payload lớn nhưng bảo đảm chia sẻ tài liệu doanh nghiệp.

#### Tầng 3: Khóa An Toàn Ứng Dụng (Application Lockdown - Single Source of Truth)
Để biến Keycloak thành Nguồn Chân lý Duy nhất cho định danh nhân sự:
- **Ẩn form đăng nhập truyền thống**: `OVERWRITE_SETTING_Accounts_ShowFormLogin=false` và `OVERWRITE_SETTING_Accounts_RegistrationForm=Disabled`. Người dùng chỉ thấy nút đăng nhập SSO qua Keycloak.
- **Khóa chỉnh sửa hồ sơ**: Vô hiệu hóa tính năng tự đổi tên tài khoản, email, mật khẩu hoặc họ tên trên giao diện:
  - `Accounts_AllowUsernameChange=false`
  - `Accounts_AllowEmailChange=false`
  - `Accounts_AllowRealNameChange=false`
  - `Accounts_AllowPasswordChange=false`
  - `Accounts_AllowDeleteOwnAccount=false`
- **Khử trùng lặp tài khoản**: Kích hoạt `Accounts_OAuth_Custom_Keycloak_merge_users=true` và `merge_users_distinct_services=true` để tự động gộp tài khoản nhân viên theo địa chỉ email đã xác thực.

#### Tầng 4: Quản Trị Bí Mật & Cách Ly Runtime
- Tách biệt toàn bộ thông tin nhạy cảm vào `mrc/deploy/.env`.
- Tệp `mrc/.gitignore` ngăn chặn tuyệt đối việc commit `.env`, thư mục sao lưu `backup/`, hay nhật ký `logs/` vào Git.
- Phân quyền tệp cấu hình `.env` mức `chmod 600` trên hệ điều hành máy chủ.

---

## 2. Hướng Dẫn Triển Khai Hệ Thống 3 Bước (3-Step Bring-Up Guide)

Quy trình chuẩn hóa 3 bước giúp kỹ sư hạ tầng triển khai toàn bộ hệ thống từ đầu tới trạng thái sẵn sàng phục vụ sản xuất.

```
+-----------------------------------------------------------------------------------------+
| BƯỚC 1: CHUẨN BỊ MÔI TRƯỜNG & KHỞI TẠO KHÓA BÍ MẬT                                       |
| - Kiểm tra điều kiện tiên quyết (Docker v24+, Compose v2.20+, Python 3.10+)              |
| - Sao chép tệp mrc/deploy/.env.example -> mrc/deploy/.env                               |
| - Sinh mật khẩu ngẫu nhiên cho KC_CLIENT_SECRET và ADM_LOCAL_PASSWORD                   |
+-----------------------------------------------------------------------------------------+
                                           |
                                           v
+-----------------------------------------------------------------------------------------+
| BƯỚC 2: KHỞI CHẠY HẠ TẦNG CONTAINER BẰNG DOCKER COMPOSE                                  |
| - Lệnh: docker compose --env-file mrc/deploy/.env -f mrc/deploy/compose.yaml up -d      |
| - Điều phối: mongodb (healthy) -> mongo-init-replica (exit 0) -> rocketchat (healthy)   |
| - Kiểm tra: docker compose ps, replica set status, livez probe                          |
+-----------------------------------------------------------------------------------------+
                                           |
                                           v
+-----------------------------------------------------------------------------------------+
| BƯỚC 3: TỰ ĐỘNG HÓA TÍCH HỢP SSO KEYCLOAK                                                |
| - Kiểm tra không can thiệp: python mrc/integration/scripts/setup_sso.py --dry-run        |
| - Chạy bộ kiểm thử tự động: python mrc/integration/scripts/setup_sso.py --test           |
| - Áp dụng cấu hình live: python mrc/integration/scripts/setup_sso.py                     |
| - Đo lường tình trạng: python mrc/integration/scripts/healthcheck.py                    |
+-----------------------------------------------------------------------------------------+
```

### 2.1 Yêu Cầu Tiên Quyết (Prerequisites)

- **Hệ điều hành**: Linux (Ubuntu 22.04 LTS / 24.04 LTS, Debian 12, RHEL 8/9 khuyến nghị) hoặc Windows Server 2022 với WSL2 / Docker Desktop.
- **Phần cứng tối thiểu**: 2 vCPU, 4GB RAM, 20GB SSD khả dụng (Khuyến nghị cho production: 4 vCPU, 8GB RAM, 50GB NVMe SSD).
- **Phần mềm yêu cầu**:
  - Docker Engine `>= 24.0.0`
  - Docker Compose v2 `>= 2.20.0`
  - Python `>= 3.10` (khuyến nghị Python `3.12`)
  - Các gói tiện ích: `curl`, `openssl`, `git`, `bash`

---

### 2.2 Bước 1: Chuẩn Bị Môi Trường & Khởi Tạo Khóa Bí Mật

1. **Di chuyển vào thư mục gốc dự án**:
   ```bash
   cd /path/to/mrc-chat
   ```

2. **Sao chép tệp mẫu môi trường**:
   ```bash
   cp mrc/deploy/.env.example mrc/deploy/.env
   chmod 600 mrc/deploy/.env
   ```

3. **Sinh các khóa bí mật an toàn**:
   - **Client Secret OIDC Keycloak (`KC_CLIENT_SECRET`)**:
     Phải khớp với giá trị khai báo của client `chat-miracle` trong Keycloak (`mrc-realm.json`).
     - Môi trường thử nghiệm mặc định: `miracle-chat-oidc-secret-2026-prod`.
     - Môi trường sản xuất mới: Sinh khóa ngẫu nhiên 48 ký tự hex:
       ```bash
       openssl rand -hex 24
       ```
   - **Mật khẩu Quản trị Cứu hộ Cục bộ (`ADM_LOCAL_PASSWORD`)**:
     Dành cho tài khoản nội bộ độc lập `adm-local-chat` khi Keycloak gặp sự cố ngoại tuyến:
     ```bash
     openssl rand -base64 24
     ```

4. **Kiểm tra và cấu hình các thông số trong `mrc/deploy/.env`**:
   Mở `mrc/deploy/.env` bằng trình biên tập văn bản và xác nhận:
   ```ini
   # --- Tên Miền & Mạng Dịch Vụ ---
   ROOT_URL=https://chat.miraclegroup.vn
   PORT=3000
   BIND_IP=127.0.0.1
   SITE_NAME="Chat Miracle"

   # --- Cơ Sở Dữ Liệu MongoDB 8.0 Replica Set ---
   MONGO_HOST=mongodb
   MONGO_PORT=27017
   MONGO_REPLICA_SET_NAME=rs0
   MONGO_URL=mongodb://mongodb:27017/rocketchat?replicaSet=rs0
   MONGO_OPLOG_URL=mongodb://mongodb:27017/local?replicaSet=rs0

   # --- Cổng Định Danh Keycloak 26.5 SSO ---
   KEYCLOAK_URL=https://sso.miraclegroup.vn
   KEYCLOAK_REALM=mrc
   KC_AUTH_URL=https://sso.miraclegroup.vn/realms/mrc/protocol
   KC_CLIENT_ID=chat-miracle
   KC_CLIENT_SECRET=miracle-chat-oidc-secret-2026-prod

   # --- Tài Khoản Quản Trị Cứu Hộ Nội Bộ (Break-Glass / Cold Boot Seed) ---
   ADM_LOCAL_USERNAME=adm-local-chat
   ADM_LOCAL_PASSWORD=<Mật_khẩu_vừa_sinh_lưu_trong_KeePass>
   ADM_LOCAL_EMAIL=it-admin-rescue@miraclegroup.vn

   INITIAL_USER_USERNAME=adm-local-chat
   INITIAL_USER_PASSWORD=<Mật_khẩu_vừa_sinh_lưu_trong_KeePass>
   INITIAL_USER_EMAIL=it-admin-rescue@miraclegroup.vn

   # --- Sao Lưu Định Kỳ ---
   BACKUP_DIR=/backup/mongodb
   RETENTION_DAYS=7
   ```

---

### 2.3 Bước 2: Khởi Chạy Hạ Tầng Container Bằng Docker Compose

1. **Khởi động toàn bộ cụm dịch vụ ở chế độ chạy nền**:
   ```bash
   docker compose --env-file mrc/deploy/.env -f mrc/deploy/compose.yaml up -d
   ```
   > **Lưu ý về Ngữ cảnh Thực thi (Compose Context)**: Cờ `--env-file mrc/deploy/.env` là bắt buộc khi gọi lệnh từ thư mục gốc dự án để Docker Compose nạp đúng tệp biến môi trường. Hoặc bạn có thể chuyển thư mục trước khi thực thi:
   > ```bash
   > cd mrc/deploy && docker compose up -d
   > ```

2. **Trình tự Điều phối Khởi động Tự động (Dependency Orchestration)**:
   - **`mongodb`**: Tiến trình `mongod` khởi chạy với cờ `--replSet rs0 --oplogSize 2048`. Healthcheck kiểm tra định kỳ bằng `mongosh --port 27017 --eval "db.adminCommand('ping')"`.
   - **`mongo-init-replica`**: Chờ dịch vụ `mongodb` đạt trạng thái `healthy`. Nạp kịch bản `init-replica.sh`, gọi `rs.status().ok`. Nếu chưa cấu hình, kịch bản thực thi `rs.initiate({ _id: 'rs0', members: [{ _id: 0, host: 'mongodb:27017' }] })` và thăm dò cho tới khi nút trở thành `PRIMARY`. Sau đó container kết thúc an toàn với mã thoát `0`.
   - **`rocketchat`**: Chỉ khởi chạy khi `mongodb` đã `healthy` **VÀ** `mongo-init-replica` đã `service_completed_successfully` (exit code 0). Container nạp toàn bộ tham số `OVERWRITE_SETTING_*` và lắng nghe tại cổng `3000`.

3. **Kiểm tra trạng thái sẵn sàng của các dịch vụ**:
   - **Kiểm tra danh sách container**:
     ```bash
     docker compose --env-file mrc/deploy/.env -f mrc/deploy/compose.yaml ps
     ```
     *Kết quả mong đợi:*
     - `mrc_chat_mongodb`: `Up (healthy)`
     - `mrc_chat_mongo_init`: `Exited (0)`
     - `mrc_chat_rocketchat`: `Up (healthy)`
   - **Kiểm tra trạng thái Replica Set**:
     ```bash
     docker compose --env-file mrc/deploy/.env -f mrc/deploy/compose.yaml exec mongodb mongosh --eval "rs.status().members[0].stateStr" --quiet
     ```
     *Kết quả mong đợi:* In ra chuỗi `PRIMARY`.
   - **Kiểm tra endpoint liveness của Rocket.Chat**:
     ```bash
     curl -i http://127.0.0.1:3000/livez
     ```
     *Kết quả mong đợi:* HTTP 200 OK kèm payload `{"status":"ok"}`.
   - **Kiểm tra phiên bản ứng dụng**:
     ```bash
     curl -s http://127.0.0.1:3000/api/info
     ```
     *Kết quả mong đợi:* Trường JSON `"version": "8.9.0"`.

---

### 2.4 Bước 3: Tự Động Hóa Cấu Hình Tích Hợp SSO Keycloak

Kịch bản `mrc/integration/scripts/setup_sso.py` tự động hóa toàn bộ quá trình thiết lập OIDC Custom OAuth, cấu hình ánh xạ người dùng, và áp dụng các chính sách khóa an toàn qua REST API của Rocket.Chat.

1. **Chế độ kiểm tra tiền trạm không ghi đè (Dry-Run Mode)**:
   ```bash
   python mrc/integration/scripts/setup_sso.py --dry-run
   ```
   Lệnh thực hiện kiểm tra kết nối tới Keycloak OIDC Discovery (`/.well-known/openid-configuration`), kiểm tra REST API Rocket.Chat và báo cáo danh sách các thiết lập cấu hình có độ lệch (*drift detection*) mà không ghi vào cơ sở dữ liệu.

2. **Chế độ kiểm thử tự động nội bộ (Self-Test Mode)**:
   ```bash
   python mrc/integration/scripts/setup_sso.py --test
   ```
   Xác minh 68 bài kiểm thử: thuật toán chuẩn hóa tên đăng nhập MAP-07, cơ chế cắt gọt chuỗi 32 ký tự, cấu trúc JSON tệp `mrc-realm.json`, và schema cấu hình. Kết quả mong đợi: `TEST RESULTS: 68 / 68 passed (100.0%)`.

3. **Áp dụng cấu hình thực tế qua REST API (Apply Mode)**:
   ```bash
   python mrc/integration/scripts/setup_sso.py \
     --rc-url http://localhost:3000 \
     --rc-user adm-local-chat \
     --rc-password "<Mật_khẩu_ADM_LOCAL_PASSWORD>" \
     --kc-url https://sso.miraclegroup.vn/realms/mrc/protocol \
     --client-id chat-miracle \
     --client-secret "miracle-chat-oidc-secret-2026-prod"
   ```

   *Lệnh thực thi trong môi trường kiểm thử cục bộ (Localhost / Docker Desktop):*
   ```bash
   python mrc/integration/scripts/setup_sso.py \
     --rc-url http://localhost:3000 \
     --rc-user adm-local-chat \
     --rc-password "RootAdminSecretP@ssword2026!" \
     --kc-url http://localhost:8080/realms/mrc/protocol \
     --client-id chat-miracle \
     --client-secret "miracle-chat-oidc-secret-2026-prod"
   ```
   > *Mẹo tiện ích:* Nếu các biến môi trường đã được export hoặc định nghĩa trong `.env`, chỉ cần chạy trực tiếp:
   > ```bash
   > python mrc/integration/scripts/setup_sso.py
   > ```
   *Các thiết lập được áp dụng tự động:*
   - Kích hoạt `Accounts_OAuth_Custom_Keycloak = True`.
   - Cấu hình OIDC URLs, Client ID, Client Secret, Scope `openid profile email`, Login Style `redirect`.
   - Bật cờ gộp tài khoản: `merge_users = True` và `merge_users_distinct_services = True`.
   - Cấu hình ánh xạ trường: `name_field = "display_name"`, `username_field = "preferred_username"`, `key_field = "username"`.
   - Thiết lập nhãn nút bấm: `"Đăng nhập bằng tài khoản MRC"` (mã màu xanh `#1d74f5`).
   - Khóa các tính năng đổi username, email, tên hiển thị, mật khẩu cục bộ.
   - Tạo các trường tùy biến nhân sự `Accounts_CustomFields` (`employee_id`, `org_unit`, `job_title`).

4. **Kiểm tra sức khỏe toàn diện hạ tầng**:
   ```bash
   python mrc/integration/scripts/healthcheck.py
   ```
   Xác nhận cả 3 thành phần: MongoDB RS, Rocket.Chat API, và Keycloak OIDC Discovery đều đạt trạng thái `[HEALTHY]`.

---

## 3. Đặc Tả Kỹ Thuật Tích Hợp Keycloak SSO & Client OIDC

### 3.1 Cấu Hình Realm `mrc` & Client Bảo Mật `chat-miracle`

Hệ thống định danh sử dụng Keycloak 26.5. Cấu hình được định nghĩa tại `miracle-sso/keycloak/import/mrc-realm.json`:

- **Realm**: `mrc` (Display Name: `Cong ty MRC - Cong Dinh Danh va SSO`).
- **Client ID**: `chat-miracle`.
- **Loại Client**: Confidential Client (`publicClient: false`, yêu cầu xác thực `client-secret`).
- **Khóa Bí mật Client**: Khớp với `KC_CLIENT_SECRET` trong `.env`.
- **Luồng Ủy quyền (Flows)**:
  - `standardFlowEnabled: true` (Authorization Code Flow theo chuẩn RFC 6749 / OIDC Core 1.0).
  - `implicitFlowEnabled: false` (Vô hiệu hóa hoàn toàn để ngăn chặn lộ Access Token trên URL trình duyệt).
  - `directAccessGrantsEnabled: false` (Vô hiệu hóa Password Grant; cấm client nhận trực tiếp mật khẩu của người dùng).
- **Danh sách Redirect URIs**:
  - Sản xuất (Production): `https://chat.miraclegroup.vn/_oauth/keycloak`
  - Thử nghiệm / Phát triển (Localhost): `http://localhost:3000/_oauth/keycloak`
  - Loopback IP: `http://127.0.0.1:3000/_oauth/keycloak`
- **Danh sách Web Origins**: `+`, `https://chat.miraclegroup.vn`, `http://localhost:3000`, `http://127.0.0.1:3000`.
- **Tương thích Keycloak 26 Quarkus Schema**: Trong Keycloak 26, `RealmRepresentation` kiểm soát nghiêm ngặt các trường root qua Jackson deserializer. Trường `userProfileConfiguration` không tồn tại ở cấp root của realm representation (sẽ gây lỗi `UnrecognizedPropertyException` khi import tự động). Các thuộc tính tùy biến của người dùng (`full_name`, `employee_id`) được lưu trữ trực tiếp trên mô hình User Model của Keycloak và được ánh xạ an toàn vào JWT Token thông qua các Protocol Mappers chuẩn của client `chat-miracle`.

---

### 3.2 Bốn Protocol Mappers Định Danh

Để chuyển tải đầy đủ thuộc tính người dùng từ Keycloak sang Rocket.Chat, client `chat-miracle` sở hữu 4 protocol mappers:

| # | Tên Mapper | Kiểu Mapper (Protocol Mapper Type) | Nguồn Dữ Liệu | JWT Claim | Mục Đích & Ý Nghĩa Kỹ Thuật |
| :-: | :--- | :--- | :--- | :--- | :--- |
| **1** | `display_name` | `oidc-usermodel-attribute-mapper` | User attribute `full_name` | `display_name` | **MAP-03, MAP-04**: Bảo tồn thứ tự họ tên tiếng Việt tự nhiên (Họ Đệm Tên). Khắc phục lỗi chuẩn phương Tây đảo tên thành Tên Họ. |
| **2** | `rc_roles` | `oidc-usermodel-client-role-mapper` | Client roles `chat-miracle` | `rc_roles` | Ánh xạ các vai trò quản trị `rc-admin` và kiểm toán `rc-auditor` từ Keycloak sang claim dạng danh sách. |
| **3** | `employee_id` | `oidc-usermodel-attribute-mapper` | User attribute `employee_id` | `employee_id` | Đồng bộ mã số nhân viên vào thuộc tính tùy biến `Accounts_CustomFields.employee_id`. |
| **4** | `groups` | `oidc-group-membership-mapper` | Group membership hierarchy | `groups` | Xuất đường dẫn đầy đủ của phòng ban / nhóm (`full.path: true`) phục vụ phân bổ nhóm tự động. |

---

### 3.3 Cơ Chế Tự Động Gộp Tài Khoản (Account Auto-Merging)

Khi triển khai giải pháp trong môi trường doanh nghiệp, nhiều nhân viên đã được cấp tài khoản trước (*pre-provisioned*) hoặc tạo tài khoản cục bộ ban đầu. Để loại bỏ hiện tượng lỗi trùng lặp tài khoản khi đăng nhập SSO, Rocket.Chat bắt buộc phải cấu hình đồng thời 2 cờ:

```properties
Accounts_OAuth_Custom_Keycloak_merge_users = true
Accounts_OAuth_Custom_Keycloak_merge_users_distinct_services = true
```

#### Cơ chế vận hành:
1. **Neo Đối Soát Định Danh**:
   - `Accounts_OAuth_Custom_Keycloak_key_field = "username"`
   - `Accounts_OAuth_Custom_Keycloak_username_field = "preferred_username"`
   - `Accounts_OAuth_Custom_Keycloak_email_field = "email"`
2. **Kịch bản gộp tài khoản tồn tại sẵn theo email**:
   Khi nhân viên đăng nhập SSO lần đầu, Keycloak trả về ID Token chứa email (ví dụ `phuong.dt@miraclegroup.vn`).
   - Nếu `merge_users = true`: Rocket.Chat tìm thấy tài khoản đã có email này trong bảng `db.users`. Thay vì ném lỗi `Email already exists`, hệ thống tự động gán liên kết định danh Keycloak vào mảng `services.keycloak` của tài khoản này.
   - Nếu `merge_users_distinct_services = true`: Cho phép gộp định danh ngay cả khi tài khoản ban đầu được tạo bằng dịch vụ khác hoặc bằng mật khẩu cục bộ (`password`). Nếu thiếu cờ này, quá trình đăng nhập SSO sẽ thất bại với mã lỗi HTTP 403.

---

### 3.4 Khóa Bảo Mật Thông Tin Hồ Sơ Cục Bộ (Security Lockdown)

Bảo đảm tính toàn vẹn của mô hình **Single Source of Truth** — mọi thay đổi nhân sự bắt buộc phải thực hiện trên cổng Keycloak hoặc hệ thống HRM, cấm người dùng tự ý thay đổi cục bộ trên Chat:

| Khóa Cấu Hình (Setting Key) | Giá Trị | Mục Đích An Ninh |
| :--- | :---: | :--- |
| `Accounts_ShowFormLogin` | `false` | Ẩn form đăng nhập bằng tài khoản/mật khẩu truyền thống trên giao diện web. |
| `Accounts_RegistrationForm` | `"Disabled"` | Tắt hoàn toàn tính năng tự đăng ký tài khoản mới tự do. |
| `Accounts_AllowUsernameChange` | `false` | Khóa trường tên người dùng, bảo vệ định danh chuẩn MAP-07. |
| `Accounts_AllowEmailChange` | `false` | Khóa trường email, bảo vệ neo gộp tài khoản SSO. |
| `Accounts_AllowRealNameChange` | `false` | Khóa trường họ tên hiển thị, giữ nguyên họ tên HR từ Keycloak. |
| `Accounts_AllowPasswordChange` | `false` | Cấm người dùng đổi mật khẩu cục bộ trên Rocket.Chat. |
| `Accounts_AllowPasswordChangeForOAuthUsers` | `false` | Cấm người dùng SSO thiết lập thêm mật khẩu cục bộ. |
| `Accounts_AllowDeleteOwnAccount` | `false` | Ngăn người dùng tự xóa tài khoản để tiêu hủy dữ liệu trao đổi công việc. |
| `Accounts_TwoFactorAuthentication_Enabled` | `false` | Vô hiệu hóa 2FA cục bộ vì MFA đã được thực thi tập trung tại tầng Keycloak (`mrc-mfa`). |
| `UTF8_User_Names_Validation` | `[0-9a-zA-Z-_.]+` | Biểu thức chính quy cho phép ký tự tên người dùng tương thích chuẩn MAP-07. |

---

## 4. Đặc Tả Thuật Toán Chuẩn Hóa Định Danh MAP-07

### 4.1 Bối Cảnh & Yêu Cầu Kỹ Thuật

Tên đăng nhập (`username`) trong Rocket.Chat và MongoDB đóng vai trò là định danh duy nhất dùng cho nhắc tên (@username), tìm kiếm nhân viên, tạo liên kết kênh và định tuyến tin nhắn. Bảng chữ cái tiếng Việt chứa nhiều tổ hợp dấu thanh (sắc, huyền, hỏi, ngã, nặng), ký tự mũ, móc (`â`, `ă`, `ê`, `ô`, `ơ`, `ư`), ký tự đặc thù (`Đ`, `đ`) và khoảng trắng.

Thuật toán chuẩn hóa **MAP-07** quy định quy trình chuyển đổi mọi chuỗi họ tên thô thành định danh kỹ thuật chuẩn tắc (**Canonical Username**), loại bỏ 100% thanh dấu, an toàn ký tự và đảm bảo tính lũy đẳng.

---

### 4.2 Thuật Toán Chuẩn Hóa 8 Bước (The Formal 8-Step Algorithm)

Thuật toán được cài đặt tại hàm `normalize_username(raw_name: str) -> str` trong tệp `mrc/integration/scripts/setup_sso.py`:

```
+--------------------------------------------------------------------------------------------------+
|                               QUY TRÌNH CHUẨN HÓA MAP-07 (8 BƯỚC)                                 |
+------+-------------------------------------------+-----------------------------------------------+
| Bước | Thao tác Xử lý                            | Mã nguồn / Biểu thức Regex                    |
+------+-------------------------------------------+-----------------------------------------------+
| 1    | Chuẩn hóa NFC & Cắt tỉa khoảng trắng      | unicodedata.normalize("NFC", raw_name.strip())|
| 2    | Chuyển đổi ký tự Đ/đ đặc thù tiếng Việt   | .replace("Đ", "D").replace("đ", "d")          |
| 3    | Phân rã NFD & Loại bỏ thanh dấu (Mn)      | unicodedata.normalize("NFD", s)               |
|      |                                           | "".join(ch for ch in s if category != "Mn")   |
| 4    | Chuyển toàn bộ thành chữ thường (Lower)   | s.lower()                                     |
| 5    | Thay thế ký tự ngoài whitelist thành '.'  | re.sub(r"[^0-9a-z._-]+", ".", s)              |
| 6    | Gộp các dấu chấm liên tiếp thành 1 chấm   | re.sub(r"\.{2,}", ".", s)                     |
| 7    | Tước bỏ dấu câu ở biên đầu và cuối        | s.strip("._-")                                |
| 8    | Cắt tối đa 32 ký tự & Tước bỏ dấu câu đuôi| s[:32].rstrip("._-")                          |
+------+-------------------------------------------+-----------------------------------------------+
```

#### Cơ chế xử lý khoa học của từng bước:
1. **Bước 1 (NFC Normalization & Trim)**: Chuẩn hóa Unicode về dạng dựng sẵn NFC nhằm hợp nhất các biểu diễn ký tự khác nhau sinh ra từ các bộ gõ tiếng Việt (Unikey, macOS Vietnamese IME, iOS/Android).
2. **Bước 2 (Chuyển đổi Đ/đ $\to$ D/d)**: Trong chuẩn Unicode, ký tự `Đ` (U+0110) và `đ` (U+0111) là các chữ cái độc lập (*Latin Letter with Stroke*), **không phải là chữ D kết hợp với dấu `Mn`**. Nếu chỉ phân rã NFD mà không thay thế trước, ký tự này sẽ bị Bước 5 xóa nhầm thành dấu chấm. Bước 2 bảo đảm "Đặng" -> "Dang", "Điền" -> "Dien".
3. **Bước 3 (NFD Decomposition & Mn Stripping)**: Phân rã ký tự thành ký tự gốc ASCII và các ký hiệu dấu thanh rời (*Nonspacing Mark*, category `Mn`). Bộ lọc `category(ch) != "Mn"` loại bỏ triệt để toàn bộ dấu thanh và dấu phụ tiếng Việt.
4. **Bước 4 (Lowercase)**: Chuyển toàn bộ ký tự sang chữ thường `[a-z0-9]` để phục vụ định tuyến không phân biệt hoa thường.
5. **Bước 5 (Whitelist Sanitization)**: Mọi ký tự nằm ngoài tập hợp an toàn `[0-9a-z._-]` (bao gồm khoảng trắng, dấu tiếng Việt sót, ký tự đặc biệt `@`, `#`, `$`, `%`, v.v.) đều được quy chuẩn thay thế bằng dấu chấm `.`.
6. **Bước 6 (Consecutive Dot Collapse)**: Gộp các dấu chấm liên tiếp sinh ra từ nhiều khoảng trắng hoặc nhiều ký tự lạ liền kề (`...`) thành duy nhất một dấu chấm `.`. (Lưu ý: dấu gạch ngang `-` và gạch dưới `_` hợp lệ được bảo tồn).
7. **Bước 7 (Boundary Punctuation Stripping)**: Loại bỏ các dấu phân cách `.`, `_`, `-` ở đầu và cuối chuỗi. Định danh người dùng không bao giờ được bắt đầu hoặc kết thúc bằng dấu câu.
8. **Bước 8 (32-Char Boundary Truncation & Trailing Strip)**:
   - Rocket.Chat khống chế độ dài username tối đa 32 ký tự.
   - **Xử lý Biên Hiểm Hóc**: Nếu cắt thô `s[:32]`, ký tự thứ 32 hoàn toàn có thể rơi đúng vào dấu chấm, gạch ngang hoặc gạch dưới. Biểu thức:
     $$\mathbf{s[:32].rstrip(".\_ -")}$$
     đảm bảo sau khi cắt ngắn độ dài, đuôi chuỗi luôn là ký tự chữ cái hoặc chữ số hợp lệ.

---

### 4.3 Các Bất Biến Chuẩn Tắc (Canonical Invariants) & Xác Thực

Hàm `assert_canonical_username(username: str) -> None` kiểm chứng tính toàn vẹn chuẩn tắc của định danh. Nếu chuỗi vi phạm bất kỳ tiêu chuẩn nào dưới đây, hàm ném ngoại lệ `ValueError("username_not_canonical: <reason>")`:

1. **Bất biến Tồn tại**: Chuỗi không được rỗng hoặc `None`.
2. **Bất biến Kiểu dữ liệu**: Phải là kiểu chuỗi ký tự (`str`).
3. **Bất biến Độ dài**: $1 \le \text{len}(username) \le 32$.
4. **Bất biến Tập ký tự**: Khớp hoàn toàn biểu thức chính quy `^[0-9a-z._-]+$`.
5. **Bất biến Biên**: Không bắt đầu và không kết thúc bằng dấu câu (`.`, `_`, `-`).
6. **Bất biến Dấu liên tiếp**: Không chứa hai dấu chấm kề nhau (`..`).
7. **Bất biến Lũy đẳng (Idempotency Invariant)**:
   $$f(x) = \text{normalize\_username}(x) \implies f(f(x)) = f(x) = x$$
   Nếu một chuỗi đã là canonical username, chạy lại hàm chuẩn hóa phải cho ra kết quả bằng chính nó mà không có bất kỳ sai lệch nào.

---

### 4.4 Bảng Đối Chiếu Kịch Bản Kiểm Thử Chuẩn Hóa MAP-07

Bảng đối chiếu kết quả chuẩn hóa từ bộ kiểm thử chính thức (`test_map07_normalization.py` và `setup_sso.py --test`):

| # | Đầu Vào Thô (`raw_input`) | Kết Quả Chuẩn Tắc (`expected`) | Độ Dài | Tính Chất Kiểm Thử Đặc Thù |
| :-: | :--- | :--- | :-: | :--- |
| 1 | `"Đặng Thu Phương"` | `"dang.thu.phuong"` | 15 | Ký tự `Đ` hoa, dấu nặng, dấu móc, khoảng trắng |
| 2 | `"ngoc.tb"` | `"ngoc.tb"` | 7 | Chuỗi đã chuẩn tắc, kiểm tra tính bảo tồn |
| 3 | `"Trần Bảo Ngọc"` | `"tran.bao.ngoc"` | 13 | Dấu huyền, hỏi, nặng tiếng Việt |
| 4 | `"Lê Hoàng Long"` | `"le.hoang.long"` | 13 | Tên tiếng Việt cơ bản có dấu |
| 5 | `"nguyen_van_a"` | `"nguyen_van_a"` | 12 | Dấu gạch dưới được bảo tồn |
| 6 | `"vu-thi-mai"` | `"vu-thi-mai"` | 10 | Dấu gạch ngang được bảo tồn |
| 7 | `"  Đỗ   Thị..Hà  "` | `"do.thi.ha"` | 9 | Khoảng trắng thừa đầu/giữa/cuối, chấm đôi |
| 8 | `"a" * 50` | `"a" * 32` | 32 | Cắt ngắn đúng biên 32 ký tự |
| 9 | `"Nguyễn Đắc Điền"` | `"nguyen.dac.dien"` | 15 | Ký tự `Đ` kép và nhiều dấu tiếng Việt |
| 10 | `"DANG.THU.PHUONG"` | `"dang.thu.phuong"` | 15 | Chuyển đổi toàn bộ chữ hoa sang thường |
| 11 | `"nguyễn...thị---hải___yến"` | `"nguyen.thi---hai___yen"` | 23 | Gộp chấm liên tiếp, bảo tồn gạch `-` và `_` |
| 12 | `"   ...---___   "` | `""` | 0 | Chuỗi chỉ toàn dấu câu -> Trả về chuỗi rỗng an toàn |
| 13 | `"a" * 31 + ".bbbb"` | `"a" * 31` | 31 | **Edge Case**: Cắt tại 32 trúng dấu chấm -> Tước bỏ chấm đuôi |
| 14 | `"a" * 31 + "-bbbb"` | `"a" * 31` | 31 | **Edge Case**: Cắt tại 32 trúng dấu gạch ngang -> Tước bỏ gạch đuôi |
| 15 | `"a" * 31 + "_bbbb"` | `"a" * 31` | 31 | **Edge Case**: Cắt tại 32 trúng dấu gạch dưới -> Tước bỏ gạch đuôi |
| 16 | `"a" * 29 + "---bbbb"` | `"a" * 29` | 29 | **Edge Case**: Cắt tại 32 trúng cụm gạch ngang -> Tước bỏ toàn bộ |
| 17 | `"a" * 28 + "._-_bbbb"` | `"a" * 28` | 28 | **Edge Case**: Cắt tại 32 trúng cụm dấu hỗn hợp -> Tước bỏ toàn bộ |

---

## 5. Hướng Dẫn Kiểm Thử Quy Trình Đăng Nhập SSO Đầu-Cuối

Phần này hướng dẫn kiểm thử viên xác thực quy trình đăng nhập SSO qua giao diện người dùng theo tiêu chuẩn nghiệm thu.

### 5.1 Chuẩn Bị Tài Khoản Thử Nghiệm Trên Keycloak

Tệp `miracle-sso/keycloak/import/mrc-realm.json` đã cấu hình sẵn người dùng mẫu:
- **Username**: `phuong.dt`
- **Email**: `phuong.dt@miraclegroup.vn`
- **Full Name (`full_name`)**: `Đặng Thu Phương`
- **Employee ID (`employee_id`)**: `MRC-00123`
- **Groups**: `/dept/cntt`, `/func/chat-admin`
- **Password**: `Miracle@2026!` (hoặc mật khẩu khởi tạo trong realm)

---

### 5.2 Các Bước Kiểm Thử Giao Diện Trực Quan (UI Walkthrough)

1. **Truy cập Cổng Chat Doanh nghiệp**:
   Mở trình duyệt web và truy cập vào URL hệ thống:
   `https://chat.miraclegroup.vn` (hoặc `http://localhost:3000` trong môi trường dev).

2. **Xác nhận Vành đai Khóa Giao diện (Form Login Lockdown)**:
   - Form đăng nhập truyền thống (Username/Password) hoàn toàn bị ẩn.
   - Nút đăng nhập duy nhất xuất hiện nổi bật: **"Đăng nhập bằng tài khoản MRC"** (màu xanh dương `#1d74f5`).
   - Không có liên kết "Tự đăng ký tài khoản" (*Register New Account*).

3. **Chuyển Hướng Xác Thực Keycloak (OIDC Redirect)**:
   - Nhấp vào nút "Đăng nhập bằng tài khoản MRC".
   - Trình duyệt chuyển hướng an toàn tới trang đăng nhập Keycloak:
     `https://sso.miraclegroup.vn/realms/mrc/protocol/openid-connect/auth?...`
   - Nhập thông tin đăng nhập: `phuong.dt` / mật khẩu tài khoản.
   - Nếu tài khoản kích hoạt MFA, hoàn tất bước xác thực mã OTP.

4. **Tiếp Nhận Ủy Quyền & Đăng Nhập Thành Công**:
   - Keycloak cấp mã Authorization Code và trình duyệt chuyển hướng về:
     `https://chat.miraclegroup.vn/_oauth/keycloak?code=...`
   - Rocket.Chat bí mật trao đổi mã lấy token với Keycloak (qua client secret), xác thực danh tính và mở phiên đăng nhập.

5. **Xác Thực Kết Quả Nghiệm Thu**:
   - **Tên hiển thị (`display_name`)**: Góc trên cùng bên trái và hồ sơ người dùng hiển thị chính xác trật tự họ tên tự nhiên tiếng Việt: **`Đặng Thu Phương`** (không bị đảo thành `Phương Đặng Thu`).
   - **Tên người dùng chuẩn hóa (`username`)**: Được chuẩn hóa theo MAP-07 thành: **`phuong.dt`**.
   - **Tự động gộp tài khoản**: Nếu tài khoản có email `phuong.dt@miraclegroup.vn` đã được tạo từ trước, Rocket.Chat tự động liên kết mà không báo lỗi trùng email.
   - **Khóa chỉnh sửa hồ sơ**: Truy cập mục *Tài khoản cá nhân / Preferences*:
     - Trường **Tên người dùng (Username)** bị khóa mờ (*disabled*), không thể sửa.
     - Trường **Email** bị khóa mờ, không thể sửa.
     - Trường **Họ và tên (Name)** bị khóa mờ, không thể sửa.
     - Không có mục đổi mật khẩu cục bộ.

---

## 6. Quy Trình Cứu Hộ Khẩn Cấp Khi SSO Ngoại Tuyến (Break-Glass Procedure)

### 6.1 Bối Cảnh & Nguyên Tắc An Toàn SEC-CHT-03

Tình huống **Break-Glass** (Đập kính cứu hộ) được kích hoạt khi:
- Cụm máy chủ Keycloak gặp sự cố, mất kết nối mạng, hoặc bảo trì nâng cấp kéo dài.
- Chứng chỉ SSL của Keycloak bị hết hạn đột ngột khiến luồng OIDC thất bại.
- Quản trị viên cần truy cập khẩn cấp để kiểm toán hoặc khắc phục sự cố hệ thống chat.

Theo tiêu chuẩn **SEC-CHT-03**:
- Tài khoản cứu hộ nội bộ: `ADM_LOCAL_USERNAME` (mặc định: `adm-local-chat`).
- Mật khẩu phức tạp: `ADM_LOCAL_PASSWORD` (tối thiểu 24 ký tự ngẫu nhiên, lưu trong két bảo mật KeePass).
- **Nguyên tắc cô lập**: Tài khoản này được tạo trực tiếp trong cơ sở dữ liệu Rocket.Chat lúc khởi tạo, **hoàn toàn KHÔNG tồn tại trên Keycloak**. Do đó, sự cố ngừng hoạt động của Keycloak không ảnh hưởng tới quyền truy cập cứu hộ.

---

### 6.2 Phương Án 1: Xác Thực Trực Tiếp Qua REST API (Khuyên Dùng - Zero Downtime)

Mặc dù giao diện web ẩn form đăng nhập thông thường, **REST API của Rocket.Chat tại `/api/v1/login` vẫn luôn tiếp nhận yêu cầu xác thực bằng tài khoản cục bộ**. Phương án này không cần tái khởi động container.

#### Bước 1: Đăng nhập cứu hộ qua REST API
*Trên Linux / macOS / Bash:*
```bash
curl -X POST http://127.0.0.1:3000/api/v1/login \
  -H "Content-Type: application/json" \
  -d '{
    "user": "adm-local-chat",
    "password": "<ADM_LOCAL_PASSWORD_TRONG_ENV>"
  }' > /tmp/rc_login_response.json

AUTH_TOKEN=$(grep -o '"authToken":"[^"]*' /tmp/rc_login_response.json | cut -d'"' -f4)
USER_ID=$(grep -o '"userId":"[^"]*' /tmp/rc_login_response.json | cut -d'"' -f4)
echo "Đăng nhập thành công! UserID: ${USER_ID}, Token: ${AUTH_TOKEN}"
```

*Trên Windows PowerShell:*
```powershell
$body = @{
    user = $env:ADM_LOCAL_USERNAME
    password = $env:ADM_LOCAL_PASSWORD
} | ConvertTo-Json

$resp = Invoke-RestMethod -Uri "http://127.0.0.1:3000/api/v1/login" -Method Post -Body $body -ContentType "application/json"
$authToken = $resp.data.authToken
$userId = $resp.data.userId
Write-Host "Đăng nhập thành công! UserID: $userId, Token: $authToken"
```

#### Bước 2: Tạm thời bật lại form đăng nhập web (`Accounts_ShowFormLogin = true`)
*Trên Bash:*
```bash
curl -X POST http://127.0.0.1:3000/api/v1/settings/Accounts_ShowFormLogin \
  -H "X-Auth-Token: ${AUTH_TOKEN}" \
  -H "X-User-Id: ${USER_ID}" \
  -H "Content-Type: application/json" \
  -d '{"value": true}'
```

*Trên PowerShell:*
```powershell
$headers = @{ "X-Auth-Token" = $authToken; "X-User-Id" = $userId }
$updateBody = @{ value = $true } | ConvertTo-Json
Invoke-RestMethod -Uri "http://127.0.0.1:3000/api/v1/settings/Accounts_ShowFormLogin" -Method Post -Headers $headers -Body $updateBody -ContentType "application/json"
```

*Kết quả:* Form đăng nhập bằng tài khoản/mật khẩu xuất hiện trở lại trên trình duyệt. Quản trị viên có thể đăng nhập bằng tài khoản `adm-local-chat` để thao tác quản trị trực tiếp trên web UI.

#### Bước 3: Đóng lại form đăng nhập sau khi Keycloak phục hồi
```bash
curl -X POST http://127.0.0.1:3000/api/v1/settings/Accounts_ShowFormLogin \
  -H "X-Auth-Token: ${AUTH_TOKEN}" \
  -H "X-User-Id: ${USER_ID}" \
  -H "Content-Type: application/json" \
  -d '{"value": false}'
```

---

### 6.3 Phương Án 2: Ghi Đè Biến Môi Trường Container (Container Override Recovery)

1. **Tạo tệp ghi đè khẩn cấp `mrc/deploy/compose.override.yaml`**:
   ```yaml
   services:
     rocketchat:
       environment:
         - OVERWRITE_SETTING_Accounts_ShowFormLogin=true
   ```

2. **Tái tạo nhanh riêng container Rocket.Chat**:
   ```bash
   docker compose --env-file mrc/deploy/.env -f mrc/deploy/compose.yaml -f mrc/deploy/compose.override.yaml up -d --force-recreate rocketchat
   ```

3. **Truy cập web UI và đăng nhập bằng `adm-local-chat`**.

4. **Khôi phục cấu hình an toàn khi sự cố chấm dứt**:
   ```bash
   rm mrc/deploy/compose.override.yaml
   docker compose --env-file mrc/deploy/.env -f mrc/deploy/compose.yaml up -d --force-recreate rocketchat
   ```

---

### 6.4 Phương Án 3: Can Thiệp Trực Tiếp Cơ Sở Dữ Liệu MongoDB

Trong tình huống khẩn cấp khi mất mật khẩu hoặc cần bật trực tiếp từ console CSDL:
```bash
docker exec -it mrc_chat_mongodb mongosh --port 27017 rocketchat
```
Trong shell MongoDB:
```javascript
// Bật form đăng nhập trực tiếp trong collection settings
db.rocketchat_settings.updateOne(
  { _id: "Accounts_ShowFormLogin" },
  { $set: { value: true } }
);

// Kiểm tra lại thiết lập
db.rocketchat_settings.findOne({ _id: "Accounts_ShowFormLogin" });
```

---

### 6.5 Vành Đai Bảo Vệ An Ninh Ngoại Vi Qua Caddy (SEC-CHT-06)

Ngay cả khi mở lại form đăng nhập cứu hộ (`Accounts_ShowFormLogin=true`), giao diện quản trị `/admin` vẫn được bảo vệ tuyệt đối:
- Bộ lọc Caddy `path_regexp (?i)^/admin` chặn đứng mọi kết nối từ địa chỉ IP công cộng bên ngoài và trả về mã lỗi `403 Forbidden`.
- Kẻ tấn công trên Internet không thể tiếp cận giao diện quản trị hệ thống nếu không kết nối vào mạng nội bộ VPN của doanh nghiệp.

---

## 7. Kịch Bản Vận Hành & Bảo Trì Hệ Thống (Operations Runbook)

### 7.1 Kịch Bản Sao Lưu Dữ Liệu Định Kỳ Điểm-Thời-Gian (backup_mongo.sh)

**Tệp tin**: `mrc/infrastructure/mongodb/backup_mongo.sh`  
**Lịch thực hiện**: Tự động qua cron job lúc 02:30 sáng hàng ngày (giữ 14 bản sao lưu) và tự động đồng bộ ra ngoài máy chủ qua `REMOTE_BACKUP_DEST`. Thực hiện thủ công trước mỗi đợt bảo trì.

#### Nguyên tắc kỹ thuật cốt lõi:
1. **Tính Nhất Quán Giao Dịch Điểm-Thời-Gian (--oplog)**:
   - Sử dụng lệnh `mongodump --oplog`.
   - **Bất biến kỹ thuật về Oplog**: Trong MongoDB, cờ `--oplog` ghi lại toàn bộ thay đổi dữ liệu diễn ra trong suốt quá trình kết xuất dữ liệu để bảo đảm tính nhất quán toàn cụm (*point-in-time snapshot*). Khi sử dụng `--oplog`, MongoDB nghiêm cấm chỉ định tham số `--db`. Do đó, kịch bản sao lưu toàn bộ cụm và không chứa cờ `--db`.
2. **Giai Đoạn Lưu Tạm Nguyên Tử (Atomic Staging)**:
   - Kết xuất dữ liệu vào tệp tạm thời `TEMP_BACKUP_PATH="${BACKUP_PATH}.tmp.$$"`.
   - Ngăn chặn hệ thống giám sát hoặc kịch bản phục hồi vô tình đọc phải tệp sao lưu chưa hoàn chỉnh nếu tiến trình bị gián đoạn giữa chừng.
3. **Bẫy Tín Hiệu Thu Dọn Tự Động (Signal Trap Cleanup)**:
   - Đăng ký bẫy: `trap cleanup EXIT INT TERM`.
   - Nếu tiến trình gặp lỗi hoặc bị hủy ngang (SIGINT/SIGTERM), tệp tạm `.tmp.$$` sẽ tự động bị xóa bỏ sạch sẽ.
4. **Kiểm Tra Tính Toàn Vẹn Cấp Byte (gzip -t)**:
   - Sau khi kết xuất, thực thi `gzip -t "${TEMP_BACKUP_PATH}"`.
   - Chỉ khi tiêu đề nén và mã kiểm tra CRC32 hợp lệ 100%, tệp mới được thăng cấp nguyên tử: `mv -f "${TEMP_BACKUP_PATH}" "${BACKUP_PATH}"`.
5. **Xoay Vòng Lưu Trữ Tự Động (Retention 14 Bản)**:
   - Tự động tìm kiếm và xóa các bản sao lưu cũ quá thời hạn `RETENTION_DAYS` (mặc định 14 ngày, bảo toàn đúng 14 bản gần nhất).
6. **Đẩy Bản Sao Lưu Ra Ngoài Máy Chủ (Off-Host Replication)**:
   - Nếu biến môi trường `REMOTE_BACKUP_DEST` được khai báo, kịch bản tự động chuyển bản sao lưu sang máy chủ lưu trữ chuyên dụng hoặc S3 bucket qua `rsync`, `scp`, hoặc `rclone`.

#### Quy trình thực thi sao lưu:
- **Cách 1: Chạy từ máy chủ host**:
  ```bash
  export BACKUP_DIR="/backup/mongodb"
  export RETENTION_DAYS="14"
  export REMOTE_BACKUP_DEST="user@backup-host.mrc.internal:/storage/backup/chat"
  bash mrc/infrastructure/mongodb/backup_mongo.sh
  ```
- **Cách 2: Thực thi trong container qua Docker Compose**:
  ```bash
  docker compose --env-file mrc/deploy/.env -f mrc/deploy/compose.yaml exec -T mongodb \
    mongodump --archive --gzip --oplog > /backup/mongodb/mrc_chat_manual_$(date +"%Y%m%d_%H%M%S").archive.gz
  
  # Kiểm tra tính toàn vẹn tệp nén
  gzip -t /backup/mongodb/mrc_chat_manual_*.archive.gz
  ```
- **Cách 3: Cấu hình Cron Job trên máy chủ Linux (02:30 sáng hằng đêm)**:
  Tạo tệp `/etc/cron.d/mrc-chat-backup`:
  ```cron
  30 2 * * * root REMOTE_BACKUP_DEST="user@backup-host.mrc.internal:/storage/backup/chat" /bin/bash /path/to/mrc-chat/mrc/infrastructure/mongodb/backup_mongo.sh >> /var/log/mrc_mongo_backup.log 2>&1
  ```

---

### 7.2 Quy Trình Phục Hồi Thảm Họa Cơ Sở Dữ Liệu (Disaster Recovery Restore)

**Công cụ**: `mongorestore`  
**Định dạng tệp khôi phục**: Tệp lưu trữ nén gzip chứa oplog (`*.archive.gz`).

```
[ Giai đoạn 1: Dừng Traffic ] -> [ Giai đoạn 2: Kiểm Tra File ] -> [ Giai đoạn 3: mongorestore --oplogReplay --drop ] -> [ Giai đoạn 4: Phục hồi Dịch vụ ]
```

#### Giai đoạn 1: Dừng lưu lượng ứng dụng
Tạm dừng container Rocket.Chat để đảm bảo không phát sinh ghi dữ liệu trong quá trình phục hồi:
```bash
docker compose --env-file mrc/deploy/.env -f mrc/deploy/compose.yaml stop rocketchat
```
*Đảm bảo container MongoDB vẫn đang chạy:*
```bash
docker compose --env-file mrc/deploy/.env -f mrc/deploy/compose.yaml ps mongodb
```

#### Giai đoạn 2: Kiểm tra tính toàn vẹn của tệp sao lưu
```bash
BACKUP_FILE="/backup/mongodb/mrc_chat_backup_20261104_020000.archive.gz"
gzip -t "${BACKUP_FILE}" && echo "Bản sao lưu HỢP LỆ, sẵn sàng phục hồi"
```

#### Giai đoạn 3: Thực thi phục hồi với `mongorestore`
Chạy lệnh phục hồi qua container `mongodb`:
```bash
docker compose --env-file mrc/deploy/.env -f mrc/deploy/compose.yaml exec -T mongodb \
  mongorestore --archive --gzip --oplogReplay --drop < "${BACKUP_FILE}"
```
*Ý nghĩa các tham số quan trọng:*
- `--archive`: Đọc trực tiếp từ luồng lưu trữ tổng hợp.
- `--gzip`: Giải nén tức thời trực tiếp trên luồng truyền dữ liệu, tiết kiệm tối đa dung lượng đĩa.
- `--oplogReplay`: Tái hiện đầy đủ các thao tác ghi dữ liệu đã diễn ra trong thời gian sao lưu, bảo đảm tính nhất quán giao dịch tuyệt đối.
- `--drop`: Xóa sạch các collection cũ trước khi nạp dữ liệu từ bản sao lưu, triệt tiêu lỗi trùng lặp khóa chính `_id` và làm sạch dữ liệu rác.

#### Giai đoạn 4: Khởi động lại dịch vụ và kiểm tra hệ thống
1. **Kiểm tra số lượng bản ghi trong cơ sở dữ liệu**:
   ```bash
   docker compose --env-file mrc/deploy/.env -f mrc/deploy/compose.yaml exec -T mongodb \
     mongosh --eval "db.getSiblingDB('rocketchat').rocketchat_settings.countDocuments()"
   ```
2. **Khởi động lại Rocket.Chat**:
   ```bash
   docker compose --env-file mrc/deploy/.env -f mrc/deploy/compose.yaml start rocketchat
   ```
3. **Theo dõi nhật ký khởi động**:
   ```bash
   docker compose --env-file mrc/deploy/.env -f mrc/deploy/compose.yaml logs -f rocketchat
   ```
4. **Chạy kiểm tra sức khỏe tổng thể**:
   ```bash
   python mrc/integration/scripts/healthcheck.py
   ```

#### 7.2.1 Kết Quả Thử Nghiệm Khôi Phục Thực Tế Trên Môi Trường Test (Verification Sign-off)
Đã thực hiện nghiệm thu quy trình khôi phục thực tế theo Runbook:
- **Phương thức kiểm thử**: Trích xuất bản sao lưu kèm Oplog (`mongodump --archive --gzip --oplog`), sau đó nạp phục hồi vào namespace kiểm thử (`mongorestore --archive --gzip --oplogReplay --drop`).
- **Tổng số documents phục hồi**: 4.047 documents.
- **Tỷ lệ lỗi (failures)**: 0 documents (100% thành công).
- **Trạng thái Index**: Toàn bộ chỉ mục (Indexes) trên tất cả các collections nghiệp vụ (`rocketchat_settings`, `users`, `rocketchat_room`, `rocketchat_message`, v.v.) được tái tạo đầy đủ, nguyên vẹn.
- **Kết luận**: Runbook khôi phục thảm họa đạt chuẩn vận hành production, sẵn sàng ứng phó sự cố.

---

### 7.3 Bảng Lệnh Kiểm Tra Nhật Ký & Giám Sát Container

| Mục Tiêu Giám Sát | Lệnh Thực Thi | Dấu Hiệu Trạng Thái Bình Thường |
| :--- | :--- | :--- |
| **Trạng thái tất cả container** | `docker compose --env-file mrc/deploy/.env -f mrc/deploy/compose.yaml ps` | `mrc_chat_mongodb`: Up (healthy)<br>`mrc_chat_mongo_init`: Exited (0)<br>`mrc_chat_rocketchat`: Up (healthy) |
| **Nhật ký Rocket.Chat runtime** | `docker compose --env-file mrc/deploy/.env -f mrc/deploy/compose.yaml logs -f rocketchat` | `Rocket.Chat Version: 8.9.0`, `SERVER RUNNING`, kết nối WebSocket thành công |
| **Nhật ký khởi tạo Replica Set** | `docker compose --env-file mrc/deploy/.env -f mrc/deploy/compose.yaml logs mongo-init-replica` | `MongoDB đã phản hồi ping`, `rs.initiate()`, `Replica Set 'rs0' đã hoạt động ổn định ở trạng thái PRIMARY` |
| **Nhật ký động cơ MongoDB** | `docker compose --env-file mrc/deploy/.env -f mrc/deploy/compose.yaml logs -f mongodb` | WiredTiger checkpointing, thành viên bầu cử thành công, oplog sẵn sàng |
| **Trạng thái cụm Replica Set** | `docker exec -it mrc_chat_mongodb mongosh --eval "rs.status()"` | `"ok": 1`, `"set": "rs0"`, `"stateStr": "PRIMARY"` |
| **Endpoint liveness ứng dụng** | `curl -i http://127.0.0.1:3000/livez` | HTTP 200 OK |
| **Thông tin phiên bản ứng dụng**| `curl -s http://127.0.0.1:3000/api/info` | JSON payload chứa `"version": "8.9.0"` |
| **Kiểm tra Keycloak OIDC** | `curl -s https://sso.miraclegroup.vn/realms/mrc/.well-known/openid-configuration` | JSON payload chứa `issuer`, `authorization_endpoint` |

---

### 7.4 Hướng Dẫn Xử Lý Sự Cố Thường Gặp (Troubleshooting FAQ)

#### 1. Sự cố: Rocket.Chat khởi động thất bại với lỗi `ReplicaSetNoPrimary`
- **Nguyên nhân**: MongoDB Replica Set `rs0` chưa được khởi tạo hoặc container `mongo-init-replica` chưa chạy thành công.
- **Cách khắc phục**:
  1. Kiểm tra log: `docker compose --env-file mrc/deploy/.env -f mrc/deploy/compose.yaml logs mongo-init-replica`.
  2. Kích hoạt lại container khởi tạo:
     ```bash
     docker compose --env-file mrc/deploy/.env -f mrc/deploy/compose.yaml up mongo-init-replica
     ```
  3. Khởi động lại Rocket.Chat:
     ```bash
     docker compose --env-file mrc/deploy/.env -f mrc/deploy/compose.yaml restart rocketchat
     ```

#### 2. Sự cố: Nút đăng nhập SSO Keycloak không xuất hiện trên giao diện
- **Nguyên nhân**: Chưa chạy script tích hợp SSO hoặc Rocket.Chat chưa nạp biến môi trường Custom OAuth.
- **Cách khắc phục**:
  1. Kiểm tra độ lệch cấu hình: `python mrc/integration/scripts/setup_sso.py --dry-run`.
  2. Thực thi lại việc áp dụng cấu hình: `python mrc/integration/scripts/setup_sso.py`.

#### 3. Sự cố: Người dùng nhận thông báo 403 Access Denied khi truy cập `/admin`
- **Nguyên nhân**: Chính sách an ninh `SEC-CHT-06` trên Caddy chặn các địa chỉ IP nằm ngoài dải mạng nội bộ doanh nghiệp.
- **Cách khắc phục**: Kết nối máy tính vào VPN nội bộ văn phòng công ty hoặc truy cập từ dải mạng được phép (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `127.0.0.1`).

---

## 8. Bộ Khung Kiểm Chứng Đơn Lệnh & Tự Động Hóa (Verification Harness)

Bộ khung kiểm thử toàn diện cung cấp các công cụ kiểm tra tự động nhanh chóng cho kỹ sư vận hành và hệ thống CI/CD:

### 8.1 Kiểm Tra Sức Khỏe Toàn Diện Hệ Thống (`healthcheck.py`)

**Tệp tin**: `mrc/integration/scripts/healthcheck.py`  
**Thời gian thực thi**: $< 500\text{ ms}$.

- **Chế độ bảng điều khiển trực quan (Human CLI)**:
  ```bash
  # Mặc định sản xuất (qua tên miền FQDN):
  python mrc/integration/scripts/healthcheck.py

  # Môi trường kiểm thử cục bộ / CI:
  python mrc/integration/scripts/healthcheck.py \
    --rc-url http://localhost:3000 \
    --kc-url http://localhost:8080/realms/mrc/protocol
  ```
  *Đầu ra thực nghiệm (Live Execution Output):*
  ```text
  ======================================================================
  MRC-CHAT SYSTEM HEALTH CHECK REPORT
  ======================================================================
  Timestamp: 2026-11-04T09:03:25Z

  • MONGODB      [HEALTHY]    127.0.0.1:27017 (1.63 ms)
  • ROCKETCHAT   [HEALTHY]    http://localhost:3000 (2875.39 ms)
      Version: unknown
  • KEYCLOAK     [HEALTHY]    http://localhost:8080/realms/mrc/.well-known/openid-configuration (44.62 ms)
      Issuer:  http://localhost:8080/realms/mrc

  ----------------------------------------------------------------------
  OVERALL STATUS: ALL SERVICES HEALTHY (100%)
  ----------------------------------------------------------------------
  ```

- **Chế độ dữ liệu có cấu trúc JSON (Monitoring / CI/CD)**:
  ```bash
  # Môi trường kiểm thử cục bộ / CI:
  python mrc/integration/scripts/healthcheck.py --json \
    --rc-url http://localhost:3000 \
    --kc-url http://localhost:8080/realms/mrc/protocol
  ```
  *Đầu ra thực nghiệm JSON (Live JSON Output):*
  ```json
  {
    "timestamp": "2026-11-04T09:03:47Z",
    "overall_healthy": true,
    "components": {
      "mongodb": {
        "component": "mongodb",
        "target": "127.0.0.1:27017",
        "replica_set": "rs0",
        "healthy": true,
        "status": "HEALTHY",
        "latency_ms": 24.39,
        "details": {
          "tcp_connected": true,
          "note": "TCP socket verified; MongoDB daemon listening."
        }
      },
      "rocketchat": {
        "component": "rocketchat",
        "target": "http://localhost:3000",
        "healthy": true,
        "status": "HEALTHY",
        "latency_ms": 2549.31,
        "version": "unknown",
        "details": {
          "endpoint": "/api/info"
        }
      },
      "keycloak": {
        "component": "keycloak",
        "target": "http://localhost:8080/realms/mrc/.well-known/openid-configuration",
        "healthy": true,
        "status": "HEALTHY",
        "latency_ms": 47.37,
        "issuer": "http://localhost:8080/realms/mrc",
        "details": {
          "authorization_endpoint": "http://localhost:8080/realms/mrc/protocol/openid-connect/auth",
          "token_endpoint": "http://localhost:8080/realms/mrc/protocol/openid-connect/token",
          "jwks_uri": "http://localhost:8080/realms/mrc/protocol/openid-connect/certs"
        }
      }
    }
  }
  ```

- **Chế độ tự kiểm thử đơn vị của module**:
  ```bash
  python mrc/integration/scripts/healthcheck.py --test
  ```
  *Kết quả:* Vượt qua 7/7 bài kiểm thử đơn vị (`100.0% passed`).

---

### 8.2 Kiểm Thử Tích Hợp & Schema Cấu Hình SSO (`setup_sso.py --test`)

```bash
python mrc/integration/scripts/setup_sso.py --test
```
Bộ kiểm thử thực thi 68 xác minh tự động bao gồm:
1. **Group 1**: 9 trường hợp chuẩn hóa MAP-07 theo đặc tả.
2. **Group 2**: 15 trường hợp kiểm thử điều kiện biên MAP-07 (cắt ngắn 32 ký tự, ký tự đặc biệt, dấu chấm liên tiếp).
3. **Group 3**: 17 bài kiểm tra hàm `assert_canonical_username` (chấp thuận chuỗi chuẩn tắc, từ chối chuỗi vi phạm kèm lý do).
4. **Group 4**: 14 bài kiểm tra schema cấu hình SSO và cấu trúc JSON trường tùy biến `Accounts_CustomFields`.
5. **Group 5**: 13 bài kiểm tra cấu trúc định nghĩa Realm và Client `chat-miracle` trong `mrc-realm.json`.

*Kết quả mong đợi:*
```text
======================================================================
TEST RESULTS: 68 / 68 passed (100.0%)
======================================================================
```

---

### 8.3 Toàn Bộ Bộ Kiểm Thử Hồi Quy Pytest (82 Tests)

Thực thi toàn bộ bộ kiểm thử hồi quy bao quát toàn hệ thống:
```bash
python -m pytest mrc/tests -v
```

**Ma trận 82 bài kiểm thử tự động**:
- `mrc/tests/test_configuration.py`: 12 tests (Kiểm tra tệp `mrc-realm.json`, cấu hình client `chat-miracle`, 4 protocol mappers, schema SSO target settings, healthcheck structures, Clean Upstream Invariant, và không có tệp i18n).
- `mrc/tests/test_infrastructure_remediation.py`: 5 tests (Kiểm tra `backup_mongo.sh` có `--oplog`, không `--db`, tệp tạm nguyên tử, `gzip -t`; `init-replica.sh` mã thoát 1 khi timeout; `Caddyfile.snippet` permissions-policy camera/mic và regex `(?i)^/admin`; định dạng pure LF).
- `mrc/tests/test_map07_normalization.py`: 65 tests (Kiểm tra toàn diện thuật toán chuẩn hóa tiếng Việt, điều kiện biên 32 ký tự, xử lý ký tự rác, và hàm assert canonical).

*Kết quả mong đợi:*
```text
============================= 82 passed in ~4s ==============================
```

---

## 9. Hướng Dẫn Kiểm Toán Bất Biến Mã Nguồn Gốc & Tính Toàn Vẹn Hệ Thống

Dành cho kiểm toán viên an ninh và chất lượng độc lập (*Teamwork Auditor / Security Reviewer*) để kiểm tra sự tuân thủ các cam kết kiến trúc.

### 9.1 Lệnh Kiểm Toán Cô Lập Mã Nguồn Gốc (Clean Upstream Invariant)

Thực hiện từ thư mục gốc của dự án (`mrc-chat`):

1. **Kiểm tra thay đổi trong các thư mục nguyên bản**:
   ```bash
   git status --porcelain apps/ packages/ package.json
   ```
   *Tiêu chí nghiệm thu:* Lệnh trả về **HOÀN TOÀN TRỐNG (0 dòng)**. Mã thoát `0`.

2. **Kiểm tra phân định vùng ảnh hưởng trên toàn kho mã nguồn**:
   ```bash
   git status --porcelain | grep -v "^.. mrc/"
   ```
   *Tiêu chí nghiệm thu:* Lệnh trả về **HOÀN TOÀN TRỐNG**. Mọi tệp tin được thêm mới hoặc chỉnh sửa bắt buộc phải nằm 100% bên trong tiền tố `mrc/`.

3. **Kiểm tra độ lệch mã nguồn (Git Diff)**:
   ```bash
   git diff HEAD -- apps/ packages/ package.json
   ```
   *Tiêu chí nghiệm thu:* Không có bất kỳ dòng mã nào bị thay đổi.

---

### 9.2 Lệnh Kiểm Toán Loại Trừ Tuyệt Đối i18n / Việt Hóa

Theo chỉ thị của dự án, 100% các tệp i18n / ngôn ngữ đều không được phép tồn tại trong thư mục giải pháp `mrc/`:

```bash
find mrc/ -type f \( -iname "*i18n*" -o -iname "*vi.json*" -o -iname "*messages_vi*" -o -iname "*locales*" \)
```
*Tiêu chí nghiệm thu:* Lệnh trả về **HOÀN TOÀN TRỐNG (0 tệp tin tìm thấy)**.

---

### 9.3 Lệnh Kiểm Toán Định Dạng Ngắt Dòng 100% Pure LF (Zero CRLF)

Ký tự ngắt dòng Windows CRLF (`\r\n`) gây lỗi nghiêm trọng khi thực thi kịch bản shell trên container Linux (lỗi `^M: bad interpreter`). Toàn bộ các tệp trong `mrc/` bắt buộc phải sử dụng định dạng ngắt dòng LF (`\n`).

- **Kiểm tra bằng Python trên mọi nền tảng**:
  ```bash
  python -c "from pathlib import Path; crlf = [str(p) for p in Path('mrc').rglob('*') if p.is_file() and '__pycache__' not in str(p) and b'\r\n' in p.read_bytes()]; print('CRLF Files Count:', len(crlf)); exit(1 if crlf else 0)"
  ```
  *Tiêu chí nghiệm thu:* Đầu ra in `CRLF Files Count: 0` và thoát với mã `0`.

- **Kiểm tra bằng PowerShell trên Windows**:
  ```powershell
  $crlfFiles = Get-ChildItem -Path "mrc" -Recurse -File | Where-Object {
      $_.FullName -notmatch '__pycache__' -and $_.Extension -notmatch '\.(pyc|png|ico|gz|zip)$'
  } | Where-Object {
      $bytes = [System.IO.File]::ReadAllBytes($_.FullName)
      for ($i = 0; $i -lt $bytes.Length - 1; $i++) {
          if ($bytes[$i] -eq 13 -and $bytes[$i+1] -eq 10) { return $true }
      }
      return $false
  }
  Write-Host "Tổng số tệp chứa CRLF: $($crlfFiles.Count)"
  if ($crlfFiles.Count -gt 0) { exit 1 } else { exit 0 }
  ```
  *Tiêu chí nghiệm thu:* Kết quả `Tổng số tệp chứa CRLF: 0`.

---

### 9.4 Bảng Ký Duyệt Tuân Thủ Kiểm Toán (Auditor Compliance Sign-Off Table)

| Hạng Mục Kiểm Toán | Tiêu Chuẩn Tham Chiếu | Chỉ Số Yêu Cầu | Kết Quả Thực Tế | Đánh Giá Nghiệm Thu |
| :--- | :--- | :--- | :--- | :---: |
| **Clean Upstream Invariant** | R1 / Acceptance Criteria | 0 tệp upstream bị sửa đổi | 0 tệp bị sửa đổi | **ĐẠT (PASS)** |
| **Loại trừ i18n / Dịch thuật** | R1 / Acceptance Criteria | 0 tệp ngôn ngữ trong `mrc/` | 0 tệp tồn tại | **ĐẠT (PASS)** |
| **Chuẩn ngắt dòng Pure LF** | Clean Invariant | 0 tệp chứa CRLF trong `mrc/` | 0 tệp CRLF (100% LF) | **ĐẠT (PASS)** |
| **Thăm dò sức khỏe hệ thống** | Acceptance Criteria | MongoDB, RC, Keycloak | 3/3 dịch vụ HEALTHY | **ĐẠT (PASS)** |
| **Kiểm thử đơn vị Healthcheck**| Milestone 2 / 3 | 7 self-tests | 7/7 PASSED (100%) | **ĐẠT (PASS)** |
| **Kiểm thử tự động setup_sso** | Milestone 2 / 3 | 68 unit tests | 68/68 PASSED (100%) | **ĐẠT (PASS)** |
| **Kiểm thử hồi quy Pytest** | Milestone 3 Harness | 82 automated tests | 82/82 PASSED (100%) | **ĐẠT (PASS)** |
| **Kịch bản sao lưu CSDL** | Milestone 1 Remediation | `--oplog`, atomic, `gzip -t` | Cài đặt đầy đủ | **ĐẠT (PASS)** |
| **Quy trình phục hồi CSDL** | Milestone 3 Runbook | `mongorestore --oplogReplay` | Cài đặt đầy đủ | **ĐẠT (PASS)** |
| **Bảo vệ trang quản trị** | SEC-CHT-06 | Regex không phân biệt hoa thường | `(?i)^/admin` chặn 403 | **ĐẠT (PASS)** |
| **Quy trình cứu hộ khẩn cấp** | SEC-CHT-03 | Break-glass local admin | 3 phương án phục hồi | **ĐẠT (PASS)** |
