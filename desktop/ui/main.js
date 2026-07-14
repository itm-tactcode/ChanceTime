/** Chance Time desktop — Home hub + module views (US / crypto / exchange / planned)
 *
 * Tab switches paint first; data loads after (idle/deferred) so UI stays snappy.
 * Module cards always render from FALLBACK_MODULES so navigation works offline.
 */

const DASH_BASE = "http://127.0.0.1:8787/";
let monitorBook = "paper";
let activeTab = "control";
let activeView = "home"; // home | us | crypto | exchange | planned
let frameLoadedForUrl = "";

/** Mirrors `chancetime.modules` — used when API/CLI hub is unavailable. */
const FALLBACK_MODULES = [
  {
    id: "us_venues",
    title: "Kalshi + Polymarket US",
    blurb: "Account APIs, dual-list arb research, paper/live books.",
    status: "active",
    cli_hint: "chancetime run --account paper",
    desktop_view: "us",
  },
  {
    id: "crypto_updown",
    title: "Global Polymarket · Crypto Up/Down",
    blurb: "Intl CLOB 5m/15m binaries + external spot. Paper-first Path C.",
    status: "paper_only",
    cli_hint: "chancetime crypto run --once",
    desktop_view: "crypto",
  },
  {
    id: "crypto_exchange",
    title: "US Crypto Exchange",
    blurb: "Path D paper: Coinbase spot feed + optional C signals.",
    status: "paper_only",
    cli_hint: "chancetime exchange run --once",
    desktop_view: "exchange",
  },
  {
    id: "alpaca",
    title: "Alpaca (stocks)",
    blurb: "Broker equities/options — stretch module, not scheduled.",
    status: "planned",
    cli_hint: "(stretch)",
    desktop_view: "planned",
  },
];

/** @type {Record<string, unknown>} */
const cache = {
  readiness: null,
  accounts: null,
  presets: null,
  suggestions: null,
  knobs: null,
};

const STRAT_NAMES = [
  "simple_edge",
  "arb_cross",
  "complement_arb",
  "mean_revert",
  "ml_edge",
  "llm_calibrated",
  "news_impulse",
];

function getInvoke() {
  const t = window.__TAURI__;
  if (!t?.core?.invoke) throw new Error("Tauri API not available");
  return t.core.invoke.bind(t.core);
}

function dashUrl(book) {
  const b = book || monitorBook;
  return `${DASH_BASE}?book=${encodeURIComponent(b)}#book=${encodeURIComponent(b)}`;
}

function $(id) {
  return document.getElementById(id);
}

function log(msg) {
  const line = `[${new Date().toLocaleTimeString()}] ${msg}`;
  const el = $("log");
  if (el) {
    el.textContent = `${line}\n${el.textContent}`.slice(0, 6000);
  }
  const home = $("homeLog");
  if (home) {
    home.hidden = false;
    home.textContent = `${line}\n${home.textContent}`.slice(0, 2000);
  }
  console.log(line);
}

function setRun(el, running, extra) {
  if (!el) return;
  el.textContent = running ? `running${extra || ""}` : `stopped${extra || ""}`;
  el.style.color = running ? "var(--good)" : "var(--muted)";
}

function short(s) {
  const one = String(s).split("\n")[0];
  return one.length > 56 ? `${one.slice(0, 56)}…` : one;
}

function selectedAccount() {
  return $("accountSelect")?.value || "paper";
}

/** Run after browser paints the new tab (double rAF + optional timeout). */
function afterPaint(fn) {
  requestAnimationFrame(() => {
    requestAnimationFrame(() => {
      // Yield so click feels instant even if invoke is slow
      setTimeout(fn, 0);
    });
  });
}

function setLoading(el, text) {
  if (el) el.textContent = text || "Loading…";
}

/**
 * Show tab shell immediately; schedule data load separately.
 */
function switchView(name) {
  activeView = name;
  document.querySelectorAll(".app-view").forEach((v) => {
    const on = v.id === `view-${name}`;
    v.classList.toggle("active", on);
    // Prefer hidden attribute; also set display for stubborn CSS
    if (on) {
      v.hidden = false;
      v.removeAttribute("hidden");
      v.style.display = "flex";
    } else {
      v.hidden = true;
      v.setAttribute("hidden", "");
      v.style.display = "none";
    }
  });
  if (name === "home") {
    afterPaint(() => loadHub());
  }
  if (name === "us") {
    afterPaint(() => refreshStatus());
  }
  if (name === "crypto") {
    afterPaint(() => {
      switchModTab("crypto", "control");
      refreshCryptoMonitor();
    });
  }
  if (name === "exchange") {
    afterPaint(() => {
      switchModTab("exchange", "control");
      refreshExchangeMonitor();
    });
  }
}

/** Control | Monitor tabs inside Path C / Path D (independent of US switchTab). */
function switchModTab(mod, mtab) {
  document.querySelectorAll(`.mod-tab[data-mod="${mod}"]`).forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.mtab === mtab);
  });
  document.querySelectorAll(`.mod-panel[data-mod="${mod}"]`).forEach((p) => {
    const on = p.dataset.mtab === mtab;
    p.classList.toggle("active", on);
    p.hidden = !on;
    if (on) {
      p.style.display = "block";
    } else {
      p.style.display = "none";
    }
  });
  if (mod === "crypto" && mtab === "monitor") {
    afterPaint(() => refreshCryptoMonitor({ full: true }));
  }
  if (mod === "exchange" && mtab === "monitor") {
    afterPaint(() => refreshExchangeMonitor({ full: true }));
  }
}

/** Parse CLI stdout that may have log noise around JSON. */
function parseJsonLoose(text) {
  const raw = String(text || "").trim();
  if (!raw) throw new Error("empty response");
  try {
    return JSON.parse(raw);
  } catch (_) {
    /* continue */
  }
  const start = raw.indexOf("{");
  const end = raw.lastIndexOf("}");
  if (start >= 0 && end > start) {
    return JSON.parse(raw.slice(start, end + 1));
  }
  const a0 = raw.indexOf("[");
  const a1 = raw.lastIndexOf("]");
  if (a0 >= 0 && a1 > a0) {
    return JSON.parse(raw.slice(a0, a1 + 1));
  }
  throw new Error("no JSON object in response");
}

