"""@omoshiroibokete に1日1本「1枚1文で続きが気になる」創作の話カルーセルを出す。PC不要。
日ごとに「笑える話」と「ついてない話（不幸話）」を交互に。すべてフィクションで、そう明記する。

  python studio/story_cloud.py build     # 投稿枠(22時台)なら1本作る → studio/stories/<日付>/
  python studio/story_cloud.py publish   # pending を Instagram(IG) に投稿
  python studio/story_cloud.py build --force / publish --dry-run   # テスト用
"""
import datetime, json, os, re, sys, time
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from PIL import Image, ImageDraw, ImageFont

JST = ZoneInfo("Asia/Tokyo")
ROOT = Path(__file__).resolve().parent
OUT = ROOT / "stories"
STATE = ROOT / "story_state.json"
REPO = os.environ.get("GITHUB_REPOSITORY", "taro0915/boke-studio-media")
RAW = f"https://raw.githubusercontent.com/{REPO}/main/studio/stories"
API = "https://graph.facebook.com/v24.0"
SLOT_HOUR = 22
MIN_GAP_SEC = 3600
W, H = 1080, 1350
BG, FG, SUB = (43, 43, 43), (236, 233, 226), (150, 147, 140)
SERIF = ["/usr/share/fonts/opentype/noto/NotoSerifCJK-Medium.ttc", "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc"]

KINDS = {
    "funny": "思わず笑ってしまう話（最後にオチがある）",
    "unlucky": "ついてない話・不幸話（読んだ人が『うわ…』と同情しつつ、最後に少し笑える or ちょっと救われるオチ）",
}

PROMPT = """あなたはInstagramの「1枚1文で読ませる」短い話のカルーセルを書く作家です。読者は日本の30〜60代。
今日のテーマ: {kind}

条件:
- 完全な創作。実在の人物・会社・学校・事件・地名の特定につながる固有名詞は使わない。
- 題材は日常のあるある（夫婦、嫁姑、上司と部下、給料・お金、子ども、定年、ご近所、同窓会、婚活など）。
- 禁止: 自殺・自傷、子どもへの暴力や虐待、性的な内容、病気や死の生々しい描写、差別、政治、宗教。
- 1枚目は結末やいちばん気になる一文を先に言い切って「何があった？」と思わせる（例:「結婚式の当日、新郎の母が持ってきたのはご祝儀ではなく請求書だった。」）。
- 2枚目以降は時系列で、1枚に1文（40字以内）。各文の終わりで次が気になるように。
- 全部で7〜9枚。最後の1枚はオチ。
- 文体は「〜だった。」の淡々とした語り。絵文字は使わない。

JSONだけを返す: {{"slides": ["1枚目の文", "2枚目の文", ...], "question": "コメントを促す短い問いかけ（例: あなたならどうする？）", "tags": ["#ハッシュタグ", 5個]}}"""


def now():
    return datetime.datetime.now(JST)


def log(*a):
    print(now().strftime("%Y-%m-%d %H:%M:%S JST ") + " ".join(str(x) for x in a), flush=True)


def load_state():
    return json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {"posted": {}, "pending": None}


def save_state(s):
    STATE.write_text(json.dumps(s, ensure_ascii=False, indent=1), encoding="utf-8")


def font(size):
    for p in SERIF:
        if Path(p).exists():
            return ImageFont.truetype(p, size, index=0)
    raise RuntimeError("日本語の明朝フォントがありません（fonts-noto-cjk を入れてください）")


def wrap_ja(draw, text, f, maxw):
    """読点・句点の後ろで優先的に改行し、それでも長ければ文字単位で折る。"""
    chunks = re.findall(r"[^、。！？」]*[、。！？」]?", text)
    chunks = [c for c in chunks if c]
    lines, cur = [], ""
    for c in chunks:
        if draw.textlength(cur + c, font=f) <= maxw:
            cur += c
            continue
        if cur:
            lines.append(cur)
        cur = ""
        for ch in c:
            if draw.textlength(cur + ch, font=f) > maxw:
                lines.append(cur)
                cur = ""
            cur += ch
    if cur:
        lines.append(cur)
    # 行頭に句読点が来たら前の行へ
    for i in range(1, len(lines)):
        while lines[i] and lines[i][0] in "、。」！？":
            lines[i - 1] += lines[i][0]
            lines[i] = lines[i][1:]
    return [l for l in lines if l]


def slide(text, foot=None):
    im = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(im)
    f = font(62)
    lines = wrap_ja(d, text, f, W - 260)
    lh = 112
    y = 330 - (len(lines) - 3) * lh // 2 if len(lines) > 3 else 330
    for ln in lines:
        d.text(((W - d.textlength(ln, font=f)) / 2, y), ln, font=f, fill=FG)
        y += lh
    mark = font(30)
    m = "毎秒投稿おもしろボケて"
    d.text(((W - d.textlength(m, font=mark)) / 2, H - 230), m, font=mark, fill=SUB)
    if foot:
        fs = font(28)
        d.text(((W - d.textlength(foot, font=fs)) / 2, H - 170), foot, font=fs, fill=SUB)
    return im


