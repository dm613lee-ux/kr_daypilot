const state = {
  payload: null,
  recommendations: [],
  coreRecommendations: [],
  activeEngine: "tactical",
  filtered: [],
  selectedTicker: "",
  tickerDetail: null,
  jobPoll: null,
};

const techniqueNames = ["fundamental", "value_momentum", "risk", "event_flow", "value", "quality", "momentum", "defensive", "flow", "liquidity"];
const techniqueDescriptions = {
  "Defensive Trend Compounder": "방어성과 추세를 함께 보는 기법입니다. 변동성이 과도하지 않고 가격 추세가 유지되며, 품질/유동성이 일정 수준 이상인 후보를 우선 검토합니다.",
  "Event-Safe Recovery": "공시나 이벤트 리스크가 크게 차단되지 않은 회복 후보를 찾는 기법입니다. 가치 매력, 품질, 회복 모멘텀이 같이 살아 있는지를 봅니다.",
  "Flow-Backed Re-Rating": "외국인/기관 등 수급 보조 신호와 가격 재평가 가능성을 함께 보는 기법입니다. 수급이 가격 회복을 뒷받침하는지 확인합니다.",
  "Quality Value Momentum": "가치, 재무 품질, 모멘텀을 균형 있게 보는 다요인 랭킹 기법입니다. 싸지만 약한 종목이나 강하지만 비싼 종목을 걸러냅니다.",
  "Fundamental Core Composite": "다요인 펀더멘털을 중심축으로 고정한 코어 엔진입니다. 가치·품질·유동성을 먼저 보고, 가치+모멘텀은 순위 보강, 저변동성+추세는 리스크 필터, 공시·수급은 확인 신호로 사용합니다.",
};

document.addEventListener("DOMContentLoaded", () => {
  bindEvents();
  loadDashboard();
});

function bindEvents() {
  document.getElementById("refreshViewBtn").addEventListener("click", loadDashboard);
  document.getElementById("runPipelineBtn").addEventListener("click", startPipeline);
  document.getElementById("searchInput").addEventListener("input", applyFilters);
  document.getElementById("techniqueFilter").addEventListener("change", applyFilters);
  document.getElementById("stateFilter").addEventListener("change", applyFilters);
  document.getElementById("decisionFilter").addEventListener("change", applyFilters);
  document.getElementById("tacticalEngineBtn").addEventListener("click", () => setActiveEngine("tactical"));
  document.getElementById("coreEngineBtn").addEventListener("click", () => setActiveEngine("core"));
  document.getElementById("watchDecisionBtn").addEventListener("click", () => saveDecision("watch"));
  document.getElementById("excludeDecisionBtn").addEventListener("click", () => saveDecision("exclude"));
  document.getElementById("memoDecisionBtn").addEventListener("click", () => saveDecision("memo"));
  document.getElementById("clearDecisionBtn").addEventListener("click", () => saveDecision("clear"));
  document.getElementById("addPaperBtn").addEventListener("click", addPaperPosition);
  window.addEventListener("resize", () => drawPriceChart((state.tickerDetail && state.tickerDetail.history) || []));
}

async function loadDashboard() {
  const payload = await fetchJson("/api/dashboard");
  state.payload = payload;
  state.recommendations = Array.isArray(payload.recommendations) ? payload.recommendations : [];
  state.coreRecommendations = Array.isArray(payload.core_recommendations) ? payload.core_recommendations : [];
  const currentList = activeRecommendations();
  if (!state.selectedTicker && currentList.length) {
    state.selectedTicker = currentList[0].ticker;
  }
  renderDashboard(payload);
  applyFilters();
  renderJob(payload.job || {});
}

function activeRecommendations() {
  return state.activeEngine === "core" ? state.coreRecommendations : state.recommendations;
}

function activeTechniqueBreakdown() {
  const payload = state.payload || {};
  return state.activeEngine === "core" ? (payload.core_technique_breakdown || {}) : (payload.technique_breakdown || {});
}

function activeSummary() {
  const payload = state.payload || {};
  return state.activeEngine === "core" ? (payload.core_summary || {}) : (payload.summary || {});
}

function setActiveEngine(engine) {
  state.activeEngine = engine === "core" ? "core" : "tactical";
  state.selectedTicker = "";
  renderEngineTabs();
  renderTechniqueFilter(activeTechniqueBreakdown());
  applyFilters();
}