function openModule(mod) {
  if (!mod) return;
  const id = mod.id || "";
  const v = mod.desktop_view || "";
  if (id === "us_venues" || v === "us") {
    switchView("us");
    switchTab("control");
    return;
  }
  if (id === "crypto_updown" || v === "crypto") {
    switchView("crypto");
    return;
  }
  if (id === "crypto_exchange" || v === "exchange") {
    switchView("exchange");
    return;
  }
  // Planned / stretch
  if ($("plannedTitle")) $("plannedTitle").textContent = mod.title || "Coming soon";
  if ($("plannedSub")) $("plannedSub").textContent = mod.blurb || "Planned module";
  if ($("plannedBody")) {
    $("plannedBody").textContent =
      (mod.blurb || "Not implemented.") +
      " CLI: " +
      (mod.cli_hint || "(planned)") +
      " · See docs/CRYPTO_VENUES.md";
  }
  switchView("planned");
}

function renderModuleCards(mods) {
  const grid = $("moduleGrid");
  if (!grid) return;
  const list = mods && mods.length ? mods : FALLBACK_MODULES;
  grid.innerHTML = list
    .map((m) => {
      const isPlanned = m.status === "planned";
      const cls = isPlanned ? "planned" : "openable";
      const title = escapeHtml(m.title || m.id);
      const blurb = escapeHtml(m.blurb || "");
      const hint = escapeHtml(m.cli_hint || "");
      const status = escapeHtml(m.status || "");
      const cta = isPlanned ? "Coming later" : "Open desk →";
      return `<button type="button" class="module-card ${cls}" data-id="${escapeHtml(m.id)}" data-view="${escapeHtml(m.desktop_view || "")}" ${isPlanned ? 'aria-disabled="true"' : ""}>
        <span class="mod-status">${status}</span>
        <strong>${title}</strong>
        <span class="muted small">${blurb}</span>
        <code class="small">${hint}</code>
        <span class="mod-cta">${cta}</span>
      </button>`;
    })
    .join("");
  // Handlers attached via document-level delegation (see wireUi) so re-renders stay live
}

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function switchTab(name) {
  if (activeView !== "us") switchView("us");
  activeTab = name;
  document.querySelectorAll(".tab").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.tab === name);
  });
  document.querySelectorAll(".panel").forEach((p) => {
    const on = p.id === `panel-${name}`;
    p.classList.toggle("active", on);
    p.hidden = !on;
  });

  // Defer all I/O so the tab chrome appears first
  afterPaint(() => {
    if (activeTab !== name) return; // user switched away
    if (name === "monitor") {
      ensureMonitor().catch((e) => log(`monitor: ${e}`));
    } else if (name === "settings") {
      loadKnobs({ force: false }).catch((e) => log(`knobs: ${e}`));
    } else if (name === "ops") {
      // Progressive: show cached instantly, refresh in background one-by-one
      paintOpsFromCache();
      loadOpsDeferred();
    }
  });
}

function paintOpsFromCache() {
  if (cache.readiness && $("readinessOut")) {
    $("readinessOut").textContent = formatReadiness(cache.readiness);
  } else if ($("readinessOut") && !$("readinessOut").dataset.loaded) {
    setLoading($("readinessOut"), "Checklist loads in background…");
  }
  if (cache.accounts && $("accountsOut")) {
    $("accountsOut").textContent = cache.accounts;
  } else if ($("accountsOut") && !$("accountsOut").dataset.loaded) {
    setLoading($("accountsOut"), "Accounts load in background…");
  }
  if (cache.presets) {
    renderPresets(cache.presets);
  }
  if (cache.suggestions) {
    renderSuggestions(cache.suggestions);
  }
}

/** Load ops sections sequentially so one slow CLI doesn't block the tab. */
function loadOpsDeferred() {
  const chain = [
    () => loadReadiness({ force: false }),
    () => loadAccounts({ force: false }),
    () => loadPresets({ force: false }),
  ];
  let i = 0;
  const step = () => {
    if (activeTab !== "ops" || i >= chain.length) return;
    const job = chain[i++];
    Promise.resolve()
      .then(job)
      .catch((e) => log(`ops load: ${e}`))
      .finally(() => setTimeout(step, 50));
  };
  step();
}

function formatReadiness(d) {
  const lines = (d.checklist || []).map(
    (c) => `[${c.gate}] ${String(c.id).padEnd(12)} ${c.cmd}`,
  );
  if (d.doc) lines.push("", `doc: ${d.doc}`);
  return lines.join("\n") || "(empty)";
}

function setBookButtons() {
  document.querySelectorAll(".book-btn").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.book === monitorBook);
  });
}

function setMonitorLive(portOpen) {
  const toolbar = $("monitorToolbar");
  const body = document.querySelector(".monitor-body");
  const hint = $("monitorHint");
  const frame = $("dashFrame");
  if (toolbar) toolbar.dataset.state = portOpen ? "online" : "offline";
  if (body) body.classList.toggle("is-live", portOpen);
  if (hint) {
    hint.textContent = portOpen ? `API · ${monitorBook.toUpperCase()}` : "API offline";
  }
  if (!frame) return;
  const url = dashUrl(monitorBook);
  if (portOpen) {
    if (frameLoadedForUrl !== url) {
      frame.src = url;
      frameLoadedForUrl = url;
    }
  } else if (frame.getAttribute("src")) {
    frame.removeAttribute("src");
    frameLoadedForUrl = "";
  }
  setBookButtons();
}

