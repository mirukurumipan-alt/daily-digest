"""RSSを集めて docs/data/news.json を作る(標準ライブラリのみ)。

GEMINI_API_KEY があれば日本語要約・英語記事の翻訳を行い、
なければRSS付属の概要文をそのまま使う。
使い方: python collect.py [--check]  (--check はフィードの疎通確認のみ)
"""
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

ROOT = Path(__file__).parent
UA = "Mozilla/5.0 (compatible; DailyDigest/1.0)"
JST = timezone(timedelta(hours=9))


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read()


def local(tag):
    return tag.rsplit("}", 1)[-1]


def clean(text, limit=220):
    text = re.sub(r"<[^>]+>", " ", html.unescape(text or ""))
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit] + ("…" if len(text) > limit else "")


def parse_date(s):
    if not s:
        return None
    try:
        d = parsedate_to_datetime(s)
    except (TypeError, ValueError):
        try:
            d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def parse_feed(data):
    root = ET.fromstring(data)
    items = []
    for el in root.iter():
        if local(el.tag) not in ("item", "entry"):
            continue
        f = {}
        for c in el:
            name = local(c.tag)
            if name == "link":
                f.setdefault("link", c.get("href") or (c.text or "").strip())
            elif name in ("title", "description", "summary", "content", "pubDate", "published", "updated", "date"):
                f.setdefault(name, c.text or "")
        date = parse_date(f.get("pubDate") or f.get("published") or f.get("updated") or f.get("date"))
        if f.get("title") and f.get("link"):
            items.append({
                "title": clean(f["title"], 200),
                "link": f["link"],
                "date": date,
                "desc": clean(f.get("description") or f.get("summary") or f.get("content")),
            })
    return items


def load_feed(feed):
    try:
        return feed, parse_feed(fetch(feed["url"])), None
    except Exception as e:  # 1つ落ちても全体は続行
        return feed, [], f"{type(e).__name__}: {e}"


def norm(t):
    return re.sub(r"[\s\W_]+", "", t.lower())[:40]


def select(cfg, results, limit):
    now = datetime.now(timezone.utc)
    exclude = re.compile(cfg["exclude_title"]) if cfg.get("exclude_title") else None
    seen, out = set(), []
    for cat in cfg["categories"]:
        pools = []
        for feed in cat["feeds"]:
            cutoff = now - timedelta(hours=feed.get("max_age_hours", cfg["max_age_hours"]))
            items = [i for i in results[feed["url"]]
                     if (i["date"] is None or i["date"] >= cutoff) and not (exclude and exclude.search(i["title"]))]
            items.sort(key=lambda i: i["date"] or cutoff, reverse=True)
            for i in items:
                i.update(source=feed["name"], lang=feed["lang"])
            pools.append(items)
        picked = []
        # 媒体ごとに1本ずつ交互に取り、偏りを避ける
        while len(picked) < limit and any(pools):
            for p in pools:
                while p:
                    i = p.pop(0)
                    key = norm(i["title"])
                    if key in seen:
                        continue
                    seen.add(key)
                    picked.append(i)
                    break
                if len(picked) >= limit:
                    break
        for i in picked:
            out.append({
                "category": cat["id"],
                "title": i["title"],
                "summary": i["desc"],
                "link": i["link"],
                "source": i["source"],
                "lang": i["lang"],
                "date": (i["date"] or now).astimezone(JST).isoformat(timespec="minutes"),
            })
    return out


def pick_model(key):
    """使えるモデルをAPIに問い合わせ、最新の安定版Flashを選ぶ(モデル名の廃止対策)。"""
    fallback = "gemini-flash-latest"
    try:
        req = urllib.request.Request(
            "https://generativelanguage.googleapis.com/v1beta/models?pageSize=200",
            headers={"x-goog-api-key": key})
        with urllib.request.urlopen(req, timeout=30) as r:
            models = json.load(r).get("models", [])
    except Exception as e:
        print(f"[warn] モデル一覧の取得失敗、{fallback} を使用: {type(e).__name__}: {e}", file=sys.stderr)
        return [fallback]
    found = []
    for m in models:
        match = re.fullmatch(r"models/(gemini-(\d+(?:\.\d+)?)-flash)(-lite)?", m["name"])
        if match and "generateContent" in m.get("supportedGenerationMethods", []):
            rank = (float(match.group(2)), not match.group(3))  # 新しい版を優先、同版ならlite以外
            found.append((rank, match.group(1) + (match.group(3) or "")))
    if not found:
        print(f"[warn] 該当モデルなし、{fallback} を使用。利用可能: {[m['name'] for m in models][:20]}", file=sys.stderr)
        return [fallback]
    # 混雑時に切り替える候補として、新しい順に最大3つ返す
    return [name for _, name in sorted(found, reverse=True)[:3]]


