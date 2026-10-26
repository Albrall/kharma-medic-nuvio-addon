#!/usr/bin/env python3
"""
build_seamless_marathons.py
---------------------------
Stitches the multi-part 1080p marathon MP4s into ONE single unbroken continuous
1080p Full HD HLS VOD stream per session (using continuous single-muxer MPEG-TS
packed into <=1.44 GiB #EXT-X-BYTERANGE assets on GitHub Releases v2.1-hls-1080p),
hosted at https://albrall.github.io/kharma-medic-nuvio-addon/hls/km_<ytId>.m3u8.
"""
import concurrent.futures
import http.client
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
WORK = "/var/tmp/km_hls_build"
REPO = "Albrall/kharma-medic-nuvio-addon"
SRC_TAG = "v2.0-1080p"
DST_TAG = "v2.1-hls-1080p"
PAGES_BASE = "https://albrall.github.io/kharma-medic-nuvio-addon"
MAX_PACK_BYTES = 1_550_000_000  # ~1.44 GiB per .ts pack

MARATHONS = [
    "V3DNjGB27Go",  # 6h 46m
    "cPTRA1gIWak",  # 7h 02m
    "1Z33r0rOlNc",  # 8h 53m
    "fJP7c8xt1n4",  # 10h 01m
    "A_BjvkJOetA",  # 12h 01m
]


def log(msg: str):
    ts = time.strftime("%H:%M:%S")
    try:
        print(f"[{ts}] {msg}", flush=True)
    except BrokenPipeError:
        pass


def gh_api(token: str, method: str, url: str, data: bytes = None):
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "User-Agent": "kharma-seamless-hls/1.0",
        },
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        raw = resp.read()
        return json.loads(raw.decode("utf-8")) if raw else {}


def ensure_release(token: str, tag: str):
    try:
        return gh_api(token, "GET", f"https://api.github.com/repos/{REPO}/releases/tags/{tag}")
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
    payload = json.dumps(
        {
            "tag_name": tag,
            "name": "Kharma Medic — Seamless 1080p Full-Length Marathon Streams",
            "body": "Unbroken single-timeline 1080p Full HD MPEG-TS byte-range streams for marathon sessions (6h–12h).",
            "draft": False,
            "prerelease": False,
        }
    ).encode("utf-8")
    return gh_api(token, "POST", f"https://api.github.com/repos/{REPO}/releases", data=payload)


def upload_ts_streaming(token: str, release_id: int, filepath: str, asset_name: str) -> str:
    fsize = os.path.getsize(filepath)
    for attempt in range(5):
        existing = gh_api(token, "GET", f"https://api.github.com/repos/{REPO}/releases/{release_id}/assets?per_page=100")
        for a in existing:
            if a["name"] == asset_name:
                if a.get("state") == "uploaded" and a.get("size") == fsize:
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
            conn.putheader("Content-Type", "video/mp2t")
            conn.putheader("Content-Length", str(fsize))
            conn.putheader("User-Agent", "kharma-seamless-hls/1.0")
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
                return res["browser_download_url"]
            log(f"  ⚠️ Upload retry {attempt+1} HTTP {resp.status}: {raw_body[:180]}")
        except Exception as e:
            log(f"  ⚠️ Upload retry {attempt+1} exc: {e}")
        time.sleep(3)
    raise RuntimeError(f"Failed to upload {asset_name}")


def download_part(url: str, dest: str):
    subprocess.run(
        ["curl", "-sSL", "--fail", "--retry", "4", "-o", dest, url],
        check=True,
    )


