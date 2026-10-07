"use strict";

const state = {
  status: null,
  watchlist: [],
  signals: [],
  history: [],
  oi: [],
  audit: [],
};

const $ = (id) => document.getElementById(id);

function node(tag, className, content) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (content !== undefined && content !== null) element.textContent = String(content);
  return element;
}

function clear(element) {
  while (element.firstChild) element.removeChild(element.firstChild);
}

function dateInIST() {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Kolkata",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(new Date());
  const values = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return `${values.year}-${values.month}-${values.day}`;
}

function formatTime(value, options = {}) {
  if (!value) return "—";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "Invalid timestamp";
  return new Intl.DateTimeFormat("en-IN", {
    timeZone: "Asia/Kolkata",
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    ...options,
  }).format(parsed);
}

function formatNumber(value) {
  if (value === null || value === undefined || value === "") return "—";
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return String(value);
  return new Intl.NumberFormat("en-IN", { maximumFractionDigits: 4 }).format(parsed);
}

function signalLabel(signal) {
  const side = signal.signal_type.endsWith("high") ? "HIGH" : "LOW";
  const kind = signal.signal_type.startsWith("collision_") ? " COLLISION" : "";
  return `${signal.timeframe}${kind} ${side} BREAK`;
}

function setError(message) {
  const banner = $("error-banner");
  banner.textContent = message;
  banner.hidden = !message;
}

function toast(message) {
  const element = $("toast");
  element.textContent = message;
  element.hidden = false;
  window.setTimeout(() => {
    element.hidden = true;
  }, 4500);
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    cache: "no-store",
    ...options,
    headers: {
      ...(options.headers || {}),
    },
  });
  const contentType = response.headers.get("content-type") || "";
  const body = contentType.includes("application/json")
    ? await response.json()
    : await response.text();
  if (!response.ok) {
    const detail = typeof body === "object" && body !== null ? body.detail : body;
    throw new Error(detail || `Request failed (${response.status}).`);
  }
  return body;
}

function makeCell(label, value, extraClass = "") {
  const cell = node("div", `signal-cell ${extraClass}`.trim());
  cell.append(node("small", "", label));
  cell.append(node("strong", "", value));
  return cell;
}

function makeSignalCard(signal) {
  const card = node("article", "signal-row");
  if (signal.status !== "active" || !signal.is_current) card.classList.add("signal-superseded");
  card.append(makeCell("SYMBOL", signal.symbol, "symbol-cell"));

  const direction = node("div", "signal-cell");
  direction.append(node("small", "", "DIRECTION"));
  direction.append(
    node(
      "span",
      `direction-pill direction-${signal.direction}`,
      signal.direction === "bullish" ? "Bullish" : "Bearish",
    ),
  );
  card.append(direction);

  const event = node("div", "signal-cell");
  event.append(node("small", "", `EVENT · ${formatTime(signal.candle_end)} IST`));
  event.append(node("strong", "signal-type", signalLabel(signal)));
  if (signal.status !== "active" || !signal.is_current) {
    event.append(node("span", "signal-history-tag", "SUPERSEDED BY REPLAY"));
  }
  card.append(event);
  card.append(makeCell("CANDLE CLOSE", formatNumber(signal.close)));

  const level = node("div", "signal-cell");
  level.append(node("small", "", "LEVEL · OPENING RANGE"));
  level.append(
    node(
      "strong",
      "",
      `${formatNumber(signal.level)} · ${formatNumber(signal.range_high)} / ${formatNumber(signal.range_low)}`,
    ),
  );
  card.append(level);

  const oi = node("div", "signal-cell");
  oi.append(node("small", "", "OI CONTEXT"));
  const oiStatusLabels = {
    raw_only_uncalibrated: "Raw · not calibrated",
    stale: "Stale",
    illiquid: "Illiquid",
    unavailable: "Unavailable",
  };
  const oiStatus = oiStatusLabels[signal.oi_context_status] || "Unavailable";
  oi.append(node("strong", "signal-oi", oiStatus));
  card.append(oi);

  const chart = node("a", "signal-chart", "TradingView ↗");
  chart.href = `https://www.tradingview.com/chart/?symbol=${encodeURIComponent(`NSE:${signal.symbol}`)}&interval=5`;
  chart.target = "_blank";
  chart.rel = "noopener noreferrer";
  chart.setAttribute("aria-label", `Open ${signal.symbol} on TradingView`);
  card.append(chart);

  const details = node("details", "signal-details");
  const summary = node("summary", "", "Source & OI details");
  details.append(summary);
  const source = signal.candle_source || "source not recorded";
  const timestamp = signal.candle_provider_time
    ? formatTime(signal.candle_provider_time)
    : "not supplied";
  const receipt = signal.candle_received_at
    ? formatTime(signal.candle_received_at)
    : "not recorded";
  details.append(
    node(
      "p",
      "",
      `Completed candle: ${formatTime(signal.candle_start)}–${formatTime(signal.candle_end)} IST · source: ${source} · source timestamp: ${timestamp} IST · imported: ${receipt} IST · range: ${formatNumber(signal.range_high)} / ${formatNumber(signal.range_low)} · strategy ${signal.strategy_version}`,
    ),
  );
  details.append(
    node(
      "p",
      "",
      signal.in_app_notification_status === "available"
        ? "In-app record: stored locally and available in signal history. External delivery is disabled."
        : "No notification record is available; review the local audit history.",
    ),
  );
  const snapshots = signal.oi_contexts || [];
  if (snapshots.length) {
    details.append(node("p", "", "Latest available contract snapshots at or before this candle:"));
    const list = node("ul");
    for (const snapshot of snapshots) {
      const identity = snapshot.contract_type === "option"
        ? `${snapshot.symbol} ${snapshot.option_type} ${formatNumber(snapshot.strike)} · ${snapshot.expiry}`
        : `${snapshot.symbol} FUT · ${snapshot.expiry}`;
      list.append(
        node(
          "li",
          "",
          `${identity} · OI ${formatNumber(snapshot.open_interest)} · provider OI Δ ${formatNumber(snapshot.oi_change)} · volume ${formatNumber(snapshot.volume)} · LTP ${formatNumber(snapshot.ltp)} · ${formatTime(snapshot.provider_time)} IST · ${snapshot.status}`,
        ),
      );
    }
    details.append(list);
  } else {
    details.append(node("p", "", "No OI observation was available at or before this signal. Price validity is unaffected."));
  }
  card.append(details);
  return card;
}

