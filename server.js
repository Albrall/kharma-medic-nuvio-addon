const http = require("http");
const fs = require("fs");
const path = require("path");

const PORT = process.env.PORT || 7000;
const ROOT = __dirname;

const CORS_HEADERS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type",
};

function renderLandingPage(host, proto) {
  const manifestUrl = `${proto}://${host}/manifest.json`;
  const stremioUrl = `stremio://${host}/manifest.json`;
  const catalogData = JSON.parse(
    fs.readFileSync(path.join(ROOT, "catalog/movie/kharma_all.json"), "utf8")
  );

  const cardsHtml = catalogData.metas
    .map(
      (m) => `
      <div class="card">
        <div class="thumb-wrap">
          <img src="${m.poster}" alt="${m.name}" loading="lazy" />
          <span class="badge">${m.releaseInfo}</span>
        </div>
        <div class="card-body">
          <h3>${m.name}</h3>
          <p>${m.description.split("\n\n")[1] || ""}</p>
        </div>
      </div>`
    )
    .join("");

  return `<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Kharma Medic — Study With Me | Nuvio Addon</title>
  <style>
    :root {
      --bg: #0b0f19;
      --surface: #131a2a;
      --surface-hover: #1b2438;
      --border: #25314d;
      --primary: #38bdf8;
      --primary-dark: #0284c7;
      --text: #f1f5f9;
      --muted: #94a3b8;
      --success: #22c55e;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: system-ui, -apple-system, "Segoe UI", Tahoma, Arial, sans-serif;
      background: radial-gradient(circle at top, #16213e 0%, var(--bg) 60%);
      color: var(--text);
      min-height: 100vh;
      padding: 2rem 1.25rem 4rem;
      line-height: 1.65;
    }
    .container { max-width: 980px; margin: 0 auto; }
    .hero {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 20px;
      padding: 2rem;
      display: flex;
      flex-direction: column;
      gap: 1.5rem;
      box-shadow: 0 20px 40px rgba(0,0,0,0.35);
      margin-bottom: 2rem;
    }
    .hero-top {
      display: flex;
      align-items: center;
      gap: 1.25rem;
      flex-wrap: wrap;
    }
    .logo {
      width: 76px;
      height: 76px;
      border-radius: 50%;
      border: 2px solid var(--primary);
      object-fit: cover;
    }
    .hero-title h1 { font-size: 1.65rem; margin-bottom: 0.25rem; }
    .hero-title p { color: var(--muted); font-size: 0.98rem; }
    .install-box {
      background: #090d16;
      border: 1px solid var(--border);
      border-radius: 14px;
      padding: 1.1rem;
      display: flex;
      flex-direction: column;
      gap: 0.85rem;
    }
    .install-label { font-size: 0.9rem; color: var(--primary); font-weight: 600; }
    .url-row {
      display: flex;
      gap: 0.75rem;
      flex-wrap: wrap;
    }
    input.manifest-input {
      flex: 1;
      min-width: 240px;
      background: var(--surface);
      border: 1px solid var(--border);
      color: var(--text);
      padding: 0.75rem 1rem;
      border-radius: 10px;
      font-family: monospace;
      font-size: 0.88rem;
      direction: ltr;
    }
    .btn {
      cursor: pointer;
      border: none;
      padding: 0.75rem 1.3rem;
      border-radius: 10px;
      font-weight: 600;
      font-size: 0.92rem;
      transition: all 0.18s ease;
      text-decoration: none;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 0.45rem;
    }
    .btn-primary {
      background: linear-gradient(135deg, var(--primary), var(--primary-dark));
      color: #fff;
    }
    .btn-primary:hover { filter: brightness(1.1); transform: translateY(-1px); }
    .btn-outline {
      background: transparent;
      border: 1px solid var(--border);
      color: var(--text);
    }
    .btn-outline:hover { background: var(--surface-hover); }
    .steps {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 1rem;
      margin-bottom: 2.25rem;
    }
    .step {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 14px;
      padding: 1.15rem;
    }
    .step-num {
      display: inline-block;
      background: rgba(56, 189, 248, 0.15);
      color: var(--primary);
      font-weight: 700;
      padding: 0.15rem 0.65rem;
      border-radius: 999px;
      font-size: 0.82rem;
      margin-bottom: 0.5rem;
    }
    .step h3 { font-size: 1rem; margin-bottom: 0.35rem; }
    .step p { font-size: 0.88rem; color: var(--muted); }
    .section-head {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 1rem;
      flex-wrap: wrap;
      gap: 0.5rem;
    }
    .grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(275px, 1fr));
      gap: 1.15rem;
    }
    .card {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 14px;
      overflow: hidden;
      transition: transform 0.2s ease, border-color 0.2s ease;
    }
    .card:hover { transform: translateY(-3px); border-color: var(--primary); }
    .thumb-wrap { position: relative; aspect-ratio: 16 / 9; background: #000; }
    .thumb-wrap img { width: 100%; height: 100%; object-fit: cover; display: block; }
    .badge {
      position: absolute;
      bottom: 8px;
      left: 8px;
      background: rgba(11, 15, 25, 0.88);
      color: #fff;
      font-size: 0.76rem;
      padding: 0.2rem 0.55rem;
      border-radius: 6px;
      direction: ltr;
      border: 1px solid rgba(255,255,255,0.12);
    }
    .card-body { padding: 0.95rem 1rem 1.1rem; }
    .card-body h3 { font-size: 0.98rem; margin-bottom: 0.4rem; direction: ltr; text-align: right; }
    .card-body p { font-size: 0.84rem; color: var(--muted); }
    .toast {
      color: var(--success);
      font-size: 0.86rem;
      display: none;
    }
  </style>
</head>
<body>
  <div class="container">
    <div class="hero">
      <div class="hero-top">
        <img class="logo" src="https://yt3.googleusercontent.com/ytc/AIdro_nX_iJMKECaF3eGa4F38RwjfSWH39a1k0S2Tnk4Qhi6iLo=s900-c-k-c0x00ffffff-no-rj" alt="Kharma Medic" />
        <div class="hero-title">
          <h1>إضافة Kharma Medic — Study With Me لتطبيق Nuvio</h1>
          <p>تضم جميع جلسات Real-Time Pomodoro (من ساعة واحدة حتى 12 ساعة بدون موسيقى) مرتبة وجاهزة للتشغيل داخل مشغل Nuvio الداخلي.</p>
        </div>
      </div>

      <div class="install-box">
        <span class="install-label">رابط الإضافة (Manifest URL) — انسخه وألصقه في Nuvio (Settings → Addons → Add Addon):</span>
        <div class="url-row">
          <input id="manifestUrl" class="manifest-input" type="text" readonly value="${manifestUrl}" />
          <button class="btn btn-primary" onclick="copyManifest()">📋 نسخ رابط Manifest</button>
          <a class="btn btn-outline" href="/manifest.json" target="_blank">عرض manifest.json</a>
        </div>
        <span id="copyToast" class="toast">✅ تم نسخ الرابط! افتح Nuvio ثم Settings → Addons → Add Addon وألصقه.</span>
      </div>
    </div>

    <div class="steps">
      <div class="step">
        <span class="step-num">الخطوة 1</span>
        <h3>استضافة مجانية دائمة (GitHub)</h3>
        <p>ارفع ملفات المجلد إلى مستودع GitHub عام (Public) واستخدم رابط raw.githubusercontent.com/.../manifest.json ليعمل 24/7 بدون سيرفر.</p>
      </div>
      <div class="step">
        <span class="step-num">الخطوة 2</span>
        <h3>التثبيت في Nuvio</h3>
        <p>ادخل إلى Nuvio ← Settings ← Content & Discovery ← Addons ← Add Addon ثم ألصق رابط manifest.json.</p>
      </div>
      <div class="step">
        <span class="step-num">الخطوة 3</span>
        <h3>التشغيل المباشر</h3>
        <p>اختر أي جلسة وستظهر لك خيارات البث: Nuvio Player (لتشغيل المقطع مباشرة داخل مشغل Nuvio) أو فتحه في تطبيق يوتيوب.</p>
      </div>
    </div>

    <div class="section-head">
      <h2>📚 الجلسات المتوفرة في الإضافة (${catalogData.metas.length} جلسة Real-Time Pomodoro)</h2>
    </div>

    <div class="grid">
      ${cardsHtml}
    </div>
  </div>

  <script>
    function copyManifest() {
      const input = document.getElementById('manifestUrl');
      input.select();
      input.setSelectionRange(0, 99999);
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(input.value);
      } else {
        document.execCommand('copy');
      }
      const toast = document.getElementById('copyToast');
      toast.style.display = 'inline';
      setTimeout(() => { toast.style.display = 'none'; }, 4000);
    }
  </script>
</body>
</html>`;
}