async function refresh() {
  try {
    const s = await getInvoke()("get_status");
    if ($("projectRoot")) $("projectRoot").textContent = s.projectRoot;
    let botExtra = "";
    if (s.botRunning) {
      if (s.botMode === "session" && s.botMaxPolls) {
        botExtra = ` · session ${s.botMaxPolls} polls`;
      } else if (s.botMode === "continuous") {
        botExtra = " · continuous";
      }
      if (s.lastBotMsg) botExtra += ` · ${short(s.lastBotMsg)}`;
    } else if (s.lastBotMsg) {
      botExtra = ` · ${short(s.lastBotMsg)}`;
    }
    setRun($("botState"), s.botRunning, botExtra);
    setRun(
      $("dashState"),
      s.dashboardRunning,
      s.lastDashMsg ? ` · ${short(s.lastDashMsg)}` : "",
    );
    if ($("portState")) {
      $("portState").textContent = s.dashboardPortOpen ? "open" : "closed";
      $("portState").style.color = s.dashboardPortOpen ? "var(--good)" : "var(--muted)";
    }
    if ($("trayState")) {
      $("trayState").textContent = s.trayOk ? "ok" : "unavailable";
      $("trayState").style.color = s.trayOk ? "var(--good)" : "var(--warn)";
    }
    if ($("modeBadge")) {
      $("modeBadge").textContent = s.paperMode ? "PAPER" : "LIVE";
      $("modeBadge").className = `badge ${s.paperMode ? "paper" : "live"}`;
    }
    if ($("miniBot")) {
      $("miniBot").textContent = s.botRunning
        ? s.botMode === "session"
          ? "bot session"
          : "bot on"
        : "bot off";
      $("miniBot").classList.toggle("on", s.botRunning);
    }
    if ($("miniDash")) {
      $("miniDash").textContent = s.dashboardPortOpen ? "api on" : "api off";
      $("miniDash").classList.toggle("on", s.dashboardPortOpen);
    }
    if ($("nowStrip")) {
      const acct = selectedAccount();
      const mode = s.botRunning
        ? s.botMode === "session"
          ? `SESSION ${s.botMaxPolls || "?"} polls`
          : "CONTINUOUS until Stop"
        : "bot stopped";
      $("nowStrip").textContent = `${s.paperMode ? "PAPER" : "LIVE"} · account ${acct} · ${mode}`;
    }
    if (activeTab === "monitor") {
      setMonitorLive(!!s.dashboardPortOpen);
      const h = $("monitorLiveHint");
      if (h) {
        h.textContent = s.dashboardPortOpen
          ? "page refreshes ~5s (equity/tables)"
          : "start API for live view";
        h.classList.toggle("pulse", !!s.botRunning && !!s.dashboardPortOpen);
      }
    }
  } catch (e) {
    log(`status error: ${e}`);
  }
}

function startBotOpts(maxPolls) {
  return {
    config: $("configPath")?.value || "config/default.yaml",
    account: selectedAccount(),
    maxPolls: maxPolls == null ? null : Number(maxPolls),
  };
}

async function ensureMonitor() {
  const invoke = getInvoke();
  let s = await invoke("get_status");
  if (!s.dashboardPortOpen) {
    try {
      log(String(await invoke("start_dashboard")));
    } catch (e) {
      log(`start API failed: ${e}`);
    }
  }
  for (let i = 0; i < 15; i++) {
    s = await invoke("get_status");
    if (s.dashboardPortOpen) {
      setMonitorLive(true);
      await refresh();
      return;
    }
    await new Promise((r) => setTimeout(r, 250));
  }
  setMonitorLive(false);
}

async function act(name, fn) {
  try {
    const msg = await fn();
    if (msg != null && msg !== "") {
      String(msg)
        .split("\n")
        .forEach((line, i) => log(i === 0 ? line : `  ${line}`));
    }
    await refresh();
  } catch (e) {
    String(e)
      .split("\n")
      .forEach((line, i) => log(i === 0 ? `${name} failed: ${line}` : `  ${line}`));
    await refresh();
  }
}

function renderStratGrid(strategies) {
  const grid = $("stratGrid");
  if (!grid) return;
  grid.innerHTML = "";
  STRAT_NAMES.forEach((name) => {
    const st = (strategies && strategies[name]) || { enabled: false, weight: 1, max_open: 8 };
    const cap =
      st.max_open === null || st.max_open === undefined || st.max_open === ""
        ? ""
        : Number(st.max_open);
    const row = document.createElement("div");
    row.className = "strat-row";
    row.innerHTML = `
      <span class="name">${name}</span>
      <label class="check"><input type="checkbox" data-s="${name}" data-f="enabled" ${st.enabled ? "checked" : ""}/> on</label>
      <label>w <input class="num" type="number" step="0.1" min="0" data-s="${name}" data-f="weight" value="${st.weight ?? 1}" title="Risk weight"/></label>
      <label>cap <input class="num" type="number" step="1" min="0" data-s="${name}" data-f="max_open" value="${cap === "" ? 8 : cap}" title="Max open positions for this strategy (0=unlimited)"/></label>
    `;
    grid.appendChild(row);
  });
}

function readFormSnapshot() {
  const strategies = {};
  STRAT_NAMES.forEach((n) => {
    strategies[n] = { enabled: false, weight: 1, max_open: 8 };
  });
  document.querySelectorAll("[data-s][data-f]").forEach((el) => {
    const name = el.dataset.s;
    const f = el.dataset.f;
    if (!strategies[name]) strategies[name] = {};
    if (el.type === "checkbox") {
      strategies[name][f] = el.checked;
    } else if (f === "max_open") {
      const v = el.value === "" ? null : Number(el.value);
      strategies[name][f] = Number.isFinite(v) ? v : null;
    } else {
      strategies[name][f] = Number(el.value);
    }
  });
  return {
    poll_interval_seconds: Number($("pollInterval").value) || 30,
    shadow_mode: $("shadowMode").checked,
    hot_reload_risk: !!$("hotReloadRisk")?.checked,
    data_source: $("dataSource").value,
    max_markets: Number($("maxMarkets").value) || 100,
    discovery_every_polls: Number($("discoveryEvery")?.value ?? 5),
    discovery_limit: Number($("discoveryLimit")?.value ?? 150),
    history_enabled: !!$("historyEnabled")?.checked,
    max_position_usd: Number($("maxPos").value) || 50,
    max_daily_loss_usd: Number($("maxLoss").value) || 25,
    max_open_positions: Number($("maxOpen").value) || 10,
    max_family_exposure_usd: Number($("maxFam").value) || 100,
    max_cluster_exposure_usd: Number($("maxCluster")?.value ?? 0),
    max_deploy_pct: Number($("maxDeployPct")?.value ?? 0),
    min_hours_to_close: Number($("minHoursClose")?.value ?? 0),
    max_days_to_close: Number($("maxDaysClose")?.value ?? 0),
    max_spread: Number($("maxSpread")?.value ?? 0.06),
    max_open_per_strategy: Number($("maxOpenPerStrat")?.value) || 8,
    take_profit_pct: Number($("tp").value),
    stop_loss_pct: Number($("sl").value),
    default_order_size_usd: Number($("orderSize").value) || 10,
    llm_enabled: $("llmEnabled").checked,
    llm_daily_budget_usd: Number($("llmBudget").value) || 5,
    llm_calibrated_max_calls: Number($("llmMaxCalls")?.value ?? 2),
    strategies,
  };
}