function renderSignals() {
  const container = $("signals-list");
  clear(container);
  const symbolFilter = $("filter-symbol").value.trim().toUpperCase();
  const directionFilter = $("filter-direction").value;
  const timeframeFilter = $("filter-timeframe").value;
  const typeFilter = $("filter-type").value;
  const oiFilter = $("filter-oi").value;
  const source = $("filter-superseded").checked ? state.history : state.signals;
  const filtered = source.filter((signal) => {
    return (!symbolFilter || signal.symbol.includes(symbolFilter))
      && (!directionFilter || signal.direction === directionFilter)
      && (!timeframeFilter || signal.timeframe === timeframeFilter)
      && (!typeFilter || signal.signal_type.startsWith(typeFilter))
      && (!oiFilter || signal.oi_context_status === oiFilter);
  });
  $("signal-count").textContent = `${filtered.length} ${filtered.length === 1 ? "event" : "events"}`;
  if (!filtered.length) {
    container.append(
      node(
        "div",
        "empty-state",
        source.length
          ? "No confirmed price events match these filters."
          : "No confirmed signals currently. This replay contains no qualifying completed-candle event.",
      ),
    );
    return;
  }
  for (const signal of filtered) container.append(makeSignalCard(signal));
}

function healthItem(title, description, good = false) {
  const row = node("li", "health-row");
  row.append(node("span", `health-state-dot${good ? " good" : ""}`));
  const copy = node("div");
  copy.append(node("strong", "", title));
  copy.append(node("span", "", description));
  row.append(copy);
  return row;
}

