"""MAP-07: canonical chat usernames."""

import re
import unicodedata


def normalize_username(raw_name: str) -> str:
    """Normalize raw Vietnamese / foreign name to canonical username following MAP-07.

    Transformation steps:
    1. NFC normalization + trim whitespace
    2. Replace Vietnamese specific 'Đ'/'đ' with 'D'/'d'
    3. Decompose via NFD and strip 'Mn' (Nonspacing Mark / diacritics)
    4. Convert to lowercase
    5. Replace non-[0-9a-z._-] with '.'
    6. Collapse multiple consecutive dots into a single dot
    7. Strip leading and trailing '._-'
    8. Truncate to maximum 32 characters and strip trailing boundary punctuation '._-'
    """
    if raw_name is None:
        return ""
    if not isinstance(raw_name, str):
        raw_name = str(raw_name)

    # 1. NFC normalization + replace Đ/đ
    s = unicodedata.normalize("NFC", raw_name.strip()).replace("Đ", "D").replace("đ", "d")
    # 2 & 3. NFD decomposition and strip diacritical marks (Mn)
    s = unicodedata.normalize("NFD", s)
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    # 4. Lowercase
    s = s.lower()
    # 5. Replace non-[0-9a-z._-] with '.'
    s = re.sub(r"[^0-9a-z._-]+", ".", s)
    # 6 & 7. Collapse consecutive dots and strip boundary punctuation
    s = re.sub(r"\.{2,}", ".", s).strip("._-")
    # 8. Truncate to max 32 characters and strip trailing boundary punctuation
    return s[:32].rstrip("._-")


def assert_canonical_username(username: str) -> None:
    """Validate that username strictly adheres to MAP-07 canonical form.

    Raises:
        ValueError with message prefix 'username_not_canonical' if invalid.
    """
    if username is None or username == "":
        raise ValueError("username_not_canonical: username cannot be empty")
    if not isinstance(username, str):
        raise ValueError(f"username_not_canonical: expected string, got {type(username).__name__}")
    if len(username) > 32:
        raise ValueError(f"username_not_canonical: length {len(username)} exceeds maximum 32 characters")
    if not re.fullmatch(r"[0-9a-z._-]+", username):
        raise ValueError(f"username_not_canonical: '{username}' contains invalid characters")
    if username.startswith((".", "_", "-")) or username.endswith((".", "_", "-")):
        raise ValueError(f"username_not_canonical: '{username}' cannot have leading or trailing punctuation")
    if ".." in username:
        raise ValueError(f"username_not_canonical: '{username}' contains consecutive dots")
    normalized = normalize_username(username)
    if normalized != username:
        raise ValueError(f"username_not_canonical: '{username}' does not match canonical form '{normalized}'")
