"""captionthis.daily（英語の写真で一言）をクラウドだけで回す。PC不要。
@omoshiroibokete と同じAI写真（studio/posts/*/1.jpg の写真部分）に、Claude が英語のボケを付けてカルーセルにする。

  python studio/en_cloud.py build     # 投稿時刻なら次の1本を作って studio/posts_en/ に保存（pending.json）
  python studio/en_cloud.py publish   # pending を Instagram(IG2) に投稿
  python studio/en_cloud.py build --force   # 時刻に関係なく作る（テスト用）
  python studio/en_cloud.py publish --dry-run

投稿枠（日本時間）: 10時台＝米国東部の夜 / 22時台＝米国東部の朝・インドの夕方 / 0時台＝インドの夜
毎時のreplyワークフロー（Cloudflare Workerから起動）の中で呼ばれ、枠の時間だけ動く。
"""
import base64, datetime, io, json, os, re, sys, time
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from PIL import Image, ImageDraw, ImageFont

JST = ZoneInfo("Asia/Tokyo")
ROOT = Path(__file__).resolve().parent
SRC = ROOT / "posts"
OUT = ROOT / "posts_en"
STATE = ROOT / "en_state.json"
REPO = os.environ.get("GITHUB_REPOSITORY", "taro0915/boke-studio-media")
RAW = f"https://raw.githubusercontent.com/{REPO}/main/studio/posts_en"
API = "https://graph.facebook.com/v24.0"
SLOTS = [10, 22, 0]          # JST hours
MIN_GAP_SEC = 3 * 3600
# captionthis.daily はフォロワーが実在しないためカルーセルはリーチ0。フォロワー外に届くリールで出す。
FORMAT = os.environ.get("EN_FORMAT", "reel")
W, H, PHOTO_H = 1080, 1350, 780

FONT_BOLD = ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
             "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"]
FONT_REG = ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"]

PROMPT = """You write captions for "Caption This 📸", an Instagram account where followers caption funny photos.
The audience: adults 35-65 in the USA and India. Write in simple, natural American English.

Look at the photo and write 10 short, funny one-liner captions (each max 55 characters).
What works best: relatable everyday life — spouse, in-laws, boss, Monday, bills and money, kids, diets, retirement, group chats, family WhatsApp.
Do NOT just describe the photo. Each line needs a twist that makes you look at the photo again.
Rules: no real people or celebrities, no brands, no politics, no religion, no jokes about race, nationality, body weight, disability or age-shaming. Nothing sexual. Do not mention Japan.
Also invent a fictional poster for each caption (first name, age 30-70, city — mostly US cities, some Indian cities). These are part of the joke, keep them ordinary.

Then pick the best 7 (best one first) and write a one-line question for the post caption.
Return ONLY JSON: {"items":[{"text":"...","by":"Linda, 54, Ohio"}, ...7 items], "question":"..."}"""


def now():
    return datetime.datetime.now(JST)


def log(*a):
    print(now().strftime("%Y-%m-%d %H:%M:%S JST ") + " ".join(str(x) for x in a), flush=True)


def load_state():
    if STATE.exists():
        return json.loads(STATE.read_text(encoding="utf-8"))
    return {"posted": {}, "pending": None}


def save_state(s):
    STATE.write_text(json.dumps(s, ensure_ascii=False, indent=1), encoding="utf-8")


def slot_key(t):
    if t.hour not in SLOTS:
        return None
    return f"{t.date().isoformat()}_{t.hour:02d}"


def font(paths, size):
    for p in paths:
        if Path(p).exists():
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def photo_of(src_dir):
    im = Image.open(src_dir / "1.jpg").convert("RGB")
    # 日本版は上840pxが写真（右下に透かし）。透かしの手前で切る。
    return im.crop((0, 0, im.width, PHOTO_H))


