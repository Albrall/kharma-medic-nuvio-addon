#!/usr/bin/env python3
"""
upload_all_1080p.py (v3 — 8-Lane Multi-Instance Tor Striped Engine)
-------------------------------------------------------------------
Stripes every video across 8 independent Tor circuits (across ports 9050, 9052, 9054, 9056
with UseEntryGuards=0 and MaxCircuitDirtiness=7200), keeping all completed 8 MiB chunks
on disk permanently until muxed, so zero bytes are ever re-downloaded on circuit rotation.
"""
import concurrent.futures
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

ROOT = os.path.dirname(os.path.abspath(__file__))
WORK_ROOT = "/var/tmp/km_1080p_work"
REPO = "Albrall/kharma-medic-nuvio-addon"
TAG = "v2.0-1080p"
TOR_PORTS = [9050, 9052, 9054, 9056]
CHUNK_SIZE = 8 * 1024 * 1024  # 8 MiB chunks for fast straggler tail completion
MAX_SINGLE_BYTES = 1_950_000_000
TARGET_PART_BYTES = 1_450_000_000

PRINT_LOCK = threading.Lock()
JSON_LOCK = threading.Lock()


def log(msg: str):
    with PRINT_LOCK:
        ts = time.strftime("%H:%M:%S")
        try:
            print(f"[{ts}] {msg}", flush=True)
        except BrokenPipeError:
            pass


def gh_api(token: str, method: str, url: str, data: bytes = None, content_type: str = "application/json"):
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": content_type,
            "User-Agent": "kharma-1080p-uploader/3.0",
        },
    )
    with urllib.request.urlopen(req, timeout=300) as resp:
        raw = resp.read()
        return json.loads(raw.decode("utf-8")) if raw else {}


def ensure_release(token: str):
    try:
        return gh_api(token, "GET", f"https://api.github.com/repos/{REPO}/releases/tags/{TAG}")
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
    payload = json.dumps(
        {
            "tag_name": TAG,
            "name": "Kharma Medic — Full 1080p HD Direct Streams",
            "body": "Direct FastStart 1080p Full HD (H.264 + AAC) streams for instant zero-buffering playback on Xbox & iPad.",
            "draft": False,
            "prerelease": False,
        }
    ).encode("utf-8")
    return gh_api(token, "POST", f"https://api.github.com/repos/{REPO}/releases", data=payload)


def get_existing_assets(token: str, release_id: int) -> dict:
    assets = gh_api(token, "GET", f"https://api.github.com/repos/{REPO}/releases/{release_id}/assets?per_page=100")
    out = {}
    for a in assets:
        if a.get("state") == "uploaded":
            out[a["name"]] = {"id": a["id"], "size": a["size"], "url": a["browser_download_url"]}
        else:
            try:
                gh_api(token, "DELETE", f"https://api.github.com/repos/{REPO}/releases/assets/{a['id']}")
            except Exception:
                pass
    return out


def upload_asset_streaming(token: str, release_id: int, filepath: str, asset_name: str) -> dict:
    import http.client

    fsize = os.path.getsize(filepath)
    for attempt in range(5):
        # Always clean any prior stub with same name before uploading
        existing = gh_api(token, "GET", f"https://api.github.com/repos/{REPO}/releases/{release_id}/assets?per_page=100")
        for a in existing:
            if a["name"] == asset_name:
                if a.get("state") == "uploaded" and a.get("size") == fsize:
                    return {"size": a["size"], "url": a["browser_download_url"]}
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
            conn.putheader("Content-Type", "video/mp4")
            conn.putheader("Content-Length", str(fsize))
            conn.putheader("User-Agent", "kharma-1080p-uploader/3.1")
            conn.endheaders()

            with open(filepath, "rb") as f:
                while True:
                    buf = f.read(8 * 1024 * 1024)
                    if not buf:
                        break
                    conn.send(buf)

            resp = conn.getresponse()
            raw_body = resp.read().decode("utf-8", errors="ignore")
            conn.close()
            if resp.status in (200, 201):
                res = json.loads(raw_body)
                dt = max(time.time() - t0, 0.1)
                log(f"  ☁️ Uploaded {asset_name} ({fsize/1048576:.1f} MiB in {dt:.1f}s @ {fsize/1048576/dt:.1f} MiB/s)")
                return {"size": res["size"], "url": res["browser_download_url"]}
            log(f"  ⚠️ Upload retry {attempt+1} HTTP {resp.status} for {asset_name}: {raw_body[:200]}")
        except Exception as e:
            log(f"  ⚠️ Upload retry {attempt+1} exc for {asset_name}: {e}")
        time.sleep(3)
    raise RuntimeError(f"Failed to upload {asset_name}")


