import { Fragment, useEffect, useRef, useState } from "react";
import { apiDelete, apiGet, apiPost } from "./api";
import {
  countPromptFragments,
  diffFragments,
  estimatePromptTokens,
  formatActual,
  splitPromptFragments,
} from "./format";
type PromptNode = {
  node_id: string;
  class_type: string;
  roles: string[];
  text: string;
  editable: boolean;
};

type CandidateScope = "positive" | "negative";

type CandidateItem = {
  text: string;
  mode: string;
  scope: CandidateScope;
};
type Analysis = {
  format: "api" | "ui";
  node_count: number;
  model_nodes: Array<{
    node_id: string;
    class_type: string;
    inputs: Record<string, unknown>;
  }>;
  lora_nodes: Array<{
    node_id: string;
    class_type: string;
    inputs: Record<string, unknown>;
  }>;
  prompt_groups: Array<{
    sampler_node_id: string;
    sampler_class: string;
    positive_node_ids: string[];
    negative_node_ids: string[];
  }>;
  prompt_nodes: PromptNode[];
  safe_prompt_write_supported: boolean;
  notes: string[];
};
type WorkflowListItem = {
  id: string;
  filename: string;
  format: "api" | "ui";
  imported_at: string;
  confirmed: boolean;
};
type WorkflowDetail = {
  manifest: {
    id: string;
    filename: string;
    format: "api" | "ui";
    confirmed: boolean;
    whitelist: Array<{
      node_id: string;
      role: "positive" | "negative";
    }>;
  };
  report: Analysis;
  working_prompts: Array<{
    node_id: string;
    role: "positive" | "negative";
    text: string;
  }>;
};
type VersionEntry = {
  stamp: string;
  prompts: Array<{
    node_id: string;
    role: "positive" | "negative";
    text: string;
  }>;
};
type ImportResult = {
  id: string;
  filename: string;
  format: "api" | "ui";
  report: Analysis;
  duplicate?: boolean;
};
type WriteResult = {
  ok: boolean;
  changes: Array<{
    node_id: string;
    role: string;
    old_text: string;
    new_text: string;
  }>;
  workflow: WorkflowDetail;
};
type ComparisonEntry = {
  key: string;
  rule_status: string;
  actual_key: string | null;
  actual: unknown;
  status: string;
  reason?: string;
  rule?: unknown;
  set_role?: "specific" | "general";
  set_index?: number;
  fell_back?: boolean;
  general_checked?: boolean;
};
type GroupComparison = {
  sampler_node_id: string | null;
  params: Record<string, unknown>;
  comparisons: ComparisonEntry[];
  counts: Record<string, number>;
};
type ConflictSource = {
  level: string;
  scope_key: string | null;
  provenance: string;
  confidence: string;
  version: string;
  note: string;
};
type ConflictEntry = {
  key: string;
  message: string | null;
  sources: ConflictSource[];
};
type ComparePayload = {
  status: string;
  checkpoint: string | null;
  architecture: string | null;
  architecture_family?: string | null;
  architecture_label?: string | null;
  actuals: Record<string, unknown>;
  comparisons: ComparisonEntry[];
  counts: Record<string, number>;
  conflicts?: string[];
  unknown?: string[];
  conflict_entries?: ConflictEntry[];
  sampler_group_comparisons?: GroupComparison[];
};
type RoleChoice = "" | "positive" | "negative";
type OllamaModelsResponse = {
  online: boolean;
  models: string[];
  model_details?: Record<string, string>;
  error: string | null;
};
type OllamaModelDetail = { name: string; params: string };
type CandidateResponse = {
  model: string;
  mode: string;
  candidates: string[];
  rule_status: string;
  conflicts: unknown[];
  unknown: unknown[];
  persona_id?: string | null;
  persona_name?: string | null;
  persona_kind?: string | null;
  genre?: string | null;
  style?: string | null;
  selected_profile_id?: string | null;
  selected_profile_name?: string | null;
  profile_match_source?: string;
  profile_version?: string | null;
  fallback_reason?: string | null;
  profile_warning?: string | null;
  architecture?: string | null;
  system_prompt_layers?: {
    core: boolean;
    profile: boolean;
    workflow: boolean;
    genre: boolean;
    style: boolean;
  };
};
type Persona = {
  id: string;
  display_name: string;
  kind: "prompt" | "writing" | string;
  enabled?: boolean;
  exists?: boolean;
};
type LayerItem = { id: string; display_name: string; exists?: boolean };
type ModeItem = {
  id: string;
  display_name: string;
  exists?: boolean;
  skip_existing?: boolean;
  negative_output?: boolean;
};
type PersonasResponse = { items: Persona[] };
type LayersResponse = { items: LayerItem[] };
type ModesResponse = { items: ModeItem[] };
type PromptProfile = {
  id: string;
  display_name: string;
  description: string;
  architecture: string;
  enabled: boolean;
  version: string;
  auto_eligible: boolean;
  errors: string[];
};
type PromptProfilesResponse = { items: PromptProfile[] };
type SnapshotFile = { node_id?: string; file: string };

type EnvSnapshot = {
  checkpoints?: SnapshotFile[];
  loras?: SnapshotFile[];
  vaes?: SnapshotFile[];
  clips?: SnapshotFile[];
  resolution?: string | null;
  clip_skip?: number | null;
  samplers?: Record<string, unknown>[];
};

type ReleaseVramResult = {
  ollama_reachable: boolean;
  ollama_stopped: string[];
  ollama_errors: string[];
  ollama_still_resident: string[];
  comfyui_freed: boolean;
  comfyui_error: string | null;
};

type ExportWorkflowPayload = {
  filename: string;
  workflow: Record<string, unknown>;
};

type DraftImage = {
  filename: string;
  subfolder: string;
  exists: boolean | null;
  prompt_id: string | null;
};

type DraftEntry = {
  id: string;
  name: string;
  positive: string;
  negative: string;
  note: string;
  favored?: boolean;
  pinned?: boolean;
  tags?: string[];
  trashed?: boolean;
  source_history_id?: string | null;
  env_snapshot?: EnvSnapshot | null;
  images?: DraftImage[];
  created_at: string | null;
  updated_at: string | null;
};

function draftImageUrl(image: DraftImage): string {
  const params = new URLSearchParams({
    filename: image.filename,
    subfolder: image.subfolder || "",
  });
  return `/api/history/image?${params.toString()}`;
}

function draftGroup(draft: DraftEntry): "pinned" | "favored" | "plain" {
  if (draft.pinned) return "pinned";
  if (draft.favored) return "favored";
  return "plain";
}

const DRAFT_GROUP_LABEL: Record<"pinned" | "favored" | "plain", string> = {
  pinned: "置顶",
  favored: "收藏",
  plain: "普通",
};

function draftTagKeys(draft: DraftEntry): string[] {
  const tags = draft.tags || [];
  return tags.length > 0 ? tags : ["未标签"];
}

function draftGroupRank(draft: DraftEntry): [number, number] {
  return [draft.pinned ? 0 : 1, draft.favored ? 0 : 1];
}

function draftRecency(draft: DraftEntry): string {
  return draft.updated_at ?? draft.created_at ?? "";
}

function VersionDiffBlock({
  snapshot,
  current,
  same,
}: {
  snapshot: string;
  current: string;
  same: boolean;
}) {
  if (same) return <div className="workflow-meta">与当前副本一致</div>;
  const diff = diffFragments(snapshot, current);
  return (
    <div className="version-diff">
      <div className="workflow-meta">快照：{snapshot || "（空）"}</div>
      <div className="workflow-meta">当前：{current || "（空）"}</div>
      {diff.onlySnapshot.length > 0 && (
        <div className="diff-only-a">快照独有：{diff.onlySnapshot.join("、")}</div>
      )}
      {diff.onlyCurrent.length > 0 && (
        <div className="diff-only-b">当前独有：{diff.onlyCurrent.join("、")}</div>
      )}
    </div>
  );
}

type ExpandedDraft = { draft: DraftEntry; groupKey: string };

function snapshotFiles(files: SnapshotFile[] | undefined): string {
  return (files || []).map((item) => item.file).filter(Boolean).join("、");
}

function envSnapshotSummary(snapshot: EnvSnapshot | null | undefined): string {
  if (!snapshot) return "";
  const parts: string[] = [];
  const checkpoint = snapshotFiles(snapshot.checkpoints);
  if (checkpoint) parts.push(`模型 ${checkpoint}`);
  const lora = snapshotFiles(snapshot.loras);
  if (lora) parts.push(`LoRA ${lora}`);
  if (snapshot.resolution) parts.push(`尺寸 ${snapshot.resolution}`);
  if (typeof snapshot.clip_skip === "number") parts.push(`Clip Skip ${snapshot.clip_skip}`);
  const sampler = (snapshot.samplers || [])[0];
  if (sampler) {
    const bits: string[] = [];
    if (sampler.steps != null) bits.push(`Steps ${sampler.steps}`);
    if (sampler.cfg != null) bits.push(`CFG ${sampler.cfg}`);
    if (sampler.sampler_name) bits.push(String(sampler.sampler_name));
    if (sampler.scheduler) bits.push(String(sampler.scheduler));
    if (bits.length) parts.push(bits.join(" · "));
  }
  return parts.join(" · ");
}

function parseDraftTags(text: string): string[] {
  const parts = text.split(/[,，、;；\s]+/);
  const tags: string[] = [];
  const seen = new Set<string>();
  for (const part of parts) {
    const tag = part.trim();
    if (!tag || seen.has(tag)) continue;
    seen.add(tag);
    tags.push(tag);
  }
  return tags.slice(0, 12);
}

const STATUS_LABEL: Record<string, string> = {
  match: "匹配",
  out_of_range: "超出范围",
  out_of_set: "不在集合",
  conflict: "冲突",
  unknown: "无法判定",
};

// 旧版英文模式 slug 迁移到新的中文文件名 id。
const MODE_LEGACY_ALIAS: Record<string, string> = {
  draft: "起草",
  polish: "润色",
  trim: "精简",
  continue: "续写",
  fresh: "空白直出",
  bilingual: "中英对照",
  negative: "负向词",
};
const DEFAULT_MODE = "起草";

function resolveStoredMode(stored: string | null): string {
  if (!stored) return DEFAULT_MODE;
  return MODE_LEGACY_ALIAS[stored] ?? stored;
}

// 旧版英文人设 id 迁移到新的中文文件名 id；写作类身份串到本页回退提示词助手。
const LEGACY_PERSONA_IDS: Record<string, string> = {
  prompt_assistant: "提示词助手",
  writing_assistant: "提示词助手",
  polish_assistant: "提示词助手",
};
const DEFAULT_PERSONA = "提示词助手";

function formatVersionStamp(stamp: string): string {
  if (!stamp) return "—";
  const [date, time] = stamp.split("-");
  if (date && time && date.length === 8 && time.length >= 6) {
    return `${date.slice(0, 4)}-${date.slice(4, 6)}-${date.slice(6, 8)} ${time.slice(0, 2)}:${time.slice(2, 4)}:${time.slice(4, 6)}`;
  }
  return stamp;
}

function clipText(text: string, limit = 80): string {
  if (!text) return "—";
  return text.length > limit ? `${text.slice(0, limit)}…` : text;
}

type PromptImport = { positive: string; negative: string };

function parsePromptJson(text: string): PromptImport {
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    throw new Error("JSON 文件无法解析。");
  }
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
    throw new Error("不是有效的 Prompt 导出 JSON。");
  }
  const record = parsed as Record<string, unknown>;
  const positive = typeof record.positive === "string" ? record.positive : "";
  const negative = typeof record.negative === "string" ? record.negative : "";
  if (!positive.trim() && !negative.trim()) {
    throw new Error("JSON 中未包含有效的正向/负向 Prompt。");
  }
  return { positive, negative };
}