const server = http.createServer((req, res) => {
  if (req.method === "OPTIONS") {
    res.writeHead(204, CORS_HEADERS);
    return res.end();
  }

  const cleanUrl = decodeURIComponent(req.url.split("?")[0]);
  const host = req.headers["x-forwarded-host"] || req.headers.host || `localhost:${PORT}`;
  const proto = req.headers["x-forwarded-proto"] || "http";

  if (cleanUrl === "/" || cleanUrl === "/configure") {
    res.writeHead(200, {
      ...CORS_HEADERS,
      "Content-Type": "text/html; charset=utf-8",
    });
    return res.end(renderLandingPage(host, proto));
  }

  // Handle Stremio/Nuvio catalog pagination requests gracefully (e.g. /catalog/movie/kharma_all/skip=20.json)
  if (/^\/catalog\/movie\/[^/]+\/skip=\d+\.json$/.test(cleanUrl)) {
    res.writeHead(200, {
      ...CORS_HEADERS,
      "Content-Type": "application/json; charset=utf-8",
    });
    return res.end(JSON.stringify({ metas: [] }));
  }

  // Serve static JSON files safely inside ROOT
  const safeRelPath = path.normalize(cleanUrl).replace(/^(\.\.(\/|\\|$))+/, "");
  const filePath = path.join(ROOT, safeRelPath);

  if (!filePath.startsWith(ROOT)) {
    res.writeHead(403, CORS_HEADERS);
    return res.end(JSON.stringify({ error: "Forbidden" }));
  }

  if (fs.existsSync(filePath) && fs.statSync(filePath).isFile()) {
    const ext = path.extname(filePath).toLowerCase();
    const contentType =
      ext === ".json"
        ? "application/json; charset=utf-8"
        : "text/plain; charset=utf-8";
    res.writeHead(200, {
      ...CORS_HEADERS,
      "Content-Type": contentType,
      "Cache-Control": "public, max-age=3600",
    });
    return res.end(fs.readFileSync(filePath));
  }

  res.writeHead(404, {
    ...CORS_HEADERS,
    "Content-Type": "application/json; charset=utf-8",
  });
  res.end(JSON.stringify({ error: "Not found" }));
});

server.listen(PORT, "0.0.0.0", () => {
  console.log(`Kharma Medic Nuvio Addon running on http://0.0.0.0:${PORT}/manifest.json`);
});
