const http = require("http");
const { Readable } = require("stream");
const ADDON_DATA = require("./lib/addon_data");
const { handleAddonRequest } = require("./lib/torbox_resolver");

const PORT = Number(process.env.PORT) || 7000;

const server = http.createServer(async (req, res) => {
  try {
    const proto = (req.headers["x-forwarded-proto"] || "http").split(",")[0].trim();
    const host = (
      req.headers["x-forwarded-host"] ||
      req.headers.host ||
      `localhost:${PORT}`
    )
      .split(",")[0]
      .trim();
    const origin = `${proto}://${host}`;
    const clientIp =
      req.headers["cf-connecting-ip"] ||
      req.headers["x-real-ip"] ||
      req.headers["x-forwarded-for"] ||
      req.socket?.remoteAddress ||
      "";
    const pathname = (req.url || "/").split("?")[0];
    const rangeHeader = req.headers["range"] || "";

    const result = await handleAddonRequest(
      {
        method: req.method || "GET",
        pathname,
        origin,
        clientIp,
        rangeHeader,
        env: process.env,
      },
      ADDON_DATA
    );

    res.writeHead(result.status, result.headers);
    if (req.method === "HEAD" || result.body === null || result.body === undefined) {
      return res.end();
    }
    if (result.isStream && typeof result.body.getReader === "function") {
      const nodeStream = Readable.fromWeb(result.body);
      req.on("close", () => {
        try {
          nodeStream.destroy();
        } catch (_) {}
      });
      nodeStream.on("error", () => {
        try {
          res.destroy();
        } catch (_) {}
      });
      return nodeStream.pipe(res);
    }
    res.end(result.body);
  } catch (err) {
    if (!res.headersSent) {
      res.writeHead(500, {
        "Access-Control-Allow-Origin": "*",
        "Content-Type": "application/json; charset=utf-8",
      });
      res.end(JSON.stringify({ error: String(err.message || err) }));
    } else {
      res.destroy();
    }
  }
});

server.listen(PORT, "0.0.0.0", () => {
  console.log(
    `Kharma Medic 1080p Nuvio Addon listening on http://0.0.0.0:${PORT}/manifest.json`
  );
});