async function fetchJson(url, options = {}) {
  const response = await fetch(url, { cache: "no-store", ...options });
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.error || `request_failed_${response.status}`);
  }
  return payload;
}

function renderDashboard(payload) {
  const summary = activeSummary();
  const freshness = summary.data_freshness || {};
  const paper = payload.paper_portfolio_summary || {};
  const list = activeRecommendations();
  const breakdown = activeTechniqueBreakdown();
  const generated = payload.generated_at || "";
  document.getElementById("generatedAt").textContent = generated ? `updated ${formatDateTime(generated)}` : "updated -";

  const stateValue = document.getElementById("stateValue");
  stateValue.textContent = summary.state || "-";
  stateValue.className = summary.state || "";
  document.getElementById("freshnessValue").textContent = freshness.price_is_stale
    ? `stale ${freshness.price_age_calendar_days ?? "-"}d`
    : `fresh ${freshness.price_age_calendar_days ?? "-"}d`;
  document.getElementById("recommendedValue").textContent = summary.recommended ?? "-";
  document.getElementById("paperReviewValue").textContent = `paper review ${summary.paper_review ?? "-"}`;
  document.getElementById("signalDayValue").textContent = formatDay(summary.signal_day);
  document.getElementById("runDateValue").textContent = `run ${formatDay(freshness.run_date)}`;
  document.getElementById("paperPositionsValue").textContent = paper.open_positions ?? 0;
  document.getElementById("paperPnlValue").textContent = `평균 ${formatPct(paper.avg_pnl_pct)}`;
  document.getElementById("blockedValue").textContent = summary.blocked ?? "-";
  document.getElementById("inputRowsValue").textContent = `universe ${summary.input_rows ?? "-"}`;
  document.getElementById("listSubtitle").textContent = `${engineLabel(state.activeEngine)} · ${list.length} candidates / ${Object.keys(breakdown).length} techniques`;

  const reportLink = document.getElementById("latestReportLink");
  reportLink.href = state.activeEngine === "core"
    ? ((payload.files && payload.files.core_report_html) || "/report/core.html")
    : ((payload.files && payload.files.latest_report_html) || "/report/latest.html");

  renderEngineTabs();
  renderTechniqueFilter(breakdown);
  renderConsensus(payload.engine_comparison || {});
  renderLedger(payload.paper_ledger || [], payload.paper_portfolio_summary || {});
  renderPipeline(payload.pipeline || {});
  renderDataFiles(payload.data_files || {});
}

function engineLabel(engine) {
  return engine === "core" ? "Fundamental Core" : "기존 엔진";
}

function renderEngineTabs() {
  document.querySelectorAll(".engine-tab").forEach((button) => {
    button.classList.toggle("active", button.dataset.engine === state.activeEngine);
  });
}

function renderTechniqueFilter(breakdown) {
  const select = document.getElementById("techniqueFilter");
  const current = select.value;
  select.innerHTML = '<option value="">전체 기법</option>';
  Object.keys(breakdown).forEach((technique) => {
    const option = document.createElement("option");
    option.value = technique;
    option.textContent = `${technique} (${breakdown[technique]})`;
    option.title = techniqueDescription(technique);
    select.appendChild(option);
  });
  select.value = current;
}

function applyFilters() {
  const search = document.getElementById("searchInput").value.trim().toLowerCase();
  const technique = document.getElementById("techniqueFilter").value;
  const filterState = document.getElementById("stateFilter").value;
  const decision = document.getElementById("decisionFilter").value;
  const source = activeRecommendations();
  state.filtered = source.filter((item) => {
    const haystack = `${item.ticker || ""} ${item.company || ""} ${item.technique || ""}`.toLowerCase();
    return (!search || haystack.includes(search)) &&
      (!technique || item.technique === technique) &&
      (!filterState || item.state === filterState) &&
      (!decision || item.user_status === decision);
  });
  renderRecommendationRows();
  const selected = state.filtered.find((item) => item.ticker === state.selectedTicker) || state.filtered[0] || source[0];
  renderDetail(selected);
}