def get_exact_size(url: str, port: int, proxy_user: str, fallback: int) -> int:
    sep = "&" if "?" in url else "?"
    p = subprocess.run(
        [
            "curl", "-sSI",
            "--socks5-hostname", f"127.0.0.1:{port}",
            "--proxy-user", f"{proxy_user}:pass",
            "--max-time", "18",
            f"{url}{sep}range=0-1",
        ],
        capture_output=True, text=True,
    )
    for line in p.stdout.splitlines():
        low = line.lower()
        if low.startswith("content-range:") and "/" in low:
            tot = low.split("/")[-1].strip()
            if tot.isdigit():
                return int(tot)
    if fallback and fallback > 0:
        return int(fallback)
    raise RuntimeError("Could not verify Content-Range total size")


class TorLane:
    def __init__(self, vid: str, lane_id: int):
        self.vid = vid
        self.lane_id = lane_id
        self.port = TOR_PORTS[lane_id % len(TOR_PORTS)]
        self.gen = 0
        self.lock = threading.Lock()
        self.proxy_user = ""
        self.v_url = ""
        self.a_url = ""
        self.v_size = 0
        self.a_size = 0
        self.duration = 0.0
        self.ready = False

    def refresh(self, failed_gen: int = None) -> bool:
        with self.lock:
            if failed_gen is not None and self.gen != failed_gen:
                return self.ready  # Another worker on this lane already refreshed it!
            for attempt in range(5):
                self.gen += 1
                puser = f"s_{self.vid}_L{self.lane_id}_g{self.gen}"
                p = subprocess.run(
                    [
                        "yt-dlp",
                        "--js-runtimes", "node:/usr/bin/node",
                        "--proxy", f"socks5h://{puser}:pass@127.0.0.1:{self.port}",
                        "-J", "--no-playlist",
                        f"https://www.youtube.com/watch?v={self.vid}",
                    ],
                    capture_output=True, text=True, timeout=65,
                )
                if p.returncode != 0 or not p.stdout.strip():
                    continue
                info = json.loads(p.stdout)
                fmts = {
                    str(f.get("format_id")): f
                    for f in info.get("formats", [])
                    if "m3u8" not in str(f.get("protocol", ""))
                }
                vfmt = fmts.get("137") or fmts.get("299")
                afmt = fmts.get("140") or fmts.get("139")
                if not vfmt or not afmt:
                    continue
                try:
                    vs = get_exact_size(vfmt["url"], self.port, puser, vfmt.get("filesize") or 0)
                    as_ = get_exact_size(afmt["url"], self.port, puser, afmt.get("filesize") or 0)
                except Exception:
                    continue
                self.proxy_user = puser
                self.v_url = vfmt["url"]
                self.a_url = afmt["url"]
                self.v_size = vs
                self.a_size = as_
                self.duration = float(info.get("duration") or 0)
                self.ready = True
                return True
            self.ready = False
            return False


