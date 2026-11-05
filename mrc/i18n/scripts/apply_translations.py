#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MRC Rocket.Chat Apply Translations Script (VH-07)
================================================
Nạp bản dịch tuỳ biến vào Rocket.Chat qua REST API (Setting: Custom_Translations).
Theo tài liệu kỹ thuật MRC-CHT-HD-01 Mục 6.3 (VH-07):
- Đọc tệp custom-translations.vi-VN.json.
- Kiểm tra tính hợp lệ trước khi gửi.
- Gửi payload lên endpoint POST /api/v1/settings/Custom_Translations.
- Đảm bảo tính lũy đẳng (Idempotent), áp dụng tức thì không cần khởi động lại máy chủ.
"""

import os
import sys
import json
import argparse
import urllib.request
import urllib.error

if sys.stdout and hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if sys.stderr and hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

DEFAULT_JSON_FILE = r"D:\project\mrc-chat\mrc\i18n\locales\custom-translations.vi-VN.json"
DEFAULT_RC_URL = os.environ.get("RC_URL", "http://127.0.0.1:3000")


def get_auth_token(rc_url: str, user: str, password: str) -> tuple[str, str]:
    """Lấy token xác thực quản trị viên nếu chưa có RC_UID / RC_TOKEN."""
    login_url = f"{rc_url.rstrip('/')}/api/v1/login"
    body = json.dumps({"user": user, "password": password}).encode("utf-8")
    req = urllib.request.Request(
        login_url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        if data.get("status") == "success":
            uid = data["data"]["userId"]
            token = data["data"]["authToken"]
            return uid, token
        else:
            raise RuntimeError(f"Đăng nhập thất bại: {data}")


def apply_custom_translations(rc_url: str, uid: str, token: str, json_file: str) -> bool:
    """Gọi REST API nạp Custom_Translations vào Rocket.Chat."""
    if not os.path.exists(json_file):
        print(f"[LỖI] Không tìm thấy tệp bản dịch: {json_file}")
        return False

    with open(json_file, "r", encoding="utf-8") as f:
        payload_data = json.load(f)

    # Đảm bảo cấu trúc {"vi-VN": {...}}
    if "vi-VN" not in payload_data:
        payload_data = {"vi-VN": payload_data}

    # Chuỗi JSON được stringify trước khi gửi vào field "value"
    json_str_value = json.dumps(payload_data, ensure_ascii=False)
    api_url = f"{rc_url.rstrip('/')}/api/v1/settings/Custom_Translations"
    request_body = json.dumps({"value": json_str_value}).encode("utf-8")

    req = urllib.request.Request(
        api_url,
        data=request_body,
        headers={
            "X-User-Id": uid,
            "X-Auth-Token": token,
            "Content-Type": "application/json"
        },
        method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp_data = json.loads(resp.read().decode("utf-8"))
            if resp_data.get("success"):
                print(f"[THÀNH CÔNG] Đã nạp thành công Custom_Translations lên Rocket.Chat ({rc_url})!")
                print(f"  -> Tổng số chuỗi Việt hóa áp dụng: {len(payload_data['vi-VN'])} khóa")
                return True
            else:
                print(f"[LỖI] API trả về không thành công: {resp_data}")
                return False
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="ignore")
        print(f"[LỖI HTTP {e.code}] {err_body}")
        return False
    except Exception as e:
        print(f"[LỖI] Không thể kết nối tới Rocket.Chat: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="MRC Rocket.Chat Apply Translations (VH-07)")
    parser.add_argument("--file", default=DEFAULT_JSON_FILE, help="Đường dẫn tệp custom-translations.vi-VN.json")
    parser.add_argument("--url", default=DEFAULT_RC_URL, help="Địa chỉ Rocket.Chat (Mặc định: http://127.0.0.1:3000)")
    parser.add_argument("--uid", default=os.environ.get("RC_UID", ""), help="Rocket.Chat User ID")
    parser.add_argument("--token", default=os.environ.get("RC_TOKEN", ""), help="Rocket.Chat Auth Token")
    parser.add_argument("--admin-user", default=os.environ.get("ADM_LOCAL_USERNAME", "adm-local-chat"), help="Admin Username")
    parser.add_argument("--admin-pass", default=os.environ.get("ADM_LOCAL_PASSWORD", ""), help="Admin Password")
    args = parser.parse_args()

    print("================================================================================")
    print("MRC ROCKET.CHAT APPLY TRANSLATIONS (VH-07)")
    print("================================================================================")

    uid = args.uid
    token = args.token

    if not uid or not token:
        if not args.admin_pass:
            print("[LỖI] Thiếu thông tin xác thực: đặt RC_UID và RC_TOKEN, hoặc ADM_LOCAL_PASSWORD.")
            sys.exit(1)
        print(f"-> Chưa có token, đang xác thực bằng tài khoản admin: {args.admin_user}...")
        try:
            uid, token = get_auth_token(args.url, args.admin_user, args.admin_pass)
            print("-> Xác thực admin thành công!")
        except Exception as e:
            print(f"[LỖI] Không thể xác thực admin: {e}")
            sys.exit(1)

    success = apply_custom_translations(args.url, uid, token, args.file)
    if not success:
        sys.exit(1)


if __name__ == "__main__":
    main()
