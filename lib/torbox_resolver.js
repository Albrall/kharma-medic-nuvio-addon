/**
 * Universal Kharma Medic × TorBox Nuvio/Stremio Addon Handler
 * Works identically on:
 *   1. Vercel Serverless Functions (api/index.js)
 *   2. Cloudflare Workers (cloudflare-worker.js)
 *   3. Node.js HTTP Server (server.js)
 */

const CORS_HEADERS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type, Authorization, Range",
};

// In-memory edge caches (persist across warm invocations)
const MYLIST_CACHE = new Map();     // key -> { items, fetchedAt, ttlMs }
const CDN_LINK_CACHE = new Map();   // `${key}:${webId}:${fileId}` -> { url, expiresAt }
const AUTO_QUEUE_SEEN = new Set();  // `${key}:${ytId}` -> prevents duplicate background adds
const SLICE_SIGNED_URL_CACHE = new Map(); // sliceUrl -> { signedUrl, expiresAt }

const CDN_TTL_MS = 45 * 60 * 1000; // 45 minutes (TorBox links stay valid 1–3 hours)
const SLICE_SIGNED_TTL_MS = 180 * 1000; // 3 minutes (GitHub signed asset URLs last ~5m)

async function resolveSignedSliceUrl(sliceUrl) {
  const now = Date.now();
  const hit = SLICE_SIGNED_URL_CACHE.get(sliceUrl);
  if (hit && hit.expiresAt > now) {
    return hit.signedUrl;
  }
  try {
    const resp = await fetch(sliceUrl, {
      method: "GET",
      redirect: "manual",
      headers: { "User-Agent": "kharma-medic-edge-mp4/2.2" },
    });
    const loc = resp.headers.get("location");
    try {
      await resp.body?.cancel();
    } catch (_) {}
    if (loc && loc.startsWith("http")) {
      SLICE_SIGNED_URL_CACHE.set(sliceUrl, {
        signedUrl: loc,
        expiresAt: now + SLICE_SIGNED_TTL_MS,
      });
      return loc;
    }
  } catch (_) {}
  return sliceUrl;
}

async function openSliceRangeResponse(sliceUrl, localStart, localEnd, signal) {
  const rangeHdr = `bytes=${localStart}-${localEnd}`;
  const targetUrl = await resolveSignedSliceUrl(sliceUrl);
  let resp = await fetch(targetUrl, {
    method: "GET",
    redirect: "follow",
    signal,
    headers: {
      Range: rangeHdr,
      "User-Agent": "kharma-medic-edge-mp4/2.2",
    },
  });
  if ((resp.status === 401 || resp.status === 403 || resp.status === 404) && targetUrl !== sliceUrl) {
    try {
      await resp.body?.cancel();
    } catch (_) {}
    SLICE_SIGNED_URL_CACHE.delete(sliceUrl);
    resp = await fetch(sliceUrl, {
      method: "GET",
      redirect: "follow",
      signal,
      headers: {
        Range: rangeHdr,
        "User-Agent": "kharma-medic-edge-mp4/2.2",
      },
    });
  }
  if (resp.status !== 206 && !(localStart === 0 && resp.status === 200)) {
    const st = resp.status;
    try {
      await resp.body?.cancel();
    } catch (_) {}
    throw new Error(`Upstream slice HTTP ${st} for ${rangeHdr}`);
  }
  return resp;
}

function parseHttpRange(rangeHeader, totalSize) {
  if (!rangeHeader || !/^bytes=/i.test(rangeHeader.trim())) {
    return { partial: false, start: 0, end: totalSize - 1 };
  }
  const spec = rangeHeader.trim().replace(/^bytes=/i, "").split(",")[0].trim();
  const dash = spec.indexOf("-");
  if (dash === -1) return null;

  const startStr = spec.slice(0, dash).trim();
  const endStr = spec.slice(dash + 1).trim();

  let start, end;
  if (startStr === "") {
    // Suffix byte-range-spec: bytes=-N
    const suffix = Number(endStr);
    if (!Number.isFinite(suffix) || suffix <= 0) return null;
    start = Math.max(0, totalSize - Math.floor(suffix));
    end = totalSize - 1;
  } else {
    start = Number(startStr);
    if (!Number.isFinite(start) || start < 0 || !Number.isInteger(start)) return null;
    if (endStr === "") {
      end = totalSize - 1;
    } else {
      end = Number(endStr);
      if (!Number.isFinite(end) || !Number.isInteger(end)) return null;
    }
  }

  if (start >= totalSize || start > end) return null;
  end = Math.min(end, totalSize - 1);
  return { partial: true, start, end };
}

