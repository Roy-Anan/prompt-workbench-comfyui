/**
 * Workbench sidebar panel (read-only shell).
 *
 * Registers one sidebar tab in the ComfyUI UI. It probes the local workbench
 * backend over loopback and links back to the full workbench web app. Draft,
 * asset, LoRA and rule management stay in the workbench frontend; this panel
 * is intentionally a thin shell until a read-only browser session is wired up.
 */

import { app } from "/scripts/app.js";

const DEFAULT_BASE_URL = "http://127.0.0.1:17890";
const STORAGE_KEY = "workbench.apiUrl";

function baseUrl() {
  return (localStorage.getItem(STORAGE_KEY) || DEFAULT_BASE_URL).replace(/\/+$/, "");
}

function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value);
  }
  for (const child of children) {
    if (child) node.appendChild(child);
  }
  return node;
}

async function probe(base) {
  const resp = await fetch(`${base}/api/health`, {
    method: "GET",
    headers: { Accept: "application/json" },
    cache: "no-store",
  });
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  return resp.json();
}

async function fetchOverview(base) {
  const resp = await fetch(`${base}/api/overview`, {
    method: "GET",
    headers: { Accept: "application/json" },
    cache: "no-store",
  });
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  return resp.json();
}

const COUNT_LABELS = [
  ["rules", "规则"],
  ["workflows", "工作流"],
  ["drafts", "草稿"],
  ["assets", "素材"],
  ["history", "历史记录"],
];

function renderCounts(container, counts) {
  container.innerHTML = "";
  const grid = el("div", {
    style: "display:grid;grid-template-columns:repeat(3,1fr);gap:8px;",
  });
  for (const [key, label] of COUNT_LABELS) {
    const value = Number(counts && counts[key]);
    const cell = el("div", {
      style: "border:1px solid rgba(128,128,128,0.25);border-radius:8px;padding:8px;text-align:center;",
    });
    cell.append(
      el("div", { text: Number.isFinite(value) ? String(value) : "—", style: "font-size:16px;font-weight:600;" }),
      el("div", { text: label, style: "font-size:11px;opacity:0.7;margin-top:2px;" }),
    );
    grid.appendChild(cell);
  }
  container.appendChild(grid);
}

function renderPanel(container) {
  container.style.padding = "12px";
  container.style.display = "flex";
  container.style.flexDirection = "column";
  container.style.gap = "10px";

  const title = el("h3", { text: "Prompt 工作台", style: "margin:0;font-size:15px;" });
  const status = el("div", { class: "wb-status", text: "正在连接后端…" });
  status.style.fontSize = "13px";

  const countsBox = el("div", { style: "display:none;" });

  const urlRow = el("div", { style: "display:flex;gap:6px;align-items:center;" });
  const urlInput = el("input", {
    type: "text",
    value: baseUrl(),
    style: "flex:1;min-width:0;",
    placeholder: DEFAULT_BASE_URL,
  });
  const saveUrl = el("button", { text: "保存", onclick: () => {
    localStorage.setItem(STORAGE_KEY, urlInput.value.trim() || DEFAULT_BASE_URL);
    refresh();
  } });
  urlRow.append(urlInput, saveUrl);

  const actions = el("div", { style: "display:flex;gap:6px;" });
  const refreshBtn = el("button", { text: "刷新", onclick: refresh });
  const openBtn = el("button", {
    text: "打开工作台",
    onclick: () => window.open(baseUrl(), "_blank"),
  });
  actions.append(refreshBtn, openBtn);

  const note = el("div", {
    text: "草稿、素材、LoRA 资料与规则编辑在工作台页面内完成；本页签为只读入口。",
    style: "font-size:12px;opacity:0.7;line-height:1.5;",
  });

  container.append(title, status, countsBox, urlRow, actions, note);

  async function refresh() {
    status.textContent = "正在连接后端…";
    status.style.color = "";
    countsBox.style.display = "none";
    try {
      const data = await probe(baseUrl());
      status.textContent = `已连接：${data.app || "工作台"} v${data.version || "?"}`;
      status.style.color = "#4caf50";
      try {
        const overview = await fetchOverview(baseUrl());
        if (overview && overview.counts) {
          renderCounts(countsBox, overview.counts);
          countsBox.style.display = "";
        }
      } catch (overviewErr) {
        // Counts are optional; keep the connection status even if overview fails.
        console.warn("[workbench] overview unavailable:", overviewErr);
      }
    } catch (err) {
      status.textContent = `后端不可达：${err.message || err}`;
      status.style.color = "#e57373";
    }
  }

  refresh();
  return () => {};
}

function register() {
  if (!app.extensionManager || typeof app.extensionManager.registerSidebarTab !== "function") {
    console.warn("[workbench] sidebar tab API unavailable; panel skipped.");
    return;
  }
  app.extensionManager.registerSidebarTab({
    id: "workbenchSidebar",
    icon: "pi pi-pencil",
    title: "工作台",
    tooltip: "Prompt 工作台只读入口",
    type: "custom",
    render: (el) => renderPanel(el),
  });
}

app.registerExtension({
  name: "WorkbenchSidebar",
  setup() {
    register();
  },
});