def striped_download(vid: str, kind: str, lanes: list, total_size: int, out_path: str, workers_per_lane: int = 3):
    if os.path.exists(out_path) and os.path.getsize(out_path) == total_size:
        log(f"  ⏭️ [{vid}] {kind} already complete on disk ({total_size/1048576:.1f} MiB)")
        return

    part_dir = out_path + ".parts"
    os.makedirs(part_dir, exist_ok=True)
    num_chunks = math.ceil(total_size / CHUNK_SIZE)

    q = queue.Queue()
    already_done = 0
    for idx in range(num_chunks):
        start = idx * CHUNK_SIZE
        end = min(start + CHUNK_SIZE - 1, total_size - 1)
        expected = end - start + 1
        cpath = os.path.join(part_dir, f"c_{idx:05d}.bin")
        if os.path.exists(cpath) and os.path.getsize(cpath) == expected:
            already_done += 1
        else:
            q.put(idx)

    done_lock = threading.Lock()
    done_count = [already_done]
    t0 = time.time()
    bytes_at_start = already_done * CHUNK_SIZE

    if q.empty():
        log(f"  ✅ [{vid}] {kind}: all {num_chunks} chunks already cached on disk!")
    else:
        stop_flag = threading.Event()
        errors = []

        def lane_worker(lane: TorLane, worker_idx: int):
            if not lane.ready and not lane.refresh():
                return
            while not stop_flag.is_set():
                try:
                    idx = q.get_nowait()
                except queue.Empty:
                    return

                start = idx * CHUNK_SIZE
                end = min(start + CHUNK_SIZE - 1, total_size - 1)
                expected = end - start + 1
                cpath = os.path.join(part_dir, f"c_{idx:05d}.bin")
                tmp_path = os.path.join(part_dir, f"c_{idx:05d}.tmp.{lane.lane_id}.{worker_idx}")

                cur_gen = lane.gen
                base_url = lane.v_url if kind == "video" else lane.a_url
                sep = "&" if "?" in base_url else "?"
                rurl = f"{base_url}{sep}range={start}-{end}"

                p = subprocess.run(
                    [
                        "curl", "-sS", "--fail",
                        "--socks5-hostname", f"127.0.0.1:{lane.port}",
                        "--proxy-user", f"{lane.proxy_user}:pass",
                        "--connect-timeout", "12",
                        "--speed-time", "18",
                        "--speed-limit", "150000",  # Rotate immediately if relay < 150 KB/s for 18s!
                        "--max-time", "75",
                        "-A", "com.google.android.youtube/19.29.37 (Linux; U; Android 14)",
                        "-o", tmp_path,
                        rurl,
                    ],
                    capture_output=True, text=True,
                )
                if p.returncode == 0 and os.path.exists(tmp_path) and os.path.getsize(tmp_path) == expected:
                    os.replace(tmp_path, cpath)
                    q.task_done()
                    with done_lock:
                        done_count[0] += 1
                        dc = done_count[0]
                        if dc == num_chunks or dc % max(1, num_chunks // 6) == 0:
                            dt = max(time.time() - t0, 0.1)
                            dl_bytes = min(dc * CHUNK_SIZE, total_size) - bytes_at_start
                            spd = max(0.0, dl_bytes / 1048576 / dt)
                            log(f"  ⚡ [{vid}] {kind}: {dc}/{num_chunks} ({min(dc*CHUNK_SIZE, total_size)/1048576:.0f}/{total_size/1048576:.0f} MiB @ {spd:.1f} MiB/s)")
                else:
                    try:
                        os.remove(tmp_path)
                    except OSError:
                        pass
                    # Put chunk back for any fast lane to grab immediately, and rotate this slow/dead lane!
                    q.put(idx)
                    q.task_done()
                    if not lane.refresh(failed_gen=cur_gen):
                        return

        total_threads = len(lanes) * workers_per_lane
        with concurrent.futures.ThreadPoolExecutor(max_workers=total_threads) as pool:
            futs = [
                pool.submit(lane_worker, lane, w)
                for lane in lanes
                for w in range(workers_per_lane)
            ]
            concurrent.futures.wait(futs)

        if not q.empty():
            raise RuntimeError(f"[{vid}] {q.qsize()} chunks remained unfinished for {kind}")

    # Assemble file and remove chunks progressively to save disk
    with open(out_path, "wb") as out_f:
        for i in range(num_chunks):
            cpath = os.path.join(part_dir, f"c_{i:05d}.bin")
            with open(cpath, "rb") as in_f:
                shutil.copyfileobj(in_f, out_f, length=4 * 1024 * 1024)
            os.remove(cpath)
    shutil.rmtree(part_dir, ignore_errors=True)
    if os.path.getsize(out_path) != total_size:
        raise RuntimeError(f"[{vid}] Assembled size mismatch on {kind}")


def probe_duration_sec(filepath: str) -> float:
    p = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", filepath,
        ],
        capture_output=True, text=True, check=True,
    )
    return float(p.stdout.strip())


def fmt_hhmm(sec: float) -> str:
    sec = max(0, int(round(sec)))
    return f"{sec // 3600:02d}:{(sec % 3600) // 60:02d}"


