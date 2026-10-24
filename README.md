# 📚 إضافة Kharma Medic — Study With Me لتطبيق Nuvio (Xbox & iPad)

إضافة (Addon) مخصصة ومتكاملة لتطبيق **Nuvio** (وتعمل أيضاً على **Stremio**)، تجمع جميع جلسات **Real-Time Study With Me (Pomodoro - No Music)** لقناة **Kharma Medic** (17 جلسة كاملة من ساعة واحدة حتى 12 ساعة) مع دعم البث المباشر الفوري بدون تقطيع على **Xbox** و **iPad**.

---

## ✨ أوضاع التشغيل الجاهزة

### 1️⃣ الوضع المجاني المباشر (مفعّل الآن 24/7 عبر GitHub Pages + Releases CDN)
- **رابط التثبيت المباشر:**
  ```text
  https://albrall.github.io/kharma-medic-nuvio-addon/manifest.json
  ```
- يتضمن بثاً مباشراً فورياً بصيغة `Direct MP4 720p ⚡` لجلسة الـ **12 ساعة الكاملة (`A_BjvkJOetA`)** يعمل فوراً بدون أي تقطيع على الإكسبوكس والآيباد.

### 2️⃣ وضع سحابة TorBox الفائق لجميع الـ 17 جلسة (مجهّز بالكامل وجاهز للتفعيل الفوري ⚡)
- **`sync_to_torbox.py`:** أداة مزامنة ذكية تضيف جميع الـ 17 جلسة تلقائياً إلى **TorBox Web Downloads**، تدير الخانات المتزامنة لباقة Essential ($3) تلقائياً، وتولّد خريطة `torbox_map.json`.
- **`api/index.js` + `vercel.json` (Vercel Serverless) & `cloudflare-worker.js` (Cloudflare Workers):** محرك ديناميكي يولّد روابط `tb-cdn.st` سريعة ومباشرة عند كل نقرة في الإكسبوكس والآيباد مع تخزين مؤقت ذكي لمدة 45 دقيقة (لتفادي حدود الطلبات عند التقديم والترجيع) وحفظ كامل لخصوصية مفتاح `TORBOX_API_KEY`.

```bash
# مزامنة جميع الـ 17 جلسة إلى حساب TorBox + نشر تلقائي إلى Vercel
python3 sync_to_torbox.py YOUR_TORBOX_API_KEY --wait --deploy-vercel YOUR_VERCEL_TOKEN
```
