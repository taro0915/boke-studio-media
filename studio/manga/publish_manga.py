"""恋愛漫画を captionthis.daily に1日1本カルーセル投稿する（GitHub Actions 用）。
在庫: studio/manga/posts/mNNN_*  状態: studio/manga/posted.json
環境変数: IG2_ACCESS_TOKEN, IG2_USER_ID"""
import datetime, json, os, sys, time
from pathlib import Path
from zoneinfo import ZoneInfo
import requests

JST = ZoneInfo("Asia/Tokyo")
ROOT = Path(__file__).resolve().parent
POSTS = ROOT / "posts"
STATE = ROOT / "posted.json"
REPO = os.environ.get("GITHUB_REPOSITORY", "taro0915/boke-studio-media")
BRANCH = os.environ.get("GITHUB_REF_NAME", "main")
RAW = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}/studio/manga/posts"
API = "https://graph.facebook.com/v24.0"
MIN_GAP_SEC = 18 * 3600


def log(*a):
    print(datetime.datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S JST"), *a, flush=True)


def api(method, path, tok, **params):
    params["access_token"] = tok
    r = requests.request(method, f"{API}/{path}", data=params if method == "POST" else None,
                         params=params if method == "GET" else None, timeout=60)
    if r.status_code != 200:
        raise RuntimeError(f"{path} HTTP {r.status_code}: {r.text.replace(tok, '***')[:400]}")
    return r.json()


def wait_ready(cid, tok):
    for _ in range(60):
        s = api("GET", cid, tok, fields="status_code").get("status_code")
        if s == "FINISHED":
            return
        if s == "ERROR":
            raise RuntimeError(f"container {cid} ERROR")
        time.sleep(3)
    raise RuntimeError("container not ready")


def main():
    dry = "--dry-run" in sys.argv
    tok, uid = os.environ["IG2_ACCESS_TOKEN"], os.environ["IG2_USER_ID"]
    state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {"posted": {}}
    post = next((d for d in sorted(POSTS.glob("m[0-9][0-9][0-9]_*")) if d.name not in state["posted"] and (d / "1.jpg").exists()), None)
    if not post:
        log("在庫なし"); return 0
    last = max([v["time"] for v in state["posted"].values()] or [""])
    if last and not dry and (datetime.datetime.now(JST) - datetime.datetime.fromisoformat(last)).total_seconds() < MIN_GAP_SEC:
        log("前回から18時間たっていないので投稿しません"); return 0
    files = sorted(post.glob("[0-9]*.jpg"), key=lambda p: int(p.stem))[:20]
    urls = [f"{RAW}/{post.name}/{f.name}" for f in files]
    for u in urls:
        for _ in range(30):
            if requests.get(u, timeout=30).status_code == 200:
                break
            time.sleep(4)
        else:
            raise RuntimeError("画像URLが開けません: " + u)
    log("対象:", post.name, len(urls), "枚", "dry-run" if dry else "")
    kids = [api("POST", f"{uid}/media", tok, image_url=u, is_carousel_item="true")["id"] for u in urls]
    for k in kids:
        wait_ready(k, tok)
    cap = (post / "caption.txt").read_text(encoding="utf-8")
    parent = None
    for extra in ({"is_ai_generated": "true"}, {}):
        try:
            parent = api("POST", f"{uid}/media", tok, media_type="CAROUSEL", children=",".join(kids), caption=cap, **extra)["id"]; break
        except RuntimeError as e:
            log("親コンテナ再試行:", str(e)[:200])
    if not parent:
        raise RuntimeError("親コンテナ失敗")
    wait_ready(parent, tok)
    if dry:
        log("dry-run 終了（投稿していません）"); return 0
    mid = api("POST", f"{uid}/media_publish", tok, creation_id=parent)["id"]
    link = api("GET", mid, tok, fields="permalink").get("permalink", "")
    state["posted"][post.name] = {"time": datetime.datetime.now(JST).isoformat(timespec="seconds"), "media_id": mid, "permalink": link}
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    log("投稿完了:", link)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        log("失敗:", str(e)[:500]); sys.exit(1)