function renderRecommendationRows() {
  const tbody = document.getElementById("recommendationRows");
  tbody.innerHTML = "";
  if (!state.filtered.length) {
    const row = document.createElement("tr");
    row.innerHTML = '<td colspan="6">표시할 후보가 없습니다.</td>';
    tbody.appendChild(row);
    return;
  }
  state.filtered.forEach((item) => {
    const row = document.createElement("tr");
    if (item.ticker === state.selectedTicker) row.classList.add("selected");
    if (item.user_status === "exclude") row.classList.add("excluded");
    const hasPaper = item.paper_position && item.paper_position.status === "open";
    row.innerHTML = `
      <td>${escapeHtml(item.rank ?? "")}</td>
      <td><span class="ticker"><strong>${escapeHtml(item.company || "-")}</strong><span>${escapeHtml(item.ticker || "")} · ${escapeHtml(item.market || "")}</span></span></td>
      <td>${techniqueBadge(item.technique)}</td>
      <td class="score-cell">${formatNumber(item.final_score)}</td>
      <td><span class="decision-chip ${escapeHtml(item.user_status || "")}">${escapeHtml(decisionLabel(item.user_status))}</span>${hasPaper ? '<span class="paper-dot">Paper</span>' : ""}</td>
      <td><span class="state-chip ${escapeHtml(item.state || "")}">${escapeHtml(item.state || "-")}</span></td>
    `;
    row.addEventListener("click", () => {
      state.selectedTicker = item.ticker;
      renderRecommendationRows();
      renderDetail(item);
    });
    tbody.appendChild(row);
  });
}

function renderDetail(item) {
  if (!item) {
    document.getElementById("detailTitle").textContent = "선택한 후보";
    document.getElementById("detailMeta").textContent = "목록에서 선택";
    document.getElementById("decisionNote").value = "";
    document.getElementById("decisionStatus").textContent = "-";
    drawPriceChart([]);
    return;
  }
  state.selectedTicker = item.ticker;
  document.getElementById("detailTitle").textContent = item.company || item.ticker || "-";
  document.getElementById("detailMeta").textContent = `${item.ticker || ""} · ${item.market || ""} · ${formatDay(item.source_bas_dt)}`;
  document.getElementById("detailState").textContent = item.state || "-";
  document.getElementById("detailState").className = `state-chip ${item.state || ""}`;
  document.getElementById("detailScore").textContent = formatNumber(item.final_score);
  document.getElementById("detailTechnique").innerHTML = techniqueBadge(item.technique);
  document.getElementById("detailEvidence").textContent = item.evidence_summary || "-";
  document.getElementById("detailPlan").textContent = item.paper_plan || "-";
  document.getElementById("detailRisk").textContent = item.block_reason || item.disclosure_titles || "clear";
  document.getElementById("decisionNote").value = item.user_note || "";
  document.getElementById("decisionStatus").textContent = decisionLabel(item.user_status);
  setScoreArc(Number(item.final_score || 0));
  renderComponentBars(item.score_components || "");
  loadTickerDetail(item.ticker);
}

async function loadTickerDetail(ticker) {
  document.getElementById("chartSummary").textContent = "loading";
  try {
    const payload = await fetchJson(`/api/ticker?ticker=${encodeURIComponent(ticker)}`);
    if (ticker !== state.selectedTicker) return;
    state.tickerDetail = payload;
    const decision = payload.decision || {};
    document.getElementById("decisionNote").value = decision.note || document.getElementById("decisionNote").value;
    document.getElementById("decisionStatus").textContent = decisionLabel(decision.status);
    renderChartSummary(payload.history_summary || {});
    drawPriceChart(Array.isArray(payload.history) ? payload.history : []);
  } catch (error) {
    document.getElementById("chartSummary").textContent = String(error.message || error);
    drawPriceChart([]);
  }
}

async function saveDecision(status) {
  if (!state.selectedTicker) return;
  const note = status === "clear" ? "" : document.getElementById("decisionNote").value;
  const buttonMap = {
    watch: "watchDecisionBtn",
    exclude: "excludeDecisionBtn",
    memo: "memoDecisionBtn",
    clear: "clearDecisionBtn",
  };
  const button = document.getElementById(buttonMap[status]);
  button.disabled = true;
  try {
    await fetchJson("/api/decision/save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ticker: state.selectedTicker, status, note }),
    });
    await loadDashboard();
  } catch (error) {
    document.getElementById("decisionStatus").textContent = String(error.message || error);
  } finally {
    button.disabled = false;
  }
}

