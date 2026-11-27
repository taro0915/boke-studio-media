"""11/19より前の古い投稿を、古い順に1日10件ずつ削除する（2026-11-27 本人の依頼。削除は取り消せない）。
- 対象: 2026-11-19 00:00 JST より前に投稿されたものだけ
- 1日の上限: 10件（急に減ると不自然なため）
- 記録: studio/reports/deleted.json（削除した投稿のID・日時・説明文の先頭）
--check はアクセス許可と対象件数の確認だけ（削除しない）。"""
import argparse, datetime, json, os, pathlib, requests

API = "https://graph.facebook.com/v24.0"
JST = datetime.timezone(datetime.timedelta(hours=9))
CUTOFF = datetime.datetime(2026, 11, 19, tzinfo=JST)
PER_DAY = 10
LOG = pathlib.Path(__file__).parent / "reports" / "deleted.json"


def log(*a):
    print(datetime.datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S JST"), *a, flush=True)


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--check", action="store_true"); a = ap.parse_args()
    tok, uid = os.environ["IG_ACCESS_TOKEN"], os.environ["IG_USER_ID"]
    try:
        perms = requests.get(f"{API}/me/permissions", params={"access_token": tok}, timeout=60).json().get("data", [])
        granted = sorted(p["permission"] for p in perms if p.get("status") == "granted")
        log("許可:", ", ".join(granted))
        if not a.check and "instagram_manage_contents" not in granted:
            log("削除に必要な許可（instagram_manage_contents）がないので何もしません。トークンの取り直しが必要"); return 0
    except Exception as e:
        log("許可の確認に失敗:", e)
    items, url, params = [], f"{API}/{uid}/media", {"fields": "id,timestamp,caption,media_type", "limit": 100, "access_token": tok}
    while url:
        r = requests.get(url, params=params, timeout=60).json()
        items += r.get("data", [])
        url, params = r.get("paging", {}).get("next"), None
    old = sorted((i for i in items if datetime.datetime.fromisoformat(i["timestamp"].replace("+0000", "+00:00")) < CUTOFF),
                 key=lambda i: i["timestamp"])
    log(f"全投稿 {len(items)}件 / 11/19より前 {len(old)}件")
    done = json.loads(LOG.read_text(encoding="utf-8")) if LOG.exists() else []
    today = datetime.datetime.now(JST).date().isoformat()
    left = PER_DAY - sum(1 for d in done if d["deleted_at"].startswith(today))
    if a.check:
        for i in old[:PER_DAY]:
            log("次の対象:", i["timestamp"][:10], (i.get("caption") or "")[:30].replace("\n", " "))
        return 0
    if left <= 0:
        log("今日の分は削除済み"); return 0
    for i in old[:left]:
        r = requests.delete(f"{API}/{i['id']}", params={"access_token": tok}, timeout=60)
        if r.status_code != 200:
            log("削除できません。止めます:", r.status_code, r.text.replace(tok, "***")[:300]); break
        done.append({"id": i["id"], "posted": i["timestamp"], "caption": (i.get("caption") or "")[:60],
                     "deleted_at": datetime.datetime.now(JST).isoformat(timespec="seconds")})
        log("削除:", i["timestamp"][:10], (i.get("caption") or "")[:30].replace("\n", " "))
    LOG.parent.mkdir(exist_ok=True)
    LOG.write_text(json.dumps(done, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"残り {len(old) - (PER_DAY - left) - min(left, len(old))}件ほど")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
