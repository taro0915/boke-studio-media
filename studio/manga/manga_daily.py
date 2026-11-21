"""1日1話の恋愛漫画カルーセルを全自動で作る（GitHub Actions 用、PC不要）。
台本: Claude Sonnet / 絵: Cloudflare Workers AI (FLUX.2 klein, キャラ設定画を参照して顔をそろえる)
事故チェック: Claude Haiku(画像を見て崩れ・文字・若すぎ等を検出、描き直し最大2回)
文字入れ: Pillow（AIに文字は描かせない）
出力: studio/manga/posts/mNNN_slug/{1..N}.jpg, caption.txt, episode.json
環境変数: ANTHROPIC_API_KEY, CF_ACCOUNT_ID, CF_API_TOKEN, (任意) MANGA_MODEL
"""
import base64, io, json, os, re, sys, time, uuid
from pathlib import Path
import requests
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent
POSTS = ROOT / "posts"
WORK = ROOT / "work"
HIST = ROOT / "history.json"
FONT_TALK = str(ROOT / "fonts" / "PatrickHand-Regular.ttf")
FONT_NARR = str(ROOT / "fonts" / "Lora.ttf")
MODEL = os.environ.get("MANGA_MODEL", "flux-2-klein-4b")
W, H = 1080, 1350
GW, GH = 832, 1040            # 生成サイズ（4:5、16の倍数）
STYLE = ("Japanese manga illustration, josei romance style, clean confident ink lines with soft watercolor coloring, "
         "cinematic composition, warm emotional lighting, mature adult characters drawn with realistic age, "
         "absolutely no text, no letters, no speech bubbles, no signature, no watermark")


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

You are the head writer of this series. Write ONE complete episode as JSON only, no commentary:
{
 "title": "short evocative title, max 6 words",
 "pattern": "which pattern number you used",
 "premise": "one sentence",
 "characters": [{"id": "A", "name": "Japanese first name", "age": 52, "gender": "woman/man",
   "look": "visual description for an illustrator: face, hair (with gray if fitting), build, signature clothing item and color, one distinctive accessory. Must be specific and stable."}],
 "panels": [
   {"scene": "what the illustrator draws: setting, time, weather, camera angle, who is in frame and what they do, emotion. No text in the image.",
    "chars": ["A"],
    "narration": "optional short line or empty string",
    "lines": [{"who": "A", "text": "short line"}]}
 ],
 "caption": "Instagram caption: 2-3 short warm lines that tease the story without spoiling, then a question that invites comments (e.g. 'Would you have said yes?')"
}
Exactly 8 panels. 2 or 3 characters total. Panel 1 must hook in one glance. Panel 8 is the emotional payoff.
Keep the main couple in most panels so readers bond with them."""


def write_episode(hist):
    recent = "\n".join(f"- {h['title']} (pattern {h.get('pattern')}): {h.get('premise', '')}" for h in hist[-30:])
    prompt = f"Previous episodes (do not repeat premise, setting or names):\n{recent or '(none yet)'}\n\nWrite the next episode."
    for _ in range(3):
        try:
            ep = parse_json(claude("claude-sonnet-5-5", STORY_SYS, prompt, 6000))
            assert len(ep["panels"]) >= 6 and ep["characters"]
            return ep
        except Exception as e:
            log("台本やり直し:", str(e)[:200])
    raise RuntimeError("台本が作れません")


# ---------- 絵 ----------
class QuotaOut(Exception):
    pass


def cf_image(prompt, refs=()):
    url = f"https://api.cloudflare.com/client/v4/accounts/{os.environ['CF_ACCOUNT_ID']}/ai/run/@cf/black-forest-labs/{MODEL}"
    files = {"prompt": (None, prompt), "width": (None, str(GW)), "height": (None, str(GH))}
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
ok=false if ANY of: deformed or extra/missing fingers, hands or limbs; broken or melted face; readable text, letters, speech bubbles or watermark in the image; characters who are supposed to be 45-65 look like young people in their 20s (unless the requested scene is explicitly a flashback to their youth); nudity or sexual content; it clearly does not match the requested scene."""


