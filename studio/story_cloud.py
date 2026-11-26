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
SLOT_HOURS = [12, 22]  # 昼休みと寝る前（話カルーセルはボケの約3倍読まれるため1日2本）
THREE_FROM = "2026-12-02"  # この日から朝7時台（通勤電車）を足して1日3本


def slot_hours():
    return [7, 12, 22] if now().date().isoformat() >= THREE_FROM else SLOT_HOURS
MIN_GAP_SEC = 3600
W, H = 1080, 1350
BG, FG, SUB = (43, 43, 43), (236, 233, 226), (150, 147, 140)
SERIF = ["/usr/share/fonts/opentype/noto/NotoSerifCJK-Medium.ttc", "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc"]

# ジャンルは日替わりで回し、2週間後に数字の良いものへ寄せる
KINDS = {
    "sukatto": "スカッとする話。威張っている人・ずるい人が、最後に自分のしたことの報いを受ける。主人公は声を荒げず、暴力・脅し・違法な仕返しはしない。現実にありそうな出来事だけで書く",
    "shuraba": "修羅場の話。結婚式・法事・同窓会・家族会議など人が集まる場で、隠されていた事実が明るみに出る",
    "donden": "どんでん返しの話。最後の1〜2枚で、1枚目の意味がまったく逆に見えるような真相が明かされる",
    "unlucky": "ついてない話。小さな不運が雪だるま式に重なり、最後は思わず笑うか、ちょっと救われるオチ",
    "naki": "じんわり泣ける話。不器用な家族（父・母・祖父母・夫婦）が、言葉にしなかった思いを最後に知る",
}
# スカッとの「返し方」。毎回ちがう型を使い、同じ構成が続かないようにする（2026-11-26 似た話が続いたため）
TWISTS = [
    "相手が自慢していたものが、実は主人公のおかげで成り立っていたと、本人の口からばれる",
    "相手が主人公をはめようとした仕掛けに、相手自身が引っかかる",
    "主人公は何もしない。相手が調子に乗って自分で墓穴を掘り、まわりが勝手に気づく",
    "見下されていた主人公の『本当の立場』（店のオーナー、相手の親の恩人など）が、最後にさらっと明かされる",
    "主人公が残しておいた記録（メモ、写真、領収書、録音など合法なもの）が、いちばんいい場面で効く",
    "相手がいつも言っていたルールや口ぐせを、主人公がそのまま守っただけで相手が困る",
    "子どもや店員など、相手が一番なめていた人の素直な一言で、場の空気がひっくり返る",
    "主人公は仕返しをせず、相手を助ける。その結果、相手が一番恥ずかしい思いをする",
]

# スカッと系が一番伸びたので4分の3をスカッとにする（2026-11-25）。飽きないよう舞台は毎回変える
ROTATION = ["sukatto", "sukatto", "donden", "sukatto", "sukatto", "naki", "sukatto", "sukatto"]

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
- 全部で13〜16枚。文体は「〜だった。」の淡々とした語り。絵文字なし。
- スマホで、電車の中やリビングでさっと読まれる。ひと目で意味がわかる文にする:
  ・1枚1文、35字以内。長くなるなら2枚に分ける。
  ・小学校高学年でもわかる言葉だけを使う。難しい漢字・四字熟語・業界用語・カタカナ語は使わない（例:「稟議」→「会社の申請」、「エビデンス」→「証拠」）。
  ・だれが何をしたかを毎回はっきり書く。「彼」「それ」など、指すものがわかりにくい言葉は避ける。
  ・登場人物は「私」「部長」「義母」「夫」のように役割で呼ぶ。名前は付けない。
  ・セリフは「」で短く。1枚に出来事は1つだけ。
- いちばん大事：初めて読む人が一度で話をわかること。
  ・時間は前から順に進める。時間を戻すのは「1枚目のひっかけ → 2枚目で『その○日前。』と戻る」の1回だけ。それ以外の回想は使わない。
  ・登場人物は3人まで（私＋相手役＋判定する人）。
  ・なぜ相手が負けたのか、仕組みを読者がはっきりわかるように、決め手の1枚の前後で「何が・なぜ効いたか」を1文で書く。ほのめかしや、わかる人だけわかるオチは禁止。
  ・最後の一言は、前に出てきた言葉をそのまま使い、読者がすぐ「あの言葉だ」とわかるようにする。
