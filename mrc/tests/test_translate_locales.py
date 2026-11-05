#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import pytest
import os
import sys

# Thêm đường dẫn scripts vào sys.path
SCRIPT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "i18n", "scripts"))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from translate_locales import (
    VariableProtector,
    TranslationEngine,
    scan_locales_directory,
    validate_rule_k1_to_k8,
    DEFAULT_LOCALES_DIR
)


def test_scan_locales_finds_max_locale():
    """Kiểm tra việc quét trực tiếp thư mục locales xác định chính xác locale lớn nhất."""
    max_locale, locale_counts, base_data = scan_locales_directory(DEFAULT_LOCALES_DIR)
    assert max_locale == "en", "Locale có số khóa lớn nhất phải là 'en'"
    assert len(base_data) == 7518, "Locale 'en' phải có chính xác 7.518 khóa"
    assert "vi-VN" in locale_counts, "Phải tìm thấy locale vi-VN"
    assert locale_counts["vi-VN"] == 2788, "vi-VN hiện có 2.788 khóa"


def test_variable_protection():
    """Kiểm tra việc bảo vệ biến {{...}}, %s, thẻ HTML và Markdown trong quá trình dịch."""
    protector = VariableProtector()
    original = "Hello {{user}}, you have %s new items in <1>the room</1> (**urgent**)"
    masked = protector.mask(original)

    # Đảm bảo các biến đã được che chắn bằng token
    assert "{{user}}" not in masked
    assert "%s" not in masked
    assert "<1>" not in masked
    assert "**urgent**" not in masked
    assert "ZZVAR" in masked

    # Phục hồi nguyên trạng
    unmasked = protector.unmask(masked)
    assert unmasked == original


def test_glossary_and_rule_k5_replacement():
    """Kiểm tra tuân thủ bảng thuật ngữ chuẩn và thay thế từ cấm (Luật K5)."""
    engine = TranslationEngine()

    sample_bad = "Gửi thông điệp tới Đội kỹ thuật kèm tập tin đính kèm và hủy bỏ nếu lỗi"
    processed = engine.post_process(sample_bad, source_text=sample_bad)

    assert "thông điệp" not in processed.lower()
    assert "tin nhắn" in processed.lower()
    assert "đội" not in processed.lower()
    assert "nhóm" in processed.lower()
    assert "tập tin" not in processed.lower()
    assert "tệp" in processed.lower()
    assert "hủy bỏ" not in processed.lower()
    assert "Huỷ" in processed


def test_validation_rule_k1_to_k8():
    """Kiểm tra các luật K2, K3, K4, K5."""
    # K2: Thiếu biến
    errs = validate_rule_k1_to_k8("test_key", "Hello {{name}}", "Xin chào")
    assert any("K2" in e for e in errs)

    # K3: Thiếu thẻ React
    errs_tag = validate_rule_k1_to_k8("tag_key", "<1>Click</1>", "Bấm")
    assert any("K3" in e for e in errs_tag)

    # K5: Chứa từ cấm
    errs_k5 = validate_rule_k1_to_k8("k5_key", "Send message", "Gửi thông điệp")
    assert any("K5" in e for e in errs_k5)

    # Trường hợp hợp lệ
    errs_ok = validate_rule_k1_to_k8("ok_key", "Hello {{name}}", "Xin chào {{name}}")
    assert len(errs_ok) == 0


def test_validator_script_and_k6_k7_k8():
    """Kiểm tra validator độc lập và các luật K6, K7, K8."""
    from validate_i18n import validate_entry

    # K7: Viết hoa chữ đầu
    _, warns_k7 = validate_entry("Save", "Save", "lưu")
    assert any("K7" in w for w in warns_k7)

    # K6: Độ dài vượt 160% với nhãn ngắn
    _, warns_k6 = validate_entry("Short", "Add", "Thêm biểu tượng cảm xúc mới vào hệ thống")
    assert any("K6" in w for w in warns_k6)

    # Hợp lệ
    errs, warns = validate_entry("Save", "Save", "Lưu")
    assert len(errs) == 0
    assert len(warns) == 0
