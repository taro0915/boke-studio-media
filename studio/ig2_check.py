"""captionthis.daily(IG2) の状態確認。直近の投稿の数字とアカウント情報を出す（読むだけ）。"""
import os, json, requests
tok, uid = os.environ["IG_ACCESS_TOKEN"], os.environ["IG_USER_ID"]
A = "https://graph.facebook.com/v24.0"
def g(path, **p):
    p["access_token"] = tok
    r = requests.get(f"{A}/{path}", params=p, timeout=60)
    return r.json() if r.ok else {"error": r.text.replace(tok, "***")[:300]}
print(json.dumps(g(uid, fields="username,followers_count,follows_count,media_count,account_type"), ensure_ascii=False))
for m in g(f"{uid}/media", fields="id,timestamp,media_type,like_count,comments_count,permalink", limit=8).get("data", []):
    ins = g(f"{m['id']}/insights", metric="views,reach,saved,shares")
    vals = {d["name"]: d["values"][0]["value"] for d in ins.get("data", [])} if "data" in ins else ins
    print(m["timestamp"], m["media_type"], "likes", m.get("like_count"), "comments", m.get("comments_count"), vals, m["permalink"])
for metric, extra in [("reach", {"period": "day"}), ("follower_count", {"period": "day"})]:
    print(metric, json.dumps(g(f"{uid}/insights", metric=metric, **extra), ensure_ascii=False)[:400])
for bd in ["country", "age", "gender"]:
    d = g(f"{uid}/insights", metric="follower_demographics", period="lifetime", metric_type="total_value", breakdown=bd)
    try:
        res = d["data"][0]["total_value"]["breakdowns"][0]["results"]
        res = sorted(res, key=lambda x: -x["value"])[:8]
        print(bd, [(r["dimension_values"][0], r["value"]) for r in res])
    except Exception:
        print(bd, json.dumps(d)[:300])
