#!/usr/bin/env python3
"""
Infrastructure Remediation Verification Tests (Milestone 1 Iteration 2).
Validates that:
1. backup_mongo.sh performs a full instance dump with --oplog (no --db), uses atomic temp files, cleanup trap, and gzip -t.
2. init-replica.sh exits 1 on election failure, checks PRIMARY status after RS_STATUS=1, and parameterizes retries.
3. Caddyfile.snippet permits camera and microphone for self, and uses case-insensitive regex for /admin.
4. README.md specifies explicit --env-file mrc/deploy/.env in quickstart compose commands.
5. All operational scripts maintain pure LF line endings.
"""

import re
from pathlib import Path

MRC_DIR = Path(__file__).resolve().parent.parent

def test_backup_mongo_oplog_and_atomic_gzip():
    backup_script = MRC_DIR / "infrastructure" / "mongodb" / "backup_mongo.sh"
    assert backup_script.is_file(), f"Missing {backup_script}"
    content = backup_script.read_text(encoding="utf-8")

    # Verify --oplog present and --db absent in mongodump call
    mongodump_match = re.search(r"^[ \t]*mongodump\s+(?:\\?\r?\n)?((?:[^\r\n#]+\\\r?\n)*[^\r\n#]+)", content, re.MULTILINE)
    assert mongodump_match, "mongodump command not found in backup_mongo.sh"
    cmd_args = mongodump_match.group(1)
    assert "--oplog" in cmd_args, "mongodump must include --oplog for point-in-time snapshot"
    assert "--db" not in cmd_args, "mongodump must not include --db when using --oplog"

    # Verify gzip -t byte-level integrity verification
    assert "gzip -t" in content, "backup_mongo.sh must verify archive using gzip -t"

    # Verify cleanup trap
    assert re.search(r"trap\s+['\"a-zA-Z0-9_\s]+\s+(?:EXIT|ERR|INT|TERM)", content), "cleanup trap missing in backup_mongo.sh"

    # Verify atomic temp file usage
    assert "TEMP_BACKUP_PATH" in content, "backup_mongo.sh must use temporary file before atomic rename"
    assert "mv -f" in content, "backup_mongo.sh must atomically mv temp file to final destination"

def test_init_replica_no_masked_timeout_and_race_prevention():
    init_script = MRC_DIR / "infrastructure" / "mongodb" / "init-replica.sh"
    assert init_script.is_file(), f"Missing {init_script}"
    content = init_script.read_text(encoding="utf-8")

    # Verify no break on primary election timeout
    assert not re.search(r'primary_retries.*?\n.*?if\s+\[\s*"\${primary_retries}"\s+-ge\s+.*?break', content, re.DOTALL), (
        "init-replica.sh must not use 'break' on primary election timeout"
    )

    # Verify exit 1 is triggered when retries exhausted
    assert "exit 1" in content, "init-replica.sh must exit 1 on timeout"

    # Verify RS_STATUS=1 does not immediately exit 0 without verifying PRIMARY
    assert not re.search(r'if\s+\[\s*"\${RS_STATUS}"\s*=\s*"1"\s*\];\s*then\s*[^#\n]*\n\s*exit 0', content), (
        "init-replica.sh must verify PRIMARY even when RS_STATUS=1"
    )

    # Verify parameterized retries
    assert "MAX_RETRIES=" in content and "MAX_PRIMARY_RETRIES=" in content, (
        "init-replica.sh must parameterize MAX_RETRIES and MAX_PRIMARY_RETRIES"
    )

def test_caddyfile_permissions_policy_and_admin_matcher():
    caddyfile = MRC_DIR / "deploy" / "caddy" / "Caddyfile.snippet"
    assert caddyfile.is_file(), f"Missing {caddyfile}"
    content = caddyfile.read_text(encoding="utf-8")

    # Permissions-Policy check
    perm_match = re.search(r'Permissions-Policy\s+"([^"]+)"', content)
    assert perm_match, "Permissions-Policy header missing"
    policy = perm_match.group(1)
    assert "camera=(self)" in policy, "Permissions-Policy must allow camera for (self)"
    assert "microphone=(self)" in policy, "Permissions-Policy must allow microphone for (self)"
    assert "camera=()" not in policy, "Permissions-Policy must not block camera completely"
    assert "microphone=()" not in policy, "Permissions-Policy must not block microphone completely"

    # Case-insensitive admin route matcher
    assert re.search(r'@admin_restricted\s*\{[^}]*path_regexp\s+(?:\w+\s+)?\(\?i\)\^/admin', content), (
        "@admin_restricted must use case-insensitive regex path_regexp (?i)^/admin"
    )
    assert not re.search(r'@admin_restricted\s*\{\s*path\s+/admin\*', content), (
        "@admin_restricted must not use case-sensitive 'path /admin*'"
    )

def test_readme_compose_commands():
    readme = MRC_DIR / "README.md"
    assert readme.is_file(), f"Missing {readme}"
    content = readme.read_text(encoding="utf-8")

    # Step 2 check
    assert "docker compose --env-file mrc/deploy/.env -f mrc/deploy/compose.yaml up -d" in content, (
        "README.md Step 2 must use --env-file mrc/deploy/.env"
    )

    # Step 3 check
    assert "docker compose --env-file mrc/deploy/.env -f mrc/deploy/compose.yaml ps" in content, (
        "README.md Step 3 ps command must use --env-file mrc/deploy/.env"
    )

def test_compose_requires_secrets_without_defaults():
    compose = MRC_DIR / "deploy" / "compose.yaml"
    content = compose.read_text(encoding="utf-8")

    assert content.count("${ADM_LOCAL_PASSWORD:?") == 2, "admin password must be mandatory in compose"
    assert "${KC_CLIENT_SECRET:?" in content, "client secret must be mandatory in compose"
    assert not re.search(r"(PASSWORD|SECRET):-[^$]", content), "compose must not ship a default secret"

def test_pure_lf_line_endings():
    for rel_path in [
        "infrastructure/mongodb/backup_mongo.sh",
        "infrastructure/mongodb/init-replica.sh",
        "deploy/caddy/Caddyfile.snippet",
        "README.md",
    ]:
        full_path = MRC_DIR / rel_path
        data = full_path.read_bytes()
        assert b"\r\n" not in data, f"CRLF line ending detected in {rel_path}"
