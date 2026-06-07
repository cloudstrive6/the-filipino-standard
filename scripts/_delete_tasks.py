import truststore; truststore.inject_into_ssl()
import os, sys
from pathlib import Path
import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
tok = os.environ["CLICKUP_API_TOKEN"].strip()
ids = [x.strip() for x in sys.argv[1].split(",") if x.strip()]
s = requests.Session(); s.headers.update({"Authorization": tok})
ok = fail = 0
for tid in ids:
    done = False
    for _ in range(4):
        try:
            r = s.delete(f"https://api.clickup.com/api/v2/task/{tid}", timeout=30)
            if r.status_code in (200, 204):
                done = True
            else:
                print(f"{tid}: HTTP {r.status_code} {r.text[:80]}")
            break
        except requests.RequestException:
            continue
    print(f"{tid}: {'deleted' if done else 'FAILED'}")
    ok += 1 if done else 0
    fail += 0 if done else 1
print(f"=== deleted {ok}, failed {fail} of {len(ids)} ===")
