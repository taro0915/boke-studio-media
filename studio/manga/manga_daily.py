"""1日1話の恋愛漫画カルーセルを全自動で作る（GitHub Actions 用、PC不要）。
台本: Claude Sonnet / 絵: Cloudflare Workers AI (FLUX.2 klein, キャラ設定画を参照して顔をそろえる)
事故チェック: Claude Haiku(崩れ・文字・若すぎ等を検出、描き直し最大2回)
コマ割り: 1ページに1〜3コマ（見せ場は大ゴマ）。文字はPillowで入れる（AIに文字は描かせない）
出力: studio/manga/posts/mNNN_slug/{1..N}.jpg, caption.txt, episode.json
環境変数: ANTHROPIC_API_KEY, CF_ACCOUNT_ID, CF_API_TOKEN, (任意) MANGA_MODEL
"""
import base64, io, json, math, os, re, sys, time
from pathlib import Path
import requests
from PIL import Image, ImageDraw, ImageFont, ImageFilter

ROOT = Path(__file__).resolve().parent
POSTS = ROOT / "posts"
WORK = ROOT / "work"
HIST = ROOT / "history.json"
FONT_TALK = str(ROOT / "fonts" / "PatrickHand-Regular.ttf")
FONT_NARR = str(ROOT / "fonts" / "Lora.ttf")
MODEL = os.environ.get("MANGA_MODEL", "flux-2-klein-4b")
W, H = 1080, 1350
M, G = 22, 16                 # 外枠の余白・コマ間
# 絵柄の基準画像。毎回これを1枚目の参考画像として渡し、全話の絵柄をそろえる（人物は真似させない）
STYLE_REF = ROOT / "style_ref.png"
STYLE_NOTE = ("Reference image 1 is ONLY a style guide: copy its drawing style, line weight, coloring, shading and palette exactly, "
              "but do NOT copy its people, faces, pose or scene.")
STYLE = ("hand-drawn Japanese manga panel by a professional josei manga artist, drawn with a G-pen nib: black ink line art "
         "with varied line weight and slightly imperfect hand-made strokes, hatching and screentone shading, "
         "colored afterwards with alcohol markers in soft flat tones, simple manga backgrounds, expressive manga eyes, "
         "classic romance-manga conventions: delicate thin lines on faces, soft gradient screentones, wind-blown hair, "
         "sparkles or scattered petals behind characters at emotional peaks, tender close-ups of eyes and hands, "
         "an original art style that does not imitate any specific artist or existing work, "
         "mature adult characters drawn with their real age. NOT a photograph, NOT photorealistic, NOT a 3D render, "
         "NOT a digital painting, no glossy airbrushed AI look. "
         "Absolutely no text, no letters, no speech bubbles, no panel borders, no signature, no watermark")


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


# ---------- Claude ----------
def claude(model, system, content, max_tokens):
    body = {"model": model, "max_tokens": max_tokens, "system": system, "messages": [{"role": "user", "content": content}]}
    if "haiku" in model:
        body["thinking"] = {"type": "disabled"}
    for i in range(4):
        r = requests.post("https://api.anthropic.com/v1/messages", json=body, timeout=300,
                          headers={"x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01"})
        if r.status_code == 200:
            return "".join(b.get("text", "") for b in r.json()["content"] if b["type"] == "text")
        if r.status_code in (429, 500, 529):
            time.sleep(15 * (i + 1)); continue
        raise RuntimeError(f"claude {r.status_code}: {r.text[:300]}")
    raise RuntimeError("claude retry over")


def parse_json(t):
    return json.loads(re.search(r"\{.*\}", t, re.S).group(0))