function handleVirtualMp4Request(method, vid, rangeHeader, marathonMap) {
  const entry = marathonMap?.[vid];
  if (!entry || !Array.isArray(entry.slices) || !entry.total_size) {
    return {
      status: 404,
      headers: { ...CORS_HEADERS, "Content-Type": "application/json; charset=utf-8" },
      body: JSON.stringify({ error: "Marathon MP4 not found", vid }),
      isStream: false,
    };
  }

  const totalSize = Number(entry.total_size);
  const parsed = parseHttpRange(rangeHeader, totalSize);
  const baseHeaders = {
    ...CORS_HEADERS,
    "Access-Control-Expose-Headers": "Content-Length, Content-Range, Accept-Ranges",
    "Accept-Ranges": "bytes",
    "Content-Type": "video/mp4",
    "Content-Disposition": `inline; filename="kharma_${vid}_1080p.mp4"`,
    "Cache-Control": "no-transform",
  };

  if (!parsed) {
    return {
      status: 416,
      headers: {
        ...baseHeaders,
        "Content-Range": `bytes */${totalSize}`,
        "Content-Length": "0",
      },
      body: "",
      isStream: false,
    };
  }

  const { partial, start, end } = parsed;
  const contentLength = end - start + 1;
  const status = partial ? 206 : 200;
  const headers = {
    ...baseHeaders,
    "Content-Length": String(contentLength),
    ...(partial ? { "Content-Range": `bytes ${start}-${end}/${totalSize}` } : {}),
  };

  if (method === "HEAD") {
    return { status, headers, body: null, isStream: true };
  }

  // Build exact cross-slice execution plan
  const plan = [];
  for (const s of entry.slices) {
    if (s.end < start || s.start > end) continue;
    const localStart = Math.max(0, start - s.start);
    const localEnd = Math.min(s.size - 1, end - s.start);
    plan.push({
      url: s.url,
      localStart,
      localEnd,
      expected: localEnd - localStart + 1,
    });
  }

  let qIdx = 0;
  let bytesReadInSlice = 0;
  let retriesInSlice = 0;
  let activeReader = null;
  let activeAbort = null;
  let cancelled = false;

  const stream = new ReadableStream({
    async pull(controller) {
      while (!cancelled) {
        if (!activeReader) {
          if (qIdx >= plan.length) {
            controller.close();
            return;
          }
          const seg = plan[qIdx];
          const curStart = seg.localStart + bytesReadInSlice;
          activeAbort = new AbortController();
          const resp = await openSliceRangeResponse(
            seg.url,
            curStart,
            seg.localEnd,
            activeAbort.signal
          );
          activeReader = resp.body.getReader();
        }

        let readRes;
        try {
          readRes = await activeReader.read();
        } catch (err) {
          try {
            activeReader?.releaseLock();
          } catch (_) {}
          activeReader = null;
          if (cancelled) return;
          if (retriesInSlice < 4) {
            retriesInSlice += 1;
            continue;
          }
          throw err;
        }

        const { done, value } = readRes;
        if (done) {
          try {
            activeReader.releaseLock();
          } catch (_) {}
          activeReader = null;
          activeAbort = null;
          const seg = plan[qIdx];
          if (bytesReadInSlice < seg.expected) {
            if (retriesInSlice < 4) {
              retriesInSlice += 1;
              continue;
            }
            throw new Error(
              `Incomplete slice read (${bytesReadInSlice}/${seg.expected})`
            );
          }
          qIdx += 1;
          bytesReadInSlice = 0;
          retriesInSlice = 0;
          continue;
        }

        if (value && value.byteLength > 0) {
          retriesInSlice = 0;
          const seg = plan[qIdx];
          const remaining = seg.expected - bytesReadInSlice;
          const chunk =
            value.byteLength > remaining ? value.subarray(0, remaining) : value;
          bytesReadInSlice += chunk.byteLength;
          controller.enqueue(chunk);
          if (bytesReadInSlice >= seg.expected) {
            try {
              await activeReader.cancel();
            } catch (_) {}
            activeReader = null;
            activeAbort = null;
            qIdx += 1;
            bytesReadInSlice = 0;
          }
          return;
        }
      }
    },
    cancel() {
      cancelled = true;
      try {
        activeReader?.cancel();
      } catch (_) {}
      try {
        activeAbort?.abort();
      } catch (_) {}
      activeReader = null;
      activeAbort = null;
    },
  });

  return { status, headers, body: stream, isStream: true };
}

