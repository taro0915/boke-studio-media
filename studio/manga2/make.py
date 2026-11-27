"""1枚1場面の「絵つきの話」（漫画）の試作を作る。投稿はしない（本人の確認用）。2026-11-27
前回の失敗への対策:
- 絵の中に文字を描かせない。セリフ・ナレーションは絵の下の帯にPillowで入れる（文字化けゼロ）
- 1枚1場面・コマ割りなし（構図を単純にして崩れを減らす）
- 人物と小道具の設定表を先に決め、毎回の絵の指示に入れる。人物は最初の設定画を参考画像として毎回渡す
- 一番賢いモデル（Opus）が1枚ずつ検品：文字・看板・指・顔・設定表との矛盾（小道具の持ち方など）。だめなら最大3回描き直し
使い方: python studio/manga2/make.py [話のフォルダ名]
環境変数: ANTHROPIC_API_KEY, CF_ACCOUNT_ID, CF_API_TOKEN, (任意) MANGA_MODEL
"""
import base64, io, json, os, re, sys, time
from pathlib import Path
import requests
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent))
import story_cloud as sc  # 日本語フォント・改行処理を使い回す

OUT = ROOT / "out"
W, H = 1080, 1350
IMG_H = 980            # 絵の高さ（下の帯に文字）
MODELS = [os.environ.get("MANGA_MODEL") or "flux-2-dev", "flux-2-klein-9b", "flux-2-klein-4b"]
STYLE = ("clean modern Japanese manga illustration, confident black ink line art with soft cel-shaded color, "
         "warm natural palette, simple uncluttered background, clear readable composition with one moment only, "
         "adult Japanese characters drawn at their real age, an original style not imitating any existing artist or work. "
         "NOT a photograph, NOT photorealistic, NOT 3D. ABSOLUTELY NO TEXT anywhere: no letters, no numbers, no signs, "
         "no labels, no speech bubbles, no captions, no panel borders, no watermark")


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def claude_raw(content, max_tokens=32000, model="claude-opus-5-5"):
    body = {"model": model, "max_tokens": max_tokens, "messages": [{"role": "user", "content": content}]}
    for i in range(4):
        r = requests.post("https://api.anthropic.com/v1/messages", json=body, timeout=300,
                          headers={"x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01"})
        if r.status_code == 200:
            t = "".join(b.get("text", "") for b in r.json()["content"] if b["type"] == "text")
            m = re.search(r"\{.*\}", t, re.S)
            if m:
                try:
                    return json.loads(m.group(0))
                except json.JSONDecodeError:
                    pass
            log("JSONなし、再試行", r.json().get("stop_reason"))
            continue
        log("claude", r.status_code, r.text[:200]); time.sleep(15 * (i + 1))
    raise RuntimeError("claude 失敗")


PLAN = """次の「1枚1文の話」を、1枚1場面の漫画（8〜10枚、コマ割りなし）にする設計図を作ってください。
条件:
- 各場面の下に入れる日本語の文（caption）は、話の流れがこの文だけで分かるようにする。1〜2文・合計40字以内・小学校高学年でも読める言葉。セリフは「」で。
- 1枚目は一番『ありえない瞬間』の絵にする（話の1枚目と同じ場面）。
- 登場人物は3人まで。日本人。各人物の見た目（髪型・髪色・服の色と形・年齢・体型・メガネ等）を英語でとても具体的に固定する。
- 話の決め手になる小道具（物）は、見た目・色・大きさ・誰がどちらの手で持つかを英語で固定する。
- 各場面の絵の指示（scene_en）は英語で、1つの瞬間だけ。誰が・どこで・何をしているか・表情・持っている物（手まで）・カメラの距離を書く。文字や数字が絵に出る物（請求書の文字、看板、画面の文字）は「紙は白紙に見えるように」「看板は無地」など、文字が描かれないよう指示する。
- 実在の人物・会社・作品・ロゴは出さない。
【話】
{story}
JSONだけを返す: {{"characters": [{{"id": "a", "role_ja": "私", "visual_en": "..."}}], "props": [{{"name": "...", "visual_en": "..."}}], "scenes": [{{"caption": "...", "scene_en": "...", "chars": ["a"], "shot": "close-up/medium/wide"}}]}}"""

QA = """You are a strict art director checking ONE illustration for a published manga carousel. Answer JSON only:
{{"ok": true/false, "issue": "short reason in English"}}
Reject (ok=false) if ANY:
- any visible text, letters, numbers, kana/kanji, signs with writing, labels, speech bubbles, watermark (blank paper and blank signs are fine)
- deformed hands, wrong number of fingers, extra or missing limbs, melted or broken face, broken eyes
- a character does not match the character sheet below (hair, clothing colors, age) or does not match the reference portrait given first (if any)
- a prop is wrong, floating, missing from the hand that should hold it, or contradicts the prop sheet
- the picture does not show the requested scene
- photo-realistic or 3D look instead of manga illustration
Character sheet: {chars}
Prop sheet: {props}
Requested scene: {scene}"""


