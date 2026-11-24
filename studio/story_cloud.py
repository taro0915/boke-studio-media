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

# ジャンルは日替わりで回し、2週間後に数字の良いものへ寄せる
KINDS = {
    "sukatto": "スカッとする話。理不尽な人（義母・上司・ママ友・元夫など）に主人公が静かに、でも完璧にやり返す。最後の一撃は相手の言葉や行動がそのまま本人に返ってくる形",
    "shuraba": "修羅場の話。結婚式・法事・同窓会・家族会議など人が集まる場で、隠されていた事実が明るみに出る",
    "donden": "どんでん返しの話。最後の1〜2枚で、1枚目の意味がまったく逆に見えるような真相が明かされる",
    "unlucky": "ついてない話。小さな不運が雪だるま式に重なり、最後は思わず笑うか、ちょっと救われるオチ",
    "naki": "じんわり泣ける話。不器用な家族（父・母・祖父母・夫婦）が、言葉にしなかった思いを最後に知る",
}
ROTATION = ["sukatto", "donden", "shuraba", "unlucky", "naki"]

PROMPT = """あなたはInstagramで何十万回も読まれる「1枚1文の実話風ショートストーリー」の作家です。読者は日本の30〜60代。
今日のジャンル: {kind}

いちばん大事なこと:
- 1枚目で「えっ、どういうこと？」と思わせる。結末そのものではなく、結末の"ありえない一場面"を先に見せる。
  良い例:「義母が結婚式のスピーチで読み上げたのは、私の通帳の残高だった。」「定年の日、私の机に置かれていたのは花束ではなく、20年前の私の辞表だった。」
  悪い例:「ついてない一日だった。」（何も気にならない）
- 1枚目から結末が予想できたら失敗。最後の2枚で、読者の予想を必ず一度裏切る。
- 具体的な数字・物・セリフ（「」）を入れて、場面が目に浮かぶようにする。説明より出来事。
- 各文の終わりは「だが」「その夜」「数日後」など、次をめくらずにいられない引きで切る。
- 最後の1枚は短く、強く。読後に誰かに話したくなる一文。

ルール:
- 完全な創作。実在の人物・会社・学校・事件・地名の特定につながる固有名詞は使わない。
- 禁止: 自殺・自傷、子どもへの暴力や虐待、性的な内容、病気や死の生々しい描写、差別、政治、宗教、犯罪の手口。
- 1枚1文（45字以内）、全部で13〜16枚。文体は「〜だった。」の淡々とした語り。絵文字なし。
- 構成: 1枚目のひっかけ → 状況説明(2〜3枚) → 1回目の小さな意外 → 盛り上がり → 2回目の大きな意外 → 最後の一撃。中だるみの説明文は入れない。
- 最後の2〜3枚が一番大事（読者はここで「いいね」を押す）。読後に胸がざわつく・スカッとする・泣ける・吹き出す、のどれかを必ず起こす。
- 読者が「自分ならこうする」「これはひどい」と意見を言いたくなる、白黒つけにくい要素を1つ入れる（コメントを生むため）。

JSONだけを返す: {{"slides": ["1枚目", "2枚目", ...], "question": "読者が自分の意見や体験を書きたくなる問いかけ（例: あなたなら許せますか？／どっちが悪いと思う？）", "tags": ["#ハッシュタグ", 5個]}}"""


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


def _phrases(text):
    try:
        import budoux
        parts = budoux.load_default_japanese_parser().parse(text)
    except Exception:
        parts = list(text)
    out = []
    for t in parts:
        if out and t and t[0] in "、。」！？）":
            out[-1] += t
        else:
            out.append(t)
    return out


