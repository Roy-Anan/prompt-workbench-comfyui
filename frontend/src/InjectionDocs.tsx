import { useEffect, useMemo, useRef, useState } from "react";
import { apiDelete, apiGet, apiPost } from "./api";

type LayerKey = "prompt_persona" | "writing_persona" | "genre" | "style" | "mode" | "core";

type LayerItem = {
  layer: string;
  id: string;
  name: string;
  hidden: boolean;
  exists: boolean;
  chars: number;
  preview: string;
};

type LayersPayload = {
  layers: Record<string, LayerItem[]>;
  core: { exists: boolean; chars: number };
};

type ModeFlags = {
  id: string;
  name?: string;
  display_name?: string;
  kind?: string;
  enabled?: boolean;
};

const LAYER_TABS: { key: LayerKey; label: string; hint: string }[] = [
  { key: "prompt_persona", label: "提示词身份", hint: "提示词类人设" },
  { key: "writing_persona", label: "写作身份", hint: "写作类人设" },
  { key: "genre", label: "题材", hint: "题材偏好" },
  { key: "style", label: "文风", hint: "文风偏好" },
  { key: "mode", label: "创作模式", hint: "模式清单（含程序标志）" },
  { key: "core", label: "核心系统规则", hint: "所有调用共用的底层规则" },
];

function fileNameOf(name: string): string {
  return `${name}.txt`;
}

