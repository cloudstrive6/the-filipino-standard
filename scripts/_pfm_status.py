import truststore; truststore.inject_into_ssl()
import os, sys, json
from pathlib import Path
import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
key = os.environ["POSTFORME_API_KEY"].strip()
base = (os.environ.get("POSTFORME_BASE_URL") or "https://api.postforme.dev/v1").rstrip("/")
s = requests.Session(); s.headers.update({"Authorization": f"Bearer {key}", "Accept": "application/json"})
for pid in sys.argv[1].split(","):
    pid = pid.strip()
    if not pid:
        continue
    r = s.get(f"{base}/social-posts/{pid}", timeout=30)
    print(f"--- {pid}: HTTP {r.status_code} ---")
    def redact(o):
        if isinstance(o, dict):
            return {k: ("***" if ("token" in k.lower() or "secret" in k.lower()) else redact(v)) for k, v in o.items()}
        if isinstance(o, list):
            return [redact(x) for x in o]
        return o
    try:
        d = r.json()
        print("status:", d.get("status"))
        print("error:", d.get("error") or d.get("error_message") or d.get("errors"))
        print(json.dumps(redact(d), indent=2)[:2000])
    except Exception:
        print(r.text[:600])
    print()