def qa(img, scene, ages):
    b64 = base64.b64encode(small_png(img, 768)).decode()
    t = claude("claude-haiku-5-5", QA_SYS, [
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}},
        {"type": "text", "text": f"Requested scene: {scene}\nCharacter ages: {ages}"}], 200)
    try:
        d = parse_json(t); return bool(d.get("ok")), d.get("issue", "")
    except Exception:
        return True, ""


def draw_with_qa(prompt, refs, scene, ages, tries=3):
    last = None
    for k in range(tries):
        img = cf_image(prompt, refs)
        ok, why = qa(img, scene, ages)
        if ok:
            return img
        log("  描き直し:", why[:120]); last = img
    return last  # 3回だめでも一番最後を使う（全部捨てると1話が作れないため）


# ---------- 文字入れ ----------
def wrap(text, font, draw, max_w):
    words, lines, cur = text.split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if draw.textlength(t, font=font) <= max_w:
            cur = t
        else:
            lines.append(cur); cur = w
    if cur:
        lines.append(cur)
    return lines


def box(d, xy, fill, outline, radius=28, width=4):
    d.rounded_rectangle(xy, radius=radius, fill=fill, outline=outline, width=width)


def letter(panel_img, narration, lines, title=None, end=False):
    im = Image.open(io.BytesIO(panel_img)).convert("RGB").resize((W, H), Image.LANCZOS).convert("RGBA")
    layer = Image.new("RGBA", im.size, (0, 0, 0, 0)); d = ImageDraw.Draw(layer)
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
    if narration:
        f = ImageFont.truetype(FONT_NARR, 40)
        nl = wrap(narration, f, d, W - 200)
        bh = len(nl) * 52 + 40; bw = max(d.textlength(x, font=f) for x in nl) + 60
        box(d, (40, 40, 40 + bw, 40 + bh), (255, 250, 238, 235), (60, 50, 40, 255), radius=10, width=3)
        y = 60
        for ln in nl:
            d.text((70, y), ln, font=f, fill=(40, 30, 25, 255)); y += 52
    if lines:
        f = ImageFont.truetype(FONT_TALK, 50)
        blocks = []
        for i, l in enumerate(lines):
            tl = wrap(l["text"], f, d, 700)
            bw = max(d.textlength(x, font=f) for x in tl) + 70; bh = len(tl) * 60 + 44
            blocks.append((tl, bw, bh, i % 2))
        y = H - 50 - sum(b[2] + 24 for b in blocks)
        for tl, bw, bh, side in blocks:
            x = 50 if side == 0 else W - 50 - bw
            box(d, (x, y, x + bw, y + bh), (255, 255, 255, 245), (20, 20, 20, 255), radius=40, width=4)
            ty = y + 20
            for ln in tl:
                d.text((x + 35, ty), ln, font=f, fill=(15, 15, 15, 255)); ty += 60
            y += bh + 24
    if end:
        f = ImageFont.truetype(FONT_NARR, 46)
        t = "— The End —"
        d.text(((W - d.textlength(t, font=f)) / 2, 50), t, font=f, fill=(255, 255, 255, 255), stroke_width=3, stroke_fill=(0, 0, 0, 200))
    out = Image.alpha_composite(im, layer).convert("RGB")
    b = io.BytesIO(); out.save(b, "JPEG", quality=92); return b.getvalue()


