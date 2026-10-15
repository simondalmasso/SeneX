(function () {
  "use strict";
  var button = document.getElementById("binance-rea-evaluate");
  var host = document.getElementById("binance-rea-content");
  var status = document.getElementById("binance-rea-status");
  if (!button || !host || !status) return;

  function el(tag, className, value) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (value !== undefined && value !== null) node.textContent = String(value);
    return node;
  }

  function metricText(metrics, key, suffix) {
    var value = metrics ? metrics[key] : undefined;
    if (value === undefined || value === null || value === "") return "UNKNOWN";
    var numeric = Number(value);
    if (Number.isFinite(numeric)) {
      return numeric.toLocaleString(undefined, { maximumFractionDigits: 5 }) + (suffix || "");
    }
    return String(value) + (suffix || "");
  }

  function metricCard(parent, label, value, note) {
    var card = el("div", "binance-public-metric");
    card.appendChild(el("span", "binance-public-label", label));
    card.appendChild(el("strong", "binance-public-value", value));
    if (note) card.appendChild(el("small", "binance-public-note", note));
    parent.appendChild(card);
  }

  function fail(message) {
    status.textContent = "DIAGNOSTIC FAILED · NO FALLBACK";
    status.className = "meta binance-public-error";
    host.replaceChildren(el("p", "binance-public-error-box", message || "Paper evaluation unavailable."));
  }

  function summaryMetrics(row) {
    return row && row.base ? row.base : {};
  }

  function render(report) {
    var safe = report && report.safety;
    if (!report || report.status !== "completed-paper-diagnostic" ||
        report.mode !== "PUBLIC_MARKET_DATA / PAPER_ONLY" ||
        report.evaluation_kind === undefined ||
        !report.source || !report.strategies || !report.benchmarks || !safe ||
        safe.api_key_used !== false || safe.signed_requests_used !== false ||
        safe.account_endpoints_called !== false || safe.order_endpoints_called !== 0 ||
        safe.live_orders_submitted !== 0 || safe.testnet_orders_submitted !== 0 ||
        safe.live_trading_enabled !== false ||
        safe.runtime_execution_engine_touched !== false ||
        safe.runtime_risk_kernel_touched !== false ||
        safe.runtime_trade_journal_touched !== false || safe.EDGE !== "UNPROVEN") {
      fail("Paper result rejected: safety, scope, or evidence fields are missing. No metric values are being displayed.");
      return;
    }

    status.textContent = "PAPER DIAGNOSTIC COMPLETE · EDGE UNPROVEN";
    status.className = "meta binance-public-ok";
    host.replaceChildren();

    host.appendChild(el("p", "binance-public-note-line",
      report.evaluation_kind + " · No trade signal is generated or routed to execution."));

    var meta = el("div", "binance-public-grid");
    metricCard(meta, "Sample", report.symbol + " · " + report.interval,
      report.source.closed_rows + " closed candles");
    metricCard(meta, "Sample window", report.source.first_closed_candle_utc,
      "through " + report.source.last_closed_candle_utc);
    metricCard(meta, "Data gaps", report.source.gap_count,
      report.source.gaps_filled === false ? "not interpolated" : "gap policy unknown");
    metricCard(meta, "Paper capital", report.starting_paper_cash_usdt + " USDT",
      "reset for this run · no real balance");
    metricCard(meta, "Execution cost assumption",
      report.cost_model.fee_bps + "/" + report.cost_model.spread_bps + "/" + report.cost_model.slippage_bps + " bps",
      "fee / spread / slippage");
    metricCard(meta, "Live / testnet orders", "0 / 0",
      "API key used: NO");
    host.appendChild(meta);

    var strategySection = el("div", "binance-paper-subsection");
    strategySection.appendChild(el("h4", "binance-paper-heading", "Clean-room strategy results"));
    var strategyGrid = el("div", "binance-public-grid");
    Object.keys(report.strategies).forEach(function (name) {
      var row = summaryMetrics(report.strategies[name]);
      var card = el("article", "binance-paper-strategy");
      card.appendChild(el("h5", "", name.toUpperCase()));
      var returnValue = metricText(row, "return_pct", "%");
      card.appendChild(el("strong", "binance-paper-return", returnValue));
      var details = el("dl", "binance-paper-kv");
      [
        ["Net P&L (USDT)", metricText(row, "net_pnl")],
        ["Ending equity (USDT)", metricText(row, "ending_equity")],
        ["Max drawdown", metricText(row, "max_drawdown_pct", "%")],
        ["Fills / pending", metricText(row, "fills") + " / " + metricText(row, "pending_orders")],
        ["Time in market", metricText(row, "time_in_market_pct", "%")],
        ["Fees / spread / slippage", metricText(row, "total_fees") + " / " +
          metricText(row, "total_spread_cost") + " / " + metricText(row, "total_slippage_cost")]
      ].forEach(function (pair) {
        details.appendChild(el("dt", "", pair[0]));
        details.appendChild(el("dd", "binance-public-mono", pair[1]));
      });
      card.appendChild(details);
      strategyGrid.appendChild(card);
    });
    strategySection.appendChild(strategyGrid);
    host.appendChild(strategySection);

    var benchSection = el("div", "binance-paper-subsection");
    benchSection.appendChild(el("h4", "binance-paper-heading", "Benchmarks"));
    var benchGrid = el("div", "binance-public-grid");
    Object.keys(report.benchmarks).forEach(function (name) {
      var row = summaryMetrics(report.benchmarks[name]);
      var card = el("article", "binance-paper-strategy");
      card.appendChild(el("h5", "", name.replace(/_/g, " ").toUpperCase()));
      card.appendChild(el("strong", "binance-paper-return", metricText(row, "return_pct", "%")));
      card.appendChild(el("p", "binance-public-note-line",
        "Net P&L " + metricText(row, "net_pnl") + " USDT · max DD " +
        metricText(row, "max_drawdown_pct", "%") + " · fills " + metricText(row, "fills")));
      benchGrid.appendChild(card);
    });
    benchSection.appendChild(benchGrid);
    host.appendChild(benchSection);

    var stress = el("details", "binance-public-details");
    stress.appendChild(el("summary", "", "Cost stress sensitivity · 1x / 2x / 3x"));
    var stressTableWrap = el("div", "table-wrap binance-public-table-wrap");
    var table = el("table", "table binance-public-table");
    var head = el("thead", "");
    var header = el("tr", "");
    ["Model", "Stress", "Return %", "Net P&L", "Max DD %", "Fills"].forEach(function (x) {
      header.appendChild(el("th", "", x));
    });
    head.appendChild(header); table.appendChild(head);
    var tbody = el("tbody", "");
    Object.keys(report.strategies).forEach(function (name) {
      var stressMap = report.strategies[name].cost_stress || {};
      Object.keys(stressMap).forEach(function (factor) {
        var item = stressMap[factor];
        var tr = el("tr", "");
        var values = [name, factor, metricText(item, "return_pct"),
          metricText(item, "net_pnl"), metricText(item, "max_drawdown_pct"),
          metricText(item, "fills")];
        values.forEach(function (v) { tr.appendChild(el("td", "binance-public-mono", v)); });
        tbody.appendChild(tr);
      });
    });
    table.appendChild(tbody); stressTableWrap.appendChild(table); stress.appendChild(stressTableWrap);
    host.appendChild(stress);

    host.appendChild(el("p", "binance-public-footer",
      "EDGE=UNPROVEN · PAPER_ONLY · No order/cancel endpoints, no account endpoints, no credentials. " +
      "A positive short-sample result would not prove a durable edge; this diagnostic is not a recommendation to trade."));
  }

  button.addEventListener("click", async function () {
    var symbol = document.getElementById("binance-public-symbol").value;
    var interval = document.getElementById("binance-public-interval").value;
    var limit = Number(document.getElementById("binance-public-limit").value);
    if (!Number.isInteger(limit) || limit < 2 || limit > 100) {
      fail("Closed bars must be an integer between 2 and 100.");
      return;
    }
    button.disabled = true;
    status.textContent = "RUNNING LOCAL PAPER EVALUATION…";
    status.className = "meta";
    host.replaceChildren(el("p", "placeholder",
      "Fetching closed public candles and replaying clean-room strategies in a reset paper ledger. No order will be sent."));
    try {
      var query = new URLSearchParams({ symbol: symbol, interval: interval, limit: String(limit) });
      var response = await fetch("/api/binance/rea_paper_diagnostic?" + query.toString(), {
        method: "GET", credentials: "same-origin", cache: "no-store",
        headers: { "Accept": "application/json" }
      });
      var body = await response.json();
      if (!response.ok) {
        var detail = body && body.detail;
        fail(detail && detail.message ? detail.message :
          (detail && detail.code ? detail.code : "HTTP " + response.status + " from local paper evaluator."));
        return;
      }
      render(body);
    } catch (error) {
      fail("Unable to complete local paper diagnostic: " +
        (error && error.name ? error.name : "network error"));
    } finally {
      button.disabled = false;
    }
  });
}());
