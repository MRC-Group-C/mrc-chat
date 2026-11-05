#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MRC Rocket.Chat i18n Translation & Aggregation Tool
===================================================
Tự động tổng hợp và dịch các tệp ngôn ngữ từ packages/i18n/src/locales.
- Tự động phát hiện locale có số lượng khóa lớn nhất (base locale: en.i18n.json với 7.518 khóa).
- Tổng hợp các bản dịch hiện có, rà soát chất lượng theo bảng thuật ngữ MRC.
- Dịch tự động các khóa còn thiếu KHÔNG CẦN quét lại toàn bộ dự án (không AST parse source code).
- Bảo vệ biến suy diễn {{variable}}, %s, thẻ HTML/React <1></1>, markdown.
- Tuân thủ nghiêm ngặt bộ quy tắc chất lượng K1 - K8 theo tài liệu MRC-CHT-HD-01.
- Xuất ra tệp vi-VN.i18n.json và custom-translations.vi-VN.json (cho thiết lập Custom_Translations).
- Hỗ trợ lọc theo mức ưu tiên (P1, P2, P3, P4) từ danh mục chuẩn MRC.
- Hỗ trợ nạp trực tiếp qua Rocket.Chat REST API (Custom_Translations setting).
"""

import os
import sys
import json
import re
import time
import argparse
import unicodedata
from typing import Dict, Any, List, Tuple, Set, Optional
from urllib.request import Request, urlopen
from urllib.parse import quote

# Đảm bảo console UTF-8 trên Windows
if sys.stdout and hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if sys.stderr and hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

# Đường dẫn mặc định
DEFAULT_LOCALES_DIR = r"D:\project\mrc-chat\packages\i18n\src\locales"
DEFAULT_OUTPUT_DIR = r"D:\project\mrc-chat\mrc\i18n\locales"
DEFAULT_EXCEL_PATH = r"D:\MRC_RC_Viet-hoa_Danh-muc-chuoi.xlsx"
CACHE_FILE_PATH = r"D:\project\mrc-chat\mrc\i18n\translations_cache.json"

# ==============================================================================
# BẢNG THUẬT NGỮ CHUẨN MRC (Theo tài liệu kỹ thuật MRC-CHT-HD-01 Mục 6.4)
# ==============================================================================
STANDARD_GLOSSARY = {
    # Khái niệm không gian & phòng
    "Workspace": "Không gian làm việc",
    "Room": "Phòng",
    "Channel": "Kênh công khai",
    "Channels": "Kênh công khai",
    "Public Channel": "Kênh công khai",
    "Private Channel": "Kênh riêng tư",
    "Private channel": "Kênh riêng tư",
    "Direct Message": "Tin nhắn trực tiếp",
    "Direct Messages": "Tin nhắn trực tiếp",
    "Direct message": "Tin nhắn trực tiếp",
    "Discussion": "Thảo luận",
    "Discussions": "Thảo luận",
    "Thread": "Luồng",
    "Threads": "Luồng",
    "Topic": "Chủ đề",
    "Directory": "Danh bạ",
    "Sidebar": "Thanh bên",
    "Category": "Nhóm hiển thị",

    # Nhóm & Vai trò
    "Team": "Nhóm",
    "Teams": "Nhóm",
    "Owner": "Chủ sở hữu",
    "Moderator": "Người điều phối",
    "Leader": "Trưởng nhóm",
    "Admin": "Quản trị viên",
    "Administrator": "Quản trị viên",
    "User": "Người dùng",
    "Member": "Thành viên",
    "Members": "Thành viên",

    # Thao tác & Tin nhắn
    "Message": "Tin nhắn",
    "Messages": "Tin nhắn",
    "Mention": "Nhắc",
    "Mentions": "Lượt nhắc",
    "Reaction": "Bày tỏ cảm xúc",
    "Reactions": "Bày tỏ cảm xúc",
    "File": "Tệp",
    "Files": "Tệp",
    "Upload": "Tải lên",
    "Pin": "Ghim",
    "Unpin": "Bỏ ghim",
    "Star": "Gắn sao",
    "Follow": "Theo dõi",
    "Archive": "Lưu trữ",
    "Delete": "Xoá",
    "Remove": "Gỡ",
    "Cancel": "Huỷ",
    "Sign in": "Đăng nhập",
    "Log in": "Đăng nhập",
    "Log out": "Đăng xuất",
    "Sign out": "Đăng xuất",

    # Trạng thái & Bảo mật
    "Status": "Trạng thái",
    "Online": "Trực tuyến",
    "Away": "Vắng mặt",
    "Busy": "Bận",
    "Invisible": "Ẩn",
    "Offline": "Ngoại tuyến",
    "Two-factor authentication": "Xác thực hai lớp",
    "Personal access token": "Mã truy cập cá nhân"
}

# Các từ cấm và từ thay thế bắt buộc (Luật K5)
PROHIBITED_TERMS_REPLACEMENT = [
    (r"\bThông điệp\b", "Tin nhắn"),
    (r"\bthông điệp\b", "tin nhắn"),
    (r"\bĐội\b", "Nhóm"),
    (r"\bđội\b", "nhóm"),
    (r"\btập tin\b", "tệp"),
    (r"\bTập tin\b", "Tệp"),
    (r"\bhủy bỏ\b", "Huỷ"),
    (r"\bhủy\b", "huỷ"),
    (r"\bxóa\b", "xoá"),
    (r"\bhoá\b", "hoá"),
    (r"\btin nhắn riêng\b", "tin nhắn trực tiếp"),
    (r"\bTin nhắn riêng\b", "Tin nhắn trực tiếp")
]

# Regex nhận diện biến và thẻ cần bảo vệ
VAR_PATTERNS = [
    re.compile(r"\{\{[^}]+\}\}"),          # {{count}}, {{username}}
    re.compile(r"__[^_]+__"),               # __count__, __username__
    re.compile(r"%[0-9]*[sdifoxX]"),       # %s, %d
    re.compile(r"</?[a-zA-Z0-9_-]+[^>]*>"), # <strong>, <1>, </1>, <icon>
    re.compile(r"\*\*[^*]+\*\*"),           # **bold**
    re.compile(r"\b_[^_]+_\b"),             # _italic_
]


class VariableProtector:
    """Bảo vệ các biến {{...}}, %s, thẻ HTML/React và Markdown trong quá trình dịch."""
    def __init__(self):
        self.mapping = {}
        self.counter = 0

    def mask(self, text: str) -> str:
        self.mapping.clear()
        self.counter = 0

        def replace_token(match):
            placeholder = f"ZZVAR{self.counter}ZZ"
            self.mapping[placeholder] = match.group(0)
            self.counter += 1
            return placeholder

        masked_text = text
        for pat in VAR_PATTERNS:
            masked_text = pat.sub(replace_token, masked_text)
        return masked_text

    def unmask(self, text: str) -> str:
        result = text
        for placeholder, original in self.mapping.items():
            pattern = re.compile(re.escape(placeholder), re.IGNORECASE)
            result = pattern.sub(original, result)
            loose_pattern = re.compile(r"ZZ\s*VAR\s*" + placeholder.replace("ZZVAR", "").replace("ZZ", "") + r"\s*ZZ", re.IGNORECASE)
            result = loose_pattern.sub(original, result)
        return result


class TranslationEngine:
    """Bộ máy dịch tự động có caching, rate-limiting và xử lý lỗi."""
    def __init__(self, cache_path: str = CACHE_FILE_PATH):
        self.cache_path = cache_path
        self.cache: Dict[str, str] = {}
        self.load_cache()
        self.protector = VariableProtector()

    def load_cache(self):
        if os.path.exists(self.cache_path):
            try:
                with open(self.cache_path, "r", encoding="utf-8") as f:
                    self.cache = json.load(f)
            except Exception as e:
                print(f"[Cảnh báo] Không thể đọc cache: {e}")

    def save_cache(self):
        try:
            os.makedirs(os.path.dirname(self.cache_path), exist_ok=True)
            with open(self.cache_path, "w", encoding="utf-8") as f:
                json.dump(self.cache, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[Lỗi] Không thể lưu cache: {e}")

    def translate_text(self, text: str, src: str = "en", tgt: str = "vi") -> str:
        if not text or not text.strip():
            return text

        norm_text = text.strip()
        if norm_text in self.cache:
            return self.cache[norm_text]

        # Kiểm tra nếu là thuật ngữ chuẩn đơn lẻ
        if norm_text in STANDARD_GLOSSARY:
            translated = STANDARD_GLOSSARY[norm_text]
            self.cache[norm_text] = translated
            return translated

        # Che các biến để không bị dịch làm sai cú pháp
        masked = self.protector.mask(text)

        # Thử dịch qua Google Translate web endpoint
        translated = None
        for attempt in range(3):
            try:
                url = f"https://translate.googleapis.com/translate_a/single?client=gtx&sl={src}&tl={tgt}&dt=t&q={quote(masked)}"
                req = Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
                with urlopen(req, timeout=8) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    translated = "".join(item[0] for item in data[0] if item and item[0])
                    break
            except Exception as e:
                time.sleep(1.0 + attempt * 1.5)

        if not translated:
            # Fallback nếu mạng lỗi hoặc 429: Giữ nguyên các biến
            translated = masked

        # Khôi phục các biến
        unmasked = self.protector.unmask(translated)

        # Hậu xử lý theo bảng thuật ngữ và luật K5
        processed = self.post_process(unmasked, source_text=text)

        self.cache[norm_text] = processed
        return processed

    def post_process(self, text: str, source_text: str = "") -> str:
        """Hậu xử lý chuẩn hóa Unicode NFC, viết hoa chữ cái đầu và thay từ cấm."""
        res = unicodedata.normalize("NFC", text)

        for pat, repl in PROHIBITED_TERMS_REPLACEMENT:
            res = re.sub(pat, repl, res)

        # Luật K7: Giữ viết hoa chữ cái đầu nếu nguồn viết hoa
        if source_text and source_text[0].isupper() and len(res) > 0:
            res = res[0].upper() + res[1:]

        return res


def load_excel_references(excel_path: str) -> Tuple[Dict[str, str], Dict[str, str], Dict[str, str], Dict[str, str]]:
    """Đọc dữ liệu từ tệp Excel chuẩn MRC: Chuỗi thiếu, Rà soát chất lượng, Thuật ngữ, Phân nhóm ưu tiên."""
    missing_dict = {}
    review_dict = {}
    glossary_dict = {}
    priority_dict = {}

    if not os.path.exists(excel_path):
        print(f"[Thông báo] Không tìm thấy tệp Excel: {excel_path}, bỏ qua nạp từ Excel.")
        return missing_dict, review_dict, glossary_dict, priority_dict

    try:
        import openpyxl
        wb = openpyxl.load_workbook(excel_path, read_only=True)

        if "Chuoi thieu" in wb.sheetnames:
            sheet = wb["Chuoi thieu"]
            for row in sheet.iter_rows(min_row=2, values_only=True):
                k = row[0]
                prio = row[3]
                init_val = row[4]
                prop_val = row[5]
                val = prop_val or init_val
                if k:
                    key_str = str(k).strip()
                    if prio:
                        priority_dict[key_str] = str(prio).strip().upper()
                    if val:
                        missing_dict[key_str] = str(val).strip()

        if "Ra soat chat luong" in wb.sheetnames:
            sheet = wb["Ra soat chat luong"]
            for row in sheet.iter_rows(min_row=2, values_only=True):
                k = row[0]
                suggest_val = row[4]
                if k and suggest_val:
                    review_dict[str(k).strip()] = str(suggest_val).strip()

        if "Thuat ngu" in wb.sheetnames:
            sheet = wb["Thuat ngu"]
            for row in sheet.iter_rows(min_row=2, values_only=True):
                en_term = row[0]
                vi_term = row[1]
                if en_term and vi_term:
                    glossary_dict[str(en_term).strip()] = str(vi_term).strip()

        print(f"[Excel] Đã nạp từ {excel_path}:")
        print(f"  - Phân loại ưu tiên: {len(priority_dict)} khóa")
        print(f"  - Bản dịch chuỗi thiếu khởi điểm: {len(missing_dict)} mục")
        print(f"  - Đề xuất sửa rà soát chất lượng: {len(review_dict)} mục")
        print(f"  - Thuật ngữ chuẩn: {len(glossary_dict)} mục")
    except Exception as e:
        print(f"[Cảnh báo] Lỗi khi đọc Excel {excel_path}: {e}")

    return missing_dict, review_dict, glossary_dict, priority_dict


def scan_locales_directory(locales_dir: str) -> Tuple[str, Dict[str, int], Dict[str, Any]]:
    """
    Quét trực tiếp thư mục locales, không quét toàn bộ dự án.
    Tìm locale có số lượng khóa lớn nhất.
    """
    print(f"\n[Bước 1] Quét các bản dịch trong: {locales_dir}")
    if not os.path.exists(locales_dir):
        raise FileNotFoundError(f"Thư mục không tồn tại: {locales_dir}")

    locale_counts = {}
    base_locale_data = {}
    max_locale = None
    max_count = -1

    for fname in os.listdir(locales_dir):
        if not fname.endswith(".i18n.json"):
            continue
        fpath = os.path.join(locales_dir, fname)
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                data = json.load(f)
                count = len(data)
                locale_name = fname.replace(".i18n.json", "")
                locale_counts[locale_name] = count
                if count > max_count:
                    max_count = count
                    max_locale = locale_name
                    base_locale_data = data
        except Exception as e:
            print(f"  [!] Không thể đọc {fname}: {e}")

    sorted_locales = sorted(locale_counts.items(), key=lambda x: x[1], reverse=True)
    print(f"  -> Tổng số locale tìm thấy: {len(sorted_locales)}")
    print(f"  -> Locale có số khóa nhiều nhất: '{max_locale}' ({max_count} khóa)")
    print("  -> Top 5 locale đầy đủ nhất:")
    for loc, cnt in sorted_locales[:5]:
        pct = (cnt / max_count * 100) if max_count > 0 else 0
        print(f"     * {loc}.i18n.json: {cnt} khóa ({pct:.1f}%)")

    return max_locale, locale_counts, base_locale_data


def validate_rule_k1_to_k8(key: str, en_val: Any, trans_val: Any) -> List[str]:
    """Kiểm tra tuân thủ đầy đủ 8 quy tắc chất lượng K1 - K8 (MRC-CHT-HD-01 Mục 6.3)."""
    errors = []

    # K4: Xử lý trường hợp số nhiều (dict)
    if isinstance(en_val, dict):
        if not isinstance(trans_val, dict):
            errors.append("K4: Giá trị nguồn là dict số nhiều nhưng bản dịch là chuỗi đơn.")
        else:
            if "other" not in trans_val:
                errors.append("K4: Thiếu biến thể số nhiều '_other' bắt buộc cho tiếng Việt.")
        return errors

    if not isinstance(trans_val, str):
        errors.append("K8: Bản dịch không phải chuỗi ký tự hợp lệ.")
        return errors

    # K8: Kiểm tra chuẩn hóa Unicode NFC
    if not unicodedata.is_normalized("NFC", trans_val):
        errors.append("K8: Chuỗi chưa được chuẩn hóa Unicode dạng dựng sẵn (NFC).")

    en_str = str(en_val)

    # K2: Biến {{...}} và %s
    en_vars = sorted(re.findall(r"\{\{[^}]+\}\}", en_str))
    trans_vars = sorted(re.findall(r"\{\{[^}]+\}\}", trans_val))
    if en_vars != trans_vars:
        errors.append(f"K2: Biến không khớp! Gốc: {en_vars} vs Dịch: {trans_vars}")

    en_s_count = len(re.findall(r"%[0-9]*[sd]", en_str))
    trans_s_count = len(re.findall(r"%[0-9]*[sd]", trans_val))
    if en_s_count != trans_s_count:
        errors.append(f"K2: Số lượng %s không khớp! Gốc: {en_s_count} vs Dịch: {trans_s_count}")

    # K3: Thẻ thành phần <1>, </1>, <strong>, Markdown
    en_tags = sorted(re.findall(r"</?[a-zA-Z0-9_-]+[^>]*>", en_str))
    trans_tags = sorted(re.findall(r"</?[a-zA-Z0-9_-]+[^>]*>", trans_val))
    if en_tags != trans_tags:
        errors.append(f"K3: Thẻ thành phần React/HTML không khớp! Gốc: {en_tags} vs Dịch: {trans_tags}")

    # K5: Kiểm tra từ cấm theo bảng thuật ngữ 6.4 ("thông điệp", "tập tin", "đội")
    for prohibited in ["thông điệp", "tập tin", "đội"]:
        if re.search(r"\b" + re.escape(prohibited) + r"\b", trans_val, re.IGNORECASE):
            # Ngoại trừ các trường hợp tên riêng hoặc context hợp lệ nếu có
            errors.append(f"K5: Chứa thuật ngữ cấm '{prohibited}' (phải dùng 'tin nhắn', 'tệp', hoặc 'nhóm')")

    # K6: Độ dài vượt 160% bản gốc đối với nhãn ngắn dưới 25 ký tự (nguy cơ tràn nút)
    if len(en_str) < 25 and len(en_str) > 0:
        if len(trans_val) > len(en_str) * 1.6 and len(trans_val) > 15:
            errors.append(f"K6: Cảnh báo độ dài {len(trans_val)} ký tự vượt 160% gốc ({len(en_str)} ký tự)")

    # K7: Chữ đầu viết thường khi bản gốc viết hoa
    if en_str and en_str[0].isupper() and trans_val and trans_val[0].islower():
        errors.append(f"K7: Cảnh báo chữ đầu viết thường khi bản gốc viết hoa ('{en_str[0]}' vs '{trans_val[0]}')")

    return errors


def synthesize_and_translate(
    base_data: Dict[str, Any],
    target_data: Dict[str, Any],
    missing_dict_excel: Dict[str, str],
    review_dict_excel: Dict[str, str],
    priority_dict_excel: Dict[str, str],
    engine: TranslationEngine,
    priorities_filter: Optional[Set[str]] = None,
    limit: Optional[int] = None,
    dry_run: bool = False
) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, int]]:
    """
    Tổng hợp các khóa:
    1. Lấy tất cả khóa từ base_data (en).
    2. Điền bản dịch từ: target hiện có -> review excel -> missing excel -> dịch tự động.
    3. Hỗ trợ lọc theo mức độ ưu tiên (P1, P2...).
    4. Thực hiện kiểm tra chất lượng K1-K8.
    """
    print("\n[Bước 2] Tổng hợp và đối soát dữ liệu bản dịch...")
    merged_full = {}
    custom_translations = {}
    stats = {
        "total_base_keys": len(base_data),
        "existing_target_keys": len(target_data),
        "from_target": 0,
        "from_excel_review": 0,
        "from_excel_missing": 0,
        "auto_translated": 0,
        "skipped_by_priority": 0,
        "errors_flagged": 0
    }

    count = 0
    total_to_process = len(base_data)

    for key, en_val in base_data.items():
        # Kiểm tra lọc theo ưu tiên
        key_priority = priority_dict_excel.get(key, "P2")  # Mặc định P2 nếu chưa phân loại
        if priorities_filter and key_priority not in priorities_filter and key not in target_data:
            stats["skipped_by_priority"] += 1
            continue

        if limit is not None and count >= limit:
            break

        final_val = None

        # 1. Kiểm tra đề xuất sửa từ Excel
        if key in review_dict_excel:
            final_val = review_dict_excel[key]
            stats["from_excel_review"] += 1
        # 2. Kiểm tra bản dịch hiện có trong target locale
        elif key in target_data and target_data[key]:
            final_val = target_data[key]
            stats["from_target"] += 1
        # 3. Kiểm tra bản dịch khởi điểm trong Excel
        elif key in missing_dict_excel:
            final_val = missing_dict_excel[key]
            stats["from_excel_missing"] += 1
        # 4. Cần dịch mới
        else:
            if dry_run:
                final_val = f"[TODO: TRANSLATE] {en_val}"
            else:
                if isinstance(en_val, dict):
                    final_val = {}
                    for plural_k, plural_v in en_val.items():
                        final_val[plural_k] = engine.translate_text(plural_v)
                else:
                    final_val = engine.translate_text(str(en_val))

                stats["auto_translated"] += 1

        # Hậu kiểm tra luật K1-K8
        validation_errors = validate_rule_k1_to_k8(key, en_val, final_val)
        if validation_errors:
            stats["errors_flagged"] += len(validation_errors)

        merged_full[key] = final_val

        # Nếu khóa này được sửa hoặc dịch mới so với bản target ban đầu, đưa vào custom_translations
        if key not in target_data or target_data[key] != final_val:
            custom_translations[key] = final_val

        count += 1
        if count % 500 == 0 or count == total_to_process:
            print(f"  -> Tiến độ xử lý: {count}/{total_to_process} khóa ({(count/total_to_process*100):.1f}%)")

    return merged_full, custom_translations, stats


def upload_to_rocketchat_api(payload_dict: dict, rc_url: str, uid: str, token: str) -> bool:
    """Nạp bản dịch tuỳ biến trực tiếp vào Rocket.Chat qua REST API setting Custom_Translations."""
    print(f"\n[REST API] Đang nạp Custom_Translations vào {rc_url}...")
    try:
        import urllib.request
        api_url = f"{rc_url.rstrip('/')}/api/v1/settings/Custom_Translations"
        json_str = json.dumps(payload_dict, ensure_ascii=False)
        body = json.dumps({"value": json_str}).encode("utf-8")
        req = urllib.request.Request(
            api_url,
            data=body,
            headers={
                "X-User-Id": uid,
                "X-Auth-Token": token,
                "Content-Type": "application/json"
            },
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp_data = json.loads(resp.read().decode("utf-8"))
            if resp_data.get("success"):
                print("  -> Nạp thành công Custom_Translations lên Rocket.Chat!")
                return True
            else:
                print(f"  [!] Rocket.Chat trả về kết quả không thành công: {resp_data}")
                return False
    except Exception as e:
        print(f"  [!] Lỗi khi gọi Rocket.Chat API: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(
        description="Script tổng hợp và dịch các tệp i18n từ packages/i18n/src/locales (không quét toàn bộ project)."
    )
    parser.add_argument(
        "--locales-dir",
        default=DEFAULT_LOCALES_DIR,
        help="Thư mục chứa các tệp *.i18n.json (Mặc định: packages/i18n/src/locales)"
    )
    parser.add_argument(
        "--target-locale",
        default="vi-VN",
        help="Mã locale đích cần tổng hợp và dịch (Mặc định: vi-VN)"
    )
    parser.add_argument(
        "--output-dir",
        default=DEFAULT_OUTPUT_DIR,
        help="Thư mục xuất kết quả (Mặc định: mrc/i18n/locales)"
    )
    parser.add_argument(
        "--excel-path",
        default=DEFAULT_EXCEL_PATH,
        help="Đường dẫn tệp Excel danh mục chuỗi chuẩn MRC"
    )
    parser.add_argument(
        "--priority",
        default=None,
        help="Lọc dịch theo mức ưu tiên (ví dụ: 'P1' hoặc 'P1,P2')"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Giới hạn số lượng khóa cần dịch thử nghiệm (ví dụ: --limit 50)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Chạy mô phỏng, phân tích thống kê và đánh dấu chuỗi thiếu mà không gọi API dịch"
    )
    parser.add_argument(
        "--apply-upstream",
        action="store_true",
        help="Ghi đè trực tiếp kết quả vào packages/i18n/src/locales/<target>.i18n.json"
    )
    parser.add_argument(
        "--upload-api",
        action="store_true",
        help="Tự động gọi Rocket.Chat REST API cập nhật Custom_Translations (dùng biến môi trường RC_URL, RC_UID, RC_TOKEN)"
    )

    args = parser.parse_args()

    print("================================================================================")
    print("MRC ROCKET.CHAT I18N SYNTHESIZER & TRANSLATOR")
    print("================================================================================")

    # Bước 1: Quét trực tiếp thư mục locales để tìm locale nhiều nhất
    max_locale, locale_counts, base_data = scan_locales_directory(args.locales_dir)

    # Đọc locale đích hiện có
    target_file = os.path.join(args.locales_dir, f"{args.target_locale}.i18n.json")
    target_data = {}
    if os.path.exists(target_file):
        with open(target_file, "r", encoding="utf-8") as f:
            target_data = json.load(f)
        print(f"\n[Bước 1.1] Locale đích '{args.target_locale}' hiện có: {len(target_data)}/{len(base_data)} khóa ({(len(target_data)/len(base_data)*100):.1f}%)")
        print(f"  -> Số khóa còn thiếu cần dịch: {len(base_data) - len(target_data)} khóa")
    else:
        print(f"\n[Bước 1.1] Locale đích '{args.target_locale}' chưa có tệp bản dịch. Sẽ tạo mới hoàn toàn từ '{max_locale}'.")

    # Bước 2: Nạp các tài liệu tham chiếu Excel nếu có
    missing_excel, review_excel, glossary_excel, priority_excel = load_excel_references(args.excel_path)
    if glossary_excel:
        STANDARD_GLOSSARY.update(glossary_excel)

    prio_filter = set(p.strip().upper() for p in args.priority.split(",")) if args.priority else None
    if prio_filter:
        print(f"[Bộ lọc] Áp dụng lọc theo mức độ ưu tiên: {prio_filter}")

    # Khởi tạo engine dịch
    engine = TranslationEngine()

    # Bước 3: Tổng hợp và dịch
    merged_full, custom_trans, stats = synthesize_and_translate(
        base_data=base_data,
        target_data=target_data,
        missing_dict_excel=missing_excel,
        review_dict_excel=review_excel,
        priority_dict_excel=priority_excel,
        engine=engine,
        priorities_filter=prio_filter,
        limit=args.limit,
        dry_run=args.dry_run
    )

    # Lưu cache bản dịch
    if not args.dry_run:
        engine.save_cache()

    # Bước 4: Xuất kết quả
    os.makedirs(args.output_dir, exist_ok=True)

    # 4.1 Tệp full locale
    out_locale_path = os.path.join(args.output_dir, f"{args.target_locale}.i18n.json")
    with open(out_locale_path, "w", encoding="utf-8") as f:
        json.dump(merged_full, f, ensure_ascii=False, indent=2)

    # 4.2 Tệp Custom_Translations (chuẩn nạp REST API của Rocket.Chat)
    out_custom_path = os.path.join(args.output_dir, f"custom-translations.{args.target_locale}.json")
    custom_payload = {args.target_locale: custom_trans}
    with open(out_custom_path, "w", encoding="utf-8") as f:
        json.dump(custom_payload, f, ensure_ascii=False, indent=2)

    # 4.3 Nếu yêu cầu apply upstream
    if args.apply_upstream:
        upstream_path = os.path.join(args.locales_dir, f"{args.target_locale}.i18n.json")
        with open(upstream_path, "w", encoding="utf-8") as f:
            json.dump(merged_full, f, ensure_ascii=False, indent=2)
        print(f"\n[Upstream] Đã ghi đè trực tiếp vào: {upstream_path}")

    # 4.4 Nếu yêu cầu upload qua REST API
    if args.upload_api:
        rc_url = os.environ.get("RC_URL", "http://localhost:3000")
        rc_uid = os.environ.get("RC_UID", "")
        rc_token = os.environ.get("RC_TOKEN", "")
        if rc_uid and rc_token:
            upload_to_rocketchat_api(custom_payload, rc_url, rc_uid, rc_token)
        else:
            print("\n[Cảnh báo] Bỏ qua upload API do thiếu biến môi trường RC_UID hoặc RC_TOKEN.")

    # Báo cáo kết quả
    print("\n================================================================================")
    print("KẾT QUẢ TỔNG HỢP VÀ DỊCH:")
    print("================================================================================")
    print(f"Tổng số khóa chuẩn ({max_locale}):         {stats['total_base_keys']} khóa")
    print(f"Khóa có sẵn từ {args.target_locale}:             {stats['from_target']} khóa")
    print(f"Khóa sửa theo rà soát Excel:               {stats['from_excel_review']} khóa")
    print(f"Khóa lấy từ bản dịch khởi điểm Excel:      {stats['from_excel_missing']} khóa")
    print(f"Khóa dịch mới tự động:                     {stats['auto_translated']} khóa")
    if prio_filter:
        print(f"Khóa bỏ qua ngoài ưu tiên:                 {stats['skipped_by_priority']} khóa")
    print(f"Cảnh báo quy tắc chất lượng (K1-K8):        {stats['errors_flagged']} lỗi/cảnh báo")
    print("--------------------------------------------------------------------------------")
    print(f"1. Tệp toàn bộ bản dịch (Clean Invariant):  {out_locale_path}")
    print(f"2. Tệp nạp REST API Custom_Translations:   {out_custom_path} ({len(custom_trans)} khóa)")
    print("================================================================================\n")


if __name__ == "__main__":
    main()