- 構成: 1枚目のひっかけ → 状況説明(2〜3枚) → 1回目の小さな意外 → 盛り上がり → 2回目の大きな意外 → 最後の一撃。中だるみの説明文は入れない。
- 最後の2〜3枚が一番大事（読者はここで「いいね」を押す）。読後に胸がざわつく・スカッとする・泣ける・吹き出す、のどれかを必ず起こす。
- 読者が「自分ならこうする」「これはひどい」と意見を言いたくなる、白黒つけにくい要素を1つ入れる（コメントを生むため）。
- 「これ、あの人に読ませたい」と誰かに送りたくなる話にする（シェアが一番広がる）。だれもが身近に1人は思い当たる人物（威張る上司、口うるさい義母、自慢ばかりのママ友など）を相手役にし、最後の一撃は、そのまま友だちに送れるくらい短く気持ちいいセリフにする。

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
採点軸（各10点）: 1枚目で指が止まるか / 1枚目から結末が予想できないか（予想できたら0点） / 13枚以上あっても途中でダレないか / 最後の2〜3枚の感情の強さ（いいねを押したくなるか） / 読者が意見を言いたくなる要素があるか（コメント） / 「あの人に読ませたい」と友だちに送りたくなるか（シェア）。
いちばん点の高い1本を選び、弱い文だけを書き直して完成版にしてください。条件は元と同じ（1枚1文・35字以内・13〜16枚・創作・禁止事項を守る・絵文字なし）。
仕上げとして全文を読み直し、スマホでひと目で読めるか（難しい言葉・長い文・だれのことかわからない文がないか）を直し、誤字脱字・不自然な日本語・話の矛盾をゼロにしてください。
下書き:
{drafts}
JSONだけを返す: {{"scores": [各下書きの合計点], "slides": [...], "question": "...", "tags": [5個]}}"""


def claude(prompt, max_tokens=16000, model="claude-sonnet-5-5"):
    body = {"model": model, "max_tokens": max_tokens, "messages": [{"role": "user", "content": prompt}]}
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
    return 10 <= len(slides) <= 18 and all(len(x) <= 50 for x in slides)


STAGES = ["会社（上司・同僚）", "義母・義実家", "ママ友・PTA", "元夫・元妻", "ご近所・マンション", "お店（客と店員）", "親戚の集まり", "取引先・営業先"]


MIN_SCORE = 48  # 60点満点（6軸）。これ未満なら書き直す（2026-11-26 ボケ廃止で話一本に）
HIT_SAMPLE = "部長が全社員の前で読み上げたのは、私が書いた部長自身の退職願だった。"  # 実績5.3万回・フォロー3


def ask_claude(kind, stage=None, extra=""):
    """3本書く→採点→最高点が基準未満なら1回だけ書き直し、良い方を採る。"""
    best = None
    for attempt in range(2):
        r = _ask_once(kind, stage, extra)
        sc = r[3]
        log(f"試行{attempt+1}: 最高点 {sc}")
        if best is None or sc > best[3]:
            best = r
        if sc >= MIN_SCORE:
            break
    return best[0], best[1], best[2]


LOGLINES = """あなたはヒット連発の短編ドラマの企画担当です。日本の30〜60代がスマホで読む「1枚1文」の話の企画を出してください。
ジャンル: {kind}
条件: 一行あらすじ（60字以内）を20個。どれも「誰が・誰に・何をされて・どうやって・どうなる」が一文でわかること。
- 生活の『あるある』（家事、職場、義実家、近所、店、学校行事など）に根ざし、読者の半分以上が『いる、こういう人』と思えること
- 決め手になる具体的な『物』か『一言』を必ず入れる（領収書、回覧板、LINEの既読、ハンコ、レシート、席順表 など）
- 複雑な仕掛けや専門知識が必要なものは不可。説明なしで小学生でもわかる仕組みだけ
JSONだけを返す: {{"loglines": [20個]}}"""

PICK = """次の一行あらすじ20個から、Instagramで一番読まれ・友だちに送られそうなものを厳しく3つ選んでください。
基準: ①一読でわかる ②『いる、こういう人』と思える ③オチの仕組みがシンプルで気持ちいい ④最近の話と似ていない
最近出した話:
{recent}
【候補】
{cands}
JSONだけを返す: {{"picks": ["選んだあらすじ1", "2", "3"]}}"""

READER = """あなたは初めてこの話を読む、ふつうの50代の読者です。スマホで1枚ずつ読みました。
【話】
{body}
質問: ①3行で、何が起きたか要約して。 ②相手はなぜ負けた（または、なぜその結末になった）の？ ③わかりにくかった枚数（番号）と理由。なければ空。
JSONだけを返す: {{"summary": "...", "why": "...", "unclear": [{{"no": 番号, "reason": "..."}}], "understood": true/false}}"""

FIX = """次の話は、読者に『わかりにくい』と言われました。指摘された所だけを直して、一度で誰でもわかる話にしてください。
話の流れ・つかみ（1枚目）・オチは変えない。1枚1文・35字以内・枚数は13〜17枚。時間は前から順に。
【読者の指摘】
{notes}
【話】
{body}
JSONだけを返す: {{"slides": [...]}}"""


def pick_loglines(kind_text, recent):
    r = claude(LOGLINES.format(kind=kind_text))
    cands = (r or {}).get("loglines") or []
    if len(cands) < 5:
        return []
    p = claude(PICK.format(recent=recent or "（なし）", cands="\n".join(f"- {c}" for c in cands)), max_tokens=16000, model="claude-opus-5-5")
    picks = (p or {}).get("picks") or []
    log("企画:", picks)
    return picks[:3]


def reader_check(slides):
    """別のAIに『初めての読者』として読ませ、わからない所があれば直す（最大2回）。"""
    for _ in range(2):
        body = "\n".join(f"{i}. {x}" for i, x in enumerate(slides, 1))
        r = claude(READER.format(body=body))
        if not r:
            return slides
        log("読者テスト:", r.get("summary", "")[:80], "| わかりにくい:", r.get("unclear"))
        if r.get("understood") and not r.get("unclear"):
            return slides
        f = claude(FIX.format(notes=json.dumps(r, ensure_ascii=False), body=body), max_tokens=24000, model="claude-opus-5-5")
        new = [x.strip() for x in (f or {}).get("slides", []) if x.strip()]
        if not new or not ok(new):
            return slides
        slides = new
    return slides


def _ask_once(kind, stage=None, extra=""):
    drafts = []
    base = KINDS[kind] + (f"。今回の舞台は「{stage}」" if stage else "")
    picks = [] if "続編" in extra else pick_loglines(base, extra)
    for i in range(3):
        idea = f"。\nこの企画で書く：「{picks[i]}」" if i < len(picks) else ""
        d = claude(PROMPT.format(kind=base + extra + idea))
        if d and ok([x.strip() for x in d.get("slides", []) if x.strip()]):
            drafts.append(d)
    if not drafts:
        raise RuntimeError("話を作れませんでした")
    text = "\n\n".join(f"【下書き{i+1}】\n" + "\n".join(d["slides"]) for i, d in enumerate(drafts))
    # 仕上げ（選定・推敲・校正）は一番賢いOpusで行う
    final = claude(f"基準：これまでで一番伸びた話の1枚目は「{HIT_SAMPLE}」（5.3万回・フォロー獲得）。これより弱い1枚目には厳しく点をつけること（ただし内容や構成をまねた話は減点。似た話が続くと読者は飽きる）。\n\n" + CRITIC.format(n=len(drafts), drafts=text), max_tokens=24000, model="claude-opus-5-5")
    if final and ok([x.strip() for x in final.get("slides", []) if x.strip()]):
        log("採点:", final.get("scores"))
        src = final
    else:
        log("編集が失敗したので下書き1を使う")
        src = drafts[0]
    slides = [x.strip() for x in src["slides"] if x.strip()]
    slides[0] = sharpen_hook(slides)
    slides = reader_check(slides)
    try:
        score = max(int(x) for x in (final or {}).get("scores") or [0])
    except Exception:
        score = 0
    return slides, src.get("question") or drafts[0].get("question", "あなたならどうする？"), (src.get("tags") or drafts[0].get("tags", []))[:6], score


HOOK = """あなたはInstagramの見出し職人です。次の話（1枚1文のカルーセル）の1枚目だけを、もっと指が止まる1文にしたい。
基準：これまでで一番伸びた1枚目は「{hit}」（5.3万回）。この強さ以上を目指す。
条件：
- 話の中身と矛盾しない（ネタばらしはしない。結末は書かない）
- 「ありえない瞬間」「え、なんで？」と思わせる場面を、具体的な人と物で見せる
- 35字以内、1文、小学5〜6年生でも読める言葉、絵文字なし
- 今の1枚目より弱いなら、今の1枚目をそのまま選ぶ
- 基準の文の言い回しや形（「〜が読み上げたのは、〜だった」）はまねしない
候補を5つ書き、いちばん強い1つを選ぶ。
【今の1枚目】{cur}
【話の全体】
{body}
JSONだけを返す: {{"candidates": [5個], "best": "選んだ1文"}}"""


def sharpen_hook(slides):
    """1枚目（つかみ）だけ専用に5案出して一番強いものに差し替える。失敗したら元のまま。"""
    try:
        r = claude(HOOK.format(hit=HIT_SAMPLE, cur=slides[0], body="\n".join(slides)), max_tokens=16000, model="claude-opus-5-5")
        b = (r or {}).get("best", "").strip()
        if b and len(b) <= 50:
            log("つかみ:", slides[0], "→", b)
            return b
    except Exception as e:
        log("つかみ調整に失敗:", e)
    return slides[0]


# ---- 伸び始めた話を早めに見つけて、次の枠で続編を出す（10万回を待たない） ----
HOT_MIN_VIEWS = 10000      # 最低これだけ見られている
HOT_PER_HOUR = 1500        # 1時間あたりの伸び（部長の話は約2,700回/時、ふつうの話は500〜600回/時）
HOT_WINDOW_H = (4, 36)     # 投稿から4〜36時間の話だけを見る


def find_hot(st):
    tok, uid = os.environ.get("IG_ACCESS_TOKEN"), os.environ.get("IG_USER_ID")
    if not tok:
        return None
    best = None
    for i, (k, v) in enumerate(st["posted"].items(), 1):
        if v.get("sequel_done") or not v.get("media_id"):
            continue
        try:
            age = (now() - datetime.datetime.fromisoformat(v["time"])).total_seconds() / 3600
        except Exception:
            continue
        if not (HOT_WINDOW_H[0] <= age <= HOT_WINDOW_H[1]):
            continue
        try:
            d = api("GET", f"{v['media_id']}/insights", tok, metric="views")
            views = int(d["data"][0]["values"][0]["value"])
        except Exception as e:
            log("閲覧数を取れず:", k, e); continue
        pace = views / max(age, 1)
        log(f"伸び確認: {k} {views}回 / {age:.1f}時間 = {pace:.0f}回/時")
        if views >= HOT_MIN_VIEWS and pace >= HOT_PER_HOUR and (best is None or pace > best[3]):
            best = (k, v, i, pace)
    return best


def sequel_extra(k, no):
    try:
        orig = json.loads((OUT / k / "story.json").read_text(encoding="utf-8"))["slides"]
    except Exception:
        return None
    return ("。\n今回は大好評だった前作（No." + str(no) + "）の続編。同じ登場人物（役割の呼び名も同じ）の『その後』を書く。\n"
            "前作を読んでいない人にも分かるように、2〜3枚目で前作のあらすじを1〜2文だけ入れる。\n"
            "1枚目は前作を知らなくても指が止まる『ありえない瞬間』にする（『続編です』とは書かない）。\n"
            "前作よりさらに大きな意外とスカッとを用意する。前作の決めゼリフを最後に1回だけ効かせてよい。\n"
            "【前作】\n" + "\n".join(orig))


def recent_summary(st, n=6):
    lines = []
    for k in list(st["posted"])[-n:]:
        try:
            sl = json.loads((OUT / k / "story.json").read_text(encoding="utf-8"))["slides"]
            lines.append("・" + sl[0] + " … " + sl[-1])
        except Exception:
            pass
    return "\n".join(lines)


def build(force=False):
    st = load_state()
    if st.get("pending"):
        log("未投稿の pending あり"); return 0
    today = now().date().isoformat()
    slot = f"{today}_{now().hour:02d}"
    if not force and (now().hour not in slot_hours() or slot in st["posted"]):
        log("投稿枠ではないので作りません"); return 0
    kind = ROTATION[len(st["posted"]) % len(ROTATION)]
    stage = STAGES[len(st["posted"]) % len(STAGES)] if kind == "sukatto" else None
    sequel_of = None
    hot = None if force else find_hot(st)
    extra = sequel_extra(hot[0], hot[2]) if hot else None
    if hot and extra:
        sequel_of = hot[2]
        kind, stage = hot[1]["kind"], None
        st["posted"][hot[0]]["sequel_done"] = True
        log(f"伸びている話 No.{sequel_of} の続編を作ります（{hot[3]:.0f}回/時）")
    if not extra:
        extra = ""
        if kind == "sukatto":
            extra += "。返し方の型は「" + TWISTS[len(st["posted"]) % len(TWISTS)] + "」。相手役は舞台に合った人物にする（会社以外の舞台で上司や部長を出さない）"
        rec = recent_summary(st)
        if rec:
            extra += ("。\n最近出した話（1枚目…最後の1枚）は次のとおり。これらと舞台・相手役・仕返しの手口・決めゼリフの形が似た話は書かない。"
                      "特に『部長が手柄を横取り→第三者が判定→口ぐせが返る』の形はもう使わない：\n" + rec)
    slides, question, tags = ask_claude(kind, stage, extra)
    key = slot if not force else f"{today}_test{now().strftime('%H%M')}"
    out = OUT / key
    out.mkdir(parents=True, exist_ok=True)
    # 通し番号（シリーズとして集めたくなるように）
    no = len(st["posted"]) + 1
    label = {"sukatto": "スカッとする話", "shuraba": "修羅場の話", "donden": "どんでん返しの話", "unlucky": "ついてない話", "naki": "泣ける話"}[kind]
    series = f"{label} No.{no}" + (f"（No.{sequel_of}の続き）" if sequel_of else "")
    for i, s in enumerate(slides, 1):
        slide(s, series if i == 1 else None, kind).save(out / f"{i}.jpg", quality=90)
    # 最後に「感想を聞く」1枚（いいね・コメントは最後に押されるため）＋フォローする理由（次の話が届く）
    closing = question + "\nコメントで教えてください。\n\n身近に「こういう人」がいたら、\nこの話をそっと送ってあげてください。"
    slide(closing, "※この話はフィクションです", kind).save(out / f"{len(slides) + 1}.jpg", quality=90)
    three = 7 in slot_hours()
    h = now().hour
    nxt = "今日のお昼12時" if h < 10 else ("今夜22時" if h < 17 else ("明日の朝7時" if three else "明日のお昼12時"))
    when = "毎日7時・12時・22時に1話ずつ" if three else "毎日12時と22時に1話ずつ"
    slide(f"次の話は、\n{nxt}に更新します。\nフォローしておくと、\n見逃しません。", when, kind).save(out / f"{len(slides) + 2}.jpg", quality=90)
    caption = (f"【{series}】{slides[0]}\n\n{question} コメントで教えてね👇\n\n思い当たる人がいたら、紙ひこうきのマークでこの話を送ってみてください✈️\n\n"
               + (f"前のお話（No.{sequel_of}）は、プロフィールから読めます📌\n\n" if sequel_of else "") + f"{when}更新しています。続きを見逃さないように、フォローしておいてください📖\n\n"
               + "※AIで作った創作のお話です。実在の人物・団体とは関係ありません。\n\n"
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
    if not dry and not any(now().hour in (h, h + 1) for h in slot_hours()):
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
