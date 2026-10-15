(function () {
  "use strict";

  var host = document.getElementById("rea-panel-content");
  if (!host) return;

  function node(tag, className, text) {
    var item = document.createElement(tag);
    if (className) item.className = className;
    if (text !== undefined && text !== null) item.textContent = String(text);
    return item;
  }

  function add(parent, child) {
    parent.appendChild(child);
    return child;
  }

  function formatNumber(value, digits) {
    if (typeof value !== "number" || !Number.isFinite(value)) return "UNKNOWN";
    return value.toLocaleString("en-US", {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits
    });
  }

  function formatPct(value) {
    if (typeof value !== "number" || !Number.isFinite(value)) return "UNKNOWN";
    var prefix = value > 0 ? "+" : "";
    return prefix + formatNumber(value, 4) + "%";
  }

  function formatMoney(value) {
    if (typeof value !== "number" || !Number.isFinite(value)) return "UNKNOWN";
    return (value > 0 ? "+" : value < 0 ? "−" : "") +
      Math.abs(value).toLocaleString("en-US", {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2
      }) + " USDT";
  }

  function formatPlainMoney(value) {
    if (typeof value !== "number" || !Number.isFinite(value)) return "UNKNOWN";
    return value.toLocaleString("en-US", {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2
    }) + " USDT";
  }

  function formatHash(value) {
    return typeof value === "string" && value.length === 64 ? value : "UNKNOWN";
  }

  function section(title, description) {
    var block = node("section", "rea-block");
    add(block, node("h3", "rea-block-title", title));
    if (description) add(block, node("p", "rea-block-description", description));
    add(host, block);
    return block;
  }

  function metricCards(parent, items) {
    var grid = add(parent, node("div", "rea-kpi-grid"));
    items.forEach(function (item) {
      var card = add(grid, node("div", "rea-kpi"));
      add(card, node("span", "rea-kpi-label", item.label));
      add(card, node("strong", "rea-kpi-value", item.value));
      if (item.detail) add(card, node("span", "rea-kpi-detail", item.detail));
    });
    return grid;
  }

  function keyValueGrid(parent, items) {
    var grid = add(parent, node("dl", "rea-kv-grid"));
    items.forEach(function (item) {
      var wrapper = add(grid, node("div", "rea-kv"));
      add(wrapper, node("dt", "", item.label));
      var dd = add(wrapper, node("dd", "", item.value));
      if (item.mono) dd.classList.add("rea-mono");
      if (item.breakAll) dd.classList.add("rea-break");
    });
    return grid;
  }

  function table(parent, columns, rows, options) {
    var wrap = add(parent, node("div", "rea-table-wrap"));
    var tbl = add(wrap, node("table", "rea-table"));
    var thead = add(tbl, node("thead"));
    var hrow = add(thead, node("tr"));
    columns.forEach(function (column) {
      add(hrow, node("th", "", column.label));
    });
    var tbody = add(tbl, node("tbody"));
    if (!rows.length) {
      var emptyRow = add(tbody, node("tr"));
      var empty = add(emptyRow, node("td", "rea-empty", "No verified rows available."));
      empty.colSpan = columns.length;
      return tbl;
    }
    rows.forEach(function (row) {
      var tr = add(tbody, node("tr"));
      if (options && options.rowClass) {
        var rowClass = options.rowClass(row);
        if (rowClass) tr.classList.add(rowClass);
      }
      columns.forEach(function (column) {
        var td = add(tr, node("td"));
        if (column.className) td.classList.add(column.className);
        var value = column.value(row);
        td.textContent = value === undefined || value === null || value === "" ? "UNKNOWN" : String(value);
      });
    });
    return tbl;
  }

  function disclosure(parent, title, open) {
    var details = add(parent, node("details", "rea-disclosure"));
    details.open = Boolean(open);
    add(details, node("summary", "", title));
    return details;
  }

  function checkRows(data) {
    var checks = data.integrity || {};
    return [
      { check: "Canonical historical report", result: checks.report_matches_final_status ? "PASS" : "FAIL", detail: "SHA-256 matches FINAL_STATUS" },
      { check: "BTC CSV integrity", result: checks.dataset_hash_matches_report ? "PASS" : "FAIL", detail: "Actual CSV hash compared with report input hash" },
      { check: "Source file inventory", result: checks.inventory_ok ? "PASS" : "FAIL", detail: String(checks.inventory_rows) + " inventory rows; no path/hash/size mismatches reported" },
      { check: "Gunbot ZIP manifest", result: checks.zip_manifest_ok ? "PASS" : "FAIL", detail: String(checks.zip_manifest_rows) + " manifest rows; " + String(checks.zip_manifest_mismatches) + " mismatches" },
      { check: "ETH CSV input integrity", result: checks.eth_dataset_hash_matches_status ? "PASS" : "FAIL", detail: "Hash matches the local research status; metrics are not shown here" },
      { check: "Snapshot aggregate", result: checks.overall_ok ? "PASS" : "FAIL", detail: "A failed check blocks this render" }
    ];
  }

  var data = window.SENEX_REA_RESEARCH_SNAPSHOT;
  if (!data || data.schema_version !== 1 || !data.integrity ||
      data.integrity.overall_ok !== true || data.edge !== "UNPROVEN" ||
      data.live !== false || data.live_orders_submitted !== 0) {
    host.replaceChildren(node("div", "rea-unavailable", "REA SNAPSHOT UNAVAILABLE / UNKNOWN. The static snapshot is missing or failed schema, integrity, or safety validation; no metric values are being substituted."));
    return;
  }

  var market = data.market || {};
  var integrity = data.integrity || {};
  var tests = data.tests || {};
  var labels = {
    grid: "Grid clean-room",
    dca: "DCA clean-room",
    combo: "Combo clean-room",
    buy_and_hold_quote_100: "Buy & hold · 100 USDT",
    buy_and_hold_fixed_qty: "Buy & hold · fixed 0.02 BTC"
  };

  metricCards(host, [
    { label: "Evidence scope", value: "HISTORICAL PAPER", detail: "Local, static, read-only report" },
    { label: "EDGE", value: data.edge, detail: "No profitability claim" },
    { label: "REA live orders", value: String(data.live_orders_submitted), detail: "Historical replay only" },
    { label: "Integrity checks", value: integrity.overall_ok ? "PASS" : "UNKNOWN", detail: "Report, datasets, inventory and manifest" }
  ]);

  var provenance = section("01 · Provenance and market scope",
    "Figures below are sourced from the canonical local report and audit CSVs. They are not live exchange data.");
  keyValueGrid(provenance, [
    { label: "Market", value: String(market.venue || "UNKNOWN") },
    { label: "Symbol / timeframe", value: String(market.symbol || "UNKNOWN") + " · " + String(market.timeframe || "UNKNOWN") },
    { label: "Source rows", value: formatNumber(market.source_rows, 0) },
    { label: "Evaluated OOS rows", value: formatNumber(market.evaluated_rows, 0) },
    { label: "Source coverage", value: String(market.source_timestamp_start || "UNKNOWN") + " → " + String(market.source_timestamp_end || "UNKNOWN") },
    { label: "OOS window", value: String(market.oos_timestamp_start || "UNKNOWN") + " → " + String(market.oos_timestamp_end || "UNKNOWN") },
    { label: "Initial simulated cash", value: formatPlainMoney(market.starting_cash_usdt) },
    { label: "Fees / spread / slippage", value: formatNumber(market.fees_bps, 2) + " / " + formatNumber(market.spread_bps, 2) + " / " + formatNumber(market.slippage_bps, 2) + " bps" },
    { label: "Fill policy", value: market.fill_policy_id || "UNKNOWN", mono: true },
    { label: "Inventory cap / max fill", value: formatNumber(market.max_inventory_btc, 4) + " / " + formatNumber(market.max_fill_qty_btc, 4) + " BTC" },
    { label: "Report SHA-256", value: formatHash(data.report_sha256), mono: true, breakAll: true },
    { label: "Dataset SHA-256 (reported)", value: formatHash(data.source_dataset_sha256), mono: true, breakAll: true },
    { label: "Dataset SHA-256 (recalculated)", value: formatHash(integrity.actual_dataset_sha256), mono: true, breakAll: true }
  ]);
  add(provenance, node("p", "rea-note", "The replay uses OHLCV and a deterministic next-open/resting-limit fill policy. It does not model real queue priority, complete order-book depth, or the true intrabar path."));
  if (Array.isArray(market.interval_gaps) && market.interval_gaps.length) {
    var gapList = add(provenance, node("ul", "rea-simple-list"));
    market.interval_gaps.forEach(function (gap) {
      add(gapList, node("li", "", "Source interval gap: " + String(gap.previous_timestamp || "UNKNOWN") + " → " + String(gap.timestamp || "UNKNOWN") + " (" + String(gap.delta_seconds || "UNKNOWN") + " seconds). Preserved; no candle was invented."));
    });
  }

  var resultsSection = section("02 · Base results · strategies vs benchmarks",
    "These clean-room models are not the commercial bots. Returns are percentages of the simulated starting-equity reference. Benchmarks have different exposures and must not be ranked as if equivalent.");
  var strategyRows = data.results.filter(function (row) { return row.group === "strategy"; });
  var benchmarkRows = data.results.filter(function (row) { return row.group === "benchmark"; });
  var baseColumns = [
    { label: "Model", value: function (r) { return r.label; } },
    { label: "Net return", value: function (r) { return formatPct(r.return_pct); }, className: "rea-number" },
    { label: "Net PnL", value: function (r) { return formatMoney(r.net_pnl_usdt); }, className: "rea-number" },
    { label: "Final equity", value: function (r) { return formatPlainMoney(r.ending_equity_usdt); }, className: "rea-number" },
    { label: "Max DD", value: function (r) { return formatPct(r.max_drawdown_pct); }, className: "rea-number" },
    { label: "Fills", value: function (r) { return formatNumber(r.fills, 0); }, className: "rea-number" },
    { label: "Time in market", value: function (r) { return formatPct(r.time_in_market_pct); }, className: "rea-number" },
    { label: "Fees", value: function (r) { return formatPlainMoney(r.fees_usdt); }, className: "rea-number" },
    { label: "Spread cost", value: function (r) { return formatPlainMoney(r.spread_cost_usdt); }, className: "rea-number" },
    { label: "Slippage cost", value: function (r) { return formatPlainMoney(r.slippage_cost_usdt); }, className: "rea-number" },
    { label: "Turnover", value: function (r) { return formatPlainMoney(r.turnover_usdt); }, className: "rea-number" }
  ];
  add(resultsSection, node("h4", "rea-subtitle", "Independent clean-room models"));
  table(resultsSection, baseColumns, strategyRows);
  add(resultsSection, node("h4", "rea-subtitle", "Benchmarks · different exposures"));
  table(resultsSection, baseColumns, benchmarkRows);

  var stressSection = section("03 · Mechanical cost stress",
    "1x is the configured profile; 2x and 3x multiply the configured cost assumptions. This is a mechanical sensitivity check, not proof of statistical robustness or positive edge.");
  var costColumns = [
    { label: "Group", value: function (r) { return r.group === "strategies" ? "Strategy" : "Benchmark"; } },
    { label: "Model", value: function (r) { return labels[r.model] || r.model; } },
    { label: "Cost stress", value: function (r) { return r.stress_factor; } },
    { label: "Return", value: function (r) { return formatPct(r.return_pct); }, className: "rea-number" },
    { label: "Max DD", value: function (r) { return formatPct(r.max_drawdown_pct); }, className: "rea-number" },
    { label: "Fills", value: function (r) { return formatNumber(r.fills, 0); }, className: "rea-number" },
    { label: "Time in market", value: function (r) { return formatPct(r.time_in_market_pct); }, className: "rea-number" },
    { label: "Fees", value: function (r) { return formatPlainMoney(r.fees_usdt); }, className: "rea-number" },
    { label: "Spread", value: function (r) { return formatPlainMoney(r.spread_cost_usdt); }, className: "rea-number" },
    { label: "Slippage", value: function (r) { return formatPlainMoney(r.slippage_cost_usdt); }, className: "rea-number" },
    { label: "Turnover", value: function (r) { return formatPlainMoney(r.turnover_usdt); }, className: "rea-number" }
  ];
  table(stressSection, costColumns, data.cost_stress || []);

  var annualSection = section("04 · Annual slices · DCA only",
    "Each yearly slice resets its reference cash; it is not a compounded equity curve. Grid and Combo annual results are deliberately omitted until their source anomaly is resolved.");
  table(annualSection, [
    { label: "Period", value: function (r) { return r.year; } },
    { label: "Net return", value: function (r) { return formatPct(r.return_pct); }, className: "rea-number" },
    { label: "Max DD", value: function (r) { return formatPct(r.max_drawdown_pct); }, className: "rea-number" },
    { label: "Fills", value: function (r) { return formatNumber(r.fills, 0); }, className: "rea-number" },
    { label: "Time in market", value: function (r) { return formatPct(r.time_in_market_pct); }, className: "rea-number" }
  ], data.annual_dca || []);
  if (data.annual_validation && data.annual_validation.warnings && data.annual_validation.warnings.length) {
    var warning = add(annualSection, node("div", "rea-warning"));
    add(warning, node("strong", "", "ANNUAL GRID/COMBO SLICES: VALIDATION REQUIRED"));
    var warnList = add(warning, node("ul", "rea-simple-list"));
    data.annual_validation.warnings.forEach(function (text) { add(warnList, node("li", "", text)); });
  }

  var auditSection = section("05 · Integrity and reproducible tests",
    "This view distinguishes verified laboratory checks from the blocked full SENEX test collection. It does not convert test success into evidence of trading profitability.");
  table(auditSection, [
    { label: "Check", value: function (r) { return r.check; } },
    { label: "Result", value: function (r) { return r.result; }, className: "rea-status" },
    { label: "Evidence", value: function (r) { return r.detail; } }
  ], checkRows(data));
  metricCards(auditSection, [
    { label: "REA engine suite", value: String(tests.rea_test_passed || "UNKNOWN") + "/" + String(tests.rea_test_total || "UNKNOWN"), detail: "Six isolated accounting/replay/strategy/resilience regression files" },
    { label: "Paper execution contract", value: String((tests.paper_execution_contract_suite || {}).result || "UNKNOWN"), detail: "Lifecycle, durable journal, checkpoint replay, risk and reconciliation" },
    { label: "Focused regression bundle", value: String((tests.latest_focused_bundle || {}).result || "UNKNOWN"), detail: "REA + paper contract + dashboard snapshot tests" },
    { label: "Broader SENEX suite", value: String((tests.suite_excluding_collection_blocker || {}).result || "UNKNOWN"), detail: "Excludes test_health_readiness.py; not the complete suite" },
    { label: "Full suite", value: "NOT VERIFIED", detail: "Collection fails: missing sse_starlette; no dependency was installed" }
  ]);
  var testDetails = disclosure(auditSection, "Exact test scope and resilience coverage", false);
  add(testDetails, node("p", "rea-note", "pytest " + String(tests.pytest_version || "UNKNOWN") + " · REA engine suite exit code " + String((tests.rea_engine_suite || {}).exit_code) + " · " + String((tests.rea_engine_suite || {}).result || "UNKNOWN") + "."));
  var features = add(testDetails, node("ul", "rea-simple-list"));
  (tests.resilience_features || []).forEach(function (feature) { add(features, node("li", "", feature)); });
  add(testDetails, node("p", "rea-note", String(tests.global_suite_status || "Full-suite status unknown.")));
  var gunbotDetails = disclosure(auditSection, "Static Gunbot package audit (not semantic equivalence)", false);
  keyValueGrid(gunbotDetails, [
    { label: "Audit ZIP", value: formatNumber(data.gunbot_static_audit.zip_bytes, 0) + " bytes" },
    { label: "ZIP SHA-256", value: formatHash(data.gunbot_static_audit.zip_sha256), mono: true, breakAll: true },
    { label: "ZIP entries / manifest rows", value: String(data.gunbot_static_audit.zip_entries) + " / " + String(data.gunbot_static_audit.manifest_rows) },
    { label: "Manifest mismatches", value: String(data.gunbot_static_audit.manifest_mismatches) },
    { label: "Semantic equivalence", value: data.gunbot_static_audit.semantic_equivalence_verified ? "VERIFIED" : "NOT VERIFIED" },
    { label: "Commercial executable", value: String(data.gunbot_static_audit.commercial_executable_analysis || "UNKNOWN") }
  ]);
  add(gunbotDetails, node("p", "rea-note", "Bytecode extraction, indexing and selective pseudocode are not equivalent to verified semantic reconstruction. Further commercial-executable analysis remains paused pending authorization and scope clarification."));

  var otherAsset = section("06 · Second asset audit · ETHUSDT",
    "The local lab contains an ETH evaluation report, but this dashboard deliberately displays ingestion/integrity status only. No ETH return figures are rendered here.");
  var eth = data.eth_ingestion_audit_only || {};
  metricCards(otherAsset, [
    { label: "Monthly source archives", value: String(eth.archive_count || "UNKNOWN"), detail: "Official archive checksums: " + (eth.all_official_checksums_match ? "verified" : "UNKNOWN") },
    { label: "CSV rows", value: formatNumber(eth.data_rows, 0), detail: "4-hour spot candles" },
    { label: "OOS rows", value: formatNumber(eth.oos_rows, 0), detail: "Performance omitted in this panel" },
    { label: "Historical gap count", value: String(eth.historical_gap_count === undefined ? "UNKNOWN" : eth.historical_gap_count), detail: "Gap preserved; no synthetic candle" }
  ]);
  keyValueGrid(otherAsset, [
    { label: "ETH CSV SHA-256", value: formatHash(eth.dataset_sha256), mono: true, breakAll: true },
    { label: "Recalculated ETH hash", value: formatHash(eth.actual_dataset_sha256), mono: true, breakAll: true },
    { label: "ETH report exists", value: eth.evaluation_report_exists ? "YES · LOCAL ONLY" : "NO / UNKNOWN" },
    { label: "ETH strategy metrics shown", value: eth.performance_metrics_included ? "YES" : "NO · intentionally withheld" }
  ]);

  var limits = section("07 · Constraints and limitations",
    "The numbers below describe a historical simulation on a limited data representation. They do not establish expected future returns.");
  var limitList = add(limits, node("ul", "rea-simple-list"));
  (market.limitations || []).forEach(function (item) { add(limitList, node("li", "", item)); });
  add(limitList, node("li", "", "Market regimes remain UNKNOWN_NOT_CLASSIFIED; no claim is made that performance generalizes across regimes."));
  add(limitList, node("li", "", "DCA's BTC test had approximately 99.36% time in market; Grid and Combo had much lower exposure."));
  add(limitList, node("li", "", "Neither historical results nor unit-test pass rates prove prospective profitability. EDGE remains UNPROVEN."));
  var license = add(limits, node("div", "rea-license-banner", ""));
  add(license, node("strong", "", String(data.license.notice || "NO DEPLOY / NO COMMERCIAL INTEGRATION UNTIL RIGHTS ARE CLEARED")));
  add(license, node("p", "", String(data.license.status || "Research-only. Rights must be cleared before commercial or production use.")));
  add(license, node("p", "", "Current data-use boundary from the lab audit: research-only; commercial/product use and live proprietary trading require applicable written authorization."));
  var link = add(license, node("a", "", "Read the public-data terms recorded by the audit"));
  link.href = String(data.license.source_url || "https://github.com/binance/binance-public-data/blob/master/TERMS_AND_CONDITIONS.md");
  link.target = "_blank";
  link.rel = "noopener noreferrer";

  var footer = add(host, node("div", "rea-panel-footer"));
  footer.textContent = "Snapshot integrity PASS · report SHA-256 " + formatHash(data.report_sha256) +
    " · static assets only · panel has no execution controls · LIVE=NO · EDGE=UNPROVEN.";
})();
