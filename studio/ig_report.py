"""@omoshiroibokete の投稿ごとの数字を集めて studio/reports/ig_latest.json に保存する（読むだけ）。"""
import os, json, datetime, requests
from pathlib import Path
from zoneinfo import ZoneInfo
ROOT = Path(__file__).resolve().parent
tok, uid = os.environ["IG_ACCESS_TOKEN"], os.environ["IG_USER_ID"]
A = "https://graph.facebook.com/v24.0"
def g(path, **p):
    p["access_token"] = tok
    r = requests.get(f"{A}/{path}", params=p, timeout=60)
    return r.json() if r.ok else {"error": r.text.replace(tok, "***")[:300]}
posted = json.loads((ROOT / "posted.json").read_text(encoding="utf-8"))["posted"]
arm_by_id = {v.get("media_id"): (k, v.get("arm")) for k, v in posted.items()}
st = ROOT / "story_state.json"
story_by_id = {v.get("media_id"): (k, v.get("kind")) for k, v in (json.loads(st.read_text(encoding="utf-8"))["posted"].items() if st.exists() else [])}
rows, url = [], f"{uid}/media"
params = dict(fields="id,timestamp,media_type,caption,like_count,comments_count,permalink", limit=50)
for _ in range(3):
    d = g(url, **params)
    for m in d.get("data", []):
        ins = g(f"{m['id']}/insights", metric="views,reach,saved,shares,total_interactions,follows,profile_visits")
        vals = {x["name"]: x["values"][0]["value"] for x in ins.get("data", [])} if "data" in ins else {"insights_error": ins.get("error", "")[:120]}
        key, arm = arm_by_id.get(m["id"], (None, None))
        skey, kind = story_by_id.get(m["id"], (None, None))
        rows.append({"id": m["id"], "time": m["timestamp"], "type": m["media_type"], "likes": m.get("like_count"), "comments": m.get("comments_count"),
                     "post": key or skey, "arm": arm, "story_kind": kind, "first_line": (m.get("caption") or "").split("\n")[0][:60], **vals, "link": m.get("permalink")})
    nxt = d.get("paging", {}).get("cursors", {}).get("after")
    if not nxt or len(rows) >= 120:
        break
    params["after"] = nxt
out = ROOT / "reports"
out.mkdir(exist_ok=True)
(out / "ig_latest.json").write_text(json.dumps({"at": datetime.datetime.now(ZoneInfo("Asia/Tokyo")).isoformat(timespec="minutes"), "rows": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
print("rows", len(rows))
