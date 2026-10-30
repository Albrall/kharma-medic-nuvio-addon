#!/usr/bin/env python3
"""
remaster_all_48k.py
-------------------
Remasters all 17 Kharma Medic 1080p sessions with Xbox-verified crystal-clear audio:
  - Video: 1080p H.264 untouched (-c:v copy)
  - Audio: Dolby Digital Plus (E-AC-3) 48,000 Hz Stereo 160 kbps (exact 32.000ms frames,
           native Xbox WASAPI 48kHz clock, native iPad libmpv & Apple AVPlayer support)
  - Filter: aresample=48000:async=1:first_pts=0, static clean gain per session, alimiter=limit=0.95
            (zero noise-gate, zero FFT pumping)
  - Container: FastStart MP4 with -use_editlist 0 -avoid_negative_ts make_zero -max_interleave_delta 0
               (zero elst edit-list offsets, STTS=1 entry across entire 12h duration)
"""
import concurrent.futures
import http.client
import json
import math
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

REPO = "Albrall/kharma-medic-nuvio-addon"
TOKEN = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN", "")
REL_SINGLE_ID = 423756287   # v2.0-1080p
REL_MARATHON_ID = 428205581 # v2.2-mp4-1080p
SLICE_SIZE = 1900 * 1024 * 1024  # 1,992,294,400 bytes
WORK_DIR = "/var/tmp/remaster"
WORKER_ORIGIN = "https://kharma-medic-1080p.albrall-kharma.workers.dev"

print_lock = threading.Lock()


def log(msg: str):
    with print_lock:
        print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# Static clean linear gain (dB) calibrated from each session's measured LUFS
GAIN_DB = {
    "qIxx7ffhPy0": 2.5,
    "yyqQJD1PMrA": 4.0,
    "iQ8_E9xZQg0": 5.5,
    "m5HqaG9TnDk": 7.0,
    "Dx0Cko4e5Bk": 8.0,
    "A_BjvkJOetA": 8.0,
    "fJP7c8xt1n4": 8.0,
    "H_wpady4dh4": 8.0,
    "cPTRA1gIWak": 8.0,
    "qND2ScWE1e4": 8.0,
    "V3DNjGB27Go": 8.0,
    "1Z33r0rOlNc": 9.0,
    "jnOIlmmVraE": 8.0,
    "XBo0H5Rn17s": 9.0,
    "Qe2Uw2dbq7A": 9.0,
    "m23k_wS4umU": 9.0,
    "R8FRmElERig": 12.0,
}

SINGLE_SESSIONS = [
    ("m5HqaG9TnDk", "kharma_m5HqaG9TnDk_1080p.mp4"),
    ("m23k_wS4umU", "kharma_m23k_wS4umU_1080p.mp4"),
    ("yyqQJD1PMrA", "kharma_yyqQJD1PMrA_1080p.mp4"),
    ("Qe2Uw2dbq7A", "kharma_Qe2Uw2dbq7A_1080p.mp4"),
    ("Dx0Cko4e5Bk", "kharma_Dx0Cko4e5Bk_1080p_v2.mp4"),
    ("qIxx7ffhPy0", "kharma_qIxx7ffhPy0_1080p.mp4"),
    ("qND2ScWE1e4", "kharma_qND2ScWE1e4_1080p.mp4"),
    ("iQ8_E9xZQg0", "kharma_iQ8_E9xZQg0_1080p.mp4"),
    ("R8FRmElERig", "kharma_R8FRmElERig_1080p.mp4"),
    ("jnOIlmmVraE", "kharma_jnOIlmmVraE_1080p.mp4"),
    ("XBo0H5Rn17s", "kharma_XBo0H5Rn17s_1080p.mp4"),
    ("H_wpady4dh4", "kharma_H_wpady4dh4_1080p.mp4"),
]

MARATHON_ORDER = [
    "V3DNjGB27Go",
    "cPTRA1gIWak",
    "1Z33r0rOlNc",
    "A_BjvkJOetA",
    "fJP7c8xt1n4",
]