function cleanIpv4(rawHeader) {
  if (!rawHeader) return null;
  const first = String(rawHeader).split(",")[0].trim();
  if (/^\d{1,3}(\.\d{1,3}){3}$/.test(first)) {
    const parts = first.split(".").map(Number);
    if (parts.every((n) => n >= 0 && n <= 255)) {
      // Skip private/loopback ranges
      if (parts[0] === 10 || parts[0] === 127) return null;
      if (parts[0] === 192 && parts[1] === 168) return null;
      if (parts[0] === 172 && parts[1] >= 16 && parts[1] <= 31) return null;
      return first;
    }
  }
  return null;
}

function parseRouteAndToken(pathname, envToken) {
  let clean = decodeURIComponent((pathname || "/").split("?")[0]);
  if (clean.length > 1 && clean.endsWith("/")) {
    clean = clean.slice(0, -1);
  }

  // Case 1: /tb/<TOKEN>/...
  const tbWithToken = clean.match(
    /^\/tb\/([^/]+)(\/(?:manifest\.json|configure|status|catalog\/.*|meta\/.*|stream\/.*))?$/
  );
  if (
    tbWithToken &&
    tbWithToken[1] !== "manifest.json" &&
    tbWithToken[1] !== "configure" &&
    tbWithToken[1] !== "status" &&
    tbWithToken[1] !== "catalog" &&
    tbWithToken[1] !== "meta" &&
    tbWithToken[1] !== "stream"
  ) {
    return {
      token: tbWithToken[1].trim(),
      subpath: tbWithToken[2] || "/configure",
      prefix: `/tb/${encodeURIComponent(tbWithToken[1].trim())}`,
    };
  }

  // Case 2: /tb/... (uses server env TORBOX_API_KEY)
  if (clean === "/tb" || clean.startsWith("/tb/")) {
    return {
      token: (envToken || "").trim() || null,
      subpath: clean.slice(3) || "/configure",
      prefix: "/tb",
    };
  }

  // Case 3: /<TOKEN>/(manifest.json|catalog/...|meta/...|stream/...|configure|status)
  const directToken = clean.match(
    /^\/([^/]+)(\/(?:manifest\.json|configure|status|catalog\/.*|meta\/.*|stream\/.*))$/
  );
  if (
    directToken &&
    !["api", "catalog", "meta", "stream", "lib", "tb", "mp4"].includes(directToken[1])
  ) {
    return {
      token: directToken[1].trim(),
      subpath: directToken[2],
      prefix: `/${encodeURIComponent(directToken[1].trim())}`,
    };
  }

  // Case 4: Root path (/, /manifest.json, /stream/movie/..., etc.)
  return {
    token: (envToken || "").trim() || null,
    subpath: clean || "/",
    prefix: "",
  };
}

async function fetchWithTimeout(url, options = {}, timeoutMs = 8500) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, { ...options, signal: controller.signal });
  } finally {
    clearTimeout(timer);
  }
}

async function getTorBoxWebList(apiKey, apiBase, forceFresh = false) {
  const now = Date.now();
  const cached = MYLIST_CACHE.get(apiKey);
  if (!forceFresh && cached && now - cached.fetchedAt < cached.ttlMs) {
    return cached.items;
  }

  const qs = forceFresh ? "?bypass_cache=true&limit=1000" : "?limit=1000";
  const resp = await fetchWithTimeout(`${apiBase}/v1/api/webdl/mylist${qs}`, {
    method: "GET",
    headers: {
      Authorization: `Bearer ${apiKey}`,
      Accept: "application/json",
      "User-Agent": "kharma-medic-nuvio-addon/2.0",
    },
  });

  if (!resp.ok) {
    throw new Error(`TorBox mylist HTTP ${resp.status}`);
  }

  const body = await resp.json();
  let items = [];
  if (Array.isArray(body?.data)) {
    items = body.data;
  } else if (body?.data && typeof body.data === "object") {
    items = [body.data];
  }

  // Shorter cache TTL while any item is actively downloading
  const anyActive = items.some(
    (it) => it && !it.download_present && Number(it.progress || 0) < 1
  );
  const ttlMs = anyActive ? 10 * 1000 : 45 * 1000;
  MYLIST_CACHE.set(apiKey, { items, fetchedAt: now, ttlMs });
  return items;
}