async function addPaperPosition() {
  if (!state.selectedTicker) return;
  const button = document.getElementById("addPaperBtn");
  button.disabled = true;
  try {
    await fetchJson("/api/ledger/add", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ticker: state.selectedTicker, quantity: 1, note: document.getElementById("decisionNote").value }),
    });
    await loadDashboard();
    document.getElementById("ledger").scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) {
    document.getElementById("decisionStatus").textContent = String(error.message || error);
  } finally {
    button.disabled = false;
  }
}

async function removePaperPosition(entryId, ticker, company) {
  const label = company || ticker || "선택한 포지션";
  if (!window.confirm(`${label} paper ledger 항목을 제거할까요?`)) return;
  try {
    await fetchJson("/api/ledger/remove", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ entry_id: entryId, ticker }),
    });
    await loadDashboard();
  } catch (error) {
    document.getElementById("ledgerSubtitle").textContent = String(error.message || error);
  }
}

function setScoreArc(score) {
  const circle = document.getElementById("scoreArc");
  const circumference = 301.59;
  const offset = circumference - (Math.max(0, Math.min(score, 100)) / 100) * circumference;
  circle.style.strokeDashoffset = offset.toString();
}

function renderComponentBars(text) {
  const root = document.getElementById("componentBars");
  root.innerHTML = "";
  const scores = {};
  String(text).split(";").forEach((part) => {
    const [name, value] = part.split("=").map((item) => item && item.trim());
    if (name && value) scores[name] = Number(value);
  });
  const orderedNames = [
    ...techniqueNames.filter((name) => Object.prototype.hasOwnProperty.call(scores, name)),
    ...Object.keys(scores).filter((name) => !techniqueNames.includes(name)),
  ];
  orderedNames.forEach((name) => {
    const value = Number.isFinite(scores[name]) ? scores[name] : 0;
    const row = document.createElement("div");
    row.className = "bar-row";
    row.innerHTML = `
      <span>${name}</span>
      <span class="bar-track"><span class="bar-fill" style="width:${Math.max(0, Math.min(value, 100))}%"></span></span>
      <strong>${formatNumber(value)}</strong>
    `;
    root.appendChild(row);
  });
}

function renderChartSummary(summary) {
  const latest = summary.latest_close == null ? "-" : formatCurrency(summary.latest_close);
  document.getElementById("chartSummary").textContent = `${summary.points || 0}일 · 최근 ${latest} · ${formatPct(summary.return_pct)}`;
}

function drawPriceChart(history) {
  const canvas = document.getElementById("priceChart");
  const ctx = canvas.getContext("2d");
  const rect = canvas.getBoundingClientRect();
  const width = Math.max(320, rect.width);
  const height = 190;
  const ratio = window.devicePixelRatio || 1;
  canvas.width = width * ratio;
  canvas.height = height * ratio;
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = "#fbfcfe";
  ctx.fillRect(0, 0, width, height);

  const points = history
    .map((row) => ({ day: row.source_bas_dt, close: Number(row.close), ma20: Number(row.ma20) }))
    .filter((row) => Number.isFinite(row.close));
  if (points.length < 2) {
    ctx.fillStyle = "#667085";
    ctx.font = "12px Segoe UI, Arial";
    ctx.fillText("가격 히스토리가 없습니다.", 16, 32);
    return;
  }

  const pad = { left: 46, right: 16, top: 16, bottom: 30 };
  const values = points.flatMap((point) => Number.isFinite(point.ma20) ? [point.close, point.ma20] : [point.close]);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const x = (index) => pad.left + (index / (points.length - 1)) * (width - pad.left - pad.right);
  const y = (value) => pad.top + ((max - value) / span) * (height - pad.top - pad.bottom);

  ctx.strokeStyle = "#d8dde6";
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(pad.left, pad.top);
  ctx.lineTo(pad.left, height - pad.bottom);
  ctx.lineTo(width - pad.right, height - pad.bottom);
  ctx.stroke();

  ctx.fillStyle = "#667085";
  ctx.font = "11px Segoe UI, Arial";
  ctx.fillText(formatCurrency(max), 6, pad.top + 4);
  ctx.fillText(formatCurrency(min), 6, height - pad.bottom);
  ctx.fillText(formatDay(points[0].day), pad.left, height - 9);
  ctx.fillText(formatDay(points[points.length - 1].day), Math.max(pad.left + 80, width - 96), height - 9);

  drawLine(ctx, points, x, y, "close", "#2558d8", 2.4);
  drawLine(ctx, points, x, y, "ma20", "#057a55", 1.6);
}

