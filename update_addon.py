#!/usr/bin/env python3
"""
تحديث تلقائي لإضافة Kharma Medic — Study With Me لتطبيق Nuvio / Stremio.
يقوم بسحب أحدث مقاطع Real Time Study With Me من قناة @KharmaMedic على يوتيوب
وتوليد ملفات manifest.json و catalog/ و meta/ و stream/ بدون الحاجة لمفتاح API.
"""
import json
import os
import re
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
CHANNEL_SEARCH_URL = "https://www.youtube.com/@KharmaMedic/search?query=study%20with%20me"
PLAYLIST_ID = "PLmWo-cY6xKJbFIhZZZ-dm599XwucdH6gW"


def fetch_html(url: str) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept-Language": "en-US,en;q=0.9",
            "Cookie": "SOCS=CAISNQgDEitib3FfaWRlbnRpdHlmcm9udGVuZHVpc2VydmVyXzIwMjMwODI5LjA3X3AxGgJlbiACGgYIgJnPpwY",
        },
    )
    return urllib.request.urlopen(req, timeout=20).read().decode("utf-8", errors="ignore")


def find_objs(obj, key, out):
    if isinstance(obj, dict):
        if key in obj:
            out.append(obj[key])
        for v in obj.values():
            find_objs(v, key, out)
    elif isinstance(obj, list):
        for item in obj:
            find_objs(item, key, out)


def parse_duration_minutes(dur_str: str) -> int:
    parts = [int(p) for p in dur_str.split(":") if p.isdigit()]
    if len(parts) == 3:
        return parts[0] * 60 + parts[1] + (1 if parts[2] >= 30 else 0)
    if len(parts) == 2:
        return parts[0] + (1 if parts[1] >= 30 else 0)
    return 0


def has_maxres(vid: str) -> bool:
    url = f"https://i.ytimg.com/vi/{vid}/maxresdefault.jpg"
    try:
        r = urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=5)
        return r.status == 200
    except Exception:
        return False


def main():
    html = fetch_html(CHANNEL_SEARCH_URL)
    m = re.search(r"var ytInitialData = (\{.*?\});</script>", html)
    if not m:
        raise RuntimeError("لم يتم العثور على بيانات يوتيوب (ytInitialData).")

    data = json.loads(m.group(1))
    renderers = []
    find_objs(data, "videoRenderer", renderers)

    sessions = []
    seen = set()
    for r in renderers:
        vid = r.get("videoId")
        if not vid or vid in seen:
            continue
        title = "".join(run.get("text", "") for run in r.get("title", {}).get("runs", []))
        dur = r.get("lengthText", {}).get("simpleText", "")
        mins = parse_duration_minutes(dur)
        # نختار فقط جلسات Real-Time Study With Me الطويلة (أكثر من 45 دقيقة)
        if "study with me" in title.lower() and mins >= 45:
            seen.add(vid)
            sessions.append({"ytId": vid, "fullTitle": title, "dur": dur, "mins": mins})

    sessions.sort(key=lambda x: x["mins"], reverse=True)
    print(f"تم العثور على {len(sessions)} جلسة Study With Me.")

    os.makedirs(os.path.join(ROOT, "catalog/movie"), exist_ok=True)
    os.makedirs(os.path.join(ROOT, "meta/movie"), exist_ok=True)
    os.makedirs(os.path.join(ROOT, "stream/movie"), exist_ok=True)

    all_metas, long_metas, short_med_metas = [], [], []
    for s in sessions:
        vid = s["ytId"]
        item_id = f"km_{vid}"
        hours_label = f"{s['mins'] // 60}h {s['mins'] % 60:02d}m"
        thumb = (
            f"https://i.ytimg.com/vi/{vid}/maxresdefault.jpg"
            if has_maxres(vid)
            else f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"
        )
        short_name = re.sub(r"\s*\|\s*KharmaMedic.*$", "", s["fullTitle"], flags=re.I)

        meta_preview = {
            "id": item_id,
            "type": "movie",
            "name": short_name,
            "poster": thumb,
            "posterShape": "landscape",
            "background": thumb,
            "description": f"⏱️ المدة: {hours_label}\n\n{s['fullTitle']}",
            "releaseInfo": hours_label,
            "runtime": f"{s['mins']} min",
            "genres": ["Study With Me", "Pomodoro", "No Music"],
        }
        all_metas.append(meta_preview)
        if s["mins"] >= 360:
            long_metas.append(meta_preview)
        else:
            short_med_metas.append(meta_preview)

        with open(os.path.join(ROOT, f"meta/movie/{item_id}.json"), "w", encoding="utf-8") as f:
            json.dump({"meta": meta_preview}, f, ensure_ascii=False, indent=2)

        streams = {
            "streams": [
                {
                    "name": "Nuvio Player\nYouTube HD",
                    "title": f"▶️ تشغيل داخل مشغل Nuvio الداخلي ({hours_label})\n{s['fullTitle']}",
                    "ytId": vid,
                },
                {
                    "name": "YouTube App",
                    "title": f"📱 فتح في تطبيق يوتيوب ({hours_label})\nhttps://www.youtube.com/watch?v={vid}",
                    "externalUrl": f"https://www.youtube.com/watch?v={vid}",
                },
            ]
        }
        with open(os.path.join(ROOT, f"stream/movie/{item_id}.json"), "w", encoding="utf-8") as f:
            json.dump(streams, f, ensure_ascii=False, indent=2)

    with open(os.path.join(ROOT, "catalog/movie/kharma_all.json"), "w", encoding="utf-8") as f:
        json.dump({"metas": all_metas}, f, ensure_ascii=False, indent=2)
    with open(os.path.join(ROOT, "catalog/movie/kharma_short_med.json"), "w", encoding="utf-8") as f:
        json.dump({"metas": list(reversed(short_med_metas))}, f, ensure_ascii=False, indent=2)
    with open(os.path.join(ROOT, "catalog/movie/kharma_long.json"), "w", encoding="utf-8") as f:
        json.dump({"metas": long_metas}, f, ensure_ascii=False, indent=2)

    print("تم تحديث جميع ملفات الإضافة بنجاح!")


if __name__ == "__main__":
    main()