function renderHealth() {
  const status = state.status;
  const list = $("health-list");
  clear(list);
  list.append(
    healthItem(
      "Price feed · replay only",
      `No live feed is configured. ${status.candle_interval_count} imported intervals${status.last_candle_import_at ? ` · last import ${formatTime(status.last_candle_import_at)} IST` : ""}.`,
    ),
  );
  list.append(
    healthItem(
      "Beacon · manual import",
      `${state.watchlist.length} watched symbols; no automated collection.`,
    ),
  );
  list.append(
    healthItem(
      status.integrations.oi_data === "stale"
        ? "OI · stale observations"
        : status.integrations.oi_data === "illiquid"
          ? "OI · illiquid observations"
          : status.integrations.oi_data === "raw_only_uncalibrated"
            ? "OI · raw / uncalibrated"
            : "OI · unavailable",
      status.integrations.oi_data === "stale"
        ? `${status.oi_observation_count} stored snapshot(s); stale context is excluded from scoring.`
        : `${status.oi_observation_count} stored contract snapshot(s); no score or aggregate.`,
    ),
  );
  list.append(
    healthItem(
      "Calendar · not configured",
      "Trading-day validity and special sessions are not verified.",
    ),
  );
  list.append(
    healthItem(
      "Telegram · disabled",
      "Credentials and delivery setup are not approved or configured.",
    ),
  );
  list.append(
    healthItem(
      "Network · loopback only",
      "LAN and public access are blocked by the app.",
      true,
    ),
  );
  $("alerts-toggle").textContent = status.alerts_paused ? "Resume alerts" : "Pause alerts";
  $("alerts-toggle").setAttribute(
    "aria-pressed",
    status.alerts_paused ? "true" : "false",
  );
}

function rangeStatus(ranges, timeframe) {
  const item = ranges.find((range) => range.timeframe === timeframe);
  if (!item) return `${timeframe} —`;
  if (item.status === "complete") return `${timeframe} ready`;
  return `${timeframe} incomplete · ${item.missing_candles} missing`;
}

function renderWatchlist() {
  const body = $("watchlist-body");
  clear(body);
  $("watchlist-count").textContent = `${state.watchlist.length} ${state.watchlist.length === 1 ? "symbol" : "symbols"}`;
  if (!state.watchlist.length) {
    const row = node("tr");
    const cell = node("td", "table-empty", "No monitored symbols for this date.");
    cell.colSpan = 5;
    row.append(cell);
    body.append(row);
  } else {
    for (const item of state.watchlist) {
      const row = node("tr");
      row.append(node("td", "", item.symbol));
      const beacon = item.direction_conflict
        ? "BULL + BEAR · conflict"
        : item.bullish_seen
          ? "Bullish"
          : item.bearish_seen
            ? "Bearish"
            : item.source === "beacon"
              ? "Imported"
              : "Manual";
      const beaconCell = node("td");
      beaconCell.append(node("span", `table-tag${item.direction_conflict ? " danger" : ""}`, beacon));
      row.append(beaconCell);
      const mappingCell = node("td");
      mappingCell.append(
        node(
          "span",
          `table-tag${item.is_excluded ? "" : item.provider_instrument_id && item.fno_eligible ? " success" : " danger"}`,
          item.is_excluded
            ? "Excluded"
            : item.provider_instrument_id && item.fno_eligible
            ? `ID ${item.provider_instrument_id}`
            : "Mapping needed",
        ),
      );
      row.append(mappingCell);
      const rangeCell = node("td");
      rangeCell.append(node("span", "range-status", rangeStatus(item.opening_ranges, "15M")));
      rangeCell.append(node("span", "range-status", rangeStatus(item.opening_ranges, "30M")));
      row.append(rangeCell);
      const candleCell = node("td", "", String(item.candles_loaded));
      if (item.data_issues.length) {
        const issues = node("details", "data-issue-details");
        issues.append(
          node("summary", "micro-copy", `${item.data_issues.length} data issue(s) · review`),
        );
        const issueList = node("ul");
        for (const issue of item.data_issues) {
          const time = issue.interval_start ? ` · ${formatTime(issue.interval_start)} IST` : "";
          issueList.append(node("li", "", `${issue.message}${time}`));
        }
        issues.append(issueList);
        candleCell.append(issues);
      }
      if (item.is_excluded) {
        candleCell.append(node("div", "micro-copy", item.exclusion_reason || "Excluded for this date."));
      } else {
        const exclude = node("button", "exclude-button", "Exclude");
        exclude.type = "button";
        exclude.setAttribute("aria-label", `Exclude ${item.symbol} from this trading date`);
        exclude.addEventListener("click", () => excludeSymbol(item.symbol));
        candleCell.append(exclude);
      }
      row.append(candleCell);
      if (item.is_excluded) row.classList.add("row-excluded");
      body.append(row);
    }
  }
  const unmapped = state.unmapped || [];
  $("unmapped-note").textContent = unmapped.length
    ? `Beacon symbols without a confirmed mapping are not monitored: ${unmapped.join(", ")}.`
    : "Symbols stay on today's list after a Beacon import disappears. Conflicting directions are retained.";
}