def summarize(items, names, batch=30):
    """関連度(score 0-10)・日本語見出し・要約を付ける。成功した記事数を返す。"""
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        return 0
    models = [os.environ["GEMINI_MODEL"]] if os.environ.get("GEMINI_MODEL") else pick_model(key)
    print(f"使用モデル候補: {models}")
    cur = 0
    done = 0
    for start in range(0, len(items), batch):
        chunk = items[start:start + batch]
        src = [{"id": n, "category": names[i["category"]], "lang": i["lang"], "title": i["title"], "text": i["summary"]}
               for n, i in enumerate(chunk)]
        prompt = (
            "あなたは日本の医療・歯科従事者向けニュースダイジェストの編集者です。"
            "次の記事それぞれについて、以下を作ってください。\n"
            "- score: 記事が割り当てられた分野(category)に合致し、読む価値がある度合い(0〜10の整数)。"
            "読者は医療・歯科・ビジネス・政策・テクノロジーに関心がある社会人で、娯楽より仕事や社会の判断に役立つ情報を求めている。"
            "次は3以下にすること: 分野と無関係な記事、広告・求人・セミナー告知、地域の小さな話題、園芸や趣味の記事、"
            "芸能・スポーツ・ゴシップ、個人の相談事例や身の上話、個別の事件・事故(社会全体に影響しないもの)。"
            "制度変更・市場動向・統計・重要な技術や国際的な出来事・大規模な災害やサイバー攻撃は7以上。"
            "迷ったら低めにつけること。\n"
            "- title: 日本語の見出し(英語記事は翻訳。媒体名の接尾辞は除く)\n"
            "- summary: 要点を伝える日本語の要約(80〜120字)。本文が短い場合は書かれた事実のみ、推測や補足をしない。\n"
            'JSON配列 [{"id":数値,"score":数値,"title":"","summary":""}] のみを出力。\n\n'
            + json.dumps(src, ensure_ascii=False)
        )
        body = json.dumps({
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json"},
        }).encode()
        text = None
        # 混雑(429/500/503)は待って再試行し、だめなら次の候補モデルへ切り替える
        while cur < len(models) and text is None:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{models[cur]}:generateContent"
            req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json", "x-goog-api-key": key})
            for attempt in range(3):
                try:
                    with urllib.request.urlopen(req, timeout=120) as r:
                        text = json.load(r)["candidates"][0]["content"]["parts"][0]["text"]
                    break
                except Exception as e:
                    detail = e.read().decode("utf-8", "replace")[:300] if isinstance(e, urllib.error.HTTPError) else ""
                    print(f"[warn] {models[cur]} 失敗({attempt + 1}/3): {type(e).__name__}: {e} {detail}", file=sys.stderr)
                    if not (isinstance(e, urllib.error.HTTPError) and e.code in (429, 500, 503)):
                        break
                    time.sleep(15 * (attempt + 1))
            if text is None:
                cur += 1
        try:
            for row in json.loads(text or "[]"):
                it = chunk[row["id"]]
                it["score"], it["title_ja"], it["summary"] = int(row["score"]), row["title"], row["summary"]
                done += 1
        except Exception as e:
            print(f"[warn] 応答の解析失敗: {type(e).__name__}: {e}", file=sys.stderr)
        else:
            if text is None:
                print(f"[warn] 候補モデルすべて失敗(この{len(chunk)}件はRSS概要・関連度判定なし)", file=sys.stderr)
    return done


def finalize(cfg, items):
    """関連度の低い記事を除き、分野ごとに上位だけ残す。0本の分野はそのまま空にする。"""
    keep = []
    for cat in cfg["categories"]:
        mine = [i for i in items if i["category"] == cat["id"]]
        scored = [i for i in mine if "score" in i]
        # 判定できなかった記事は、判定済みの合格記事で枠が埋まらないときだけ補う
        passed = sorted((i for i in scored if i["score"] >= cfg["min_score"]), key=lambda i: -i["score"])
        rest = [i for i in mine if "score" not in i]
        keep += (passed + rest)[:cfg["per_category"]]
    for i in keep:
        i.pop("score", None)
    return keep


def main():
    cfg = json.loads((ROOT / "feeds.json").read_text(encoding="utf-8"))
    feeds = [f for c in cfg["categories"] for f in c["feeds"]]
    with ThreadPoolExecutor(8) as ex:
        loaded = list(ex.map(load_feed, feeds))
    results = {}
    for feed, items, err in loaded:
        results[feed["url"]] = items
        status = f"NG {err}" if err else f"OK {len(items)}件"
        print(f"{status:40} {feed['name']}")
    if "--check" in sys.argv:
        return
    items = select(cfg, results, cfg["candidates_per_category"])
    names = {c["id"]: c["name"] for c in cfg["categories"]}
    ai = summarize(items, names) > 0
    items = finalize(cfg, items)
    out = {
        "generated_at": datetime.now(JST).isoformat(timespec="minutes"),
        "ai_summary": ai,
        "categories": [{"id": c["id"], "name": c["name"]} for c in cfg["categories"]],
        "items": items,
    }
    path = ROOT / "docs" / "data" / "news.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(items)}件を {path} に出力(AI要約: {ai})")


if __name__ == "__main__":
    main()
