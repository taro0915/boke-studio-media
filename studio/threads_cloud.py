"""Threads (@omoshiroibokete) の自動運用。GitHub Actions 用、PC不要。
  python studio/threads_cloud.py carousel   # インスタに出した写真で一言カルーセルを1本Threadsにも流す
  python studio/threads_cloud.py odai       # 写真1枚だけの「この写真に一言」お題を出す
  python studio/threads_cloud.py reply      # Threadsへの返信に自動で返す（最大10件）
環境変数: THREADS_ACCESS_TOKEN, THREADS_USER_ID, ANTHROPIC_API_KEY
"""
import datetime, io, json, os, subprocess, sys, time
from pathlib import Path
from zoneinfo import ZoneInfo
import requests

JST = ZoneInfo("Asia/Tokyo")
ROOT = Path(__file__).resolve().parent
POSTS = ROOT / "posts"
STATE = ROOT / "threads_state.json"
MEDIA = ROOT / "threads_media"
REPO = os.environ.get("GITHUB_REPOSITORY", "taro0915/boke-studio-media")
BRANCH = os.environ.get("GITHUB_REF_NAME", "main")
RAW = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}/studio"
API = "https://graph.threads.net/v1.0"
TOK = os.environ["THREADS_ACCESS_TOKEN"]
UID = os.environ["THREADS_USER_ID"]

CAPTION = ("写真で一言📸\nどのボケが一番ツボでしたか？番号で返信してね👇\n\n"
           "あなたならこの写真に何て言う？\n返信は全部読んでます🙌\n\n※写真はAIで生成しています")
ODAI = ("【お題】この写真に一言どうぞ📸\n\n思いついたボケを返信で👇\n全部読んで返します🙌\n\n※写真はAIで生成しています")