SESSION_META_AR = {
    "m5HqaG9TnDk": ("1h 09m", "ساعة قوة وتركيز متواصل بدون فواصل (Power Hour) وبدون موسيقى لإنجاز المهام السريعة.", "REAL TIME study with me (no music): 1 HOUR Productive Power Session | KharmaMedic"),
    "m23k_wS4umU": ("2h 02m", "جلسة مذاكرة لمدة ساعتين بالوقت الفعلي بنظام بومودورو بدون موسيقى.", "REAL TIME study with me (no music): 2 HOUR Productive Pomodoro Session | KharmaMedic"),
    "yyqQJD1PMrA": ("2h 01m", "جلسة مذاكرة لمدة ساعتين مع موسيقى هادئة تساعد على التركيز العميق.", "REAL TIME study with me (with music): 2 HOUR Productive Pomodoro Session | KharmaMedic"),
    "R8FRmElERig": ("2h 31m", "جلسة مذاكرة حقيقية لمدة ساعتين ونصف بنظام بومودورو بدون موسيقى.", "REAL TIME study with me (no music): 2.5 HOUR Productive Pomodoro Session | KharmaMedic"),
    "Qe2Uw2dbq7A": ("3h 02m", "جلسة مذاكرة لمدة 3 ساعات متواصلة بنظام بومودورو (50/10) بدون موسيقى.", "REAL TIME study with me (no music): 3 HOUR Productive Pomodoro Session | KharmaMedic"),
    "qND2ScWE1e4": ("3h 05m", "جلسة مذاكرة مسائية هادئة لمدة 3 ساعات بالوقت الفعلي بدون موسيقى.", "REAL TIME study with me (no music): 3 HOUR Productive Pomodoro Session | KharmaMedic"),
    "iQ8_E9xZQg0": ("4h 02m", "جلسة مذاكرة لمدة 4 ساعات كاملة بنظام بومودورو بدون موسيقى.", "REAL TIME study with me (no music): 4 HOUR Productive Pomodoro Session | KharmaMedic"),
    "XBo0H5Rn17s": ("4h 05m", "جلسة مذاكرة صباحية لمدة 4 ساعات بنظام بومودورو (50/10) بدون موسيقى.", "REAL TIME study with me (no music): 4 HOUR Productive Pomodoro Session | KharmaMedic"),
    "Dx0Cko4e5Bk": ("4h 01m", "جلسة مذاكرة مركزة لمدة 4 ساعات (إصدار أغسطس 2023) بنظام بومودورو بدون موسيقى.", "REAL TIME study with me (no music): 4 HOUR Productive Pomodoro Session | KharmaMedic"),
    "qIxx7ffhPy0": ("5h 02m", "جلسة مذاكرة لمدة 5 ساعات مع موسيقى Lofi هادئة وبنظام بومودورو.", "REAL TIME study with me (Lofi music): 5 HOUR Productive Pomodoro Session | KharmaMedic"),
    "jnOIlmmVraE": ("5h 44m", "جلسة مذاكرة حديثة لمدة 5 ساعات و44 دقيقة بدون موسيقى بنظام بومودورو مع د. ناصر.", "REAL TIME Study with me (no music): 5 Hour Productive Pomodoro Session | KharmaMedic"),
    "H_wpady4dh4": ("6h 03m", "جلسة مذاكرة مطولة لمدة 6 ساعات كاملة بنظام بومودورو (50/10) بدون موسيقى.", "REAL TIME study with me (no music): 6 HOUR Productive Pomodoro Session | KharmaMedic"),
    "V3DNjGB27Go": ("6h 46m", "جلسة مذاكرة بالوقت الفعلي قرابة 7 ساعات (6 ساعات و46 دقيقة) بنظام 50/10 بدون موسيقى.", "REAL TIME study with me (no music): 6 HOUR Productive Pomodoro Session | KharmaMedic"),
    "cPTRA1gIWak": ("7h 02m", "جلسة المذاكرة الشهيرة لمدة 7 ساعات متواصلة بنظام Pomodoro بدون موسيقى مع Kharma Medic.", "REAL TIME study with me (no music): 7 HOUR Productive Pomodoro Session | KharmaMedic"),
    "1Z33r0rOlNc": ("8h 53m", "جلسة مذاكرة حقيقية لمدة 8 ساعات (مع جولة بومودورو إضافية) بدون موسيقى.", "REAL TIME study with me (no music): 8 HOUR Productive Pomodoro Session | KharmaMedic"),
    "fJP7c8xt1n4": ("10h 01m", "جلسة مذاكرة مطولة لمدة 10 ساعات بالوقت الفعلي بدون موسيقى بنظام بومودورو (50/10).", "REAL TIME study with me (no music): 10 HOUR Productive Pomodoro Session | KharmaMedic"),
    "A_BjvkJOetA": ("12h 01m", "جلسة مذاكرة حقيقية لمدة 12 ساعة كاملة بدون موسيقى بنظام بومودورو (50 دقيقة مذاكرة / 10 دقائق راحة) مع د. ناصر (Kharma Medic).", "REAL TIME study with me (no music): 12 HOUR Productive Pomodoro Session | KharmaMedic"),
}


