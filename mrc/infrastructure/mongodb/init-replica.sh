#!/usr/bin/env bash
# =============================================================================
# MRC-CHAT MONGODB REPLICA SET INITIALIZATION SCRIPT
# Location: mrc/infrastructure/mongodb/init-replica.sh
# Purpose: Automatically initialize rs0 for MongoDB 8.0 on cluster startup
# =============================================================================

set -eo pipefail

MONGO_HOST="${MONGO_HOST:-mongodb}"
MONGO_PORT="${MONGO_PORT:-27017}"
MONGO_REPLICA_SET_NAME="${MONGO_REPLICA_SET_NAME:-rs0}"
MAX_RETRIES="${MAX_RETRIES:-30}"
RETRY_INTERVAL="${RETRY_INTERVAL:-2}"
MAX_PRIMARY_RETRIES="${MAX_PRIMARY_RETRIES:-30}"

echo ">>> [init-replica] Bắt đầu kiểm tra kết nối tới MongoDB tại ${MONGO_HOST}:${MONGO_PORT}..."

# Bước 1: Chờ MongoDB sẵn sàng nhận lệnh ping
retries=0
until mongosh --host "${MONGO_HOST}" --port "${MONGO_PORT}" --eval "db.adminCommand('ping')" --quiet > /dev/null 2>&1; do
    retries=$((retries + 1))
    if [ "${retries}" -ge "${MAX_RETRIES}" ]; then
        echo ">>> [init-replica] LỖI: Quá thời gian chờ MongoDB khởi động sau ${MAX_RETRIES} lần thử." >&2
        exit 1
    fi
    echo ">>> [init-replica] Đang chờ MongoDB (${retries}/${MAX_RETRIES})..."
    sleep "${RETRY_INTERVAL}"
done

echo ">>> [init-replica] MongoDB đã phản hồi ping thành công."

# Bước 2: Kiểm tra trạng thái Replica Set hiện tại
RS_STATUS=$(mongosh --host "${MONGO_HOST}" --port "${MONGO_PORT}" --eval "try { rs.status().ok } catch(e) { 0 }" --quiet || echo "0")

if [ "${RS_STATUS}" = "1" ]; then
    echo ">>> [init-replica] THÔNG BÁO: Replica Set '${MONGO_REPLICA_SET_NAME}' đã được khởi tạo trước đó. Bỏ qua lệnh rs.initiate()."
else
    # Bước 3: Thực hiện khởi tạo rs.initiate()
    echo ">>> [init-replica] Đang thực hiện rs.initiate() với Replica Set name: '${MONGO_REPLICA_SET_NAME}'..."
    INIT_OUTPUT=$(mongosh --host "${MONGO_HOST}" --port "${MONGO_PORT}" --eval "rs.initiate({ _id: '${MONGO_REPLICA_SET_NAME}', members: [{ _id: 0, host: '${MONGO_HOST}:${MONGO_PORT}' }] })" --quiet)
    echo ">>> [init-replica] Kết quả rs.initiate(): ${INIT_OUTPUT}"
fi

# Bước 4: Chờ nút MongoDB được bầu chọn làm PRIMARY (áp dụng cho cả khởi tạo mới và sau reboot)
echo ">>> [init-replica] Đang chờ bầu cử/xác nhận trạng thái PRIMARY..."
primary_retries=0
until mongosh --host "${MONGO_HOST}" --port "${MONGO_PORT}" --eval "try { (db.hello().isWritablePrimary || db.isMaster().ismaster) } catch(e) { false }" --quiet | grep -q "true"; do
    primary_retries=$((primary_retries + 1))
    if [ "${primary_retries}" -ge "${MAX_PRIMARY_RETRIES}" ]; then
        echo ">>> [init-replica] LỖI: Quá thời gian chờ bầu cử PRIMARY sau ${primary_retries} lần thử. Chi tiết rs.status():" >&2
        mongosh --host "${MONGO_HOST}" --port "${MONGO_PORT}" --eval "rs.status()" --quiet || true
        exit 1
    fi
    sleep 1
done

echo ">>> [init-replica] HOÀN TẤT: Replica Set '${MONGO_REPLICA_SET_NAME}' đã hoạt động ổn định ở trạng thái PRIMARY!"
exit 0
