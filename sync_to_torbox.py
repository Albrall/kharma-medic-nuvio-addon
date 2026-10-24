#!/usr/bin/env python3
"""
sync_to_torbox.py — الأداة التلقائية الكاملة لربط ومزامنة TorBox (Turnkey Autopilot)
===================================================================================
بمجرد تجديد اشتراك TorBox (Essential $3 أو أعلى)، يقوم هذا السكربت بكل شيء تلقائياً:
  1. التحقق من حالة حساب TorBox ونوع الباقة الفعّالة (/v1/api/user/me).
  2. فحص الكاش العالمي لـ TorBox (/v1/api/webdl/checkcached) لتفعيل الجلسات الجاهزة فوراً بـ 0 ثانية!
  3. جدولة وإضافة جميع الـ 17 جلسة لـ Kharma Medic مع إدارة ذكية لعدد الخانات المتزامنة
     (مثلاً 3 خانات نشطة في باقة Essential) بدون أي خطأ ACTIVE_LIMIT أو تكرار!
  4. توليد ملف الربط السريع torbox_map.json (بدون أي مفتاح سري) وتحديث حزم Vercel و Cloudflare.
  5. خيار النشر التلقائي المباشر بضغطة واحدة إلى Vercel REST API (--deploy-vercel).

أمثلة الاستخدام:
  python3 sync_to_torbox.py <TORBOX_API_KEY>
  python3 sync_to_torbox.py <TORBOX_API_KEY> --wait
  python3 sync_to_torbox.py <TORBOX_API_KEY> --wait --deploy-vercel <VERCEL_TOKEN>
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
DEFAULT_API_BASE = os.environ.get("TORBOX_API_BASE", "https://api.torbox.app").rstrip("/")

PLAN_NAMES = {
    0: "Free (مجاني — لا يدعم API أو Web Downloads)",
    1: "Essential ($3/mo)",
    2: "Pro ($10/mo)",
    3: "Standard ($5/mo)",
}


def load_sessions():
    cat_path = os.path.join(ROOT, "catalog/movie/kharma_all.json")
    metas = json.load(open(cat_path, encoding="utf-8"))["metas"]
    sessions = []
    for m in metas:
        yt_id = m["id"].replace("km_", "")
        yt_url = f"https://www.youtube.com/watch?v={yt_id}"
        sessions.append(
            {
                "ytId": yt_id,
                "id": m["id"],
                "name": m["name"],
                "ytUrl": yt_url,
                "md5": hashlib.md5(yt_url.encode("utf-8")).hexdigest(),
                "md5Short": hashlib.md5(
                    f"https://youtu.be/{yt_id}".encode("utf-8")
                ).hexdigest(),
            }
        )
    return sessions


def api_get(api_base: str, api_key: str, path: str, params: dict = None):
    url = f"{api_base}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(
        url,
        method="GET",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
            "User-Agent": "kharma-nuvio-torbox-autopilot/2.0",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def api_post_multipart(api_base: str, api_key: str, path: str, fields: dict):
    boundary = "----KharmaTorBoxAutopilotBoundary7MA4YWxk"
    lines = []
    for k, v in fields.items():
        lines.append(f"--{boundary}")
        lines.append(f'Content-Disposition: form-data; name="{k}"')
        lines.append("")
        lines.append(str(v))
    lines.append(f"--{boundary}--")
    lines.append("")
    payload = "\r\n".join(lines).encode("utf-8")

    req = urllib.request.Request(
        f"{api_base}{path}",
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Accept": "application/json",
            "User-Agent": "kharma-nuvio-torbox-autopilot/2.0",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def verify_user(api_base: str, api_key: str):
    res = api_get(api_base, api_key, "/v1/api/user/me")
    data = res.get("data") or {}
    plan = data.get("plan", 0)
    plan_label = PLAN_NAMES.get(plan, f"Plan #{plan}")
    print(f"👤 حساب TorBox متصل بنجاح | الباقة: {plan_label}")
    if plan == 0 and not data.get("is_subscribed"):
        print("⚠️ تنبيه: الحساب لا يزال على الباقة المجانية (Free). فعّل باقة Essential ($3) أولاً ثم أعد التشغيل.")
        return False
    return True


def check_global_cache(api_base: str, api_key: str, sessions: list) -> set:
    hashes = ",".join(s["md5"] for s in sessions)
    cached_hashes = set()
    try:
        res = api_get(
            api_base,
            api_key,
            "/v1/api/webdl/checkcached",
            {"hash": hashes, "format": "list"},
        )
        data = res.get("data")
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict) and item.get("hash"):
                    cached_hashes.add(item["hash"].lower())
                elif isinstance(item, str):
                    cached_hashes.add(item.lower())
        elif isinstance(data, dict):
            cached_hashes.update(k.lower() for k, v in data.items() if v)
    except Exception:
        pass
    return cached_hashes


def get_mylist(api_base: str, api_key: str, bypass_cache: bool = True) -> list:
    params = {"limit": "1000"}
    if bypass_cache:
        params["bypass_cache"] = "true"
    res = api_get(api_base, api_key, "/v1/api/webdl/mylist", params)
    data = res.get("data")
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return [data]
    return []


def match_entry(session: dict, items: list):
    yt_low = session["ytId"].lower()
    target_hashes = {session["md5"].lower(), session["md5Short"].lower()}
    for it in items:
        if not isinstance(it, dict):
            continue
        h = str(it.get("hash") or "").lower()
        if h and h in target_hashes:
            return it
        name_low = str(it.get("name") or "").lower()
        if yt_low in name_low:
            return it
        files = it.get("files") or []
        if isinstance(files, list):
            for f in files:
                fname = str((f or {}).get("name") or (f or {}).get("short_name") or "").lower()
                if yt_low in fname:
                    return it
    return None


def is_ready(entry: dict) -> bool:
    if not entry:
        return False
    state = str(entry.get("download_state") or "").lower()
    if state in ("expired", "error", "failed"):
        return False
    files = entry.get("files")
    has_files = isinstance(files, list) and len(files) > 0
    if entry.get("download_present") or entry.get("download_finished"):
        return has_files
    if state in ("cached", "completed", "uploading", "seeding"):
        return has_files
    return False


def count_active(items: list) -> int:
    active = 0
    for it in items:
        if isinstance(it, dict) and not is_ready(it):
            state = str(it.get("download_state") or "").lower()
            if state not in ("expired", "error", "failed"):
                active += 1
    return active


def pick_best_file(entry: dict):
    files = entry.get("files") or []
    if not isinstance(files, list) or not files:
        return None
    video_exts = (".mp4", ".mkv", ".webm", ".mov", ".m4v")
    scored = []
    for idx, f in enumerate(files):
        if not isinstance(f, dict):
            continue
        fname = str(f.get("short_name") or f.get("name") or "")
        is_vid = fname.lower().endswith(video_exts) or str(f.get("mimetype") or "").startswith("video/")
        size = int(f.get("size") or 0)
        fid = f["id"] if ("id" in f and f["id"] is not None) else idx
        scored.append((1 if is_vid else 0, size, fid, fname))
    if not scored:
        return None
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    _, size, fid, fname = scored[0]
    return {"file_id": fid, "size": size, "name": fname}


def write_torbox_map(sessions: list, items: list):
    tb_map = {}
    for s in sessions:
        matched = match_entry(s, items)
        if matched and is_ready(matched):
            bf = pick_best_file(matched)
            if bf:
                tb_map[s["ytId"]] = {
                    "web_id": matched.get("id"),
                    "file_id": bf["file_id"],
                    "size": bf["size"],
                    "name": bf["name"],
                    "hash": matched.get("hash") or s["md5"],
                }
    map_path = os.path.join(ROOT, "torbox_map.json")
    with open(map_path, "w", encoding="utf-8") as f:
        json.dump(tb_map, f, ensure_ascii=False, indent=2)
    subprocess.run([sys.executable, os.path.join(ROOT, "build_bundle.py")], check=True)
    return tb_map


def sync_all(api_base: str, api_key: str, wait_all: bool = False, max_wait_sec: int = 1200):
    sessions = load_sessions()
    cached_hashes = check_global_cache(api_base, api_key, sessions)
    if cached_hashes:
        print(f"⚡ تم اكتشاف {len(cached_hashes)} جلسة مخزنة مسبقاً في الكاش الفوري لـ TorBox!")

    items = get_mylist(api_base, api_key, bypass_cache=True)

    # ترتيب ذكي: المخزنة في الكاش العالمي أولاً (تنتهي في 0 ثانية ولا تستهلك خانة نشطة)، ثم الأحدث/الأهم
    ordered = sorted(
        sessions,
        key=lambda s: (0 if s["md5"].lower() in cached_hashes else 1),
    )

    pending_to_add = []
    for s in ordered:
        ex = match_entry(s, items)
        if ex:
            st = "✅ جاهز" if is_ready(ex) else f"⏳ قيد التحميل ({int(float(ex.get('progress') or 0)*100)}%)"
            print(f"  • [{s['name']}] موجود مسبقاً في حسابك ({st})")
        else:
            pending_to_add.append(s)

    idx = 0
    started = time.time()
    while idx < len(pending_to_add):
        s = pending_to_add[idx]
        label = f"Kharma Medic - {s['name']} [{s['ytId']}]"
        try:
            res = api_post_multipart(
                api_base,
                api_key,
                "/v1/api/webdl/createwebdownload",
                {"link": s["ytUrl"], "name": label},
            )
            detail = res.get("detail") or "تمت الإضافة"
            print(f"✅ تمت إضافة [{s['name']}] -> {detail}")
            idx += 1
            time.sleep(1.1)
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="ignore")
            if ("ACTIVE_LIMIT" in body or "limit" in body.lower() or e.code == 429) and (
                time.time() - started < max_wait_sec
            ):
                print(
                    f"⏳ الخانات النشطة ممتلئة حالياً عند [{s['name']}] — ننتظر 15 ثانية حتى تنتهي إحدى الجلسات ثم نكمل تلقائياً..."
                )
                time.sleep(15)
            else:
                print(f"❌ تعذرت إضافة [{s['name']}] (HTTP {e.code}): {body}")
                idx += 1

    if wait_all:
        print("\n⏳ جاري متابعة اكتمال الجلسات في سحابة TorBox وتحديث torbox_map.json...")
        poll_start = time.time()
        while time.time() - poll_start < max_wait_sec:
            items = get_mylist(api_base, api_key, bypass_cache=True)
            ready_n = sum(1 for s in sessions if is_ready(match_entry(s, items)))
            active_n = count_active(items)
            print(f"  📊 الجاهز الآن: {ready_n}/{len(sessions)} جلسة | قيد التحميل النشط: {active_n}")
            if ready_n == len(sessions) or active_n == 0:
                break
            time.sleep(12)

    items = get_mylist(api_base, api_key, bypass_cache=True)
    tb_map = write_torbox_map(sessions, items)
    print(f"\n🎉 اكتملت المزامنة! الجلسات الجاهزة فوراً في torbox_map.json: {len(tb_map)}/{len(sessions)}")
    return tb_map


def deploy_to_vercel(vercel_token: str, torbox_api_key: str, project_name: str = "kharma-medic-torbox"):
    """يرفع الإضافة الديناميكية مباشرة إلى حساب Vercel عبر REST API ويحقن مفتاح TORBOX_API_KEY مشفراً."""
    print(f"\n🚀 جاري النشر التلقائي إلى Vercel ({project_name})...")
    files_to_include = [
        "vercel.json",
        "package.json",
        "manifest.json",
        "api/index.js",
        "lib/addon_data.js",
        "lib/torbox_resolver.js",
    ]
    vercel_files = []
    for rel in files_to_include:
        full = os.path.join(ROOT, rel)
        content = open(full, "rb").read().decode("utf-8")
        vercel_files.append({"file": rel, "data": content})

    headers = {
        "Authorization": f"Bearer {vercel_token}",
        "Content-Type": "application/json",
        "User-Agent": "kharma-nuvio-vercel-deployer/2.0",
    }

    # 1. Create or ensure project + encrypted env var TORBOX_API_KEY
    try:
        proj_req = urllib.request.Request(
            "https://api.vercel.com/v10/projects",
            data=json.dumps({"name": project_name}).encode("utf-8"),
            method="POST",
            headers=headers,
        )
        urllib.request.urlopen(proj_req, timeout=20).read()
    except urllib.error.HTTPError as e:
        if e.code != 409:  # 409 = already exists
            pass

    # Upsert TORBOX_API_KEY env var
    env_payload = [
        {
            "key": "TORBOX_API_KEY",
            "value": torbox_api_key,
            "type": "encrypted",
            "target": ["production", "preview"],
        }
    ]
    env_req = urllib.request.Request(
        f"https://api.vercel.com/v10/projects/{project_name}/env?upsert=true",
        data=json.dumps(env_payload).encode("utf-8"),
        method="POST",
        headers=headers,
    )
    urllib.request.urlopen(env_req, timeout=20).read()

    # 2. Create production deployment
    dep_payload = {
        "name": project_name,
        "target": "production",
        "files": vercel_files,
        "projectSettings": {"framework": None},
    }
    dep_req = urllib.request.Request(
        "https://api.vercel.com/v13/deployments?skipAutoDetectionConfirmation=1",
        data=json.dumps(dep_payload).encode("utf-8"),
        method="POST",
        headers=headers,
    )
    with urllib.request.urlopen(dep_req, timeout=60) as resp:
        dep_data = json.loads(resp.read().decode("utf-8"))

    aliases = dep_data.get("alias") or []
    prod_domain = aliases[0] if aliases else dep_data.get("url")
    manifest_url = f"https://{prod_domain}/tb/manifest.json"
    print(f"✅ تم النشر بنجاح! رابط الإضافة الجاهز للتثبيت في Nuvio:\n   {manifest_url}")
    return manifest_url


def main():
    parser = argparse.ArgumentParser(description="Kharma Medic × TorBox Turnkey Autopilot")
    parser.add_argument("api_key", help="مفتاح TorBox API الخاص بك")
    parser.add_argument("--api-base", default=DEFAULT_API_BASE, help="عنوان TorBox API")
    parser.add_argument("--wait", action="store_true", help="انتظار اكتمال التحميل السحابي وتوليد الخريطة الكاملة")
    parser.add_argument("--deploy-vercel", metavar="VERCEL_TOKEN", help="نشر تلقائي مباشر إلى Vercel")
    args = parser.parse_args()

    api_key = args.api_key.strip()
    api_base = args.api_base.rstrip("/")

    if not verify_user(api_base, api_key):
        sys.exit(2)

    sync_all(api_base, api_key, wait_all=args.wait)

    if args.deploy_vercel:
        deploy_to_vercel(args.deploy_vercel.strip(), api_key)


if __name__ == "__main__":
    main()
