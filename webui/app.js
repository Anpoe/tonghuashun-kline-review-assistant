const state = {
  records: [],
  range: "all",
  outcome: "all",
  search: "",
  view: "overview",
  generatedAt: "",
};

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const fmt = new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false });

function profitClass(value) {
  if (value > 0) return "positive";
  if (value < 0) return "negative";
  return "flat";
}

function profitText(value) {
  return Number.isFinite(value) ? `${value > 0 ? "+" : ""}${value.toFixed(2)}%` : "未识别";
}

function rangeText(record) {
  if (!record.rangeStart || !record.rangeEnd) return "未识别";
  const short = (value) => `${value.slice(0, 4)}.${value.slice(4, 6)}.${value.slice(6, 8)}`;
  return `${short(record.rangeStart)} - ${short(record.rangeEnd)}`;
}

function dateText(value) {
  return fmt.format(new Date(value)).replaceAll("/", "-");
}

function filteredRecords(includeOutcome = true) {
  const now = Date.now();
  const days = state.range === "all" ? Infinity : Number(state.range);
  return state.records.filter((record) => {
    const inRange = days === Infinity || now - new Date(record.recordedAt).getTime() <= days * 86400000;
    const needle = state.search.trim().toLowerCase();
    const matchesSearch = !needle || record.stock.toLowerCase().includes(needle) || record.code.includes(needle);
    const result = profitClass(record.profit);
    const matchesOutcome = !includeOutcome || state.outcome === "all" || result === state.outcome;
    return inRange && matchesSearch && matchesOutcome;
  });
}

function summary(records) {
  const valid = records.filter((item) => Number.isFinite(item.profit));
  const profits = valid.map((item) => item.profit);
  const positive = profits.filter((value) => value > 0).length;
  const negative = profits.filter((value) => value < 0).length;
  const flat = profits.length - positive - negative;
  const best = valid.reduce((winner, item) => !winner || item.profit > winner.profit ? item : winner, null);
  return {
    total: records.length,
    average: profits.length ? profits.reduce((sum, value) => sum + value, 0) / profits.length : 0,
    winRate: profits.length ? positive * 100 / profits.length : 0,
    positive,
    negative,
    flat,
    best,
  };
}

function setProfitElement(element, value) {
  element.textContent = profitText(value);
  element.classList.remove("profit-positive", "profit-negative");
  if (value > 0) element.classList.add("profit-positive");
  if (value < 0) element.classList.add("profit-negative");
}

function renderMetrics(records) {
  const stats = summary(records);
  $("#metricTotal").textContent = stats.total;
  $("#metricPeriod").textContent = state.range === "all" ? "全部记录" : `最近 ${state.range} 天`;
  setProfitElement($("#metricAverage"), stats.average);
  $("#metricWinRate").textContent = `${stats.winRate.toFixed(1)}%`;
  $("#metricWins").textContent = `${stats.positive} 胜 / ${stats.negative} 负`;
  setProfitElement($("#metricBest"), stats.best?.profit ?? 0);
  $("#metricBestStock").textContent = stats.best ? `${stats.best.stock} ${stats.best.code}`.trim() : "暂无记录";

  $("#donutRate").textContent = `${stats.winRate.toFixed(0)}%`;
  $("#positiveCount").textContent = stats.positive;
  $("#flatCount").textContent = stats.flat;
  $("#negativeCount").textContent = stats.negative;
  const total = stats.positive + stats.flat + stats.negative || 1;
  const positiveDeg = stats.positive / total * 360;
  const flatDeg = stats.flat / total * 360;
  $("#outcomeChart").style.background = `conic-gradient(var(--green) 0 ${positiveDeg}deg, var(--yellow) ${positiveDeg}deg ${positiveDeg + flatDeg}deg, var(--red) ${positiveDeg + flatDeg}deg 360deg)`;
}

function svgElement(name, attributes = {}) {
  const element = document.createElementNS("http://www.w3.org/2000/svg", name);
  Object.entries(attributes).forEach(([key, value]) => element.setAttribute(key, value));
  return element;
}