def update_stream_json(vid: str, meta_entry: dict, uploaded_parts: list):
    item_id = f"km_{vid}"
    stream_path = os.path.join(ROOT, f"stream/movie/{item_id}.json")
    with JSON_LOCK:
        cur = json.load(open(stream_path, encoding="utf-8"))
        tail_streams = [s for s in cur.get("streams", []) if "1080p" not in s.get("name", "")]
        for s in tail_streams:
            if "Direct MP4 720p" in s.get("name", ""):
                s["name"] = "Backup MP4 720p\n(12h متصلة)"

        desc_parts = (meta_entry.get("description") or "").split("\n\n")
        full_title = desc_parts[1].strip() if len(desc_parts) > 1 else meta_entry["name"]
        rel = meta_entry.get("releaseInfo") or ""
        hours_label = rel.split("•")[-1].strip() if "•" in rel else rel.strip()

        new_streams = []
        if len(uploaded_parts) == 1:
            pt = uploaded_parts[0]
            gb_str = f"{pt['size'] / (1024**3):.2f} GB" if pt["size"] >= 1024**3 else f"{pt['size'] / (1024**2):.0f} MB"
            new_streams.append(
                {
                    "name": "Direct 1080p ⚡\nXbox & iPad",
                    "title": f"⚡ بث مباشر فوري 1080p Full HD بدون تقطيع ({hours_label} • {gb_str})\n{full_title}",
                    "url": pt["url"],
                    "behaviorHints": {
                        "notWebReady": False,
                        "bingeGroup": f"kharma-1080p-{vid}",
                        "filename": pt["asset_name"],
                        "videoSize": pt["size"],
                    },
                }
            )
        else:
            total_parts = len(uploaded_parts)
            cum_sec = 0.0
            for idx, pt in enumerate(uploaded_parts, start=1):
                dur = pt.get("duration") or 0
                range_txt = f"الساعات {fmt_hhmm(cum_sec)} إلى {fmt_hhmm(cum_sec + dur)} • " if dur > 0 else ""
                cum_sec += dur
                gb_str = f"{pt['size'] / (1024**3):.2f} GB"
                new_streams.append(
                    {
                        "name": f"Direct 1080p ⚡\nالجزء {idx}/{total_parts}",
                        "title": f"⚡ 1080p Full HD — الجزء {idx} من {total_parts} ({range_txt}{gb_str})\n{full_title}",
                        "url": pt["url"],
                        "behaviorHints": {
                            "notWebReady": False,
                            "bingeGroup": f"kharma-1080p-{vid}",
                            "filename": pt["asset_name"],
                            "videoSize": pt["size"],
                        },
                    }
                )

        cur["streams"] = new_streams + tail_streams
        with open(stream_path, "w", encoding="utf-8") as f:
            json.dump(cur, f, ensure_ascii=False, indent=2)


