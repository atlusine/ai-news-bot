"""Daily AI news -> Telegram. Free: RSS + optional Gemini free tier."""
import calendar
import html
import json
import os
import re
import time
from datetime import datetime, timezone

import feedparser
import requests

FEEDS = {
    "OpenAI": "https://openai.com/news/rss.xml",
    "Google AI": "https://blog.google/technology/ai/rss/",
    "DeepMind": "https://deepmind.google/blog/rss.xml",
    "Hugging Face": "https://huggingface.co/blog/feed.xml",
    "TechCrunch": "https://techcrunch.com/category/artificial-intelligence/feed/",
    "The Verge": "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml",
    "MIT Tech Review": "https://www.technologyreview.com/topic/artificial-intelligence/feed",
    "Hacker News": "https://hnrss.org/newest?q=AI+OR+LLM+OR+Claude+OR+GPT&points=150",
}
OFFICIAL = {"OpenAI", "Google AI", "DeepMind", "Hugging Face"}
KEYWORDS = {
    "launch": 3, "release": 3, "introduc": 3, "announce": 2, "model": 2,
    "claude": 3, "anthropic": 3, "openai": 3, "gpt": 3, "gemini": 3, "llama": 2,
    "agent": 2, "open-source": 2, "open source": 2, "funding": 1, "acquire": 2,
    "regulation": 2, "ai act": 2, "benchmark": 1,
}
MAX_ITEMS = int(os.getenv("MAX_ITEMS", "7"))
HOURS = int(os.getenv("LOOKBACK_HOURS", "26"))


def fetch_items():
    cutoff = time.time() - HOURS * 3600
    items, seen = [], set()
    for source, url in FEEDS.items():
        feed = feedparser.parse(url, agent="ai-news-bot/1.0")
        if not feed.entries:
            print(f"[warn] no entries from {source}")
        for e in feed.entries:
            t = e.get("published_parsed") or e.get("updated_parsed")
            if not t or calendar.timegm(t) < cutoff:
                continue
            title = (e.get("title") or "").strip()
            key = re.sub(r"\W+", "", title.lower())[:80]
            if not title or key in seen:
                continue
            seen.add(key)
            summary = re.sub(r"<[^>]+>", " ", e.get("summary", ""))
            items.append({
                "source": source,
                "title": title,
                "link": e.get("link", ""),
                "summary": re.sub(r"\s+", " ", summary).strip()[:400],
            })
    return items


def score(item):
    text = (item["title"] + " " + item["summary"]).lower()
    s = sum(w for k, w in KEYWORDS.items() if k in text)
    return s + (3 if item["source"] in OFFICIAL else 0)


def pick_with_keywords(items):
    ranked = sorted(items, key=score, reverse=True)[:MAX_ITEMS]
    return [(it, "") for it in ranked]


def pick_with_gemini(items, api_key):
    model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    lang = os.getenv("SUMMARY_LANG", "Armenian")
    candidates = sorted(items, key=score, reverse=True)[:60]
    listing = "\n".join(
        f"[{i}] ({it['source']}) {it['title']} — {it['summary'][:200]}"
        for i, it in enumerate(candidates)
    )
    prompt = (
        f"You are an editor of a daily AI news digest. From the list below pick the "
        f"{MAX_ITEMS} most important AI news items (new models, major product launches, "
        f"big company moves, research breakthroughs, regulation). Skip duplicates, "
        f"opinion pieces and minor items. For each, write ONE short sentence in {lang} "
        f"explaining what happened and why it matters.\n"
        f'Return only JSON: [{{"id": <number>, "text": "<sentence>"}}], most important first.\n\n'
        f"{listing}"
    )
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    r = requests.post(
        url,
        params={"key": api_key},
        json={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0.2},
        },
        timeout=90,
    )
    r.raise_for_status()
    text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
    picks = json.loads(text)
    out = []
    for p in picks:
        i = int(p.get("id", -1))
        if 0 <= i < len(candidates):
            out.append((candidates[i], p.get("text", "")))
    return out[:MAX_ITEMS]


def build_message(picked):
    date = datetime.now(timezone.utc).strftime("%d.%m.%Y")
    lines = [f"<b>🤖 AI նորություններ — {date}</b>", ""]
    if not picked:
        lines.append("Այսօր կարևոր նորություն չգտնվեց։")
    for n, (it, note) in enumerate(picked, 1):
        title = html.escape(it["title"])
        link = html.escape(it["link"], quote=True)
        lines.append(f'{n}. <a href="{link}">{title}</a> <i>({html.escape(it["source"])})</i>')
        if note:
            lines.append(html.escape(note))
        lines.append("")
    return "\n".join(lines).strip()


def send_telegram(text):
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    chunks, cur = [], ""
    for line in text.split("\n"):
        if len(cur) + len(line) + 1 > 4000:
            chunks.append(cur)
            cur = ""
        cur += line + "\n"
    chunks.append(cur)
    for chunk in chunks:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": chunk,
                "parse_mode": "HTML",
                "link_preview_options": {"is_disabled": True},
            },
            timeout=30,
        )
        r.raise_for_status()


def main():
    items = fetch_items()
    print(f"collected {len(items)} items")
    picked = []
    gemini_key = os.getenv("GEMINI_API_KEY")
    if gemini_key and items:
        try:
            picked = pick_with_gemini(items, gemini_key)
        except Exception as e:
            print(f"[warn] Gemini failed, using keyword ranking: {e}")
    if not picked:
        picked = pick_with_keywords(items)
    send_telegram(build_message(picked))
    print("sent")


if __name__ == "__main__":
    main()