def gh_api(method: str, url: str, data=None):
    for attempt in range(5):
        try:
            req = urllib.request.Request(
                url,
                data=data,
                method=method,
                headers={
                    "Authorization": f"Bearer {TOKEN}",
                    "Accept": "application/vnd.github+json",
                    "User-Agent": "kharma-48k-remaster",
                },
            )
            with urllib.request.urlopen(req, timeout=60) as r:
                raw = r.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise
            time.sleep(2 * (attempt + 1))
        except Exception:
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"GitHub API failed: {method} {url}")


def list_release_assets(release_id: int) -> dict:
    items = gh_api("GET", f"https://api.github.com/repos/{REPO}/releases/{release_id}/assets?per_page=100")
    out = {}
    for a in items:
        if a.get("state") == "uploaded":
            out[a["name"]] = {"id": a["id"], "size": a["size"], "url": a["browser_download_url"]}
        else:
            try:
                gh_api("DELETE", f"https://api.github.com/repos/{REPO}/releases/assets/{a['id']}")
            except Exception:
                pass
    return out


def delete_asset_by_name(release_id: int, name: str):
    items = gh_api("GET", f"https://api.github.com/repos/{REPO}/releases/{release_id}/assets?per_page=100")
    for a in items:
        if a["name"] == name:
            try:
                gh_api("DELETE", f"https://api.github.com/repos/{REPO}/releases/assets/{a['id']}")
                log(f"  🗑️ Deleted old asset {name}")
            except Exception as e:
                log(f"  ⚠️ Delete warning {name}: {e}")