def process_video(vid: str, meta_entry: dict, token: str, release_id: int, existing_assets: dict, num_lanes: int = 8):
    single_name = f"kharma_{vid}_1080p.mp4"
    if single_name in existing_assets:
        log(f"⏭️ [{vid}] Already uploaded ({single_name}), ensuring stream JSON updated...")
        update_stream_json(
            vid,
            meta_entry,
            [{"asset_name": single_name, "url": existing_assets[single_name]["url"], "size": existing_assets[single_name]["size"], "duration": 0}],
        )
        return

    part_assets = sorted(
        k for k in existing_assets if k.startswith(f"kharma_{vid}_1080p_part") and k.endswith(".mp4")
    )
    if len(part_assets) >= 2:
        log(f"⏭️ [{vid}] Multi-part already uploaded ({len(part_assets)} parts)...")
        return

    vdir = os.path.join(WORK_ROOT, vid)
    os.makedirs(vdir, exist_ok=True)
    log(f"🚀 [{vid}] Initializing {num_lanes} parallel Tor lanes for: {meta_entry['name']}")

    lanes = [TorLane(vid, i) for i in range(num_lanes)]
    # Prime at least 1 lane first to know exact byte sizes, while priming the rest in parallel!
    with concurrent.futures.ThreadPoolExecutor(max_workers=num_lanes) as init_pool:
        init_futs = [init_pool.submit(l.refresh) for l in lanes]
        first_lane = None
        for fut in concurrent.futures.as_completed(init_futs):
            if fut.result():
                for l in lanes:
                    if l.ready:
                        first_lane = l
                        break
                if first_lane:
                    break
        if not first_lane:
            raise RuntimeError(f"[{vid}] All {num_lanes} Tor lanes failed initialization")
        # Wait briefly for remaining initial lanes to join
        concurrent.futures.wait(init_futs, timeout=25)

    ready_lanes = [l for l in lanes if l.ready]
    log(f"  🌐 [{vid}] {len(ready_lanes)}/{num_lanes} Tor lanes active! Video={first_lane.v_size/1048576:.0f} MiB, Audio={first_lane.a_size/1048576:.0f} MiB")

    vpath = os.path.join(vdir, "video.mp4")
    apath = os.path.join(vdir, "audio.m4a")

    striped_download(vid, "video", lanes, first_lane.v_size, vpath, workers_per_lane=3)
    striped_download(vid, "audio", lanes, first_lane.a_size, apath, workers_per_lane=3)

    est_total = first_lane.v_size + first_lane.a_size
    dur_sec = first_lane.duration
    uploaded_parts = []

    try:
        if est_total <= MAX_SINGLE_BYTES:
            out_mp4 = os.path.join(vdir, single_name)
            log(f"🎬 [{vid}] Muxing 1080p FastStart MP4 (~{est_total/1048576:.0f} MiB)...")
            subprocess.run(
                [
                    "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                    "-i", vpath, "-i", apath,
                    "-map", "0:v:0", "-map", "1:a:0",
                    "-c", "copy", "-movflags", "+faststart",
                    out_mp4,
                ],
                check=True,
            )
            os.remove(vpath)
            os.remove(apath)
            actual_dur = probe_duration_sec(out_mp4)
            up = upload_asset_streaming(token, release_id, out_mp4, single_name)
            os.remove(out_mp4)
            uploaded_parts.append({"asset_name": single_name, "url": up["url"], "size": up["size"], "duration": actual_dur})
        else:
            num_parts = max(2, math.ceil(est_total / TARGET_PART_BYTES))
            if dur_sec <= 0:
                dur_sec = probe_duration_sec(vpath)
            seg_time = math.ceil(dur_sec / num_parts) + 20
            seg_pattern = os.path.join(vdir, f"kharma_{vid}_1080p_part%d.mp4")
            log(f"🎬 [{vid}] Splitting marathon 1080p into {num_parts} FastStart parts (~{est_total/1048576:.0f} MiB)...")
            subprocess.run(
                [
                    "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                    "-i", vpath, "-i", apath,
                    "-map", "0:v:0", "-map", "1:a:0",
                    "-c", "copy",
                    "-f", "segment",
                    "-segment_time", str(seg_time),
                    "-reset_timestamps", "1",
                    "-segment_format_options", "movflags=+faststart",
                    seg_pattern,
                ],
                check=True,
            )
            os.remove(vpath)
            os.remove(apath)

            raw_parts = sorted(
                f for f in os.listdir(vdir) if f.startswith(f"kharma_{vid}_1080p_part") and f.endswith(".mp4")
            )
            for p_idx, raw_fname in enumerate(raw_parts, start=1):
                p_full = os.path.join(vdir, raw_fname)
                asset_name = f"kharma_{vid}_1080p_part{p_idx}.mp4"
                part_dur = probe_duration_sec(p_full)
                up = upload_asset_streaming(token, release_id, p_full, asset_name)
                os.remove(p_full)
                uploaded_parts.append({"asset_name": asset_name, "url": up["url"], "size": up["size"], "duration": part_dur})

        update_stream_json(vid, meta_entry, uploaded_parts)
        log(f"✅ [{vid}] COMPLETED 1080p ({len(uploaded_parts)} file(s))!")
    finally:
        shutil.rmtree(vdir, ignore_errors=True)


def main():
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        print("Missing GITHUB_TOKEN env var", file=sys.stderr)
        sys.exit(1)

    target_ids = sys.argv[1:]
    cat = json.load(open(os.path.join(ROOT, "catalog/movie/kharma_all.json"), encoding="utf-8"))["metas"]
    meta_by_vid = {m["id"].replace("km_", ""): m for m in cat}
    if not target_ids:
        target_ids = list(meta_by_vid.keys())

    os.makedirs(WORK_ROOT, exist_ok=True)
    rel = ensure_release(token)
    release_id = rel["id"]
    existing_assets = get_existing_assets(token, release_id)
    log(f"📦 Release {TAG} ready (id={release_id}, existing uploaded={len(existing_assets)})")

    lanes = int(os.environ.get("TOR_LANES", "8"))
    for vid in target_ids:
        process_video(vid, meta_by_vid[vid], token, release_id, existing_assets, num_lanes=lanes)

    subprocess.run([sys.executable, os.path.join(ROOT, "build_bundle.py")], check=True)
    log("🎉 Batch complete!")


if __name__ == "__main__":
    main()
