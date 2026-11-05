#!/usr/bin/env bash
# =============================================================================
# MRC-CHAT MONGODB AUTOMATED BACKUP SCRIPT
# Location: mrc/infrastructure/mongodb/backup_mongo.sh
# Purpose: Point-in-time full cluster backup with mongodump oplog and retention
# =============================================================================

set -eo pipefail

MONGO_HOST="${MONGO_HOST:-127.0.0.1}"
MONGO_PORT="${MONGO_PORT:-27017}"
MONGO_DB="${MONGO_DB:-rocketchat}"
BACKUP_DIR="${BACKUP_DIR:-/backup/mongodb}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"
REMOTE_BACKUP_DEST="${REMOTE_BACKUP_DEST:-}"

TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
BACKUP_FILENAME="mrc_chat_backup_${TIMESTAMP}.archive.gz"
BACKUP_PATH="${BACKUP_DIR}/${BACKUP_FILENAME}"
TEMP_BACKUP_PATH="${BACKUP_PATH}.tmp.$$"

# Bẫy lỗi và tín hiệu ngắt để dọn dẹp tệp tạm không hoàn chỉnh
cleanup() {
    local exit_code=$?
    if [ -f "${TEMP_BACKUP_PATH}" ]; then
        echo ">>> [backup_mongo] CẢNH BÁO: Dọn dẹp tệp tạm không hoàn chỉnh: ${TEMP_BACKUP_PATH}" >&2
        rm -f "${TEMP_BACKUP_PATH}"
    fi
    exit "${exit_code}"
}
trap cleanup EXIT INT TERM

echo ">>> [backup_mongo] ========================================================"
echo ">>> [backup_mongo] Bắt đầu sao lưu toàn bộ cụm MongoDB (kèm Oplog) lúc $(date '+%Y-%m-%d %H:%M:%S')"
echo ">>> [backup_mongo] Máy chủ đích: ${MONGO_HOST}:${MONGO_PORT}"
echo ">>> [backup_mongo] Tệp đích: ${BACKUP_PATH}"

# Tạo thư mục lưu trữ nếu chưa tồn tại
mkdir -p "${BACKUP_DIR}"

# Thực hiện lệnh mongodump toàn bộ instance với --oplog (KHÔNG dùng --db khi có --oplog)
# Ghi ra tệp tạm để tránh tệp hỏng khi tiến trình bị gián đoạn
mongodump \
    --host="${MONGO_HOST}:${MONGO_PORT}" \
    --archive="${TEMP_BACKUP_PATH}" \
    --gzip \
    --oplog

# Kiểm tra tính toàn vẹn của tệp nén bằng gzip -t và kiểm tra kích thước
if [ -f "${TEMP_BACKUP_PATH}" ] && gzip -t "${TEMP_BACKUP_PATH}" 2>/dev/null; then
    mv -f "${TEMP_BACKUP_PATH}" "${BACKUP_PATH}"
    FILE_SIZE=$(du -h "${BACKUP_PATH}" | cut -f1)
    echo ">>> [backup_mongo] Sao lưu THÀNH CÔNG! Dung lượng tệp: ${FILE_SIZE} (đã xác thực tính toàn vẹn gzip)"
else
    echo ">>> [backup_mongo] LỖI: Tệp sao lưu không tồn tại hoặc bị lỗi tính toàn vẹn gzip!" >&2
    rm -f "${TEMP_BACKUP_PATH}"
    exit 1
fi

# Đẩy bản sao lưu ra đích ngoài máy chủ (Off-host backup) nếu được khai báo
if [ -n "${REMOTE_BACKUP_DEST}" ]; then
    echo ">>> [backup_mongo] Đang chuyển bản sao lưu ra ngoài máy chủ: ${REMOTE_BACKUP_DEST}..."
    if command -v rsync >/dev/null 2>&1; then
        rsync -avz "${BACKUP_PATH}" "${REMOTE_BACKUP_DEST}/"
        echo ">>> [backup_mongo] Đẩy ra máy chủ ngoài qua rsync THÀNH CÔNG."
    elif command -v rclone >/dev/null 2>&1; then
        rclone copy "${BACKUP_PATH}" "${REMOTE_BACKUP_DEST}"
        echo ">>> [backup_mongo] Đẩy ra cloud/S3 qua rclone THÀNH CÔNG."
    elif command -v scp >/dev/null 2>&1; then
        scp -q "${BACKUP_PATH}" "${REMOTE_BACKUP_DEST}/"
        echo ">>> [backup_mongo] Đẩy ra máy chủ ngoài qua scp THÀNH CÔNG."
    else
        echo ">>> [backup_mongo] CẢNH BÁO: Khai báo REMOTE_BACKUP_DEST nhưng máy chủ không có sẵn rsync/rclone/scp!" >&2
    fi
fi

# Tự động dọn dẹp các bản sao lưu cũ vượt quá RETENTION_DAYS (mặc định giữ 14 ngày/14 bản)
echo ">>> [backup_mongo] Kiểm tra dọn dẹp các bản sao lưu cũ hơn ${RETENTION_DAYS} ngày trong ${BACKUP_DIR}..."
DELETED_COUNT=0
while IFS= read -r old_file; do
    if [ -n "${old_file}" ]; then
        rm -f "${old_file}"
        echo ">>> [backup_mongo] Đã xóa bản sao lưu cũ: ${old_file}"
        DELETED_COUNT=$((DELETED_COUNT + 1))
    fi
done < <(find "${BACKUP_DIR}" -type f \( -name "mrc_chat_backup_*.archive.gz" -o -name "mrc_chat_${MONGO_DB}_backup_*.archive.gz" \) -mtime "+${RETENTION_DAYS}")

echo ">>> [backup_mongo] Đã hoàn tất dọn dẹp (${DELETED_COUNT} tệp đã xóa)."
echo ">>> [backup_mongo] Kết thúc tiến trình sao lưu lúc $(date '+%Y-%m-%d %H:%M:%S')"
echo ">>> [backup_mongo] ========================================================"

exit 0