function parsePromptText(text: string): PromptImport {
  const lines = text.split(/\r?\n/);
  const positiveLines: string[] = [];
  const negativeLines: string[] = [];
  const markPositive = /^\s*正向\s*[:：]/;
  const markNegative = /^\s*负向\s*[:：]/;
  let section: "positive" | "negative" = "positive";
  let anyMark = false;
  for (const line of lines) {
    if (markPositive.test(line)) {
      section = "positive";
      anyMark = true;
      const rest = line.replace(markPositive, "").trim();
      if (rest) positiveLines.push(rest);
      continue;
    }
    if (markNegative.test(line)) {
      section = "negative";
      anyMark = true;
      const rest = line.replace(markNegative, "").trim();
      if (rest) negativeLines.push(rest);
      continue;
    }
    if (!anyMark || section === "positive") {
      positiveLines.push(line);
    } else {
      negativeLines.push(line);
    }
  }
  const positive = positiveLines.join("\n").trim();
  const negative = negativeLines.join("\n").trim();
  if (!positive && !negative) {
    throw new Error("未解析到有效的 Prompt 文本。");
  }
  return { positive, negative };
}

function parsePromptImport(filename: string, text: string): PromptImport {
  const name = filename.toLowerCase();
  if (name.endsWith(".json")) {
    return parsePromptJson(text);
  }
  return parsePromptText(text);
}

function parsePromptContent(text: string): PromptImport {
  const trimmed = text.trim();
  if (trimmed.startsWith("{")) {
    try {
      return parsePromptJson(trimmed);
    } catch {
      // 非导出 JSON 结构时回退按纯文本分段，不臆造字段。
    }
  }
  return parsePromptText(text);
}

// 与后端 workflow_service.MAX_PROMPT_CHARS 对齐；前端仅只读提示，不做硬拦截。
const PROMPT_MAX_CHARS = 200_000;

function promptCountLabel(text: string): string {
  const len = text.length;
  const fragments = countPromptFragments(text);
  const tokens = estimatePromptTokens(text);
  const base = `${len} 字符 · ${fragments} 条 · 约 ${tokens} token`;
  return len > PROMPT_MAX_CHARS ? `${base}（已超上限）` : base;
}

const ACTUAL_SUMMARY: Array<{ key: string; label: string }> = [
  { key: "steps", label: "步数" },
  { key: "cfg", label: "CFG" },
  { key: "sampler_name", label: "采样器" },
  { key: "scheduler", label: "调度器" },
  { key: "clip_skip", label: "Clip Skip" },
];

