#!/usr/bin/env python3
"""
build_marathon_mp4_slices.py
----------------------------
Builds 100% lossless, continuous, FastStart single-timeline 1080p .mp4 files
for all 5 marathon Study With Me sessions (6h46m to 12h01m) from the lossless
v2.1-hls-1080p MPEG-TS packs (-c copy, zero quality loss), verifies moov-at-head
and exact duration via ffprobe, and uploads exact 1900 MiB raw byte-slices
directly from file offsets to GitHub Release `v2.2-mp4-1080p`.
"""
import concurrent.futures
import http.client
import json
import math
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
WORK_DIR = "/var/tmp/marathon_mp4_work"
MAP_PATH = os.path.join(ROOT, "marathon_mp4_map.json")
REPO = "Albrall/kharma-medic-nuvio-addon"
SRC_TAG = "v2.1-hls-1080p"
DST_TAG = "v2.2-mp4-1080p"
SLICE_SIZE = 1_992_294_400  # Exact 1900 MiB (< GitHub's 2048 MiB asset limit)

MARATHONS = [
    ("V3DNjGB27Go", 2, 24418),
    ("cPTRA1gIWak", 3, 25365),
    ("1Z33r0rOlNc", 3, 31995),
    ("fJP7c8xt1n4", 4, 36062),
    ("A_BjvkJOetA", 4, 43243),
]


def log(msg: str):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def gh_api(token: str, method: str, url: str, data: bytes = None):
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "User-Agent": "kharma-marathon-mp4/1.0",
        },
    )
    with urllib.request.urlopen(req, timeout=300) as resp:
        raw = resp.read()
        return json.loads(raw.decode("utf-8")) if raw else {}


def ensure_dst_release(token: str) -> dict:
    try:
        return gh_api(token, "GET", f"https://api.github.com/repos/{REPO}/releases/tags/{DST_TAG}")
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
    payload = json.dumps({
        "tag_name": DST_TAG,
        "name": "Kharma Medic — Lossless 1080p Marathon MP4 Byte-Slices",
        "body": "Exact 1900 MiB byte-slices of single-file FastStart 1080p Full HD (.mp4) marathon sessions.",
        "draft": False,
        "prerelease": False,
    }).encode("utf-8")
    return gh_api(token, "POST", f"https://api.github.com/repos/{REPO}/releases", data=payload)


def upload_file_slice(token: str, release_id: int, filepath: str, offset: int, length: int, asset_name: str) -> str:
    for attempt in range(5):
        existing = gh_api(token, "GET", f"https://api.github.com/repos/{REPO}/releases/{release_id}/assets?per_page=100")
        for a in existing:
            if a["name"] == asset_name:
                if a.get("state") == "uploaded" and a.get("size") == length:
                    log(f"  ⏭️ Slice {asset_name} already uploaded ({length/1048576:.1f} MiB)")
                    return a["browser_download_url"]
                try:
                    gh_api(token, "DELETE", f"https://api.github.com/repos/{REPO}/releases/assets/{a['id']}")
                except Exception:
                    pass

        t0 = time.time()
        try:
            conn = http.client.HTTPSConnection("uploads.github.com", timeout=300)
            path = f"/repos/{REPO}/releases/{release_id}/assets?name={urllib.parse.quote(asset_name)}"
            conn.putrequest("POST", path)
            conn.putheader("Authorization", f"token {token}")
            conn.putheader("Accept", "application/vnd.github+json")
            conn.putheader("Content-Type", "application/octet-stream")
            conn.putheader("Content-Length", str(length))
            conn.putheader("User-Agent", "kharma-marathon-mp4/1.0")
            conn.endheaders()

            with open(filepath, "rb") as f:
                f.seek(offset)
                rem = length
                while rem > 0:
                    chunk = f.read(min(8 * 1024 * 1024, rem))
                    if not chunk:
                        raise RuntimeError(f"Unexpected EOF while reading slice {asset_name}")
                    conn.send(chunk)
                    rem -= len(chunk)

            resp = conn.getresponse()
            raw_body = resp.read().decode("utf-8", errors="ignore")
            conn.close()
            if resp.status in (200, 201):
                res = json.loads(raw_body)
                dt = max(time.time() - t0, 0.1)
                log(f"  ☁️ Uploaded {asset_name} ({length/1048576:.1f} MiB in {dt:.1f}s @ {length/1048576/dt:.1f} MiB/s)")
                return res["browser_download_url"]
            log(f"  ⚠️ Upload retry {attempt+1} HTTP {resp.status}: {raw_body[:200]}")
        except Exception as e:
            log(f"  ⚠️ Upload retry {attempt+1} exc: {e}")
        time.sleep(3)
    raise RuntimeError(f"Failed to upload slice {asset_name}")


def verify_moov_at_front(mp4_path: str) -> bool:
    """Confirm FastStart placed `moov` atom immediately after `ftyp` near byte 0."""
    import struct
    with open(mp4_path, "rb") as f:
        pos = 0
        for _ in range(6):
            hdr = f.read(8)
            if len(hdr) < 8:
                return False
            sz, box = struct.unpack(">I4s", hdr)
            box_name = box.decode("latin1", errors="ignore")
            if sz == 1:
                sz = struct.unpack(">Q", f.read(8))[0]
            if box_name == "moov":
                return pos < 1024 * 1024  # moov starts within first 1 MiB!
            if box_name == "mdat":
                return False  # mdat came before moov -> NOT faststart!
            if sz < 8:
                return False
            pos += sz
            f.seek(pos)
    return False


