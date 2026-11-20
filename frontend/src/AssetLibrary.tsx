import { useEffect, useRef, useState } from "react";
import { apiDelete, apiGet, apiPost } from "./api";

type AssetKind = "copy" | "reference";

type AssetGroup = {
  id: number;
  name: string;
  is_default: boolean;
  count: number;
};

type AssetTag = {
  id: number;
  name: string;
  count: number;
};

type AssetItem = {
  id: number;
  name: string;
  kind: AssetKind;
  storage_rel: string | null;
  source_path: string | null;
  group_id: number | null;
  group_name: string | null;
  size_bytes: number | null;
  ext: string | null;
  note: string;
  tags: string[];
  exists: boolean;
  created_at: string;
  updated_at: string;
};

type ImportMode = "copy" | "reference";

const STORE_GROUP = "workbench.assetGroup";
const STORE_KEYWORD = "workbench.assetKeyword";
const STORE_TAGS = "workbench.assetTags";

function readStored(key: string): string {
  try {
    return localStorage.getItem(key) || "";
  } catch {
    return "";
  }
}

function persist(key: string, value: string): void {
  try {
    localStorage.setItem(key, value);
  } catch {
    // 本地存储不可用时忽略。
  }
}

function assetImageUrl(item: AssetItem): string {
  return `/api/assets/image?asset_id=${item.id}`;
}

// In-memory blob-URL cache so thumbnails and the detail viewer reuse one fetch.
const objectUrlCache = new Map<number, string>();

async function cachedObjectUrl(item: AssetItem): Promise<string> {
  const cached = objectUrlCache.get(item.id);
  if (cached) return cached;
  const response = await fetch(assetImageUrl(item), { credentials: "include" });
  if (!response.ok) throw new Error(`图片加载失败（${response.status}）`);
  const blob = await response.blob();
  const objectUrl = URL.createObjectURL(blob);
  objectUrlCache.set(item.id, objectUrl);
  return objectUrl;
}

function formatBytes(bytes: number | null): string {
  if (bytes === null || bytes === undefined) return "—";
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB"];
  let value = bytes / 1024;
  let index = 0;
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024;
    index += 1;
  }
  return `${value.toFixed(1)} ${units[index]}`;
}

function formatTime(value: string): string {
  if (!value) return "—";
  return value.replace("T", " ").slice(0, 16);
}

function splitTags(text: string): string[] {
  return text
    .split(/[,，]/)
    .map((entry) => entry.trim())
    .filter(Boolean);
}

function AssetThumb({ item, onOpen }: { item: AssetItem; onOpen: () => void }) {
  const [objectUrl, setObjectUrl] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    if (!item.exists) {
      setObjectUrl(null);
      return;
    }
    cachedObjectUrl(item)
      .then((cached) => {
        if (alive) setObjectUrl(cached);
      })
      .catch(() => {
        // 失效时回退到代理 URL，由浏览器占位。
      });
    return () => {
      alive = false;
    };
  }, [item]);

  return (
    <div className="asset-thumb" onClick={onOpen} title="点击查看详情">
      {item.exists ? (
        <img src={objectUrl ?? assetImageUrl(item)} alt={item.name} loading="lazy" />
      ) : (
        <div className="asset-thumb-missing">图片已失效</div>
      )}
    </div>
  );
}