export default function InjectionDocs() {
  const [payload, setPayload] = useState<LayersPayload | null>(null);
  const [modes, setModes] = useState<ModeFlags[]>([]);
  const [layer, setLayer] = useState<LayerKey>("prompt_persona");
  const [selected, setSelected] = useState<string | null>(null);
  const [text, setText] = useState("");
  const [keyword, setKeyword] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState("");
  // Stale-response guard: switching layer/文件 fast must not let an older read
  // overwrite the newer editor content.
  const requestToken = useRef(0);

  // Toast-style notice: confirmations fade on their own so they never pile up.
  useEffect(() => {
    if (!notice) return;
    const timer = window.setTimeout(() => setNotice(null), 4000);
    return () => window.clearTimeout(timer);
  }, [notice]);

  async function reloadLayers() {
    setLoading(true);
    try {
      const data = await apiGet<LayersPayload>("/api/injection-docs");
      setPayload(data);
      setError(null);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void reloadLayers();
    void apiGet<{ items: ModeFlags[] }>("/api/injection-docs/modes")
      .then((data) => setModes(data.items))
      .catch(() => setModes([]));
  }, []);

  const items = useMemo<LayerItem[]>(() => {
    if (!payload) return [];
    if (layer === "core") {
      return [
        {
          layer: "core",
          id: "core",
          // Selection uses the fixed id "core"; the filename is display-only.
          name: "core",
          hidden: false,
          exists: payload.core.exists,
          chars: payload.core.chars,
          preview: "",
        },
      ];
    }
    return payload.layers[layer] ?? [];
  }, [payload, layer]);

  const visibleItems = useMemo(() => {
    const key = keyword.trim().toLowerCase();
    if (!key) return items;
    return items.filter((item) =>
      `${item.name} ${item.preview}`.toLowerCase().includes(key),
    );
  }, [items, keyword]);

  // Keep a valid selection when the layer changes or the list reloads; after a
  // save we stay on the same file instead of jumping back to the top.
  useEffect(() => {
    if (layer === "core") {
      setSelected("core");
      return;
    }
    const names = (payload?.layers[layer] ?? []).map((item) => item.name);
    setSelected((prev) => (prev && names.includes(prev) ? prev : names[0] ?? null));
  }, [layer, payload]);

  useEffect(() => {
    if (!selected) {
      setText("");
      return;
    }
    // Only read a name that actually belongs to the current layer; during a
    // tab switch the previous selection lingers for one render.
    if (layer !== "core") {
      const names = (payload?.layers[layer] ?? []).map((item) => item.name);
      if (!names.includes(selected)) {
        setText("");
        return;
      }
    }
    void load(layer === "core" ? "core" : layer, selected);
  }, [layer, selected, payload]);

  async function load(targetLayer: string, name: string) {
    const token = ++requestToken.current;
    try {
      const data = await apiGet<{ text: string }>(
        `/api/injection-docs/${targetLayer}/${encodeURIComponent(name)}`,
      );
      if (requestToken.current !== token) return;
      setText(data.text);
      setError(null);
    } catch (exc) {
      if (requestToken.current !== token) return;
      setText("");
      setError(exc instanceof Error ? exc.message : String(exc));
    }
  }

  async function save() {
    if (!selected && layer !== "core") return;
    setBusy(true);
    try {
      if (layer === "core") {
        await apiPost("/api/injection-docs/core/core", { text });
      } else {
        await apiPost(`/api/injection-docs/${layer}/${encodeURIComponent(selected as string)}`, {
          text,
        });
      }
      setNotice("已保存");
      await reloadLayers();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBusy(false);
    }
  }

  async function createItem() {
    const name = newName.trim();
    if (!name || layer === "core") return;
    setBusy(true);
    try {
      await apiPost(`/api/injection-docs/${layer}`, { name, text: "" });
      setCreating(false);
      setNewName("");
      await reloadLayers();
      setSelected(name);
      setNotice("已新建空文本，记得写入正文并保存");
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBusy(false);
    }
  }

  async function removeItem(item: LayerItem) {
    if (!window.confirm(`删除「${item.name}」？\n这是文件删除，无法在工作台内撤销。`)) return;
    setBusy(true);
    try {
      await apiDelete(`/api/injection-docs/${layer}/${encodeURIComponent(item.name)}`, {});
      setSelected(null);
      await reloadLayers();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBusy(false);
    }
  }

  async function toggleHidden(item: LayerItem) {
    if (layer === "core") return;
    setBusy(true);
    try {
      await apiPost(
        `/api/injection-docs/${layer}/${encodeURIComponent(item.name)}/hidden?hidden=${!item.hidden}`,
        {},
      );
      await reloadLayers();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBusy(false);
    }
  }

  const current = items.find((item) => item.name === selected) ?? null;

  return (
    <div className="asset-panel">
      <div className="section-heading">
        <div>
          <h2>注入文本</h2>
          <p className="muted">
            送进本地 LLM 的文本层：身份、题材、文风、创作模式与核心规则。停用即改名隐藏，下拉里不再出现；规则套（模型专属）在工作流工作区的规则库中管理。
          </p>
        </div>
      </div>

      {error && <div className="injection-error">{error}</div>}
      {notice && <div className="injection-notice">{notice}</div>}

      <div className="mg-toolbar">
        {LAYER_TABS.map((tab) => {
          const count =
            tab.key === "core"
              ? payload?.core.exists
                ? 1
                : 0
              : payload?.layers[tab.key]?.length ?? 0;
          return (
            <button
              key={tab.key}
              className={`chip ${layer === tab.key ? "active" : ""}`}
              onClick={() => setLayer(tab.key)}
              title={tab.hint}
            >
              {tab.label} {count}
            </button>
          );
        })}
      </div>

      <div className="inj-body">
        <div className="inj-list">
          {layer !== "core" && (
            <div className="inj-list-head">
              <input
                className="lora-filter-input"
                placeholder="搜索名称 / 摘要"
                value={keyword}
                onChange={(event) => setKeyword(event.target.value)}
              />
              {!creating ? (
                <button onClick={() => setCreating(true)}>新建</button>
              ) : (
                <div className="inj-new">
                  <input
                    autoFocus
                    placeholder="名称（即文件名）"
                    value={newName}
                    onChange={(event) => setNewName(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter") void createItem();
                    }}
                  />
                  <button onClick={() => void createItem()} disabled={busy}>
                    确定
                  </button>
                  <button onClick={() => setCreating(false)}>取消</button>
                </div>
              )}
            </div>
          )}

          {loading ? (
            <p className="muted">加载中…</p>
          ) : visibleItems.length === 0 ? (
            <p className="muted">这一层还没有文本。</p>
          ) : (
            <ul className="inj-items">
              {visibleItems.map((item) => (
                <li
                  key={item.name}
                  className={`inj-item ${selected === item.name ? "active" : ""} ${item.hidden ? "is-hidden" : ""}`}
                  onClick={() => setSelected(item.name)}
                >
                  <div className="inj-item-name">
                    {item.layer === "core" ? "core_system_prompt.txt" : item.name}
                    {item.hidden && <span className="chip static">已隐藏</span>}
                    {!item.exists && <span className="chip static warn">空文本</span>}
                  </div>
                  <div className="inj-item-sub">
                    {item.layer === "core" ? "全局核心规则" : fileNameOf(item.name)} · {item.chars} 字符
                  </div>
                  <div className="inj-item-preview">{item.preview || "（空）"}</div>
                  {layer !== "core" && (
                    <div className="inj-item-actions" onClick={(event) => event.stopPropagation()}>
                      <button onClick={() => void toggleHidden(item)}>
                        {item.hidden ? "启用" : "停用"}
                      </button>
                      <button className="danger" onClick={() => void removeItem(item)}>
                        删除
                      </button>
                    </div>
                  )}
                </li>
              ))}
            </ul>
          )}

          {layer === "mode" && modes.length > 0 && (
            <div className="inj-mode-flags">
              <div className="inj-mode-title">程序侧模式标志（只读）</div>
              {modes.map((mode) => (
                <div key={mode.id} className="inj-mode-row">
                  <span>{mode.display_name || mode.name || mode.id}</span>
                  {mode.kind && <span className="chip static">{mode.kind}</span>}
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="inj-editor">
          <div className="inj-editor-head">
            <span>
              {!current
                ? "未选择文本"
                : current.layer === "core"
                  ? "核心系统规则（core_system_prompt.txt）"
                  : current.name}
            </span>
            <span className="muted">{text.length} 字符</span>
          </div>
          <textarea
            className="inj-textarea"
            value={text}
            onChange={(event) => setText(event.target.value)}
            placeholder="选择左侧一份文本开始编辑；新建的空文本在这里写入正文后点保存。"
            rows={24}
            disabled={!current}
          />
          <div className="mg-modal-foot">
            <button className="primary" disabled={busy || !current} onClick={() => void save()}>
              {busy ? "保存中…" : "保存"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