function contractName(snapshot) {
  if (snapshot.contract_type === "option") {
    return `${snapshot.symbol} ${snapshot.option_type} ${formatNumber(snapshot.strike)} · ${snapshot.expiry}`;
  }
  return `${snapshot.symbol} FUT · ${snapshot.expiry}`;
}

function renderOI() {
  const body = $("oi-body");
  clear(body);
  $("oi-count").textContent = `${state.oi.length} ${state.oi.length === 1 ? "contract" : "contracts"}`;
  if (!state.oi.length) {
    const row = node("tr");
    const cell = node("td", "table-empty", "No OI snapshots imported.");
    cell.colSpan = 6;
    row.append(cell);
    body.append(row);
    return;
  }
  for (const snapshot of state.oi) {
    const row = node("tr");
    row.append(node("td", "", contractName(snapshot)));
    row.append(node("td", "", formatNumber(snapshot.open_interest)));
    row.append(node("td", "", formatNumber(snapshot.oi_change)));
    row.append(node("td", "", formatNumber(snapshot.volume)));
    row.append(node("td", "", formatNumber(snapshot.ltp)));
    const timeCell = node("td");
    timeCell.append(node("div", "", `${formatTime(snapshot.provider_time)} IST`));
    timeCell.append(node("span", "table-tag", snapshot.status));
    row.append(timeCell);
    body.append(row);
  }
}

function renderAudit() {
  const body = $("audit-body");
  clear(body);
  if (!state.audit.length) {
    const row = node("tr");
    const cell = node("td", "table-empty", "No audit events for this session.");
    cell.colSpan = 4;
    row.append(cell);
    body.append(row);
    return;
  }
  for (const event of state.audit) {
    const row = node("tr");
    row.append(node("td", "", `${formatTime(event.occurred_at)} IST`));
    row.append(node("td", "", event.event_type));
    row.append(node("td", "", event.symbol || "—"));
    row.append(node("td", "", JSON.stringify(event.details)));
    body.append(row);
  }
}

function renderAll() {
  const status = state.status;
  $("metric-watchlist").textContent = String(status.watchlist_count);
  $("metric-signals").textContent = String(status.active_signal_count);
  $("metric-ranges").textContent = String(status.ranges_ready);
  $("metric-issues").textContent = String(status.data_issue_count);
  renderSignals();
  renderHealth();
  renderWatchlist();
  renderOI();
  renderAudit();
}

async function refresh() {
  const date = $("trading-date").value;
  if (!date) {
    setError("Choose a trading date before loading the local replay.");
    return;
  }
  setError("");
  $("refresh-button").disabled = true;
  const query = `trading_date=${encodeURIComponent(date)}`;
  try {
    const [status, watchlist, history, oi, audit] = await Promise.all([
      api(`/api/status?${query}`),
      api(`/api/watchlist?${query}`),
      api(`/api/history?${query}`),
      api(`/api/oi?${query}`),
      api(`/api/audit?${query}&limit=100`),
    ]);
    state.status = status;
    state.watchlist = watchlist.symbols;
    state.unmapped = watchlist.unmapped_symbols;
    state.history = history.events;
    state.signals = history.events.filter(
      (item) => item.status === "active" && item.is_current,
    );
    state.oi = oi.observations;
    state.audit = audit.events;
    $("export-button").href = `/api/export/signals.csv?${query}`;
    renderAll();
  } catch (error) {
    setError(`Could not load local replay state: ${error.message}`);
  } finally {
    $("refresh-button").disabled = false;
  }
}

function parseSymbols(value) {
  return value.split(/[\s,;]+/).map((item) => item.trim()).filter(Boolean);
}

function setImportResult(message, isError = false) {
  const result = $("import-result");
  result.textContent = message;
  result.classList.toggle("has-errors", isError);
  result.hidden = false;
}

async function submitForm(form, action) {
  const button = form.querySelector('button[type="submit"]');
  button.disabled = true;
  try {
    await action();
  } catch (error) {
    setImportResult(error.message, true);
    setError(`Import failed: ${error.message}`);
  } finally {
    button.disabled = false;
  }
}

async function readCsvFile(input) {
  const file = input.files && input.files[0];
  if (!file) throw new Error("Choose a CSV file first.");
  if (file.size > 5_000_000) throw new Error("CSV upload exceeds the 5 MB limit.");
  return file.text();
}

