(function () {
  "use strict";

  var button = document.getElementById("binance-public-fetch");
  var host = document.getElementById("binance-public-content");
  var status = document.getElementById("binance-public-status");
  if (!button || !host || !status) return;

  function el(tag, className, text) {
    var item = document.createElement(tag);
    if (className) item.className = className;
    if (text !== undefined && text !== null) item.textContent = String(text);
    return item;
  }

  function formatNumber(value, digits) {
    var parsed = Number(value);
    if (!Number.isFinite(parsed)) return "UNKNOWN";
    return parsed.toLocaleString(undefined, { maximumFractionDigits: digits === undefined ? 8 : digits });
  }

  function addMetric(parent, label, value, note) {
    var card = el("div", "binance-public-metric");
    card.appendChild(el("span", "binance-public-label", label));
    card.appendChild(el("strong", "binance-public-value", value));
    if (note) card.appendChild(el("small", "binance-public-note", note));
    parent.appendChild(card);
  }

  function displayError(message) {
    status.textContent = "REQUEST FAILED · NO STALE FALLBACK";
    status.className = "meta binance-public-error";
    host.replaceChildren(el("p", "binance-public-error-box", message || "Public market-data request failed."));
  }

  function render(payload) {
    if (!payload || payload.status !== "ok" ||
        payload.mode !== "READ_ONLY_PUBLIC_MARKET_DATA / PAPER_ONLY" ||
        !payload.source || !Array.isArray(payload.candles) ||
        !payload.latest_closed_candle || !payload.safety ||
        payload.safety.api_key_used !== false ||
        payload.safety.signed_requests_used !== false ||
        payload.safety.account_endpoints_called !== false ||
        payload.safety.order_endpoints_called !== 0 ||
        payload.safety.live_orders_submitted !== 0 ||
        payload.safety.testnet_orders_submitted !== 0 ||
        payload.safety.live_trading_enabled !== false) {
      displayError("Respuesta rechazada: faltan garantías verificables de solo lectura. No se muestran datos.");
      return;
    }

    status.textContent = "PUBLIC DATA VERIFIED · PAPER ONLY · EDGE=UNPROVEN";
    status.className = "meta binance-public-ok";
    host.replaceChildren();

    var summary = el("div", "binance-public-grid");
    var candle = payload.latest_closed_candle;
    addMetric(summary, "Venue / symbol", payload.venue + " · " + payload.symbol, payload.interval + " interval");
    addMetric(summary, "Latest closed", candle.close, candle.close_time_utc);
    addMetric(summary, "Closed bars", payload.source.closed_rows_returned,
      "requested " + payload.requested_limit + " · dropped open " + payload.source.incomplete_rows_dropped);
    addMetric(summary, "Detected gaps", payload.source.gap_count,
      payload.source.gaps_filled === false ? "none interpolated" : "gap policy unknown");
    addMetric(summary, "Cache", payload.cache_hit ? "HIT" : "MISS",
      "snapshot fetched " + payload.fetched_at_utc);
    addMetric(summary, "Live / testnet orders", "0 / 0", "API key used: NO");
    host.appendChild(summary);

    var provenance = el("details", "binance-public-details");
    provenance.appendChild(el("summary", "", "Source provenance and integrity"));
    var hashes = el("dl", "binance-public-hashes");
    [
      ["Server time UTC", payload.server_time_utc],
      ["Last closed candle UTC", candle.open_time_utc],
      ["Raw response SHA-256", payload.source.raw_response_sha256],
      ["Canonical candles SHA-256", payload.source.canonical_closed_candles_sha256],
      ["Public endpoint", payload.source.endpoint],
      ["Safety", "API_KEY=NO · SIGNED=NO · ACCOUNT=NO · ORDER_ENDPOINTS=0"]
    ].forEach(function (pair) {
      hashes.appendChild(el("dt", "", pair[0]));
      hashes.appendChild(el("dd", "binance-public-mono", pair[1]));
    });
    provenance.appendChild(hashes);
    host.appendChild(provenance);

    if (payload.source.gap_count > 0) {
      var gaps = el("div", "binance-public-warning");
      gaps.appendChild(el("strong", "", "DATA GAPS DETECTED"));
      gaps.appendChild(el("p", "", "No candles were invented or interpolated. The list below records only what the public feed returned."));
      var gapList = el("ul", "");
      (payload.source.gaps || []).forEach(function (gap) {
        gapList.appendChild(el("li", "", gap.previous_open_time_utc + " → " + gap.next_open_time_utc +
          "; missing intervals: " + (gap.missing_intervals === null ? "unknown" : gap.missing_intervals)));
      });
      gaps.appendChild(gapList);
      host.appendChild(gaps);
    }

    var tableWrap = el("div", "table-wrap binance-public-table-wrap");
    var table = el("table", "table binance-public-table");
    var head = el("thead", "");
    var headerRow = el("tr", "");
    ["Open time UTC", "Open", "High", "Low", "Close", "Volume"].forEach(function (name) {
      headerRow.appendChild(el("th", "", name));
    });
    head.appendChild(headerRow);
    table.appendChild(head);
    var body = el("tbody", "");
    payload.candles.slice(-12).slice().reverse().forEach(function (row) {
      var tr = el("tr", "");
      [row.open_time_utc, row.open, row.high, row.low, row.close, row.volume].forEach(function (value) {
        tr.appendChild(el("td", "binance-public-mono", value));
      });
      body.appendChild(tr);
    });
    table.appendChild(body);
    tableWrap.appendChild(table);
    host.appendChild(el("p", "binance-public-note-line",
      "Showing up to the latest 12 of " + payload.candles.length +
      " returned closed candles. This is public market data, not an account balance, fill, signal, or profitability proof."));
    host.appendChild(tableWrap);
    host.appendChild(el("p", "binance-public-footer",
      "PAPER ONLY · EDGE=UNPROVEN · Runtime orders are not connected. Binance endpoint used: " +
      payload.source.endpoint + "."));
  }

  button.addEventListener("click", async function () {
    var symbolInput = document.getElementById("binance-public-symbol");
    var intervalInput = document.getElementById("binance-public-interval");
    var limitInput = document.getElementById("binance-public-limit");
    var symbol = symbolInput ? symbolInput.value : "BTCUSDT";
    var interval = intervalInput ? intervalInput.value : "4h";
    var limit = Number(limitInput ? limitInput.value : 24);
    if (!Number.isInteger(limit) || limit < 2 || limit > 100) {
      displayError("Closed bars must be an integer between 2 and 100.");
      return;
    }

    button.disabled = true;
    status.textContent = "REQUESTING PUBLIC CLOSED CANDLES…";
    status.className = "meta";
    host.replaceChildren(el("p", "placeholder", "Contacting public Binance Spot market-data endpoint. No credentials or order endpoints are used."));
    try {
      var query = new URLSearchParams({
        symbol: symbol,
        interval: interval,
        limit: String(limit)
      });
      var response = await fetch("/api/binance/public_klines?" + query.toString(), {
        method: "GET",
        credentials: "same-origin",
        cache: "no-store",
        headers: { "Accept": "application/json" }
      });
      var payload = await response.json();
      if (!response.ok) {
        var detail = payload && payload.detail;
        displayError(detail && detail.message ? detail.message : "HTTP " + response.status + " from the local public-data route.");
        return;
      }
      render(payload);
    } catch (error) {
      displayError("Unable to read public-market-data route: " + (error && error.name ? error.name : "network error"));
    } finally {
      button.disabled = false;
    }
  });
}());