def end_card(img, caption_q):
    im = Image.open(io.BytesIO(img)).convert("RGB").resize((W, H), Image.LANCZOS)
    from PIL import ImageFilter
    im = im.filter(ImageFilter.GaussianBlur(18)).convert("RGBA")
    layer = Image.new("RGBA", im.size, (0, 0, 0, 120)); d = ImageDraw.Draw(layer)
    big = ImageFont.truetype(FONT_NARR, 72); mid = ImageFont.truetype(FONT_TALK, 54); sm = ImageFont.truetype(FONT_NARR, 36)
    y = 420
    for ln in ["A new love story", "every night."]:
        d.text(((W - d.textlength(ln, font=big)) / 2, y), ln, font=big, fill=(255, 245, 235, 255)); y += 95
    y += 60
    for ln in wrap(caption_q, mid, d, W - 200):
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
    # 途中で枠切れになった回は翌日に続きから再開する
    ep = json.loads(wf.read_text(encoding="utf-8")) if wf.exists() else None
    if not ep:
        ep = write_episode(hist); wf.write_text(json.dumps(ep, ensure_ascii=False, indent=1), encoding="utf-8")
        log("台本:", ep["title"], "/", ep.get("premise", ""))
    chars = {c["id"]: c for c in ep["characters"]}
    ages = ", ".join(f"{c['name']} {c['age']}" for c in ep["characters"])
    try:
        for c in ep["characters"]:
            p = WORK / f"char_{c['id']}.png"
            if not p.exists():
                prompt = f"{STYLE}. Character reference portrait, upper body, plain soft background. {c['age']}-year-old Japanese {c['gender']}: {c['look']}"
                p.write_bytes(draw_with_qa(prompt, [], "character portrait", ages)); log("キャラ", c["name"])
        for i, pn in enumerate(ep["panels"], 1):
            p = WORK / f"panel_{i}.png"
            if p.exists():
                continue
            ids = [x for x in pn.get("chars", []) if x in chars][:4]
            refs = [small_png((WORK / f"char_{x}.png").read_bytes()) for x in ids]
            who = "; ".join(f"the {chars[x]['gender']} in reference image {k + 1} is {chars[x]['name']}, {chars[x]['age']}, {chars[x]['look']}" for k, x in enumerate(ids))
            prompt = f"{STYLE}. Keep each character's face, hair and clothing exactly as in their reference image. {who}. Scene: {pn['scene']}"
            p.write_bytes(draw_with_qa(prompt, refs, pn["scene"], ages)); log("コマ", i)
    except QuotaOut as e:
        log("今日の画像の無料枠が終わりました。続きは次回。", str(e)[:120]); return 0
    # 文字入れ
    n = len(list(POSTS.glob("m[0-9][0-9][0-9]_*"))) + 1
    slug = re.sub(r"[^a-z0-9]+", "-", ep["title"].lower()).strip("-")[:30]
    out = POSTS / f"m{n:03d}_{slug}"; out.mkdir(parents=True, exist_ok=True)
    panels = [(WORK / f"panel_{i}.png").read_bytes() for i in range(1, len(ep["panels"]) + 1)]
    slides = [letter(panels[0], "", [], title=ep["title"])]
    for i, (img, pn) in enumerate(zip(panels, ep["panels"])):
        slides.append(letter(img, pn.get("narration", ""), pn.get("lines", [])[:2], end=(i == len(panels) - 1)))
    q = ep["caption"].strip().splitlines()[-1]
    slides.append(end_card(panels[-1], q if q.endswith("?") else "Follow for tomorrow's story."))
    for k, s in enumerate(slides, 1):
        (out / f"{k}.jpg").write_bytes(s)
    caption = (ep["caption"].strip() + "\n\n🇯🇵 A new love story from Japan every night. Follow so you don't miss the next one.\n"
               "Stories and art are made in Japan with AI.\n\n#romance #lovestory #manga #webcomic #comics #japan #secondchance #lovestories #romancecomic")
    (out / "caption.txt").write_text(caption, encoding="utf-8")
    (out / "episode.json").write_text(json.dumps(ep, ensure_ascii=False, indent=1), encoding="utf-8")
    hist.append({"title": ep["title"], "pattern": ep.get("pattern"), "premise": ep.get("premise"), "post": out.name})
    HIST.write_text(json.dumps(hist, ensure_ascii=False, indent=1), encoding="utf-8")
    for f in WORK.glob("*"):
        f.unlink()
    log("完成:", out.name, len(slides), "枚")
    return 0


if __name__ == "__main__":
    sys.exit(main())
