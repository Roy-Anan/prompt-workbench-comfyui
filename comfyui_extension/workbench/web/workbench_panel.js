/**
 * Workbench sidebar panel (read-only).
 *
 * Registers one sidebar tab in the ComfyUI UI. The panel first tries the local
 * read-only proxy served by this extension on the ComfyUI origin
 * (``/workbench-api/*``); the Python side attaches the node token, so the
 * browser never holds a credential. If the proxy is unavailable it falls back
 * to the unauthenticated health/overview probes against the workbench backend.
 *
 * Draft, asset, LoRA and rule editing stay in the full workbench web app; this
 * panel is a read-only overview with a jump-back entry point.
 */

import { app } from "/scripts/app.js";

const DEFAULT_BASE_URL = "http://127.0.0.1:17890";
const STORAGE_KEY = "workbench.apiUrl";
const PROXY_BASE = "/workbench-api";

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

async function fetchJson(url) {
  const resp = await fetch(url, {
    method: "GET",
    headers: { Accept: "application/json" },
    cache: "no-store",
  });
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  return resp.json();
}

// --- Direct (fallback) probes against the workbench backend -----------------

async function probeDirect(base) {
  return fetchJson(`${base}/api/health`);
}

async function fetchOverviewDirect(base) {
  return fetchJson(`${base}/api/overview`);
}

// --- Proxy-backed reads on the ComfyUI origin ------------------------------

async function probeProxy() {
  return fetchJson(`${PROXY_BASE}/health`);
}

async function fetchSummaryProxy() {
  return fetchJson(`${PROXY_BASE}/sidebar-summary`);
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
      el("div", {
        text: Number.isFinite(value) ? String(value) : "—",
        style: "font-size:16px;font-weight:600;",
      }),
      el("div", { text: label, style: "font-size:11px;opacity:0.7;margin-top:2px;" }),
    );
    grid.appendChild(cell);
  }
  container.appendChild(grid);
}

function clip(text, limit = 40) {
  const value = String(text == null ? "" : text);
  return value.length <= limit ? value : value.slice(0, limit) + "…";
}

function detailLine(main, sub) {
  const row = el("div", { style: "line-height:1.4;" });
  row.appendChild(el("div", { text: clip(main), style: "font-size:12px;" }));
  if (sub) {
    row.appendChild(
      el("div", { text: clip(sub, 60), style: "font-size:11px;opacity:0.65;" }),
    );
  }
  return row;
}

const SECTION_META = [
  ["workflows", "工作流"],
  ["rules", "规则"],
  ["drafts", "草稿"],
  ["assets", "素材"],
  ["loras", "LoRA"],
  ["history", "历史"],
];

function renderSection(box, key, label, section) {
  const total = Number((section && section.total) || 0);
  const items = (section && section.items) || [];

  const head = el("div", {
    style: "display:flex;justify-content:space-between;align-items:center;margin-top:10px;",
  });
  head.append(
    el("strong", { text: label, style: "font-size:12px;" }),
    el("span", { text: `${total} 条`, style: "font-size:11px;opacity:0.65;" }),
  );
  box.appendChild(head);

  if (!items.length) {
    box.appendChild(
      el("div", { text: "暂无", style: "font-size:11px;opacity:0.55;margin-top:2px;" }),
    );
    return;
  }

  const list = el("div", { style: "display:flex;flex-direction:column;gap:5px;margin-top:4px;" });
  for (const item of items.slice(0, 5)) {
    let main = "";
    let sub = "";
    if (key === "workflows") {
      main = item.title || "";
      sub = `${item.id || ""}${item.confirmed ? " · 已确认" : ""}`;
    } else if (key === "rules") {
      main = `${item.rule_key || ""}`;
      sub = `${item.level || ""}${item.scope_key ? " · " + item.scope_key : ""}${item.value ? " · " + item.value : ""}`;
    } else if (key === "drafts") {
      main = item.name || "";
      sub = [item.workflow_title, (item.tags || []).join(", ")].filter(Boolean).join(" · ");
    } else if (key === "assets") {
      main = item.name || "";
      sub = [item.group_name, item.kind].filter(Boolean).join(" · ");
    } else if (key === "loras") {
      main = item.name || "";
    } else if (key === "history") {
      main = `${item.status || ""}${item.model ? " · " + item.model : ""}`;
      sub = item.prompt_id || "";
    }
    list.appendChild(detailLine(main, sub));
  }
  if (total > items.length) {
    list.appendChild(
      el("div", {
        text: `其余 ${total - items.length} 条见工作台`,
        style: "font-size:11px;opacity:0.55;",
      }),
    );
  }
  box.appendChild(list);
}

