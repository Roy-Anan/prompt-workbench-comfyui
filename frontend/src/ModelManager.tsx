import { useEffect, useMemo, useState } from "react";
import { apiDelete, apiGet, apiPost } from "./api";

type ModelDoc = {
  id: string;
  display_name: string;
  description: string;
  architecture: string;
  strengths: string;
  tags: string[];
  cover_asset_id: number | null;
  model_page: string;
  author: string;
  trust_level: string;
  research_status: string;
  trigger_words: string[];
  checkpoint_patterns: string[];
  version: string;
  enabled: boolean;
  exists: boolean;
  rules?: string;
  section_warnings?: string[];
};

type CatalogEntry = { name: string; on_disk: boolean | null };

type ModelCatalog = { checkpoints: CatalogEntry[]; online?: boolean };

type AssetItem = { id: number; name: string; exists: boolean; group_name: string | null };

type FormState = {
  id: string | null;
  display_name: string;
  checkpoint_file: string;
  architecture: string;
  strengths: string;
  description: string;
  tags: string;
  cover_asset_id: string;
  model_page: string;
  author: string;
  trust_level: string;
  trigger_words: string;
  rules_text: string;
};

const EMPTY_FORM: FormState = {
  id: null,
  display_name: "",
  checkpoint_file: "",
  architecture: "",
  strengths: "",
  description: "",
  tags: "",
  cover_asset_id: "",
  model_page: "",
  author: "",
  trust_level: "",
  trigger_words: "",
  rules_text: "",
};

const ARCH_SUGGESTIONS = ["SD1.5", "SDXL", "SDXL Pony", "Illustrious", "Flux", "SD3"];
const TRUST_SUGGESTIONS = ["已验证", "部分核实", "社区经验", "未验证"];

function splitList(text: string): string[] {
  return text
    .split(/[,，、]/)
    .map((entry) => entry.trim())
    .filter(Boolean);
}

function initialOf(name: string): string {
  return (name.trim()[0] || "?").toUpperCase();
}

function normalized(value: string): string {
  return value.trim().toLowerCase();
}

function coverUrl(id: number): string {
  return `/api/assets/image?asset_id=${id}`;
}