function importSummary(result, kind) {
  if (kind === "candles") {
    const details = result.symbols_reconciled.map((item) => {
      const rangeSummary = Object.entries(item.ranges)
        .map(([frame, range]) => `${frame} ${range.status}`)
        .join(" · ");
      const issues = item.issues.length ? ` · ${item.issues.length} data issue(s)` : "";
      return `${item.symbol}: ${rangeSummary}${issues}`;
    });
    return `Replay import saved ${result.inserted} new candle version(s), ${result.duplicates} duplicate(s), and ${result.corrections} correction(s). ${details.join(" | ")} Signals are historical results, not live alerts.`;
  }
  return `Stored ${result.inserted} raw OI observation(s); skipped ${result.duplicates} duplicate(s). OI scoring remains uncalibrated.`;
}

function bindEvents() {
  $("refresh-button").addEventListener("click", refresh);
  $("trading-date").addEventListener("change", refresh);
  for (const id of [
    "filter-symbol",
    "filter-direction",
    "filter-timeframe",
    "filter-type",
    "filter-oi",
  ]) {
    $(id).addEventListener("input", renderSignals);
    $(id).addEventListener("change", renderSignals);
  }
  $("filter-superseded").addEventListener("change", renderSignals);

  $("mapping-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    submitForm(form, async () => {
      await api("/api/mappings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          trading_date: $("trading-date").value,
          symbol: $("mapping-symbol").value,
          provider_instrument_id: $("mapping-id").value,
          confirm_fno_eligibility: $("mapping-confirm").checked,
        }),
      });
      form.reset();
      setImportResult("Mapping recorded. Import authorized candles for historical replay.");
      toast("Symbol mapping saved to the local audit history.");
      await refresh();
    });
  });

  $("beacon-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    submitForm(form, async () => {
      const result = await api("/api/beacon/import", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          trading_date: $("trading-date").value,
          bullish_symbols: parseSymbols($("bullish-symbols").value),
          bearish_symbols: parseSymbols($("bearish-symbols").value),
          source_name: $("beacon-source").value,
        }),
      });
      setImportResult(
        `Recorded ${result.observations} directional observation(s). ${result.mapped_symbols.length} mapped symbol(s) added to the watchlist; ${result.unmapped_symbols.length} require an explicit mapping.${result.conflicts.length ? ` Conflicts preserved: ${result.conflicts.join(", ")}.` : ""}`,
      );
      toast("Manual list observations recorded.");
      await refresh();
    });
  });

  $("candle-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    submitForm(form, async () => {
      const csvText = await readCsvFile($("candle-file"));
      const result = await api(
        `/api/candles/import?trading_date=${encodeURIComponent($("trading-date").value)}`,
        {
          method: "POST",
          headers: { "Content-Type": "text/csv" },
          body: csvText,
        },
      );
      setImportResult(importSummary(result, "candles"));
      toast("Candles imported and replayed.");
      await refresh();
    });
  });

  $("oi-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    submitForm(form, async () => {
      const csvText = await readCsvFile($("oi-file"));
      const result = await api(
        `/api/oi/import?trading_date=${encodeURIComponent($("trading-date").value)}`,
        {
          method: "POST",
          headers: { "Content-Type": "text/csv" },
          body: csvText,
        },
      );
      setImportResult(importSummary(result, "oi"));
      toast("Raw OI observations imported; no score was calculated.");
      await refresh();
    });
  });

  $("alerts-toggle").addEventListener("click", async () => {
    if (!state.status) return;
    try {
      const paused = !state.status.alerts_paused;
      await api("/api/alerts", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          trading_date: $("trading-date").value,
          paused,
        }),
      });
      toast(paused ? "Alert preference paused; replay records remain visible." : "Alert preference resumed.");
      await refresh();
    } catch (error) {
      setError(`Could not update alert preference: ${error.message}`);
    }
  });
}

async function excludeSymbol(symbol) {
  const reason = window.prompt(`Reason to exclude ${symbol} from this trading date?`);
  if (reason === null) return;
  if (!reason.trim()) {
    setError("Enter a reason before excluding a symbol.");
    return;
  }
  try {
    await api("/api/watchlist/exclude", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        trading_date: $("trading-date").value,
        symbol,
        reason: reason.trim(),
      }),
    });
    toast(`${symbol} excluded for this trading date. Existing history remains intact.`);
    await refresh();
  } catch (error) {
    setError(`Could not exclude ${symbol}: ${error.message}`);
  }
}

function init() {
  $("trading-date").value = dateInIST();
  bindEvents();
  refresh();
}

document.addEventListener("DOMContentLoaded", init);