def fast_range_download(url: str, out_path: str, threads: int = 6, dest_offset: int = 0, preallocated: bool = False):
    req = urllib.request.Request(url, headers={"Range": "bytes=0-1", "User-Agent": "kharma-dl"})
    with urllib.request.urlopen(req, timeout=30) as r:
        cdn_url = r.geturl()
        total = int(r.headers["Content-Range"].split("/")[-1])

    if not preallocated:
        if os.path.exists(out_path) and os.path.getsize(out_path) == total:
            return total
        with open(out_path, "wb") as f:
            f.truncate(total)

    chunk = 32 * 1024 * 1024  # 32 MiB segments
    ranges = []
    for start in range(0, total, chunk):
        end = min(start + chunk - 1, total - 1)
        ranges.append((start, end))

    q = queue.Queue()
    for rng in ranges:
        q.put(rng)

    errors = []

    def worker():
        nonlocal cdn_url
        while not q.empty():
            try:
                start, end = q.get_nowait()
            except queue.Empty:
                return
            ok = False
            for _ in range(5):
                try:
                    rq = urllib.request.Request(cdn_url, headers={"Range": f"bytes={start}-{end}"})
                    with urllib.request.urlopen(rq, timeout=60) as resp:
                        data = resp.read()
                    if len(data) != (end - start + 1):
                        raise IOError("short read")
                    with open(out_path, "r+b") as fp:
                        fp.seek(dest_offset + start)
                        fp.write(data)
                    ok = True
                    break
                except Exception:
                    try:
                        rq0 = urllib.request.Request(url, headers={"Range": "bytes=0-1"})
                        with urllib.request.urlopen(rq0, timeout=20) as r0:
                            cdn_url = r0.geturl()
                    except Exception:
                        pass
                    time.sleep(1.5)
            if not ok:
                errors.append((start, end))
            q.task_done()

    ts = [threading.Thread(target=worker, daemon=True) for _ in range(threads)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    if errors:
        raise RuntimeError(f"Download failed ranges for {url}: {errors[:3]}")
    return total


def upload_file_slice(release_id: int, filepath: str, offset: int, length: int, asset_name: str) -> dict:
    for attempt in range(5):
        items = gh_api("GET", f"https://api.github.com/repos/{REPO}/releases/{release_id}/assets?per_page=100")
        for a in items:
            if a["name"] == asset_name:
                if a.get("state") == "uploaded" and a.get("size") == length:
                    return {"size": a["size"], "url": a["browser_download_url"]}
                try:
                    gh_api("DELETE", f"https://api.github.com/repos/{REPO}/releases/assets/{a['id']}")
                except Exception:
                    pass

        t0 = time.time()
        try:
            conn = http.client.HTTPSConnection("uploads.github.com", timeout=300)
            path = f"/repos/{REPO}/releases/{release_id}/assets?name={urllib.parse.quote(asset_name)}"
            conn.putrequest("POST", path)
            conn.putheader("Authorization", f"Bearer {TOKEN}")
            conn.putheader("Accept", "application/vnd.github+json")
            conn.putheader("Content-Type", "video/mp4")
            conn.putheader("Content-Length", str(length))
            conn.putheader("User-Agent", "kharma-48k-uploader")
            conn.endheaders()

            rem = length
            with open(filepath, "rb") as f:
                f.seek(offset)
                while rem > 0:
                    buf = f.read(min(8 * 1024 * 1024, rem))
                    if not buf:
                        break
                    conn.send(buf)
                    rem -= len(buf)

            resp = conn.getresponse()
            body = resp.read().decode("utf-8", errors="ignore")
            conn.close()
            if resp.status in (200, 201):
                res = json.loads(body)
                dt = max(time.time() - t0, 0.1)
                log(f"  ☁️ Uploaded {asset_name} ({length/1048576:.1f} MiB in {dt:.1f}s @ {length/1048576/dt:.1f} MiB/s)")
                return {"size": res["size"], "url": res["browser_download_url"]}
            log(f"  ⚠️ Upload HTTP {resp.status} for {asset_name}: {body[:160]}")
        except Exception as e:
            log(f"  ⚠️ Upload exc ({attempt+1}) for {asset_name}: {e}")
        time.sleep(3)
    raise RuntimeError(f"Failed upload {asset_name}")


def fetch_dx_clean_audio(out_m4a: str):
    if os.path.exists(out_m4a) and os.path.getsize(out_m4a) > 150_000_000:
        log(f"✅ Dx0Cko4e5Bk clean audio already present ({os.path.getsize(out_m4a)/1048576:.1f} MiB)")
        return

    log("🎙️ Fetching clean original YouTube format 140 audio for Dx0Cko4e5Bk via Tor lanes...")
    vid = "Dx0Cko4e5Bk"
    a_url = None
    a_size = 0
    puser_good = None

    for attempt in range(15):
        puser = f"dx_clean_{int(time.time())}_{attempt}"
        try:
            p = subprocess.run(
                [
                    "yt-dlp",
                    "--proxy", f"socks5h://{puser}:pass@127.0.0.1:9050",
                    "-J", "--no-playlist",
                    f"https://www.youtube.com/watch?v={vid}",
                ],
                capture_output=True, text=True, timeout=60,
            )
        except subprocess.TimeoutExpired:
            continue
        if p.returncode != 0 or not p.stdout.strip():
            continue
        info = json.loads(p.stdout)
        fmts = {str(f.get("format_id")): f for f in info.get("formats", []) if "m3u8" not in str(f.get("protocol", ""))}
        afmt = fmts.get("140") or fmts.get("251")
        if not afmt:
            continue
        fsize_est = int(afmt.get("filesize") or afmt.get("filesize_approx") or 0)
        sep = "&" if "?" in afmt["url"] else "?"
        c = subprocess.run(
            ["curl", "-sS", "-D", "-", "-o", "/dev/null", "--socks5-hostname", "127.0.0.1:9050", "--proxy-user", f"{puser}:pass", "--max-time", "20", f"{afmt['url']}{sep}range=0-1"],
            capture_output=True, text=True,
        )
        if "HTTP/2 200" in c.stdout or "HTTP/1.1 200" in c.stdout or "206" in c.stdout:
            if fsize_est > 100_000_000:
                a_size = fsize_est
                a_url = afmt["url"]
                puser_good = puser
                break

    if not a_url:
        raise RuntimeError("Could not resolve Dx0Cko4e5Bk format 140 over Tor")

    log(f"  Resolved Dx0Cko4e5Bk format 140 ({a_size/1048576:.1f} MiB) on circuit {puser_good} — downloading...")
    tmp_out = out_m4a + ".part"
    if os.path.exists(tmp_out):
        os.remove(tmp_out)
    subprocess.run(
        [
            "yt-dlp",
            "--proxy", f"socks5h://{puser_good}:pass@127.0.0.1:9050",
            "-f", "140",
            "--concurrent-fragments", "6",
            "-o", out_m4a,
            f"https://www.youtube.com/watch?v={vid}",
        ],
        check=True,
    )
    log(f"✅ Downloaded clean Dx0Cko4e5Bk audio ({os.path.getsize(out_m4a)/1048576:.1f} MiB)")


def verify_mp4(path: str):
    p = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_name,height,sample_rate:format=duration", "-of", "json", path],
        capture_output=True, text=True, check=True,
    )
    info = json.loads(p.stdout)
    streams = info.get("streams", [])
    v = [s for s in streams if s.get("codec_name") == "h264"]
    a = [s for s in streams if s.get("codec_name") == "eac3"]
    if not v or int(v[0].get("height", 0)) != 1080:
        raise RuntimeError(f"Video stream check failed for {path}: {streams}")
    if not a or str(a[0].get("sample_rate")) != "48000":
        raise RuntimeError(f"Audio stream check failed for {path}: {streams}")
    return float(info["format"]["duration"])