def convert_one_marathon(vid: str, meta_entry: dict, src_assets: dict, dst_rel_id: int, token: str):
    part_names = sorted(
        k for k in src_assets if k.startswith(f"kharma_{vid}_1080p_part") and k.endswith(".mp4")
    )
    if not part_names:
        raise RuntimeError(f"No source parts found for {vid}")

    vdir = os.path.join(WORK, vid)
    shutil.rmtree(vdir, ignore_errors=True)
    os.makedirs(vdir, exist_ok=True)

    try:
        log(f"🚀 [{vid}] Downloading {len(part_names)} 1080p parts from GitHub CDN...")
        t0 = time.time()
        local_parts = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            futs = []
            for pname in part_names:
                lp = os.path.join(vdir, pname)
                local_parts.append(lp)
                futs.append(pool.submit(download_part, src_assets[pname]["browser_download_url"], lp))
            for f in futs:
                f.result()
        total_mp4_bytes = sum(os.path.getsize(p) for p in local_parts)
        log(f"  ⚡ [{vid}] Downloaded {total_mp4_bytes/1048576:.0f} MiB in {time.time()-t0:.1f}s!")

        concat_txt = os.path.join(vdir, "concat.txt")
        with open(concat_txt, "w", encoding="utf-8") as cf:
            for lp in local_parts:
                cf.write(f"file '{lp}'\n")

        raw_m3u8 = os.path.join(vdir, "raw.m3u8")
        seg_pat = os.path.join(vdir, "seg_%05d.ts")
        log(f"🎬 [{vid}] Remuxing continuous single-timeline 1080p HLS (-c copy)...")
        subprocess.run(
            [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-f", "concat", "-safe", "0", "-i", concat_txt,
                "-map", "0:v:0", "-map", "0:a:0",
                "-c", "copy",
                "-f", "hls",
                "-hls_time", "15",
                "-hls_list_size", "0",
                "-hls_playlist_type", "vod",
                "-hls_segment_filename", seg_pat,
                raw_m3u8,
            ],
            check=True,
        )

        # Free input MP4 parts immediately!
        for lp in local_parts:
            os.remove(lp)

        lines = open(raw_m3u8, encoding="utf-8").read().splitlines()
        target_dur_line = next(
            (l for l in lines if l.startswith("#EXT-X-TARGETDURATION")),
            "#EXT-X-TARGETDURATION:16",
        )

        # Pack consecutive seg_XXXXX.ts into <=1.44 GiB continuous .ts chunks & upload each immediately!
        pack_idx = 1
        pack_name = f"kharma_{vid}_1080p_c{pack_idx}.ts"
        pack_path = os.path.join(vdir, pack_name)
        pack_f = open(pack_path, "wb")
        cur_offset = 0
        pack_entries = []  # (extinf, seg_len, offset, pack_idx)
        pack_urls = {}

        i = 0
        while i < len(lines):
            if lines[i].startswith("#EXTINF:"):
                extinf = lines[i]
                seg_name = lines[i + 1].strip()
                seg_path = os.path.join(vdir, seg_name)
                seg_len = os.path.getsize(seg_path)

                if cur_offset > 0 and cur_offset + seg_len > MAX_PACK_BYTES:
                    pack_f.close()
                    pack_urls[pack_idx] = upload_ts_streaming(token, dst_rel_id, pack_path, pack_name)
                    os.remove(pack_path)

                    pack_idx += 1
                    pack_name = f"kharma_{vid}_1080p_c{pack_idx}.ts"
                    pack_path = os.path.join(vdir, pack_name)
                    pack_f = open(pack_path, "wb")
                    cur_offset = 0

                with open(seg_path, "rb") as sf:
                    shutil.copyfileobj(sf, pack_f, length=4 * 1024 * 1024)
                os.remove(seg_path)

                pack_entries.append((extinf, seg_len, cur_offset, pack_idx))
                cur_offset += seg_len
                i += 2
            else:
                i += 1

        pack_f.close()
        pack_urls[pack_idx] = upload_ts_streaming(token, dst_rel_id, pack_path, pack_name)
        os.remove(pack_path)

        # Build final VOD .m3u8 pointing to live GitHub Releases URLs
        m3u8_out = [
            "#EXTM3U",
            "#EXT-X-VERSION:4",
            "#EXT-X-PLAYLIST-TYPE:VOD",
            "#EXT-X-MEDIA-SEQUENCE:0",
            target_dur_line,
        ]
        for extinf, seg_len, offset, pidx in pack_entries:
            m3u8_out.append(extinf)
            m3u8_out.append(f"#EXT-X-BYTERANGE:{seg_len}@{offset}")
            m3u8_out.append(pack_urls[pidx])
        m3u8_out.append("#EXT-X-ENDLIST")

        os.makedirs(os.path.join(ROOT, "hls"), exist_ok=True)
        final_m3u8_path = os.path.join(ROOT, f"hls/km_{vid}.m3u8")
        with open(final_m3u8_path, "w", encoding="utf-8") as mf:
            mf.write("\n".join(m3u8_out) + "\n")

        # Update stream/movie/km_<vid>.json to ONE SINGLE continuous 1080p stream!
        stream_path = os.path.join(ROOT, f"stream/movie/km_{vid}.json")
        cur_stream_obj = json.load(open(stream_path, encoding="utf-8"))
        tail_streams = [
            s for s in cur_stream_obj.get("streams", [])
            if "1080p" not in s.get("name", "")
        ]
        desc_parts = (meta_entry.get("description") or "").split("\n\n")
        full_title = desc_parts[1].strip() if len(desc_parts) > 1 else meta_entry["name"]
        rel = meta_entry.get("releaseInfo") or ""
        hours_label = rel.split("•")[-1].strip() if "•" in rel else rel.strip()
        gb_str = f"{total_mp4_bytes / (1024**3):.2f} GB"

        seamless_stream = {
            "name": "Direct 1080p ⚡\nXbox & iPad",
            "title": f"⚡ بث مباشر فوري 1080p Full HD بدون تقطيع — مقطع واحد كامل متصل ({hours_label} • {gb_str})\n{full_title}",
            "url": f"{PAGES_BASE}/hls/km_{vid}.m3u8",
            "behaviorHints": {
                "notWebReady": False,
                "bingeGroup": f"kharma-1080p-{vid}",
            },
        }
        cur_stream_obj["streams"] = [seamless_stream] + tail_streams
        with open(stream_path, "w", encoding="utf-8") as sf:
            json.dump(cur_stream_obj, sf, ensure_ascii=False, indent=2)

        log(f"✅ [{vid}] SEAMLESS SINGLE-VIDEO 1080p READY ({len(pack_entries)} HLS segments across {pack_idx} TS packs)!")
    finally:
        shutil.rmtree(vdir, ignore_errors=True)


def main():
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        sys.exit("Missing GITHUB_TOKEN")

    src_rel = gh_api(token, "GET", f"https://api.github.com/repos/{REPO}/releases/tags/{SRC_TAG}")
    src_assets_list = gh_api(token, "GET", f"https://api.github.com/repos/{REPO}/releases/{src_rel['id']}/assets?per_page=100")
    src_assets = {a["name"]: a for a in src_assets_list if a.get("state") == "uploaded"}

    dst_rel = ensure_release(token, DST_TAG)
    cat = json.load(open(os.path.join(ROOT, "catalog/movie/kharma_all.json"), encoding="utf-8"))["metas"]
    meta_by_vid = {m["id"].replace("km_", ""): m for m in cat}

    targets = sys.argv[1:] or MARATHONS
    os.makedirs(WORK, exist_ok=True)
    for vid in targets:
        convert_one_marathon(vid, meta_by_vid[vid], src_assets, dst_rel["id"], token)

    subprocess.run([sys.executable, os.path.join(ROOT, "build_bundle.py")], check=True)
    log("🎉 ALL MARATHONS STITCHED INTO SEAMLESS SINGLE 1080p STREAMS!")


if __name__ == "__main__":
    main()
