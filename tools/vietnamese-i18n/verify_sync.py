import json
import subprocess

with open('tools/vietnamese-i18n/translations/MRC_RC_Custom-Translations_vi-VN.json', 'r', encoding='utf-8') as f:
    file_data = json.load(f)['vi-VN']

res = subprocess.run(
    ['docker', 'exec', '-i', 'mrc-chat-mongo', 'mongosh', 'rocketchat', '--quiet', '--eval', 'db.rocketchat_settings.findOne({_id: "Custom_Translations"}).value'],
    capture_output=True,
    text=True,
    encoding='utf-8'
)

# Output is printed as a string or quoted string
stdout_clean = res.stdout.strip()
if stdout_clean.startswith("'") and stdout_clean.endswith("'"):
    stdout_clean = stdout_clean[1:-1]
elif stdout_clean.startswith('"') and stdout_clean.endswith('"'):
    stdout_clean = stdout_clean[1:-1]

try:
    mongo_data = json.loads(stdout_clean).get('vi-VN', {})
except Exception as e:
    # mongosh might format or print with newlines
    for line in res.stdout.splitlines():
        line = line.strip()
        if line.startswith('{') and line.endswith('}'):
            try:
                mongo_data = json.loads(line).get('vi-VN', {})
                break
            except:
                pass
    else:
        print("Failed to parse. First 200 chars:", repr(res.stdout[:200]))
        mongo_data = {}

print(f"File keys: {len(file_data)}")
print(f"MongoDB keys: {len(mongo_data)}")
diff = set(file_data.keys()) ^ set(mongo_data.keys())
print(f"Key differences: {len(diff)}")
if not diff and len(mongo_data) > 0:
    mismatches = [k for k in file_data if file_data[k] != mongo_data.get(k)]
    print(f"Value mismatches: {len(mismatches)}")
    if not mismatches:
        print("MongoDB and Translation file are 100% IN SYNC!")
