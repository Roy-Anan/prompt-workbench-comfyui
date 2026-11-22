import { useEffect, useState } from "react";
import { apiDelete, apiGet, apiPost } from "./api";

type HistoryView = "list" | "grid" | "timeline";

const VIEW_OPTIONS: { key: HistoryView; label: string }[] = [
  { key: "list", label: "列表" },
  { key: "grid", label: "网格" },
  { key: "timeline", label: "时间轴" },
];

function readStoredView(): HistoryView {
  try {
    const value = localStorage.getItem("workbench.historyView");
    if (value === "list" || value === "grid" || value === "timeline") return value;
  } catch {
    // 本地存储不可用时回落到列表视图。
  }
  return "grid";
}

function readStoredFilter(key: string): string {
  try {
    return localStorage.getItem(key) || "";
  } catch {
    // 本地存储不可用时回落到全部。
  }
  return "";
}

function persistFilter(key: string, value: string): void {
  try {
    localStorage.setItem(key, value);
  } catch {
    // 忽略本地存储写入失败。
  }
}

type HistoryImage = {
  node_id: string;
  filename: string;
  subfolder: string;
  type: string;
  exists: boolean | null;
};

type ModelFile = { node_id: string; file: string };

type SamplerParams = Record<string, string | number | boolean | null>;

type ModelSnapshot = {
  checkpoints: ModelFile[];
  loras: ModelFile[];
  vaes: ModelFile[];
  clips: ModelFile[];
  resolution?: string | null;
  clip_skip?: number | null;
  samplers?: SamplerParams[];
};

type ModelFileKey = "checkpoints" | "loras" | "vaes" | "clips";

type LinkedDraft = { id: string; name: string };

type WorkflowBrief = { id: string; filename: string };

type SyncResult = {
  added: number;
  skipped: number;
  matched: number;
  unmatched: number;
};

type HistoryRecord = {
  kind?: "prompt";
  id?: string;
  prompt_id: string;
  workflow_id: string;
  created_at: string;
  positive_prompt: string | null;
  negative_prompt: string | null;
  model_snapshot: ModelSnapshot;
  task_status: string;
  images: HistoryImage[] | null;
  last_error: string | null;
  linked_draft: LinkedDraft | null;
};

type VisionAsset = { asset_id: number; name: string; exists: boolean };

type VisionRecord = {
  kind: "vision";
  id: string;
  created_at: string;
  model: string | null;
  prompt: string | null;
  description: string;
  assets: VisionAsset[];
};

type WritingRecord = {
  kind: "writing";
  id: string;
  created_at: string;
  model: string | null;
  persona_id: string | null;
  persona_name: string | null;
  genre: string | null;
  style: string | null;
  mode: string | null;
  task: string | null;
  output_text: string;
};

type AggregateRecord = HistoryRecord | VisionRecord | WritingRecord;

function isPromptRecord(record: AggregateRecord): record is HistoryRecord {
  return record.kind !== "vision" && record.kind !== "writing";
}

function recordKey(record: AggregateRecord): string {
  return `${record.kind ?? "prompt"}/${record.id ?? (record as HistoryRecord).prompt_id}`;
}

const STATUS_LABEL: Record<string, string> = {
  submitted: "待运行",
  running: "运行中",
  succeeded: "已完成",
  failed: "失败",
  interrupted: "已中断",
  unknown: "未知",
};

function formatTime(value: string): string {
  if (!value) return "—";
  return value.replace("T", " ").slice(0, 16);
}

function snapshotFiles(snapshot: ModelSnapshot, key: ModelFileKey): string[] {
  return (snapshot[key] || []).map((entry) => entry.file).filter(Boolean);
}

function samplerSummary(samplers: SamplerParams[] | undefined): string {
  if (!samplers || samplers.length === 0) return "—";
  return samplers
    .map((params) => {
      const parts = [
        params.steps != null ? `Steps ${params.steps}` : "",
        params.cfg != null ? `CFG ${params.cfg}` : "",
        params.sampler_name ? String(params.sampler_name) : "",
        params.scheduler ? String(params.scheduler) : "",
      ].filter(Boolean);
      return parts.join(" / ");
    })
    .filter(Boolean)
    .join("；") || "—";
}

function existenceLabel(image: HistoryImage): string {
  if (image.exists === null) return "目录未配置";
  return image.exists ? "存在" : "丢失";
}