function pickBestFile(entry) {
  const files = Array.isArray(entry?.files) ? entry.files : [];
  if (files.length === 0) return null;

  const videoExts = [".mp4", ".mkv", ".webm", ".mov", ".m4v"];
  const scored = files.map((f, idx) => {
    const fname = String(f?.name || f?.short_name || "").toLowerCase();
    const isVideo =
      videoExts.some((ext) => fname.endsWith(ext)) ||
      String(f?.mimetype || "").startsWith("video/");
    const size = Number(f?.size) > 0 ? Number(f.size) : 0;
    const fileId = f?.id !== undefined && f?.id !== null ? f.id : idx;
    return { raw: f, fileId, size, isVideo, name: f?.short_name || f?.name || "" };
  });

  scored.sort((a, b) => {
    if (a.isVideo !== b.isVideo) return a.isVideo ? -1 : 1;
    return b.size - a.size;
  });

  return scored[0];
}

function matchSessionEntry(session, items, torboxMap) {
  const ytId = session.ytId;
  const mapped = torboxMap?.[ytId];

  // 1. Explicit web_id match from torbox_map.json
  if (mapped && mapped.web_id !== undefined && mapped.web_id !== null) {
    const byId = items.find((it) => String(it?.id) === String(mapped.web_id));
    if (byId) return byId;
  }

  // 2. Exact MD5 URL hash match (TorBox hashes the submitted link with MD5)
  const hashes = new Set(
    [session.md5Watch, session.md5Short, mapped?.hash]
      .filter(Boolean)
      .map((h) => String(h).toLowerCase())
  );
  const byHash = items.find(
    (it) => it?.hash && hashes.has(String(it.hash).toLowerCase())
  );
  if (byHash) return byHash;

  // 3. Video ID substring match in entry name or file names (e.g. "[A_BjvkJOetA]" or yt-dlp default filename)
  const idLower = ytId.toLowerCase();
  const byVid = items.find((it) => {
    if (String(it?.name || "").toLowerCase().includes(idLower)) return true;
    if (Array.isArray(it?.files)) {
      return it.files.some((f) =>
        String(f?.name || f?.short_name || "").toLowerCase().includes(idLower)
      );
    }
    return false;
  });
  if (byVid) return byVid;

  // 4. Exact label match if named "Kharma Medic - <label>"
  if (session.shortLabel) {
    const needle = session.shortLabel.toLowerCase();
    const byLabel = items.find((it) =>
      String(it?.name || "").toLowerCase().includes(needle)
    );
    if (byLabel) return byLabel;
  }

  return null;
}

function isEntryDownloadable(entry) {
  if (!entry) return false;
  const state = String(entry.download_state || "").toLowerCase();
  if (state === "expired" || state === "error" || state === "failed") return false;
  if (entry.download_present === true || entry.download_finished === true) {
    return Array.isArray(entry.files) && entry.files.length > 0;
  }
  if (["cached", "completed", "uploading", "seeding"].includes(state)) {
    return Array.isArray(entry.files) && entry.files.length > 0;
  }
  return false;
}

async function requestTorBoxCdnLink(apiKey, apiBase, webId, fileId, clientIpv4) {
  const cacheKey = `${apiKey}:${webId}:${fileId}`;
  const now = Date.now();
  const hit = CDN_LINK_CACHE.get(cacheKey);
  if (hit && hit.expiresAt > now) {
    return hit.url;
  }

  const params = new URLSearchParams({
    token: apiKey,
    web_id: String(webId),
    file_id: String(fileId),
    zip_link: "false",
    redirect: "false",
  });
  if (clientIpv4) {
    params.set("user_ip", clientIpv4);
  }

  const resp = await fetchWithTimeout(
    `${apiBase}/v1/api/webdl/requestdl?${params.toString()}`,
    {
      method: "GET",
      headers: {
        Authorization: `Bearer ${apiKey}`,
        Accept: "application/json",
        "User-Agent": "kharma-medic-nuvio-addon/2.0",
      },
    }
  );

  if (!resp.ok) {
    throw new Error(`TorBox requestdl HTTP ${resp.status}`);
  }

  const body = await resp.json();
  const dlUrl =
    typeof body?.data === "string"
      ? body.data
      : body?.data?.download_link || body?.data?.link || null;

  if (!dlUrl || !String(dlUrl).startsWith("http")) {
    throw new Error("TorBox returned empty download link");
  }

  CDN_LINK_CACHE.set(cacheKey, { url: dlUrl, expiresAt: now + CDN_TTL_MS });
  return dlUrl;
}

