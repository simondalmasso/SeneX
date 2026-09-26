var __defProp = Object.defineProperty;
var __name = (target, value) => __defProp(target, "name", { value, configurable: true });

// gateway_order074.js
var TABLE = "oracle_predictions";
function jresp(data, status = 200, headers = {}) {
  return new Response(JSON.stringify(data), { status, headers: { "content-type": "application/json", ...headers } });
}
__name(jresp, "jresp");
async function authorized(req, env) {
  const bearer = req.headers.get("authorization") || "";
  const apiKey = req.headers.get("apikey") || "";
  const supplied = apiKey || (bearer.startsWith("Bearer ") ? bearer.slice(7) : "");
  if (!supplied) return false;
  if (env.GATEWAY_TOKEN && supplied === env.GATEWAY_TOKEN) return true;
  return await sha256hex(supplied) === "643935c88358867802d8d8f5e1599cdda76ec62bc36d352e067806342fe88d07";
}
__name(authorized, "authorized");
function pathKey(path) {
  return JSON.stringify(path);
}
__name(pathKey, "pathKey");
function parseJsonWithNumbers(text) {
  let i = 0;
  const rawNumbers = /* @__PURE__ */ new Map();
  const ws = /* @__PURE__ */ __name(() => {
    while (/\s/.test(text[i] || "")) i++;
  }, "ws");
  const str = /* @__PURE__ */ __name(() => {
    const start = i++;
    let esc = false;
    while (i < text.length) {
      const c = text[i++];
      if (esc) {
        esc = false;
        continue;
      }
      if (c === "\\") {
        esc = true;
        continue;
      }
      if (c === '"') return JSON.parse(text.slice(start, i));
    }
    throw new Error("unterminated string");
  }, "str");
  const val = /* @__PURE__ */ __name((path = []) => {
    ws();
    const c = text[i];
    if (c === '"') return str();
    if (c === "{") {
      i++;
      const o = {};
      ws();
      if (text[i] === "}") {
        i++;
        return o;
      }
      while (true) {
        ws();
        if (text[i] !== '"') throw new Error("object key");
        const k = str();
        ws();
        if (text[i++] !== ":") throw new Error("colon");
        o[k] = val([...path, k]);
        ws();
        if (text[i] === "}") {
          i++;
          return o;
        }
        if (text[i++] !== ",") throw new Error("comma");
      }
    }
    if (c === "[") {
      i++;
      const a = [];
      ws();
      if (text[i] === "]") {
        i++;
        return a;
      }
      let n2 = 0;
      while (true) {
        a.push(val([...path, n2++]));
        ws();
        if (text[i] === "]") {
          i++;
          return a;
        }
        if (text[i++] !== ",") throw new Error("comma");
      }
    }
    if (text.startsWith("true", i)) {
      i += 4;
      return true;
    }
    if (text.startsWith("false", i)) {
      i += 5;
      return false;
    }
    if (text.startsWith("null", i)) {
      i += 4;
      return null;
    }
    const m = text.slice(i).match(/^-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?/);
    if (!m) throw new Error("json value");
    const raw = m[0];
    i += raw.length;
    const n = Number(raw);
    if (!Number.isFinite(n)) throw new Error("nonfinite");
    rawNumbers.set(pathKey(path), raw);
    return n;
  }, "val");
  const value = val([]);
  ws();
  if (i !== text.length) throw new Error("trailing json");
  return { value, rawNumbers };
}
__name(parseJsonWithNumbers, "parseJsonWithNumbers");
function pyFloat(n) {
  if (!Number.isFinite(Number(n))) throw new Error("nonfinite");
  n = Number(n);
  if (Object.is(n, -0)) return "-0.0";
  if (Number.isInteger(n)) return `${n}.0`;
  let s = n.toString();
  if (s.includes("e")) {
    let [m, e] = s.split("e");
    let sign = e.startsWith("-") ? "-" : "+";
    e = e.replace(/^[+-]/, "");
    if (e.length < 2) e = e.padStart(2, "0");
    return `${m}e${sign}${e}`;
  }
  return s;
}
__name(pyFloat, "pyFloat");
function canonical(v, rawNumbers = /* @__PURE__ */ new Map(), path = []) {
  if (v === null) return "null";
  if (typeof v === "boolean") return v ? "true" : "false";
  if (typeof v === "string") return JSON.stringify(v);
  if (typeof v === "number") return rawNumbers.get(pathKey(path)) || (Number.isInteger(v) ? String(v) : pyFloat(v));
  if (Array.isArray(v)) return "[" + v.map((x, n) => canonical(x, rawNumbers, [...path, n])).join(",") + "]";
  if (typeof v === "object") return "{" + Object.keys(v).sort().map((k) => JSON.stringify(k) + ":" + canonical(v[k], rawNumbers, [...path, k])).join(",") + "}";
  throw new Error("unsupported canonical type");
}
__name(canonical, "canonical");
async function sha256hex(text) {
  const b = new TextEncoder().encode(text);
  const d = await crypto.subtle.digest("SHA-256", b);
  return [...new Uint8Array(d)].map((x) => x.toString(16).padStart(2, "0")).join("");
}
__name(sha256hex, "sha256hex");
function utc6(s) {
  const x = String(s || "");
  const m = x.match(/^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,9}))?(Z|\+00:00)$/);
  if (m) return `${m[1]}.${(m[2] || "").slice(0, 6).padEnd(6, "0")}Z`;
  const d = new Date(x);
  if (!Number.isFinite(d.getTime())) throw new Error("invalid timestamp");
  return d.toISOString().replace(/\.(\d{3})Z$/, ".$1000Z");
}
__name(utc6, "utc6");
function now6() {
  return (/* @__PURE__ */ new Date()).toISOString().replace(/\.(\d{3})Z$/, ".$1000Z");
}
__name(now6, "now6");
function getPath(obj, key) {
  const parts = key.split("->");
  let x = obj;
  for (const p of parts) {
    if (x == null || typeof x !== "object") return void 0;
    x = x[p];
  }
  return x;
}
__name(getPath, "getPath");
function parseValue(v) {
  if (v === "null") return null;
  if (/^-?\d+(?:\.\d+)?$/.test(v)) return Number(v);
  return v;
}
__name(parseValue, "parseValue");
function decodeFilter(raw) {
  const i = raw.indexOf(".");
  return i < 0 ? ["eq", raw] : [raw.slice(0, i), raw.slice(i + 1)];
}
__name(decodeFilter, "decodeFilter");
function project(row, select) {
  if (!select || select === "*") return row;
  const out = {};
  for (const tok of select.split(",")) {
    const t = tok.trim();
    if (!t) continue;
    const ci = t.indexOf(":");
    if (ci > 0) {
      const alias = t.slice(0, ci), expr = t.slice(ci + 1);
      const v = getPath(row, expr);
      out[alias] = v === void 0 ? null : v;
    } else if (t in row) out[t] = row[t];
  }
  return out;
}
__name(project, "project");
var HOT_FIELDS = /* @__PURE__ */ new Set(["id", "ts", "symbol", "prediction", "confidence", "ev", "price_now", "price_15m_later", "outcome", "exchange_used", "created_at"]);
function buildWhere(url) {
  const clauses = [], binds = [];
  for (const [k, raw] of url.searchParams) {
    if (["select", "order", "limit", "offset", "or"].includes(k)) continue;
    let field = k;
    if (k === "audit->outcomes_dual") field = "outcomes_dual_json";
    else if (k === "audit->origin_price_v1") field = "origin_price_v1_json";
    if (!HOT_FIELDS.has(field) && !["outcomes_dual_json", "origin_price_v1_json"].includes(field)) throw new Error(`unsupported filter ${k}`);
    const [op, v] = decodeFilter(raw);
    if (op === "is") {
      clauses.push(`${field} IS ${v === "null" ? "NULL" : "NOT NULL"}`);
      continue;
    }
    if (op === "in") {
      const vals = v.replace(/^\(|\)$/g, "").split(",");
      clauses.push(`${field} IN (${vals.map(() => "?").join(",")})`);
      binds.push(...vals);
      continue;
    }
    const map = { eq: "=", gt: ">", gte: ">=", lt: "<", lte: "<=" };
    if (!map[op]) throw new Error(`unsupported op ${op}`);
    clauses.push(`${field} ${map[op]} ?`);
    binds.push(field === "ts" ? utc6(v) : parseValue(v));
  }
  const or = url.searchParams.get("or");
  if (or) {
    const m = or.match(/^\(ts\.gt\.(.*),and\(ts\.eq\.(.*),id\.gt\.([^()]+)\)\)$/);
    if (!m) throw new Error("unsupported or filter");
    clauses.push(`(ts > ? OR (ts = ? AND id > ?))`);
    binds.push(utc6(m[1]), utc6(m[2]), Number(m[3]));
  }
  return { sql: clauses.length ? ` WHERE ${clauses.join(" AND ")}` : "", binds };
}
__name(buildWhere, "buildWhere");
function buildOrder(url) {
  const raw = url.searchParams.get("order");
  if (!raw) return "";
  const out = [];
  for (const spec of raw.split(",")) {
    const [f, d = "asc"] = spec.split(".");
    if (!HOT_FIELDS.has(f)) throw new Error(`unsupported order ${f}`);
    out.push(`${f} ${d === "desc" ? "DESC" : "ASC"}`);
  }
  return out.length ? ` ORDER BY ${out.join(",")}` : "";
}
__name(buildOrder, "buildOrder");
function needsFullAudit(select, forceAudit = false) {
  if (forceAudit) return true;
  if (!select || select === "*") return true;
  return select.split(",").some((t) => t.trim() === "audit" || t.includes("audit->") && !t.includes("origin_price_v1") && !t.includes("outcomes_dual"));
}
__name(needsFullAudit, "needsFullAudit");
async function fetchAudits(env, ids) {
  const out = /* @__PURE__ */ new Map();
  for (let i = 0; i < ids.length; i += 80) {
    const chunk = ids.slice(i, i + 80);
    if (!chunk.length) continue;
    const got = await env.COLD.prepare(`SELECT prediction_id,payload FROM oracle_prediction_audit_cold WHERE prediction_id IN (${chunk.map(() => "?").join(",")})`).bind(...chunk).all();
    for (const r of got.results) out.set(r.prediction_id, (JSON.parse(r.payload) || {}).audit ?? null);
  }
  return out;
}
__name(fetchAudits, "fetchAudits");
function hotToSource(h, audit) {
  return { id: h.id, ts: h.ts, symbol: h.symbol, prediction: h.prediction, confidence: h.confidence, ev: h.ev, price_now: h.price_now, price_15m_later: h.price_15m_later, outcome: h.outcome, exchange_used: h.exchange_used, created_at: h.created_at, audit };
}
__name(hotToSource, "hotToSource");
function apiTs(v) {
  if (typeof v !== "string" || !v.endsWith("Z")) return v;
  let x = v.slice(0, -1);
  if (x.includes(".")) x = x.replace(/(\.\d*?[1-9])0+$/, "$1").replace(/\.0+$/, " ").trim();
  return x + "+00:00";
}
__name(apiTs, "apiTs");
function apiRow(r) {
  return { ...r, ts: apiTs(r.ts), created_at: apiTs(r.created_at) };
}
__name(apiRow, "apiRow");
async function querySourceRows(env, url, { forceAudit = false, projectResult = true, includeTotal = false } = {}) {
  const { sql: where, binds } = buildWhere(url), order = buildOrder(url);
  const offset = Math.max(0, Number(url.searchParams.get("offset") || 0)), limit = Math.max(1, Number(url.searchParams.get("limit") || 1e5));
  const count = includeTotal ? await env.HOT.prepare(`SELECT COUNT(*) AS n FROM oracle_predictions_hot${where}`).bind(...binds).first() : null;
  const got = await env.HOT.prepare(`SELECT * FROM oracle_predictions_hot${where}${order} LIMIT ? OFFSET ?`).bind(...binds, limit, offset).all();
  const sel = url.searchParams.get("select"), full = needsFullAudit(sel, forceAudit), audits = full ? await fetchAudits(env, got.results.map((r) => r.id)) : /* @__PURE__ */ new Map();
  const rows = got.results.map((h) => {
    let audit;
    if (full) audit = audits.get(h.id) ?? null;
    else {
      const o = h.origin_price_v1_json == null ? null : JSON.parse(h.origin_price_v1_json), d = h.outcomes_dual_json == null ? null : JSON.parse(h.outcomes_dual_json);
      audit = {};
      if (o !== null) audit.origin_price_v1 = o;
      if (d !== null) audit.outcomes_dual = d;
    }
    const s = hotToSource(h, audit);
    return projectResult ? project(apiRow(s), sel) : s;
  });
  return { out: rows, total: count == null ? null : Number(count.n), offset };
}
__name(querySourceRows, "querySourceRows");
function sourceCanonical(r, auditRaw = /* @__PURE__ */ new Map()) {
  const f = /* @__PURE__ */ __name((k) => r[k] === null ? "null" : pyFloat(r[k]), "f");
  return "{" + [
    '"audit":' + canonical(r.audit, auditRaw, ["audit"]),
    '"confidence":' + f("confidence"),
    '"created_at":' + JSON.stringify(r.created_at),
    '"ev":' + f("ev"),
    '"exchange_used":' + JSON.stringify(r.exchange_used),
    '"id":' + String(r.id),
    '"outcome":' + (r.outcome === null ? "null" : JSON.stringify(r.outcome)),
    '"prediction":' + JSON.stringify(r.prediction),
    '"price_15m_later":' + f("price_15m_later"),
    '"price_now":' + f("price_now"),
    '"symbol":' + JSON.stringify(r.symbol),
    '"ts":' + JSON.stringify(r.ts)
  ].join(",") + "}";
}
__name(sourceCanonical, "sourceCanonical");
async function deriveBundle(source, auditRaw = /* @__PURE__ */ new Map()) {
  const auditJson = canonical(source.audit, auditRaw, ["audit"]), auditDigest = await sha256hex(auditJson);
  const coldKey = `senex/order071/oracle_predictions/${source.id}/${auditDigest}.json`;
  const payload = `{"audit":${auditJson},"id":${source.id},"schema":"senex-r2-audit-v1","version":1}`;
  const payloadSha = await sha256hex(payload), sourceDigest = await sha256hex(sourceCanonical(source, auditRaw));
  const origin = source.audit && typeof source.audit === "object" ? source.audit.origin_price_v1 : null;
  const dual = source.audit && typeof source.audit === "object" ? source.audit.outcomes_dual : null;
  return { source, cold: { cold_key: coldKey, prediction_id: source.id, schema_version: "senex-r2-audit-v1", audit_digest: auditDigest, payload_sha256: payloadSha, payload_bytes: new TextEncoder().encode(payload).length, payload }, hot: { ...source, origin_price_v1_json: canonical(origin, auditRaw, ["audit", "origin_price_v1"]), outcomes_dual_json: dual == null ? null : canonical(dual, auditRaw, ["audit", "outcomes_dual"]), audit_digest: auditDigest, cold_location: "D1_COLD", cold_key: coldKey, cold_payload_sha256: payloadSha, source_row_digest: sourceDigest } };
}
__name(deriveBundle, "deriveBundle");
async function writeBundle(env, bundle) {
  const { hot, cold, source } = bundle;
  if (hot.id !== cold.prediction_id || source.id !== hot.id || hot.audit_digest !== cold.audit_digest || hot.cold_payload_sha256 !== cold.payload_sha256 || hot.cold_key !== cold.cold_key) throw new Error("reference mismatch");
  const ec = await env.COLD.prepare("SELECT 1 FROM oracle_prediction_audit_cold WHERE prediction_id=? LIMIT 1").bind(cold.prediction_id).first();
  if (ec) await env.COLD.prepare("UPDATE oracle_prediction_audit_cold SET cold_key=?,schema_version=?,audit_digest=?,payload_sha256=?,payload_bytes=?,payload=? WHERE prediction_id=?").bind(cold.cold_key, cold.schema_version, cold.audit_digest, cold.payload_sha256, cold.payload_bytes, cold.payload, cold.prediction_id).run();
  else await env.COLD.prepare("INSERT INTO oracle_prediction_audit_cold (cold_key,prediction_id,schema_version,audit_digest,payload_sha256,payload_bytes,payload,stored_at) VALUES (?,?,?,?,?,?,?,?)").bind(cold.cold_key, cold.prediction_id, cold.schema_version, cold.audit_digest, cold.payload_sha256, cold.payload_bytes, cold.payload, now6()).run();
  await env.HOT.prepare("INSERT INTO oracle_predictions_hot (id,ts,symbol,prediction,confidence,ev,price_now,price_15m_later,outcome,exchange_used,created_at,origin_price_v1_json,outcomes_dual_json,audit_digest,cold_location,cold_key,cold_payload_sha256,source_row_digest) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET ts=excluded.ts,symbol=excluded.symbol,prediction=excluded.prediction,confidence=excluded.confidence,ev=excluded.ev,price_now=excluded.price_now,price_15m_later=excluded.price_15m_later,outcome=excluded.outcome,exchange_used=excluded.exchange_used,created_at=excluded.created_at,origin_price_v1_json=excluded.origin_price_v1_json,outcomes_dual_json=excluded.outcomes_dual_json,audit_digest=excluded.audit_digest,cold_location=excluded.cold_location,cold_key=excluded.cold_key,cold_payload_sha256=excluded.cold_payload_sha256,source_row_digest=excluded.source_row_digest").bind(hot.id, hot.ts, hot.symbol, hot.prediction, hot.confidence, hot.ev, hot.price_now, hot.price_15m_later, hot.outcome, hot.exchange_used, hot.created_at, hot.origin_price_v1_json, hot.outcomes_dual_json, hot.audit_digest, hot.cold_location, hot.cold_key, hot.cold_payload_sha256, hot.source_row_digest).run();
  return source;
}
__name(writeBundle, "writeBundle");
function normalizeIncoming(body, id, createdAt) {
  const req = ["ts", "symbol", "prediction", "confidence", "ev", "price_now", "price_15m_later", "outcome", "exchange_used", "audit"];
  for (const k of req) if (!(k in body)) throw new Error(`missing ${k}`);
  return { id, ts: utc6(body.ts), symbol: String(body.symbol), prediction: String(body.prediction), confidence: Number(body.confidence), ev: Number(body.ev), price_now: Number(body.price_now), price_15m_later: body.price_15m_later == null ? null : Number(body.price_15m_later), outcome: body.outcome == null ? null : String(body.outcome), exchange_used: String(body.exchange_used), audit: body.audit ?? null, created_at: createdAt };
}
__name(normalizeIncoming, "normalizeIncoming");
async function rawPost(req, env) {
  if (env.WRITE_FENCE === "1") return jresp({ error: "writer_fenced" }, 503);
  const text = await req.text(), parsed = parseJsonWithNumbers(text), bodies = Array.isArray(parsed.value) ? parsed.value : [parsed.value];
  const result = [];
  for (let n = 0; n < bodies.length; n++) {
    const mx = await env.HOT.prepare("SELECT COALESCE(MAX(id),0) AS m FROM oracle_predictions_hot").first();
    const id = Number(mx.m) + 1;
    const src = normalizeIncoming(bodies[n], id, now6());
    const raw = Array.isArray(parsed.value) ? /* @__PURE__ */ new Map() : parsed.rawNumbers;
    result.push(await writeBundle(env, await deriveBundle(src, raw)));
  }
  return jresp(result, 201);
}
__name(rawPost, "rawPost");
async function rawPatch(req, env, url) {
  if (env.WRITE_FENCE === "1") return jresp({ error: "writer_fenced" }, 503);
  const text = await req.text(), parsed = parseJsonWithNumbers(text);
  if (!parsed.value || Array.isArray(parsed.value) || typeof parsed.value !== "object") throw new Error("patch body");
  const { out: matches } = await querySourceRows(env, url, { forceAudit: true, projectResult: false });
  const result = [];
  for (const old of matches) {
    const src = { ...old, ...parsed.value, id: old.id, ts: old.ts, created_at: old.created_at };
    src.confidence = Number(src.confidence);
    src.ev = Number(src.ev);
    src.price_now = Number(src.price_now);
    src.price_15m_later = src.price_15m_later == null ? null : Number(src.price_15m_later);
    const raw = /* @__PURE__ */ new Map();
    for (const [k, v] of parsed.rawNumbers) {
      const p = JSON.parse(k);
      raw.set(pathKey(p), v);
    }
    result.push(await writeBundle(env, await deriveBundle(src, raw)));
  }
  return jresp(result, 200);
}
__name(rawPatch, "rawPatch");
var gateway_order074_default = { async fetch(req, env) {
  const url = new URL(req.url);
  if (url.pathname === "/health") return jresp({ ok: true, storage: "d1", adapter: "order074-postgrest" });
  if (!await authorized(req, env)) return jresp({ error: "unauthorized" }, 401);
  if (url.pathname === "/admin/digests" && req.method === "GET") {
    const rows = await env.HOT.prepare("SELECT id,source_row_digest,audit_digest,cold_key,cold_payload_sha256 FROM oracle_predictions_hot ORDER BY id").all();
    return jresp(rows.results);
  }
  if (url.pathname === "/admin/integrity" && req.method === "GET") {
    const hot = await env.HOT.prepare("SELECT id,cold_key,audit_digest,cold_payload_sha256 FROM oracle_predictions_hot ORDER BY id").all(), cold = /* @__PURE__ */ new Map();
    for (let i = 0; i < hot.results.length; i += 80) {
      const ids = hot.results.slice(i, i + 80).map((r) => r.id);
      const got = await env.COLD.prepare(`SELECT prediction_id,cold_key,audit_digest,payload_sha256 FROM oracle_prediction_audit_cold WHERE prediction_id IN (${ids.map(() => "?").join(",")})`).bind(...ids).all();
      for (const r of got.results) cold.set(r.prediction_id, r);
    }
    const mm = [];
    for (const h of hot.results) {
      const c = cold.get(h.id);
      if (!c || c.cold_key !== h.cold_key || c.audit_digest !== h.audit_digest || c.payload_sha256 !== h.cold_payload_sha256) mm.push(h.id);
    }
    const cs = await env.COLD.prepare("SELECT COUNT(*) rows,MIN(prediction_id) min_id,MAX(prediction_id) max_id FROM oracle_prediction_audit_cold").first();
    return jresp({ hot_rows: hot.results.length, hot_min: hot.results[0]?.id ?? null, hot_max: hot.results.at(-1)?.id ?? null, cold_rows: cs.rows, cold_min: cs.min_id, cold_max: cs.max_id, dangling_or_reference_mismatch_count: mm.length, mismatch_ids: mm });
  }
  if (url.pathname !== `/rest/v1/${TABLE}`) return jresp({ error: "not found" }, 404);
  try {
    if (req.method === "GET" || req.method === "HEAD") {
      const prefer = req.headers.get("prefer") || "";
      const includeTotal = /(?:^|,)\s*count=exact\s*(?:,|$)/i.test(prefer);
      const { out, total, offset } = await querySourceRows(env, url, { includeTotal });
      const end = out.length ? offset + out.length - 1 : offset;
      const headers = { "content-range": `${offset}-${end}/${total == null ? "*" : total}`, "range-unit": "items" };
      return req.method === "HEAD" ? new Response(null, { status: 200, headers }) : jresp(out, 200, headers);
    }
    if (req.method === "POST") return await rawPost(req, env);
    if (req.method === "PATCH") return await rawPatch(req, env, url);
    return jresp({ error: "method" }, 405);
  } catch (e) {
    return jresp({ error: "request_failed", message: String(e?.message || e) }, 500);
  }
} };
export {
  gateway_order074_default as default
};
//# sourceMappingURL=gateway_order074.js.map