function fillForm(snap) {
  $("pollInterval").value = snap.poll_interval_seconds ?? 30;
  $("shadowMode").checked = !!snap.shadow_mode;
  if ($("hotReloadRisk")) $("hotReloadRisk").checked = !!snap.hot_reload_risk;
  $("dataSource").value = snap.data_source || "mock";
  $("maxMarkets").value = snap.max_markets ?? 100;
  if ($("discoveryEvery")) $("discoveryEvery").value = snap.discovery_every_polls ?? 5;
  if ($("discoveryLimit")) $("discoveryLimit").value = snap.discovery_limit ?? 150;
  if ($("historyEnabled")) $("historyEnabled").checked = !!snap.history_enabled;
  $("maxPos").value = snap.max_position_usd ?? 50;
  $("maxLoss").value = snap.max_daily_loss_usd ?? 25;
  $("maxOpen").value = snap.max_open_positions ?? 10;
  $("maxFam").value = snap.max_family_exposure_usd ?? 100;
  if ($("maxCluster")) $("maxCluster").value = snap.max_cluster_exposure_usd ?? 0;
  if ($("maxDeployPct")) $("maxDeployPct").value = snap.max_deploy_pct ?? 0;
  if ($("minHoursClose")) $("minHoursClose").value = snap.min_hours_to_close ?? 0;
  if ($("maxDaysClose")) $("maxDaysClose").value = snap.max_days_to_close ?? 0;
  if ($("maxSpread")) $("maxSpread").value = snap.max_spread ?? 0.06;
  if ($("maxOpenPerStrat")) {
    $("maxOpenPerStrat").value = snap.max_open_per_strategy ?? 8;
  }
  $("tp").value = snap.take_profit_pct ?? 0.3;
  $("sl").value = snap.stop_loss_pct ?? 0.25;
  $("orderSize").value = snap.default_order_size_usd ?? 10;
  $("llmEnabled").checked = snap.llm_enabled !== false;
  $("llmBudget").value = snap.llm_daily_budget_usd ?? 5;
  if ($("llmMaxCalls")) {
    const lc = snap.llm_calibrated_max_calls
      ?? snap.strategies?.llm_calibrated?.max_llm_calls_per_poll
      ?? 2;
    $("llmMaxCalls").value = lc;
  }
  renderStratGrid(snap.strategies || {});
  if ($("knobsPath")) $("knobsPath").textContent = "snapshot loaded — Save → user.yaml";
}

async function loadKnobs({ force = true } = {}) {
  if (!force && cache.knobs) {
    fillForm(cache.knobs);
    return;
  }
  if ($("knobsPath")) $("knobsPath").textContent = "Loading settings…";
  const snap = await getInvoke()("get_user_knobs");
  cache.knobs = snap;
  fillForm(snap);
  log("settings loaded");
}

async function saveKnobs() {
  cache.knobs = null;
  await act("save knobs", () =>
    getInvoke()("save_user_knobs_cmd", { knobs: readFormSnapshot() }),
  );
  await loadKnobs({ force: true });
}

async function runDoctor() {
  try {
    setLoading($("doctorOut"), "Running doctor…");
    const d = await getInvoke()("run_doctor");
    const lines = [d.summary || ""].concat(
      (d.checks || []).map((c) => `[${c.ok ? "ok" : c.level}] ${c.name}: ${c.detail}`),
    );
    if ($("doctorOut")) $("doctorOut").textContent = lines.join("\n");
    log(d.summary || "doctor done");
  } catch (e) {
    if ($("doctorOut")) $("doctorOut").textContent = String(e);
    log(`doctor failed: ${e}`);
  }
}

async function loadReadiness({ force = true } = {}) {
  if (!force && cache.readiness) {
    if ($("readinessOut")) {
      $("readinessOut").textContent = formatReadiness(cache.readiness);
      $("readinessOut").dataset.loaded = "1";
    }
    return;
  }
  setLoading($("readinessOut"), "Loading checklist…");
  const d = await getInvoke()("readiness_cmd");
  cache.readiness = d;
  if ($("readinessOut")) {
    $("readinessOut").textContent = formatReadiness(d);
    $("readinessOut").dataset.loaded = "1";
  }
}

async function loadAccounts({ force = true } = {}) {
  if (!force && cache.accounts) {
    if ($("accountsOut")) {
      $("accountsOut").textContent = cache.accounts;
      $("accountsOut").dataset.loaded = "1";
    }
    return;
  }
  setLoading($("accountsOut"), "Loading accounts…");
  const d = await getInvoke()("list_accounts_cmd");
  const text = d.raw || (d.lines || []).join("\n");
  cache.accounts = text;
  if ($("accountsOut")) {
    $("accountsOut").textContent = text;
    $("accountsOut").dataset.loaded = "1";
  }
}

function renderPresets(rows) {
  const el = $("presetList");
  if (!el) return;
  el.innerHTML = "";
  (rows || []).forEach((r) => {
    const div = document.createElement("div");
    div.className = "preset-item";
    div.innerHTML = `<div><strong>${r.name}</strong><div class="muted">${r.blurb || ""}</div></div>`;
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "slim primary";
    btn.textContent = "Apply";
    btn.onclick = () => {
      cache.knobs = null;
      act("preset", () => getInvoke()("apply_preset_cmd", { name: r.name }));
    };
    div.appendChild(btn);
    el.appendChild(div);
  });
}

async function loadPresets({ force = true } = {}) {
  if (!force && cache.presets) {
    renderPresets(cache.presets);
    return;
  }
  const el = $("presetList");
  if (el && !cache.presets) el.innerHTML = "<span class='muted'>Loading presets…</span>";
  const rows = await getInvoke()("list_presets_cmd");
  cache.presets = rows;
  renderPresets(rows);
}

