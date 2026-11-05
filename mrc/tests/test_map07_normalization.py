import sys
from pathlib import Path
import pytest

MRC_CHAT_DIR = Path(__file__).resolve().parent.parent.parent
SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "integration" / "scripts"
for p in [str(MRC_CHAT_DIR), str(SCRIPTS_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)

try:
    from mrc.integration.scripts.setup_sso import normalize_username, assert_canonical_username
except ImportError:
    from setup_sso import normalize_username, assert_canonical_username

class TestMap07Normalization:
    """Test suite covering MAP-07 normalization specification."""

    @pytest.mark.parametrize("raw_input, expected", [
        ("Đặng Thu Phương", "dang.thu.phuong"),
        ("ngoc.tb", "ngoc.tb"),
        ("Trần Bảo Ngọc", "tran.bao.ngoc"),
        ("Lê Hoàng Long", "le.hoang.long"),
        ("nguyen_van_a", "nguyen_van_a"),
        ("vu-thi-mai", "vu-thi-mai"),
        ("  Đỗ   Thị..Hà  ", "do.thi.ha"),
        ("a" * 50, "a" * 32),
        ("Nguyễn Đắc Điền", "nguyen.dac.dien"),
    ])
    def test_spec_canonical_cases(self, raw_input: str, expected: str):
        """Verify the 9 canonical test cases mandated by specification."""
        assert normalize_username(raw_input) == expected

    @pytest.mark.parametrize("raw_input, expected", [
        ("DANG.THU.PHUONG", "dang.thu.phuong"),
        ("NGUYỄN VĂN A", "nguyen.van.a"),
        ("TRẦN BẢO NGỌC", "tran.bao.ngoc"),
    ])
    def test_uppercase_conversion(self, raw_input: str, expected: str):
        """Verify uppercase letters are cleanly converted to lowercase."""
        assert normalize_username(raw_input) == expected

    @pytest.mark.parametrize("raw_input, expected", [
        ("nguyễn...thị---hải___yến", "nguyen.thi---hai___yen"),
        ("...user...name...", "user.name"),
        ("---user---name---", "user---name"),
        ("___user___name___", "user___name"),
        ("...user-name_test...", "user-name_test"),
    ])
    def test_consecutive_punctuation_and_boundary_stripping(self, raw_input: str, expected: str):
        """Verify consecutive dots collapse to single dot and boundary punctuation is trimmed."""
        assert normalize_username(raw_input) == expected

    @pytest.mark.parametrize("raw_input, expected", [
        ("Đinh Đức Đạt", "dinh.duc.dat"),
        ("Vũ Thị Tuyết Mai", "vu.thi.tuyet.mai"),
        ("Phạm Quốc Cường", "pham.quoc.cuong"),
        ("Bùi Thế Hiển", "bui.the.hien"),
        ("Hồ Viết Dũng", "ho.viet.dung"),
    ])
    def test_vietnamese_diacritics_and_special_d(self, raw_input: str, expected: str):
        """Verify Đ/đ and complex diacritics are properly decomposed and stripped."""
        assert normalize_username(raw_input) == expected

    @pytest.mark.parametrize("raw_input, expected", [
        ("user!@#$%^&*()+=~`{}[]|:;'<>,?/name", "user.name"),
        ("john.doe@miraclegroup.vn", "john.doe.miraclegroup.vn"),
        ("user_name-123.test", "user_name-123.test"),
    ])
    def test_special_characters_replaced_with_dot(self, raw_input: str, expected: str):
        """Verify non-alphanumeric characters are mapped to dots without invalid sequences."""
        assert normalize_username(raw_input) == expected

    @pytest.mark.parametrize("raw_input, expected, expected_len", [
        ("tran.nguyen.hoang.anh.thu.thao.hien", "tran.nguyen.hoang.anh.thu.thao.hien"[:32], 32),
        ("Trần Lê Minh Hoàng Vũ Quốc Việt Cường", "tran.le.minh.hoang.vu.quoc.viet", 31),
        ("Nguyễn Thị Bích Huyền Trang Mai Phương", "nguyen.thi.bich.huyen.trang.mai", 31),
        ("a" * 31 + ".bbbb", "a" * 31, 31),
        ("a" * 31 + "-bbbb", "a" * 31, 31),
        ("a" * 31 + "_bbbb", "a" * 31, 31),
        ("a" * 29 + "---bbbb", "a" * 29, 29),
        ("a" * 28 + "._-_bbbb", "a" * 28, 28),
        ("a" * 32, "a" * 32, 32),
        ("a" * 33, "a" * 32, 32),
        ("a" * 50, "a" * 32, 32),
    ])
    def test_max_length_truncation_boundary(self, raw_input: str, expected: str, expected_len: int):
        """Verify 32-char truncation cleanly strips trailing boundary punctuation and ensures canonical form."""
        normalized = normalize_username(raw_input)
        assert normalized == expected
        assert len(normalized) == expected_len
        assert_canonical_username(normalized)

    @pytest.mark.parametrize("empty_input", [
        "",
        "   ",
        ".....",
        "-----",
        "_____",
        " . _ - . - _ ",
    ])
    def test_empty_or_punctuation_only_input(self, empty_input: str):
        """Verify empty or pure-punctuation inputs return empty string."""
        assert normalize_username(empty_input) == ""


class TestAssertCanonicalUsername:
    """Test suite covering assert_canonical_username validation."""

    @pytest.mark.parametrize("canonical_username", [
        "dang.thu.phuong",
        "ngoc.tb",
        "tran.bao.ngoc",
        "le.hoang.long",
        "nguyen_van_a",
        "vu-thi-mai",
        "do.thi.ha",
        "a" * 32,
        "user.123_456-789",
        "tran.le.minh.hoang.vu.quoc.viet",
    ])
    def test_valid_canonical_usernames_pass(self, canonical_username: str):
        """Verify assert_canonical_username succeeds on valid canonical usernames."""
        # Should execute without raising ValueError
        assert_canonical_username(canonical_username)

    @pytest.mark.parametrize("invalid_username, reason", [
        ("Đặng Thu Phương", "contains whitespace and accented characters"),
        ("Dang.Thu.Phuong", "contains uppercase characters"),
        ("dang..thu.phuong", "contains consecutive dots"),
        (".dang.thu.phuong", "starts with leading dot"),
        ("dang.thu.phuong.", "ends with trailing dot"),
        ("_dang.thu.phuong", "starts with leading underscore"),
        ("dang.thu.phuong_", "ends with trailing underscore"),
        ("-dang.thu.phuong", "starts with leading hyphen"),
        ("dang.thu.phuong-", "ends with trailing hyphen"),
        ("dang@thu", "contains invalid character @"),
        ("dang#thu", "contains invalid character #"),
        ("a" * 33, "exceeds 32 character limit"),
        ("", "empty string"),
    ])
    def test_invalid_usernames_raise_canonical_error(self, invalid_username: str, reason: str):
        """Verify assert_canonical_username raises ValueError with 'username_not_canonical'."""
        with pytest.raises(ValueError) as exc_info:
            assert_canonical_username(invalid_username)
        assert "username_not_canonical" in str(exc_info.value), f"Failed for {reason}: {exc_info.value}"