function renderTrend(records) {
  const host = $("#trendChart");
  host.replaceChildren();
  const values = [...records].reverse().filter((item) => Number.isFinite(item.profit));
  if (!values.length) {
    host.innerHTML = '<div class="chart-empty">暂无可绘制的收益记录</div>';
    return;
  }

  const width = 900;
  const height = 280;
  const pad = { left: 42, right: 18, top: 16, bottom: 28 };
  const cumulative = [];
  values.reduce((sum, item) => { cumulative.push(sum + item.profit); return sum + item.profit; }, 0);
  const allValues = [...values.map((item) => item.profit), ...cumulative, 0];
  const min = Math.min(...allValues);
  const max = Math.max(...allValues);
  const span = max - min || 1;
  const innerWidth = width - pad.left - pad.right;
  const innerHeight = height - pad.top - pad.bottom;
  const x = (index) => pad.left + (index + .5) * innerWidth / values.length;
  const y = (value) => pad.top + (max - value) * innerHeight / span;
  const barWidth = Math.max(2, Math.min(18, innerWidth / values.length * .58));
  const svg = svgElement("svg", { viewBox: `0 0 ${width} ${height}`, preserveAspectRatio: "none" });

  for (let index = 0; index <= 4; index += 1) {
    const value = max - span * index / 4;
    const lineY = y(value);
    svg.append(svgElement("line", { x1: pad.left, x2: width - pad.right, y1: lineY, y2: lineY, stroke: "var(--grid)", "stroke-width": 1 }));
    const label = svgElement("text", { x: pad.left - 8, y: lineY + 4, fill: "var(--muted)", "font-size": 10, "text-anchor": "end" });
    label.textContent = `${value.toFixed(1)}%`;
    svg.append(label);
  }

  const zeroY = y(0);
  svg.append(svgElement("line", { x1: pad.left, x2: width - pad.right, y1: zeroY, y2: zeroY, stroke: "#59606c", "stroke-width": 1 }));
  values.forEach((record, index) => {
    const barY = record.profit >= 0 ? y(record.profit) : zeroY;
    const rect = svgElement("rect", {
      x: x(index) - barWidth / 2,
      y: barY,
      width: barWidth,
      height: Math.max(1, Math.abs(y(record.profit) - zeroY)),
      rx: 2,
      fill: record.profit >= 0 ? "var(--green)" : "var(--red)",
      opacity: .72,
      "data-index": index,
    });
    svg.append(rect);
  });

  const points = cumulative.map((value, index) => `${x(index)},${y(value)}`).join(" ");
  svg.append(svgElement("polyline", { points, fill: "none", stroke: "var(--blue)", "stroke-width": 2.5, "vector-effect": "non-scaling-stroke" }));
  cumulative.forEach((value, index) => {
    svg.append(svgElement("circle", { cx: x(index), cy: y(value), r: 3.5, fill: "var(--surface)", stroke: "var(--blue)", "stroke-width": 2, "data-index": index }));
  });
  host.append(svg);

  const tooltip = document.createElement("div");
  tooltip.className = "chart-tooltip";
  host.append(tooltip);
  svg.addEventListener("pointermove", (event) => {
    const bounds = svg.getBoundingClientRect();
    const relativeX = (event.clientX - bounds.left) / bounds.width * width;
    const index = Math.max(0, Math.min(values.length - 1, Math.floor((relativeX - pad.left) / innerWidth * values.length)));
    const record = values[index];
    tooltip.innerHTML = `<strong>${record.stock} ${profitText(record.profit)}</strong><span>累计 ${profitText(cumulative[index])}</span>`;
    tooltip.style.left = `${event.clientX - host.getBoundingClientRect().left}px`;
    tooltip.style.top = `${event.clientY - host.getBoundingClientRect().top}px`;
    tooltip.style.opacity = "1";
  });
  svg.addEventListener("pointerleave", () => { tooltip.style.opacity = "0"; });
}