# ---------- 台本 ----------
STORY_SYS = (ROOT / "bible.md").read_text(encoding="utf-8") + """

You are the head writer AND the storyboard artist of this series. Write ONE complete episode as JSON only, no commentary:
{
 "title": "short evocative title, max 6 words",
 "pattern": "which pattern number you used",
 "premise": "one sentence",
 "characters": [{"id": "A", "name": "Japanese first name", "age": 52, "gender": "woman/man",
   "look": "visual description for an illustrator: face, hair (with gray if fitting), build, signature clothing item and color, one distinctive accessory. Specific and stable."}],
 "pages": [
   {"panels": [
     {"shot": "wide / medium / close-up / extreme close-up (eyes, hands, an object)",
      "scene": "what the illustrator draws: setting, time, weather, camera angle, who is in frame, action, emotion. No text in the image.",
      "chars": ["A"],
      "narration": "optional short line or empty string",
      "lines": [{"who": "A", "text": "short line"}]}
   ]}
 ],
 "caption": "Instagram caption: 2-3 short warm lines that tease the story without spoiling, then a question that invites comments (e.g. 'Would you have said yes?')"
}
Storyboard like a real manga, paced for a phone screen:
- 6 or 7 pages. Page 1 = exactly 1 splash panel that hooks in one glance (the title goes on it). Last page = exactly 1 splash panel, the emotional payoff.
- Middle pages have 2 or 3 panels. Mix shot sizes on each page (e.g. wide establishing shot, then a close-up of a face or a hand or an object). Use 1-panel splash once more in the middle for the biggest moment if needed.
- 12 to 15 panels in total. Many panels need no text at all; silence is powerful.
- A panel has at most 1 narration line and at most 2 speech lines, each under 12 words.
- 2 or 3 characters total. Keep the main couple in most panels."""


def write_episode(hist):
    recent = "\n".join(f"- {h['title']} (pattern {h.get('pattern')}): {h.get('premise', '')}" for h in hist[-30:])
    prompt = f"Previous episodes (do not repeat premise, setting or names):\n{recent or '(none yet)'}\n\nWrite the next episode."
    for _ in range(3):
        try:
            ep = parse_json(claude("claude-sonnet-5-5", STORY_SYS, prompt, 8000))
            pages = ep["pages"]
            assert 5 <= len(pages) <= 8 and ep["characters"]
            for pg in pages:
                pg["panels"] = pg["panels"][:3]
                assert pg["panels"]
            pages[0]["panels"] = pages[0]["panels"][:1]
            pages[-1]["panels"] = pages[-1]["panels"][:1]
            return ep
        except Exception as e:
            log("台本やり直し:", str(e)[:200])
    raise RuntimeError("台本が作れません")


# ---------- コマ割り ----------
def page_boxes(n):
    """1ページ内のコマの枠 (x0,y0,x1,y1)。1=全面、2=上下、3=上に横長+下に2つ。"""
    if n == 1:
        return [(0, 0, W, H)]
    iw, ih = W - 2 * M, H - 2 * M
    if n == 2:
        h1 = int((ih - G) * 0.46)
        return [(M, M, W - M, M + h1), (M, M + h1 + G, W - M, H - M)]
    h1 = int((ih - G) * 0.44); y2 = M + h1 + G; w2 = (iw - G) // 2
    return [(M, M, W - M, M + h1), (M, y2, M + w2, H - M), (M + w2 + G, y2, W - M, H - M)]


def gen_size(box, area=860_000):
    a = (box[2] - box[0]) / (box[3] - box[1])
    gw = max(512, round(math.sqrt(area * a) / 16) * 16)
    gh = max(512, round(gw / a / 16) * 16)
    return gw, gh


# ---------- 絵 ----------
class QuotaOut(Exception):
    pass


def cf_image(prompt, refs=(), size=(832, 1040)):
    url = f"https://api.cloudflare.com/client/v4/accounts/{os.environ['CF_ACCOUNT_ID']}/ai/run/@cf/black-forest-labs/{MODEL}"
    files = {"prompt": (None, prompt), "width": (None, str(size[0])), "height": (None, str(size[1]))}
    for i, r in enumerate(refs):
        files[f"input_image_{i}"] = (f"r{i}.png", r, "image/png")
    for i in range(3):
        resp = requests.post(url, files=files, headers={"Authorization": f"Bearer {os.environ['CF_API_TOKEN']}"}, timeout=180)
        if resp.status_code == 200:
            return base64.b64decode(resp.json()["result"]["image"])
        if resp.status_code == 429 or "neurons" in resp.text:
            raise QuotaOut(resp.text[:200])
        log("画像エラー", resp.status_code, resp.text[:200]); time.sleep(10)
    raise RuntimeError("画像が作れません")


