"""Local-development helper: write Custom_Translations straight into a Dockerised MongoDB.

Staging and production load translations through the REST `upload` command instead.
"""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

# Load validation logic from gen_vi
MODULE_PATH = Path(__file__).parent / "gen_vi.py"
SPEC = importlib.util.spec_from_file_location("gen_vi", MODULE_PATH)
gen_vi = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = gen_vi
SPEC.loader.exec_module(gen_vi)

source_path = Path("packages/i18n/src/locales/en.i18n.json")
translation_path = Path("tools/vietnamese-i18n/translations/MRC_RC_Custom-Translations_vi-VN.json")
policy_path = Path("tools/vietnamese-i18n/policy.json")

baseline_cfg = gen_vi.load_json_file(Path('tools/vietnamese-i18n/baseline.json'))
ref = baseline_cfg.get('rocket_chat_ref', '8.9.0')
source = gen_vi.load_git_json('.', ref, 'packages/i18n/src/locales/en.i18n.json')
raw_data = gen_vi.load_json_file(translation_path)
translation = gen_vi.unwrap_locale(raw_data, str(translation_path))
policy = gen_vi.load_policy(policy_path)

findings = gen_vi.validate(source, translation, policy)
exit_code = gen_vi.validation_exit(findings, warnings_as_errors=False)
if exit_code:
    payload = gen_vi.report_payload(findings, len(source), len(translation))
    print(f"Update Mongo blocked by K1-K8: {payload['errors']} errors, {payload['warnings']} warnings", file=sys.stderr)
    sys.exit(1)

compact_json = json.dumps({gen_vi.LOCALE: gen_vi.runtime_translation(translation)}, ensure_ascii=False, separators=(",", ":"))

mongo_script = """
db.rocketchat_settings.updateOne(
  { _id: 'Custom_Translations' },
  {
    $set: {
      value: %s,
      public: true,
      type: 'code',
      code: 'application/json',
      group: 'General',
      section: 'Translations',
      _updatedAt: new Date()
    },
    $unset: {
      processEnvValue: 1,
      valueSource: 1
    }
  },
  { upsert: true }
);
print('Updated Custom_Translations with public: true');
""" % json.dumps(compact_json)

p = subprocess.run(
    ['docker', 'exec', '-i', os.environ.get('MRC_MONGO_CONTAINER', 'mrc-chat-mongo'), 'mongosh', 'rocketchat', '--quiet'],
    input=mongo_script,
    text=True,
    capture_output=True,
    encoding='utf-8'
)
print("STDOUT:", p.stdout)
if p.stderr:
    print("STDERR:", p.stderr)