def ask_claude(photo):
    buf = io.BytesIO()
    photo.resize((768, int(768 * photo.height / photo.width))).save(buf, "JPEG", quality=85)
    body = {"model": "claude-sonnet-5-5", "max_tokens": 2000,
            "messages": [{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                             "data": base64.b64encode(buf.getvalue()).decode()}},
                {"type": "text", "text": PROMPT}]}]}
    for i in range(3):
        r = requests.post("https://api.anthropic.com/v1/messages", timeout=120, json=body,
                          headers={"x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01"})
        if r.status_code == 200:
            txt = "".join(c.get("text", "") for c in r.json()["content"] if c.get("type") == "text")
            m = re.search(r"\{.*\}", txt, re.S)
            if m:
                data = json.loads(m.group(0))
                items = [x for x in data.get("items", []) if x.get("text") and len(x["text"]) <= 70][:7]
                if len(items) >= 5:
                    return items, data.get("question", "Which one got you? 👇")
        log("Claude応答が不正、再試行", r.status_code, r.text[:200])
        time.sleep(5)
    raise RuntimeError("Claudeから英語ボケが取れませんでした")


def wrap(draw, text, f, maxw):
    words, lines, cur = text.split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if draw.textlength(t, font=f) <= maxw:
            cur = t
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def slide(photo, text, by):
    im = Image.new("RGB", (W, H), "white")
    im.paste(photo.resize((W, PHOTO_H)), (0, 0))
    d = ImageDraw.Draw(im)
    wm = font(FONT_BOLD, 30)
    tag = "@captionthis.daily"
    tw = d.textlength(tag, font=wm)
    d.text((W - tw - 24, PHOTO_H - 50), tag, font=wm, fill=(255, 255, 255), stroke_width=2, stroke_fill=(0, 0, 0))
    area_top, area_h = PHOTO_H, H - PHOTO_H - 90
    for size in range(78, 34, -4):
        f = font(FONT_BOLD, size)
        lines = wrap(d, text, f, W - 140)
        lh = int(size * 1.25)
        if len(lines) * lh <= area_h - 40 and len(lines) <= 4:
            break
    y = area_top + (area_h - len(lines) * lh) // 2 + 10
    for ln in lines:
        d.text(((W - d.textlength(ln, font=f)) / 2, y), ln, font=f, fill=(25, 25, 25))
        y += lh
    fb = font(FONT_REG, 34)
    byline = f"— {by}"
    d.text(((W - d.textlength(byline, font=fb)) / 2, H - 80), byline, font=fb, fill=(120, 120, 120))
    return im


def reel_frames(photo, items, out):
    """縦型1080x1920のリール用コマを作り、ffmpegで約18秒の動画にする。"""
    import subprocess
    VW, VH = 1080, 1920
    fb, fr = font(FONT_BOLD, 64), font(FONT_REG, 36)
    hook = font(FONT_BOLD, 72)
    def frame(text, by, idx):
        im = Image.new("RGB", (VW, VH), (12, 12, 12))
        im.paste(photo.resize((VW, PHOTO_H)), (0, 360))
        d = ImageDraw.Draw(im)
        t = "Caption this 📸".replace(" 📸", "")
        d.text(((VW - d.textlength(t, font=hook)) / 2, 150), t, font=hook, fill=(255, 214, 0))
        sub = f"{idx}/{len(items)}  Which one wins?" if idx else "Read till the end 👀".replace(" 👀", "")
        d.text(((VW - d.textlength(sub, font=fr)) / 2, 255), sub, font=fr, fill=(200, 200, 200))
        if text:
            lines = wrap(d, text, fb, VW - 120)
            y = 360 + PHOTO_H + 70
            for ln in lines[:4]:
                d.text(((VW - d.textlength(ln, font=fb)) / 2, y), ln, font=fb, fill=(255, 255, 255))
                y += 82
            d.text(((VW - d.textlength("— " + by, font=fr)) / 2, y + 20), "— " + by, font=fr, fill=(160, 160, 160))
        return im
    tmp = out / "_frames"
    tmp.mkdir(exist_ok=True)
    seq = [(frame(None, "", 0), 1.4)] + [(frame(it["text"], it.get("by", "Anonymous"), i), 2.3) for i, it in enumerate(items, 1)]
    lst = []
    for i, (im, dur) in enumerate(seq):
        fp = tmp / f"{i:02d}.png"
        im.save(fp)
        lst.append(f"file '{fp.name}'\nduration {dur}")
    lst.append(f"file '{tmp.name}/{len(seq)-1:02d}.png'".replace(f"{tmp.name}/", ""))
    (tmp / "list.txt").write_text("\n".join(lst) + "\n", encoding="utf-8")
    video = out / "reel.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(tmp / "list.txt"),
                    "-f", "lavfi", "-t", str(sum(dd for _, dd in seq)), "-i", "anullsrc=r=44100:cl=stereo",
                    "-vf", "fps=30,format=yuv420p", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                    "-c:a", "aac", "-shortest", "-movflags", "+faststart", str(video)], check=True)
    for f in tmp.iterdir():
        f.unlink()
    tmp.rmdir()
    return video