function historyImageUrl(image: HistoryImage): string {
  const params = new URLSearchParams({
    filename: image.filename,
    subfolder: image.subfolder || "",
  });
  return `/api/history/image?${params.toString()}`;
}

// In-memory blob-URL cache: an already fetched output image keeps one
// object URL for both thumbnails and the lightbox, so reopening a record
// does not refetch the original file. No server-side resampling is done;
// the original bytes are reused as-is.
const objectUrlCache = new Map<string, string>();

async function cachedObjectUrl(url: string): Promise<string> {
  const cached = objectUrlCache.get(url);
  if (cached) return cached;
  const response = await fetch(url, { credentials: "include" });
  if (!response.ok) throw new Error(`图片加载失败（${response.status}）`);
  const blob = await response.blob();
  const objectUrl = URL.createObjectURL(blob);
  objectUrlCache.set(url, objectUrl);
  return objectUrl;
}

type LightboxState = {
  images: HistoryImage[];
  index: number;
};

function summarize(text: string | null, limit = 80): string {
  if (!text) return "—";
  return text.length > limit ? `${text.slice(0, limit)}…` : text;
}

function recordHasPrompt(record: HistoryRecord): boolean {
  return Boolean(record.positive_prompt?.trim() || record.negative_prompt?.trim());
}

function autoDraftName(record: HistoryRecord): string {
  const date = record.created_at.slice(5, 10) || "历史";
  const source = (record.positive_prompt || record.negative_prompt || "").trim();
  const summary = source.length > 30 ? `${source.slice(0, 30)}…` : source;
  return `${date} ${summary}`.trim().slice(0, 120);
}

function groupByDay(records: AggregateRecord[]): { day: string; items: AggregateRecord[] }[] {
  const sorted = [...records].sort((a, b) =>
    a.created_at < b.created_at ? 1 : -1,
  );
  const groups: { day: string; items: AggregateRecord[] }[] = [];
  const index = new Map<string, number>();
  for (const record of sorted) {
    const day = record.created_at.slice(0, 10) || "未知日期";
    const existing = index.get(day);
    if (existing === undefined) {
      index.set(day, groups.length);
      groups.push({ day, items: [record] });
    } else {
      groups[existing].items.push(record);
    }
  }
  return groups;
}

const STATUS_OPTIONS = [
  "submitted",
  "running",
  "succeeded",
  "failed",
  "interrupted",
  "unknown",
];

type ComparePick = { record: HistoryRecord; image: HistoryImage };

function HistoryThumb({
  image,
  onOpen,
  onCompare,
  picked,
}: {
  image: HistoryImage;
  onOpen: () => void;
  onCompare?: () => void;
  picked?: boolean;
}) {
  const url = historyImageUrl(image);
  const [objectUrl, setObjectUrl] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    cachedObjectUrl(url)
      .then((cached) => {
        if (alive) setObjectUrl(cached);
      })
      .catch(() => {
        // 缓存 blob 不可用时回退到代理原图 URL。
      });
    return () => {
      alive = false;
    };
  }, [url]);

  return (
    <figure>
      <img
        src={objectUrl ?? url}
        alt={image.filename}
        loading="lazy"
        onClick={onOpen}
        title="点击查看大图"
      />
      <figcaption>{image.filename}</figcaption>
      {onCompare && (
        <button className="secondary compare-pick" onClick={onCompare}>
          {picked ? "移出对比" : "加入对比"}
        </button>
      )}
    </figure>
  );
}

