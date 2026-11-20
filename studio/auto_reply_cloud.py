"""GitHub Actions 用コメント自動返信（PC不要）。返信文は Claude API（Haiku）で作る。秘密情報は環境変数から。
python auto_reply.py          … 下書きだけ表示（送らない）
python auto_reply.py --send   … 実際に返信する（1回最大10件）
返信済みのコメントIDは replied.json に記録。"""
import json, re, sys, time, datetime
from pathlib import Path
import requests
import os

ROOT = Path(__file__).resolve().parent
STATE = ROOT / "replied.json"
G = "https://graph.facebook.com/v24.0"
OLLAMA = "http://localhost:11434/api/chat"
MODEL = "qwen3:14b"
MAX_PER_RUN = 10
DAYS = 7
NG = ["死ね", "しね", "殺す", "ころす", "きもい", "キモい", "うざ", "ブス", "カス", "ゴミ", "バカ", "ばか", "アホ",
      "副業", "稼げ", "儲か", "DM", "ＤＭ", "フォロバ", "相互", "投資", "LINE", "ライン追加"]

SYSTEM = """あなたはInstagramアカウント「おもしろボケテ」の中の人です。AIで作った写真に一言ボケを付けるカルーセルを投稿しています。
フォロワーのコメントに返信します。ルール:
- 日本語で1文、30字以内。フレンドリーなタメ口寄りの丁寧語。絵文字は0〜1個。
- コメントがボケ（写真への一言）なら、具体的に面白がって褒める。「座布団1枚！」のような大喜利っぽい返しも可。
- 何枚目が面白かった、という感想なら、その枚数のボケに触れてお礼。
- 質問には分かる範囲で短く答える。分からないことは言わない。約束・宣伝・リンク・ハッシュタグは書かない。
- 写真がAIかと聞かれたら「AIで作った写真です」と正直に答える。
- 返信文だけを出力する。カギカッコや説明は付けない。
- 相手のコメントを「宣伝」「スパム」「ネタ」などと分類・説明しない。コメントの中身に直接乗っかる。
- 相手のボケを言い換えたり説明したりしない。ツッコむか、笑って褒めるか、乗っかる。
- 一番大事なのは、相手を気持ちよくさせること。「センスある」「完敗です」「本家より面白い」のように、こちらが負けを認めるくらい持ち上げる。
- 相手がまたコメントしたくなる一言を、2回に1回くらい添える（「次の写真もボケ待ってます！」など）。毎回同じ言い回しにしない。

例:
コメント: 定時で帰れるのはこいつだけ → それ一番うらやましいやつ😂完全に負けました！
コメント: 3枚目で吹いた → 3枚目わかってもらえて嬉しいです！ありがとうございます
コメント: うちの旦那もこれ → 全国の旦那さん同じでした😂教えてくれてありがとうございます
コメント: 大好評 盆栽の散歩代行（1回300円） → 本家のボケより面白いです😂次もお願いします！
コメント: これAI？ → AIで作った写真です！ボケは人力で頑張ってます"""


def load_state():
    return set(json.loads(STATE.read_text(encoding="utf-8"))) if STATE.exists() else set()


def save_state(s):
    STATE.write_text(json.dumps(sorted(s), ensure_ascii=False, indent=0), encoding="utf-8")


def boke_texts():
    """'写真番号_k' → ボケ文。"""
    m = {}
    for f in (ROOT / "boke").glob("boke*.json"):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(d, dict):
            for pid, lst in d.items():
                if isinstance(lst, list):
                    for k, t in enumerate(lst, 1):
                        if isinstance(t, str):
                            m.setdefault(f"{pid}_{k}", t.replace("\n", ""))
    return m


def post_context(media_id, texts):
    """投稿のボケ一覧（何枚目に何が書いてあるか）。"""
    p = ROOT / "posted.json"
    if not p.exists():
        return ""
    for folder, v in json.loads(p.read_text(encoding="utf-8")).get("posted", {}).items():
        if v.get("media_id") == media_id:
            items = ROOT / "posts" / folder / "items.json"
            if items.exists():
                ids = json.loads(items.read_text(encoding="utf-8"))
                return "\n".join(f"{i}枚目: {texts.get(x, '')}" for i, x in enumerate(ids, 1))
    return ""