function renderSuggestions(items) {
  const el = $("suggestList");
  if (!el) return;
  el.innerHTML = "";
  if (!items || !items.length) {
    el.innerHTML = "<span class='muted'>(no suggestions yet — need paper fills)</span>";
    return;
  }
  items.forEach((s) => {
    const div = document.createElement("div");
    div.className = `suggest-item sev-${s.severity}`;
    div.innerHTML = `<div><strong>[${s.severity}] ${s.title}</strong>
      <div class="muted">${s.detail}</div></div>`;
    if (s.patch && Object.keys(s.patch).length) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "slim";
      btn.textContent = "Apply";
      btn.onclick = () => {
        cache.knobs = null;
        act("apply suggestion", () =>
          getInvoke()("apply_suggestion_cmd", {
            account: selectedAccount(),
            suggestionId: s.id,
          }),
        );
      };
      div.appendChild(btn);
    }
    el.appendChild(div);
  });
}

function withTimeout(promise, ms, label) {
  let timer;
  const timeout = new Promise((_, reject) => {
    timer = setTimeout(
      () => reject(new Error(`${label || "op"} timed out after ${ms / 1000}s`)),
      ms,
    );
  });
  return Promise.race([promise, timeout]).finally(() => clearTimeout(timer));
}

function setBagBanner(items) {
  const banner = $("bagBanner");
  if (!banner) return;
  const bag = Array.isArray(items)
    ? items.find((s) => s && s.id === "bag_full")
    : null;
  if (bag) {
    banner.hidden = false;
    banner.textContent = `${bag.title}: ${bag.detail}`;
  } else {
    banner.hidden = true;
    banner.textContent = "";
  }
}

async function loadSuggest() {
  const el = $("suggestList");
  if (el) el.innerHTML = "<span class='muted'>Loading suggestions…</span>";
  try {
    const items = await withTimeout(
      getInvoke()("suggest_settings_cmd", {
        account: selectedAccount(),
      }),
      35000,
      "suggestions",
    );
    const list = Array.isArray(items) ? items : [];
    cache.suggestions = list;
    setBagBanner(list);
    renderSuggestions(list);
    log(`suggestions loaded (${list.length})`);
  } catch (e) {
    if (el) {
      el.innerHTML = `<span class="muted">Failed: ${String(e).slice(0, 400)}</span>`;
    }
    log(`suggestions failed: ${e}`);
  }
}

async function showLogs(which) {
  try {
    setLoading($("procLogs"), "Loading logs…");
    const text = await getInvoke()("get_logs", { which, lines: 50 });
    if ($("procLogs")) $("procLogs").textContent = text;
  } catch (e) {
    if ($("procLogs")) $("procLogs").textContent = String(e);
  }
}

function setOpsOut(text) {
  if ($("opsOut")) $("opsOut").textContent = text;
}

async function loadHub() {
  const eq = $("hubEquity");
  // Always keep clickable cards — never blank the grid on hub failure
  let mods = FALLBACK_MODULES;
  let data = null;
  try {
    try {
      const r = await fetch(`${DASH_BASE}api/hub`);
      if (r.ok) data = await r.json();
    } catch (_) {
      /* API offline */
    }
    if (!data) {
      try {
        const txt = await getInvoke()("crypto_cli_cmd", { args: ["hub"] });
        data = parseJsonLoose(txt);
      } catch (e) {
        log(`hub cli: ${e}`);
      }
    }
    if (data?.modules?.length) {
      mods = data.modules;
    }
    if (eq) {
      eq.textContent =
        data?.combined_equity != null
          ? `$${Number(data.combined_equity).toFixed(2)}`
          : "— (start API or run crypto hub for equity)";
    }
    if ($("hubNote")) {
      $("hubNote").textContent =
        data?.note ||
        "Cards work offline. Start API for live combined equity.";
    }
    if ($("hubMini")) {
      $("hubMini").textContent = `modules ${mods.length}`;
    }
  } catch (e) {
    log(`hub failed: ${e}`);
    if (eq) eq.textContent = "—";
  }
  renderModuleCards(mods);
}

let _cliBusy = false;

async function cryptoCli(args) {
  const out = $("cryptoOut");
  const busy = $("cryptoBusy");
  if (_cliBusy) {
    if (out) out.textContent = "Already running a command — wait for it to finish.";
    return;
  }
  _cliBusy = true;
  if (busy) busy.hidden = false;
  setLoading(out, `Running: chancetime crypto ${args.join(" ")}…\n(this can take 10–30s on network)`);
  log(`crypto ${args.join(" ")} start`);
  try {
    const text = await getInvoke()("crypto_cli_cmd", { args });
    if (out) out.textContent = text || "(empty output)";
    log(`crypto ${args.join(" ")} ok (${(text || "").length} chars)`);
    if (activeView === "crypto") refreshCryptoMonitor({ skipLog: true });
  } catch (e) {
    const msg = String(e);
    if (out) out.textContent = `ERROR: ${msg}\n\nIf this says command not found, rebuild desktop:\n  cd desktop && ./dev.sh`;
    log(`crypto failed: ${e}`);
  } finally {
    _cliBusy = false;
    if (busy) busy.hidden = true;
  }
}

async function exchangeCli(args) {
  const out = $("exchangeOut");
  const busy = $("exchangeBusy");
  if (_cliBusy) {
    if (out) out.textContent = "Already running a command — wait for it to finish.";
    return;
  }
  _cliBusy = true;
  if (busy) busy.hidden = false;
  setLoading(out, `Running: chancetime exchange ${args.join(" ")}…\n(this can take a few seconds)`);
  log(`exchange ${args.join(" ")} start`);
  try {
    const text = await getInvoke()("exchange_cli_cmd", { args });
    if (out) out.textContent = text || "(empty output)";
    log(`exchange ${args.join(" ")} ok (${(text || "").length} chars)`);
    if (activeView === "exchange") refreshExchangeMonitor({ skipLog: true });
  } catch (e) {
    const msg = String(e);
    if (out) {
      out.textContent = `ERROR: ${msg}\n\nIf exchange_cli_cmd missing, rebuild desktop:\n  cd desktop && ./dev.sh`;
    }
    log(`exchange failed: ${e}`);
  } finally {
    _cliBusy = false;
    if (busy) busy.hidden = true;
  }
}

