#!/usr/bin/env python3
"""
sync_to_torbox.py
-----------------
بمجرد تجديد اشتراكك في TorBox (Essential $3 أو أعلى)، يضيف هذا السكربت جميع جلسات
Kharma Medic (الـ 17 جلسة كاملة بجودة عالية) تلقائياً إلى قسم Web Downloads في حسابك بـ TorBox
لتعمل فوراً على الإكسبوكس والآيباد والتلفزيون عبر سيرفرات TorBox CDN بدون أي تقطيع!

طريقة التشغيل:
  python3 sync_to_torbox.py YOUR_TORBOX_API_KEY
"""
import json
import sys
import time
import urllib.parse
import urllib.request
import urllib.error

VIDEOS = [
    ("A_BjvkJOetA", "12 Hour Study With Me"),
    ("fJP7c8xt1n4", "10 Hour Study With Me"),
    ("1Z33r0rOlNc", "8 Hour Study With Me"),
    ("cPTRA1gIWak", "7 Hour Study With Me"),
    ("V3DNjGB27Go", "6 Hour Study With Me #1"),
    ("H_wpady4dh4", "6 Hour Study With Me #2"),
    ("jnOIlmmVraE", "5 Hour Study With Me (2024)"),
    ("qIxx7ffhPy0", "5 Hour Study With Me"),
    ("Dx0Cko4e5Bk", "4 Hour Study With Me (2024)"),
    ("XBo0H5Rn17s", "4 Hour Study With Me (2022)"),
    ("iQ8_E9xZQg0", "4 Hour Study With Me (Classic)"),
    ("Qe2Uw2dbq7A", "3 Hour Study With Me (2024)"),
    ("qND2ScWE1e4", "3 Hour Study With Me"),
    ("R8FRmElERig", "2.5 Hour Study With Me"),
    ("yyqQJD1PMrA", "2 Hour Live Med Exam"),
    ("m23k_wS4umU", "2 Hour Study With Me"),
    ("m5HqaG9TnDk", "1 Hour Power Session"),
]


def add_web_download(api_key: str, yt_url: str, name: str):
    boundary = "----TorBoxFormBoundary7MA4YWxkTrZu0gW"
    fields = {"link": yt_url, "name": f"Kharma Medic - {name}"}
    body_lines = []
    for k, v in fields.items():
        body_lines.append(f"--{boundary}")
        body_lines.append(f'Content-Disposition: form-data; name="{k}"')
        body_lines.append("")
        body_lines.append(v)
    body_lines.append(f"--{boundary}--")
    body_lines.append("")
    payload = "\r\n".join(body_lines).encode("utf-8")

    req = urllib.request.Request(
        "https://api.torbox.app/v1/api/webdl/createwebdownload",
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "User-Agent": "kharma-nuvio-torbox-sync/1.0",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main():
    if len(sys.argv) < 2:
        print("الاستخدام: python3 sync_to_torbox.py <TORBOX_API_KEY>")
        sys.exit(1)

    api_key = sys.argv[1].strip()
    print(f"جاري إرسال {len(VIDEOS)} جلسة من Kharma Medic إلى سحابة TorBox Web Downloads...\n")

    for vid, label in VIDEOS:
        yt_url = f"https://www.youtube.com/watch?v={vid}"
        try:
            res = add_web_download(api_key, yt_url, label)
            print(f"✅ [{label}] -> {res.get('detail', 'تمت الإضافة بنجاح')}")
        except urllib.error.HTTPError as e:
            err = e.read().decode("utf-8", errors="ignore")
            print(f"❌ [{label}] خطأ HTTP {e.code}: {err}")
        time.sleep(1.2)

    print("\n🎉 اكتملت الإضافة! ستظهر جميع المقاطع في سحابة TorBox الخاصة بك وفي إضافة TorBox داخل Nuvio.")


if __name__ == "__main__":
    main()