function recordRow(record, recent = false) {
  const resultClass = profitClass(record.profit);
  if (recent) {
    const button = document.createElement("button");
    button.className = "recent-row";
    button.innerHTML = `
      <span class="stock-cell"><strong>${escapeHtml(record.stock)}</strong><span>${escapeHtml(record.code || "无代码")}</span></span>
      <span class="date-cell"><strong>${dateText(record.recordedAt)}</strong><span>${rangeText(record)}</span></span>
      <span class="profit ${resultClass}">${profitText(record.profit)}</span>
      <span class="row-arrow">›</span>`;
    button.addEventListener("click", () => openRecord(record));
    return button;
  }

  const row = document.createElement("tr");
  row.innerHTML = `
    <td>${dateText(record.recordedAt)}</td>
    <td><span class="stock-cell"><strong>${escapeHtml(record.stock)}</strong><span>${escapeHtml(record.code || "无代码")}</span></span></td>
    <td>${rangeText(record)}</td>
    <td class="numeric profit ${resultClass}">${profitText(record.profit)}</td>
    <td class="view-link">查看</td>`;
  row.addEventListener("click", () => openRecord(record));
  return row;
}

function escapeHtml(value) {
  const node = document.createElement("span");
  node.textContent = value;
  return node.innerHTML;
}

function renderRecords(records) {
  const recentHost = $("#recentRecords");
  recentHost.replaceChildren(...records.slice(0, 5).map((record) => recordRow(record, true)));
  if (!records.length) recentHost.innerHTML = '<div class="empty-state"><strong>还没有训练记录</strong><span>完成一局后会自动显示在这里</span></div>';

  const history = filteredRecords(true);
  const table = $("#historyTable");
  table.replaceChildren(...history.map((record) => recordRow(record)));
  $("#historyCount").textContent = `共 ${history.length} 条`;
  $("#emptyState").hidden = history.length !== 0;
}

function render() {
  const overviewRecords = filteredRecords(false);
  renderMetrics(overviewRecords);
  renderTrend(overviewRecords);
  renderRecords(overviewRecords);
}

function openRecord(record) {
  $("#detailCode").textContent = record.code || "未识别代码";
  $("#detailTitle").textContent = record.stock;
  $("#detailMeta").textContent = `记录于 ${dateText(record.recordedAt)} · ${record.noteName}`;
  $("#detailProfit").textContent = profitText(record.profit);
  $("#detailProfit").className = `profit ${profitClass(record.profit)}`;
  $("#detailRange").textContent = rangeText(record);
  $("#detailChart").src = record.chartImage;
  $("#detailChart").hidden = !record.chartImage;
  $("#detailResult").src = record.resultImage;
  $("#resultSection").hidden = !record.resultImage;
  $("#recordDialog").showModal();
}

function switchView(target) {
  state.view = target;
  $$(".nav-item").forEach((button) => button.classList.toggle("active", button.dataset.target === target));
  $$(".view").forEach((view) => view.classList.toggle("active", view.id === `${target}View`));
  $("#pageTitle").textContent = target === "overview" ? "表现概览" : "历史记录";
}

let toastTimer;
function toast(message) {
  const element = $("#toast");
  element.textContent = message;
  element.classList.add("visible");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => element.classList.remove("visible"), 1800);
}

async function loadData(showToast = false) {
  const button = $("#refreshButton");
  button.disabled = true;
  try {
    const response = await fetch("/api/dashboard", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = await response.json();
    if (payload.generatedAt !== state.generatedAt) {
      state.generatedAt = payload.generatedAt;
      state.records = payload.records;
      render();
    }
    $("#syncStatus").textContent = `${payload.summary.total} 条记录 · 刚刚更新`;
    if (showToast) toast("数据已刷新");
  } catch (error) {
    $("#syncStatus").textContent = "读取失败";
    toast(`读取失败：${error.message}`);
  } finally {
    button.disabled = false;
  }
}

$$('[data-target]').forEach((button) => button.addEventListener("click", () => switchView(button.dataset.target)));
$$('[data-range]').forEach((button) => button.addEventListener("click", () => {
  state.range = button.dataset.range;
  $$('[data-range]').forEach((item) => item.classList.toggle("active", item === button));
  render();
}));
$("#searchInput").addEventListener("input", (event) => { state.search = event.target.value; render(); });
$("#outcomeFilter").addEventListener("change", (event) => { state.outcome = event.target.value; render(); });
$("#refreshButton").addEventListener("click", () => loadData(true));
$("#closeDialog").addEventListener("click", () => $("#recordDialog").close());
$("#recordDialog").addEventListener("click", (event) => { if (event.target === event.currentTarget) event.currentTarget.close(); });

loadData();
setInterval(() => loadData(false), 8000);