function cryptoPaperStrategyOn() {
  return !!$("cryptoPaperStrategy")?.checked;
}

function exTradeSignalsOn() {
  return !!$("exTradeSignals")?.checked;
}

async function startCryptoSession(maxPolls) {
  try {
    const msg = await getInvoke()("start_crypto_session", {
      maxPolls: maxPolls ?? null,
      paperStrategy: cryptoPaperStrategyOn(),
      interval: 15.0,
    });
    log(msg);
    await refreshCryptoMonitor();
  } catch (e) {
    log(`crypto session start failed: ${e}`);
    if ($("cryptoLiveLog")) $("cryptoLiveLog").textContent = String(e);
  }
}

async function stopCryptoSession() {
  try {
    const msg = await getInvoke()("stop_crypto_session");
    log(msg);
    await refreshCryptoMonitor();
  } catch (e) {
    log(`crypto stop failed: ${e}`);
  }
}

async function resetCryptoBook() {
  if (
    !confirm(
      "Reset Path C paper book?\n\nThis deletes all crypto paper fills, positions, and settlements, and restores $1000 cash.\n\nStop the crypto session first if it is running.",
    )
  ) {
    return;
  }
  const out = $("cryptoOut");
  setLoading(out, "Resetting crypto_paper.db…");
  try {
    // Ensure session is stopped so it cannot rewrite old state mid-reset
    try {
      await getInvoke()("stop_crypto_session");
    } catch (_) {
      /* ok if already stopped */
    }
    const text = await getInvoke()("crypto_cli_cmd", {
      args: ["reset-book", "--yes", "--cash", "1000"],
    });
    if (out) out.textContent = text || "(reset ok)";
    log("crypto paper book reset");
    await refreshCryptoMonitor();
  } catch (e) {
    if (out) out.textContent = String(e);
    log(`crypto reset failed: ${e}`);
  }
}

async function startExchangeSession(maxPolls) {
  try {
    const msg = await getInvoke()("start_exchange_session", {
      maxPolls: maxPolls ?? null,
      tradeSignals: exTradeSignalsOn(),
      interval: 20.0,
    });
    log(msg);
    await refreshExchangeMonitor();
  } catch (e) {
    log(`exchange session start failed: ${e}`);
    if ($("exLiveLog")) $("exLiveLog").textContent = String(e);
  }
}

async function stopExchangeSession() {
  try {
    const msg = await getInvoke()("stop_exchange_session");
    log(msg);
    await refreshExchangeMonitor();
  } catch (e) {
    log(`exchange stop failed: ${e}`);
  }
}

async function refreshCryptoMonitor(opts = {}) {
  try {
    const st = await getInvoke()("get_status");
    const running = !!st.cryptoRunning;
    const sessTxt = running ? "running" : "stopped";
    const color = running ? "var(--good)" : "var(--muted)";
    for (const id of ["cryptoSessionState", "cryptoMonSession"]) {
      if ($(id)) {
        $(id).textContent = sessTxt;
        $(id).style.color = color;
      }
    }
    if ($("cryptoSessionPill")) {
      $("cryptoSessionPill").textContent = running
        ? `session ${st.cryptoMode || "on"}`
        : "session —";
      $("cryptoSessionPill").classList.toggle("on", running);
    }
    if ($("cryptoModeState")) {
      const strat = st.cryptoPaperStrategy ? "paper-strategy" : "shadow";
      const polls = st.cryptoMaxPolls != null ? `${st.cryptoMaxPolls} polls` : "continuous";
      $("cryptoModeState").textContent = running
        ? `${st.cryptoMode || "—"} · ${strat} · ${polls}`
        : "—";
    }
    if ($("cryptoLastMsg")) {
      $("cryptoLastMsg").textContent = st.lastCryptoMsg || "—";
    }
    if (!opts.skipLog) {
      try {
        const logs = await getInvoke()("get_logs", { which: "crypto", lines: 80 });
        if ($("cryptoLiveLog")) $("cryptoLiveLog").textContent = logs;
      } catch (_) {
        /* keep */
      }
    }
    let bookLine = "—";
    try {
      const txt = await getInvoke()("crypto_cli_cmd", { args: ["status"] });
      const j = parseJsonLoose(txt);
      const eq = j.last_equity ?? j.equity;
      bookLine =
        eq != null
          ? `eq $${Number(eq).toFixed(2)} · pos ${j.open_positions ?? 0} · fills ${j.fills_total ?? 0}`
          : "no snapshots yet";
    } catch (_) {
      if (!opts.quietBook) bookLine = "— (status unavailable)";
    }
    for (const id of ["cryptoBookState", "cryptoMonBook"]) {
      if ($(id)) $(id).textContent = bookLine;
    }
    if (opts.full) {
      await loadCryptoScorecard();
      await loadCryptoSignalsPanel();
    }
  } catch (e) {
    log(`crypto monitor: ${e}`);
  }
}

async function loadCryptoScorecard() {
  const out = $("cryptoScoreOut");
  if (!out) return;
  try {
    setLoading(out, "Loading scorecard…");
    const txt = await getInvoke()("crypto_cli_cmd", { args: ["scorecard"] });
    out.textContent = txt;
    try {
      const j = parseJsonLoose(txt);
      const go = j.go_nogo || {};
      if ($("cryptoMonGo")) {
        $("cryptoMonGo").textContent = go.status || "—";
      }
    } catch (_) {
      /* plain text ok */
    }
  } catch (e) {
    out.textContent = String(e);
  }
}

async function loadCryptoSignalsPanel() {
  const out = $("cryptoSigOut");
  if (!out) return;
  try {
    setLoading(out, "Loading signals…");
    const txt = await getInvoke()("exchange_cli_cmd", { args: ["signals"] });
    out.textContent = txt;
  } catch (e) {
    out.textContent = String(e);
  }
}