async function queueWebDownloadInBackground(apiKey, apiBase, session) {
  const dedupeKey = `${apiKey}:${session.ytId}`;
  if (AUTO_QUEUE_SEEN.has(dedupeKey)) return;
  AUTO_QUEUE_SEEN.add(dedupeKey);

  const boundary = "----KharmaTorBoxBoundary9f8e7d6c";
  const ytUrl = `https://www.youtube.com/watch?v=${session.ytId}`;
  const itemName = `Kharma Medic - ${session.name} [${session.ytId}]`;
  const lines = [
    `--${boundary}`,
    'Content-Disposition: form-data; name="link"',
    "",
    ytUrl,
    `--${boundary}`,
    'Content-Disposition: form-data; name="name"',
    "",
    itemName,
    `--${boundary}--`,
    "",
  ];

  try {
    await fetchWithTimeout(
      `${apiBase}/v1/api/webdl/createwebdownload`,
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${apiKey}`,
          "Content-Type": `multipart/form-data; boundary=${boundary}`,
          "User-Agent": "kharma-medic-nuvio-addon/2.0",
        },
        body: lines.join("\r\n"),
      },
      6000
    );
    // Invalidate mylist cache so next open sees the queued download
    MYLIST_CACHE.delete(apiKey);
  } catch (_) {
    AUTO_QUEUE_SEEN.delete(dedupeKey);
  }
}

function formatBytes(bytes) {
  const n = Number(bytes);
  if (!n || n <= 0) return "";
  const gb = n / (1024 * 1024 * 1024);
  if (gb >= 1) return `${gb.toFixed(2)} GB`;
  const mb = n / (1024 * 1024);
  return `${mb.toFixed(0)} MB`;
}

async function resolveStreamsForItem(data, itemId, apiKey, env, clientIpv4, origin) {
  const baseObj = data.BASE_STREAMS[itemId];
  if (!baseObj) return { streams: [] };

  const ytId = itemId.replace(/^km_/, "");
  const session = data.SESSIONS[ytId];
  const mEntry = data.MARATHON_MP4_MAP?.[ytId];
  let fallbackStreams = Array.isArray(baseObj.streams)
    ? baseObj.streams.map((s) => ({ ...s, behaviorHints: s.behaviorHints ? { ...s.behaviorHints } : undefined }))
    : [];

  if (mEntry && origin && fallbackStreams.length > 0) {
    const mp4Origin = (env?.MP4_EDGE_ORIGIN || origin).replace(/\/+$/, "");
    fallbackStreams[0] = {
      ...fallbackStreams[0],
      url: `${mp4Origin}/mp4/kharma_${ytId}_1080p.mp4`,
      behaviorHints: {
        notWebReady: false,
        bingeGroup: `kharma-1080p-${ytId}`,
        filename: mEntry.filename || `kharma_${ytId}_1080p.mp4`,
        videoSize: mEntry.total_size,
      },
    };
  }

  if (!apiKey || !session) {
    return { streams: fallbackStreams };
  }

  const apiBase = (env?.TORBOX_API_BASE || "https://api.torbox.app").replace(/\/+$/, "");
  const autoQueue = env?.TORBOX_AUTO_QUEUE !== "0";

  try {
    const items = await getTorBoxWebList(apiKey, apiBase, false);
    const matched = matchSessionEntry(session, items, data.TORBOX_MAP);

    if (matched && isEntryDownloadable(matched)) {
      const bestFile = pickBestFile(matched);
      if (bestFile) {
        const cdnUrl = await requestTorBoxCdnLink(
          apiKey,
          apiBase,
          matched.id,
          bestFile.fileId,
          clientIpv4
        );
        const sizeLabel = formatBytes(bestFile.size || matched.size);
        const filename =
          bestFile.name ||
          `KharmaMedic.${ytId}.StudyWithMe.mp4`;
        const tbStream = {
          name: "TorBox ⚡ Cloud\nXbox & iPad",
          title: `⚡ تشغيل فوري فائق السرعة عبر TorBox CDN${sizeLabel ? ` (${sizeLabel})` : ""} — ${session.hoursLabel}\n${session.fullTitle}`,
          url: cdnUrl,
          behaviorHints: {
            notWebReady: false,
            bingeGroup: "kharma-torbox-cloud",
            filename,
            ...(bestFile.size > 0 ? { videoSize: Math.round(bestFile.size) } : {}),
          },
        };
        return { streams: [tbStream, ...fallbackStreams] };
      }
    } else if (!matched && autoQueue) {
      // Automatically queue in TorBox cloud on first click
      await queueWebDownloadInBackground(apiKey, apiBase, session);
    }
  } catch (_) {
    // Graceful degradation: always return working fallback streams
  }

  return { streams: fallbackStreams };
}

async function buildTorBoxStatus(data, apiKey, env) {
  const apiBase = (env?.TORBOX_API_BASE || "https://api.torbox.app").replace(/\/+$/, "");
  if (!apiKey) {
    return {
      configured: false,
      message: "لم يتم تمرير مفتاح TorBox API بعد. مرره عبر TORBOX_API_KEY أو الرابط /tb/<API_KEY>/manifest.json",
    };
  }

  let userSummary = null;
  try {
    const uResp = await fetchWithTimeout(`${apiBase}/v1/api/user/me`, {
      headers: { Authorization: `Bearer ${apiKey}`, Accept: "application/json" },
    });
    const uJson = await uResp.json();
    if (uJson?.data) {
      userSummary = {
        plan: uJson.data.plan,
        is_subscribed: Boolean(uJson.data.is_subscribed || Number(uJson.data.plan) > 0),
        premium_expires_at: uJson.data.premium_expires_at || null,
      };
    }
  } catch (_) {}

  let items = [];
  let listError = null;
  try {
    items = await getTorBoxWebList(apiKey, apiBase, true);
  } catch (err) {
    listError = String(err.message || err);
  }

  const sessionStatuses = Object.values(data.SESSIONS).map((s) => {
    const matched = matchSessionEntry(s, items, data.TORBOX_MAP);
    const ready = isEntryDownloadable(matched);
    const bestFile = ready ? pickBestFile(matched) : null;
    return {
      ytId: s.ytId,
      name: s.name,
      hours: s.hoursLabel,
      ready,
      web_id: matched?.id ?? null,
      file_id: bestFile?.fileId ?? null,
      state: matched?.download_state ?? (matched ? "pending" : "missing"),
      progress: matched?.progress !== undefined ? Number(matched.progress) : 0,
      size_bytes: bestFile?.size || matched?.size || 0,
    };
  });

  const readyCount = sessionStatuses.filter((s) => s.ready).length;
  return {
    configured: true,
    user: userSummary,
    list_error: listError,
    total_sessions: sessionStatuses.length,
    ready_sessions: readyCount,
    sessions: sessionStatuses,
  };
}

function renderConfigureHtml(data, origin, prefix, hasEnvKey) {
  const defaultManifest = hasEnvKey
    ? `${origin}/tb/manifest.json`
    : `${origin}${prefix || ""}/manifest.json`;

  const cardsHtml = data.CATALOGS.kharma_all.metas
    .map(
      (m) => `
      <div class="card">
        <div class="thumb-wrap">
          <img src="${m.poster}" alt="${m.name}" loading="lazy" />
          <span class="badge">${m.releaseInfo}</span>
        </div>
        <div class="card-body">
          <h3>${m.name}</h3>
          <p>${(m.description || "").split("\n\n")[1] || ""}</p>
        </div>
      </div>`
    )
    .join("");

  return `<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Kharma Medic × TorBox — Nuvio Addon</title>
  <style>
    :root {
      --bg: #0b0f19; --surface: #131a2a; --surface-hover: #1b2438;
      --border: #25314d; --primary: #38bdf8; --primary-dark: #0284c7;
      --accent: #10b981; --text: #f1f5f9; --muted: #94a3b8;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: system-ui, -apple-system, "Segoe UI", Tahoma, Arial, sans-serif;
      background: radial-gradient(circle at top, #16213e 0%, var(--bg) 60%);
      color: var(--text); min-height: 100vh; padding: 2rem 1.25rem 4rem; line-height: 1.65;
    }
    .container { max-width: 980px; margin: 0 auto; }
    .hero {
      background: var(--surface); border: 1px solid var(--border); border-radius: 20px;
      padding: 2rem; display: flex; flex-direction: column; gap: 1.4rem;
      box-shadow: 0 20px 40px rgba(0,0,0,0.35); margin-bottom: 2rem;
    }
    .hero-top { display: flex; align-items: center; gap: 1.25rem; flex-wrap: wrap; }
    .logo { width: 74px; height: 74px; border-radius: 50%; border: 2px solid var(--primary); object-fit: cover; }
    .hero-title h1 { font-size: 1.55rem; margin-bottom: 0.25rem; }
    .hero-title p { color: var(--muted); font-size: 0.95rem; }
    .box {
      background: #090d16; border: 1px solid var(--border); border-radius: 14px;
      padding: 1.15rem; display: flex; flex-direction: column; gap: 0.85rem;
    }
    .label { font-size: 0.92rem; color: var(--primary); font-weight: 600; }
    .row { display: flex; gap: 0.7rem; flex-wrap: wrap; }
    input.inp {
      flex: 1; min-width: 240px; background: var(--surface); border: 1px solid var(--border);
      color: var(--text); padding: 0.75rem 1rem; border-radius: 10px;
      font-family: monospace; font-size: 0.88rem; direction: ltr;
    }
    .btn {
      cursor: pointer; border: none; padding: 0.75rem 1.25rem; border-radius: 10px;
      font-weight: 600; font-size: 0.9rem; text-decoration: none;
      display: inline-flex; align-items: center; justify-content: center; gap: 0.4rem;
    }
    .btn-primary { background: linear-gradient(135deg, var(--primary), var(--primary-dark)); color: #fff; }
    .btn-accent { background: var(--accent); color: #052e16; }
    .btn-outline { background: transparent; border: 1px solid var(--border); color: var(--text); }
    .toast { color: var(--accent); font-size: 0.86rem; display: none; }
    .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(270px, 1fr)); gap: 1.1rem; margin-top: 1rem; }
    .card { background: var(--surface); border: 1px solid var(--border); border-radius: 14px; overflow: hidden; }
    .thumb-wrap { position: relative; aspect-ratio: 16/9; background: #000; }
    .thumb-wrap img { width: 100%; height: 100%; object-fit: cover; display: block; }
    .badge {
      position: absolute; bottom: 8px; left: 8px; background: rgba(11,15,25,0.88);
      color: #fff; font-size: 0.75rem; padding: 0.2rem 0.55rem; border-radius: 6px; direction: ltr;
    }
    .card-body { padding: 0.9rem 1rem 1.05rem; }
    .card-body h3 { font-size: 0.95rem; margin-bottom: 0.35rem; direction: ltr; text-align: right; }
    .card-body p { font-size: 0.82rem; color: var(--muted); }
  </style>
</head>
<body>
  <div class="container">
    <div class="hero">
      <div class="hero-top">
        <img class="logo" src="${data.MANIFEST.logo}" alt="Kharma Medic" />
        <div class="hero-title">
          <h1>إضافة Kharma Medic × TorBox Cloud لتطبيق Nuvio</h1>
          <p>بث فوري مباشر بدون تقطيع لجميع الـ 17 جلسة على الإكسبوكس والآيباد عبر TorBox CDN + دعم دائم لجلسة الـ 12 ساعة.</p>
        </div>
      </div>

      <div class="box">
        <span class="label">⚡ ربط سريع بمفتاح TorBox API (اختياري إذا لم يكن مضافاً في السيرفر):</span>
        <div class="row">
          <input id="tbKey" class="inp" type="password" placeholder="ألصق مفتاح TorBox API هنا لتوليد رابطك الخاص فوراً..." oninput="updateUrl()" />
          <button class="btn btn-outline" onclick="toggleVis()">👁️ إظهار</button>
        </div>
        <span class="label">رابط التثبيت في Nuvio (Settings → Addons → Add Addon):</span>
        <div class="row">
          <input id="manifestUrl" class="inp" type="text" readonly value="${defaultManifest}" />
          <button class="btn btn-primary" onclick="copyManifest()">📋 نسخ رابط Manifest</button>
          <a id="statusBtn" class="btn btn-outline" href="${hasEnvKey ? "/tb/status" : "/status"}" target="_blank">🔍 فحص حالة الجلسات</a>
        </div>
        <span id="copyToast" class="toast">✅ تم نسخ الرابط! ألصقه في Nuvio واستمتع بالتشغيل الفوري.</span>
      </div>
    </div>

    <h2>📚 الجلسات المتوفرة (${data.CATALOGS.kharma_all.metas.length} جلسة Real-Time Pomodoro)</h2>
    <div class="grid">${cardsHtml}</div>
  </div>

  <script>
    const originUrl = ${JSON.stringify(origin)};
    const defaultManifest = ${JSON.stringify(defaultManifest)};
    function updateUrl() {
      const k = document.getElementById('tbKey').value.trim();
      const out = document.getElementById('manifestUrl');
      const st = document.getElementById('statusBtn');
      if (k) {
        out.value = originUrl + '/tb/' + encodeURIComponent(k) + '/manifest.json';
        st.href = originUrl + '/tb/' + encodeURIComponent(k) + '/status';
      } else {
        out.value = defaultManifest;
        st.href = originUrl + '/tb/status';
      }
    }
    function toggleVis() {
      const inp = document.getElementById('tbKey');
      inp.type = inp.type === 'password' ? 'text' : 'password';
    }
    function copyManifest() {
      const input = document.getElementById('manifestUrl');
      input.select();
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

async function handleAddonRequest({ method, pathname, origin, clientIp, rangeHeader, env }, data) {
  if (method === "OPTIONS") {
    return { status: 204, headers: CORS_HEADERS, body: "" };
  }

  // 0. Lossless Single-File Marathon Virtual MP4 Byte-Range Stitcher (/mp4/kharma_<vid>_1080p.mp4)
  const cleanRaw = decodeURIComponent((pathname || "/").split("?")[0]);
  const mp4Match = cleanRaw.match(/^\/mp4\/(?:kharma_)?([A-Za-z0-9_-]{11})(?:_1080p)?\.mp4$/);
  if (mp4Match) {
    return handleVirtualMp4Request(
      method,
      mp4Match[1],
      rangeHeader || "",
      data.MARATHON_MP4_MAP
    );
  }

  const envToken = env?.TORBOX_API_KEY || "";
  const { token, subpath, prefix } = parseRouteAndToken(pathname, envToken);
  const clientIpv4 = cleanIpv4(clientIp);

  // 1. Landing / Configure UI
  if (subpath === "/" || subpath === "/configure") {
    return {
      status: 200,
      headers: { ...CORS_HEADERS, "Content-Type": "text/html; charset=utf-8" },
      body: renderConfigureHtml(data, origin, prefix, Boolean(envToken.trim())),
    };
  }

  // 2. TorBox Readiness Status JSON
  if (subpath === "/status") {
    const statusObj = await buildTorBoxStatus(data, token, env);
    return {
      status: 200,
      headers: {
        ...CORS_HEADERS,
        "Content-Type": "application/json; charset=utf-8",
        "Cache-Control": "no-store",
      },
      body: JSON.stringify(statusObj, null, 2),
    };
  }

  // 3. Manifest
  if (subpath === "/manifest.json") {
    const manifest = {
      ...data.MANIFEST,
      version: token ? "2.0.0" : data.MANIFEST.version,
      name: token
        ? "Kharma Medic ⚡ TorBox Cloud"
        : data.MANIFEST.name,
      behaviorHints: {
        ...data.MANIFEST.behaviorHints,
        configurable: true,
        configurationRequired: false,
      },
    };
    return {
      status: 200,
      headers: {
        ...CORS_HEADERS,
        "Content-Type": "application/json; charset=utf-8",
        "Cache-Control": "public, max-age=300",
      },
      body: JSON.stringify(manifest, null, 2),
    };
  }

  // 4. Catalog pagination (skip=...) -> empty list so Nuvio stops paginating cleanly
  if (/^\/catalog\/movie\/[^/]+\/skip=\d+\.json$/.test(subpath)) {
    return {
      status: 200,
      headers: { ...CORS_HEADERS, "Content-Type": "application/json; charset=utf-8" },
      body: JSON.stringify({ metas: [] }),
    };
  }

  // 5. Catalog
  const catMatch = subpath.match(/^\/catalog\/movie\/([^/]+)\.json$/);
  if (catMatch) {
    const cat = data.CATALOGS[catMatch[1]];
    if (cat) {
      return {
        status: 200,
        headers: {
          ...CORS_HEADERS,
          "Content-Type": "application/json; charset=utf-8",
          "Cache-Control": "public, max-age=1800",
        },
        body: JSON.stringify(cat),
      };
    }
  }

  // 6. Meta
  const metaMatch = subpath.match(/^\/meta\/movie\/([^/]+)\.json$/);
  if (metaMatch) {
    const metaObj = data.METAS[metaMatch[1]];
    if (metaObj) {
      return {
        status: 200,
        headers: {
          ...CORS_HEADERS,
          "Content-Type": "application/json; charset=utf-8",
          "Cache-Control": "public, max-age=1800",
        },
        body: JSON.stringify(metaObj),
      };
    }
  }

  // 7. Stream (Dynamic TorBox Cloud Resolution + Permanent Fallbacks!)
  const streamMatch = subpath.match(/^\/stream\/movie\/([^/]+)\.json$/);
  if (streamMatch) {
    const itemId = streamMatch[1];
    const streamPayload = await resolveStreamsForItem(
      data,
      itemId,
      token,
      env,
      clientIpv4,
      origin
    );
    return {
      status: 200,
      headers: {
        ...CORS_HEADERS,
        "Content-Type": "application/json; charset=utf-8",
        // Short private cache when TorBox token is active so links are always fresh
        "Cache-Control": token ? "private, max-age=120" : "public, max-age=600",
      },
      body: JSON.stringify(streamPayload, null, 2),
    };
  }

  return {
    status: 404,
    headers: { ...CORS_HEADERS, "Content-Type": "application/json; charset=utf-8" },
    body: JSON.stringify({ error: "Not found", path: subpath }),
  };
}

module.exports = {
  CORS_HEADERS,
  handleAddonRequest,
  parseRouteAndToken,
};