def log(*a):
    print(datetime.datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S JST"), *a, flush=True)


def api(method, path, **params):
    params["access_token"] = TOK
    r = requests.request(method, f"{API}/{path}", data=params if method == "POST" else None,
                         params=params if method == "GET" else None, timeout=60)
    if r.status_code != 200:
        raise RuntimeError(f"{path} HTTP {r.status_code}: {r.text.replace(TOK, '***')[:300]}")
    return r.json()


def wait_ready(cid):
    for _ in range(40):
        s = api("GET", cid, fields="status").get("status")
        if s == "FINISHED":
            return
        if s == "ERROR":
            raise RuntimeError(f"container {cid} ERROR")
        time.sleep(3)


def publish(cid):
    wait_ready(cid)
    mid = api("POST", f"{UID}/threads_publish", creation_id=cid)["id"]
    return api("GET", mid, fields="permalink").get("permalink", ""), mid


def url_ok(u):
    for _ in range(30):
        if requests.get(u, timeout=30).status_code == 200:
            return
        time.sleep(4)
    raise RuntimeError("画像URLが開けません: " + u)


def load():
    return json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {"carousel": {}, "odai": {}, "replied": []}


def save(st):
    STATE.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")


def ig_posted():
    p = ROOT / "posted.json"
    d = json.loads(p.read_text(encoding="utf-8")).get("posted", {}) if p.exists() else {}
    return sorted(d, key=lambda k: d[k].get("time", ""))


def carousel():
    st = load()
    # インスタで出し終わった投稿のうち、Threadsにまだ出していない古い順
    cand = [n for n in ig_posted() if n not in st["carousel"] and (POSTS / n / "1.jpg").exists()]
    if not cand:
        log("Threadsに流す在庫なし"); return
    name = cand[0]
    files = sorted((POSTS / name).glob("[0-9]*.jpg"), key=lambda p: int(p.stem))[:20]
    kids = []
    for f in files:
        u = f"{RAW}/posts/{name}/{f.name}"; url_ok(u)
        kids.append(api("POST", f"{UID}/threads", media_type="IMAGE", image_url=u, is_carousel_item="true")["id"])
    for k in kids:
        wait_ready(k)
    cid = api("POST", f"{UID}/threads", media_type="CAROUSEL", children=",".join(kids), text=CAPTION)["id"]
    link, mid = publish(cid)
    st["carousel"][name] = {"time": datetime.datetime.now(JST).isoformat(timespec="seconds"), "media_id": mid, "permalink": link}
    save(st); log("カルーセル投稿:", name, link)


def odai():
    """インスタで出し終わった投稿の写真部分だけを切り出してお題にする（ボケは見せない）。"""
    from PIL import Image
    st = load()
    used = {v.get("src") for v in st["odai"].values()}
    cand = [n for n in ig_posted() if n not in used and (POSTS / n / "1.jpg").exists()]
    if not cand:
        log("お題にする写真なし"); return
    name = cand[-1]  # 最近インスタに出したものほど新鮮
    im = Image.open(POSTS / name / "1.jpg").convert("RGB")
    photo = im.crop((0, 0, im.width, int(im.height * 840 / 1350)))
    MEDIA.mkdir(exist_ok=True)
    out = MEDIA / f"odai_{name}.jpg"; photo.save(out, quality=92)
    subprocess.run(["git", "add", str(out)], check=True)
    subprocess.run(["git", "commit", "-m", f"threads odai image {name}"], check=True)
    subprocess.run(["git", "pull", "--rebase"], check=True)
    subprocess.run(["git", "push"], check=True)
    u = f"{RAW}/threads_media/{out.name}"; url_ok(u)
    cid = api("POST", f"{UID}/threads", media_type="IMAGE", image_url=u, text=ODAI)["id"]
    link, mid = publish(cid)
    st["odai"][mid] = {"src": name, "time": datetime.datetime.now(JST).isoformat(timespec="seconds"), "permalink": link}
    save(st); log("お題投稿:", name, link)


def reply():
    # Metaの「自動化」判定を避けるため、返信は3時間おき（日本時間 0,3,6,...時）だけ
    import datetime as _dt
    from zoneinfo import ZoneInfo as _Z
    if _dt.datetime.now(_Z("Asia/Tokyo")).hour % 3 != 0:
        print("返信は3時間おき。今回はスキップ"); return 0
    sys.path.insert(0, str(ROOT))
    os.environ.setdefault("REPLY_LANG", "ja")
    import auto_reply_cloud as ar
    st = load(); done = set(st["replied"])
    me = api("GET", "me", fields="username")["username"]
    since = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=7)
    posts = api("GET", f"{UID}/threads", fields="id,timestamp,text", limit=25).get("data", [])
    n = 0
    for p in posts:
        if datetime.datetime.fromisoformat(p["timestamp"].replace("+0000", "+00:00")) < since:
            continue
        reps = api("GET", f"{p['id']}/replies", fields="id,text,username,timestamp,has_replies", reverse="false").get("data", [])
        ctx = "お題の写真への一言ボケ募集の投稿" if p.get("text", "").startswith("【お題】") else "写真で一言のボケのカルーセル投稿"
        for c in reps:
            cid, user, text = c["id"], c.get("username", ""), c.get("text", "") or ""
            if cid in done or user == me:
                continue
            why = ar.skip_reason(text)
            if why:
                log(f"SKIP({why}) @{user}"); done.add(cid); continue
            out = ar.make_reply(text, ctx, dict(os.environ))
            if not out:
                done.add(cid); continue
            try:
                rid = api("POST", f"{UID}/threads", media_type="TEXT", text=out, reply_to_id=cid)["id"]
                publish(rid)
            except RuntimeError as e:
                log("返信失敗:", str(e)[:200]); continue
            log(f"@{user}: {text[:40]} -> {out}")
            done.add(cid); n += 1
            st["replied"] = sorted(done); save(st)
            time.sleep(15)
            if n >= 10:
                break
        if n >= 10:
            break
    st["replied"] = sorted(done); save(st)
    log("返信", n, "件")


if __name__ == "__main__":
    try:
        {"carousel": carousel, "odai": odai, "reply": reply}[sys.argv[1]]()
    except Exception as e:
        log("失敗:", str(e)[:500]); sys.exit(1)