function drawLine(ctx, points, x, y, key, color, width) {
  ctx.strokeStyle = color;
  ctx.lineWidth = width;
  ctx.beginPath();
  let started = false;
  points.forEach((point, index) => {
    const value = Number(point[key]);
    if (!Number.isFinite(value)) return;
    if (!started) {
      ctx.moveTo(x(index), y(value));
      started = true;
    } else {
      ctx.lineTo(x(index), y(value));
    }
  });
  ctx.stroke();
}

function renderConsensus(comparison) {
  const rows = Array.isArray(comparison.rows) ? comparison.rows : [];
  const counts = comparison.counts || {};
  const tbody = document.getElementById("consensusRows");
  if (!tbody) return;
  tbody.innerHTML = "";
  document.getElementById("consensusSubtitle").textContent =
    `Consensus ${counts.consensus || 0} / Core ${counts.core_pick || 0} / Tactical ${counts.tactical_watch || 0}`;
  if (!rows.length) {
    const row = document.createElement("tr");
    row.innerHTML = '<td colspan="6">비교할 엔진 결과가 없습니다.</td>';
    tbody.appendChild(row);
    return;
  }
  rows.slice(0, 12).forEach((item) => {
    const row = document.createElement("tr");
    row.innerHTML = `
      <td><span class="comparison-chip ${escapeHtml(item.category || "")}">${escapeHtml(comparisonLabel(item.category))}</span></td>
      <td><span class="ticker"><strong>${escapeHtml(item.company || item.ticker || "-")}</strong><span>${escapeHtml(item.ticker || "")} · ${escapeHtml(item.market || "")}</span></span></td>
      <td class="score-cell">${formatNumber(item.tactical_score)}</td>
      <td class="score-cell">${formatNumber(item.core_score)}</td>
      <td>${escapeHtml(item.tactical_technique || "-")}</td>
      <td>${escapeHtml(item.core_technique || "-")}</td>
    `;
    tbody.appendChild(row);
  });
}

function renderLedger(ledger, summary) {
  const tbody = document.getElementById("ledgerRows");
  tbody.innerHTML = "";
  document.getElementById("ledgerSubtitle").textContent =
    `${summary.open_positions || 0} open / 평균 ${formatPct(summary.avg_pnl_pct)} / 평가손익 ${formatCurrency(summary.total_pnl_krw)}`;
  if (!ledger.length) {
    const row = document.createElement("tr");
    row.innerHTML = '<td colspan="7">아직 paper 포지션이 없습니다.</td>';
    tbody.appendChild(row);
    return;
  }
  ledger.forEach((item) => {
    const row = document.createElement("tr");
    const pnl = Number(item.pnl_pct);
    if (Number.isFinite(pnl)) row.classList.add(pnl >= 0 ? "positive" : "negative");
    row.innerHTML = `
      <td><span class="ticker"><strong>${escapeHtml(item.company || item.ticker || "-")}</strong><span>${escapeHtml(item.ticker || "")}</span></span></td>
      <td>${techniqueBadge(item.technique)}</td>
      <td>${formatDay(item.entry_date)}</td>
      <td>${formatCurrency(item.entry_price)}</td>
      <td>${formatCurrency(item.latest_close)}</td>
      <td class="score-cell">${formatPct(item.pnl_pct)}</td>
      <td><span class="ledger-status-cell"><span class="state-chip ${escapeHtml(item.status || "")}">${escapeHtml(item.status || "-")}</span><button class="row-action danger" type="button" data-entry-id="${escapeHtml(item.entry_id || "")}" data-ticker="${escapeHtml(item.ticker || "")}" data-company="${escapeHtml(item.company || "")}">제거</button></span></td>
    `;
    const removeButton = row.querySelector(".row-action.danger");
    removeButton.addEventListener("click", (event) => {
      event.stopPropagation();
      removePaperPosition(removeButton.dataset.entryId || "", removeButton.dataset.ticker || "", removeButton.dataset.company || "");
    });
    tbody.appendChild(row);
  });
}

