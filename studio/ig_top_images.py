"""読むだけ：伸びた過去投稿の1〜2枚目を studio/reports/top/ に保存（分析用）。"""
import os, sys, requests
from pathlib import Path
tok = os.environ["IG_ACCESS_TOKEN"]
A = "https://graph.facebook.com/v24.0"
out = Path(__file__).resolve().parent / "reports" / "top"
out.mkdir(parents=True, exist_ok=True)
for mid in sys.argv[1:]:
    ch = requests.get(f"{A}/{mid}/children", params={"fields": "media_url,media_type", "access_token": tok}, timeout=60).json().get("data", [])
    for i, c in enumerate(ch[:3], 1):
        if c.get("media_type") == "IMAGE" and c.get("media_url"):
            (out / f"{mid}_{i}.jpg").write_bytes(requests.get(c["media_url"], timeout=60).content)
    print(mid, len(ch))