def fmt_size_label(nbytes: int) -> str:
    if nbytes >= 1024 ** 3:
        return f"{nbytes / (1024 ** 3):.2f} GB"
    return f"{round(nbytes / (1024 ** 2))} MB"


def write_stream_json(vid: str, direct_url: str, fname: str, fsize: int, is_marathon: bool):
    dur_label, desc_ar, orig_title = SESSION_META_AR[vid]
    sz_label = fmt_size_label(fsize)
    extra_note = " — مقطع واحد كامل متصل" if is_marathon else ""
    streams = [
        {
            "name": "Direct 1080p ⚡\nXbox & iPad",
            "title": f"⚡ بث مباشر فوري 1080p Full HD • صوت 48kHz صافي بدون تقطيع{extra_note} ({dur_label} • {sz_label})\n{desc_ar}",
            "url": direct_url,
            "behaviorHints": {
                "notWebReady": False,
                "bingeGroup": f"kharma-1080p-{vid}",
                "filename": fname,
                "videoSize": fsize,
            },
        }
    ]
    if vid == "A_BjvkJOetA":
        streams.append(
            {
                "name": "Backup MP4 720p\n(12h متصلة)",
                "title": "⚡ بث مباشر فوري بدون تقطيع — 12 ساعة كاملة (720p HD)\nREAL TIME study with me (no music): 12 HOUR Productive Pomodoro Session",
                "url": "https://github.com/Albrall/kharma-medic-nuvio-addon/releases/download/v1.0-media/kharma_12h_720p.mp4",
                "behaviorHints": {
                    "filename": "KharmaMedic.StudyWithMe.12Hours.720p.H264.AAC.mp4",
                    "videoSize": 1259496847,
                },
            }
        )
    streams.extend(
        [
            {
                "name": "Nuvio Player\nYouTube HD",
                "title": f"▶️ تشغيل داخل مشغل Nuvio الداخلي ({dur_label})\n{orig_title}",
                "ytId": vid,
            },
            {
                "name": "YouTube App",
                "title": f"📱 فتح في تطبيق يوتيوب الخارجي ({dur_label})\nhttps://www.youtube.com/watch?v={vid}",
                "externalUrl": f"https://www.youtube.com/watch?v={vid}",
            },
        ]
    )
    out_p = f"stream/movie/km_{vid}.json"
    with open(out_p, "w", encoding="utf-8") as f:
        json.dump({"streams": streams}, f, ensure_ascii=False, indent=2)
        f.write("\n")