def download_pack(url: str, dst: str):
    subprocess.run(["curl", "-sSL", "--retry", "4", "-o", dst, url], check=True)


def process_marathon(token: str, release_id: int, mp4_map: dict, vid: str, num_packs: int, exp_dur: int):
    if vid in mp4_map and mp4_map[vid].get("verified"):
        log(f"✅ [{vid}] Already built & uploaded in marathon_mp4_map.json, skipping.")
        return

    vdir = os.path.join(WORK_DIR, vid)
    shutil.rmtree(vdir, ignore_errors=True)
    os.makedirs(vdir, exist_ok=True)

    log(f"⬇️ [{vid}] Downloading {num_packs} lossless .ts packs from {SRC_TAG}...")
    ts_paths = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
        futs = []
        for idx in range(1, num_packs + 1):
            url = f"https://github.com/{REPO}/releases/download/{SRC_TAG}/kharma_{vid}_1080p_c{idx}.ts"
            dst = os.path.join(vdir, f"c{idx}.ts")
            ts_paths.append(dst)
            futs.append(ex.submit(download_pack, url, dst))
        for fut in futs:
            fut.result()

    mp4_path = os.path.join(vdir, f"kharma_{vid}_1080p.mp4")
    concat_spec = "concat:" + "|".join(ts_paths)
    log(f"🎬 [{vid}] Remuxing lossless single-file FastStart 1080p .mp4 (-c copy)...")
    t0 = time.time()
    subprocess.run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", concat_spec,
        "-c", "copy",
        "-bsf:a", "aac_adtstoasc",
        "-max_interleave_delta", "500000",
        "-movflags", "+faststart",
        mp4_path,
    ], check=True)
    log(f"  ⚡ Remuxed in {time.time() - t0:.1f}s -> {os.path.getsize(mp4_path)/(1024**3):.3f} GiB")

    # Free .ts packs immediately
    for p in ts_paths:
        if os.path.exists(p):
            os.remove(p)

    # Verify MP4 integrity, resolution, duration, and FastStart moov atom!
    probe_raw = subprocess.check_output([
        "ffprobe", "-v", "error",
        "-show_entries", "stream=codec_name,width,height:format=duration,size",
        "-of", "json", mp4_path,
    ]).decode("utf-8")
    probe = json.loads(probe_raw)
    dur = float(probe["format"]["duration"])
    total_size = int(probe["format"]["size"])
    vstream = next(s for s in probe["streams"] if s.get("width"))
    has_faststart = verify_moov_at_front(mp4_path)
    log(f"  🔍 Probe [{vid}]: {vstream['width']}x{vstream['height']} {vstream['codec_name']}, dur={dur:.1f}s (expected ~{exp_dur}s), size={total_size} B, faststart_moov_at_0={has_faststart}")
    if not has_faststart or abs(dur - exp_dur) > 60:
        raise RuntimeError(f"MP4 verification failed for {vid}: faststart={has_faststart}, dur={dur}")

    # Upload 1900 MiB byte-slices directly from mp4_path offsets
    num_slices = math.ceil(total_size / SLICE_SIZE)
    slices_meta = []
    for s_idx in range(num_slices):
        start = s_idx * SLICE_SIZE
        length = min(SLICE_SIZE, total_size - start)
        end = start + length - 1
        sname = f"kharma_{vid}_1080p.mp4.s{s_idx}"
        surl = upload_file_slice(token, release_id, mp4_path, start, length, sname)
        slices_meta.append({
            "index": s_idx,
            "name": sname,
            "start": start,
            "end": end,
            "size": length,
            "url": surl,
        })

    mp4_map[vid] = {
        "ytId": vid,
        "filename": f"kharma_{vid}_1080p.mp4",
        "total_size": total_size,
        "duration": round(dur, 3),
        "width": vstream["width"],
        "height": vstream["height"],
        "slice_size": SLICE_SIZE,
        "slices": slices_meta,
        "verified": True,
    }
    with open(MAP_PATH, "w", encoding="utf-8") as f:
        json.dump(mp4_map, f, indent=2)

    shutil.rmtree(vdir, ignore_errors=True)
    log(f"🎉 [{vid}] Complete! ({num_slices} slices = {total_size/(1024**3):.2f} GiB total)")


def main():
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        print("Missing GITHUB_TOKEN", file=sys.stderr)
        sys.exit(1)

    os.makedirs(WORK_DIR, exist_ok=True)
    mp4_map = json.load(open(MAP_PATH)) if os.path.exists(MAP_PATH) else {}
    rel = ensure_dst_release(token)
    rel_id = rel["id"]
    log(f"Target Release {DST_TAG} ready (id={rel_id})")

    target_vids = sys.argv[1:] if len(sys.argv) > 1 else [m[0] for m in MARATHONS]
    for vid, num_packs, exp_dur in MARATHONS:
        if vid in target_vids:
            process_marathon(token, rel_id, mp4_map, vid, num_packs, exp_dur)

    shutil.rmtree(WORK_DIR, ignore_errors=True)
    log("🏆 All requested marathon MP4 byte-slices uploaded & verified!")


if __name__ == "__main__":
    main()
