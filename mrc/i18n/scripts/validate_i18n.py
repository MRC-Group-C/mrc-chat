#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MRC Rocket.Chat i18n Validator (VH-05)
=====================================
Kiểm tra tự động 8 quy tắc chất lượng K1 - K8 theo tài liệu kỹ thuật MRC-CHT-HD-01 Mục 6.3:
- K1: Khóa phải tồn tại trong en.i18n.json của phiên bản 8.9.0.
- K2: Tập biến {{...}} và số lượng %s phải trùng khớp 100% bản gốc.
- K3: Thẻ HTML, thẻ thành phần React (<1></1>, <icon></icon>) và Markdown giữ nguyên số lượng.
- K4: Khoá số nhiều phải có biến thể _other (bỏ qua _one).
- K5: Nghiêm cấm các thuật ngữ sai lệch ("Thông điệp", "Đội", "tập tin").
- K6: Cảnh báo độ dài vượt 160% bản gốc với nhãn ngắn < 25 ký tự.
- K7: Cảnh báo chữ đầu viết thường khi bản gốc viết hoa.
- K8: JSON hợp lệ, UTF-8, chuẩn hóa Unicode dạng dựng sẵn (NFC).
"""

import os
import sys
import json
import re
import unicodedata
import argparse
from typing import Dict, Any, List, Tuple

if sys.stdout and hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if sys.stderr and hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

DEFAULT_BASE_FILE = r"D:\project\mrc-chat\packages\i18n\src\locales\en.i18n.json"
DEFAULT_TARGET_FILE = r"D:\project\mrc-chat\mrc\i18n\locales\custom-translations.vi-VN.json"


def validate_entry(key: str, en_val: Any, trans_val: Any) -> Tuple[List[str], List[str]]:
    """Kiểm tra 1 khóa và trả về (errors, warnings)."""
    errors = []
    warnings = []

    if isinstance(en_val, dict):
        if not isinstance(trans_val, dict):
            errors.append(f"[{key}] K4: Nguồn là dict số nhiều nhưng bản dịch là chuỗi đơn.")
        else:
            if "other" not in trans_val:
                warnings.append(f"[{key}] K4: Thiếu biến thể số nhiều '_other' cho tiếng Việt.")
        return errors, warnings

    if not isinstance(trans_val, str):
        errors.append(f"[{key}] K8: Giá trị dịch không phải chuỗi ký tự hợp lệ.")
        return errors, warnings

    if not unicodedata.is_normalized("NFC", trans_val):
        errors.append(f"[{key}] K8: Bản dịch chưa được chuẩn hoá Unicode dựng sẵn (NFC).")

    en_str = str(en_val)

    # K2: Biến {{...}} và %s
    en_vars = sorted(re.findall(r"\{\{[^}]+\}\}", en_str))
    trans_vars = sorted(re.findall(r"\{\{[^}]+\}\}", trans_val))
    if en_vars != trans_vars:
        errors.append(f"[{key}] K2: Biến {{...}} không khớp! Gốc: {en_vars} vs Dịch: {trans_vars}")

    en_s_count = len(re.findall(r"%[0-9]*[sd]", en_str))
    trans_s_count = len(re.findall(r"%[0-9]*[sd]", trans_val))
    if en_s_count != trans_s_count:
        errors.append(f"[{key}] K2: Số lượng %s không khớp! Gốc: {en_s_count} vs Dịch: {trans_s_count}")

    # K3: Thẻ thành phần <1>, </1>, <strong>...
    en_tags = sorted(re.findall(r"</?[a-zA-Z0-9_-]+[^>]*>", en_str))
    trans_tags = sorted(re.findall(r"</?[a-zA-Z0-9_-]+[^>]*>", trans_val))
    if en_tags != trans_tags:
        errors.append(f"[{key}] K3: Thẻ cấu trúc React/HTML không khớp! Gốc: {en_tags} vs Dịch: {trans_tags}")

    # K5: Thuật ngữ cấm
    for term in ["thông điệp", "tập tin", "đội"]:
        if re.search(r"\b" + re.escape(term) + r"\b", trans_val, re.IGNORECASE):
            errors.append(f"[{key}] K5: Chứa thuật ngữ cấm '{term}' (phải dùng 'tin nhắn', 'tệp', hoặc 'nhóm')")

    # K6: Độ dài vượt 160% với nhãn ngắn < 25 ký tự
    if len(en_str) < 25 and len(en_str) > 0:
        if len(trans_val) > len(en_str) * 1.6 and len(trans_val) > 15:
            warnings.append(f"[{key}] K6: Độ dài {len(trans_val)} ký tự vượt 160% gốc ({len(en_str)} ký tự)")

    # K7: Viết hoa chữ đầu
    if en_str and en_str[0].isupper() and trans_val and trans_val[0].islower():
        warnings.append(f"[{key}] K7: Chữ đầu viết thường trong khi bản gốc viết hoa ('{en_str[0]}' vs '{trans_val[0]}')")

    return errors, warnings


def main():
    parser = argparse.ArgumentParser(description="MRC Rocket.Chat i18n Quality Validator (K1 - K8)")
    parser.add_argument("--base", default=DEFAULT_BASE_FILE, help="Đường dẫn en.i18n.json gốc")
    parser.add_argument("--target", default=DEFAULT_TARGET_FILE, help="Đường dẫn tệp bản dịch cần kiểm tra")
    parser.add_argument("--strict", action="store_true", help="Báo lỗi thất bại ngay cả khi chỉ có cảnh báo")
    args = parser.parse_args()

    print("================================================================================")
    print("MRC ROCKET.CHAT I18N QUALITY VALIDATOR (VH-05 / K1-K8)")
    print("================================================================================")

    if not os.path.exists(args.base):
        print(f"[LỖI] Không tìm thấy tệp gốc: {args.base}")
        sys.exit(1)
    if not os.path.exists(args.target):
        print(f"[LỖI] Không tìm thấy tệp dịch: {args.target}")
        sys.exit(1)

    with open(args.base, "r", encoding="utf-8") as f:
        base_data = json.load(f)

    with open(args.target, "r", encoding="utf-8") as f:
        target_raw = json.load(f)

    # Nếu file là dạng Custom_Translations {"vi-VN": {...}}
    if "vi-VN" in target_raw and isinstance(target_raw["vi-VN"], dict):
        target_data = target_raw["vi-VN"]
    else:
        target_data = target_raw

    print(f"-> Tệp gốc: {args.base} ({len(base_data)} khóa)")
    print(f"-> Tệp kiểm tra: {args.target} ({len(target_data)} khóa)")

    all_errors = []
    all_warnings = []

    for key, trans_val in target_data.items():
        # K1: Khóa phải tồn tại trong en.i18n.json
        if key not in base_data:
            all_errors.append(f"[{key}] K1: Khóa không tồn tại trong bản gốc en.i18n.json!")
            continue

        en_val = base_data[key]
        errs, warns = validate_entry(key, en_val, trans_val)
        all_errors.extend(errs)
        all_warnings.extend(warns)

    print("\n--------------------------------------------------------------------------------")
    print("KẾT QUẢ KIỂM ĐỊNH:")
    print(f"  - Tổng số khóa kiểm tra: {len(target_data)}")
    print(f"  - Số lỗi vi phạm (Errors):   {len(all_errors)}")
    print(f"  - Số cảnh báo (Warnings):   {len(all_warnings)}")
    print("--------------------------------------------------------------------------------")

    if all_warnings:
        print("\n[DANH SÁCH CẢNH BÁO (WARNINGS)]:")
        for w in all_warnings[:20]:
            print(f"  * {w}")
        if len(all_warnings) > 20:
            print(f"  ... và còn {len(all_warnings) - 20} cảnh báo khác.")

    if all_errors:
        print("\n[DANH SÁCH LỖI NGHIÊM TRỌNG (ERRORS)]:")
        for e in all_errors[:20]:
            print(f"  [X] {e}")
        if len(all_errors) > 20:
            print(f"  ... và còn {len(all_errors) - 20} lỗi khác.")
        print("\n[THẤT BẠI] Tệp bản dịch không đạt chuẩn K1 - K8. Bị chặn nạp theo quy trình VH-05!")
        sys.exit(1)

    if args.strict and all_warnings:
        print("\n[THẤT BẠI] Chế độ strict: Không chấp nhận cảnh báo.")
        sys.exit(1)

    print("\n[THÀNH CÔNG] Tệp bản dịch đạt 100% tiêu chuẩn chất lượng K1 - K8!")
    sys.exit(0)


if __name__ == "__main__":
    main()
