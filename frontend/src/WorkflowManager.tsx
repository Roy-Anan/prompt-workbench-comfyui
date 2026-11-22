import { useEffect, useMemo, useState } from "react";
import { apiDelete, apiGet, apiPost } from "./api";

type WorkflowCard = {
  id: string;
  filename: string;
  title: string;
  note: string;
  tags: string[];
  favored: boolean;
  pinned: boolean;
  archived: boolean;
  format: string;
  confirmed: boolean;
  imported_at: string;
  updated_at: string;
  last_written_at: string | null;
  draft_count: number;
  version_count: number;
};

type EditState = {
  id: string;
  title: string;
  note: string;
  tags: string;
  favored: boolean;
  pinned: boolean;
};

const STORE_TAG = "workbench.workflowTag";

function readStoredTag(): string {
  try {
    return localStorage.getItem(STORE_TAG) || "";
  } catch {
    return "";
  }
}

function formatTime(value: string | null): string {
  if (!value) return "—";
  return value.replace("T", " ").slice(0, 16);
}

function initialOf(text: string): string {
  return (text.trim()[0] || "?").toUpperCase();
}

function splitList(text: string): string[] {
  return text
    .split(/[,，、]/)
    .map((entry) => entry.trim())
    .filter(Boolean);
}

export default function WorkflowManager() {
  const [cards, setCards] = useState<WorkflowCard[]>([]);
  const [keyword, setKeyword] = useState("");
  const [tagFilter, setTagFilter] = useState(readStoredTag);
  const [showArchived, setShowArchived] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [edit, setEdit] = useState<EditState | null>(null);

  async function reload() {
    setLoading(true);
    try {
      const payload = await apiGet<{ items: WorkflowCard[] }>("/api/workflow-library");
      setCards(payload.items);
      setError(null);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void reload();
  }, []);

  useEffect(() => {
    try {
      localStorage.setItem(STORE_TAG, tagFilter);
    } catch {
      // 本地存储不可用时忽略。
    }
  }, [tagFilter]);

  const tags = useMemo(() => {
    const set = new Set<string>();
    for (const card of cards) for (const tag of card.tags) set.add(tag);
    return Array.from(set).sort();
  }, [cards]);

  const visible = useMemo(() => {
    const key = keyword.trim().toLowerCase();
    return cards.filter((card) => {
      if (card.archived !== showArchived) return false;
      if (tagFilter && !card.tags.includes(tagFilter)) return false;
      if (!key) return true;
      const haystack = [card.title, card.filename, card.note, card.tags.join(" ")]
        .join(" ")
        .toLowerCase();
      return haystack.includes(key);
    });
  }, [cards, keyword, tagFilter, showArchived]);

  const archivedCount = useMemo(
    () => cards.filter((card) => card.archived).length,
    [cards],
  );

  async function saveMeta(state: EditState) {
    setBusy(true);
    try {
      await apiPost(`/api/workflow-library/${state.id}/meta`, {
        title: state.title.trim(),
        note: state.note.trim(),
        tags: splitList(state.tags),
        favored: state.favored,
        pinned: state.pinned,
      });
      setEdit(null);
      await reload();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBusy(false);
    }
  }

  async function quickToggle(card: WorkflowCard, patch: Partial<EditState>) {
    setBusy(true);
    try {
      await apiPost(`/api/workflow-library/${card.id}/meta`, patch);
      await reload();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBusy(false);
    }
  }

  async function duplicate(card: WorkflowCard) {
    const withDrafts = window.confirm(
      `复制「${card.title}」\n\n确定 = 连草稿一起复制；取消 = 只复制工作流本身。`,
    );
    setBusy(true);
    try {
      await apiPost(`/api/workflow-library/${card.id}/duplicate`, {
        title: `${card.title} 副本`,
        with_drafts: withDrafts,
      });
      await reload();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBusy(false);
    }
  }

  async function toggleArchive(card: WorkflowCard) {
    setBusy(true);
    try {
      await apiPost(
        `/api/workflow-library/${card.id}/archive?archived=${!card.archived}`,
        {},
      );
      await reload();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBusy(false);
    }
  }

  async function remove(card: WorkflowCard) {
    if (
      !window.confirm(
        `彻底删除「${card.title}」？\n会连同草稿（${card.draft_count} 份）与版本快照（${card.version_count} 份）一起删除。\n生成历史保留，但不再关联这个工作流。`,
      )
    ) {
      return;
    }
    setBusy(true);
    try {
      await apiDelete(`/api/workflow-library/${card.id}`, {});
      await reload();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="asset-panel">
      <div className="section-heading">
        <div>
          <h2>工作流管理</h2>
          <p className="muted">
            已导入工作流的资料卡：改名、备注、标签、收藏置顶、复制副本、回收站与彻底删除。删除只动工作台数据，ComfyUI 里的原始文件不受影响。
          </p>
        </div>
      </div>

      {error && <div className="injection-error">{error}</div>}

      <div className="mg-toolbar">
        <input
          className="lora-filter-input"
          placeholder="搜索标题 / 文件名 / 备注 / 标签"
          value={keyword}
          onChange={(event) => setKeyword(event.target.value)}
        />
        <div className="mg-chips">
          <button className={`chip ${tagFilter === "" ? "active" : ""}`} onClick={() => setTagFilter("")}>
            全部标签
          </button>
          {tags.map((tag) => (
            <button
              key={tag}
              className={`chip ${tagFilter === tag ? "active" : ""}`}
              onClick={() => setTagFilter(tag)}
            >
              {tag}
            </button>
          ))}
        </div>
        <button className={`chip ${showArchived ? "active" : ""}`} onClick={() => setShowArchived((prev) => !prev)}>
          {showArchived ? "回收站" : "工作中"}（{showArchived ? archivedCount : visible.length}）
        </button>
      </div>

      {loading ? (
        <div className="muted">加载中…</div>
      ) : visible.length === 0 ? (
        <div className="mg-empty">
          <p>
            {showArchived
              ? "回收站是空的。"
              : cards.length === 0
                ? "还没有导入工作流，先在「工作流工作区」导入一个。"
                : "没有匹配筛选条件的工作流。"}
          </p>
        </div>
      ) : (
        <div className="asset-grid mg-grid">
          {visible.map((card) => (
            <article
              key={card.id}
              className={`asset-card wf-card ${card.pinned ? "is-pinned" : ""} ${card.archived ? "is-archived" : ""}`}
            >
              <div className="wf-card-head">
                <span className="wf-card-letter">{initialOf(card.title)}</span>
                <div className="wf-card-flags">
                  {card.pinned && <span className="chip static">置顶</span>}
                  {card.favored && <span className="chip static">收藏</span>}
                  <span className="asset-badge">{card.format === "api" ? "API" : "UI"}</span>
                </div>
              </div>
              <div className="asset-card-meta">
                <div className="asset-card-name" title={card.title}>
                  {card.title}
                </div>
                <div className="asset-card-sub">
                  <span className={`asset-badge ${card.confirmed ? "ok" : "warn"}`}>
                    {card.confirmed ? "已确认" : "未确认"}
                  </span>
                  <span className="muted">导入 {formatTime(card.imported_at)}</span>
                </div>
                {card.note && <div className="wf-note">{card.note}</div>}
                {card.tags.length > 0 && (
                  <div className="asset-card-tags">
                    {card.tags.slice(0, 4).map((tag) => (
                      <span key={tag} className="chip static">
                        {tag}
                      </span>
                    ))}
                  </div>
                )}
                <div className="wf-counts">
                  <span className="wf-shortcode">#{card.id.slice(0, 4)}</span>
                  <span>草稿 {card.draft_count}</span>
                  <span>版本 {card.version_count}</span>
                  <span>最近写入 {formatTime(card.last_written_at)}</span>
                </div>
                <div className="mg-actions">
                  <button onClick={() => setEdit({
                    id: card.id,
                    title: card.title,
                    note: card.note,
                    tags: card.tags.join(", "),
                    favored: card.favored,
                    pinned: card.pinned,
                  })}>
                    编辑
                  </button>
                  <button onClick={() => void quickToggle(card, { favored: !card.favored })}>
                    {card.favored ? "取消收藏" : "收藏"}
                  </button>
                  <a className="wf-export" href={`/api/workflows/${card.id}/export`} download>
                    导出
                  </a>
                  <button onClick={() => void duplicate(card)}>复制</button>
                  <button onClick={() => void toggleArchive(card)}>
                    {card.archived ? "恢复" : "归档"}
                  </button>
                  <button className="danger" onClick={() => void remove(card)}>
                    删除
                  </button>
                </div>
              </div>
            </article>
          ))}
        </div>
      )}

      {edit && (
        <div className="mg-modal" onClick={() => setEdit(null)}>
          <div className="mg-modal-body" onClick={(event) => event.stopPropagation()}>
            <div className="mg-modal-head">
              <h3>编辑工作流资料</h3>
              <button onClick={() => setEdit(null)}>关闭</button>
            </div>
            <div className="mg-form-grid">
              <label className="span2">
                标题
                <input
                  value={edit.title}
                  onChange={(event) =>
                    setEdit((prev) => (prev ? { ...prev, title: event.target.value } : prev))
                  }
                  maxLength={120}
                />
              </label>
              <label className="span2">
                备注
                <textarea
                  rows={4}
                  value={edit.note}
                  onChange={(event) =>
                    setEdit((prev) => (prev ? { ...prev, note: event.target.value } : prev))
                  }
                  maxLength={1000}
                  placeholder="这个工作流做什么、注意什么"
                />
              </label>
              <label className="span2">
                标签（逗号分隔）
                <input
                  value={edit.tags}
                  onChange={(event) =>
                    setEdit((prev) => (prev ? { ...prev, tags: event.target.value } : prev))
                  }
                  placeholder="人像, 半写实, 512"
                />
              </label>
              <label className="mg-check">
                <input
                  type="checkbox"
                  checked={edit.favored}
                  onChange={(event) =>
                    setEdit((prev) => (prev ? { ...prev, favored: event.target.checked } : prev))
                  }
                />
                收藏
              </label>
              <label className="mg-check">
                <input
                  type="checkbox"
                  checked={edit.pinned}
                  onChange={(event) =>
                    setEdit((prev) => (prev ? { ...prev, pinned: event.target.checked } : prev))
                  }
                />
                置顶
              </label>
            </div>
            <div className="mg-modal-foot">
              <button onClick={() => setEdit(null)}>取消</button>
              <button className="primary" disabled={busy} onClick={() => void saveMeta(edit)}>
                {busy ? "保存中…" : "保存"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
