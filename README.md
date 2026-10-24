# 📚 إضافة Kharma Medic — Study With Me لتطبيق Nuvio

إضافة (Addon) مخصصة وجاهزة بالكامل لتطبيق **Nuvio** (وتعمل أيضاً على **Stremio**)، تجمع لك كل جلسات **Real-Time Study With Me (Pomodoro - No Music)** الخاصة بقناة **Kharma Medic** (من جلسة الساعة الواحدة حتى جلسة الـ 12 ساعة) في صفوف (Catalogs) أنيقة على الصفحة الرئيسية، مع إمكانية تشغيلها مباشرة داخل مشغل Nuvio الداخلي!

---

## ✨ مميزات الإضافة
- **بدون سيرفر مدفوع أو مفتاح API:** الإضافة مصممة بنظام **Static Addon**، مما يعني أنه يمكنك رفع المجلد مجاناً على **GitHub** (مستودع عام Public) وستعمل مدى الحياة مجاناً `24/7`.
- **3 قوائم (Catalogs) مرتبة على الصفحة الرئيسية في Nuvio:**
  1. `Kharma Medic — Study With Me (الكل)` (جميع الـ 17 جلسة).
  2. `Study With Me — جلسات 1 إلى 5 ساعات` (مرتبة تصاعدياً من ساعة إلى 5 ساعات).
  3. `Study With Me — جلسات مطولة 6 إلى 12 ساعة` (الجلسات الماراثونية).
- **خياران للتشغيل لكل مقطع:**
  - **`Nuvio Player (ytId)`:** يشغّل مقطع اليوتيوب مباشرة داخل مشغل Nuvio الداخلي (`ExoPlayer` / `libmpv`) بجودة عالية وبدون إعلانات (يتطلب نسخة Nuvio المحدثة كاملة المزايا APK / Sideloaded).
  - **`YouTube App (externalUrl)`:** خيار احتياطي يفتح الجلسة مباشرة في تطبيق يوتيوب أو SmartTube.

---

## 🚀 طريقة التثبيت الدائم مجاناً عبر GitHub (في دقيقتين)

1. أنشئ مستودعاً جديداً عاماً (**Public Repository**) على حسابك في [GitHub](https://github.com/new) وليكن اسمه مثلاً: `kharma-nuvio-addon`.
2. ارفع محتويات هذا المجلد بالكامل كما هي (`manifest.json` ومجلدات `catalog` و `meta` و `stream`) إلى المستودع واضغط **Commit changes**.
3. سيكون رابط التثبيت المباشر الخاص بك على هذا الشكل (استبدل `USERNAME` باسم حسابك في GitHub):
   ```text
   https://raw.githubusercontent.com/Albrall/kharma-medic-nuvio-addon/main/manifest.json
   ```
4. افتح تطبيق **Nuvio**:
   - اذهب إلى **Settings** ← **Content & Discovery** ← **Addons** (أو **Addons** من القائمة الجانبية في التلفزيون).
   - اضغط **Add Addon** (أو **Install from URL**).
   - ألصق الرابط أعلاه واضغط **Install Addon**.

---

## 🔄 تحديث المقاطع مستقبلاً
إذا نشر Kharma Medic جلسات Study With Me جديدة مستقبلاً، فقط شغّل السكربت المرفق:
```bash
python3 update_addon.py
```
وسيحدث جميع ملفات `catalog` و `meta` و `stream` تلقائياً!