function renderSummary(box, summary) {
  box.innerHTML = "";
  const sections = (summary && summary.sections) || {};
  for (const [key, label] of SECTION_META) {
    renderSection(box, key, label, sections[key]);
  }
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
  const summaryBox = el("div", { style: "display:none;" });

  const urlRow = el("div", { style: "display:flex;gap:6px;align-items:center;" });
  const urlInput = el("input", {
    type: "text",
    value: baseUrl(),
    style: "flex:1;min-width:0;",
    placeholder: DEFAULT_BASE_URL,
  });
  const saveUrl = el("button", {
    text: "保存",
    onclick: () => {
      localStorage.setItem(STORAGE_KEY, urlInput.value.trim() || DEFAULT_BASE_URL);
      refresh();
    },
  });
  urlRow.append(urlInput, saveUrl);

  const actions = el("div", { style: "display:flex;gap:6px;" });
  const refreshBtn = el("button", { text: "刷新", onclick: refresh });
  const openBtn = el("button", {
    text: "打开工作台",
    onclick: () => window.open(baseUrl(), "_blank"),
  });
  actions.append(refreshBtn, openBtn);

  const note = el("div", {
    text: "草稿、素材、LoRA 资料与规则编辑在工作台页面内完成；本页签为只读概览。",
    style: "font-size:12px;opacity:0.7;line-height:1.5;",
  });

  container.append(title, status, countsBox, summaryBox, urlRow, actions, note);

  async function refresh() {
    status.textContent = "正在连接后端…";
    status.style.color = "";
    countsBox.style.display = "none";
    summaryBox.style.display = "none";

    // Prefer the extension proxy on the ComfyUI origin.
    try {
      await probeProxy();
      const summary = await fetchSummaryProxy();
      status.textContent = "已连接工作台（本地代理）";
      status.style.color = "#4caf50";
      const counts = {
        rules: (summary.sections && summary.sections.rules && summary.sections.rules.total) || 0,
        workflows: (summary.sections && summary.sections.workflows && summary.sections.workflows.total) || 0,
        drafts: (summary.sections && summary.sections.drafts && summary.sections.drafts.total) || 0,
        assets: (summary.sections && summary.sections.assets && summary.sections.assets.total) || 0,
        history: (summary.sections && summary.sections.history && summary.sections.history.total) || 0,
      };
      renderCounts(countsBox, counts);
      countsBox.style.display = "";
      renderSummary(summaryBox, summary);
      summaryBox.style.display = "";
      return;
    } catch (proxyErr) {
      console.warn("[workbench] proxy unavailable, falling back to direct probe:", proxyErr);
    }

    // Fallback: unauthenticated health/overview only (no detail list).
    try {
      const data = await probeDirect(baseUrl());
      status.textContent = `已连接：${data.app || "工作台"} v${data.version || "?"}`;
      status.style.color = "#4caf50";
      try {
        const overview = await fetchOverviewDirect(baseUrl());
        if (overview && overview.counts) {
          renderCounts(countsBox, overview.counts);
          countsBox.style.display = "";
        }
      } catch (overviewErr) {
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
    tooltip: "Prompt 工作台只读概览",
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

// --- Workflow picker: dynamic workflow options on workbench nodes ----------
//
// The Python nodes seed their ``workflow_id`` combo with an empty placeholder
// because instance-time Python cannot fetch the backend list. This side script
// reads the imported workflows through the same read-only proxy and fills the
// combo options once the canvas loads. The last chosen workflow is remembered
// locally so a freshly added node reuses it. Nodes never write back to the
// backend; draft/rule editing stays in the workbench web app.

const PICKER_NODE_TYPES = ["WorkbenchRuleContext", "WorkbenchPromptCandidate"];
const PICKER_STORAGE_KEY = "workbench.lastWorkflowId";

let cachedWorkflowItems = null;

async function fetchPickerWorkflows() {
  if (cachedWorkflowItems !== null) return cachedWorkflowItems;
  try {
    const resp = await fetch(`${PROXY_BASE}/workflows`, {
      headers: { Accept: "application/json" },
      cache: "no-store",
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();
    cachedWorkflowItems = Array.isArray(data.items) ? data.items : [];
  } catch (err) {
    console.warn("[workbench-picker] workflow list unavailable:", err);
    cachedWorkflowItems = [];
  }
  return cachedWorkflowItems;
}

function workflowOptionLabel(item) {
  const id = item.id || "";
  const short = String(id).slice(0, 8);
  const name = item.filename || short;
  const confirmed = item.confirmed ? " · 已确认" : "";
  return `${name} (${short})${confirmed}`;
}

function applyWorkflowOptions(widget, items) {
  if (!widget) return;
  // ComfyUI combo options accept ``[display, value]`` pairs: the dropdown shows
  // the readable label while the node still receives the stable workflow id.
  const options = items.length
    ? items.map((item) => [workflowOptionLabel(item), item.id])
    : [""];
  if (Array.isArray(widget.options)) {
    widget.options = options;
  } else if (widget.options && typeof widget.options === "object") {
    widget.options.values = options;
  }
}

function siblingWorkflowId(node) {
  const nodes = (node.graph && node.graph._nodes) || [];
  for (const other of nodes) {
    if (other === node) continue;
    const widget = (other.widgets || []).find((w) => w.name === "workflow_id");
    if (widget && widget.value) return String(widget.value);
  }
  return "";
}

function bindPickerNode(node) {
  const widget = (node.widgets || []).find((w) => w.name === "workflow_id");
  if (!widget) return;

  void fetchPickerWorkflows().then((items) => {
    applyWorkflowOptions(widget, items);
    if (!widget.value) {
      // Reuse a workflow already bound on this canvas first, then fall back to
      // the last choice remembered in this browser.
      const inherited = siblingWorkflowId(node) || localStorage.getItem(PICKER_STORAGE_KEY);
      if (inherited) widget.value = inherited;
    }
  });

  const originalOnChange = widget.onChange;
  widget.onChange = function () {
    if (widget.value) localStorage.setItem(PICKER_STORAGE_KEY, String(widget.value));
    if (typeof originalOnChange === "function") originalOnChange.call(this);
  };
}

app.registerExtension({
  name: "WorkbenchWorkflowPicker",
  nodeCreated(node) {
    if (PICKER_NODE_TYPES.includes(node.type)) bindPickerNode(node);
  },
});