def skip_reason(text):
    if not text.strip():
        return "空"
    if re.search(r"https?://|www\.|\.com|\.jp", text):
        return "リンク"
    if any(w in text for w in NG):
        return "NGワード"
    if re.fullmatch(r"(\s*@\S+\s*)+", text):
        return "メンションだけ"
    return ""


def make_reply(comment, context, env):
    user = f"この投稿のボケ:\n{context or '（不明）'}\n\nコメント: {comment}\n\n返信:"
    if env.get("ANTHROPIC_API_KEY"):  # .env にキーがあれば Claude、なければPCの無料AI
        r = requests.post("https://api.anthropic.com/v1/messages", timeout=60,
                          headers={"x-api-key": env["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01"},
                          json={"model": "claude-haiku-5-5", "max_tokens": 300, "thinking": {"type": "disabled"}, "system": SYSTEM,
                                "messages": [{"role": "user", "content": user}]})
        out = "".join(b.get("text", "") for b in r.json().get("content", []) if b.get("type") == "text") if r.status_code == 200 else ""
    else:
        r = requests.post(OLLAMA, json={"model": MODEL, "stream": False, "options": {"temperature": 0.7},
                                        "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user + " /no_think"}]},
                          timeout=180)
        out = r.json()["message"]["content"]
    out = re.sub(r"<think>.*?</think>", "", out, flags=re.S).strip().strip("「」\"")
    out = out.splitlines()[0].strip() if out else ""
    if not out or len(out) > 60 or re.search(r"https?://|#", out):
        return ""
    return out


def main():
    send = "--send" in sys.argv
    env = dict(os.environ)
    t, uid = env["IG_ACCESS_TOKEN"], env["IG_USER_ID"]
    me = requests.get(f"{G}/{uid}", params={"fields": "username", "access_token": t}, timeout=30).json()["username"]
    done = load_state()
    texts = boke_texts()
    since = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=DAYS)
    media = requests.get(f"{G}/{uid}/media", params={"fields": "id,timestamp,comments_count", "limit": 30, "access_token": t}, timeout=30).json().get("data", [])
    n = 0
    for m in media:
        if not m.get("comments_count"):
            continue
        cs = requests.get(f"{G}/{m['id']}/comments", params={"fields": "id,text,username,timestamp,replies{username}", "limit": 50, "access_token": t}, timeout=30).json().get("data", [])
        ctx = None
        for c in cs:
            cid, user, text = c["id"], c.get("username", ""), c.get("text", "")
            if cid in done or user == me:
                continue
            if any(r.get("username") == me for r in c.get("replies", {}).get("data", [])):
                done.add(cid); continue  # 手で返信済み
            if datetime.datetime.fromisoformat(c["timestamp"].replace("+0000", "+00:00")) < since:
                continue
            why = skip_reason(text)
            if why:
                print(f"SKIP({why}) @{user}: {text[:40]}"); done.add(cid); continue
            if ctx is None:
                ctx = post_context(m["id"], texts)
            reply = make_reply(text, ctx, env)
            if not reply:
                print(f"SKIP(返信文NG) @{user}: {text[:40]}"); continue
            print(f"@{user}: {text[:60]}\n  → {reply}")
            if send:
                r = requests.post(f"{G}/{cid}/replies", data={"message": reply, "access_token": t}, timeout=30)
                if r.status_code != 200:
                    print("  送信失敗", r.status_code, r.text[:200]); continue
                done.add(cid); save_state(done)
                time.sleep(20)
            n += 1
            if n >= MAX_PER_RUN:
                break
        if n >= MAX_PER_RUN:
            break
    if send:
        save_state(done)
    print("件数", n, "(送信)" if send else "(下書きのみ)")


if __name__ == "__main__":
    main()