def cf_image(prompt, refs=(), size=(1024, 928)):
    last = None
    for model in MODELS:
        url = f"https://api.cloudflare.com/client/v4/accounts/{os.environ['CF_ACCOUNT_ID']}/ai/run/@cf/black-forest-labs/{model}"
        files = {"prompt": (None, prompt), "width": (None, str(size[0])), "height": (None, str(size[1]))}
        for i, r in enumerate(refs):
            files[f"input_image_{i}"] = (f"r{i}.png", r, "image/png")
        for _ in range(2):
            resp = requests.post(url, files=files, headers={"Authorization": f"Bearer {os.environ['CF_API_TOKEN']}"}, timeout=240)
            if resp.status_code == 200:
                MODELS[:] = [model] + [m for m in MODELS if m != model]  # 使えたモデルを先頭に
                return base64.b64decode(resp.json()["result"]["image"])
            last = f"{model} {resp.status_code} {resp.text[:200]}"
            if resp.status_code in (400, 404) and ("model" in resp.text.lower() or "not found" in resp.text.lower()):
                break
            time.sleep(8)
        log("画像モデル切り替え:", last)
    raise RuntimeError(f"画像が作れません: {last}")


def png_small(b, size=768):
    im = Image.open(io.BytesIO(b)).convert("RGB"); im.thumbnail((size, size))
    o = io.BytesIO(); im.save(o, "PNG"); return o.getvalue()


def img_block(b):
    return {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": base64.b64encode(png_small(b)).decode()}}


def check(img, scene, chars, props, ref=None):
    content = ([img_block(ref), {"type": "text", "text": "Reference portrait of the main character:"}] if ref else []) + [
        img_block(img), {"type": "text", "text": QA.format(chars=chars, props=props, scene=scene)}]
    d = claude_raw(content, max_tokens=16000)
    return bool(d.get("ok")), d.get("issue", "")


def draw(prompt, scene, chars, props, refs=(), ref_for_qa=None, tries=3, size=(1024, 928)):
    fix, best = "", None
    for t in range(tries):
        img = cf_image(prompt + fix, refs, size)
        ok, why = check(img, scene, chars, props, ref_for_qa)
        log(f"  {'OK' if ok else 'NG'} {why[:140]}")
        if ok:
            return img, True
        best = img
        fix = f" IMPORTANT fix from previous attempt: {why}"
    return best, False


def compose(img_bytes, caption, n, total):
    im = Image.new("RGB", (W, H), (250, 247, 240))
    art = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    # 横幅に合わせて拡大し、上下を切りそろえる
    r = W / art.width
    art = art.resize((W, int(art.height * r)), Image.LANCZOS)
    top = max(0, (art.height - IMG_H) // 2)
    im.paste(art.crop((0, top, W, top + IMG_H)), (0, 0))
    d = ImageDraw.Draw(im)
    d.line([(0, IMG_H), (W, IMG_H)], fill=(60, 60, 60), width=3)
    f = sc.font(50)
    lines = sum((sc.wrap_ja(d, t, f, W - 140) for t in caption.split("\n") if t), [])
    lh = 78
    y = IMG_H + (H - IMG_H - 40 - lh * len(lines)) // 2
    for ln in lines:
        d.text(((W - d.textlength(ln, font=f)) / 2, y), ln, font=f, fill=(30, 30, 30))
        y += lh
    s = sc.font(24)
    d.text((W - 40 - d.textlength(f"{n}/{total}", font=s), H - 44), f"{n}/{total}", font=s, fill=(140, 140, 140))
    return im


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else "2026-11-26_12"
    story = json.loads((ROOT.parent / "stories" / src / "story.json").read_text(encoding="utf-8"))["slides"]
    out = OUT / (time.strftime("%Y%m%d_%H%M") + "_" + src)
    out.mkdir(parents=True, exist_ok=True)
    plan = claude_raw(PLAN.format(story="\n".join(story)))
    (out / "plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
    chars = "; ".join(f"[{c['id']}] {c['role_ja']}: {c['visual_en']}" for c in plan["characters"])
    props = "; ".join(f"{p['name']}: {p['visual_en']}" for p in plan.get("props", []))
    log("人物:", chars); log("小道具:", props)
    # 人物の設定画（参考画像）
    portraits = {}
    for c in plan["characters"]:
        p = f"{STYLE}. Character reference portrait, upper body, facing slightly to the side, plain light background. {c['visual_en']}"
        img, ok = draw(p, f"portrait of {c['role_ja']}: {c['visual_en']}", chars, props, size=(768, 960))
        portraits[c["id"]] = img
        (out / f"char_{c['id']}.png").write_bytes(img)
        log("設定画", c["role_ja"], ok)
    report = []
    scenes = plan["scenes"]
    for i, s in enumerate(scenes, 1):
        ids = [x for x in s.get("chars", []) if x in portraits][:2]
        refs = [png_small(portraits[x], 640) for x in ids]
        who = " ".join(f"Reference image {k+1} shows {next(c['role_ja'] for c in plan['characters'] if c['id']==x)} ({next(c['visual_en'] for c in plan['characters'] if c['id']==x)}): keep exactly the same face, hair and clothes."
                       for k, x in enumerate(ids))
        p = f"{STYLE}. {s.get('shot', 'medium')} shot. {who} Props: {props}. Scene: {s['scene_en']}"
        log(f"場面{i}/{len(scenes)}", s["caption"])
        img, ok = draw(p, s["scene_en"], chars, props, refs, portraits[ids[0]] if ids else None)
        (out / f"raw_{i}.png").write_bytes(img)
        compose(img, s["caption"], i, len(scenes)).save(out / f"{i}.jpg", quality=90)
        report.append({"n": i, "caption": s["caption"], "qa_ok": ok})
    (out / "report.json").write_text(json.dumps({"models": MODELS[0], "pages": report}, ensure_ascii=False, indent=1), encoding="utf-8")
    log("完成:", out, "検品OK", sum(r["qa_ok"] for r in report), "/", len(report))


if __name__ == "__main__":
    main()
