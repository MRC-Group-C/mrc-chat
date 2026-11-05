import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


MODULE_PATH = Path(__file__).parents[1] / "gen_vi.py"
SPEC = importlib.util.spec_from_file_location("gen_vi", MODULE_PATH)
gen_vi = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
import sys
sys.modules[SPEC.name] = gen_vi
SPEC.loader.exec_module(gen_vi)


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.policy = {
            "banned_terms": {"Thông điệp": "Tin nhắn"},
            "banned_term_allowlist": {},
            "banned_exceptions": {"Đội": ["đội ngũ"]},
            "length_ratio": 2,
            "length_min_source": 4,
            "length_min_target": 16,
        }

    def rules(self, source, target):
        return [item.rule for item in gen_vi.validate(source, target, self.policy) if item.severity == "error"]

    def test_k1_unknown_key(self):
        self.assertEqual(self.rules({"Known": "Known"}, {"Unknown": "Không rõ"}), ["K1"])

    def test_k2_interpolation_and_printf(self):
        source = {"Key": "Hello {{name}}, %s has %d messages"}
        self.assertIn("K2", self.rules(source, {"Key": "Xin chào {{user}}, %s có tin nhắn"}))
        self.assertEqual(self.rules(source, {"Key": "Xin chào {{name}}, %s có %d tin nhắn"}), [])

    def test_k3_html_and_react_tags(self):
        source = {"Key": "Open <strong><1>this</1></strong>"}
        self.assertIn("K3", self.rules(source, {"Key": "Mở <strong>nội dung</strong>"}))
        self.assertEqual(self.rules(source, {"Key": "Mở <strong><1>nội dung</1></strong>"}), [])

    def test_k3_nesting_order_mismatch(self):
        source = {"Key": "<b><i>text</i></b>"}
        self.assertIn("K3", self.rules(source, {"Key": "<i><b>văn bản</b></i>"}))

    def test_k4_vietnamese_needs_only_other(self):
        source = {"Count": {"one": "{{count}} file", "other": "{{count}} files"}}
        self.assertEqual(self.rules(source, {"Count": {"other": "{{count}} tệp"}}), [])
        self.assertIn("K4", self.rules(source, {"Count": {"one": "{{count}} tệp"}}))

    def test_k4_redundant_variants_rejected(self):
        source = {"Count": {"one": "{{count}} file", "other": "{{count}} files"}}
        self.assertIn("K4", self.rules(source, {"Count": {"one": "{{count}} tệp", "other": "{{count}} tệp"}}))

    def test_k5_banned_term(self):
        self.assertEqual(self.rules({"Message": "Message"}, {"Message": "Thông điệp"}), ["K5"])

    def test_k5_word_boundary_avoids_false_positive(self):
        self.policy["banned_terms"]["Đội"] = "Nhóm"
        source = {"Key": "Our team"}
        target_ok = {"Key": "đội ngũ của chúng tôi"}
        self.assertEqual(self.rules(source, target_ok), [])
        target_banned = {"Key": "Đội của chúng tôi"}
        self.assertIn("K5", self.rules(source, target_banned))

    def test_k8_non_nfc(self):
        decomposed = "Vie\u0302\u0323t"
        self.assertIn("K8", self.rules({"Key": "Vietnamese"}, {"Key": decomposed}))

    def test_k6_and_k7_are_warnings(self):
        findings = gen_vi.validate({"Key": "Short"}, {"Key": "một bản dịch dài hơn rất nhiều"}, self.policy)
        self.assertEqual({item.rule for item in findings}, {"K6", "K7"})
        self.assertTrue(all(item.severity == "warning" for item in findings))

    def test_k7_leading_placeholder_not_flagged(self):
        source = {"Key": "Hello {{name}}"}
        target = {"Key": "{{name}} đã tham gia"}
        findings = gen_vi.validate(source, target, self.policy)
        self.assertNotIn("K7", [f.rule for f in findings])


class JsonAndUploadTests(unittest.TestCase):
    def test_duplicate_json_keys_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "duplicate.json"
            path.write_text('{"vi-VN":{"A":"a","A":"b"}}', encoding="utf-8")
            with self.assertRaises(gen_vi.InputError):
                gen_vi.load_json_file(path)

    def test_upload_dry_run_does_not_read_credentials_or_network(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            source_path = directory / "en.json"
            translation_path = directory / "vi.json"
            source_path.write_text(json.dumps({"Hello": "Hello"}), encoding="utf-8")
            translation_path.write_text(json.dumps({"vi-VN": {"Hello": "Xin chào"}}), encoding="utf-8")
            with patch.object(gen_vi, "_request_json") as request:
                result = gen_vi.main([
                    "upload",
                    "--source", str(source_path),
                    "--translation", str(translation_path),
                    "--url", "https://chat.example.test",
                ])
            self.assertEqual(result, 0)
            request.assert_not_called()

    def test_upload_is_blocked_before_network_on_validation_error(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            source_path = directory / "en.json"
            translation_path = directory / "vi.json"
            source_path.write_text(json.dumps({"Hello": "Hello {{name}}"}), encoding="utf-8")
            translation_path.write_text(json.dumps({"vi-VN": {"Hello": "Xin chào"}}), encoding="utf-8")
            with patch.object(gen_vi, "_request_json") as request:
                result = gen_vi.main([
                    "upload",
                    "--source", str(source_path),
                    "--translation", str(translation_path),
                    "--url", "https://chat.example.test",
                    "--apply",
                ])
            self.assertEqual(result, 1)
            request.assert_not_called()

    def test_export_env_is_blocked_on_validation_error(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            source_path = directory / "en.json"
            translation_path = directory / "vi.json"
            output_env = directory / "custom.env"
            source_path.write_text(json.dumps({"Hello": "Hello {{name}}"}), encoding="utf-8")
            translation_path.write_text(json.dumps({"vi-VN": {"Hello": "Xin chào"}}), encoding="utf-8")
            result = gen_vi.main([
                "export-env",
                "--source", str(source_path),
                "--translation", str(translation_path),
                "--output", str(output_env),
            ])
            self.assertEqual(result, 1)
            self.assertFalse(output_env.exists())


if __name__ == "__main__":
    unittest.main()