function CompareImage({ pick, label }: { pick: ComparePick; label: string }) {
  const url = historyImageUrl(pick.image);
  const [objectUrl, setObjectUrl] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    cachedObjectUrl(url)
      .then((cached) => {
        if (alive) setObjectUrl(cached);
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, [url]);

  return (
    <figure className="compare-figure">
      <img src={objectUrl ?? url} alt={pick.image.filename} />
      <figcaption>
        {label} · {formatTime(pick.record.created_at)} · {pick.image.filename}
      </figcaption>
    </figure>
  );
}

function CompareViewer({
  picks,
  onClose,
}: {
  picks: [ComparePick, ComparePick];
  onClose: () => void;
}) {
  return (
    <div
      className="lightbox-overlay"
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <button className="lightbox-close" onClick={onClose} aria-label="关闭对比">
        ×
      </button>
      <div className="compare-grid">
        <CompareImage pick={picks[0]} label="左" />
        <CompareImage pick={picks[1]} label="右" />
      </div>
    </div>
  );
}

function LightboxViewer({
  state,
  onClose,
  onStep,
}: {
  state: LightboxState;
  onClose: () => void;
  onStep: (delta: number) => void;
}) {
  const image = state.images[state.index];
  const url = historyImageUrl(image);
  const [objectUrl, setObjectUrl] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setObjectUrl(null);
    cachedObjectUrl(url)
      .then((cached) => {
        if (alive) setObjectUrl(cached);
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, [url]);

  return (
    <div
      className="lightbox-overlay"
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <button className="lightbox-close" onClick={onClose} aria-label="关闭大图">
        ×
      </button>
      {state.images.length > 1 && (
        <button
          className="lightbox-nav lightbox-prev"
          onClick={() => onStep(-1)}
          aria-label="上一张"
        >
          ‹
        </button>
      )}
      <figure className="lightbox-figure">
        <img src={objectUrl ?? url} alt={image.filename} />
        <figcaption>
          {state.index + 1} / {state.images.length} · {image.filename}
        </figcaption>
      </figure>
      {state.images.length > 1 && (
        <button
          className="lightbox-nav lightbox-next"
          onClick={() => onStep(1)}
          aria-label="下一张"
        >
          ›
        </button>
      )}
    </div>
  );
}

type HistoryKind = "all" | "prompt" | "vision" | "writing";

const KIND_OPTIONS: { key: HistoryKind; label: string }[] = [
  { key: "all", label: "全部" },
  { key: "prompt", label: "提示词" },
  { key: "vision", label: "图生文" },
  { key: "writing", label: "写作" },
];

export default function HistoryPanel() {
  const [items, setItems] = useState<AggregateRecord[] | null>(null);
  const [counts, setCounts] = useState<{
    prompt: number;
    vision: number;
    writing: number;
  }>({ prompt: 0, vision: 0, writing: 0 });
  const [kindFilter, setKindFilter] = useState<HistoryKind>("all");
  const [error, setError] = useState("");
  const [openId, setOpenId] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState(() => readStoredFilter("workbench.historyStatus"));
  const [workflowFilter, setWorkflowFilter] = useState(() => readStoredFilter("workbench.historyWorkflow"));
  const [keyword, setKeyword] = useState(() => readStoredFilter("workbench.historyKeyword"));
  const [fromDate, setFromDate] = useState(() => readStoredFilter("workbench.historyFrom"));
  const [toDate, setToDate] = useState(() => readStoredFilter("workbench.historyTo"));
  const [view, setView] = useState<HistoryView>(readStoredView);
  const [draftingId, setDraftingId] = useState<string | null>(null);
  const [draftName, setDraftName] = useState("");
  const [draftBusy, setDraftBusy] = useState(false);
  const [savedIds, setSavedIds] = useState<string[]>([]);
  const [draftMessage, setDraftMessage] = useState<{
    id: string;
    kind: "ok" | "err";
    text: string;
  } | null>(null);
  const [lightbox, setLightbox] = useState<LightboxState | null>(null);
  const [comparePicks, setComparePicks] = useState<ComparePick[]>([]);
  const [compareOpen, setCompareOpen] = useState(false);
  const [confirmDeleteKey, setConfirmDeleteKey] = useState<string | null>(null);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const [workflowBriefs, setWorkflowBriefs] = useState<WorkflowBrief[]>([]);
  const [syncBusy, setSyncBusy] = useState(false);
  const [syncMessage, setSyncMessage] = useState<{
    kind: "ok" | "err";
    text: string;
  } | null>(null);

  function openLightbox(images: HistoryImage[], index: number) {
    setLightbox({ images, index });
  }

  function pickKey(pick: ComparePick): string {
    return `${pick.record.prompt_id}/${pick.image.subfolder}/${pick.image.filename}`;
  }

  function toggleCompare(pick: ComparePick) {
    setComparePicks((prev) => {
      const key = pickKey(pick);
      const exists = prev.some((entry) => pickKey(entry) === key);
      if (exists) {
        return prev.filter((entry) => pickKey(entry) !== key);
      }
      const next = [...prev, pick];
      return next.length > 2 ? next.slice(next.length - 2) : next;
    });
  }

  function closeLightbox() {
    setLightbox(null);
  }

  function stepLightbox(delta: number) {
    setLightbox((prev) => {
      if (!prev || prev.images.length === 0) return prev;
      const next = (prev.index + delta + prev.images.length) % prev.images.length;
      return { ...prev, index: next };
    });
  }

  useEffect(() => {
    if (!lightbox && !compareOpen) return;
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        closeLightbox();
        setCompareOpen(false);
      } else if (lightbox && event.key === "ArrowLeft") {
        stepLightbox(-1);
      } else if (lightbox && event.key === "ArrowRight") {
        stepLightbox(1);
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [lightbox, compareOpen]);

  function switchView(next: HistoryView) {
    setView(next);
    try {
      localStorage.setItem("workbench.historyView", next);
    } catch {
      // 忽略本地存储写入失败。
    }
  }

  async function load() {
    try {
      const body = await apiGet<{
        items: AggregateRecord[];
        counts: { prompt: number; vision: number; writing: number };
      }>(`/api/history/all?kind=${kindFilter}`);
      setItems(body.items);
      setCounts(body.counts);
      try {
        const wfBody = await apiGet<{ items: WorkflowBrief[] }>("/api/workflows");
        setWorkflowBriefs(wfBody.items ?? []);
      } catch {
        // Workflow list is only used for the rebind dropdown; keep history intact.
        setWorkflowBriefs([]);
      }
      let jump: string | null = null;
      try {
        jump = localStorage.getItem("workbench.jumpHistory");
      } catch {
        // 忽略本地存储读取失败。
      }
      if (jump) {
        if (body.items.some((record) => recordKey(record) === `prompt/${jump}`)) {
          setOpenId(jump);
        }
        try {
          localStorage.removeItem("workbench.jumpHistory");
        } catch {
          // 忽略本地存储写入失败。
        }
      }
      setError("");
    } catch (err) {
      setError(`读取历史失败：${String(err)}`);
      setItems([]);
    }
  }

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [kindFilter]);

  function startDraft(record: HistoryRecord) {
    setDraftingId(record.prompt_id);
    setDraftName(autoDraftName(record));
    setDraftMessage(null);
  }

  function cancelDraft() {
    setDraftingId(null);
    setDraftName("");
    setDraftMessage(null);
  }

  async function saveAsDraft(record: HistoryRecord) {
    const name = draftName.trim();
    if (!name) {
      setDraftMessage({ id: record.prompt_id, kind: "err", text: "请填写草稿名称。" });
      return;
    }
    if (!recordHasPrompt(record)) {
      setDraftMessage({
        id: record.prompt_id,
        kind: "err",
        text: "这条历史没有可保存的 Prompt。",
      });
      return;
    }
    setDraftBusy(true);
    setDraftMessage(null);
    try {
      const note = `来自历史 ${record.prompt_id} · ${formatTime(record.created_at)}`;
      await apiPost(`/api/workflows/${record.workflow_id}/drafts`, {
        name,
        positive: record.positive_prompt ?? "",
        negative: record.negative_prompt ?? "",
        note,
        source_history_id: record.prompt_id,
      });
      setSavedIds((prev) =>
        prev.includes(record.prompt_id) ? prev : [...prev, record.prompt_id],
      );
      setDraftingId(null);
      setDraftName("");
      setDraftMessage({
        id: record.prompt_id,
        kind: "ok",
        text: "已存为草稿（未写入工作副本）。",
      });
    } catch (err) {
      setDraftMessage({
        id: record.prompt_id,
        kind: "err",
        text: `保存草稿失败：${String(err)}`,
      });
    } finally {
      setDraftBusy(false);
    }
  }

  async function removeRecord(record: AggregateRecord) {
    if (deleteBusy) return;
    const kind = record.kind ?? "prompt";
    const id = record.id ?? (record as HistoryRecord).prompt_id;
    const key = `${kind}/${id}`;
    if (confirmDeleteKey !== key) {
      setConfirmDeleteKey(key);
      return;
    }
    setDeleteBusy(true);
    try {
      await apiDelete(`/api/history/${kind}/${id}`, {});
      setConfirmDeleteKey(null);
      await load();
    } catch (err) {
      setError(`删除失败：${String(err)}`);
    } finally {
      setDeleteBusy(false);
    }
  }

  async function syncCanvas() {
    if (syncBusy) return;
    setSyncBusy(true);
    setSyncMessage(null);
    try {
      const result = await apiPost<SyncResult>("/api/history/sync", {});
      setSyncMessage({
        kind: "ok",
        text: `同步完成：新增 ${result.added} 条（归属 ${result.matched}，未归属 ${result.unmatched}，已存在跳过 ${result.skipped}）。`,
      });
      await load();
    } catch (err) {
      setSyncMessage({ kind: "err", text: `同步画布记录失败：${String(err)}` });
    } finally {
      setSyncBusy(false);
    }
  }

  async function rebindRecord(record: HistoryRecord, targetId: string) {
    try {
      await apiPost(`/api/history/${record.prompt_id}/workflow`, {
        workflow_id: targetId,
      });
      setItems((prev) =>
        (prev || []).map((entry) =>
          isPromptRecord(entry) && entry.prompt_id === record.prompt_id
            ? { ...entry, workflow_id: targetId }
            : entry,
        ),
      );
    } catch (err) {
      setError(`改挂工作流失败：${String(err)}`);
    }
  }

  function workflowLabel(id: string): string {
    const brief = workflowBriefs.find((item) => item.id === id);
    return brief ? brief.filename : id.slice(0, 8);
  }

  function goToWorkbench() {
    window.dispatchEvent(
      new CustomEvent("workbench:navigate", { detail: { page: "workflows" } }),
    );
  }

  const showPromptFilters = kindFilter === "all" || kindFilter === "prompt";

  const workflowOptions = workflowBriefs.map((brief) => brief.id);

  function haystackOf(record: AggregateRecord): string {
    if (isPromptRecord(record)) {
      return [
        record.positive_prompt,
        record.negative_prompt,
        JSON.stringify(record.model_snapshot),
      ]
        .join(" ")
        .toLowerCase();
    }
    if (record.kind === "vision") {
      return [record.prompt, record.description, record.model, ...record.assets.map((a) => a.name)]
        .join(" ")
        .toLowerCase();
    }
    return [
      record.persona_name,
      record.genre,
      record.style,
      record.mode,
      record.task,
      record.output_text,
      record.model,
    ]
      .join(" ")
      .toLowerCase();
  }

  function exportHistory() {
    const payload = {
      exported_at: new Date().toISOString(),
      count: filtered.length,
      records: filtered,
    };
    const blob = new Blob([JSON.stringify(payload, null, 2)], {
      type: "application/json;charset=utf-8",
    });
    const url = URL.createObjectURL(blob);
    const stamp = new Date().toISOString().replace(/[-:T]/g, "").slice(0, 12);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `workbench-history-${stamp}.json`;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(url);
  }

  const keywordTerm = keyword.trim().toLowerCase();
  const filtered = (items || []).filter((record) => {
    if (isPromptRecord(record)) {
      if (statusFilter && record.task_status !== statusFilter) return false;
      if (workflowFilter && record.workflow_id !== workflowFilter) return false;
      const day = record.created_at.slice(0, 10);
      if (fromDate && day < fromDate) return false;
      if (toDate && day > toDate) return false;
    }
    if (keywordTerm && !haystackOf(record).includes(keywordTerm)) return false;
    return true;
  });

// 旧版英文 slug 回显；新记录直接存中文规范名，未命中时原样显示。
const WRITING_MODE_LABEL: Record<string, string> = {
  draft: "起草",
  polish: "润色",
  trim: "精简",
  bilingual: "中英对照",
  continue: "续写",
  fresh: "空白直出",
  negative: "负向词",
};

function assetImageUrl(assetId: number): string {
  return `/api/assets/image?asset_id=${assetId}`;
}

function deleteButton(record: AggregateRecord) {
  const key = recordKey(record);
  const confirming = confirmDeleteKey === key;
  return (
    <button
      className={confirming ? "workflow-warning" : "secondary"}
      disabled={deleteBusy}
      onClick={() => void removeRecord(record)}
      title="彻底删除这条历史记录；不影响草稿、素材原图与出图文件"
    >
      {confirming ? "确认删除？" : "删除"}
    </button>
  );
}

function renderVisionRecord(record: VisionRecord) {
  const open = openId === record.id;
  return (
    <div className="rule-row" key={recordKey(record)}>
      <div className="rule-row-main">
        <strong>{summarize(record.description)}</strong>
        <small>
          {formatTime(record.created_at)}
          {record.model ? ` · ${record.model}` : ""}
        </small>
        {record.prompt && <small className="muted">提问：{record.prompt}</small>}
      </div>
      <div className="rule-row-meta">
        <span className="compare-badge">图生文</span>
        <button onClick={() => setOpenId(open ? null : record.id)}>
          {open ? "收起" : "详情"}
        </button>
        {deleteButton(record)}
      </div>
      {open && (
        <div className="profile-preview" style={{ width: "100%" }}>
          <div className="rule-row">
            <div className="rule-row-main">
              <strong>引用素材</strong>
              <div className="history-images">
                {record.assets.map((asset) =>
                  asset.exists ? (
                    <figure key={asset.asset_id}>
                      <img
                        src={assetImageUrl(asset.asset_id)}
                        alt={asset.name}
                        loading="lazy"
                      />
                      <figcaption>{asset.name}</figcaption>
                    </figure>
                  ) : (
                    <figure key={asset.asset_id}>
                      <small className="muted">{asset.name}（素材已失效）</small>
                    </figure>
                  ),
                )}
              </div>
            </div>
          </div>
          <div className="rule-row">
            <div className="rule-row-main">
              <strong>识别描述</strong>
              <small>{record.description}</small>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function renderWritingRecord(record: WritingRecord) {
  const open = openId === record.id;
  const tags = [
    record.persona_name,
    record.genre,
    record.style,
    record.mode ? WRITING_MODE_LABEL[record.mode] ?? record.mode : null,
  ].filter(Boolean);
  return (
    <div className="rule-row" key={recordKey(record)}>
      <div className="rule-row-main">
        <strong>{summarize(record.output_text)}</strong>
        <small>
          {formatTime(record.created_at)}
          {record.model ? ` · ${record.model}` : ""}
          {tags.length > 0 ? ` · ${tags.join("、")}` : ""}
        </small>
        {record.task && <small className="muted">任务：{record.task}</small>}
      </div>
      <div className="rule-row-meta">
        <span className="compare-badge">写作</span>
        <button onClick={() => setOpenId(open ? null : record.id)}>
          {open ? "收起" : "详情"}
        </button>
        {deleteButton(record)}
      </div>
      {open && (
        <div className="profile-preview" style={{ width: "100%" }}>
          {(record.persona_name || record.genre || record.style) && (
            <div className="rule-row">
              <div className="rule-row-main">
                <strong>设定</strong>
                <small>
                  {[
                    `身份：${record.persona_name || "裸模型"}`,
                    `题材：${record.genre || "—"}`,
                    `文风：${record.style || "—"}`,
                  ].join(" · ")}
                </small>
              </div>
            </div>
          )}
          {record.task && (
            <div className="rule-row">
              <div className="rule-row-main">
                <strong>任务要求</strong>
                <small>{record.task}</small>
              </div>
            </div>
          )}
          <div className="rule-row">
            <div className="rule-row-main">
              <strong>成文内容</strong>
              <small>{record.output_text}</small>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function renderRecord(record: AggregateRecord) {
  if (!isPromptRecord(record)) {
    return record.kind === "vision" ? (
      renderVisionRecord(record)
    ) : (
      renderWritingRecord(record)
    );
  }
  const open = openId === record.prompt_id;
  const checkpoints = snapshotFiles(record.model_snapshot, "checkpoints");
  const loras = snapshotFiles(record.model_snapshot, "loras");
  const images = record.images || [];
  return (
    <div className="rule-row" key={record.prompt_id}>
      <div className="rule-row-main">
        <strong>{summarize(record.positive_prompt)}</strong>
        <small>
          {formatTime(record.created_at)} ·{" "}
          {record.workflow_id ? `工作流 ${workflowLabel(record.workflow_id)}` : "画布直接生成"}
          {checkpoints.length > 0 && ` · ${checkpoints.join("、")}`}
          {loras.length > 0 && ` · LoRA ${loras.join("、")}`}
        </small>
        {record.last_error && (
          <small className="workflow-warning">{record.last_error}</small>
        )}
      </div>
      <div className="rule-row-meta">
        <span className={`compare-badge ${record.task_status}`}>
          {STATUS_LABEL[record.task_status] ?? record.task_status}
        </span>
        <button
          onClick={() => setOpenId(open ? null : record.prompt_id)}
        >
          {open ? "收起" : "详情"}
        </button>
        {deleteButton(record)}
      </div>

        {open && (
          <div className="profile-preview" style={{ width: "100%" }}>
            <div className="rule-row">
              <div className="rule-row-main">
                <strong>正向 Prompt</strong>
                <small>{record.positive_prompt || "—"}</small>
              </div>
            </div>
            <div className="rule-row">
              <div className="rule-row-main">
                <strong>负向 Prompt</strong>
                <small>{record.negative_prompt || "—"}</small>
              </div>
            </div>
            <div className="rule-row">
              <div className="rule-row-main">
                <strong>模型快照</strong>
                <small>
                  {[
                    `Checkpoint：${checkpoints.join("、") || "—"}`,
                    `LoRA：${loras.join("、") || "—"}`,
                    `VAE：${snapshotFiles(record.model_snapshot, "vaes").join("、") || "—"}`,
                    `CLIP：${snapshotFiles(record.model_snapshot, "clips").join("、") || "—"}`,
                  ].join(" · ")}
                </small>
              </div>
            </div>
            <div className="rule-row">
              <div className="rule-row-main">
                <strong>采样参数</strong>
                <small>
                  {[
                    `分辨率：${record.model_snapshot.resolution || "—"}`,
                    `Clip Skip：${
                      record.model_snapshot.clip_skip ?? "—"
                    }`,
                    `采样器：${samplerSummary(record.model_snapshot.samplers)}`,
                  ].join(" · ")}
                </small>
              </div>
            </div>
            <div className="rule-row">
              <div className="rule-row-main">
                <strong>归属工作流</strong>
                <select
                  value={record.workflow_id}
                  onChange={(event) => void rebindRecord(record, event.target.value)}
                >
                  <option value="">画布直接生成（不归属）</option>
                  {workflowBriefs.map((brief) => (
                    <option key={brief.id} value={brief.id}>
                      {brief.filename}
                    </option>
                  ))}
                </select>
              </div>
            </div>
            {record.linked_draft && (
              <div className="rule-row">
                <div className="rule-row-main">
                  <strong>关联草稿</strong>
                  <small>
                    <button
                      className="secondary"
                      onClick={goToWorkbench}
                      title="跳转到工作流工作区查看草稿"
                    >
                      {record.linked_draft.name}
                    </button>
                  </small>
                </div>
              </div>
            )}
            {images.length > 0 && (
              <div className="rule-row">
                <div className="rule-row-main">
                  <strong>输出图片</strong>
                  <div className="history-images">
                    {images.map((image, index) => {
                      const pick: ComparePick = { record, image };
                      const picked = comparePicks.some(
                        (entry) => pickKey(entry) === pickKey(pick),
                      );
                      return image.exists === true ? (
                        <HistoryThumb
                          key={`${image.subfolder}/${image.filename}`}
                          image={image}
                          onOpen={() => openLightbox(images, index)}
                          onCompare={() => toggleCompare(pick)}
                          picked={picked}
                        />
                      ) : (
                        <figure key={`${image.subfolder}/${image.filename}`}>
                          <small className="muted">
                            {image.filename}（{existenceLabel(image)}）
                          </small>
                          <figcaption>{image.filename}</figcaption>
                        </figure>
                      );
                    })}
                  </div>
                </div>
              </div>
            )}
            <div className="rule-row">
              <div className="rule-row-main">
                {savedIds.includes(record.prompt_id) ? (
                  <small className="workflow-meta">已存为草稿</small>
                ) : !recordHasPrompt(record) ? (
                  <small className="muted">无 Prompt，无法存为草稿</small>
                ) : draftingId === record.prompt_id ? (
                  <div className="actions">
                    <input
                      type="text"
                      placeholder="草稿名称"
                      value={draftName}
                      disabled={draftBusy}
                      onChange={(event) => setDraftName(event.target.value)}
                    />
                    <button
                      onClick={() => void saveAsDraft(record)}
                      disabled={draftBusy}
                    >
                      确认保存
                    </button>
                    <button onClick={cancelDraft} disabled={draftBusy}>
                      取消
                    </button>
                  </div>
                ) : (
                  <button
                    onClick={() => startDraft(record)}
                    disabled={draftBusy}
                  >
                    存为草稿
                  </button>
                )}
                {draftMessage && draftMessage.id === record.prompt_id && (
                  <small
                    className={
                      draftMessage.kind === "ok"
                        ? "workflow-meta"
                        : "workflow-warning"
                    }
                  >
                    {draftMessage.text}
                  </small>
                )}
              </div>
            </div>
          </div>
        )}
      </div>
    );
  }

  return (
    <section className="panel">
      <div className="section-heading">
        <div>
          <p>汇总提示词出图、图生文与写作三类历史；图片只记录引用与存在性，不复制原图，删除为彻底删除。</p>
        </div>
        <div className="actions">
          {VIEW_OPTIONS.map((option) => (
            <button
              key={option.key}
              onClick={() => switchView(option.key)}
              disabled={filtered.length === 0}
              className={view === option.key ? "active" : ""}
            >
              {option.label}视图
            </button>
          ))}
          <button
            onClick={() => setCompareOpen(true)}
            disabled={comparePicks.length !== 2}
            title="先在两张图片上点「加入对比」，再并排查看"
          >
            并排对比（{comparePicks.length}/2）
          </button>
          <button onClick={exportHistory} disabled={filtered.length === 0}>
            导出 JSON
          </button>
          <button onClick={() => void syncCanvas()} disabled={syncBusy}>
            {syncBusy ? "同步中…" : "同步画布记录"}
          </button>
          <button onClick={() => void load()} disabled={items === null}>
            刷新
          </button>
        </div>
      </div>

      {syncMessage && (
        <p className={syncMessage.kind === "ok" ? "workflow-meta" : "workflow-warning"}>
          {syncMessage.text}
        </p>
      )}

      <div className="history-kind-chips">
        {KIND_OPTIONS.map((option) => {
          const total =
            option.key === "all"
              ? counts.prompt + counts.vision + counts.writing
              : counts[option.key];
          return (
            <button
              key={option.key}
              className={kindFilter === option.key ? "active" : ""}
              onClick={() => setKindFilter(option.key)}
            >
              {option.label}（{total}）
            </button>
          );
        })}
      </div>

      {error && <p className="workflow-warning">{error}</p>}

      {items !== null && items.length > 0 && (
        <div className="history-filters">
          {showPromptFilters && (
            <>
          <label>
            状态
            <select
              value={statusFilter}
              onChange={(event) => {
                setStatusFilter(event.target.value);
                persistFilter("workbench.historyStatus", event.target.value);
              }}
            >
              <option value="">全部</option>
              {STATUS_OPTIONS.map((status) => (
                <option key={status} value={status}>
                  {STATUS_LABEL[status] ?? status}
                </option>
              ))}
            </select>
          </label>
          {workflowOptions.length > 1 && (
            <label>
              工作流
              <select
                value={workflowFilter}
                onChange={(event) => {
                  setWorkflowFilter(event.target.value);
                  persistFilter("workbench.historyWorkflow", event.target.value);
                }}
              >
                <option value="">全部</option>
                {workflowOptions.map((workflowId) => (
                  <option key={workflowId} value={workflowId}>
                    {workflowLabel(workflowId)}
                  </option>
                ))}
              </select>
            </label>
          )}
          <label>
            开始日期
            <input
              type="date"
              value={fromDate}
              onChange={(event) => {
                setFromDate(event.target.value);
                persistFilter("workbench.historyFrom", event.target.value);
              }}
            />
          </label>
          <label>
            结束日期
            <input
              type="date"
              value={toDate}
              onChange={(event) => {
                setToDate(event.target.value);
                persistFilter("workbench.historyTo", event.target.value);
              }}
            />
          </label>
            </>
          )}
          <label>
            关键词
            <input
              type="text"
              value={keyword}
              placeholder="Prompt、描述或成文内容"
              onChange={(event) => {
                setKeyword(event.target.value);
                persistFilter("workbench.historyKeyword", event.target.value);
              }}
            />
          </label>
        </div>
      )}

      {items === null ? (
        <p>正在读取历史…</p>
      ) : items.length === 0 ? (
        <p className="catalog-empty">暂无生成历史。</p>
      ) : filtered.length === 0 ? (
        <p className="catalog-empty">没有符合筛选条件的历史记录。</p>
      ) : view === "timeline" ? (
        <div className="history-timeline">
          {groupByDay(filtered).map((group) => (
            <div className="timeline-group" key={group.day}>
              <div className="timeline-date">{group.day}</div>
              <div className="rule-list">
                {group.items.map(renderRecord)}
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div className={view === "grid" ? "history-grid" : "rule-list"}>
          {filtered.map(renderRecord)}
        </div>
      )}

      {lightbox && (
        <LightboxViewer
          state={lightbox}
          onClose={closeLightbox}
          onStep={stepLightbox}
        />
      )}

      {compareOpen && comparePicks.length === 2 && (
        <CompareViewer
          picks={[comparePicks[0], comparePicks[1]]}
          onClose={() => setCompareOpen(false)}
        />
      )}
    </section>
  );
}