def build(force=False):
    st = load_state()
    if st.get("pending"):
        log("未投稿の pending あり:", st["pending"]["dir"]); return 0
    key = slot_key(now())
    if not force and (not key or key in [v.get("slot") for v in st["posted"].values()]):
        log("投稿枠ではないので作りません"); return 0
    used = set(st["posted"])
    cands = [d for d in sorted(SRC.glob("[sa][0-9][0-9]_*"), key=lambda p: p.name[1:]) if (d / "1.jpg").exists() and d.name not in used]
    if not cands:
        log("写真の在庫なし"); return 0
    src = cands[0]
    photo = photo_of(src)
    items, question = ask_claude(photo)
    out = OUT / src.name
    out.mkdir(parents=True, exist_ok=True)
    for i, it in enumerate(items, 1):
        slide(photo, it["text"], it.get("by", "Anonymous")).save(out / f"{i}.jpg", quality=90)
    caption = (f"Caption this 📸 {question}\n"
               "Which one made you laugh? Comment the number 👇 Or drop your own caption!\n\n"
               "Photos & captions made with AI. Names are made up — just for fun 😄\n\n"
               "#captionthis #funny #funnypics #memes #lol #humor #relatable #dailylaugh #funnymemes #comedy")
    (out / "caption.txt").write_text(caption, encoding="utf-8")
    (out / "items.json").write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    if FORMAT == "reel":
        reel_frames(photo, items, out)
    st["pending"] = {"dir": src.name, "slot": key or "manual", "n": len(items), "format": FORMAT}
    save_state(st)
    log("作成:", src.name, len(items), "枚")
    return 0


def api(method, path, tok, **params):
    params["access_token"] = tok
    r = requests.request(method, f"{API}/{path}", data=params if method == "POST" else None,
                         params=params if method == "GET" else None, timeout=60)
    if r.status_code != 200:
        raise RuntimeError(f"{path} HTTP {r.status_code}: {r.text.replace(tok, '***')[:400]}")
    return r.json()


def wait_ready(cid, tok):
    for _ in range(40):
        s = api("GET", cid, tok, fields="status_code").get("status_code")
        if s == "FINISHED":
            return
        if s == "ERROR":
            raise RuntimeError(f"コンテナ {cid} がERROR")
        time.sleep(3)
    raise RuntimeError("コンテナが準備完了になりません")


def publish(dry=False):
    st = load_state()
    p = st.get("pending")
    if not p:
        log("pending なし"); return 0
    tok, uid = os.environ["IG_ACCESS_TOKEN"], os.environ["IG_USER_ID"]
    h = now().hour
    if not dry and not any(h in (x, (x + 1) % 24) for x in SLOTS):
        log("投稿枠の時間ではないので見送り（pendingは次の枠で投稿）"); return 0
    if not dry:
        latest = api("GET", f"{uid}/media", tok, fields="timestamp", limit=1).get("data", [])
        if latest:
            lt = datetime.datetime.fromisoformat(latest[0]["timestamp"].replace("+0000", "+00:00"))
            if (now() - lt).total_seconds() < MIN_GAP_SEC:
                log("直近に投稿済み。二重投稿防止のため見送り（pendingは次の枠で投稿）"); return 0
    d = OUT / p["dir"]
    if p.get("format") == "reel":
        return publish_reel(st, p, d, tok, uid, dry)
    files = sorted(d.glob("[0-9]*.jpg"), key=lambda f: int(f.stem))
    urls = [f"{RAW}/{d.name}/{f.name}" for f in files]
    for u in urls:
        for _ in range(30):
            if requests.get(u, timeout=30).status_code == 200:
                break
            time.sleep(4)
        else:
            raise RuntimeError(f"画像URLが開けません: {u}")
    children = [api("POST", f"{uid}/media", tok, image_url=u, is_carousel_item="true")["id"] for u in urls]
    for c in children:
        wait_ready(c, tok)
    caption = (d / "caption.txt").read_text(encoding="utf-8")
    n = len(children)
    poll = json.dumps({"option_a": "1-2", "option_b": "3-4", "option_c": "5+", "option_d": "None 😐"} if n >= 5
                      else {"option_a": "1", "option_b": "2", "option_c": "3+", "option_d": "None 😐"})
    pc = caption.replace("Which one made you laugh? Comment the number 👇", "Which one made you laugh? Vote 👇")
    tries = [dict(caption=pc, poll_attachment=poll, is_ai_generated="true"), dict(caption=pc, poll_attachment=poll),
             dict(caption=caption, is_ai_generated="true"), dict(caption=caption)]
    parent = None
    for prm in tries:
        try:
            parent = api("POST", f"{uid}/media", tok, media_type="CAROUSEL", children=",".join(children), **prm)["id"]
            break
        except RuntimeError as e:
            log("親コンテナ失敗、次の形で再試行:", str(e)[:200])
    if not parent:
        raise RuntimeError("親コンテナを作れませんでした")
    wait_ready(parent, tok)
    if dry:
        log("dry-run: コンテナ作成OK（投稿していません）", n, "枚"); return 0
    mid = api("POST", f"{uid}/media_publish", tok, creation_id=parent)["id"]
    link = api("GET", mid, tok, fields="permalink").get("permalink", "")
    st["posted"][p["dir"]] = {"time": now().isoformat(timespec="seconds"), "slot": p["slot"], "media_id": mid, "permalink": link}
    st["pending"] = None
    save_state(st)
    log("投稿完了:", link)
    return 0