def process_single(vid: str, old_asset: str, existing_assets: dict, dx_audio_path: str):
    new_asset = f"kharma_{vid}_1080p_48k.mp4"
    if new_asset in existing_assets and existing_assets[new_asset]["size"] > 300_000_000:
        info = existing_assets[new_asset]
        log(f"⏭️ [{vid}] {new_asset} already uploaded ({info['size']/1048576:.1f} MiB)")
        write_stream_json(vid, info["url"], new_asset, info["size"], False)
        return

    src_url = f"https://github.com/{REPO}/releases/download/v2.0-1080p/{old_asset}"
    in_mp4 = os.path.join(WORK_DIR, f"in_{vid}.mp4")
    out_mp4 = os.path.join(WORK_DIR, f"out_{vid}.mp4")

    t0 = time.time()
    log(f"⬇️ [{vid}] Downloading {old_asset}...")
    fast_range_download(src_url, in_mp4, threads=6)

    gain = GAIN_DB[vid]
    log(f"🎛️ [{vid}] Remastering 1080p + Dolby E-AC-3 48kHz (+{gain}dB, zero edit-list)...")
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-fflags", "+genpts", "-i", in_mp4]
    if vid == "Dx0Cko4e5Bk":
        cmd += ["-i", dx_audio_path, "-map", "0:v:0", "-map", "1:a:0", "-shortest"]
    else:
        cmd += ["-map", "0:v:0", "-map", "0:a:0"]

    cmd += [
        "-c:v", "copy",
        "-af", f"aresample=48000:async=1:first_pts=0,volume={gain}dB,alimiter=limit=0.95:level=false",
        "-c:a", "eac3", "-b:a", "160k", "-ar", "48000", "-ac", "2",
        "-use_editlist", "0",
        "-avoid_negative_ts", "make_zero",
        "-max_interleave_delta", "0",
        "-movflags", "+faststart",
        out_mp4,
    ]
    subprocess.run(cmd, check=True)
    os.remove(in_mp4)

    dur = verify_mp4(out_mp4)
    fsize = os.path.getsize(out_mp4)
    if fsize >= 2_140_000_000:
        raise RuntimeError(f"[{vid}] Output {fsize} exceeds GitHub 2 GiB limit!")

    up = upload_file_slice(REL_SINGLE_ID, out_mp4, 0, fsize, new_asset)
    os.remove(out_mp4)
    write_stream_json(vid, up["url"], new_asset, up["size"], False)
    delete_asset_by_name(REL_SINGLE_ID, old_asset)
    log(f"✅ [{vid}] DONE ({dur/3600:.2f}h, {fsize/1048576:.1f} MiB) in {time.time()-t0:.1f}s")