def small_png(img_bytes, size=500):
    im = Image.open(io.BytesIO(img_bytes)).convert("RGB"); im.thumbnail((size, size))
    b = io.BytesIO(); im.save(b, "PNG"); return b.getvalue()


QA_SYS = """You check one manga panel before it is published. Answer JSON only: {"ok": true/false, "issue": ""}.
ok=false if ANY of: it looks like a photograph, a photorealistic image, a 3D render or a glossy digital painting instead of hand-drawn manga line art with marker coloring; deformed or extra/missing fingers, hands or limbs; broken or melted face; readable text, letters, speech bubbles or watermark in the image; characters who are supposed to be 45-65 look like young people in their 20s (unless the requested scene is explicitly a flashback to their youth); nudity or sexual content; it clearly does not match the requested scene."""


def qa(img, scene, ages):
    b64 = base64.b64encode(small_png(img, 768)).decode()
    t = claude("claude-haiku-5-5", QA_SYS, [
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}},
        {"type": "text", "text": f"Requested scene: {scene}\nCharacter ages: {ages}"}], 200)
    try:
        d = parse_json(t); return bool(d.get("ok")), d.get("issue", "")
    except Exception:
        return True, ""


def draw_with_qa(prompt, refs, scene, ages, size, tries=3):
    last = None
    for _ in range(tries):
        img = cf_image(prompt, refs, size)
        ok, why = qa(img, scene, ages)
        if ok:
            return img
        log("  描き直し:", why[:120]); last = img
    return last  # 3回だめでも最後の絵を使う（1話を落とさないため）


# ---------- 文字入れ ----------
def wrap(text, font, draw, max_w):
    lines, cur = [], ""
    for w in text.split():
        t = (cur + " " + w).strip()
        if draw.textlength(t, font=font) <= max_w:
            cur = t
        else:
            lines.append(cur); cur = w
    if cur:
        lines.append(cur)
    return lines