export default function ModelManager() {
  const [docs, setDocs] = useState<ModelDoc[]>([]);
  const [catalog, setCatalog] = useState<ModelCatalog | null>(null);
  const [assets, setAssets] = useState<AssetItem[]>([]);
  const [keyword, setKeyword] = useState("");
  const [archFilter, setArchFilter] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const [form, setForm] = useState<FormState | null>(null);
  const [detail, setDetail] = useState<ModelDoc | null>(null);
  const [assetPickerOpen, setAssetPickerOpen] = useState(false);

  async function reload() {
    setLoading(true);
    try {
      const payload = await apiGet<{ items: ModelDoc[] }>("/api/model-docs");
      setDocs(payload.items);
      setError(null);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void reload();
    void apiGet<ModelCatalog>("/api/models/catalog")
      .then(setCatalog)
      .catch(() => setCatalog(null));
    void apiGet<{ items: AssetItem[] }>("/api/assets/items")
      .then((payload) => setAssets(payload.items))
      .catch(() => setAssets([]));
  }, []);

  const architectures = useMemo(() => {
    const set = new Set<string>();
    for (const doc of docs) if (doc.architecture) set.add(doc.architecture);
    return Array.from(set).sort();
  }, [docs]);

  const filtered = useMemo(() => {
    const key = keyword.trim().toLowerCase();
    return docs.filter((doc) => {
      if (archFilter && doc.architecture !== archFilter) return false;
      if (!key) return true;
      const haystack = [
        doc.display_name,
        doc.architecture,
        doc.strengths,
        doc.description,
        doc.tags.join(" "),
        doc.checkpoint_patterns.join(" "),
      ]
        .join(" ")
        .toLowerCase();
      return haystack.includes(key);
    });
  }, [docs, keyword, archFilter]);

  const checkpointTaken = useMemo(() => {
    const map = new Map<string, string>();
    for (const doc of docs) {
      for (const pattern of doc.checkpoint_patterns) {
        map.set(pattern.trim().toLowerCase(), doc.display_name);
      }
    }
    return map;
  }, [docs]);

  async function fillTemplate() {
    try {
      const payload = await apiGet<{ text: string }>("/api/model-docs/template");
      setForm((prev) => (prev ? { ...prev, rules_text: payload.text } : prev));
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    }
  }

  async function openDetail(doc: ModelDoc) {
    setDetail(doc);
    try {
      // The list projection omits the rules body; fetch the full entry so the
      // detail panel can show the actual documentation text.
      const full = await apiGet<ModelDoc>(`/api/model-docs/${encodeURIComponent(doc.id)}`);
      setDetail((prev) => (prev && prev.id === doc.id ? full : prev));
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    }
  }

  async function openCreate(prefillCheckpoint?: string) {
    setForm({ ...EMPTY_FORM, checkpoint_file: prefillCheckpoint ?? "" });
    setDetail(null);
  }

  async function openEdit(doc: ModelDoc) {
    try {
      const full = await apiGet<ModelDoc>(`/api/model-docs/${encodeURIComponent(doc.id)}`);
      setForm({
        id: doc.id,
        display_name: full.display_name || "",
        checkpoint_file: full.checkpoint_patterns[0] || "",
        architecture: full.architecture || "",
        strengths: full.strengths || "",
        description: full.description || "",
        tags: (full.tags || []).join(", "),
        cover_asset_id: full.cover_asset_id ? String(full.cover_asset_id) : "",
        model_page: full.model_page || "",
        author: full.author || "",
        trust_level: full.trust_level || "",
        trigger_words: (full.trigger_words || []).join(", "),
        rules_text: full.rules || "",
      });
      setDetail(null);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    }
  }

  async function submitForm() {
    if (!form) return;
    if (!form.display_name.trim() && !form.checkpoint_file.trim()) {
      setError("请至少填写显示名或选择 Checkpoint");
      return;
    }
    setBusy(true);
    setError(null);
    const body = {
      display_name: form.display_name.trim(),
      checkpoint_file: form.checkpoint_file.trim(),
      architecture: form.architecture.trim(),
      strengths: form.strengths.trim(),
      description: form.description.trim(),
      tags: splitList(form.tags),
      cover_asset_id: form.cover_asset_id ? Number(form.cover_asset_id) : null,
      model_page: form.model_page.trim(),
      author: form.author.trim(),
      trust_level: form.trust_level.trim(),
      trigger_words: splitList(form.trigger_words),
      rules_text: form.rules_text,
    };
    try {
      if (form.id) {
        await apiPost(`/api/model-docs/${encodeURIComponent(form.id)}`, body);
      } else {
        await apiPost("/api/model-docs", body);
      }
      setForm(null);
      await reload();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBusy(false);
    }
  }

  async function removeDoc(doc: ModelDoc) {
    if (!window.confirm(`删除「${doc.display_name}」的全部资料？\n仅删工作台内的说明文档与历史版本，ComfyUI 权重文件不受影响。`)) {
      return;
    }
    setBusy(true);
    try {
      await apiDelete(`/api/model-docs/${encodeURIComponent(doc.id)}`, {});
      setDetail(null);
      await reload();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBusy(false);
    }
  }

  async function toggleEnabled(doc: ModelDoc) {
    setBusy(true);
    try {
      await apiPost(`/api/model-docs/${encodeURIComponent(doc.id)}`, {
        enabled: !doc.enabled,
      });
      await reload();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBusy(false);
    }
  }

  const coverAsset = form?.cover_asset_id
    ? assets.find((asset) => String(asset.id) === form.cover_asset_id)
    : null;

  return (
    <div className="asset-panel">
      <div className="section-heading">
        <div>
          <h2>模型管理</h2>
          <p className="muted">
            只管理 Checkpoint 底模的说明资料（擅长、提示词规则、代表图）。删除只影响工作台内的文档，不动 ComfyUI 权重文件。
          </p>
        </div>
        <button className="primary" onClick={() => openCreate()}>
          新增底模资料
        </button>
      </div>

      {error && <div className="injection-error">{error}</div>}

      <div className="mg-toolbar">
        <input
          className="lora-filter-input"
          placeholder="搜索显示名 / 架构 / 擅长 / 标签"
          value={keyword}
          onChange={(event) => setKeyword(event.target.value)}
        />
        <div className="mg-chips">
          <button
            className={`chip ${archFilter === "" ? "active" : ""}`}
            onClick={() => setArchFilter("")}
          >
            全部架构
          </button>
          {architectures.map((arch) => (
            <button
              key={arch}
              className={`chip ${archFilter === arch ? "active" : ""}`}
              onClick={() => setArchFilter(arch)}
            >
              {arch}
            </button>
          ))}
        </div>
        <span className="muted mg-count">{filtered.length} / {docs.length} 份资料</span>
      </div>

      {loading ? (
        <div className="muted">加载中…</div>
      ) : filtered.length === 0 ? (
        <div className="mg-empty">
          <p>{docs.length === 0 ? "还没有底模资料，点右上角新增一份。" : "没有匹配筛选条件的资料。"}</p>
        </div>
      ) : (
        <div className="asset-grid mg-grid">
          {filtered.map((doc) => (
            <article key={doc.id} className={`asset-card mg-card ${doc.enabled ? "" : "is-disabled"}`}>
              <div className="asset-thumb mg-thumb" onClick={() => void openDetail(doc)}>
                {doc.cover_asset_id ? (
                  <img src={coverUrl(doc.cover_asset_id)} alt={doc.display_name} loading="lazy" />
                ) : (
                  <span className="mg-thumb-letter">{initialOf(doc.display_name)}</span>
                )}
                {!doc.exists && <span className="mg-flag">文档缺失</span>}
              </div>
              <div className="asset-card-meta">
                <div className="asset-card-name" title={doc.display_name}>
                  {doc.display_name}
                </div>
                <div className="asset-card-sub">
                  {doc.architecture && <span className="asset-badge">{doc.architecture}</span>}
                  {doc.trust_level && <span className="asset-badge">{doc.trust_level}</span>}
                  {!doc.enabled && <span className="asset-badge warn">已停用</span>}
                </div>
                {doc.strengths && <div className="mg-strength">{doc.strengths}</div>}
                {doc.tags.length > 0 && (
                  <div className="asset-card-tags">
                    {doc.tags.slice(0, 4).map((tag) => (
                      <span key={tag} className="chip static">
                        {tag}
                      </span>
                    ))}
                  </div>
                )}
                <div className="mg-actions">
                  <button onClick={() => void openDetail(doc)}>详情</button>
                  <button onClick={() => void openEdit(doc)}>编辑</button>
                  <button onClick={() => void toggleEnabled(doc)}>
                    {doc.enabled ? "停用" : "启用"}
                  </button>
                </div>
              </div>
            </article>
          ))}
        </div>
      )}

      {form && (
        <div className="mg-modal" onClick={() => setForm(null)}>
          <div className="mg-modal-body" onClick={(event) => event.stopPropagation()}>
            <div className="mg-modal-head">
              <h3>{form.id ? "编辑底模资料" : "新增底模资料"}</h3>
              <button onClick={() => setForm(null)}>关闭</button>
            </div>

            <div className="mg-form-grid">
              <label>
                选择的底模文件
                <select
                  value={form.checkpoint_file}
                  onChange={(event) => {
                    const value = event.target.value;
                    const takenBy = checkpointTaken.get(normalized(value));
                    setForm((prev) =>
                      prev
                        ? {
                            ...prev,
                            checkpoint_file: value,
                            display_name:
                              prev.display_name ||
                              value.replace(/\.(safetensors|ckpt)$/i, ""),
                          }
                        : prev,
                    );
                    if (value && takenBy && !form.id) {
                      setError(`该底模已有资料：「${takenBy}」，再建会生成第二份。`);
                    } else {
                      setError(null);
                    }
                  }}
                >
                  <option value="">（未在 ComfyUI 名单中 / 手动填写）</option>
                  {(catalog?.checkpoints ?? []).map((entry) => (
                    <option key={entry.name} value={entry.name}>
                      {entry.name}
                      {entry.on_disk === false ? "（缺文件）" : ""}
                      {checkpointTaken.has(normalized(entry.name)) ? " · 已有资料" : ""}
                    </option>
                  ))}
                </select>
              </label>

              <label>
                显示名
                <input
                  value={form.display_name}
                  onChange={(event) =>
                    setForm((prev) => (prev ? { ...prev, display_name: event.target.value } : prev))
                  }
                  placeholder="下拉里显示的名字"
                />
              </label>

              <label>
                架构
                <input
                  list="mg-arch-list"
                  value={form.architecture}
                  onChange={(event) =>
                    setForm((prev) => (prev ? { ...prev, architecture: event.target.value } : prev))
                  }
                  placeholder="SD1.5 / SDXL / Pony …"
                />
                <datalist id="mg-arch-list">
                  {ARCH_SUGGESTIONS.map((arch) => (
                    <option key={arch} value={arch} />
                  ))}
                </datalist>
              </label>

              <label>
                可信度
                <input
                  list="mg-trust-list"
                  value={form.trust_level}
                  onChange={(event) =>
                    setForm((prev) => (prev ? { ...prev, trust_level: event.target.value } : prev))
                  }
                  placeholder="已验证 / 部分核实 …"
                />
                <datalist id="mg-trust-list">
                  {TRUST_SUGGESTIONS.map((item) => (
                    <option key={item} value={item} />
                  ))}
                </datalist>
              </label>

              <label className="span2">
                擅长什么（卡片摘要）
                <input
                  value={form.strengths}
                  onChange={(event) =>
                    setForm((prev) => (prev ? { ...prev, strengths: event.target.value } : prev))
                  }
                  placeholder="一句话写清这个底模最擅长的题材/质感"
                  maxLength={300}
                />
              </label>

              <label className="span2">
                简介
                <input
                  value={form.description}
                  onChange={(event) =>
                    setForm((prev) => (prev ? { ...prev, description: event.target.value } : prev))
                  }
                  maxLength={500}
                />
              </label>

              <label>
                标签（逗号分隔）
                <input
                  value={form.tags}
                  onChange={(event) =>
                    setForm((prev) => (prev ? { ...prev, tags: event.target.value } : prev))
                  }
                  placeholder="写实, 插画, 人像"
                />
              </label>

              <label>
                触发词（逗号分隔）
                <input
                  value={form.trigger_words}
                  onChange={(event) =>
                    setForm((prev) => (prev ? { ...prev, trigger_words: event.target.value } : prev))
                  }
                />
              </label>

              <label>
                作者 / 模型页
                <input
                  value={form.model_page}
                  onChange={(event) =>
                    setForm((prev) => (prev ? { ...prev, model_page: event.target.value } : prev))
                  }
                  placeholder="Civitai / HuggingFace 链接"
                />
              </label>

              <label>
                代表图（素材库）
                <div className="mg-cover-row">
                  {form.cover_asset_id ? (
                    <>
                      <img
                        className="mg-cover-preview"
                        src={coverUrl(Number(form.cover_asset_id))}
                        alt="代表图"
                      />
                      <span className="muted">{coverAsset?.name ?? `#${form.cover_asset_id}`}</span>
                      <button
                        onClick={() =>
                          setForm((prev) => (prev ? { ...prev, cover_asset_id: "" } : prev))
                        }
                      >
                        移除
                      </button>
                    </>
                  ) : (
                    <button onClick={() => setAssetPickerOpen(true)}>从素材库选择</button>
                  )}
                </div>
              </label>
            </div>

            <div className="mg-rules-head">
              <span>说明文档正文（rules.txt）</span>
              <button onClick={() => void fillTemplate()}>填入模板</button>
            </div>
            <textarea
              className="mg-rules"
              value={form.rules_text}
              onChange={(event) =>
                setForm((prev) => (prev ? { ...prev, rules_text: event.target.value } : prev))
              }
              placeholder="留空则自动按模板生成；也可以点「填入模板」后逐段补充。"
              rows={12}
            />

            <div className="mg-modal-foot">
              <button onClick={() => setForm(null)}>取消</button>
              <button className="primary" disabled={busy} onClick={() => void submitForm()}>
                {busy ? "保存中…" : "保存"}
              </button>
            </div>
          </div>
        </div>
      )}

      {assetPickerOpen && (
        <div className="mg-modal" onClick={() => setAssetPickerOpen(false)}>
          <div className="mg-modal-body mg-picker" onClick={(event) => event.stopPropagation()}>
            <div className="mg-modal-head">
              <h3>选择代表图</h3>
              <button onClick={() => setAssetPickerOpen(false)}>关闭</button>
            </div>
            <div className="mg-picker-grid">
              {assets
                .filter((asset) => asset.exists)
                .map((asset) => (
                  <button
                    key={asset.id}
                    className="mg-picker-item"
                    onClick={() => {
                      setForm((prev) =>
                        prev ? { ...prev, cover_asset_id: String(asset.id) } : prev,
                      );
                      setAssetPickerOpen(false);
                    }}
                    title={asset.name}
                  >
                    <img src={coverUrl(asset.id)} alt={asset.name} loading="lazy" />
                    <span>{asset.name}</span>
                  </button>
                ))}
              {assets.length === 0 && <p className="muted">素材库里还没有图片。</p>}
            </div>
          </div>
        </div>
      )}

      {detail && (
        <div className="mg-modal" onClick={() => setDetail(null)}>
          <div className="mg-modal-body mg-detail" onClick={(event) => event.stopPropagation()}>
            <div className="mg-modal-head">
              <h3>{detail.display_name}</h3>
              <button onClick={() => setDetail(null)}>关闭</button>
            </div>
            <div className="mg-detail-meta">
              {detail.architecture && <span className="asset-badge">{detail.architecture}</span>}
              <span className="asset-badge">{detail.trust_level || "未标注可信度"}</span>
              <span className="asset-badge">{detail.version || "无版本号"}</span>
              {detail.enabled ? (
                <span className="asset-badge">已启用</span>
              ) : (
                <span className="asset-badge warn">已停用</span>
              )}
            </div>
            {detail.strengths && <p className="mg-detail-line">擅长：{detail.strengths}</p>}
            {detail.description && <p className="mg-detail-line">简介：{detail.description}</p>}
            {detail.checkpoint_patterns.length > 0 && (
              <p className="mg-detail-line">
                匹配文件：{detail.checkpoint_patterns.join("、")}
              </p>
            )}
            {detail.tags.length > 0 && (
              <div className="asset-card-tags">
                {detail.tags.map((tag) => (
                  <span key={tag} className="chip static">
                    {tag}
                  </span>
                ))}
              </div>
            )}
            {(detail.section_warnings?.length ?? 0) > 0 && (
              <p className="mg-detail-line mg-hint">
                模板章节较少：{detail.section_warnings?.join("、")} 未覆盖
              </p>
            )}
            <pre className="mg-rules-preview">{detail.rules || "（还没有正文）"}</pre>
            <div className="mg-modal-foot">
              <button onClick={() => void removeDoc(detail)} className="danger">
                删除这份资料
              </button>
              <button onClick={() => void openEdit(detail)}>编辑</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