def process_marathon(vid: str, mmap: dict, existing_m_assets: dict):
    entry = mmap[vid]
    # Check if already migrated to _48k slices
    expected_prefix = f"kharma_{vid}_1080p_48k.mp4.part"
    if entry.get("audio_48k") is True and all(s["name"].startswith(expected_prefix) for s in entry.get("slices", [])):
        log(f"⏭️ [{vid}] Marathon already remastered to 48kHz ({entry['total_size']/1073741824:.2f} GiB)")
        fname = f"kharma_{vid}_1080p_48k.mp4"
        write_stream_json(vid, f"{WORKER_ORIGIN}/mp4/{fname}", fname, entry["total_size"], True)
        return

    t0 = time.time()
    in_mp4 = os.path.join(WORK_DIR, f"in_{vid}.mp4")
    out_mp4 = os.path.join(WORK_DIR, f"out_{vid}.mp4")
    old_slices = list(entry["slices"])
    old_total = entry["total_size"]

    if os.path.exists(out_mp4) and os.path.getsize(out_mp4) > 2_000_000_000:
        log(f"♻️ [{vid}] Reusing already-remastered {out_mp4} ({os.path.getsize(out_mp4)/1073741824:.2f} GiB)")
        dur = verify_mp4(out_mp4)
    else:
        log(f"⬇️ [{vid}] Downloading {len(old_slices)} marathon slices ({old_total/1073741824:.2f} GiB) directly in-place...")
        with open(in_mp4, "wb") as f:
            f.truncate(old_total)

        for sl in old_slices:
            fast_range_download(sl["url"], in_mp4, threads=6, dest_offset=sl["start"], preallocated=True)

        gain = GAIN_DB[vid]
        log(f"🎛️ [{vid}] Remastering marathon 1080p + Dolby E-AC-3 48kHz (+{gain}dB, STTS=1, zero edit-list)...")
        cmd = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-fflags", "+genpts",
            "-i", in_mp4,
            "-map", "0:v:0", "-map", "0:a:0",
            "-c:v", "copy",
            "-af", f"aresample=48000:async=1:first_pts=0,volume={gain}dB,alimiter=limit=0.95:level=false",
            "-c:a", "eac3", "-b:a", "160k", "-ar", "48000", "-ac", "2",
            "-use_editlist", "0",
            "-avoid_negative_ts", "make_zero",
            "-max_interleave_delta", "0",
            "-movflags", "+faststart",
            out_mp4,
        ]
        subprocess.run(cmd, check=True)
        os.remove(in_mp4)
        dur = verify_mp4(out_mp4)
    new_total = os.path.getsize(out_mp4)
    num_slices = math.ceil(new_total / SLICE_SIZE)
    log(f"☁️ [{vid}] Uploading {num_slices} zero-copy slices ({new_total/1073741824:.2f} GiB total)...")

    new_slices = [None] * num_slices

    def up_slice(idx: int):
        start = idx * SLICE_SIZE
        end = min(start + SLICE_SIZE - 1, new_total - 1)
        length = end - start + 1
        sname = f"kharma_{vid}_1080p_48k.mp4.part{idx+1:02d}"
        res = upload_file_slice(REL_MARATHON_ID, out_mp4, start, length, sname)
        new_slices[idx] = {
            "part": idx + 1,
            "name": sname,
            "start": start,
            "end": end,
            "size": length,
            "url": res["url"],
        }

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        futs = [ex.submit(up_slice, i) for i in range(num_slices)]
        for ft in futs:
            ft.result()

    os.remove(out_mp4)

    # Update marathon_mp4_map.json immediately (crash-safe checkpoint!)
    fname = f"kharma_{vid}_1080p_48k.mp4"
    mmap[vid] = {
        "video_id": vid,
        "filename": fname,
        "total_size": new_total,
        "slice_size": SLICE_SIZE,
        "audio_48k": True,
        "slices": new_slices,
    }
    with open("marathon_mp4_map.json", "w", encoding="utf-8") as f:
        json.dump(mmap, f, ensure_ascii=False, indent=2)
        f.write("\n")

    write_stream_json(vid, f"{WORKER_ORIGIN}/mp4/{fname}", fname, new_total, True)

    for sl in old_slices:
        if not sl["name"].startswith(expected_prefix):
            delete_asset_by_name(REL_MARATHON_ID, sl["name"])

    log(f"✅ [{vid}] MARATHON DONE ({dur/3600:.2f}h, {new_total/1073741824:.2f} GiB) in {time.time()-t0:.1f}s")


def main():
    os.makedirs(WORK_DIR, exist_ok=True)
    t_all = time.time()

    dx_audio_path = os.path.join(WORK_DIR, "Dx0Cko4e5Bk_raw_audio.m4a")
    fetch_dx_clean_audio(dx_audio_path)

    existing_single = list_release_assets(REL_SINGLE_ID)
    log(f"=== PHASE 1: Remastering 12 Single-File Sessions (2 parallel workers) ===")
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        futs = [ex.submit(process_single, vid, old_a, existing_single, dx_audio_path) for vid, old_a in SINGLE_SESSIONS]
        for ft in concurrent.futures.as_completed(futs):
            ft.result()

    if os.path.exists(dx_audio_path):
        os.remove(dx_audio_path)

    # Remove temporary diagnostic assets test1..test4
    for tname in ["test1_mkv_ac3_48k.mkv", "test2_mp4_eac3_48k.mp4", "test3_mkv_aac_48k.mkv", "test4_mp4_mp3_48k.mp4"]:
        delete_asset_by_name(REL_SINGLE_ID, tname)

    log(f"=== PHASE 2: Remastering 5 Marathon Sessions (6h46m - 12h) ===")
    with open("marathon_mp4_map.json", encoding="utf-8") as f:
        mmap = json.load(f)
    existing_m = list_release_assets(REL_MARATHON_ID)
    for vid in MARATHON_ORDER:
        process_marathon(vid, mmap, existing_m)

    shutil.rmtree(WORK_DIR, ignore_errors=True)
    log(f"🎉 ALL 17 SESSIONS REMASTERED & UPLOADED IN {(time.time()-t_all)/60:.1f} MINUTES!")


if __name__ == "__main__":
    main()