export default function AssetLibrary() {
  const [groups, setGroups] = useState<AssetGroup[] | null>(null);
  const [tags, setTags] = useState<AssetTag[] | null>(null);
  const [items, setItems] = useState<AssetItem[] | null>(null);
  const [error, setError] = useState("");

  const [groupFilter, setGroupFilter] = useState(() => readStored(STORE_GROUP));
  const [keyword, setKeyword] = useState(() => readStored(STORE_KEYWORD));
  const [activeTags, setActiveTags] = useState<string[]>(() =>
    splitTags(readStored(STORE_TAGS)),
  );

  const [importMode, setImportMode] = useState<ImportMode | null>(null);
  const [importGroupId, setImportGroupId] = useState("");
  const [importTags, setImportTags] = useState("");
  const [importNote, setImportNote] = useState("");
  const [refPathsText, setRefPathsText] = useState("");
  const [importBusy, setImportBusy] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const [editing, setEditing] = useState<AssetItem | null>(null);
  const [editName, setEditName] = useState("");
  const [editGroupId, setEditGroupId] = useState("");
  const [editTags, setEditTags] = useState("");
  const [editNote, setEditNote] = useState("");
  const [deleteCopy, setDeleteCopy] = useState(false);
  const [editBusy, setEditBusy] = useState(false);
  const [message, setMessage] = useState<{ kind: "ok" | "err"; text: string } | null>(null);

  function groupIdParam(): number | null {
    const parsed = Number(groupFilter);
    return groupFilter && Number.isFinite(parsed) ? parsed : null;
  }

  async function loadGroups() {
    const body = await apiGet<{ groups: AssetGroup[] }>("/api/assets/groups");
    setGroups(body.groups);
  }

  async function loadTags() {
    const body = await apiGet<{ tags: AssetTag[] }>("/api/assets/tags");
    setTags(body.tags);
  }

  async function loadItems() {
    const params = new URLSearchParams();
    const gid = groupIdParam();
    if (gid !== null) params.set("group_id", String(gid));
    if (activeTags.length > 0) params.set("tags", activeTags.join(","));
    if (keyword.trim()) params.set("keyword", keyword.trim());
    const query = params.toString();
    const body = await apiGet<{ items: AssetItem[] }>(`/api/assets/items${query ? `?${query}` : ""}`);
    setItems(body.items);
  }

  async function refreshAll() {
    try {
      await Promise.all([loadGroups(), loadTags(), loadItems()]);
      setError("");
    } catch (err) {
      setError(`读取素材库失败：${String(err)}`);
    }
  }

  useEffect(() => {
    void refreshAll();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    persist(STORE_GROUP, groupFilter);
    void loadItems().catch(() => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [groupFilter]);

  useEffect(() => {
    persist(STORE_KEYWORD, keyword);
    const timer = window.setTimeout(() => {
      void loadItems().catch(() => undefined);
    }, 180);
    return () => window.clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [keyword]);

  useEffect(() => {
    persist(STORE_TAGS, activeTags.join(","));
    void loadItems().catch(() => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTags]);

  useEffect(() => {
    if (!importMode && !editing) return;
    function onKeyDown(event: KeyboardEvent) {
      if (event.key !== "Escape") return;
      if (importMode && !importBusy) setImportMode(null);
      if (editing && !editBusy) setEditing(null);
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [importMode, editing, importBusy, editBusy]);

  function toggleTag(name: string) {
    setActiveTags((prev) =>
      prev.includes(name) ? prev.filter((entry) => entry !== name) : [...prev, name],
    );
  }

  function clearFilters() {
    setGroupFilter("");
    setKeyword("");
    setActiveTags([]);
  }

  function defaultGroupId(): string {
    const fallback = groups?.find((entry) => entry.is_default)?.id;
    return fallback !== undefined ? String(fallback) : "";
  }

  function openImport(mode: ImportMode) {
    setImportMode(mode);
    setImportGroupId((prev) => prev || defaultGroupId());
    setImportTags("");
    setImportNote("");
    setRefPathsText("");
    setMessage(null);
  }

  async function readFilesAsBase64(files: FileList | null) {
    if (!files || files.length === 0) return [];
    const payload: { name: string; data: string }[] = [];
    for (const file of Array.from(files)) {
      const dataUrl = await new Promise<string>((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result ?? ""));
        reader.onerror = () => reject(new Error("文件读取失败"));
        reader.readAsDataURL(file);
      });
      const comma = dataUrl.indexOf(",");
      payload.push({ name: file.name, data: comma >= 0 ? dataUrl.slice(comma + 1) : dataUrl });
    }
    return payload;
  }

  async function submitImport() {
    setImportBusy(true);
    setMessage(null);
    try {
      const groupId = importGroupId ? Number(importGroupId) : null;
      const tags = splitTags(importTags);
      if (importMode === "copy") {
        const files = await readFilesAsBase64(fileInputRef.current?.files ?? null);
        if (files.length === 0) throw new Error("请先选择要导入的图片");
        await apiPost("/api/assets/import-copy", {
          files,
          group_id: groupId,
          tags,
          note: importNote,
        });
      } else {
        const paths = refPathsText
          .split(/\r?\n/)
          .map((entry) => entry.trim())
          .filter(Boolean);
        if (paths.length === 0) throw new Error("请填写至少一个本地图片路径");
        await apiPost("/api/assets/import-reference", {
          paths,
          group_id: groupId,
          tags,
          note: importNote,
        });
      }
      setImportMode(null);
      if (fileInputRef.current) fileInputRef.current.value = "";
      await refreshAll();
      setMessage({ kind: "ok", text: "导入完成" });
    } catch (err) {
      setMessage({ kind: "err", text: String(err) });
    } finally {
      setImportBusy(false);
    }
  }

  async function openAssetsDir() {
    setMessage(null);
    try {
      await apiPost("/api/assets/open-dir", {});
      setMessage({ kind: "ok", text: "已在系统文件夹中打开素材目录" });
    } catch (err) {
      setMessage({ kind: "err", text: String(err) });
    }
  }

  async function createGroup(name: string) {
    const trimmed = name.trim();
    if (!trimmed) return;
    await apiPost("/api/assets/groups", { name: trimmed });
    await refreshAll();
  }

  async function renameGroup(group: AssetGroup) {
    const name = window.prompt("分组名称", group.name);
    if (name === null) return;
    try {
      await apiPost(`/api/assets/groups/${group.id}/rename`, { name });
      await refreshAll();
    } catch (err) {
      setMessage({ kind: "err", text: String(err) });
    }
  }

  async function removeGroup(group: AssetGroup) {
    const ok = window.confirm(`删除分组「${group.name}」？组内素材会移到默认分组，不会删除素材。`);
    if (!ok) return;
    try {
      await apiDelete(`/api/assets/groups/${group.id}`, {});
      if (groupFilter === String(group.id)) setGroupFilter("");
      await refreshAll();
    } catch (err) {
      setMessage({ kind: "err", text: String(err) });
    }
  }

  async function removeTag(tag: AssetTag) {
    const ok = window.confirm(`删除标签「${tag.name}」？只会解除关联，不删除素材。`);
    if (!ok) return;
    try {
      await apiDelete(`/api/assets/tags/${tag.id}`, {});
      setActiveTags((prev) => prev.filter((entry) => entry !== tag.name));
      await refreshAll();
    } catch (err) {
      setMessage({ kind: "err", text: String(err) });
    }
  }

  function openDetail(item: AssetItem) {
    setEditing(item);
    setEditName(item.name);
    setEditGroupId(item.group_id !== null ? String(item.group_id) : "");
    setEditTags(item.tags.join(", "));
    setEditNote(item.note);
    setDeleteCopy(false);
    setEditBusy(false);
    setMessage(null);
  }

  async function saveDetail() {
    if (!editing) return;
    setEditBusy(true);
    setMessage(null);
    try {
      const updated = await apiPost<AssetItem>(`/api/assets/items/${editing.id}`, {
        name: editName,
        group_id: editGroupId ? Number(editGroupId) : null,
        tags: splitTags(editTags),
        note: editNote,
      });
      setEditing(updated);
      await refreshAll();
      setMessage({ kind: "ok", text: "已保存修改" });
    } catch (err) {
      setMessage({ kind: "err", text: String(err) });
    } finally {
      setEditBusy(false);
    }
  }

  async function removeItem() {
    if (!editing) return;
    const label = editing.kind === "copy" ? "工作台内副本" : "原图文件（不会被删除）";
    const ok = window.confirm(
      editing.kind === "copy"
        ? `删除素材「${editing.name}」？${deleteCopy ? "将同时删除工作台内保存的副本。" : "工作台内副本会保留。"}`
        : `删除素材记录「${editing.name}」？仅移除引用，${label}。`,
    );
    if (!ok) return;
    setEditBusy(true);
    try {
      await apiDelete(`/api/assets/items/${editing.id}`, { delete_copy: deleteCopy });
      setEditing(null);
      await refreshAll();
      setMessage({ kind: "ok", text: "已删除" });
    } catch (err) {
      setMessage({ kind: "err", text: String(err) });
    } finally {
      setEditBusy(false);
    }
  }

  const visibleGroups = groups ?? [];
  const visibleTags = tags ?? [];
  const visibleItems = items ?? [];
  const selectedGroupIdNum = groupIdParam();
  const hasFilter = groupFilter !== "" || keyword.trim() !== "" || activeTags.length > 0;

  return (
    <section className="panel asset-panel">
      <div className="section-heading">
        <div>
          <h2>素材库</h2>
          <p>
            集中管理本地图片素材：可复制保存到工作台，或只引用外部路径；支持分组、标签与检索。
            引用素材不会复制、移动或删除原图。
          </p>
        </div>
        <div className="actions">
          <button type="button" className="secondary" onClick={() => openImport("copy")}>
            复制导入图片
          </button>
          <button type="button" className="secondary" onClick={() => openImport("reference")}>
            粘贴路径引用
          </button>
          <button type="button" className="secondary" onClick={() => void openAssetsDir()}>
            打开素材文件夹
          </button>
        </div>
      </div>

      {error && <p className="message asset-message err">{error}</p>}
      {message && <p className={`message asset-message ${message.kind}`}>{message.text}</p>}

      <div className="asset-body">
        <aside className="asset-groups">
          <div
            className={`asset-group-row ${selectedGroupIdNum === null ? "active" : ""}`}
            onClick={() => setGroupFilter("")}
          >
            <span>全部素材</span>
            <span className="asset-group-count">{visibleItems.length}</span>
          </div>
          {visibleGroups.map((group) => (
            <div
              key={group.id}
              className={`asset-group-row ${selectedGroupIdNum === group.id ? "active" : ""}`}
              onClick={() => setGroupFilter(String(group.id))}
            >
              <span className="asset-group-name">{group.name}</span>
              <span className="asset-group-count">{group.count}</span>
              {!group.is_default && (
                <span className="asset-group-ops">
                  <button type="button" title="重命名" onClick={(event) => { event.stopPropagation(); void renameGroup(group); }}>✎</button>
                  <button type="button" title="删除分组" onClick={(event) => { event.stopPropagation(); void removeGroup(group); }}>×</button>
                </span>
              )}
            </div>
          ))}
          <AddGroup onCreate={(name) => void createGroup(name)} />
        </aside>

        <div className="asset-main">
          <div className="asset-filters">
            <input
              type="text"
              className="asset-search"
              placeholder="按名称搜索…"
              value={keyword}
              onChange={(event) => setKeyword(event.target.value)}
            />
            <div className="asset-tag-chips">
              {visibleTags.map((tag) => (
                <span
                  key={tag.id}
                  className={`rule-tag asset-chip ${activeTags.includes(tag.name) ? "active" : ""}`}
                  onClick={() => toggleTag(tag.name)}
                >
                  {tag.name}
                  <button
                    type="button"
                    title="删除标签"
                    onClick={(event) => { event.stopPropagation(); void removeTag(tag); }}
                  >
                    ×
                  </button>
                </span>
              ))}
              {visibleTags.length === 0 && (
                <span className="catalog-empty">暂无标签</span>
              )}
            </div>
            {hasFilter && (
              <button type="button" className="asset-clear" onClick={clearFilters}>
                清空筛选
              </button>
            )}
          </div>

          {items === null ? (
            <p className="catalog-empty">正在读取素材…</p>
          ) : visibleItems.length === 0 ? (
            <p className="catalog-empty">没有匹配的素材，可从右上角导入图片或引用路径。</p>
          ) : (
            <div className="asset-grid">
              {visibleItems.map((item) => (
                <article
                  key={item.id}
                  className={`asset-card ${item.exists ? "" : "missing"}`}
                  onClick={() => openDetail(item)}
                >
                  <AssetThumb item={item} onOpen={() => openDetail(item)} />
                  <div className="asset-card-meta">
                    <div className="asset-card-name" title={item.name}>{item.name}</div>
                    <div className="asset-card-sub">
                      <span className={`compare-badge ${item.kind === "copy" ? "match" : ""}`}>
                        {item.kind === "copy" ? "副本" : "引用"}
                      </span>
                      <span>{item.group_name ?? "未分组"}</span>
                      <span>{formatBytes(item.size_bytes)}</span>
                    </div>
                    {item.tags.length > 0 && (
                      <div className="asset-card-tags">
                        {item.tags.map((tag) => (
                          <span key={tag} className="rule-tag">{tag}</span>
                        ))}
                      </div>
                    )}
                    {!item.exists && <div className="asset-card-warn">原始文件已失效</div>}
                  </div>
                </article>
              ))}
            </div>
          )}
        </div>
      </div>

      {importMode && (
        <div className="lightbox-overlay" onClick={() => !importBusy && setImportMode(null)}>
          <div className="asset-dialog" onClick={(event) => event.stopPropagation()}>
            <div className="asset-dialog-head">
              <h3>{importMode === "copy" ? "复制导入图片" : "粘贴路径引用"}</h3>
              <button type="button" className="lightbox-close" onClick={() => setImportMode(null)} aria-label="关闭">×</button>
            </div>

            {importMode === "copy" ? (
              <p className="catalog-offline">
                可一次勾选多张图片批量导入；字节会复制保存到工作台数据目录，之后原文件移动或删除不影响副本。
              </p>
            ) : (
              <p className="catalog-offline">
                每行粘贴一个本机图片绝对路径（PNG/JPG/JPEG/WebP），可批量粘贴多行；仅记录路径，不复制原图。
              </p>
            )}

            {importMode === "copy" ? (
              <input
                ref={fileInputRef}
                type="file"
                multiple
                accept=".png,.jpg,.jpeg,.webp"
              />
            ) : (
              <textarea
                className="asset-ref-paths"
                rows={6}
                placeholder={"D:\\Images\\ref1.png\nD:\\Images\\ref2.jpg"}
                value={refPathsText}
                onChange={(event) => setRefPathsText(event.target.value)}
              />
            )}

            <div className="asset-form-row">
              <label>
                分组
                <select value={importGroupId} onChange={(event) => setImportGroupId(event.target.value)}>
                  <option value="">默认分组</option>
                  {visibleGroups.map((group) => (
                    <option key={group.id} value={group.id}>{group.name}</option>
                  ))}
                </select>
              </label>
              <label>
                标签（逗号分隔）
                <input type="text" value={importTags} onChange={(event) => setImportTags(event.target.value)} placeholder="人物, 外景" />
              </label>
            </div>
            <label className="asset-form-note">
              备注
              <input type="text" value={importNote} onChange={(event) => setImportNote(event.target.value)} />
            </label>

            {message && <p className={`message asset-message ${message.kind}`}>{message.text}</p>}

            <div className="actions">
              <button type="button" className="secondary" disabled={importBusy} onClick={() => setImportMode(null)}>
                取消
              </button>
              <button type="button" disabled={importBusy} onClick={() => void submitImport()}>
                {importBusy ? "导入中…" : "开始导入"}
              </button>
            </div>
          </div>
        </div>
      )}

      {editing && (
        <div className="lightbox-overlay" onClick={() => !editBusy && setEditing(null)}>
          <div className="asset-detail" onClick={(event) => event.stopPropagation()}>
            <div className="asset-detail-head">
              <h3>素材详情</h3>
              <button type="button" className="lightbox-close" onClick={() => setEditing(null)} aria-label="关闭">×</button>
            </div>
            <div className="asset-detail-body">
              <div className="asset-detail-image">
                {editing.exists ? (
                  <DetailImage item={editing} />
                ) : (
                  <div className="asset-thumb-missing">图片已失效</div>
                )}
              </div>
              <div className="asset-detail-form">
                {!editing.exists && (
                  <p className="asset-card-warn">原始文件已失效，下方信息仍可编辑。</p>
                )}
                <label>
                  名称
                  <input type="text" value={editName} onChange={(event) => setEditName(event.target.value)} />
                </label>
                <label>
                  分组
                  <select value={editGroupId} onChange={(event) => setEditGroupId(event.target.value)}>
                    <option value="">默认分组</option>
                    {visibleGroups.map((group) => (
                      <option key={group.id} value={group.id}>{group.name}</option>
                    ))}
                  </select>
                </label>
                <label>
                  标签（逗号分隔）
                  <input type="text" value={editTags} onChange={(event) => setEditTags(event.target.value)} />
                </label>
                <label>
                  备注
                  <textarea rows={3} value={editNote} onChange={(event) => setEditNote(event.target.value)} />
                </label>

                <div className="asset-detail-info">
                  <div>类型：{editing.kind === "copy" ? "工作台副本" : "路径引用"}</div>
                  <div>大小：{formatBytes(editing.size_bytes)}</div>
                  <div>导入时间：{formatTime(editing.created_at)}</div>
                  <div className="asset-detail-path">
                    {editing.kind === "reference" ? editing.source_path : `assets/copies/${editing.storage_rel ?? ""}`}
                  </div>
                </div>

                {editing.kind === "copy" && (
                  <label className="asset-check">
                    <input
                      type="checkbox"
                      checked={deleteCopy}
                      onChange={(event) => setDeleteCopy(event.target.checked)}
                    />
                    同时删除工作台内保存的副本
                  </label>
                )}

                {message && <p className={`message asset-message ${message.kind}`}>{message.text}</p>}

                <div className="actions">
                  <button type="button" className="secondary" disabled={editBusy} onClick={() => void saveDetail()}>
                    保存修改
                  </button>
                  <button type="button" className="secondary danger" disabled={editBusy} onClick={() => void removeItem()}>
                    删除素材
                  </button>
                </div>
              </div>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}

function AddGroup({ onCreate }: { onCreate: (name: string) => void }) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");

  function submit() {
    if (!name.trim()) return;
    onCreate(name);
    setName("");
    setOpen(false);
  }

  if (!open) {
    return (
      <button type="button" className="asset-group-add" onClick={() => setOpen(true)}>
        + 新建分组
      </button>
    );
  }
  return (
    <div className="asset-group-new">
      <input
        type="text"
        autoFocus
        placeholder="分组名称"
        value={name}
        onChange={(event) => setName(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter") submit();
        }}
      />
      <div className="actions">
        <button type="button" className="secondary" onClick={() => setOpen(false)}>取消</button>
        <button type="button" onClick={submit}>保存</button>
      </div>
    </div>
  );
}

function DetailImage({ item }: { item: AssetItem }) {
  const [objectUrl, setObjectUrl] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    cachedObjectUrl(item)
      .then((cached) => {
        if (alive) setObjectUrl(cached);
      })
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [item]);

  return <img src={objectUrl ?? assetImageUrl(item)} alt={item.name} />;
}