def wrap_ja(draw, text, f, maxw):
    """読点・カギカッコの切れ目で優先して改行し、長い所だけ文節（BudouX）で折る。短すぎる行は前後とまとめる。"""
    W_ = lambda t: draw.textlength(t, font=f)
    clauses, cur = [], ""
    for ch in text:
        cur += ch
        if ch in "、。！？" or (ch == "」" and len(cur) > 6):
            clauses.append(cur)
            cur = ""
    if cur:
        clauses.append(cur)
    lines = []
    for c in clauses:
        if W_(c) <= maxw:
            lines.append(c)
            continue
        k = int(-(-W_(c) // maxw))
        target = W_(c) / k
        line = ""
        for ph in _phrases(c):
            if line and (W_(line + ph) > maxw or W_(line + ph) > target * 1.15):
                lines.append(line)
                line = ph
            else:
                line += ph
        if line:
            lines.append(line)
    # 短い行（6文字以下）は、収まるなら隣とまとめる
    changed = True
    while changed:
        changed = False
        for i, l in enumerate(lines):
            if len(l) <= 6 and len(lines) > 1:
                if i > 0 and W_(lines[i - 1] + l) <= maxw:
                    lines[i - 1] += l; del lines[i]; changed = True; break
                if i + 1 < len(lines) and W_(l + lines[i + 1]) <= maxw:
                    lines[i + 1] = l + lines[i + 1]; del lines[i]; changed = True; break
    return lines


TINT = {"sukatto": (38, 44, 54), "shuraba": (52, 36, 36), "donden": (40, 40, 48), "unlucky": (46, 44, 38), "naki": (43, 43, 43)}


def slide(text, foot=None, kind=None):
    im = Image.new("RGB", (W, H), TINT.get(kind, BG))
    d = ImageDraw.Draw(im)
    f = font(62)
    lines = sum((wrap_ja(d, t, f, W - 260) for t in text.split("\n") if t), [])
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


CRITIC = """あなたはInstagramの編集長です。次の{n}本の下書き（1枚1文のカルーセル用の創作話）を厳しく採点してください。
採点軸（各10点）: 1枚目で指が止まるか / 1枚目から結末が予想できないか（予想できたら0点） / 13枚以上あっても途中でダレないか / 最後の2〜3枚の感情の強さ（いいねを押したくなるか） / 読者が意見を言いたくなる要素があるか（コメント）。
いちばん点の高い1本を選び、弱い文だけを書き直して完成版にしてください。条件は元と同じ（1枚1文・45字以内・13〜16枚・創作・禁止事項を守る・絵文字なし）。
下書き:
{drafts}
JSONだけを返す: {{"scores": [各下書きの合計点], "slides": [...], "question": "...", "tags": [5個]}}"""


def claude(prompt, max_tokens=16000):
    body = {"model": "claude-sonnet-5-5", "max_tokens": max_tokens, "messages": [{"role": "user", "content": prompt}]}
    for _ in range(3):
        r = requests.post("https://api.anthropic.com/v1/messages", timeout=300, json=body,
                          headers={"x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01"})
        if r.status_code == 200:
            txt = "".join(c.get("text", "") for c in r.json()["content"] if c.get("type") == "text")
            m = re.search(r"\{.*\}", txt, re.S)
            if m:
                try:
                    return json.loads(m.group(0))
                except json.JSONDecodeError:
                    pass
        log("応答が不正、再試行", r.status_code, r.text[:200])
        time.sleep(5)
    return None


def ok(slides):
    return 10 <= len(slides) <= 18 and all(len(x) <= 60 for x in slides)


def ask_claude(kind):
    """3本書く → 編集長役が採点して一番いい1本を選び、弱い文だけ直す。"""
    drafts = []
    for _ in range(3):
        d = claude(PROMPT.format(kind=KINDS[kind]))
        if d and ok([x.strip() for x in d.get("slides", []) if x.strip()]):
            drafts.append(d)
    if not drafts:
        raise RuntimeError("話を作れませんでした")
    text = "\n\n".join(f"【下書き{i+1}】\n" + "\n".join(d["slides"]) for i, d in enumerate(drafts))
    final = claude(CRITIC.format(n=len(drafts), drafts=text))
    if final and ok([x.strip() for x in final.get("slides", []) if x.strip()]):
        log("採点:", final.get("scores"))
        src = final
    else:
        log("編集が失敗したので下書き1を使う")
        src = drafts[0]
    slides = [x.strip() for x in src["slides"] if x.strip()]
    return slides, src.get("question") or drafts[0].get("question", "あなたならどうする？"), (src.get("tags") or drafts[0].get("tags", []))[:6]


def build(force=False):
    st = load_state()
    if st.get("pending"):
        log("未投稿の pending あり"); return 0
    today = now().date().isoformat()
    if not force and (now().hour != SLOT_HOUR or today in st["posted"] or any(k.startswith(today) and "test" not in k for k in st["posted"])):
        log("投稿枠ではないので作りません"); return 0
    kind = ROTATION[now().toordinal() % len(ROTATION)]
    slides, question, tags = ask_claude(kind)
    key = today if not force else f"{today}_test{now().strftime('%H%M')}"
    out = OUT / key
    out.mkdir(parents=True, exist_ok=True)
    for i, s in enumerate(slides, 1):
        slide(s, None, kind).save(out / f"{i}.jpg", quality=90)
    # 最後に「感想を聞く」1枚（いいね・コメントは最後に押されるため）
    closing = question + "\nコメントで教えてください。"
    slide(closing, "※この話はフィクションです", kind).save(out / f"{len(slides) + 1}.jpg", quality=90)
    caption = (f"{slides[0]}\n\n{question} コメントで教えてね👇\n\n"
               "※AIで作った創作のお話です。実在の人物・団体とは関係ありません。\n\n"
               + " ".join(dict.fromkeys(tags + ["#創作", "#短編", {"sukatto": "#スカッとする話", "shuraba": "#修羅場", "donden": "#どんでん返し", "unlucky": "#ついてない話", "naki": "#泣ける話"}[kind]])))
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
    if not dry and now().hour not in (SLOT_HOUR, SLOT_HOUR + 1):
        log("投稿は22時台（遅れても23時台）だけ。pending は次の枠で投稿"); return 0
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