async function refreshExchangeMonitor(opts = {}) {
  try {
    const st = await getInvoke()("get_status");
    const running = !!st.exchangeRunning;
    const sessTxt = running ? "running" : "stopped";
    const color = running ? "var(--good)" : "var(--muted)";
    for (const id of ["exSessionState", "exMonSession"]) {
      if ($(id)) {
        $(id).textContent = sessTxt;
        $(id).style.color = color;
      }
    }
    if ($("exSessionPill")) {
      $("exSessionPill").textContent = running
        ? `session ${st.exchangeMode || "on"}`
        : "session —";
      $("exSessionPill").classList.toggle("on", running);
    }
    if ($("exModeState")) {
      const sig = st.exchangeTradeSignals ? "trade-signals" : "shadow";
      const polls = st.exchangeMaxPolls != null ? `${st.exchangeMaxPolls} polls` : "continuous";
      $("exModeState").textContent = running
        ? `${st.exchangeMode || "—"} · ${sig} · ${polls}`
        : "—";
    }
    if ($("exLastMsg")) {
      $("exLastMsg").textContent = st.lastExchangeMsg || "—";
    }
    if (!opts.skipLog) {
      try {
        const logs = await getInvoke()("get_logs", { which: "exchange", lines: 80 });
        if ($("exLiveLog")) $("exLiveLog").textContent = logs;
      } catch (_) {
        /* keep */
      }
    }
    try {
      const txt = await getInvoke()("exchange_cli_cmd", { args: ["status"] });
      const j = parseJsonLoose(txt);
      const eq = j.equity ?? j.last_equity;
      const cash = j.cash;
      const bookLine =
        eq != null
          ? `eq $${Number(eq).toFixed(2)} · pos ${j.open_positions ?? 0} · fills ${j.fills_total ?? 0}`
          : "no snapshots yet";
      if ($("exBookState")) $("exBookState").textContent = bookLine;
      if ($("exMonBook")) $("exMonBook").textContent = bookLine;
      if ($("exMonEquity")) {
        $("exMonEquity").textContent =
          cash != null && eq != null
            ? `cash $${Number(cash).toFixed(2)} · eq $${Number(eq).toFixed(2)}`
            : "—";
      }
    } catch (_) {
      if ($("exBookState") && !opts.quietBook) {
        $("exBookState").textContent = "— (status unavailable)";
      }
    }
    if (opts.full) {
      // keep quotes until user scans
    }
  } catch (e) {
    log(`exchange monitor: ${e}`);
  }
}

