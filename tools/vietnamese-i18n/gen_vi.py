#!/usr/bin/env python3
"""Generate, merge, validate, compare, and deploy Vietnamese translations.

The tool deliberately uses only the Python standard library so the same checks can
run on a developer workstation and in CI without installing project dependencies.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import unicodedata
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET
import zipfile


LOCALE = "vi-VN"
DEFAULT_SOURCE = "packages/i18n/src/locales/en.i18n.json"
DEFAULT_VI = "packages/i18n/src/locales/vi-VN.i18n.json"
INTERPOLATION_RE = re.compile(r"\{\{([^{}]+)}}")
PRINTF_RE = re.compile(r"%(?:\d+\$)?[sdif]")
TAG_RE = re.compile(r"<[^<>]+>")
FIRST_ALPHA_RE = re.compile(r"[^A-Za-zÀ-ỹĐđ]*([A-Za-zÀ-ỹĐđ])")
PLURAL_KEYS = {"zero", "one", "two", "few", "many", "other"}


class InputError(ValueError):
    pass


@dataclass(frozen=True)
class Finding:
    rule: str
    severity: str
    key: str
    message: str


def _duplicates_hook(pairs):
    result = {}
    duplicates = []
    for key, value in pairs:
        if key in result:
            duplicates.append(key)
        result[key] = value
    if duplicates:
        raise InputError(f"K8: duplicate JSON keys: {', '.join(sorted(set(duplicates)))}")
    return result


def _decode_json(raw: bytes, label: str):
    if raw.startswith(b"\xef\xbb\xbf"):
        raise InputError(f"K8: {label} must be UTF-8 without BOM")
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise InputError(f"K8: {label} is not valid UTF-8: {exc}") from exc
    try:
        data = json.loads(text, object_pairs_hook=_duplicates_hook)
    except json.JSONDecodeError as exc:
        raise InputError(f"K8: invalid JSON in {label}: {exc}") from exc
    return data, text


def load_json_file(path: str | Path):
    path = Path(path)
    return _decode_json(path.read_bytes(), str(path))[0]


def load_git_json(repo: str | Path, ref: str, path: str):
    command = ["git", "-C", str(repo), "show", f"{ref}:{path}"]
    completed = subprocess.run(command, check=False, capture_output=True)
    if completed.returncode:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise InputError(f"cannot read {path} at {ref}: {detail}")
    return _decode_json(completed.stdout, f"{ref}:{path}")[0]


def load_resource(args, *, prefix: str = "source"):
    path = getattr(args, prefix, None)
    ref = getattr(args, f"{prefix}_git_ref", None)
    git_path = getattr(args, f"{prefix}_git_path", None)
    if ref:
        return load_git_json(args.repo, ref, git_path)
    if not path:
        raise InputError(f"--{prefix.replace('_', '-')} or --{prefix.replace('_', '-')}-git-ref is required")
    return load_json_file(path)


def unwrap_locale(data, label: str):
    if not isinstance(data, dict):
        raise InputError(f"{label} must contain a JSON object")
    if LOCALE in data:
        if set(data) != {LOCALE} or not isinstance(data[LOCALE], dict):
            raise InputError(f"{label} must use exactly the wrapper {{\"{LOCALE}\": {{...}}}}")
        return data[LOCALE]
    return data


def iter_strings(value, path=""):
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, dict):
        for child_key, child_value in value.items():
            child_path = f"{path}.{child_key}" if path else child_key
            yield from iter_strings(child_value, child_path)


def is_nfc(value):
    return unicodedata.normalize("NFC", value) == value


def interpolation_tokens(value: str):
    return Counter(" ".join(match.split()) for match in INTERPOLATION_RE.findall(value))


def printf_tokens(value: str):
    return Counter(PRINTF_RE.findall(value.replace("%%", "")))


def tag_tokens(value: str):
    return Counter(" ".join(token.split()) for token in TAG_RE.findall(value))


def _comparable_source(source_value, target_variant=None):
    if isinstance(source_value, str):
        return source_value
    if isinstance(source_value, dict):
        if target_variant in source_value and isinstance(source_value[target_variant], str):
            return source_value[target_variant]
        if isinstance(source_value.get("other"), str):
            return source_value["other"]
    return None


def _first_alpha(value: str):
    match = FIRST_ALPHA_RE.match(value)
    return match.group(1) if match else None


def load_policy(path: str | Path | None):
    if not path:
        return {
            "banned_terms": {},
            "banned_term_allowlist": {},
            "length_ratio": 2.0,
            "length_min_source": 4,
            "length_min_target": 16,
        }
    policy = load_json_file(path)
    if not isinstance(policy, dict):
        raise InputError("policy must be a JSON object")
    return policy


def validate(source, translation, policy):
    findings: list[Finding] = []

    def add(rule, severity, key, message):
        findings.append(Finding(rule, severity, key, message))

    if not isinstance(source, dict) or not isinstance(translation, dict):
        raise InputError("source and translation resources must be JSON objects")

    for key, target_value in translation.items():
        if not is_nfc(key):
            add("K8", "error", key, "translation key is not Unicode NFC")
        for value_path, text in iter_strings(target_value):
            if not is_nfc(text):
                add("K8", "error", key, f"value {value_path or key} is not Unicode NFC")

        if key not in source:
            add("K1", "error", key, "translation key does not exist in the English baseline")
            continue

        source_value = source[key]
        source_plural = isinstance(source_value, dict) and bool(set(source_value) & PLURAL_KEYS)
        target_plural = isinstance(target_value, dict) and bool(set(target_value) & PLURAL_KEYS)
        if source_plural:
            if not target_plural or not isinstance(target_value.get("other"), str):
                add("K4", "error", key, "Vietnamese plural must provide a string 'other' variant")
                continue
            invalid_variants = [name for name, value in target_value.items() if name not in PLURAL_KEYS or not isinstance(value, str)]
            if invalid_variants:
                add("K4", "error", key, f"invalid Vietnamese plural variants: {', '.join(invalid_variants)}")
        elif isinstance(source_value, str):
            if not isinstance(target_value, str):
                add("K4", "error", key, "translation shape differs from the source string")
                continue
        else:
            add("K4", "error", key, "unsupported source value shape")
            continue

        target_variants = target_value.items() if isinstance(target_value, dict) else [(None, target_value)]
        for variant, target_text in target_variants:
            source_text = _comparable_source(source_value, variant)
            if source_text is None or not isinstance(target_text, str):
                continue
            label = f"{key}.{variant}" if variant else key
            if interpolation_tokens(source_text) != interpolation_tokens(target_text):
                add("K2", "error", label, "{{...}} placeholders differ from the English source")
            if printf_tokens(source_text) != printf_tokens(target_text):
                add("K2", "error", label, "%s/%d-style placeholders differ from the English source")
            if tag_tokens(source_text) != tag_tokens(target_text):
                add("K3", "error", label, "HTML/React tags differ from the English source")

            source_len = len(source_text.strip())
            target_len = len(target_text.strip())
            ratio_limit = float(policy.get("length_ratio", 2.0))
            if (
                source_len >= int(policy.get("length_min_source", 4))
                and target_len >= int(policy.get("length_min_target", 16))
                and target_len > source_len * ratio_limit
            ):
                add("K6", "warning", label, f"translation length ratio is {target_len / source_len:.2f} (limit {ratio_limit:.2f})")

            source_initial = _first_alpha(source_text)
            target_initial = _first_alpha(target_text)
            if source_initial and target_initial and source_initial.isupper() and target_initial.islower():
                add("K7", "warning", label, "source starts uppercase but translation starts lowercase")

            allowlist = policy.get("banned_term_allowlist", {})
            for banned, preferred in policy.get("banned_terms", {}).items():
                allowed_keys = set(allowlist.get(banned, []))
                if key not in allowed_keys and banned.casefold() in target_text.casefold():
                    add("K5", "error", label, f"banned term '{banned}'; use '{preferred}' when context permits")

    return sorted(findings, key=lambda item: (item.severity != "error", item.rule, item.key, item.message))


def report_payload(findings, source_count, translation_count):
    counts = Counter(item.severity for item in findings)
    by_rule = Counter(item.rule for item in findings)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "locale": LOCALE,
        "source_keys": source_count,
        "translation_keys": translation_count,
        "errors": counts["error"],
        "warnings": counts["warning"],
        "by_rule": dict(sorted(by_rule.items())),
        "findings": [asdict(item) for item in findings],
    }


def write_json(path: str | Path, data, *, compact=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(
        data,
        ensure_ascii=False,
        sort_keys=False,
        separators=(",", ":") if compact else None,
        indent=None if compact else 2,
    )
    path.write_text(text + ("" if compact else "\n"), encoding="utf-8", newline="\n")


def write_report(path: str | Path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Báo cáo K1-K8",
        "",
        f"- Locale: `{payload['locale']}`",
        f"- Khóa nguồn: {payload['source_keys']}",
        f"- Khóa kiểm tra: {payload['translation_keys']}",
        f"- Lỗi: {payload['errors']}",
        f"- Cảnh báo: {payload['warnings']}",
        "",
        "| Mức | Luật | Khóa | Nội dung |",
        "|---|---|---|---|",
    ]
    for item in payload["findings"]:
        message = item["message"].replace("|", "\\|").replace("\n", " ")
        key = item["key"].replace("|", "\\|")
        lines.append(f"| {item['severity']} | {item['rule']} | `{key}` | {message} |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def _xlsx_column_index(reference: str):
    letters = "".join(character for character in reference if character.isalpha())
    result = 0
    for character in letters.upper():
        result = result * 26 + ord(character) - ord("A") + 1
    return result - 1


def read_xlsx_sheet(path: str | Path, sheet_name: str):
    """Read cell values from one XLSX sheet using only the standard library."""
    main_ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    rel_ns = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    package_rel_ns = "http://schemas.openxmlformats.org/package/2006/relationships"
    with zipfile.ZipFile(path) as archive:
        shared = []
        if "xl/sharedStrings.xml" in archive.namelist():
            root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            for item in root.findall(f"{{{main_ns}}}si"):
                shared.append("".join(node.text or "" for node in item.iter(f"{{{main_ns}}}t")))

        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        relation_id = None
        for sheet in workbook.findall(f".//{{{main_ns}}}sheet"):
            if sheet.get("name") == sheet_name:
                relation_id = sheet.get(f"{{{rel_ns}}}id")
                break
        if not relation_id:
            raise InputError(f"worksheet not found: {sheet_name}")

        relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        target = None
        for relation in relationships.findall(f"{{{package_rel_ns}}}Relationship"):
            if relation.get("Id") == relation_id:
                target = relation.get("Target")
                break
        if not target:
            raise InputError(f"worksheet relationship not found: {sheet_name}")
        target = target.lstrip("/")
        if not target.startswith("xl/"):
            target = f"xl/{target}"
        root = ET.fromstring(archive.read(target))
        output = []
        for row in root.findall(f".//{{{main_ns}}}row"):
            values = {}
            for cell in row.findall(f"{{{main_ns}}}c"):
                index = _xlsx_column_index(cell.get("r", "A1"))
                cell_type = cell.get("t")
                if cell_type == "inlineStr":
                    value = "".join(node.text or "" for node in cell.iter(f"{{{main_ns}}}t"))
                else:
                    value_node = cell.find(f"{{{main_ns}}}v")
                    raw_value = value_node.text if value_node is not None else None
                    if cell_type == "s" and raw_value is not None:
                        value = shared[int(raw_value)]
                    elif cell_type == "b" and raw_value is not None:
                        value = raw_value == "1"
                    else:
                        value = raw_value
                values[index] = value
            if values:
                width = max(values) + 1
                output.append([values.get(index) for index in range(width)])
        return output


def _row_value(row, index):
    return row[index] if index < len(row) else None


def run_workbook_queue(args):
    missing_rows = read_xlsx_sheet(args.workbook, "Chuoi thieu")
    quality_rows = read_xlsx_sheet(args.workbook, "Ra soat chat luong")
    if not missing_rows or _row_value(missing_rows[0], 0) != "Khoá (key)":
        raise InputError("unexpected header in worksheet 'Chuoi thieu'")
    if not quality_rows or _row_value(quality_rows[0], 0) != "Khoá (key)":
        raise InputError("unexpected header in worksheet 'Ra soat chat luong'")
    priority_rows = [row for row in missing_rows[1:] if _row_value(row, 3) == args.priority]
    priority_queue = [
        {
            "key": _row_value(row, 0),
            "english": _row_value(row, 1),
            "group": _row_value(row, 2),
            "priority": _row_value(row, 3),
            "starter": _row_value(row, 4) or "",
            "proposed": _row_value(row, 5) or "",
            "review_status": "pending",
        }
        for row in priority_rows
    ]
    quality_queue = [
        {
            "key": _row_value(row, 0),
            "english": _row_value(row, 1),
            "current": _row_value(row, 2),
            "issue": _row_value(row, 3),
            "proposed": _row_value(row, 4) or "",
            "review_status": "pending",
        }
        for row in quality_rows[1:]
    ]
    result = {
        "source_workbook": Path(args.workbook).name,
        "priority": args.priority,
        "priority_count": len(priority_queue),
        "quality_review_count": len(quality_queue),
        "priority_queue": priority_queue,
        "quality_review_queue": quality_queue,
    }
    write_json(args.output, result)
    print(json.dumps({"priority": args.priority, "priority_count": len(priority_queue), "quality_review_count": len(quality_queue), "output": str(args.output)}, ensure_ascii=False))
    return 0


def validation_exit(findings, warnings_as_errors=False):
    if any(item.severity == "error" for item in findings):
        return 1
    if warnings_as_errors and any(item.severity == "warning" for item in findings):
        return 1
    return 0


def run_validate(args):
    source = load_resource(args)
    translation = unwrap_locale(load_json_file(args.translation), args.translation)
    policy = load_policy(args.policy)
    findings = validate(source, translation, policy)
    payload = report_payload(findings, len(source), len(translation))
    if args.report_json:
        write_json(args.report_json, payload)
    if args.report_md:
        write_report(args.report_md, payload)
    print(json.dumps({key: payload[key] for key in ("source_keys", "translation_keys", "errors", "warnings", "by_rule")}, ensure_ascii=False))
    return validation_exit(findings, args.warnings_as_errors)


def _empty_shape(value):
    if isinstance(value, str):
        return ""
    if isinstance(value, dict) and "other" in value:
        return {"other": ""}
    return ""


def run_extract(args):
    source = load_resource(args)
    if args.current_git_ref:
        current = load_git_json(args.repo, args.current_git_ref, args.current_git_path)
    else:
        current = unwrap_locale(load_json_file(args.current), args.current) if args.current else {}
    custom = {}
    for filename in args.custom or []:
        custom.update(unwrap_locale(load_json_file(filename), filename))
    missing = {
        key: _empty_shape(value)
        for key, value in sorted(source.items())
        if key not in current and key not in custom
    }
    output = {
        "baseline": args.source_git_ref or str(args.source),
        "locale": LOCALE,
        "missing_count": len(missing),
        "entries": [
            {"key": key, "english": source[key], "vietnamese": value, "review_status": "pending"}
            for key, value in missing.items()
        ],
    }
    write_json(args.output, output)
    print(json.dumps({"missing": len(missing), "output": str(args.output)}, ensure_ascii=False))
    return 0


def run_merge(args):
    source = load_resource(args)
    merged = {}
    for filename in args.input:
        merged.update(unwrap_locale(load_json_file(filename), filename))
    merged = dict(sorted(merged.items()))
    findings = validate(source, merged, load_policy(args.policy))
    payload = report_payload(findings, len(source), len(merged))
    if args.report_json:
        write_json(args.report_json, payload)
    if args.report_md:
        write_report(args.report_md, payload)
    exit_code = validation_exit(findings, args.warnings_as_errors)
    if exit_code:
        print(f"merge blocked: {payload['errors']} errors, {payload['warnings']} warnings", file=sys.stderr)
        return exit_code
    write_json(args.output, {LOCALE: merged}, compact=args.compact)
    print(json.dumps({"merged": len(merged), "warnings": payload["warnings"], "output": str(args.output)}, ensure_ascii=False))
    return 0


def run_upgrade(args):
    old_source = load_resource(args, prefix="old_source")
    new_source = load_resource(args, prefix="new_source")
    old_vi = load_resource(args, prefix="old_vi") if args.old_vi or args.old_vi_git_ref else {}
    new_vi = load_resource(args, prefix="new_vi") if args.new_vi or args.new_vi_git_ref else {}
    custom = unwrap_locale(load_json_file(args.translation), args.translation)
    report = {
        "old_source_keys": len(old_source),
        "new_source_keys": len(new_source),
        "removed_keys": sorted(set(old_source) - set(new_source)),
        "new_keys": sorted(set(new_source) - set(old_source)),
        "custom_keys_removed_from_source": sorted(set(custom) - set(new_source)),
        "custom_keys_now_in_upstream_vi": sorted(key for key in custom if key in new_vi and key not in old_vi),
        "custom_keys_changed_in_english": sorted(key for key in custom if key in old_source and key in new_source and old_source[key] != new_source[key]),
    }
    write_json(args.output, report)
    print(json.dumps({key: len(value) if isinstance(value, list) else value for key, value in report.items()}, ensure_ascii=False))
    return 0


def _headers(args):
    user_id = os.environ.get(args.user_id_env)
    auth_token = os.environ.get(args.auth_token_env)
    if not user_id or not auth_token:
        raise InputError(f"set {args.user_id_env} and {args.auth_token_env} for the service account")
    return {
        "Content-Type": "application/json; charset=utf-8",
        "X-User-Id": user_id,
        "X-Auth-Token": auth_token,
    }


def _request_json(method, url, headers, body=None):
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    request = Request(url, method=method, headers=headers, data=data)
    try:
        with urlopen(request, timeout=30) as response:
            raw = response.read()
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise InputError(f"HTTP {exc.code} from Rocket.Chat: {detail}") from exc
    except URLError as exc:
        raise InputError(f"cannot reach Rocket.Chat: {exc.reason}") from exc
    return _decode_json(raw, url)[0]


def run_upload(args):
    source = load_resource(args)
    wrapped = load_json_file(args.translation)
    translation = unwrap_locale(wrapped, args.translation)
    findings = validate(source, translation, load_policy(args.policy))
    if validation_exit(findings, args.warnings_as_errors):
        payload = report_payload(findings, len(source), len(translation))
        print(f"upload blocked by K1-K8: {payload['errors']} errors, {payload['warnings']} warnings", file=sys.stderr)
        return 1
    endpoint = f"{args.url.rstrip('/')}/api/v1/settings/Custom_Translations"
    if not args.apply:
        print(json.dumps({"dry_run": True, "endpoint": endpoint, "keys": len(translation)}, ensure_ascii=False))
        return 0
    headers = _headers(args)
    current = _request_json("GET", endpoint, headers)
    backup_path = Path(args.backup_dir) / f"Custom_Translations-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    write_json(backup_path, current)
    setting_value = json.dumps({LOCALE: dict(sorted(translation.items()))}, ensure_ascii=False, separators=(",", ":"))
    response = _request_json("POST", endpoint, headers, {"value": setting_value})
    if response.get("success") is not True:
        raise InputError(f"Rocket.Chat did not confirm success: {response}")
    print(json.dumps({"uploaded": len(translation), "backup": str(backup_path), "success": True}, ensure_ascii=False))
    return 0


def run_export_env(args):
    translation = unwrap_locale(load_json_file(args.translation), args.translation)
    compact = json.dumps({LOCALE: dict(sorted(translation.items()))}, ensure_ascii=False, separators=(",", ":"))
    escaped = compact.replace("'", "\\'")
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(f"OVERWRITE_SETTING_Custom_Translations='{escaped}'\n", encoding="utf-8", newline="\n")
    print(json.dumps({"output": str(args.output), "keys": len(translation)}, ensure_ascii=False))
    return 0


def add_source_args(parser, prefix="source", default_path=None):
    option = prefix.replace("_", "-")
    parser.add_argument(f"--{option}", default=default_path)
    parser.add_argument(f"--{option}-git-ref")
    parser.add_argument(f"--{option}-git-path", default=DEFAULT_SOURCE if "source" in prefix else DEFAULT_VI)


def add_validation_args(parser):
    parser.add_argument("--policy")
    parser.add_argument("--warnings-as-errors", action="store_true")
    parser.add_argument("--report-json")
    parser.add_argument("--report-md")


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=".", help="repository used by --*-git-ref")
    commands = parser.add_subparsers(dest="command", required=True)

    validate_parser = commands.add_parser("validate", help="run K1-K8")
    add_source_args(validate_parser)
    add_validation_args(validate_parser)
    validate_parser.add_argument("--translation", required=True)
    validate_parser.set_defaults(func=run_validate)

    extract_parser = commands.add_parser("extract", help="extract keys missing from Vietnamese resources")
    add_source_args(extract_parser)
    extract_parser.add_argument("--current")
    extract_parser.add_argument("--current-git-ref")
    extract_parser.add_argument("--current-git-path", default=DEFAULT_VI)
    extract_parser.add_argument("--custom", action="append")
    extract_parser.add_argument("--output", required=True)
    extract_parser.set_defaults(func=run_extract)

    merge_parser = commands.add_parser("merge", help="merge fragments, validate, and sort keys")
    add_source_args(merge_parser)
    add_validation_args(merge_parser)
    merge_parser.add_argument("--input", action="append", required=True)
    merge_parser.add_argument("--output", required=True)
    merge_parser.add_argument("--compact", action="store_true")
    merge_parser.set_defaults(func=run_merge)

    upgrade_parser = commands.add_parser("upgrade", help="produce the VH-10 upgrade comparison")
    for prefix in ("old_source", "new_source", "old_vi", "new_vi"):
        add_source_args(upgrade_parser, prefix)
    upgrade_parser.add_argument("--translation", required=True)
    upgrade_parser.add_argument("--output", required=True)
    upgrade_parser.set_defaults(func=run_upgrade)

    workbook_parser = commands.add_parser("workbook-queue", help="extract a priority and quality-review queue from the MRC workbook")
    workbook_parser.add_argument("--workbook", required=True)
    workbook_parser.add_argument("--priority", default="P2")
    workbook_parser.add_argument("--output", required=True)
    workbook_parser.set_defaults(func=run_workbook_queue)

    upload_parser = commands.add_parser("upload", help="validate and optionally POST Custom_Translations")
    add_source_args(upload_parser)
    add_validation_args(upload_parser)
    upload_parser.add_argument("--translation", required=True)
    upload_parser.add_argument("--url", required=True)
    upload_parser.add_argument("--user-id-env", default="RC_USER_ID")
    upload_parser.add_argument("--auth-token-env", default="RC_AUTH_TOKEN")
    upload_parser.add_argument("--backup-dir", default="build/vietnamese-i18n/backups")
    upload_parser.add_argument("--apply", action="store_true", help="perform the external write; default is dry-run")
    upload_parser.set_defaults(func=run_upload)

    env_parser = commands.add_parser("export-env", help="write the OVERWRITE_SETTING fallback")
    env_parser.add_argument("--translation", required=True)
    env_parser.add_argument("--output", required=True)
    env_parser.set_defaults(func=run_export_env)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (InputError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