def publish_reel(st, p, d, tok, uid, dry):
    url = f"{RAW}/{d.name}/reel.mp4"
    for _ in range(30):
        if requests.head(url, timeout=30).status_code == 200:
            break
        time.sleep(4)
    else:
        raise RuntimeError(f"動画URLが開けません: {url}")
    caption = (d / "caption.txt").read_text(encoding="utf-8").replace("Comment the number 👇", "Comment the number (1-7) 👇")
    # Instagramの流行りの音楽を自動で付ける（Audio API）。使った曲は続けて使わない。
    audio = {}
    try:
        tracks = api("GET", "ig_audio", tok, audio_type="music", user_id=uid, limit=30).get("audio", [])
        used = set(st.get("used_audio", [])[-20:])
        pick = next((t for t in tracks if t.get("audio_id") not in used and 15000 <= t.get("duration_in_ms", 0)), None)
        if pick:
            audio = {"audio_configuration": json.dumps({"audio_id": pick["audio_id"], "audio_volume": 100, "video_volume": 1})}
            st.setdefault("used_audio", []).append(pick["audio_id"])
            log("音楽:", pick.get("title"), "/", pick.get("display_artist"))
    except RuntimeError as e:
        log("音楽の取得に失敗（音楽なしで投稿）:", str(e)[:200])
    cid = None
    for prm in (dict(is_ai_generated="true", **audio), dict(**audio), dict(is_ai_generated="true"), {}):
        try:
            cid = api("POST", f"{uid}/media", tok, media_type="REELS", video_url=url, caption=caption, share_to_feed="true", **prm)["id"]
            break
        except RuntimeError as e:
            log("リールのコンテナ失敗、再試行:", str(e)[:200])
    if not cid:
        raise RuntimeError("リールのコンテナを作れませんでした")
    for _ in range(60):
        s_ = api("GET", cid, tok, fields="status_code").get("status_code")
        if s_ == "FINISHED":
            break
        if s_ == "ERROR":
            raise RuntimeError("リールの処理がERROR")
        time.sleep(5)
    else:
        raise RuntimeError("リールの処理が終わりません")
    if dry:
        log("dry-run: リールのコンテナ作成OK（投稿していません）"); return 0
    mid = api("POST", f"{uid}/media_publish", tok, creation_id=cid)["id"]
    link = api("GET", mid, tok, fields="permalink").get("permalink", "")
    st["posted"][p["dir"]] = {"time": now().isoformat(timespec="seconds"), "slot": p["slot"], "format": "reel", "media_id": mid, "permalink": link}
    st["pending"] = None
    save_state(st)
    log("リール投稿完了:", link)
    return 0


if __name__ == "__main__":
    try:
        mode = sys.argv[1]
        if mode == "build":
            sys.exit(build(force="--force" in sys.argv))
        sys.exit(publish(dry="--dry-run" in sys.argv))
    except Exception as e:
        log("失敗:", str(e)[:500])
        sys.exit(1)