/** Central click router — works even if cards/buttons re-render. */
function wireUi() {
  document.addEventListener("click", (ev) => {
    const t = ev.target;
    if (!(t instanceof Element)) return;

    // Home module cards
    const card = t.closest(".module-card");
    if (card && card.closest("#moduleGrid")) {
      ev.preventDefault();
      const id = card.getAttribute("data-id");
      if (card.classList.contains("planned")) {
        const mod = FALLBACK_MODULES.find((m) => m.id === id) || {
          id,
          title: card.querySelector("strong")?.textContent,
          blurb: "Stretch / planned",
          desktop_view: "planned",
          status: "planned",
        };
        openModule(mod);
        log(`planned module ${id}`);
        return;
      }
      const mod =
        FALLBACK_MODULES.find((m) => m.id === id) || {
          id,
          desktop_view: card.getAttribute("data-view"),
          title: card.querySelector("strong")?.textContent,
        };
      // Prefer live hub module meta if present on card dataset
      openModule({
        ...mod,
        id: id || mod.id,
        desktop_view: card.getAttribute("data-view") || mod.desktop_view,
        title: card.querySelector("strong")?.textContent || mod.title,
        blurb: card.querySelector(".muted")?.textContent || mod.blurb,
        status: card.querySelector(".mod-status")?.textContent || mod.status,
      });
      log(`open module ${id}`);
      return;
    }

    // data-action buttons (crypto / exchange)
    const actionBtn = t.closest("[data-action]");
    if (actionBtn) {
      const action = actionBtn.getAttribute("data-action");
      const actions = {
        "crypto-scan": () => cryptoCli(["scan", "--limit", "12"]),
        "crypto-status": () => cryptoCli(["status"]),
        "crypto-scorecard": () => cryptoCli(["scorecard"]),
        "crypto-hub": () => cryptoCli(["hub"]),
        "crypto-reset-book": () => resetCryptoBook(),
        "ex-scan": () => exchangeCli(["scan"]),
        "ex-signals": () => exchangeCli(["signals"]),
        "ex-status": () => exchangeCli(["status"]),
        "ex-buy-btc": () => exchangeCli(["paper-buy", "BTC", "--size", "10"]),
      };
      if (actions[action]) {
        ev.preventDefault();
        actions[action]();
        return;
      }
    }
  });

  // US tabs only (data-tab without data-mod)
  document.querySelectorAll(".tab[data-tab]").forEach((btn) => {
    if (btn.classList.contains("mod-tab")) return;
    btn.onclick = () => switchTab(btn.dataset.tab);
  });
  // Path C/D module tabs
  document.querySelectorAll(".mod-tab").forEach((btn) => {
    btn.onclick = () => switchModTab(btn.dataset.mod, btn.dataset.mtab);
  });

  const bind = (id, fn) => {
    const el = $(id);
    if (el) el.onclick = fn;
  };

  bind("btnHomeFromUs", () => switchView("home"));
  bind("btnHomeFromCrypto", () => switchView("home"));
  bind("btnHomeFromExchange", () => switchView("home"));
  bind("btnHomeFromPlanned", () => switchView("home"));

  // Path C session + one-shots + monitor
  bind("btnCryptoCont", () => startCryptoSession(null));
  bind("btnCryptoStop", () => stopCryptoSession());
  bind("btnCryptoResetBook", () => resetCryptoBook());
  bind("btnCryptoRefreshLog", () => refreshCryptoMonitor());
  bind("btnCryptoGoMon", () => switchModTab("crypto", "monitor"));
  bind("btnCryptoMonScore", () => loadCryptoScorecard());
  bind("btnCryptoMonSig", () => loadCryptoSignalsPanel());
  document.querySelectorAll(".crypto-session-btn").forEach((btn) => {
    btn.onclick = () => startCryptoSession(Number(btn.dataset.polls) || 20);
  });
  bind("btnCryptoOnce", () => cryptoCli(["scan", "--limit", "12"]));
  bind("btnCryptoStatus", () => cryptoCli(["status"]));
  bind("btnCryptoScore", () => cryptoCli(["scorecard"]));
  bind("btnCryptoHub", () => cryptoCli(["hub"]));

  // Path D session + one-shots + monitor
  bind("btnExCont", () => startExchangeSession(null));
  bind("btnExStop", () => stopExchangeSession());
  bind("btnExRefreshLog", () => refreshExchangeMonitor());
  bind("btnExGoMon", () => switchModTab("exchange", "monitor"));
  bind("btnExMonScan", async () => {
    const out = $("exMonQuotes");
    if (out) setLoading(out, "Scanning quotes…");
    try {
      const t = await getInvoke()("exchange_cli_cmd", { args: ["scan"] });
      if (out) out.textContent = t;
    } catch (e) {
      if (out) out.textContent = String(e);
    }
  });
  bind("btnExMonSig", async () => {
    const out = $("exMonSig");
    if (out) setLoading(out, "Loading signals…");
    try {
      const t = await getInvoke()("exchange_cli_cmd", { args: ["signals"] });
      if (out) out.textContent = t;
    } catch (e) {
      if (out) out.textContent = String(e);
    }
  });
  document.querySelectorAll(".ex-session-btn").forEach((btn) => {
    btn.onclick = () => startExchangeSession(Number(btn.dataset.polls) || 20);
  });
  bind("btnExScan", () => exchangeCli(["scan"]));
  bind("btnExSignals", () => exchangeCli(["signals"]));
  bind("btnExStatus", () => exchangeCli(["status"]));
  bind("btnExBuy", () => exchangeCli(["paper-buy", "BTC", "--size", "10"]));

  // US desk controls
  bind("btnStartDash", () => act("start API", () => getInvoke()("start_dashboard")));
  bind("btnStopDash", () => act("stop API", () => getInvoke()("stop_dashboard")));
  bind("btnOpenBrowser", () => act("open browser", () => getInvoke()("open_dashboard")));
  bind("btnOpenBrowser2", () => act("open browser", () => getInvoke()("open_dashboard")));
  bind("btnStartBotCont", () =>
    act("start continuous", () => getInvoke()("start_bot", startBotOpts(null))),
  );
  bind("btnStopBot", () => act("stop bot", () => getInvoke()("stop_bot")));
  document.querySelectorAll(".session-btn").forEach((btn) => {
    btn.onclick = () => {
      const n = Number(btn.dataset.polls) || 20;
      act(`session ${n}`, () => getInvoke()("start_bot", startBotOpts(n)));
    };
  });
  bind("btnKill", () => act("kill all", () => getInvoke()("kill_all")));
  bind("btnGoOps", () => switchTab("ops"));
  bind("btnGoSettings", () => switchTab("settings"));
  bind("btnGoMonitor", () => switchTab("monitor"));
  bind("btnEnsureDash", () => ensureMonitor());
  bind("btnReloadFrame", () => {
    frameLoadedForUrl = "";
    if ($("dashFrame")) $("dashFrame").src = dashUrl(monitorBook);
  });
  bind("btnBookPaper", () => {
    monitorBook = "paper";
    frameLoadedForUrl = "";
    setMonitorLive(true);
  });
  bind("btnBookLive", () => {
    monitorBook = "live";
    frameLoadedForUrl = "";
    setMonitorLive(true);
  });
  bind("btnDoctor", () => runDoctor());
  bind("btnLogsBot", () => showLogs("bot"));
  bind("btnLogsDash", () => showLogs("dashboard"));
  bind("btnLoadKnobs", () => loadKnobs({ force: true }));
  bind("btnSaveKnobs", () => saveKnobs());
  bind("btnReadiness", () =>
    loadReadiness({ force: true }).then(() => log("readiness refreshed")),
  );
  bind("btnAccounts", () => loadAccounts({ force: true }));
  bind("btnDigest", () =>
    act("digest", async () => {
      setOpsOut("Running digest…");
      const t = await getInvoke()("run_digest_cmd", { account: selectedAccount() });
      setOpsOut(t);
      return short(t);
    }),
  );
  bind("btnDigestSend", () =>
    act("digest send", async () => {
      setOpsOut("Sending digest…");
      const t = await getInvoke()("run_digest_cmd", {
        account: selectedAccount(),
        send: true,
      });
      setOpsOut(t);
      return short(t);
    }),
  );
  bind("btnExport", () =>
    act("export", async () => {
      setOpsOut("Exporting…");
      const t = await getInvoke()("run_export_cmd", {
        account: selectedAccount(),
        year: new Date().getFullYear(),
      });
      setOpsOut(t);
      return t;
    }),
  );
  bind("btnSync", () =>
    act("sync", async () => {
      setOpsOut("Syncing live positions…");
      const t = await getInvoke()("sync_positions_cmd", { account: "live" });
      setOpsOut(t);
      return short(t);
    }),
  );
  bind("btnHistList", () =>
    act("history", async () => {
      if ($("histOut")) $("histOut").textContent = "Loading…";
      const t = await getInvoke()("list_history_cmd");
      if ($("histOut")) $("histOut").textContent = t || "(empty)";
      return "history listed";
    }),
  );
  bind("btnHistRecord", () =>
    act("record", async () => {
      if ($("histOut")) $("histOut").textContent = "Recording…";
      const t = await getInvoke()("record_history_cmd", { source: "mock" });
      if ($("histOut")) $("histOut").textContent = t;
      return t;
    }),
  );
  bind("btnSuggest", () => loadSuggest());
  bind("btnClearBook", () => {
    if (
      !confirm(
        "Delete this account's SQLite book (positions + fills)? Bot should be stopped first.",
      )
    ) {
      return;
    }
    act("clear book", () =>
      getInvoke()("clear_book_cmd", { account: selectedAccount() }),
    );
  });

  document.querySelectorAll("[data-cfg]").forEach((btn) => {
    btn.onclick = () => {
      if ($("configPath")) $("configPath").value = btn.dataset.cfg;
      log(`config → ${btn.dataset.cfg}`);
    };
  });
}

// Boot
wireUi();
log("Home → US · Crypto Up/Down · Exchange · (Alpaca stretch)");
renderModuleCards(FALLBACK_MODULES);
switchView("home");
setInterval(() => {
  if (activeView === "us") refresh();
  if (activeView === "home") loadHub();
  if (activeView === "crypto") refreshCryptoMonitor({ quietBook: true });
  if (activeView === "exchange") refreshExchangeMonitor({ quietBook: true });
}, 4000);