function comparisonLabel(value) {
  if (value === "consensus") return "합의";
  if (value === "core_pick") return "Core";
  if (value === "tactical_watch") return "기존";
  return "-";
}

function renderPipeline(pipeline) {
  const steps = Array.isArray(pipeline.steps) ? pipeline.steps : [];
  const root = document.getElementById("pipelineSteps");
  root.innerHTML = "";
  if (!steps.length) {
    root.innerHTML = '<div class="step-item"><strong>pipeline</strong><span>아직 실행 기록이 없습니다.</span></div>';
    return;
  }
  steps.forEach((step) => {
    const item = document.createElement("div");
    item.className = "step-item";
    item.innerHTML = `<strong>${escapeHtml(step.name || "-")}</strong><span>return ${escapeHtml(step.returncode ?? "-")} · ${step.required ? "required" : "optional"}</span>`;
    root.appendChild(item);
  });
}

function renderDataFiles(files) {
  const labels = {
    price_history: "가격",
    investor_flows: "수급",
    disclosures: "공시",
    fundamentals: "재무",
    valuation: "밸류에이션",
  };
  const root = document.getElementById("dataFiles");
  root.innerHTML = "";
  Object.keys(labels).forEach((key) => {
    const file = files[key] || {};
    const item = document.createElement("div");
    item.className = "data-file";
    item.innerHTML = `<strong>${labels[key]}</strong><span>${file.rows ?? 0} rows · latest ${formatDay(file.latest_day)}</span><span>${escapeHtml(file.mtime || "")}</span>`;
    root.appendChild(item);
  });
}

async function startPipeline() {
  const button = document.getElementById("runPipelineBtn");
  button.disabled = true;
  const form = new FormData(document.getElementById("pipelineForm"));
  const body = Object.fromEntries(form.entries());
  try {
    const payload = await fetchJson("/api/pipeline/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    renderJob(payload.job || payload);
    startJobPolling();
  } catch (error) {
    document.getElementById("jobLog").textContent = String(error.message || error);
  } finally {
    setTimeout(() => { button.disabled = false; }, 1400);
  }
}

function startJobPolling() {
  if (state.jobPoll) clearInterval(state.jobPoll);
  state.jobPoll = setInterval(async () => {
    const job = await fetchJson("/api/job");
    renderJob(job);
    if (!job.running) {
      clearInterval(state.jobPoll);
      state.jobPoll = null;
      await loadDashboard();
    }
  }, 2200);
}

function renderJob(job) {
  const running = Boolean(job.running);
  document.getElementById("jobState").textContent = running
    ? `running since ${formatDateTime(job.started_at)}`
    : job.returncode === 0
      ? `completed ${formatDateTime(job.finished_at)}`
      : job.returncode
        ? `failed ${job.returncode}`
        : "대기 중";
  const lines = Array.isArray(job.lines) ? job.lines : [];
  document.getElementById("jobLog").textContent = lines.slice(-80).join("\n");
}

function techniqueDescription(technique) {
  return techniqueDescriptions[technique] || "투자기법 설명이 아직 등록되지 않았습니다. 점수 구성과 근거 문장을 함께 확인하세요.";
}

function techniqueBadge(technique) {
  const name = technique || "-";
  const description = techniqueDescription(name);
  return `<span class="technique-label" tabindex="0" title="${escapeHtml(description)}" data-tooltip="${escapeHtml(description)}">${escapeHtml(name)}</span>`;
}

function decisionLabel(value) {
  if (value === "watch") return "관심";
  if (value === "exclude") return "제외";
  if (value === "memo") return "메모";
  return "-";
}

function formatNumber(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "-";
  return number.toFixed(2).replace(/\.00$/, "");
}

function formatCurrency(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "-";
  return `${Math.round(number).toLocaleString("ko-KR")}원`;
}

function formatPct(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "-";
  return `${number >= 0 ? "+" : ""}${number.toFixed(2)}%`;
}

function formatDay(value) {
  const text = String(value || "");
  if (text.length !== 8) return text || "-";
  return `${text.slice(0, 4)}-${text.slice(4, 6)}-${text.slice(6, 8)}`;
}

function formatDateTime(value) {
  if (!value) return "-";
  return String(value).replace("T", " ").slice(0, 19);
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}