function downloadLocalFile(filename: string, text: string, mime: string) {
  const blob = new Blob([text], { type: `${mime};charset=utf-8` });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

function promptStamp(): string {
  return new Date().toISOString().replace(/[-:T]/g, "").slice(0, 12);
}

function readStoredFavoredOnly(): boolean {
  try {
    return localStorage.getItem("workbench.favoredOnly") === "1";
  } catch {
    // 本地存储不可用时回落未启用收藏过滤。
  }
  return false;
}

function persistFavoredOnly(value: boolean): void {
  try {
    localStorage.setItem("workbench.favoredOnly", value ? "1" : "0");
  } catch {
    // 忽略本地存储写入失败。
  }
}

function readStoredTagFilter(): string {
  try {
    return localStorage.getItem("workbench.tagFilter") || "";
  } catch {
    // 本地存储不可用时回落全部标签。
  }
  return "";
}

function persistTagFilter(value: string): void {
  try {
    localStorage.setItem("workbench.tagFilter", value);
  } catch {
    // 忽略本地存储写入失败。
  }
}

function readStoredShowTrashed(): boolean {
  try {
    return localStorage.getItem("workbench.showTrashed") === "1";
  } catch {
    // 本地存储不可用时回落普通草稿箱。
  }
  return false;
}

function persistShowTrashed(value: boolean): void {
  try {
    localStorage.setItem("workbench.showTrashed", value ? "1" : "0");
  } catch {
    // 忽略本地存储写入失败。
  }
}

type DraftGroupMode = "section" | "tag";

function readStoredDraftGroupMode(): DraftGroupMode {
  try {
    return localStorage.getItem("workbench.draftGroupMode") === "tag"
      ? "tag"
      : "section";
  } catch {
    // 本地存储不可用时回落分区模式。
  }
  return "section";
}

function persistDraftGroupMode(value: DraftGroupMode): void {
  try {
    localStorage.setItem("workbench.draftGroupMode", value);
  } catch {
    // 忽略本地存储写入失败。
  }
}

function readStoredParam(key: string): string {
  try {
    return localStorage.getItem(key) || "";
  } catch {
    // 本地存储不可用时回落空参数。
  }
  return "";
}

function persistParam(key: string, value: string): void {
  try {
    localStorage.setItem(key, value);
  } catch {
    // 忽略本地存储写入失败。
  }
}

function parseModelParamSize(params: string | undefined): number {
  if (!params) return 0;
  const match = params.toLowerCase().match(/(\d+(?:\.\d+)?)/);
  return match ? Number(match[1]) : 0;
}

function defaultModel(
  models: string[],
  details: Record<string, string>,
): string {
  if (models.length === 0) return "";
  const ranked = [...models].sort((a, b) => {
    const sizeDiff =
      parseModelParamSize(details[b]) - parseModelParamSize(details[a]);
    if (sizeDiff !== 0) return sizeDiff;
    const aSmall = /3\s*b/i.test(a) ? 1 : 0;
    const bSmall = /3\s*b/i.test(b) ? 1 : 0;
    return aSmall - bSmall;
  });
  return ranked[0];
}

function parseOptionalFloat(
  text: string,
  min: number,
  max: number,
): number | null {
  const trimmed = text.trim();
  if (!trimmed) return null;
  const value = Number(trimmed);
  if (!Number.isFinite(value) || value < min || value > max) return null;
  return value;
}

function parseOptionalInt(
  text: string,
  min: number,
  max: number,
): number | null {
  const trimmed = text.trim();
  if (!trimmed) return null;
  if (!/^\d+$/.test(trimmed)) return null;
  const value = Number(trimmed);
  if (!Number.isFinite(value) || value < min || value > max) return null;
  return value;
}

type WorkbenchProps = { comfyuiUrl: string; protectedTerms: string[] };

export default function WorkflowWorkbench({
  comfyuiUrl,
  protectedTerms,
}: WorkbenchProps) {
  const [items, setItems] = useState<WorkflowListItem[]>([]);
  const [detail, setDetail] = useState<WorkflowDetail | null>(null);
  const [compare, setCompare] = useState<ComparePayload | null>(null);
  const [workflowId, setWorkflowId] = useState("");
  const [roles, setRoles] = useState<Record<string, RoleChoice>>({});
  const [positiveText, setPositiveText] = useState("");
  const [negativeText, setNegativeText] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("请选择一个 ComfyUI 工作流 JSON 文件。");
  const [ollamaModels, setOllamaModels] = useState<string[]>([]);
  const [modelDetails, setModelDetails] = useState<Record<string, string>>({});
  const [modelsOnline, setModelsOnline] = useState(true);
  const [selectedModel, setSelectedModel] = useState(() =>
    readStoredParam("workbench.selectedModel"),
  );
  const [profiles, setProfiles] = useState<PromptProfile[]>([]);
  const [selectedProfile, setSelectedProfile] = useState(() =>
    readStoredParam("workbench.selectedProfile") || "auto",
  );
  const [personas, setPersonas] = useState<Persona[]>([]);
  // 提示词工作区只列提示词类身份与裸模型；写作类身份在独立「写作助手」页签。
  const [selectedPersona, setSelectedPersona] = useState(() => {
    const stored = readStoredParam("workbench.selectedPersona");
    // 旧记忆若指向写作类身份，回退提示词助手；本页只保留提示词类与裸模型。
    return (LEGACY_PERSONA_IDS[stored] ?? stored) || DEFAULT_PERSONA;
  });
  const promptPersonas = personas.filter((p) => p.kind !== "writing");
  const [genres, setGenres] = useState<LayerItem[]>([]);
  const [selectedGenre, setSelectedGenre] = useState(
    () => readStoredParam("workbench.selectedGenre") || "",
  );
  const [styles, setStyles] = useState<LayerItem[]>([]);
  const [selectedStyle, setSelectedStyle] = useState(
    () => readStoredParam("workbench.selectedStyle") || "",
  );
  const [taskText, setTaskText] = useState(() =>
    readStoredParam("workbench.candidateTask"),
  );
  const [candidateTemp, setCandidateTemp] = useState(() =>
    readStoredParam("workbench.candidateTemp"),
  );
  const [candidateTopP, setCandidateTopP] = useState(() =>
    readStoredParam("workbench.candidateTopP"),
  );
  const [candidateCtx, setCandidateCtx] = useState(() =>
    readStoredParam("workbench.candidateCtx"),
  );
  const [modes, setModes] = useState<ModeItem[]>([]);
  const [candidateMode, setCandidateMode] = useState(() =>
    resolveStoredMode(readStoredParam("workbench.candidateMode")),
  );
  const [candidates, setCandidates] = useState<CandidateItem[]>([]);
  const [activeCandidate, setActiveCandidate] = useState<number | null>(null);
  const [compareOther, setCompareOther] = useState<number | null>(null);
  const [candidateBusy, setCandidateBusy] = useState(false);
  const [lockedFragments, setLockedFragments] = useState<string[]>([]);

  function toggleLockedFragment(fragment: string) {
    setLockedFragments((prev) =>
      prev.includes(fragment)
        ? prev.filter((item) => item !== fragment)
        : [...prev, fragment],
    );
  }

  function candidateScopeOf(mode: string): CandidateScope {
    return modes.find((m) => m.id === mode)?.negative_output
      ? "negative"
      : "positive";
  }
  const currentPersona = personas.find((p) => p.id === selectedPersona);
  const personaKind: string = currentPersona?.kind ?? "generic";
  const isWritingIdentity = personaKind === "writing";
  const candidateModeOptions = modes.filter(
    (opt) => !isWritingIdentity || !opt.negative_output,
  );
  const [importedPrompt, setImportedPrompt] = useState<PromptImport | null>(null);
  const [pasteOpen, setPasteOpen] = useState(false);
  const [pasteText, setPasteText] = useState("");
  const [releaseBusy, setReleaseBusy] = useState(false);
  const [versions, setVersions] = useState<VersionEntry[]>([]);
  const [drafts, setDrafts] = useState<DraftEntry[]>([]);
  const [draftName, setDraftName] = useState("");
  const [draftNote, setDraftNote] = useState("");
  const [draftTags, setDraftTags] = useState("");
  const [tagFilter, setTagFilter] = useState(readStoredTagFilter);
  const [draftBusy, setDraftBusy] = useState(false);
  const [currentDraftId, setCurrentDraftId] = useState<string | null>(null);
  const [deletingDraftId, setDeletingDraftId] = useState<string | null>(null);
  const [updatingDraftId, setUpdatingDraftId] = useState<string | null>(null);
  const [copyingDraftId, setCopyingDraftId] = useState<string | null>(null);
  const [copyTargetId, setCopyTargetId] = useState("");
  const [copyJumpId, setCopyJumpId] = useState<string | null>(null);
  const [showTrashed, setShowTrashed] = useState(readStoredShowTrashed);
  const [purgingDraftId, setPurgingDraftId] = useState<string | null>(null);
  const [draftKeyword, setDraftKeyword] = useState(
    () => readStoredParam("workbench.draftKeyword"),
  );
  const [favoredOnly, setFavoredOnly] = useState(readStoredFavoredOnly);
  const [draftGroupMode, setDraftGroupMode] = useState(readStoredDraftGroupMode);
  const [tagRenameOpen, setTagRenameOpen] = useState(false);
  const [renameOld, setRenameOld] = useState("");
  const [renameNew, setRenameNew] = useState("");
  const [renameBusy, setRenameBusy] = useState(false);
  const [draftPanelOpen, setDraftPanelOpen] = useState(false);
  async function refreshList() {
    const result = await apiGet<{ items: WorkflowListItem[] }>(
      "/api/workflows",
    );
    setItems(result.items);
  }
  useEffect(() => {
    void refreshList().catch((error) => {
      setMessage(`读取工作流列表失败：${String(error)}`);
    });
  }, []);
  useEffect(() => {
    void loadModels();
    void loadProfiles();
    void loadPersonas();
    void loadGenres();
    void loadStyles();
    void loadModes();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  // 模式清单就绪后，记忆值若已失效（改名 / 删除 / 旧英文 slug）回退默认起草。
  useEffect(() => {
    if (!modes.length) return;
    if (!modes.some((m) => m.id === candidateMode)) {
      const fallback = modes.some((m) => m.id === DEFAULT_MODE)
        ? DEFAULT_MODE
        : modes[0].id;
      setCandidateMode(fallback);
      persistParam("workbench.candidateMode", fallback);
    }
  }, [modes, candidateMode]);
  // 人设列表就绪后，记忆值若已失效（改名 / 删除 / 跨类串入）回退默认提示词助手或裸模型。
  useEffect(() => {
    if (!selectedPersona || selectedPersona === "none") return;
    if (!promptPersonas.some((p) => p.id === selectedPersona)) {
      const fallback = promptPersonas.some((p) => p.id === DEFAULT_PERSONA)
        ? DEFAULT_PERSONA
        : "none";
      setSelectedPersona(fallback);
      persistParam("workbench.selectedPersona", fallback);
    }
  }, [promptPersonas, selectedPersona]);
  useEffect(() => {
    const openDrafts = () => setDraftPanelOpen(true);
    window.addEventListener("workbench:open-drafts", openDrafts);
    return () => window.removeEventListener("workbench:open-drafts", openDrafts);
  }, []);

  useEffect(() => {
    if (!draftPanelOpen) return;
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape" && !draftBusy && !busy) {
        setDraftPanelOpen(false);
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [draftPanelOpen, draftBusy, busy]);

  function initialRoles(report: Analysis) {
    const next: Record<string, RoleChoice> = {};
    for (const node of report.prompt_nodes) {
      if (node.roles.includes("positive")) {
        next[node.node_id] = "positive";
      } else if (node.roles.includes("negative")) {
        next[node.node_id] = "negative";
      } else {
        next[node.node_id] = "";
      }
    }
    return next;
  }
  async function loadCompare(id: string) {
    try {
      const result = await apiGet<ComparePayload>(
        `/api/workflows/${id}/rules/compare`,
      );
      setCompare(result);
    } catch (error) {
      setCompare(null);
      setMessage(`读取规则比对失败：${String(error)}`);
    }
  }
  async function openWorkflow(id: string) {
    setBusy(true);
    setCopyJumpId(null);
    try {
      const result = await apiGet<WorkflowDetail>(
        `/api/workflows/${id}`,
      );
      setWorkflowId(id);
      setDetail(result);
      setCompare(null);
      void loadCompare(id);
      setRoles(initialRoles(result.report));
      setPositiveText(
        result.working_prompts.find((item) => item.role === "positive")?.text ?? "",
      );
      setNegativeText(
        result.working_prompts.find((item) => item.role === "negative")?.text ?? "",
      );
      setVersions([]);
      setDrafts([]);
      setDraftName("");
      setDraftNote("");
      setDraftTags("");
      setTagFilter("");
      persistTagFilter("");
      setDeletingDraftId(null);
      setUpdatingDraftId(null);
      setCopyingDraftId(null);
      setCopyTargetId("");
      setCopyJumpId(null);
      setShowTrashed(false);
      persistShowTrashed(false);
      setPurgingDraftId(null);
      setDraftKeyword("");
      persistParam("workbench.draftKeyword", "");
      setFavoredOnly(false);
      persistFavoredOnly(false);
      setConfirmStamp(null);
      setComparingStamp(null);
      setImportedPrompt(null);
      setPasteOpen(false);
      setPasteText("");
      void loadVersions(id);
      void loadDrafts(id, false);
      setMessage(`已打开：${result.manifest.filename}`);
    } catch (error) {
      setMessage(`打开失败：${String(error)}`);
    } finally {
      setBusy(false);
    }
  }
  async function importFile(file?: File) {
    if (!file) return;
    setBusy(true);
    setMessage("正在读取和解析工作流…");
    try {
      const parsed = JSON.parse(await file.text()) as Record<string, unknown>;
      const result = await apiPost<ImportResult>(
        "/api/workflows/import",
        {
          filename: file.name,
          workflow: parsed,
        },
      );
      if (result.duplicate) {
        // Same source content already exists: reopen the original record
        // instead of resetting its confirmed prompts, drafts or whitelist.
        await openWorkflow(result.id);
        setMessage(`该工作流已存在，已打开原有记录：${result.filename}`);
        return;
      }
      setWorkflowId(result.id);
      setCompare(null);
      setRoles(initialRoles(result.report));
      setPositiveText("");
      setNegativeText("");
      setDetail({
        manifest: {
          id: result.id,
          filename: result.filename,
          format: result.format,
          confirmed: false,
          whitelist: [],
        },
        report: result.report,
        working_prompts: [],
      });
      void loadCompare(result.id);
      setDrafts([]);
      setDraftName("");
      setDraftNote("");
      setDraftTags("");
      setTagFilter("");
      persistTagFilter("");
      setDeletingDraftId(null);
      setUpdatingDraftId(null);
      setCopyingDraftId(null);
      setCopyTargetId("");
      setCopyJumpId(null);
      setShowTrashed(false);
      persistShowTrashed(false);
      setPurgingDraftId(null);
      setDraftKeyword("");
      persistParam("workbench.draftKeyword", "");
      setFavoredOnly(false);
      persistFavoredOnly(false);
      setComparingStamp(null);
      setImportedPrompt(null);
      setPasteOpen(false);
      setPasteText("");
      await refreshList();
      setMessage(`导入成功：${result.filename}`);
    } catch (error) {
      setMessage(`导入失败：${String(error)}`);
    } finally {
      setBusy(false);
    }
  }
  async function confirmNodes() {
    if (!workflowId || !detail) return;
    const positiveIds = Object.entries(roles)
      .filter(([, role]) => role === "positive")
      .map(([id]) => id);
    const negativeIds = Object.entries(roles)
      .filter(([, role]) => role === "negative")
      .map(([id]) => id);
    setBusy(true);
    try {
      const result = await apiPost<WorkflowDetail>(
        `/api/workflows/${workflowId}/confirm`,
        {
          positive_node_ids: positiveIds,
          negative_node_ids: negativeIds,
        },
      );
      setDetail(result);
      setPositiveText(
        result.working_prompts.find((item) => item.role === "positive")?.text ?? "",
      );
      setNegativeText(
        result.working_prompts.find((item) => item.role === "negative")?.text ?? "",
      );
      await refreshList();
      setMessage("Prompt 节点白名单已确认。现在可以编辑工作副本。");
    } catch (error) {
      setMessage(`确认失败：${String(error)}`);
    } finally {
      setBusy(false);
    }
  }
  async function writePrompts() {
    if (!workflowId || !detail?.manifest.confirmed) return;
    setBusy(true);
    try {
      const result = await apiPost<WriteResult>(
        `/api/workflows/${workflowId}/write`,
        {
          positive_text: detail.manifest.whitelist.some(
            (item) => item.role === "positive",
          )
            ? positiveText
            : null,
          negative_text: detail.manifest.whitelist.some(
            (item) => item.role === "negative",
          )
            ? negativeText
            : null,
        },
      );
      setDetail(result.workflow);
      const count = result.changes.length;
      setMessage(
        `写入完成：${count} 个 Prompt 节点发生变化。非白名单字段完整性校验通过。`,
      );
    } catch (error) {
      setMessage(`写入被拒绝或失败：${String(error)}`);
    } finally {
      setBusy(false);
    }
  }
  async function loadProfiles() {
    try {
      const result = await apiGet<PromptProfilesResponse>("/api/prompt-profiles");
      setProfiles(result.items ?? []);
    } catch {
      // Profiles are optional: keep the default auto match on failure.
      setProfiles([]);
    }
  }
  async function loadPersonas() {
    try {
      const result = await apiGet<PersonasResponse>("/api/personas");
      setPersonas(result.items ?? []);
    } catch {
      setPersonas([]);
    }
  }
  async function loadGenres() {
    try {
      const result = await apiGet<LayersResponse>("/api/genres");
      setGenres(result.items ?? []);
    } catch {
      setGenres([]);
    }
  }
  async function loadStyles() {
    try {
      const result = await apiGet<LayersResponse>("/api/styles");
      setStyles(result.items ?? []);
    } catch {
      setStyles([]);
    }
  }
  async function loadModes() {
    try {
      const result = await apiGet<ModesResponse>("/api/modes");
      setModes(result.items ?? []);
    } catch {
      setModes([]);
    }
  }
  async function loadModels() {
    try {
      const result = await apiGet<OllamaModelsResponse>("/api/ollama/models");
      const details = result.model_details ?? {};
      setOllamaModels(result.models);
      setModelDetails(details);
      setModelsOnline(result.online);
      if (result.models.length > 0) {
        const remembered = readStoredParam("workbench.selectedModel");
        const pick = result.models.includes(remembered)
          ? remembered
          : defaultModel(result.models, details);
        setSelectedModel(pick);
      }
    } catch (error) {
      setModelsOnline(false);
      setMessage(`读取本地模型列表失败：${String(error)}`);
    }
  }
  async function generateCandidate() {
    if (!workflowId || !selectedModel) return;
    setCandidateBusy(true);
    try {
      // Writing identities have no negative-prompt mode; fall back to draft if
      // a remembered negative mode is still selected after switching identity.
      const selectedMode = modes.find((m) => m.id === candidateMode);
      const effectiveMode =
        isWritingIdentity && selectedMode?.negative_output
          ? DEFAULT_MODE
          : candidateMode;
      const body: Record<string, unknown> = {
        model: selectedModel,
        task: taskText.trim() || null,
        mode: effectiveMode,
      };
      const temperature = parseOptionalFloat(candidateTemp, 0, 2);
      const topP = parseOptionalFloat(candidateTopP, 0, 1);
      const numCtx = parseOptionalInt(candidateCtx, 1024, 131_072);
      if (temperature !== null) body.temperature = temperature;
      if (topP !== null) body.top_p = topP;
      if (numCtx !== null) body.num_ctx = numCtx;
      if (lockedFragments.length) body.locked_fragments = lockedFragments;

      // 提示词工作区可选提示词类身份或裸模型；写作类身份走独立写作助手页签。
      body.persona = (selectedPersona || "none").trim() || "none";

      // Profile slot applies to prompt-class identities only. Three states:
      // auto (default, omitted), explicit id, or none (skip the layer).
      if (!isWritingIdentity) {
        const profileChoice = (selectedProfile || "auto").trim();
        if (profileChoice === "" || profileChoice === "none") {
          body.prompt_profile = "none";
        } else if (profileChoice !== "auto") {
          body.prompt_profile = profileChoice;
        }
      }
      if (selectedGenre.trim()) body.genre = selectedGenre.trim();
      if (selectedStyle.trim()) body.style = selectedStyle.trim();

      const result = await apiPost<CandidateResponse>(
        `/api/workflows/${workflowId}/prompt/candidates`,
        body,
      );
      const text = result.candidates[0] ?? "";
      if (text) {
        const item: CandidateItem = {
          text,
          mode: effectiveMode,
          scope: candidateScopeOf(effectiveMode),
        };
        const nextIndex = candidates.length;
        setCandidates((prev) => [...prev, item]);
        setActiveCandidate(nextIndex);
        setCompareOther(null);
      }
      const profileNote = isWritingIdentity
        ? ""
        : result.selected_profile_name
          ? `，Profile：${result.selected_profile_name}（${result.profile_match_source || "auto"}）`
          : `，Profile 回退通用规则${result.fallback_reason ? `（${result.fallback_reason}）` : ""}`;
      const warnNote = result.profile_warning ? `；${result.profile_warning}` : "";
      setMessage(`已生成候选（规则状态：${result.rule_status}）${profileNote}${warnNote}，采用前请核对。`);
    } catch (error) {
      setMessage(`候选生成失败：${String(error)}`);
    } finally {
      setCandidateBusy(false);
    }
  }
  function adoptCandidate(item: CandidateItem) {
    const sourceText = item.scope === "negative" ? negativeText : positiveText;
    // Keep protected terms that already exist in the current source text but
    // were dropped from the candidate; append them rather than overwriting.
    const missingProtected = protectedTerms.filter(
      (term) => term && !item.text.includes(term) && sourceText.includes(term),
    );
    // Request-level locked fragments are kept even when the current text does
    // not yet contain them; they are appended rather than overwritten.
    const missingLocked = lockedFragments.filter(
      (fragment) => fragment && !item.text.includes(fragment),
    );
    const missing = [...missingProtected, ...missingLocked];
    const merged = missing.length
      ? `${item.text.trimEnd()}${item.text.trim() ? ", " : ""}${missing.join(", ")}`
      : item.text;
    if (item.scope === "negative") setNegativeText(merged);
    else setPositiveText(merged);
  }

  async function copyCandidate(item: CandidateItem) {
    try {
      await navigator.clipboard.writeText(item.text);
      setMessage("候选已复制到剪贴板（未写入工作副本）。");
    } catch (error) {
      setMessage(`复制候选失败：${String(error)}`);
    }
  }

  function removeCandidate(index: number) {
    setCandidates((prev) => prev.filter((_, i) => i !== index));
    setActiveCandidate((prev) =>
      prev === null ? null : prev > index ? prev - 1 : prev,
    );
    setCompareOther(null);
  }

  function clearCandidates() {
    setCandidates([]);
    setActiveCandidate(null);
    setCompareOther(null);
    setMessage("候选已清空（未写入工作副本）。");
  }

  function toggleCompareOther(index: number) {
    setCompareOther((prev) => (prev === index ? null : index));
  }

  async function copyPromptText(text: string, label: string) {
    if (!text.trim()) {
      setMessage(`${label}为空，无需复制。`);
      return;
    }
    try {
      await navigator.clipboard.writeText(text);
      setMessage(`${label} 已复制到剪贴板（未写入工作副本）。`);
    } catch (error) {
      setMessage(`复制 ${label} 失败：${String(error)}`);
    }
  }

  function copyPositive() {
    void copyPromptText(positiveText, "正向 Prompt");
  }

  function copyNegative() {
    void copyPromptText(negativeText, "负向 Prompt");
  }

  function copyBoth() {
    const parts: string[] = [];
    if (positiveText.trim()) parts.push(`正向：\n${positiveText}`);
    if (negativeText.trim()) parts.push(`负向：\n${negativeText}`);
    if (parts.length === 0) {
      setMessage("正向与负向均为空，无需复制。");
      return;
    }
    void copyPromptText(parts.join("\n\n"), "完整 Prompt");
  }

  function exportPromptTxt() {
    if (!positiveText.trim() && !negativeText.trim()) {
      setMessage("正向与负向均为空，无需导出。");
      return;
    }
    const parts: string[] = [];
    if (positiveText.trim()) parts.push(`正向：\n${positiveText}`);
    if (negativeText.trim()) parts.push(`负向：\n${negativeText}`);
    downloadLocalFile(
      `workbench-prompt-${promptStamp()}.txt`,
      parts.join("\n\n"),
      "text/plain",
    );
    setMessage("Prompt 已导出为 TXT（仅本地下载，未写入工作副本）。");
  }

  function exportPromptJson() {
    if (!positiveText.trim() && !negativeText.trim()) {
      setMessage("正向与负向均为空，无需导出。");
      return;
    }
    const payload = {
      version: 1,
      positive: positiveText,
      negative: negativeText,
    };
    downloadLocalFile(
      `workbench-prompt-${promptStamp()}.json`,
      JSON.stringify(payload, null, 2),
      "application/json",
    );
    setMessage("Prompt 已导出为 JSON（仅本地下载，未写入工作副本）。");
  }

  async function onPromptFileChosen(file?: File) {
    if (!file) return;
    try {
      const text = await file.text();
      const parsed = parsePromptImport(file.name, text);
      setImportedPrompt(parsed);
      setMessage("已解析导入内容，采用前请核对（未写入编辑框与工作副本）。");
    } catch (error) {
      setMessage(`导入失败：${String(error)}`);
    }
  }

  function adoptImported() {
    if (!importedPrompt) return;
    setPositiveText(importedPrompt.positive);
    setNegativeText(importedPrompt.negative);
    setImportedPrompt(null);
    setMessage("已将导入内容填入编辑框（尚未写入工作副本）。");
  }

  function dismissImported() {
    setImportedPrompt(null);
    setMessage("导入内容已放弃（未写入编辑框与工作副本）。");
  }

  function parsePasted() {
    try {
      const parsed = parsePromptContent(pasteText);
      setImportedPrompt(parsed);
      setPasteOpen(false);
      setPasteText("");
      setMessage("已解析粘贴内容，采用前请核对（未写入编辑框与工作副本）。");
    } catch (error) {
      setMessage(`粘贴解析失败：${String(error)}`);
    }
  }

  function cancelPaste() {
    setPasteOpen(false);
    setPasteText("");
  }

  async function releaseVram() {
    setReleaseBusy(true);
    try {
      const result = await apiPost<ReleaseVramResult>("/api/system/release-vram", {});
      const parts: string[] = [];
      if (!result.ollama_reachable) {
        parts.push("本地 LLM 未运行");
      } else if (result.ollama_stopped.length) {
        parts.push(`已卸载 LLM：${result.ollama_stopped.join("、")}`);
      } else {
        parts.push("无驻留 LLM");
      }
      if (result.ollama_still_resident.length) {
        parts.push(`仍驻留：${result.ollama_still_resident.join("、")}`);
      }
      parts.push(
        result.comfyui_freed ? "已释放 ComfyUI 模型显存" : "ComfyUI 模型释放未成功",
      );
      if (result.ollama_errors.length) {
        parts.push(`LLM：${result.ollama_errors.join("；")}`);
      }
      if (result.comfyui_error) parts.push(`ComfyUI：${result.comfyui_error}`);
      setMessage(parts.join("；"));
    } catch (error) {
      setMessage(`卸载失败：${String(error)}`);
    } finally {
      setReleaseBusy(false);
    }
  }

  async function exportWorkflowJson() {
    if (!workflowId) return;
    try {
      const result = await apiGet<ExportWorkflowPayload>(
        `/api/workflows/${workflowId}/export`,
      );
      downloadLocalFile(
        result.filename || "workflow.json",
        JSON.stringify(result.workflow, null, 2),
        "application/json",
      );
      setMessage("已导出 API 格式工作流 JSON，可拖入 ComfyUI 画布后排队出图。");
    } catch (error) {
      setMessage(`导出工作流失败：${String(error)}`);
    }
  }

  async function resetWorkingCopy() {
    if (!workflowId) return;
    setBusy(true);
    try {
      const result = await apiPost<WorkflowDetail>(
        `/api/workflows/${workflowId}/reset`,
        {},
      );
      setDetail(result);
      setPositiveText(
        result.working_prompts.find((item) => item.role === "positive")?.text ?? "",
      );
      setNegativeText(
        result.working_prompts.find((item) => item.role === "negative")?.text ?? "",
      );
      setMessage("已清空本次写入，工作副本回退到导入时的原始内容。");
    } catch (error) {
      setMessage(`清空工作副本失败：${String(error)}`);
    } finally {
      setBusy(false);
    }
  }

  async function loadVersions(id: string) {
    try {
      const result = await apiGet<{ items: VersionEntry[] }>(
        `/api/workflows/${id}/versions`,
      );
      setVersions(result.items);
    } catch {
      setVersions([]);
    }
  }

  async function loadDrafts(id: string, trashed: boolean) {
    try {
      const result = await apiGet<{ items: DraftEntry[] }>(
        `/api/workflows/${id}/drafts?include_trashed=${trashed}`,
      );
      setDrafts(result.items);
    } catch {
      setDrafts([]);
    }
  }

  async function saveDraft() {
    if (!workflowId) return;
    const name = draftName.trim();
    if (!name) {
      setMessage("请先填写草稿名称。");
      return;
    }
    if (!positiveText.trim() && !negativeText.trim()) {
      setMessage("正向与负向均为空，无法保存草稿。");
      return;
    }
    setDraftBusy(true);
    try {
      const record = await apiPost<{ id: string }>(
        `/api/workflows/${workflowId}/drafts`,
        {
          name,
          positive: positiveText,
          negative: negativeText,
          note: draftNote.trim(),
          tags: parseDraftTags(draftTags),
        },
      );
      setCurrentDraftId(record.id);
      setDraftName("");
      setDraftNote("");
      setDraftTags("");
      await loadDrafts(workflowId, showTrashed);
      setMessage("草稿已保存（未写入工作副本）。");
    } catch (error) {
      setMessage(`保存草稿失败：${String(error)}`);
    } finally {
      setDraftBusy(false);
    }
  }

  async function submitTagRename() {
    const oldTag = renameOld.trim();
    const newTag = renameNew.trim();
    if (!oldTag || !newTag) {
      setMessage("请填写原标签与新标签。");
      return;
    }
    setRenameBusy(true);
    try {
      const result = await apiPost<{ renamed: number; old: string; new: string }>(
        "/api/workflows/tags/rename",
        { old_tag: oldTag, new_tag: newTag },
      );
      if (workflowId) await loadDrafts(workflowId, showTrashed);
      setTagRenameOpen(false);
      setRenameOld("");
      setRenameNew("");
      setMessage(
        `已重命名标签「${result.old}」→「${result.new}」，涉及 ${result.renamed} 份草稿。`,
      );
    } catch (error) {
      setMessage(`标签重命名失败：${String(error)}`);
    } finally {
      setRenameBusy(false);
    }
  }

  function adoptDraft(draft: DraftEntry) {
    setPositiveText(draft.positive);
    setNegativeText(draft.negative);
    setDraftName(draft.name);
    setDraftNote(draft.note);
    setDraftTags((draft.tags || []).join(", "));
    setCurrentDraftId(draft.id);
    setMessage(`草稿「${draft.name}」已载入编辑框（尚未写入工作副本）。`);
  }

  function goToHistory(promptId: string) {
    try {
      localStorage.setItem("workbench.jumpHistory", promptId);
    } catch {
      // 忽略本地存储写入失败。
    }
    window.dispatchEvent(
      new CustomEvent("workbench:navigate", {
        detail: { page: "history", promptId },
      }),
    );
  }

  async function deleteDraft(draftId: string) {
    if (!workflowId) return;
    setDraftBusy(true);
    try {
      await apiDelete(`/api/workflows/${workflowId}/drafts/${draftId}`, {});
      setDeletingDraftId(null);
      await loadDrafts(workflowId, showTrashed);
      setMessage("草稿已移入回收站，可在回收站恢复。");
    } catch (error) {
      setMessage(`删除草稿失败：${String(error)}`);
    } finally {
      setDraftBusy(false);
    }
  }

  async function restoreDraft(draft: DraftEntry) {
    if (!workflowId) return;
    setDraftBusy(true);
    try {
      await apiPost(`/api/workflows/${workflowId}/drafts/${draft.id}/restore`, {});
      await loadDrafts(workflowId, showTrashed);
      setMessage(`草稿「${draft.name}」已恢复到草稿箱。`);
    } catch (error) {
      setMessage(`恢复草稿失败：${String(error)}`);
    } finally {
      setDraftBusy(false);
    }
  }

  async function purgeDraft(draftId: string) {
    if (!workflowId) return;
    setDraftBusy(true);
    try {
      await apiPost(`/api/workflows/${workflowId}/drafts/${draftId}/purge`, {});
      setPurgingDraftId(null);
      await loadDrafts(workflowId, true);
      setMessage("草稿已彻底删除，不可恢复。");
    } catch (error) {
      setMessage(`彻底删除失败：${String(error)}`);
    } finally {
      setDraftBusy(false);
    }
  }

  async function toggleFavorite(draft: DraftEntry) {
    if (!workflowId) return;
    setDraftBusy(true);
    try {
      await apiPost(`/api/workflows/${workflowId}/drafts/${draft.id}/favorite`, {});
      await loadDrafts(workflowId, showTrashed);
      setMessage(draft.favored ? `已取消收藏草稿「${draft.name}」。` : `已收藏草稿「${draft.name}」。`);
    } catch (error) {
      setMessage(`切换收藏失败：${String(error)}`);
    } finally {
      setDraftBusy(false);
    }
  }

  async function togglePin(draft: DraftEntry) {
    if (!workflowId) return;
    setDraftBusy(true);
    try {
      await apiPost(`/api/workflows/${workflowId}/drafts/${draft.id}/pin`, {});
      await loadDrafts(workflowId, showTrashed);
      setMessage(draft.pinned ? `已取消置顶草稿「${draft.name}」。` : `已置顶草稿「${draft.name}」。`);
    } catch (error) {
      setMessage(`切换置顶失败：${String(error)}`);
    } finally {
      setDraftBusy(false);
    }
  }

  function exportDrafts() {
    const payload = {
      exported_at: new Date().toISOString(),
      count: filteredDrafts.length,
      records: filteredDrafts,
    };
    const blob = new Blob([JSON.stringify(payload, null, 2)], {
      type: "application/json;charset=utf-8",
    });
    const url = URL.createObjectURL(blob);
    const stamp = new Date().toISOString().replace(/[-:T]/g, "").slice(0, 12);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `workbench-drafts-${stamp}.json`;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(url);
  }

  async function updateDraft(draft: DraftEntry) {
    if (!workflowId) return;
    const name = draftName.trim();
    if (!name) {
      setMessage("请先填写草稿名称。");
      return;
    }
    if (!positiveText.trim() && !negativeText.trim()) {
      setMessage("正向与负向均为空，无法更新草稿。");
      return;
    }
    setDraftBusy(true);
    try {
      await apiPost(`/api/workflows/${workflowId}/drafts`, {
        draft_id: draft.id,
        name,
        positive: positiveText,
        negative: negativeText,
        note: draftNote.trim(),
        tags: parseDraftTags(draftTags),
        source_history_id: draft.source_history_id ?? null,
      });
      setCurrentDraftId(draft.id);
      setUpdatingDraftId(null);
      await loadDrafts(workflowId, showTrashed);
      setMessage("草稿已更新（未写入工作副本）。");
    } catch (error) {
      setMessage(`更新草稿失败：${String(error)}`);
    } finally {
      setDraftBusy(false);
    }
  }

  async function copyDraftTo(draft: DraftEntry) {
    if (!copyTargetId) {
      setMessage("请先选择要复制到的目标工作流。");
      return;
    }
    setDraftBusy(true);
    try {
      await apiPost(`/api/workflows/${copyTargetId}/drafts`, {
        name: draft.name,
        positive: draft.positive,
        negative: draft.negative,
        note: draft.note,
        tags: draft.tags || [],
      });
      const targetId = copyTargetId;
      const target = items.find((workflow) => workflow.id === targetId);
      const targetLabel = target?.filename || targetId;
      setCopyingDraftId(null);
      setCopyTargetId("");
      setShowTrashed(false);
      persistShowTrashed(false);
      setPurgingDraftId(null);
      setCopyJumpId(targetId);
      setMessage(`已复制草稿「${draft.name}」到「${targetLabel}」。`);
    } catch (error) {
      setMessage(`复制草稿失败：${String(error)}`);
    } finally {
      setDraftBusy(false);
    }
  }

  const allDraftTags = Array.from(
    new Set(drafts.flatMap((draft) => draft.tags || [])),
  ).sort();
  const copyTargets = items.filter((workflow) => workflow.id !== workflowId);
  const draftKeywordTerm = draftKeyword.trim().toLowerCase();
  const filteredDrafts = drafts.filter((draft) => {
    if (favoredOnly && !draft.favored) return false;
    if (tagFilter && !(draft.tags || []).includes(tagFilter)) return false;
    if (!draftKeywordTerm) return true;
    const haystack = [
      draft.name,
      draft.note,
      draft.positive,
      draft.negative,
      ...(draft.tags || []),
    ]
      .join(" ")
      .toLowerCase();
    return haystack.includes(draftKeywordTerm);
  });
  const groupedDrafts: ExpandedDraft[] =
    draftGroupMode === "tag"
      ? filteredDrafts
          .flatMap((draft) =>
            draftTagKeys(draft).map((groupKey) => ({ draft, groupKey })),
          )
          .sort((a, b) => {
            const keyCmp = a.groupKey.localeCompare(b.groupKey, "zh-Hans-CN");
            if (keyCmp !== 0) return keyCmp;
            const ra = draftGroupRank(a.draft);
            const rb = draftGroupRank(b.draft);
            if (ra[0] !== rb[0]) return ra[0] - rb[0];
            if (ra[1] !== rb[1]) return ra[1] - rb[1];
            return draftRecency(b.draft).localeCompare(draftRecency(a.draft));
          })
      : filteredDrafts.map((draft) => ({ draft, groupKey: draftGroup(draft) }));
  const [restoringStamp, setRestoringStamp] = useState<string | null>(null);
  const [confirmStamp, setConfirmStamp] = useState<string | null>(null);
  const [comparingStamp, setComparingStamp] = useState<string | null>(null);

  async function restoreVersion(stamp: string) {
    if (!workflowId) return;
    setRestoringStamp(stamp);
    setConfirmStamp(null);
    try {
      const result = await apiPost<WriteResult>(
        `/api/workflows/${workflowId}/versions/${stamp}/restore`,
        {},
      );
      setDetail(result.workflow);
      setPositiveText(
        result.workflow.working_prompts.find((item) => item.role === "positive")
          ?.text ?? "",
      );
      setNegativeText(
        result.workflow.working_prompts.find((item) => item.role === "negative")
          ?.text ?? "",
      );
      await loadVersions(workflowId);
      setMessage("已恢复所选历史版本快照的提示词文本。");
    } catch (error) {
      setMessage(`版本恢复失败：${String(error)}`);
    } finally {
      setRestoringStamp(null);
    }
  }
  const canWrite =
    detail?.report.safe_prompt_write_supported === true &&
    detail.manifest.confirmed === true;

  // 5.2 工作流信息摘要（只读聚合，不参与写入）
  const firstModelInput = detail?.report.model_nodes[0]
    ? Object.values(detail.report.model_nodes[0].inputs)
        .map(String)
        .find((v) => v && v !== "undefined")
    : undefined;
  const checkpointName = compare?.checkpoint ?? firstModelInput ?? "—";
  const loraCount = detail?.report.lora_nodes.length ?? 0;
  const actuals = compare?.actuals ?? {};
  const resolution = `${formatActual(actuals.width)}×${formatActual(actuals.height)}`;
  const compareSummaryText = compare
    ? Object.entries(compare.counts)
        .map(([status, count]) => `${STATUS_LABEL[status] ?? status} ${count}`)
        .join(" · ") || "—"
    : "—";

  return (
    <section className="workflow-page">
      {detail && (
        <div className="w-summary">
          <div className="w-sum-block panel">
            <div className="k">当前工作流</div>
            <div className="v" title={detail.manifest.filename}>
              {detail.manifest.filename}
            </div>
            <div className="w-row">
              <span>格式</span>
              <b>{detail.report.format.toUpperCase()}</b>
            </div>
            <div className="w-row">
              <span>节点数</span>
              <b>{detail.report.node_count}</b>
            </div>
          </div>
          <div className="w-sum-block panel">
            <div className="k">模型信息</div>
            <div className="v" title={checkpointName}>
              {checkpointName}
            </div>
            <div className="w-row">
              <span>LoRA 数量</span>
              <b>{loraCount}</b>
            </div>
          </div>
          <div className="w-sum-block panel">
            <div className="k">采样参数</div>
            <div className="w-row">
              <span>采样器</span>
              <b>{formatActual(actuals.sampler_name)}</b>
            </div>
            <div className="w-row">
              <span>步数 / CFG</span>
              <b>
                {formatActual(actuals.steps)} / {formatActual(actuals.cfg)}
              </b>
            </div>
            <div className="w-row">
              <span>分辨率</span>
              <b>{resolution}</b>
            </div>
            <div className="w-row">
              <span>Clip Skip</span>
              <b>{formatActual(actuals.clip_skip)}</b>
            </div>
          </div>
          <div className="w-sum-block panel">
            <div className="k">规则比对结果</div>
            <div className="v">
              {compare ? (compare.status === "registered" ? "已登记" : "未登记") : "—"}
            </div>
            <div className="w-row">
              <span>结果摘要</span>
              <b title={compareSummaryText}>{compareSummaryText}</b>
            </div>
          </div>
        </div>
      )}
      <div className="panel" id="step-01">
        <div className="section-heading">
          <div>
            <p>
              导入、解析并确认 Prompt 节点。原始工作流只读，修改只写入工作副本。
            </p>
          </div>
          <label className="workflow-import-button">
            导入 JSON
            <input
              type="file"
              accept=".json,application/json"
              disabled={busy}
              onChange={(event) => {
                const file = event.target.files?.[0];
                void importFile(file);
                event.currentTarget.value = "";
              }}
            />
          </label>
        </div>
        <div className="workflow-columns">
          <div>
            <h3>已导入工作流</h3>
            {items.length === 0 ? (
              <p className="muted">还没有导入工作流。</p>
            ) : (
              <div className="workflow-list">
                {items.map((item) => (
                  <button
                    key={item.id}
                    className={`workflow-list-item ${
                      workflowId === item.id ? "selected" : ""
                    }`}
                    disabled={busy}
                    onClick={() => void openWorkflow(item.id)}
                  >
                    <strong>{item.filename}</strong>
                    <small>
                      {item.format.toUpperCase()} ·{" "}
                      {item.confirmed ? "已确认白名单" : "待确认"}
                    </small>
                  </button>
                ))}
              </div>
            )}
          </div>
          <div>
            <h3 id="step-02">当前工作流识别结果</h3>
            {!detail ? (
              <p className="muted">导入或从左侧选择一个工作流。</p>
            ) : (
              <>
                <p>
                  <strong>{detail.manifest.filename}</strong> ·{" "}
                  {detail.report.format.toUpperCase()} ·{" "}
                  {detail.report.node_count} 个节点
                </p>
                <h4>模型加载节点</h4>
                {detail.report.model_nodes.length === 0 ? (
                  <p className="muted">未识别到常见模型加载节点。</p>
                ) : (
                  <ul>
                    {detail.report.model_nodes.map((node) => (
                      <li key={node.node_id}>
                        节点 {node.node_id} · {node.class_type}
                        {Object.entries(node.inputs).map(([key, value]) => (
                          <span key={key} className="workflow-meta">
                            {key}: {String(value)}
                          </span>
                        ))}
                      </li>
                    ))}
                  </ul>
                )}
                <h4>LoRA 节点</h4>
                {detail.report.lora_nodes.length === 0 ? (
                  <p className="muted">未识别到常见 LoRA 节点。</p>
                ) : (
                  <ul>
                    {detail.report.lora_nodes.map((node) => (
                      <li key={node.node_id}>
                        节点 {node.node_id} · {node.class_type}
                        {Object.entries(node.inputs).map(([key, value]) => (
                          <span key={key} className="workflow-meta">
                            {key}: {String(value)}
                          </span>
                        ))}
                      </li>
                    ))}
                  </ul>
                )}
                <h4>历史版本</h4>
                {versions.length === 0 ? (
                  <p className="muted">暂无保存过的历史版本。</p>
                ) : (
                  <ul>
                    {versions.map((entry) => {
                      const positive =
                        entry.prompts.find((p) => p.role === "positive")?.text ?? "";
                      const negative =
                        entry.prompts.find((p) => p.role === "negative")?.text ?? "";
                      const restoring = restoringStamp === entry.stamp;
                      const confirming = confirmStamp === entry.stamp;
                      const comparing = comparingStamp === entry.stamp;
                      const positiveSame = positive.trim() === positiveText.trim();
                      const negativeSame = negative.trim() === negativeText.trim();
                      return (
                        <li key={entry.stamp}>
                          <small className="workflow-meta">
                            {formatVersionStamp(entry.stamp)}
                          </small>
                          <div>正向：{clipText(positive)}</div>
                          <div>负向：{clipText(negative)}</div>
                          <div className="actions">
                            {confirming ? (
                              <>
                                <button
                                  type="button"
                                  disabled={restoring || busy}
                                  onClick={() => void restoreVersion(entry.stamp)}
                                >
                                  {restoring ? "正在恢复…" : "再次点击确认恢复"}
                                </button>
                                <button
                                  type="button"
                                  disabled={restoring || busy}
                                  onClick={() => setConfirmStamp(null)}
                                >
                                  取消
                                </button>
                              </>
                            ) : (
                              <button
                                type="button"
                                disabled={restoring || busy}
                                onClick={() => setConfirmStamp(entry.stamp)}
                              >
                                {restoring ? "正在恢复…" : "恢复此版本"}
                              </button>
                            )}
                            <button
                              type="button"
                              disabled={restoring || busy}
                              onClick={() =>
                                setComparingStamp(comparing ? null : entry.stamp)
                              }
                            >
                              {comparing ? "收起比对" : "比对当前副本"}
                            </button>
                          </div>
                          {comparing && (
                            <div className="workflow-meta">
                              <div>
                                正向：
                                <VersionDiffBlock
                                  snapshot={positive}
                                  current={positiveText}
                                  same={positiveSame}
                                />
                              </div>
                              <div>
                                负向：
                                <VersionDiffBlock
                                  snapshot={negative}
                                  current={negativeText}
                                  same={negativeSame}
                                />
                              </div>
                            </div>
                          )}
                        </li>
                      );
                    })}
                  </ul>
                )}
              </>
            )}
          </div>
        </div>
      </div>
      {detail && (
        <>
          {compare && (
            <div className="panel">
              <h3 id="step-03">规则与参数比对（只读）</h3>
              <div className="compare-meta">
                <span>
                  状态：{compare.status === "registered" ? "已登记" : "未登记"}
                </span>
                <span>Checkpoint：{compare.checkpoint ?? "—"}</span>
                <span>
                  架构：
                  {compare.architecture_label ?? compare.architecture ?? "—"}
                </span>
              </div>
              {compare.status !== "registered" ? (
                <p className="muted">
                  当前工作流未在规则库登记；不依据模型名猜测参数或风格。
                </p>
              ) : (
                <>
                  <div className="compare-counts">
                    {Object.entries(compare.counts).map(([status, count]) => (
                      <span key={status} className={`compare-badge ${status}`}>
                        {STATUS_LABEL[status] ?? status} · {count}
                      </span>
                    ))}
                  </div>
                  <h4>实际参数摘要</h4>
                  <div className="compare-actuals">
                    {ACTUAL_SUMMARY.map(({ key, label }) => (
                      <span key={key} className="workflow-meta">
                        {label}: {formatActual(compare.actuals[key])}
                      </span>
                    ))}
                    <span className="workflow-meta">
                      分辨率: {formatActual(compare.actuals.width)}×
                      {formatActual(compare.actuals.height)}
                    </span>
                  </div>
                  {(Number(compare.actuals.latent_node_count) ?? 0) > 1 && (
                    <p className="muted">
                      检测到 {formatActual(compare.actuals.latent_node_count)} 个
                      latent 节点，分辨率结论取首个；其余节点未参与比对。
                    </p>
                  )}
                  {(Number(compare.actuals.clip_skip_node_count) ?? 0) > 1 && (
                    <p className="muted">
                      检测到 {formatActual(compare.actuals.clip_skip_node_count)} 个
                      CLIP 跳过层节点，Clip Skip 结论取首个；其余节点未参与比对。
                    </p>
                  )}
                  {compare.conflict_entries &&
                  compare.conflict_entries.length > 0 ? (
                    <div className="compare-conflicts">
                      <h4>
                        待人工裁决的冲突（{compare.conflict_entries.length}）
                      </h4>
                      <p className="workflow-warning">
                        同一层级存在相互冲突的规则，系统不替选；请人工核对下列来源后再采用。
                      </p>
                      <div className="compare-table">
                        {compare.conflict_entries.map((entry) => (
                          <div className="compare-row" key={entry.key}>
                            <div className="compare-row-main">
                              <strong>{entry.key}</strong>
                              <small>
                                {entry.message ?? "需人工核对后再采用"}
                              </small>
                              <div className="compare-sources">
                                {(entry.sources ?? []).map((source, idx) => (
                                  <span
                                    className="rule-tag"
                                    key={`${source.level}-${source.scope_key ?? ""}-${idx}`}
                                  >
                                    {source.level}
                                    {source.scope_key ? ` · ${source.scope_key}` : ""}
                                    {` · ${source.provenance}`}
                                    {` · ${source.confidence}`}
                                    {` · v${source.version}`}
                                    {source.note ? ` · ${source.note}` : ""}
                                  </span>
                                ))}
                              </div>
                            </div>
                            <span className="compare-badge conflict">冲突</span>
                          </div>
                        ))}
                      </div>
                    </div>
                  ) : null}
                  <h4>比对条目</h4>
                  <div className="compare-table">
                    {compare.comparisons.length === 0 ? (
                      <p className="muted">无可比对的规则字段。</p>
                    ) : (
                      compare.comparisons.map((entry) => (
                        <div className="compare-row" key={entry.key}>
                          <div className="compare-row-main">
                            <strong>{entry.key}</strong>
                            <small>
                              规则：{formatActual(entry.rule)} · 实际：
                              {formatActual(entry.actual)}
                            </small>
                          </div>
                          <span className={`compare-badge ${entry.status}`}>
                            {STATUS_LABEL[entry.status] ?? entry.status}
                            {entry.fell_back ? "（指南套放宽）" : entry.set_role === "specific" ? "（专用套）" : ""}
                            {entry.reason ? `（${entry.reason}）` : ""}
                          </span>
                        </div>
                      ))
                    )}
                  </div>
                  {compare.sampler_group_comparisons &&
                    compare.sampler_group_comparisons.length > 1 && (
                      <>
                        <h4>逐采样器组比对</h4>
                        <p className="muted">
                          多组采样器各自独立比对，不合并、不替选；顶层结论对应首组。
                        </p>
                        <div className="compare-groups">
                          {compare.sampler_group_comparisons.map((group, idx) => (
                            <div className="compare-group" key={idx}>
                              <div className="compare-group-head">
                                <strong>
                                  采样器组 {idx + 1}
                                  {group.sampler_node_id
                                    ? ` · 节点 ${group.sampler_node_id}`
                                    : ""}
                                </strong>
                                <span className="compare-counts">
                                  {Object.entries(group.counts).map(([status, count]) => (
                                    <span
                                      key={status}
                                      className={`compare-badge ${status}`}
                                    >
                                      {STATUS_LABEL[status] ?? status} · {count}
                                    </span>
                                  ))}
                                </span>
                              </div>
                              <div className="compare-actuals">
                                <span className="workflow-meta">
                                  步数: {formatActual(group.params.steps)}
                                </span>
                                <span className="workflow-meta">
                                  CFG: {formatActual(group.params.cfg)}
                                </span>
                                <span className="workflow-meta">
                                  采样器: {formatActual(group.params.sampler_name)}
                                </span>
                                <span className="workflow-meta">
                                  调度器: {formatActual(group.params.scheduler)}
                                </span>
                              </div>
                              <div className="compare-table">
                                {group.comparisons.map((entry) => (
                                  <div className="compare-row" key={entry.key}>
                                    <div className="compare-row-main">
                                      <strong>{entry.key}</strong>
                                      <small>
                                        规则：{formatActual(entry.rule)} · 实际：
                                        {formatActual(entry.actual)}
                                      </small>
                                    </div>
                                    <span className={`compare-badge ${entry.status}`}>
                                      {STATUS_LABEL[entry.status] ?? entry.status}
                                      {entry.reason ? `（${entry.reason}）` : ""}
                                    </span>
                                  </div>
                                ))}
                              </div>
                            </div>
                          ))}
                        </div>
                      </>
                    )}
                </>
              )}
            </div>
          )}
          <div className="panel">
            <h3 id="step-04">Prompt 节点确认</h3>
            {detail.report.prompt_nodes.length === 0 ? (
              <p className="muted">
                没有识别到常见文本编码节点。请确认导入的工作流类型和节点结构。
              </p>
            ) : (
              <>
                <p className="muted">
                  请逐个确认节点用途。自动识别结果只是建议，最终以你的选择为准。
                </p>
                <div className="workflow-node-list">
                  {detail.report.prompt_nodes.map((node) => (
                    <div className="workflow-node-row" key={node.node_id}>
                      <div>
                        <strong>
                          节点 {node.node_id} · {node.class_type}
                        </strong>
                        <small>
                          {node.text.slice(0, 180) || "（空文本）"}
                        </small>
                      </div>
                      <select
                        value={roles[node.node_id] ?? ""}
                        disabled={
                          busy ||
                          !detail.report.safe_prompt_write_supported ||
                          !node.editable
                        }
                        onChange={(event) =>
                          setRoles((old) => ({
                            ...old,
                            [node.node_id]: event.target.value as RoleChoice,
                          }))
                        }
                      >
                        <option value="">不参与</option>
                        <option value="positive">正向 Prompt</option>
                        <option value="negative">负向 Prompt</option>
                      </select>
                    </div>
                  ))}
                </div>
              </>
            )}
            {detail.report.notes.map((note) => (
              <p className="workflow-warning" key={note}>{note}</p>
            ))}
            {detail.report.safe_prompt_write_supported && (
              <div className="actions">
                <button
                  disabled={busy || detail.report.prompt_nodes.length === 0}
                  onClick={() => void confirmNodes()}
                >
                  确认 Prompt 白名单
                </button>
                <span className="muted">
                  {detail.manifest.confirmed ? "白名单已确认" : "尚未确认"}
                </span>
              </div>
            )}
          </div>
          {detail && (
            <div className="panel">
              <div className="candidate-heading">
                <h3 id="step-05">提示词候选</h3>
                <p className="candidate-subtitle">
                  {isWritingIdentity
                    ? "本地 LLM 按身份、题材与文风生成写作内容"
                    : "本地 LLM 起草或润色提示词，模型专用规则按 checkpoint 与架构自动匹配"}
                </p>
              </div>
              {!modelsOnline ? (
                <p className="muted">本地 Ollama 不可用，无法生成候选。</p>
              ) : ollamaModels.length === 0 ? (
                <p className="muted">未发现本地模型。</p>
              ) : (
                <>
                  <div className="candidate-box">
                    <textarea
                      className="candidate-input"
                      rows={5}
                      placeholder="输入提示词要求或描写（可选）"
                      value={taskText}
                      disabled={busy || candidateBusy}
                      onChange={(event) => {
                        setTaskText(event.target.value);
                        persistParam(
                          "workbench.candidateTask",
                          event.target.value,
                        );
                      }}
                    />
                    <div className="candidate-controls">
                      <div className="candidate-primary">
                      <select
                        value={selectedModel}
                        disabled={busy || candidateBusy}
                        onChange={(event) => {
                          setSelectedModel(event.target.value);
                          persistParam(
                            "workbench.selectedModel",
                            event.target.value,
                          );
                        }}
                      >
                        {ollamaModels.map((model) => {
                          const params = modelDetails[model];
                          const label = params ? `${model}（${params}）` : model;
                          return (
                            <option key={model} value={model}>
                              {label}
                            </option>
                          );
                        })}
                      </select>
                      <select
                        value={selectedPersona}
                        disabled={busy || candidateBusy}
                        onChange={(event) => {
                          setSelectedPersona(event.target.value);
                          persistParam(
                            "workbench.selectedPersona",
                            event.target.value,
                          );
                        }}
                        title="LLM 身份定位；裸模型表示不注入身份系统提示词"
                      >
                        <option value="none">裸模型（不注入身份）</option>
                        {promptPersonas.map((persona) => (
                          <option
                            key={persona.id}
                            value={persona.id}
                            disabled={persona.enabled === false}
                          >
                            {persona.display_name}
                          </option>
                        ))}
                      </select>
                      {!isWritingIdentity && (
                        <select
                          value={selectedProfile}
                          disabled={busy || candidateBusy}
                          onChange={(event) => {
                            setSelectedProfile(event.target.value);
                            persistParam(
                              "workbench.selectedProfile",
                              event.target.value,
                            );
                          }}
                          title="模型专用规则 Profile；自动匹配按 checkpoint 文件名与架构选择"
                        >
                          <option value="auto">自动匹配 Profile</option>
                          <option value="none">不使用规则套</option>
                          {profiles.map((profile) => {
                            const arch = profile.architecture
                              ? `｜${profile.architecture}`
                              : "";
                            const stateTag = profile.enabled ? "" : "（已停用）";
                            return (
                              <option
                                key={profile.id}
                                value={profile.id}
                                disabled={!profile.enabled}
                              >
                                {profile.display_name}
                                {arch}
                                {stateTag}
                              </option>
                            );
                          })}
                        </select>
                      )}
                      {isWritingIdentity && (
                        <>
                          <select
                            value={selectedGenre}
                            disabled={busy || candidateBusy}
                            onChange={(event) => {
                              setSelectedGenre(event.target.value);
                              persistParam(
                                "workbench.selectedGenre",
                                event.target.value,
                              );
                            }}
                            title="写作题材设定"
                          >
                            <option value="">不指定题材</option>
                            {genres.map((genre) => (
                              <option key={genre.id} value={genre.id}>
                                {genre.display_name}
                              </option>
                            ))}
                          </select>
                          <select
                            value={selectedStyle}
                            disabled={busy || candidateBusy}
                            onChange={(event) => {
                              setSelectedStyle(event.target.value);
                              persistParam(
                                "workbench.selectedStyle",
                                event.target.value,
                              );
                            }}
                            title="写作文风"
                          >
                            <option value="">不指定文风</option>
                            {styles.map((style) => (
                              <option key={style.id} value={style.id}>
                                {style.display_name}
                              </option>
                            ))}
                          </select>
                        </>
                      )}
                      <select
                        value={candidateMode}
                        disabled={busy || candidateBusy}
                        onChange={(event) => {
                          setCandidateMode(event.target.value);
                          persistParam(
                            "workbench.candidateMode",
                            event.target.value,
                          );
                        }}
                      >
                        {candidateModeOptions.map((opt) => (
                          <option key={opt.id} value={opt.id}>
                            {opt.display_name}
                          </option>
                        ))}
                      </select>
                      </div>
                      <div className="candidate-params">
                      <input
                        type="text"
                        placeholder="温度（0–2）"
                        value={candidateTemp}
                        disabled={busy || candidateBusy}
                        onChange={(event) => {
                          setCandidateTemp(event.target.value);
                          persistParam("workbench.candidateTemp", event.target.value);
                        }}
                      />
                      <input
                        type="text"
                        placeholder="Top P（0–1）"
                        value={candidateTopP}
                        disabled={busy || candidateBusy}
                        onChange={(event) => {
                          setCandidateTopP(event.target.value);
                          persistParam("workbench.candidateTopP", event.target.value);
                        }}
                      />
                      <input
                        type="text"
                        placeholder="上下文长度"
                        value={candidateCtx}
                        disabled={busy || candidateBusy}
                        onChange={(event) => {
                          setCandidateCtx(event.target.value);
                          persistParam("workbench.candidateCtx", event.target.value);
                        }}
                      />
                      </div>
                      <button
                        className="candidate-submit"
                        disabled={busy || candidateBusy || !selectedModel}
                        onClick={() => void generateCandidate()}
                      >
                        {candidateBusy ? "生成中…" : "生成提示词候选"}
                      </button>
                    </div>
                  </div>
                  {candidates.length > 0 && (
                    <div className="candidate-list">
                      <div className="candidate-list-head">
                        <span>候选 {candidates.length} 条（只读，采用后进入下方编辑框）</span>
                        <button onClick={clearCandidates}>清空全部</button>
                      </div>
                      {candidates.map((item, index) => {
                        const modeLabel =
                          modes.find((o) => o.id === item.mode)?.display_name ??
                          item.mode;
                        const active = activeCandidate === index;
                        const comparing = compareOther === index;
                        return (
                          <div
                            key={index}
                            className={`candidate-card${active ? " active" : ""}`}
                          >
                            <div className="candidate-card-head">
                              <button
                                className="candidate-title"
                                onClick={() => {
                                  setActiveCandidate(index);
                                  setCompareOther(null);
                                }}
                              >
                                <span className="candidate-mode">{modeLabel}</span>
                                <span
                                  className={`candidate-scope ${item.scope}`}
                                >
                                  {item.scope === "negative" ? "写入负向" : "写入正向"}
                                </span>
                              </button>
                              <div className="actions">
                                <button
                                  className={comparing ? "active" : ""}
                                  onClick={() => toggleCompareOther(index)}
                                >
                                  {comparing ? "取消对比" : "加入对比"}
                                </button>
                                <button onClick={() => void copyCandidate(item)}>复制</button>
                                <button onClick={() => adoptCandidate(item)}>
                                  {item.scope === "negative" ? "采用到负向" : "采用到正向"}
                                </button>
                                <button onClick={() => removeCandidate(index)}>移除</button>
                              </div>
                            </div>
                            <textarea readOnly rows={6} value={item.text} />
                          </div>
                        );
                      })}
                      {compareOther !== null &&
                        activeCandidate !== null &&
                        compareOther !== activeCandidate && (
                          <div className="candidate-compare">
                            <div className="candidate-compare-col">
                              <span>当前选中</span>
                              <textarea
                                readOnly
                                rows={8}
                                value={candidates[activeCandidate].text}
                              />
                            </div>
                            <div className="candidate-compare-col">
                              <span>对比项</span>
                              <textarea
                                readOnly
                                rows={8}
                                value={candidates[compareOther].text}
                              />
                            </div>
                          </div>
                        )}
                    </div>
                  )}
                </>
              )}
            </div>
          )}
          {canWrite && (
            <div className="panel">
              <h3 id="step-06">编辑工作副本 Prompt</h3>
              <div className="prompt-pair">
              {detail.manifest.whitelist.some(
                (item) => item.role === "positive",
              ) && (
                <label className="workflow-text-field">
                  <div className="workflow-field-head">
                    <span>
                      正向 Prompt{" "}
                      <span className="muted">{promptCountLabel(positiveText)}</span>
                    </span>
                    <button onClick={copyPositive}>复制正向</button>
                  </div>
                  <textarea
                    value={positiveText}
                    onChange={(event) => setPositiveText(event.target.value)}
                    rows={7}
                    disabled={busy}
                  />
                </label>
              )}
              {detail.manifest.whitelist.some(
                (item) => item.role === "negative",
              ) && (
                <label className="workflow-text-field">
                  <div className="workflow-field-head">
                    <span>
                      负向 Prompt{" "}
                      <span className="muted">{promptCountLabel(negativeText)}</span>
                    </span>
                    <button onClick={copyNegative}>复制负向</button>
                  </div>
                  <textarea
                    value={negativeText}
                    onChange={(event) => setNegativeText(event.target.value)}
                    rows={7}
                    disabled={busy}
                  />
                </label>
              )}
              </div>
              {Array.from(
                new Set([
                  ...splitPromptFragments(positiveText),
                  ...splitPromptFragments(negativeText),
                ]),
              ).length > 0 && (
                <div className="workflow-text-field">
                  <span>
                    片段锁定（勾选后生成候选时原样保留，不被改写或删除）
                  </span>
                  <div className="locked-fragments">
                    {Array.from(
                      new Set([
                        ...splitPromptFragments(positiveText),
                        ...splitPromptFragments(negativeText),
                      ]),
                    ).map((fragment) => (
                      <label key={fragment} className="locked-fragment">
                        <input
                          type="checkbox"
                          checked={lockedFragments.includes(fragment)}
                          disabled={busy}
                          onChange={() => toggleLockedFragment(fragment)}
                        />
                        <span>{fragment}</span>
                      </label>
                    ))}
                  </div>
                </div>
              )}
              <div className="actions">
                <button disabled={busy} onClick={() => void writePrompts()}>
                  写入工作副本并校验
                </button>
                <button onClick={copyBoth}>复制完整</button>
                <button onClick={exportPromptTxt}>导出 TXT</button>
                <button onClick={exportPromptJson}>导出 JSON</button>
                <label className="workflow-import-button">
                  导入 TXT/JSON
                  <input
                    type="file"
                    accept=".txt,.json,application/json,text/plain"
                    disabled={busy}
                    onChange={(event) => {
                      const file = event.target.files?.[0];
                      void onPromptFileChosen(file);
                      event.currentTarget.value = "";
                    }}
                  />
                </label>
                <button onClick={() => setPasteOpen((v) => !v)}>粘贴文本</button>
                <span className="muted">
                  不会修改原始文件，也不会提交 ComfyUI 任务。
                </span>
              </div>
              {pasteOpen && (
                <div className="workflow-text-field">
                  <span>粘贴 Prompt 文本（可含正向/负向标记，或导出 JSON）</span>
                  <textarea
                    rows={6}
                    value={pasteText}
                    disabled={busy}
                    onChange={(event) => setPasteText(event.target.value)}
                    placeholder={"正向：…\n负向：…"}
                  />
                  <div className="actions">
                    <button onClick={parsePasted}>解析到预览</button>
                    <button onClick={cancelPaste}>取消</button>
                    <span className="muted">
                      粘贴内容不会自动写入工作副本。
                    </span>
                  </div>
                </div>
              )}
              {importedPrompt && (
                <div className="workflow-text-field">
                  <span>导入预览（只读，采用后进入下方编辑框）</span>
                  {importedPrompt.positive && (
                    <>
                      <div className="workflow-field-head">
                        <span>正向 Prompt</span>
                      </div>
                      <textarea readOnly rows={5} value={importedPrompt.positive} />
                    </>
                  )}
                  {importedPrompt.negative && (
                    <>
                      <div className="workflow-field-head">
                        <span>负向 Prompt</span>
                      </div>
                      <textarea readOnly rows={5} value={importedPrompt.negative} />
                    </>
                  )}
                  <div className="actions">
                    <button onClick={adoptImported}>采用到编辑框</button>
                    <button onClick={dismissImported}>放弃导入</button>
                    <span className="muted">
                      导入内容不会自动写入工作副本。
                    </span>
                  </div>
                </div>
              )}
            </div>
          )}
          {canWrite && (
            <div className="panel" id="step-07">
              <div className="section-heading">
                <div>
                  <h3>草稿箱</h3>
                  <p className="muted">
                    手动保存的 Prompt 组合；保存与更新都不写入工作副本，载入编辑框后仍需确认写入。
                  </p>
                </div>
                <button onClick={() => setDraftPanelOpen(true)}>
                  打开草稿箱{drafts.length > 0 ? `（${drafts.length}）` : ""}
                </button>
              </div>
            </div>
          )}
          {canWrite && draftPanelOpen && (
            <div
              className="lightbox-overlay"
              onClick={() => !draftBusy && setDraftPanelOpen(false)}
            >
              <div
                className="asset-detail"
                onClick={(event) => event.stopPropagation()}
              >
                <div className="asset-dialog-head">
                  <h3>草稿箱{drafts.length > 0 ? `（${drafts.length}）` : ""}</h3>
                  <button
                    type="button"
                    className="lightbox-close"
                    onClick={() => setDraftPanelOpen(false)}
                    aria-label="关闭"
                  >×</button>
                </div>
                <p className="muted">
                  手动保存的 Prompt 组合；保存与更新都不写入工作副本，载入编辑框后仍需确认写入。
                  载入后可在上方名称 / 备注框修改，更新时用当前编辑框内容与名称备注一并覆盖该草稿。
                </p>
              <div className="actions">
                <input
                  type="text"
                  placeholder="草稿名称"
                  value={draftName}
                  disabled={draftBusy || busy}
                  onChange={(event) => setDraftName(event.target.value)}
                />
                <input
                  type="text"
                  placeholder="备注（可选）"
                  value={draftNote}
                  disabled={draftBusy || busy}
                  onChange={(event) => setDraftNote(event.target.value)}
                />
                <input
                  type="text"
                  placeholder="标签（逗号分隔）"
                  value={draftTags}
                  disabled={draftBusy || busy}
                  onChange={(event) => setDraftTags(event.target.value)}
                />
                <button
                  disabled={draftBusy || busy}
                  onClick={() => void saveDraft()}
                >
                  保存当前为草稿
                </button>
              </div>
              {drafts.length > 0 && (
                <>
                <div className="actions">
                  <input
                    type="text"
                    placeholder="按名称 / 备注 / Prompt 过滤草稿"
                    value={draftKeyword}
                    disabled={draftBusy || busy}
                    onChange={(event) => {
                      setDraftKeyword(event.target.value);
                      persistParam("workbench.draftKeyword", event.target.value);
                    }}
                  />
                  {allDraftTags.length > 0 && (
                    <select
                      value={tagFilter}
                      disabled={draftBusy || busy}
                      onChange={(event) => {
                        setTagFilter(event.target.value);
                        persistTagFilter(event.target.value);
                      }}
                    >
                      <option value="">全部标签</option>
                      {allDraftTags.map((tag) => (
                        <option key={tag} value={tag}>
                          {tag}
                        </option>
                      ))}
                    </select>
                  )}
                  <select
                    value={draftGroupMode}
                    disabled={draftBusy || busy}
                    onChange={(event) => {
                      const next = event.target.value as DraftGroupMode;
                      setDraftGroupMode(next);
                      persistDraftGroupMode(next);
                    }}
                  >
                    <option value="section">按分区</option>
                    <option value="tag">按标签分组</option>
                  </select>
                  <label className="workflow-meta">
                    <input
                      type="checkbox"
                      checked={favoredOnly}
                      disabled={draftBusy || busy || showTrashed}
                      onChange={(event) => {
                        setFavoredOnly(event.target.checked);
                        persistFavoredOnly(event.target.checked);
                      }}
                    />
                    仅看收藏
                  </label>
                  <label className="workflow-meta">
                    <input
                      type="checkbox"
                      checked={showTrashed}
                      disabled={draftBusy || busy}
                      onChange={(event) => {
                        const next = event.target.checked;
                        setShowTrashed(next);
                        persistShowTrashed(next);
                        if (workflowId) void loadDrafts(workflowId, next);
                      }}
                    />
                    回收站
                  </label>
                  <button
                    onClick={exportDrafts}
                    disabled={draftBusy || busy || filteredDrafts.length === 0}
                  >
                    导出 JSON
                  </button>
                  <button
                    onClick={() => setTagRenameOpen((open) => !open)}
                    disabled={draftBusy || busy || allDraftTags.length === 0}
                  >
                    标签重命名
                  </button>
                </div>
                {tagRenameOpen && (
                  <div className="draft-tag-rename">
                    <small className="muted">
                      跨所有草稿把原标签替换为新标签并去重；其他内容不变。
                    </small>
                    <div className="actions">
                      <select
                        value={renameOld}
                        disabled={renameBusy || draftBusy}
                        onChange={(event) => setRenameOld(event.target.value)}
                      >
                        <option value="">选择原标签</option>
                        {allDraftTags.map((tag) => (
                          <option key={tag} value={tag}>
                            {tag}
                          </option>
                        ))}
                      </select>
                      <input
                        type="text"
                        placeholder="新标签"
                        value={renameNew}
                        disabled={renameBusy || draftBusy}
                        maxLength={30}
                        onChange={(event) => setRenameNew(event.target.value)}
                      />
                      <button
                        onClick={() => void submitTagRename()}
                        disabled={
                          renameBusy ||
                          draftBusy ||
                          !renameOld.trim() ||
                          !renameNew.trim()
                        }
                      >
                        {renameBusy ? "处理中…" : "确认重命名"}
                      </button>
                      <button
                        onClick={() => {
                          setTagRenameOpen(false);
                          setRenameOld("");
                          setRenameNew("");
                        }}
                        disabled={renameBusy || draftBusy}
                      >
                        取消
                      </button>
                    </div>
                  </div>
                )}
                </>
              )}
              {drafts.length === 0 ? (
                <p className="muted">还没有保存的草稿。</p>
              ) : filteredDrafts.length === 0 ? (
                <p className="muted">没有匹配的草稿。</p>
              ) : (
                <div className="workflow-node-list">
                  {groupedDrafts.map((entry, index) => {
                    const previous = groupedDrafts[index - 1];
                    const { draft } = entry;
                    const groupLabel =
                      draftGroupMode === "tag"
                        ? entry.groupKey
                        : DRAFT_GROUP_LABEL[draftGroup(draft)];
                    const showGroupHead =
                      (!showTrashed || draftGroupMode === "tag") &&
                      (!previous || previous.groupKey !== entry.groupKey);
                    return (
                      <Fragment key={`${draft.id}-${entry.groupKey}`}>
                        {showGroupHead && (
                          <div className="draft-group-head">
                            {groupLabel}
                          </div>
                        )}
                        <div className="workflow-node-row">
                          <div>
                            <strong>{draft.name}</strong>
                        {showTrashed && (
                          <small className="workflow-meta">已删除</small>
                        )}
                        {draft.pinned && !showTrashed && (
                          <small className="workflow-meta">置顶</small>
                        )}
                        {draft.favored && !showTrashed && (
                          <small className="workflow-meta">已收藏</small>
                        )}
                        <small>正向：{clipText(draft.positive)}</small>
                        <small>负向：{clipText(draft.negative)}</small>
                        {draft.note && (
                          <small className="workflow-meta">备注：{draft.note}</small>
                        )}
                        {(draft.tags || []).length > 0 && (
                          <small className="workflow-meta">
                            标签：{(draft.tags || []).join("、")}
                          </small>
                        )}
                        {draft.source_history_id && (
                          <small className="workflow-meta">
                            来源历史：
                            <button
                              className="secondary"
                              onClick={() => goToHistory(draft.source_history_id!)}
                              title="跳转到生成历史"
                            >
                              {draft.source_history_id.slice(0, 8)}
                            </button>
                          </small>
                        )}
                        {envSnapshotSummary(draft.env_snapshot) && (
                          <small className="workflow-meta">
                            环境：{envSnapshotSummary(draft.env_snapshot)}
                          </small>
                        )}
                        {(draft.images || []).length > 0 && (
                          <div className="draft-thumbs">
                            {(draft.images || [])
                              .filter((image) => image.exists !== false)
                              .slice(0, 6)
                              .map((image, index) => (
                                <button
                                  key={`${image.subfolder}/${image.filename}-${index}`}
                                  className="draft-thumb"
                                  title={image.filename}
                                  onClick={() =>
                                    image.prompt_id &&
                                    goToHistory(image.prompt_id)
                                  }
                                >
                                  <img
                                    src={draftImageUrl(image)}
                                    alt={image.filename}
                                    loading="lazy"
                                  />
                                </button>
                              ))}
                          </div>
                        )}
                      </div>
                      <div className="actions">
                        {!showTrashed && (
                        <>
                        <button
                          onClick={() => adoptDraft(draft)}
                          disabled={draftBusy || busy}
                        >
                          载入到编辑框
                        </button>
                        <button
                          onClick={() => void toggleFavorite(draft)}
                          disabled={draftBusy || busy}
                        >
                          {draft.favored ? "取消收藏" : "收藏"}
                        </button>
                        <button
                          onClick={() => void togglePin(draft)}
                          disabled={draftBusy || busy}
                        >
                          {draft.pinned ? "取消置顶" : "置顶"}
                        </button>
                        {copyingDraftId === draft.id ? (
                          <>
                            <select
                              value={copyTargetId}
                              disabled={draftBusy || busy}
                              onChange={(event) => setCopyTargetId(event.target.value)}
                            >
                              <option value="">选择目标工作流</option>
                              {copyTargets.map((workflow) => (
                                <option key={workflow.id} value={workflow.id}>
                                  {workflow.filename}
                                </option>
                              ))}
                            </select>
                            <button
                              onClick={() => void copyDraftTo(draft)}
                              disabled={draftBusy || busy || !copyTargetId}
                            >
                              确认复制？
                            </button>
                            <button
                              onClick={() => {
                                setCopyingDraftId(null);
                                setCopyTargetId("");
                              }}
                              disabled={draftBusy || busy}
                            >
                              取消
                            </button>
                          </>
                        ) : (
                          <button
                            onClick={() => {
                              setCopyingDraftId(draft.id);
                              setCopyTargetId("");
                            }}
                            disabled={draftBusy || busy || copyTargets.length === 0}
                          >
                            复制到…
                          </button>
                        )}
                        {updatingDraftId === draft.id ? (
                          <>
                            <button
                              onClick={() => void updateDraft(draft)}
                              disabled={draftBusy || busy}
                            >
                              确认更新？
                            </button>
                            <button
                              onClick={() => setUpdatingDraftId(null)}
                              disabled={draftBusy || busy}
                            >
                              取消
                            </button>
                          </>
                        ) : (
                          <button
                            onClick={() => setUpdatingDraftId(draft.id)}
                            disabled={draftBusy || busy}
                          >
                            更新
                          </button>
                        )}
                        {deletingDraftId === draft.id ? (
                          <>
                            <button
                              onClick={() => void deleteDraft(draft.id)}
                              disabled={draftBusy || busy}
                            >
                              确认移入回收站？
                            </button>
                            <button
                              onClick={() => setDeletingDraftId(null)}
                              disabled={draftBusy || busy}
                            >
                              取消
                            </button>
                          </>
                        ) : (
                          <button
                            onClick={() => setDeletingDraftId(draft.id)}
                            disabled={draftBusy || busy}
                          >
                            移入回收站
                          </button>
                        )}
                        </>
                        )}
                        {showTrashed && (
                        <>
                          <button
                            onClick={() => void restoreDraft(draft)}
                            disabled={draftBusy || busy}
                          >
                            恢复
                          </button>
                          {purgingDraftId === draft.id ? (
                            <>
                              <button
                                onClick={() => void purgeDraft(draft.id)}
                                disabled={draftBusy || busy}
                              >
                                确认彻底删除？
                              </button>
                              <button
                                onClick={() => setPurgingDraftId(null)}
                                disabled={draftBusy || busy}
                              >
                                取消
                              </button>
                            </>
                          ) : (
                            <button
                              onClick={() => setPurgingDraftId(draft.id)}
                              disabled={draftBusy || busy}
                            >
                              彻底删除
                            </button>
                          )}
                        </>
                        )}
                        </div>
                        </div>
                      </Fragment>
                    );
                  })}
                </div>
              )}
              </div>
            </div>
          )}
          {detail && (
            <div className="panel">
              <h3 id="step-08">画布出图</h3>
              {detail.manifest.format !== "api" ? (
                <p className="muted">
                  UI 格式工作流不能导出 API JSON，请先在 ComfyUI 中导出 API 格式。
                </p>
              ) : (
                <>
                  <p className="muted">
                    工作台只把提示词写入工作副本，不直接提交 ComfyUI。确认无误后导出 API
                    工作流 JSON，拖入 ComfyUI 画布排队出图；画布直接生成的记录在历史页手动同步补录。
                  </p>
                  <div className="actions">
                    <button
                      onClick={() => void writePrompts()}
                      disabled={busy || !detail.manifest.confirmed}
                    >
                      覆盖保存
                    </button>
                    <button
                      onClick={() => void exportWorkflowJson()}
                      disabled={busy}
                    >
                      导出工作流 JSON
                    </button>
                    <button
                      onClick={() => void resetWorkingCopy()}
                      disabled={busy}
                    >
                      清空本次写入
                    </button>
                    <button
                      onClick={() => void releaseVram()}
                      disabled={releaseBusy || busy}
                    >
                      {releaseBusy ? "卸载中…" : "卸载 LLM 和底模"}
                    </button>
                    <span className="muted">
                      {detail.manifest.confirmed ? "白名单已确认" : "需先确认白名单"}
                    </span>
                  </div>
                </>
              )}
            </div>
          )}
        </>
      )}
      <div className="workflow-message" role="status">
        <span>{message}</span>
        {copyJumpId && (
          <button
            type="button"
            onClick={() => {
              const targetId = copyJumpId;
              setCopyJumpId(null);
              void openWorkflow(targetId);
            }}
          >
            前往目标工作流
          </button>
        )}
      </div>
    </section>
  );
}
