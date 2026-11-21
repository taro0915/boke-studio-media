"""GitHub Actions 用：在庫(studio/posts/sNN_*)から次の1本をInstagramにカルーセル投稿する。PC不要。
画像はこの公開リポジトリの raw URL をそのまま Instagram に渡す。
秘密情報は環境変数（GitHub Secrets）から読む: IG_ACCESS_TOKEN, IG_USER_ID
  python studio/publish_cloud.py            # 次の1本を投稿
  python studio/publish_cloud.py --dry-run  # コンテナ作成まで（投稿しない）
"""
import argparse, datetime, json, os, sys, time
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

JST = ZoneInfo("Asia/Tokyo")
ROOT = Path(__file__).resolve().parent          # studio/
POSTS = ROOT / "posts"
STATE = ROOT / "posted.json"
REPO = os.environ.get("GITHUB_REPOSITORY", "taro0915/boke-studio-media")
BRANCH = os.environ.get("GITHUB_REF_NAME", "main")
RAW = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}/studio/posts"
API = "https://graph.facebook.com/v24.0"
MAX_PER_DAY = 4          # 7:30 / 11:30 / 18:00 / 21:00
MIN_GAP_SEC = 2 * 3600   # 前の投稿から2時間以上あける


def now():
    return datetime.datetime.now(JST)


def log(*a):
    print(now().strftime("%Y-%m-%d %H:%M:%S JST ") + " ".join(str(x) for x in a), flush=True)


def api(method, path, tok, **params):
    params["access_token"] = tok
    r = requests.request(method, f"{API}/{path}", data=params if method == "POST" else None,
                         params=params if method == "GET" else None, timeout=60)
    text = r.text.replace(tok, "***")
    if r.status_code != 200:
        raise RuntimeError(f"{path} HTTP {r.status_code}: {text[:400]}")
    return r.json()


def next_post(state, slot=0):
    """比較テスト: その日の1本目・3本目は人が選んだ在庫(s)、2本目・4本目はAIだけで選んだ在庫(a)。
    片方が切れたらもう片方から出す。"""
    arms = ["s", "a"] if slot % 2 == 0 else ["a", "s"]
    for arm in arms:
        for d in sorted(POSTS.glob(f"{arm}[0-9][0-9]_*")):
            if d.name not in state["posted"] and (d / "1.jpg").exists():
                return d
    return None


def image_urls(post_dir):
    files = sorted(post_dir.glob("[0-9]*.jpg"), key=lambda p: int(p.stem))
    urls = [f"{RAW}/{post_dir.name}/{f.name}" for f in files]
    for u in urls:
        for _ in range(30):
            if requests.get(u, timeout=30).status_code == 200:
                break
            time.sleep(4)
        else:
            raise RuntimeError(f"画像URLが開けません: {u}")
    return urls


def wait_ready(cid, tok):
    for _ in range(40):
        s = api("GET", cid, tok, fields="status_code").get("status_code")
        if s == "FINISHED":
            return
        if s == "ERROR":
            raise RuntimeError(f"コンテナ {cid} がERROR")
        time.sleep(3)
    raise RuntimeError(f"コンテナ {cid} が準備完了になりません")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    tok, uid = os.environ["IG_ACCESS_TOKEN"], os.environ["IG_USER_ID"]
    state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {"posted": {}}
    today = now().date().isoformat()
    todays = [v for v in state["posted"].values() if v.get("date") == today]
    post = next_post(state, len(todays))
    if not post:
        log("在庫なし。投稿しません。"); return 0
    if not a.dry_run and len(todays) >= MAX_PER_DAY:
        log(f"今日はすでに{MAX_PER_DAY}本投稿済み。投稿しません。"); return 0
    last = max([v.get("time", "") for v in todays] or [""])
    if not a.dry_run and last:
        lt = datetime.datetime.fromisoformat(last)
        if lt.tzinfo is None:
            lt = lt.replace(tzinfo=JST)
        if (now() - lt).total_seconds() < MIN_GAP_SEC:
            log("前の投稿から2時間たっていません。投稿しません。"); return 0
    # 二重投稿防止: 記録ファイルが古くても、インスタ側の実際の最新投稿時刻で確認する
    if not a.dry_run:
        latest = api("GET", f"{uid}/media", tok, fields="timestamp", limit=1).get("data", [])
        if latest:
            lt = datetime.datetime.fromisoformat(latest[0]["timestamp"].replace("+0000", "+00:00"))
            if (now() - lt).total_seconds() < MIN_GAP_SEC:
                log("インスタ側で2時間以内に投稿済み。二重投稿を防ぐため投稿しません。"); return 0
    caption = (post / "caption.txt").read_text(encoding="utf-8")
    log("対象:", post.name, "dry-run" if a.dry_run else "")
    urls = image_urls(post)
    log("画像URL OK:", len(urls), "枚")
    children = [api("POST", f"{uid}/media", tok, image_url=u, is_carousel_item="true")["id"] for u in urls]
    for c in children:
        wait_ready(c, tok)
    n = len(children)
    poll = {"option_a": "1〜2枚目", "option_b": "3〜4枚目", "option_c": "5枚目以降", "option_d": "笑ってない"} if n >= 5 \
        else {"option_a": "1枚目", "option_b": "2枚目", "option_c": "それ以降", "option_d": "笑ってない"}
    poll_caption = caption.replace("どのボケが一番ツボでしたか？番号でコメントしてね👇", "何枚目で笑った？👇タップで投票")
    pj = json.dumps(poll, ensure_ascii=False)
    tries = [(dict(caption=poll_caption, poll_attachment=pj, is_ai_generated="true"), True, True),
             (dict(caption=poll_caption, poll_attachment=pj), False, True),
             (dict(caption=caption, is_ai_generated="true"), True, False),
             (dict(caption=caption), False, False)]
    parent, ai, used_poll = None, False, False
    for params, ai_flag, poll_flag in tries:
        try:
            parent = api("POST", f"{uid}/media", tok, media_type="CAROUSEL", children=",".join(children), **params)["id"]
            ai, used_poll = ai_flag, poll_flag
            break
        except RuntimeError as e:
            log("親コンテナ失敗、次の形で再試行:", str(e)[:200])
    if not parent:
        raise RuntimeError("親コンテナを作れませんでした")
    wait_ready(parent, tok)
    log("コンテナ作成OK: 子", n, "AIラベル", ai, "アンケート", used_poll)
    if a.dry_run:
        log("dry-run のためここで終了（投稿していません）"); return 0
    media_id = api("POST", f"{uid}/media_publish", tok, creation_id=parent)["id"]
    link = api("GET", media_id, tok, fields="permalink").get("permalink", "")
    state["posted"][post.name] = {"date": today, "time": now().isoformat(timespec="seconds"),
                                  "media_id": media_id, "permalink": link, "ai_label": ai, "by": "cloud",
                                  "arm": "ai" if post.name.startswith("a") else "human"}
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    log("投稿完了:", link)
    log("残り在庫:", {arm: len([d for d in POSTS.glob(f"{arm}[0-9][0-9]_*") if d.name not in state["posted"]]) for arm in "sa"}, "本")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        log("失敗:", str(e)[:500])
        sys.exit(1)