def paper(im):
    """紙の質感を薄く乗せて、手描き原稿らしくする。"""
    import random
    rnd = random.Random(7)
    g = Image.new("L", (im.width // 3, im.height // 3))
    g.putdata([rnd.randint(118, 138) for _ in range(g.width * g.height)])
    g = g.resize(im.size, Image.BICUBIC).filter(ImageFilter.GaussianBlur(0.8))
    tex = Image.merge("RGB", (g, g, g))
    from PIL import ImageChops
    return Image.blend(im, ImageChops.overlay(im, tex), 0.35)


def fill_crop(img_bytes, w, h):
    im = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    s = max(w / im.width, h / im.height)
    im = im.resize((math.ceil(im.width * s), math.ceil(im.height * s)), Image.LANCZOS)
    x, y = (im.width - w) // 2, (im.height - h) // 2
    return paper(im.crop((x, y, x + w, y + h)))


def text_on_panel(d, box, narration, lines, big):
    x0, y0, x1, y1 = box
    pw = x1 - x0
    y = y0 + 18
    if narration:
        f = ImageFont.truetype(FONT_NARR, 40 if big else 34)
        nl = wrap(narration, f, d, pw - 90)
        lh = f.size + 12
        bw = max(d.textlength(t, font=f) for t in nl) + 44; bh = len(nl) * lh + 28
        d.rounded_rectangle((x0 + 18, y, x0 + 18 + bw, y + bh), radius=8, fill=(255, 250, 238, 240), outline=(60, 50, 40, 255), width=3)
        ty = y + 14
        for t in nl:
            d.text((x0 + 40, ty), t, font=f, fill=(40, 30, 25, 255)); ty += lh
        y += bh + 14
    if not lines:
        return
    f = ImageFont.truetype(FONT_TALK, 50 if big else 42)
    lh = f.size + 10
    blocks = []
    for i, l in enumerate(lines[:2]):
        tl = wrap(l["text"], f, d, min(680, pw - 110))
        bw = max(d.textlength(t, font=f) for t in tl) + 60; bh = len(tl) * lh + 36
        blocks.append((tl, bw, bh, i % 2))
    if big:  # 大ゴマは下側にセリフ
        y = y1 - 40 - sum(b[2] + 18 for b in blocks)
    for tl, bw, bh, side in blocks:
        x = x0 + 22 if side == 0 else x1 - 22 - bw
        d.rounded_rectangle((x, y, x + bw, y + bh), radius=38, fill=(255, 255, 255, 248), outline=(20, 20, 20, 255), width=4)
        ty = y + 18
        for t in tl:
            d.text((x + 30, ty), t, font=f, fill=(15, 15, 15, 255)); ty += lh
        y += bh + 18


def compose_page(imgs, panels, title=None, end=False):
    boxes = page_boxes(len(panels))
    page = Image.new("RGB", (W, H), (250, 248, 244))
    for img, box in zip(imgs, boxes):
        page.paste(fill_crop(img, box[2] - box[0], box[3] - box[1]), box[:2])
    page = page.convert("RGBA")
    layer = Image.new("RGBA", page.size, (0, 0, 0, 0)); d = ImageDraw.Draw(layer)
    if len(boxes) > 1:
        for b in boxes:
            d.rectangle(b, outline=(15, 15, 15, 255), width=5)
    for pn, box in zip(panels, boxes):
        text_on_panel(d, box, "" if title else pn.get("narration", ""), [] if title else pn.get("lines", []), big=len(boxes) == 1)
    if title:
        f = ImageFont.truetype(FONT_NARR, 84)
        try:
            f.set_variation_by_axes([700])
        except Exception:
            pass
        tl = wrap(title, f, d, W - 160)
        h = len(tl) * 100 + 150
        d.rectangle((0, H - h - 60, W, H), fill=(15, 12, 20, 170))
        y = H - h - 20
        for ln in tl:
            d.text(((W - d.textlength(ln, font=f)) / 2, y), ln, font=f, fill=(255, 245, 235, 255)); y += 100
        s = ImageFont.truetype(FONT_NARR, 38)
        sub = "A love story from Japan  ·  swipe to read"
        d.text(((W - d.textlength(sub, font=s)) / 2, y + 20), sub, font=s, fill=(240, 200, 210, 255))
    out = Image.alpha_composite(page, layer).convert("RGB")
    b = io.BytesIO(); out.save(b, "JPEG", quality=92); return b.getvalue()


def end_card(img, question):
    im = fill_crop(img, W, H).filter(ImageFilter.GaussianBlur(18)).convert("RGBA")
    layer = Image.new("RGBA", im.size, (0, 0, 0, 120)); d = ImageDraw.Draw(layer)
    big = ImageFont.truetype(FONT_NARR, 72); mid = ImageFont.truetype(FONT_TALK, 54); sm = ImageFont.truetype(FONT_NARR, 36)
    t = "— The End —"
    d.text(((W - d.textlength(t, font=sm)) / 2, 300), t, font=sm, fill=(255, 235, 240, 255))
    y = 420
    for ln in ["A new love story", "every night."]:
        d.text(((W - d.textlength(ln, font=big)) / 2, y), ln, font=big, fill=(255, 245, 235, 255)); y += 95
    y += 60
    for ln in wrap(question, mid, d, W - 200):
        d.text(((W - d.textlength(ln, font=mid)) / 2, y), ln, font=mid, fill=(255, 215, 225, 255)); y += 66
    t = "Stories & art made in Japan with AI"
    d.text(((W - d.textlength(t, font=sm)) / 2, H - 120), t, font=sm, fill=(230, 230, 230, 255))
    out = Image.alpha_composite(im, layer).convert("RGB")
    b = io.BytesIO(); out.save(b, "JPEG", quality=92); return b.getvalue()


# ---------- 本体 ----------
def main():
    hist = json.loads(HIST.read_text(encoding="utf-8")) if HIST.exists() else []
    WORK.mkdir(parents=True, exist_ok=True)
    wf = WORK / "episode.json"
    ep = json.loads(wf.read_text(encoding="utf-8")) if wf.exists() else None   # 枠切れの回は続きから
    if not ep or "pages" not in ep:
        ep = write_episode(hist); wf.write_text(json.dumps(ep, ensure_ascii=False, indent=1), encoding="utf-8")
        log("台本:", ep["title"], "/", ep.get("premise", ""))
    chars = {c["id"]: c for c in ep["characters"]}
    ages = ", ".join(f"{c['name']} {c['age']}" for c in ep["characters"])
    try:
        for c in ep["characters"]:
            p = WORK / f"char_{c['id']}.png"
            if not p.exists():
                sref = [small_png(STYLE_REF.read_bytes())] if STYLE_REF.exists() else []
                prompt = (f"{STYLE}. {STYLE_NOTE if sref else ''} Character reference portrait, upper body, plain soft background. "
                          f"{c['age']}-year-old Japanese {c['gender']}: {c['look']}")
                p.write_bytes(draw_with_qa(prompt, sref, "character portrait", ages, (768, 960))); log("キャラ", c["name"])
        for pi, pg in enumerate(ep["pages"], 1):
            boxes = page_boxes(len(pg["panels"]))
            for ki, (pn, box) in enumerate(zip(pg["panels"], boxes), 1):
                p = WORK / f"p{pi}_{ki}.png"
                if p.exists():
                    continue
                ids = [x for x in pn.get("chars", []) if x in chars][:3]
                refs = [small_png((WORK / f"char_{x}.png").read_bytes()) for x in ids]
                off = 1 if STYLE_REF.exists() else 0
                who = "; ".join(f"the {chars[x]['gender']} in reference image {k + 1 + off} is {chars[x]['name']}, {chars[x]['age']}, {chars[x]['look']}" for k, x in enumerate(ids))
                prompt = (f"{STYLE}. {STYLE_NOTE if off else ''} {pn.get('shot', 'medium')} shot. Keep each character's face, hair and clothing exactly as in their reference image. "
                          f"{who}. Scene: {pn['scene']}")
                if off:
                    refs = [small_png(STYLE_REF.read_bytes())] + refs
                p.write_bytes(draw_with_qa(prompt, refs, pn["scene"], ages, gen_size(box))); log("コマ", pi, ki)
    except QuotaOut as e:
        log("今日の画像の無料枠が終わりました。続きは次回。", str(e)[:120]); return 0
    n = len(list(POSTS.glob("m[0-9][0-9][0-9]_*"))) + 1
    slug = re.sub(r"[^a-z0-9]+", "-", ep["title"].lower()).strip("-")[:30]
    out = POSTS / f"m{n:03d}_{slug}"; out.mkdir(parents=True, exist_ok=True)
    slides, last_img = [], None
    for pi, pg in enumerate(ep["pages"], 1):
        imgs = [(WORK / f"p{pi}_{ki}.png").read_bytes() for ki in range(1, len(pg["panels"]) + 1)]
        last_img = imgs[-1]
        if pi == 1:
            slides.append(compose_page(imgs, pg["panels"], title=ep["title"]))
            if pg["panels"][0].get("narration") or pg["panels"][0].get("lines"):   # 表紙のセリフは次のページで見せる
                slides.append(compose_page(imgs, pg["panels"]))
        else:
            slides.append(compose_page(imgs, pg["panels"], end=(pi == len(ep["pages"]))))
    q = ep["caption"].strip().splitlines()[-1]
    slides.append(end_card(last_img, q if q.endswith("?") else "Follow for tomorrow's story."))
    for k, s in enumerate(slides[:20], 1):
        (out / f"{k}.jpg").write_bytes(s)
    caption = (ep["caption"].strip() + "\n\n🇯🇵 A new love story from Japan every night. Follow so you don't miss the next one.\n"
               "Stories and art are made in Japan with AI.\n\n#romance #lovestory #manga #webcomic #comics #japan #secondchance #lovestories #romancecomic")
    (out / "caption.txt").write_text(caption, encoding="utf-8")
    (out / "episode.json").write_text(json.dumps(ep, ensure_ascii=False, indent=1), encoding="utf-8")
    hist.append({"title": ep["title"], "pattern": ep.get("pattern"), "premise": ep.get("premise"), "post": out.name})
    HIST.write_text(json.dumps(hist, ensure_ascii=False, indent=1), encoding="utf-8")
    if not STYLE_REF.exists():   # 第1話の表紙を絵柄の基準にして、2話目以降をそろえる
        STYLE_REF.write_bytes((WORK / "p1_1.png").read_bytes())
    for f in WORK.glob("*"):
        f.unlink()
    log("完成:", out.name, len(slides), "枚")
    return 0


if __name__ == "__main__":
    sys.exit(main())
