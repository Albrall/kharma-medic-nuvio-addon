const ADDON_DATA = require("../lib/addon_data");
const { handleAddonRequest } = require("../lib/torbox_resolver");

module.exports = async function handler(req, res) {
  const proto =
    (req.headers["x-forwarded-proto"] || "https").split(",")[0].trim();
  const host =
    (req.headers["x-forwarded-host"] || req.headers.host || "localhost").split(",")[0].trim();
  const origin = `${proto}://${host}`;
  const clientIp =
    req.headers["x-real-ip"] || req.headers["x-forwarded-for"] || "";
  const pathname = (req.url || "/").split("?")[0];

  const result = await handleAddonRequest(
    {
      method: req.method || "GET",
      pathname,
      origin,
      clientIp,
      env: process.env,
    },
    ADDON_DATA
  );

  res.statusCode = result.status;
  for (const [k, v] of Object.entries(result.headers || {})) {
    res.setHeader(k, v);
  }
  if (req.method === "HEAD") {
    return res.end();
  }
  return res.end(result.body);
};
