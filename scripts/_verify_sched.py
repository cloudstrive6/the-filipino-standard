import truststore; truststore.inject_into_ssl()
import os, sys, datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo
import requests
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")
tok = os.environ["CLICKUP_API_TOKEN"].strip()
PHT = ZoneInfo("Asia/Manila")
for tid in sys.argv[1].split(","):
    r = requests.get(f"https://api.clickup.com/api/v2/task/{tid}", params={"custom_fields": "true"},
                     headers={"Authorization": tok}, timeout=30)
    f = [x for x in r.json()["custom_fields"] if x["id"] == "8a89f1c0-f964-4281-bbe8-82f2bc187ca0"][0]
    v = f.get("value")
    when = (dt.datetime.fromtimestamp(int(v) / 1000, tz=dt.timezone.utc).astimezone(PHT)
            .strftime("%Y-%m-%d %I:%M %p PHT") if v else "EMPTY")
    print(tid, "| value =", v, "->", when, "| type_config =", f.get("type_config"))