def ask_claude(kind):
    body = {"model": "claude-sonnet-5-5", "max_tokens": 2000,
            "messages": [{"role": "user", "content": PROMPT.format(kind=KINDS[kind])}]}
    for _ in range(3):
        r = requests.post("https://api.anthropic.com/v1/messages", timeout=120, json=body,
                          headers={"x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01"})
        if r.status_code == 200:
            txt = "".join(c.get("text", "") for c in r.json()["content"] if c.get("type") == "text")
            m = re.search(r"\{.*\}", txt, re.S)
            if m:
                data = json.loads(m.group(0))
                slides = [s.strip() for s in data.get("slides", []) if s.strip()]
                if 6 <= len(slides) <= 10 and all(len(s) <= 60 for s in slides):
                    return slides, data.get("question", "あなたならどうする？"), data.get("tags", [])[:6]
        log("応答が不正、再試行", r.status_code, r.text[:200])
        time.sleep(5)
    raise RuntimeError("話を作れませんでした")


def build(force=False):
    st = load_state()
    if st.get("pending"):
        log("未投稿の pending あり"); return 0
    today = now().date().isoformat()
    if not force and (now().hour != SLOT_HOUR or today in st["posted"]):
        log("投稿枠ではないので作りません"); return 0
    kind = "funny" if now().toordinal() % 2 == 0 else "unlucky"
    slides, question, tags = ask_claude(kind)
    key = today if not force else f"{today}_test{now().strftime('%H%M')}"
    out = OUT / key
    out.mkdir(parents=True, exist_ok=True)
    for i, s in enumerate(slides, 1):
        foot = "※この話はフィクションです" if i == len(slides) else None
        slide(s, foot).save(out / f"{i}.jpg", quality=90)
    caption = (f"{slides[0]}\n\n{question} コメントで教えてね👇\n\n"
               "※AIで作った創作のお話です。実在の人物・団体とは関係ありません。\n\n"
               + " ".join(tags + ["#創作", "#短編", "#ちょっといい話" if kind == "funny" else "#ついてない話"]))
    (out / "caption.txt").write_text(caption, encoding="utf-8")
    (out / "story.json").write_text(json.dumps({"kind": kind, "slides": slides}, ensure_ascii=False, indent=1), encoding="utf-8")
    st["pending"] = {"dir": key, "kind": kind}
    save_state(st)
    log("作成:", key, kind, len(slides), "枚")
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
            raise RuntimeError("コンテナがERROR")
        time.sleep(3)
    raise RuntimeError("コンテナが準備完了になりません")


def publish(dry=False):
    st = load_state()
    p = st.get("pending")
    if not p:
        log("pending なし"); return 0
    tok, uid = os.environ["IG_ACCESS_TOKEN"], os.environ["IG_USER_ID"]
    if not dry:
        latest = api("GET", f"{uid}/media", tok, fields="timestamp", limit=1).get("data", [])
        if latest:
            lt = datetime.datetime.fromisoformat(latest[0]["timestamp"].replace("+0000", "+00:00"))
            if (now() - lt).total_seconds() < MIN_GAP_SEC:
                log("直近に投稿あり。次の回で投稿します"); return 0
    d = OUT / p["dir"]
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
    parent = None
    for prm in (dict(caption=caption, is_ai_generated="true"), dict(caption=caption)):
        try:
            parent = api("POST", f"{uid}/media", tok, media_type="CAROUSEL", children=",".join(children), **prm)["id"]
            break
        except RuntimeError as e:
            log("親コンテナ失敗、再試行:", str(e)[:200])
    if not parent:
        raise RuntimeError("親コンテナを作れませんでした")
    wait_ready(parent, tok)
    if dry:
        log("dry-run: コンテナ作成OK（投稿していません）", len(children), "枚"); return 0
    mid = api("POST", f"{uid}/media_publish", tok, creation_id=parent)["id"]
    link = api("GET", mid, tok, fields="permalink").get("permalink", "")
    st["posted"][p["dir"]] = {"time": now().isoformat(timespec="seconds"), "kind": p["kind"], "media_id": mid, "permalink": link}
    st["pending"] = None
    save_state(st)
    log("投稿完了:", link)
    return 0


if __name__ == "__main__":
    try:
        if sys.argv[1] == "build":
            sys.exit(build(force="--force" in sys.argv))
        sys.exit(publish(dry="--dry-run" in sys.argv))
    except Exception as e:
        log("失敗:", str(e)[:500])
        sys.exit(1)
