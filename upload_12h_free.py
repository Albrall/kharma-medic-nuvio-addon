#!/usr/bin/env python3
"""
upload_12h_free.py
------------------
سكربت مجاني بالكامل يشغّل على جهازك الشخصي (شبكة البيت) لتحميل جلسة الـ 12 ساعة
(A_BjvkJOetA) بصيغة MP4 سريعة متوافقة مع Xbox (LibVLC) و iPad (AVPlayer/MPV)،
ورفعها تلقائياً إلى GitHub Releases في مستودعك وتحديث الإضافة بدون الحاجة لاشتراك Debrid!

المتطلبات على جهازك:
  pip install yt-dlp
  (ووجود ffmpeg على الجهاز إذا أردت تقسيم الملف تلقائياً لأجزاء أقل من 1.9GB)

طريقة التشغيل:
  python upload_12h_free.py YOUR_GITHUB_TOKEN
"""
import json
import math
import os
import subprocess
import sys
import urllib.request
import urllib.error

USERNAME = "Albrall"
REPO = "kharma-medic-nuvio-addon"
RELEASE_TAG = "v1.0-media"
VIDEO_ID = "A_BjvkJOetA"
VIDEO_URL = f"https://www.youtube.com/watch?v={VIDEO_ID}"
MAX_GITHUB_ASSET_BYTES = 1900 * 1024 * 1024  # 1.9 GB safety margin under GitHub's 2.0 GB limit


def gh_api(token, method, url, data=None, content_type="application/json"):
    body = json.dumps(data).encode("utf-8") if isinstance(data, dict) else data
    req = urllib.request.Request(
        url,
        data=body,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": content_type,
            "User-Agent": "kharma-nuvio-uploader",
        },
    )
    with urllib.request.urlopen(req, timeout=3600) as resp:
        raw = resp.read().decode("utf-8")
        return json.loads(raw) if raw else {}


def ensure_release(token):
    try:
        return gh_api(token, "GET", f"https://api.github.com/repos/{USERNAME}/{REPO}/releases/tags/{RELEASE_TAG}")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return gh_api(
                token,
                "POST",
                f"https://api.github.com/repos/{USERNAME}/{REPO}/releases",
                {
                    "tag_name": RELEASE_TAG,
                    "name": "Kharma Medic Direct MP4 Streams",
                    "body": "Direct HTTP MP4 streams for Xbox & iPad Nuvio playback.",
                    "draft": False,
                    "prerelease": False,
                },
            )
        raise


def main():
    if len(sys.argv) < 2:
        print("الاستخدام: python upload_12h_free.py <GITHUB_TOKEN>")
        sys.exit(1)

    token = sys.argv[1].strip()
    out_file = "kharma_12h_full.mp4"

    print("1) جاري تحميل مقطع الـ 12 ساعة من يوتيوب بصيغة MP4 متوافقة مع Xbox و iPad...")
    # نختار صيغة MP4 جاهزة (H.264 + AAC) بحجم خفيف وسريع البث
    subprocess.run(
        [
            sys.executable,
            "-m",
            "yt_dlp",
            "-f",
            "18/best[ext=mp4][height<=480]/best[ext=mp4][height<=720]",
            "-o",
            out_file,
            VIDEO_URL,
        ],
        check=True,
    )

    size = os.path.getsize(out_file)
    files_to_upload = []

    if size <= MAX_GITHUB_ASSET_BYTES:
        files_to_upload.append(("kharma_12h.mp4", out_file, "كامل (12 ساعة)"))
    else:
        print("حجم الملف أكبر من 1.9GB — جاري تقسيمه بـ ffmpeg بدون إعادة ترميز إلى 3 أجزاء (4 ساعات لكل جزء)...")
        parts = math.ceil(size / MAX_GITHUB_ASSET_BYTES)
        # 12 hours = 43244s -> ~14415s per part for 3 parts
        seg_time = int(43250 / parts) + 10
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                out_file,
                "-c",
                "copy",
                "-map",
                "0",
                "-segment_time",
                str(seg_time),
                "-f",
                "segment",
                "-reset_timestamps",
                "1",
                "-movflags",
                "+faststart",
                "kharma_12h_part%02d.mp4",
            ],
            check=True,
        )
        for idx in range(parts):
            fname = f"kharma_12h_part{idx:02d}.mp4"
            if os.path.exists(fname):
                files_to_upload.append((fname, fname, f"الجزء {idx + 1} من {parts}"))

    print("2) جاري تجهيز GitHub Release ورفع ملفات الـ MP4 على CDN...")
    rel = ensure_release(token)
    upload_base = rel["upload_url"].split("{")[0]

    direct_streams = []
    for asset_name, local_path, label in files_to_upload:
        print(f"   -> رفع {asset_name} ({round(os.path.getsize(local_path) / (1024*1024), 1)} MB)...")
        with open(local_path, "rb") as f:
            file_bytes = f.read()
        asset = gh_api(
            token,
            "POST",
            f"{upload_base}?name={asset_name}",
            data=file_bytes,
            content_type="video/mp4",
        )
        direct_streams.append(
            {
                "name": "Direct MP4 ⚡\nXbox & iPad",
                "title": f"⚡ بث مباشر بدون تقطيع — {label}\nREAL TIME Study With Me: 12 HOUR Pomodoro",
                "url": asset["browser_download_url"],
                "behaviorHints": {
                    "filename": asset_name,
                    "videoSize": os.path.getsize(local_path),
                },
            }
        )

    print("3) جاري تحديث ملف البث في المستودع ليعمل فوراً على الإكسبوكس والآيباد...")
    import base64

    stream_api = f"https://api.github.com/repos/{USERNAME}/{REPO}/contents/stream/movie/km_{VIDEO_ID}.json"
    existing = gh_api(token, "GET", stream_api)
    current_json = json.loads(base64.b64decode(existing["content"]).decode("utf-8"))

    new_streams = direct_streams + current_json.get("streams", [])
    updated_content = base64.b64encode(
        json.dumps({"streams": new_streams}, ensure_ascii=False, indent=2).encode("utf-8")
    ).decode("ascii")

    gh_api(
        token,
        "PUT",
        stream_api,
        {
            "message": "feat: add direct MP4 stream URLs for 12-hour session (Xbox + iPad)",
            "content": updated_content,
            "sha": existing["sha"],
        },
    )
    print("\n✅ تم بنجاح! افتح الآن Nuvio على Xbox أو iPad وبتلقى خيار Direct MP4 يشتغل فوراً بدون أي تقطيع!")


if __name__ == "__main__":
    main()
