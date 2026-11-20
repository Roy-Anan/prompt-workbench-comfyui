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

function renderPanel(container) {
  container.style.padding = "12px";
  container.style.display = "flex";
  container.style.flexDirection = "column";
  container.style.gap = "10px";

  const title = el("h3", { text: "Prompt 工作台", style: "margin:0;font-size:15px;" });
  const status = el("div", { class: "wb-status", text: "正在连接后端…" });
  status.style.fontSize = "13px";

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

  container.append(title, status, urlRow, actions, note);

  async function refresh() {
    status.textContent = "正在连接后端…";
    status.style.color = "";
    try {
      const data = await probe(baseUrl());
      status.textContent = `已连接：${data.app || "工作台"} v${data.version || "?"}`;
      status.style.color = "#4caf50";
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
