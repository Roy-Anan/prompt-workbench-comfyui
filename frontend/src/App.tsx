import { useEffect, useRef, useState, type ChangeEvent } from "react";
import {
  apiDelete,
  apiGet,
  apiGetBlob,
  apiPost,
  apiPostBinary,
} from "./api";
import { formatStructured } from "./format";
import AssetLibrary from "./AssetLibrary";
import HistoryPanel from "./HistoryPanel";
import WorkflowWorkbench from "./WorkflowWorkbench";

type PageKey = "settings" | "workflows" | "history" | "assets" | "loras";

const PAGE_KEYS: PageKey[] = ["settings", "workflows", "history", "assets", "loras"];

function readStoredPage(): PageKey {
  try {
    const value = localStorage.getItem("workbench.activePage");
    if (PAGE_KEYS.includes(value as PageKey)) return value as PageKey;
  } catch {
    // 本地存储不可用时回落到默认页签。
  }
  return "settings";
}

type Theme = "dark" | "light";
type FontSize = "small" | "normal" | "large";

function systemTheme(): Theme {
  try {
    return window.matchMedia("(prefers-color-scheme: light)").matches
      ? "light"
      : "dark";
  } catch {
    return "dark";
  }
}

function readStoredTheme(): Theme {
  try {
    const value = localStorage.getItem("workbench.theme");
    if (value === "dark" || value === "light") return value;
  } catch {
    // 本地存储不可用时跟随系统。
  }
  return systemTheme();
}

function readStoredFont(): FontSize {
  try {
    const value = localStorage.getItem("workbench.font");
    if (value === "small" || value === "normal" || value === "large") return value;
  } catch {
    // 本地存储不可用时回落到标准字号。
  }
  return "normal";
}

type Health = {
  ok: boolean;
  app: string;
  version: string;
  python: string;
  platform: string;
};

type ServiceResult = {
  online: boolean;
  status_code: number | null;
  url: string;
  error: string | null;
};

type ServiceStatus = {
  ollama: ServiceResult;
  comfyui: ServiceResult;
};

type ComfyDevice = {
  index: number | null;
  name: string | null;
  type: string | null;
  vram_total: number | null;
  vram_free: number | null;
};

type ComfyStatus = {
  online: boolean;
  error: string | null;
  version: string | null;
  pytorch_version: string | null;
  ram_total: number | null;
  ram_free: number | null;
  devices: ComfyDevice[];
};

type ComfyQueue = {
  online: boolean;
  error: string | null;
  running: string[];
  pending: string[];
  history: string[];
};

function formatGiB(bytes: number | null): string {
  if (bytes === null || Number.isNaN(bytes) || bytes < 0) return "—";
  return `${(bytes / 1024 ** 3).toFixed(1)} GB`;
}

// 与后端 comfy_client.VRAM_FREE_MIN_BYTES 对齐：5 GiB 空闲方可提交。
const VRAM_FREE_MIN_BYTES = 5 * 1024 ** 3;

function vramGateLabel(free: number | null): string {
  if (free === null || Number.isNaN(free) || free < 0) return "显存未知";
  return free >= VRAM_FREE_MIN_BYTES ? "达标" : "不足 5GB";
}

type Settings = {
  storage: Record<string, string>;
  connections: {
    ollama_url: string;
    comfyui_url: string;
  };
  prompt?: {
    protected_terms?: string[];
  };
};

type PathCheck = {
  path: string;
  exists: boolean;
  is_directory: boolean;
  writable: boolean;
};

type IdentityCopy = {
  relative_name: string;
  absolute_path: string;
  on_disk: boolean;
};

type IdentitySummary = {
  known: boolean;
  on_disk?: boolean;
  size_bytes?: number | null;
  sha256?: string | null;
  hashed?: boolean;
  identity_key?: string | null;
  copy_count?: number;
  copies?: IdentityCopy[];
};

type LoraMetaStatus = "complete" | "partial" | "empty" | "missing" | "unreadable";

type LoraCombo = { name: string; trigger_words: string[] };

type LoraSample = { asset_id: number; caption: string };

type LoraMeta = {
  name: string;
  status: LoraMetaStatus;
  has_json: boolean;
  trigger_words: string[];
  description: string;
  base_model: string;
  source?: "manual" | "sidecar" | "none";
  manual_present?: boolean;
  manual_unreadable?: boolean;
  combos?: LoraCombo[];
  tags?: string[];
  samples?: LoraSample[];
  hidden?: boolean;
};

type CatalogEntry = {
  name: string;
  on_disk: boolean | null;
  identity?: IdentitySummary;
  meta?: LoraMeta | null;
  related?: string[];
  related_triggers?: string[];
  compatible?: string[];
};

const LORA_META_LABEL: Record<LoraMetaStatus, string> = {
  complete: "资料完整",
  partial: "部分资料",
  empty: "资料为空",
  missing: "缺资料",
  unreadable: "资料不可读",
};

const LORA_SOURCE_LABEL: Record<"manual" | "sidecar" | "none", string> = {
  manual: "手动资料",
  sidecar: "自动扫描",
  none: "无资料",
};

type ModelCatalog = {
  online: boolean;
  error: string | null;
  checkpoints: CatalogEntry[];
  clips: CatalogEntry[];
  vaes: CatalogEntry[];
  loras: CatalogEntry[];
};

type RuleRecord = {
  id: number;
  level: string;
  scope_key: string | null;
  rule_key: string;
  value: unknown;
  structured: unknown;
  provenance: string;
  confidence: string;
  version: string;
  note: string;
};

type RuleHistoryItem = {
  history_id: number;
  level: string;
  scope_key: string | null;
  rule_key: string;
  value: unknown;
  structured: unknown;
  provenance: string;
  confidence: string;
  version: string;
  note: string;
  snapshot_at: string;
  reason: string;
};

type ProfileCandidate = {
  level: string;
  scope_key: string | null;
  rule_key: string;
  value: unknown;
  structured: unknown;
  provenance: string;
  confidence: string;
  version: string;
  note: string;
  diff?: "new" | "override" | "same";
  stored_value?: unknown;
  stored_version?: string | null;
};

type ProfileSummary = {
  file: string;
  filename: string | null;
  display_name: string | null;
  architecture: string | null;
  candidates: number;
  unknown_fields: string[];
  error: string | null;
};

type ProfileDirInfo = {
  path: string;
  exists: boolean;
};

type ProfilePreview = {
  file: string;
  filename: string | null;
  architecture: string | null;
  unknown_fields: string[];
  candidates: ProfileCandidate[];
};

type ProfileImportRejected = {
  rule_key: string;
  reason: string;
};

type ProfileImportReport = {
  file: string;
  filename: string;
  imported: number;
  rejected: ProfileImportRejected[];
  unknown_fields: string[];
  selected: boolean;
  unknown_keys: string[];
};

function profileCandidateKey(candidate: ProfileCandidate): string {
  return [candidate.level, candidate.scope_key || "", candidate.rule_key].join(
    "\u001f",
  );
}

const RULE_LEVEL_LABEL: Record<string, string> = {
  checkpoint: "模型档案",
  architecture: "架构基线",
  global: "全局规则",
};
const RULE_PROVENANCE_LABEL: Record<string, string> = {
  author: "作者",
  verified_mirror: "已核实镜像",
  community: "社区",
  local_test: "本机测试",
  unverified: "未核实",
};
const RULE_CONFIDENCE_LABEL: Record<string, string> = {
  authoritative: "权威",
  partial: "部分",
  unverified: "未核实",
};
const RULE_LEVEL_ORDER = ["checkpoint", "architecture", "global"];

// 档案条目大类（rule_key 中 "::" 前缀）的中文归类；映射不到时保留原文。
const RULE_SECTION_LABEL: Record<string, string> = {
  IDENTITY: "模型身份",
  ARCHITECTURE: "架构",
  "PROMPT STYLE": "提示词风格",
  "GENERATION SETTINGS": "生成参数",
  "MODEL BEHAVIOR": "模型表现",
  "LOCAL WORKFLOW": "本地工作流",
  "LOCAL TEST FEEDBACK": "本机测试反馈",
  CONFIDENCE: "可信度",
};

// 常见条目的中文译名；自由文本条目保留原文。
const RULE_FIELD_LABEL: Record<string, string> = {
  Steps: "步数",
  CFG: "提示词相关性",
  Sampler: "采样器",
  Scheduler: "调度器",
  Resolution: "分辨率",
  "Clip Skip": "CLIP 跳层",
  Strengths: "优点",
  Style: "风格倾向",
  Weaknesses: "短板",
  "Known Weaknesses": "已知短板",
  "Base Architecture": "基座架构",
  "Model Type": "模型类型",
  "Last Updated": "更新时间",
  "Uncertain fields": "待核实项",
  "Verified by author documentation": "作者文档核实",
  "Parameter Source": "参数来源",
  "Source Note": "来源说明",
  "NSFW / Mature Content Statement": "成人内容声明",
  "Preferred Prompt Format": "推荐提示词格式",
};

function splitRuleKey(ruleKey: string): { section: string; title: string } {
  const idx = ruleKey.indexOf("::");
  if (idx === -1) return { section: "", title: ruleKey };
  const section = ruleKey.slice(0, idx).trim();
  const rawTitle = ruleKey.slice(idx + 2).trim();
  return { section, title: RULE_FIELD_LABEL[rawTitle] ?? rawTitle };
}

function confidenceTagClass(confidence: string): string {
  if (confidence === "authoritative") return "rule-tag conf-authoritative";
  if (confidence === "partial") return "rule-tag conf-partial";
  return "rule-tag conf-unverified";
}

function provenanceTagClass(provenance: string): string {
  if (provenance === "author") return "rule-tag prov-author";
  if (provenance === "verified_mirror") return "rule-tag prov-mirror";
  if (provenance === "local_test") return "rule-tag prov-local";
  if (provenance === "community") return "rule-tag prov-community";
  return "rule-tag prov-unverified";
}

const storageLabels: Record<string, string> = {
  root: "工作台数据根目录（固定）",
  database: "数据库目录",
  knowledge: "模型知识库",
  prompts: "Prompt 规则",
  workflows: "工作流资料",
  drafts: "草稿与版本",
  history: "历史记录",
  cache: "缓存",
  logs: "日志",
  backups: "备份",
  exports: "导入导出",
  checkpoint: "Checkpoint 模型目录（只读扫描）",
  lora: "LoRA 模型目录（只读扫描）",
  clip: "CLIP 模型目录（只读扫描）",
  vae: "VAE 模型目录（只读扫描）",
};

// Optional external ComfyUI model directories: read-only scanned, never
// created by the workbench and never copied into the data root.
const EXTERNAL_STORAGE_KEYS = ["checkpoint", "lora", "clip", "vae"];

function isExternalStorageKey(key: string): boolean {
  return EXTERNAL_STORAGE_KEYS.includes(key);
}

function diskLabel(onDisk: boolean | null): string {
  if (onDisk === null) return "目录未配置";
  return onDisk ? "本机存在" : "本机未找到";
}

function formatRuleValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function formatSize(bytes?: number | null): string {
  if (bytes === null || bytes === undefined) return "";
  if (bytes >= 1024 * 1024 * 1024) {
    return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`;
  }
  if (bytes >= 1024 * 1024) {
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  }
  return `${bytes} B`;
}

function identityLabel(entry: CatalogEntry): string {
  const identity = entry.identity;
  if (!identity || !identity.known) return diskLabel(entry.on_disk);
  const size = formatSize(identity.size_bytes);
  const digest = identity.hashed ? "已校验" : "未哈希";
  return [size, digest].filter(Boolean).join(" · ");
}

function StatusDot({ online }: { online: boolean }) {
  return (
    <span
      className={`dot ${online ? "online" : "offline"}`}
      aria-label={online ? "在线" : "离线"}
    />
  );
}

type HashableCategory = "lora" | "clip" | "vae";

function HashButton({
  category,
  entry,
  busyKey,
  onHash,
}: {
  category: HashableCategory;
  entry: CatalogEntry;
  busyKey: string | null;
  onHash: (category: HashableCategory, entry: CatalogEntry) => void;
}) {
  const identity = entry.identity;
  if (!identity || !identity.known || identity.hashed) return null;
  const busy = busyKey === `${category}:${entry.name}`;
  return (
    <button
      type="button"
      className="hash-trigger"
      disabled={busy}
      onClick={() => onHash(category, entry)}
    >
      {busy ? "计算中…" : "计算哈希"}
    </button>
  );
}

export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [services, setServices] = useState<ServiceStatus | null>(null);
  const [comfyStatus, setComfyStatus] = useState<ComfyStatus | null>(null);
  const [comfyQueue, setComfyQueue] = useState<ComfyQueue | null>(null);
  const [catalog, setCatalog] = useState<ModelCatalog | null>(null);
  const [rules, setRules] = useState<RuleRecord[] | null>(null);
  const [profiles, setProfiles] = useState<ProfileSummary[] | null>(null);
  const [profileDir, setProfileDir] = useState<ProfileDirInfo | null>(null);
  const [openingDir, setOpeningDir] = useState(false);
  const [openProfile, setOpenProfile] = useState<string | null>(null);
  const [preview, setPreview] = useState<ProfilePreview | null>(null);
  const settingsFileRef = useRef<HTMLInputElement>(null);
  const backupFileRef = useRef<HTMLInputElement>(null);
  const [restoreFile, setRestoreFile] = useState<File | null>(null);
  const [restoreOverwrite, setRestoreOverwrite] = useState(false);
  const [importingProfile, setImportingProfile] = useState<string | null>(null);
  const [deletingRule, setDeletingRule] = useState<string | null>(null);
  const [editingLora, setEditingLora] = useState<string | null>(null);
  const [loraTriggersText, setLoraTriggersText] = useState("");
  const [loraDescription, setLoraDescription] = useState("");
  const [loraBaseModel, setLoraBaseModel] = useState("");
  const [loraTagsText, setLoraTagsText] = useState("");
  const [loraKeyword, setLoraKeyword] = useState("");
  const [loraActiveTag, setLoraActiveTag] = useState<string | null>(null);
  const [loraShowHidden, setLoraShowHidden] = useState(false);
  const [loraSamples, setLoraSamples] = useState<LoraSample[]>([]);
  const [assetOptions, setAssetOptions] = useState<
    { id: number; name: string }[]
  >([]);
  const [samplePickerAsset, setSamplePickerAsset] = useState("");
  const [sampleCaption, setSampleCaption] = useState("");
  const [loraSaveBusy, setLoraSaveBusy] = useState(false);
  const [hashingIdentity, setHashingIdentity] = useState<string | null>(null);
  const [openRuleHistory, setOpenRuleHistory] = useState<string | null>(null);
  const [ruleHistory, setRuleHistory] = useState<RuleHistoryItem[]>([]);
  const [restoringHistory, setRestoringHistory] = useState<number | null>(null);
  const [importReports, setImportReports] = useState<
    Record<string, ProfileImportReport>
  >({});
  const [selectedByProfile, setSelectedByProfile] = useState<
    Record<string, string[]>
  >({});
  const [dismissedByProfile, setDismissedByProfile] = useState<
    Record<string, string[]>
  >({});
  const [checks, setChecks] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(true);
  const [message, setMessage] = useState("正在连接本地服务…");
  const [activePage, setActivePage] = useState<PageKey>(readStoredPage);
  const [theme, setTheme] = useState<Theme>(readStoredTheme);
  const [fontSize, setFontSize] = useState<FontSize>(readStoredFont);

  useEffect(() => {
    const root = document.documentElement;
    root.dataset.theme = theme;
    root.dataset.font = fontSize;
    try {
      localStorage.setItem("workbench.theme", theme);
      localStorage.setItem("workbench.font", fontSize);
    } catch {
      // 忽略本地存储写入失败。
    }
  }, [theme, fontSize]);

  function toggleTheme() {
    setTheme((prev) => (prev === "dark" ? "light" : "dark"));
  }

  function switchPage(page: PageKey) {
    setActivePage(page);
    try {
      localStorage.setItem("workbench.activePage", page);
    } catch {
      // 忽略本地存储写入失败。
    }
  }

  function goToStep(step: string) {
    if (step === "09") {
      switchPage("history");
      return;
    }
    if (step === "07") {
      window.dispatchEvent(new Event("workbench:open-drafts"));
      return;
    }
    const el = document.getElementById(`step-${step}`);
    if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  useEffect(() => {
    function onNavigate(event: Event) {
      const detail = (
        event as CustomEvent<{ page: PageKey; promptId?: string }>
      ).detail;
      if (detail && PAGE_KEYS.includes(detail.page)) {
        if (detail.promptId) {
          try {
            localStorage.setItem("workbench.jumpHistory", detail.promptId);
          } catch {
            // 忽略本地存储写入失败。
          }
        }
        switchPage(detail.page);
      }
    }
    window.addEventListener("workbench:navigate", onNavigate);
    return () => window.removeEventListener("workbench:navigate", onNavigate);
  }, []);

  async function refresh() {
    const [healthResult, settingsResult, serviceResult, catalogResult, ruleResult, profileResult] =
      await Promise.all([
        apiGet<Health>("/api/health"),
        apiGet<Settings>("/api/settings"),
        apiGet<ServiceStatus>("/api/services/status"),
        apiGet<ModelCatalog>("/api/models/catalog"),
        apiGet<{ items: RuleRecord[] }>("/api/rules"),
        apiGet<{ items: ProfileSummary[] }>("/api/profiles"),
      ]);

    setHealth(healthResult);
    setSettings(settingsResult);
    setServices(serviceResult);
    setCatalog(catalogResult);
    setRules(ruleResult.items);
    setProfiles(profileResult.items);

    // 档案目录路径为只读辅助信息；独立容错，取不到不影响主面板。
    apiGet<ProfileDirInfo>("/api/profiles/dir")
      .then(setProfileDir)
      .catch(() => setProfileDir(null));

    // 显存与队列为只读摘要；独立容错，取不到置空，不影响主面板连接状态。
    apiGet<ComfyStatus>("/api/comfyui/status")
      .then(setComfyStatus)
      .catch(() => setComfyStatus(null));
    apiGet<ComfyQueue>("/api/comfyui/queue")
      .then(setComfyQueue)
      .catch(() => setComfyQueue(null));
  }

  useEffect(() => {
    let alive = true;

    async function connect() {
      try {
        await refresh();

        if (alive) {
          setMessage("本地服务已连接");
        }
      } catch (error) {
        if (alive) {
          setMessage(`连接失败：${String(error)}`);
        }
      } finally {
        if (alive) {
          setBusy(false);
        }
      }
    }

    void connect();

    return () => {
      alive = false;
    };
  }, []);

  async function refreshStatus() {
    setBusy(true);

    try {
      await refresh();
      setMessage("状态已刷新");
    } catch (error) {
      setMessage(`刷新失败：${String(error)}`);
    } finally {
      setBusy(false);
    }
  }

  async function checkPath(key: string) {
    if (!settings) return;

    const path = settings.storage[key]?.trim() ?? "";

    if (!path) {
      setChecks((old) => ({
        ...old,
        [key]: "路径为空",
      }));
      return;
    }

    setChecks((old) => ({
      ...old,
      [key]: "正在检查…",
    }));

    try {
      const result = await apiPost<PathCheck>(
        "/api/storage/validate",
        {
          path,
          create: !isExternalStorageKey(key),
        },
      );

      let status: string;

      if (
        result.exists &&
        result.is_directory &&
        result.writable
      ) {
        status = "目录存在且可写";
      } else {
        status =
          `存在：${result.exists}；` +
          `目录：${result.is_directory}；` +
          `可写：${result.writable}`;
      }

      setChecks((old) => ({
        ...old,
        [key]: status,
      }));
    } catch (error) {
      setChecks((old) => ({
        ...old,
        [key]: `检查失败：${String(error)}`,
      }));
    }
  }

  async function toggleProfile(file: string) {
    if (openProfile === file) {
      setOpenProfile(null);
      setPreview(null);
      return;
    }
    setOpenProfile(file);
    try {
      const detail = await apiGet<ProfilePreview>(
        `/api/profiles/${encodeURIComponent(file)}`,
      );
      setPreview(detail);
    } catch (error) {
      setPreview(null);
      setMessage(`档案预览失败：${String(error)}`);
    }
  }

  async function importProfile(file: string, selectedKeys?: string[]) {
    setImportingProfile(file);
    try {
      const body: Record<string, unknown> = { file_name: file };
      if (selectedKeys && selectedKeys.length > 0) {
        body.candidate_keys = selectedKeys;
      }
      const report = await apiPost<ProfileImportReport>(
        "/api/profiles/import",
        body,
      );
      setImportReports((old) => ({ ...old, [file]: report }));
      setSelectedByProfile((old) => ({ ...old, [file]: [] }));
      // Refresh rules and the profile summary after the write; the backend
      // validates each candidate and rejects invalid ones in the report.
      const [ruleResult, profileResult] = await Promise.all([
        apiGet<{ items: RuleRecord[] }>("/api/rules"),
        apiGet<{ items: ProfileSummary[] }>("/api/profiles"),
      ]);
      setRules(ruleResult.items);
      setProfiles(profileResult.items);
      const unknown = report.unknown_keys?.length
        ? `，${report.unknown_keys.length} 条未匹配被跳过`
        : "";
      setMessage(
        report.rejected.length > 0
          ? `档案已导入：${report.imported} 条入库，${report.rejected.length} 条被拒绝${unknown}。`
          : `档案已导入：${report.imported} 条规则入库${unknown}。`,
      );
    } catch (error) {
      setMessage(`档案导入失败：${String(error)}`);
    } finally {
      setImportingProfile(null);
    }
  }

  async function openProfileDir() {
    setOpeningDir(true);
    try {
      await apiPost<{ path: string }>("/api/profiles/open-dir", {});
      setMessage("已在文件管理器中打开档案目录。");
    } catch (error) {
      setMessage(`打开档案目录失败：${String(error)}`);
    } finally {
      setOpeningDir(false);
    }
  }

  function toggleCandidateSelected(file: string, key: string) {
    setSelectedByProfile((old) => {
      const current = old[file] || [];
      const next = current.includes(key)
        ? current.filter((item) => item !== key)
        : [...current, key];
      return { ...old, [file]: next };
    });
  }

  function dismissSelectedCandidates(file: string) {
    const picked = selectedByProfile[file] || [];
    if (picked.length === 0) return;
    setDismissedByProfile((old) => ({
      ...old,
      [file]: [...new Set([...(old[file] || []), ...picked])],
    }));
    setSelectedByProfile((old) => ({ ...old, [file]: [] }));
    setMessage(`已驳回 ${picked.length} 条候选（仅本次预览忽略，未写入规则库）。`);
  }

  function ruleIdentity(rule: RuleRecord): string {
    return `${rule.level}::${rule.scope_key ?? ""}::${rule.rule_key}`;
  }

  async function copyLoraTriggers(meta: LoraMeta) {
    if (meta.trigger_words.length === 0) return;
    try {
      await navigator.clipboard.writeText(meta.trigger_words.join(", "));
      setMessage(`已复制「${meta.name}」触发词（未写入工作副本）。`);
    } catch (error) {
      setMessage(`复制触发词失败：${String(error)}`);
    }
  }

  const allLoraTags = Array.from(
    new Set(
      (catalog?.loras ?? []).flatMap((entry) => entry.meta?.tags ?? []),
    ),
  ).sort((a, b) => a.localeCompare(b, "zh"));

  const hiddenLoraCount = (catalog?.loras ?? []).filter(
    (entry) => entry.meta?.hidden,
  ).length;

  const loraKeywordNorm = loraKeyword.trim().toLowerCase();
  const visibleLoras = (catalog?.loras ?? []).filter((entry) => {
    const meta = entry.meta;
    if (!loraShowHidden && meta?.hidden) return false;
    if (loraActiveTag && !(meta?.tags ?? []).includes(loraActiveTag)) {
      return false;
    }
    if (loraKeywordNorm) {
      const haystack = [
        entry.name,
        meta?.base_model ?? "",
        meta?.description ?? "",
        ...(meta?.tags ?? []),
        ...(meta?.trigger_words ?? []),
      ]
        .join(" ")
        .toLowerCase();
      if (!haystack.includes(loraKeywordNorm)) return false;
    }
    return true;
  });

  const renderLoraEntry = (entry: CatalogEntry) => {
    const meta = entry.meta ?? null;
    const detail = [
      meta?.base_model ? `底座：${meta.base_model}` : "",
      meta && meta.trigger_words.length > 0
        ? `触发：${meta.trigger_words.slice(0, 4).join("、")}`
        : "",
      meta?.description
        ? `说明：${
            meta.description.length > 60
              ? `${meta.description.slice(0, 60)}…`
              : meta.description
          }`
        : "",
    ]
      .filter(Boolean)
      .join("　");
    const source: "manual" | "sidecar" | "none" | null = meta
      ? meta.source ?? (meta.manual_present ? "manual" : "sidecar")
      : null;
    const combos = meta?.combos ?? [];
    return (
      <li
        key={entry.name}
        className={meta ? `lora-meta lora-meta-${meta.status}` : "lora-meta"}
      >
        <span>
          {entry.name}
          {meta && (
            <small className="lora-meta-status">
              {LORA_META_LABEL[meta.status]}
            </small>
          )}
          {source && (
            <small className="lora-meta-source">
              {LORA_SOURCE_LABEL[source]}
            </small>
          )}
          {meta?.hidden && (
            <small className="lora-meta-source">已隐藏</small>
          )}
        </span>
        <small>{identityLabel(entry)}</small>
        <HashButton
          category="lora"
          entry={entry}
          busyKey={hashingIdentity}
          onHash={(cat, item) => void hashModelIdentity(cat, item)}
        />
        {detail && <small className="lora-meta-detail">{detail}</small>}
        {meta && meta.trigger_words.length > 0 && (
          <div className="lora-triggers">
            {meta.trigger_words.map((word) => (
              <span key={word} className="lora-trigger-chip">
                {word}
              </span>
            ))}
            <button
              type="button"
              onClick={() => void copyLoraTriggers(meta)}
            >
              复制触发词
            </button>
          </div>
        )}
        {meta && (meta.tags ?? []).length > 0 && (
          <div className="lora-tags">
            {(meta.tags ?? []).map((tag) => (
              <span key={tag} className="lora-tag-chip">
                {tag}
              </span>
            ))}
          </div>
        )}
        {meta && (meta.samples ?? []).length > 0 && (
          <div className="lora-samples">
            {(meta.samples ?? []).map((sample) => (
              <figure key={sample.asset_id} className="lora-sample">
                <img
                  src={`/api/assets/image?asset_id=${sample.asset_id}`}
                  alt="效果图"
                  loading="lazy"
                />
                {sample.caption && (
                  <figcaption>{sample.caption}</figcaption>
                )}
              </figure>
            ))}
          </div>
        )}
        {combos.length > 0 && (
          <ul className="lora-combos">
            {combos.map((combo, index) => (
              <li key={index}>
                {combo.name && <strong>{combo.name}：</strong>}
                {combo.trigger_words.join("、")}
              </li>
            ))}
          </ul>
        )}
        {(entry.related ?? []).length > 0 && (
          <small className="lora-related">
            疑似同一身份：{(entry.related ?? []).join("、")}
          </small>
        )}
        {(entry.related_triggers ?? []).length > 0 && (
          <small className="lora-related">
            共享触发词：{(entry.related_triggers ?? []).join("、")}
          </small>
        )}
        {(entry.compatible ?? []).length > 0 && (
          <small className="lora-related">
            同底座可搭配：{(entry.compatible ?? []).join("、")}
          </small>
        )}
        {editingLora === entry.name ? (
          <div className="lora-edit">
            <label>
              触发词（逗号或换行分隔）
              <textarea
                rows={2}
                value={loraTriggersText}
                onChange={(event) =>
                  setLoraTriggersText(event.target.value)
                }
              />
            </label>
            <label>
              底座模型
              <input
                value={loraBaseModel}
                onChange={(event) =>
                  setLoraBaseModel(event.target.value)
                }
              />
            </label>
            <label>
              标签（逗号分隔，便于筛选）
              <input
                value={loraTagsText}
                onChange={(event) => setLoraTagsText(event.target.value)}
                placeholder="例如：角色、风格、服装、场景"
              />
            </label>
            <label>
              说明
              <textarea
                rows={2}
                value={loraDescription}
                onChange={(event) =>
                  setLoraDescription(event.target.value)
                }
              />
            </label>
            <fieldset className="lora-samples-edit">
              <legend>效果图（来自素材库）</legend>
              <div className="lora-sample-picker">
                <select
                  value={samplePickerAsset}
                  onChange={(event) =>
                    setSamplePickerAsset(event.target.value)
                  }
                >
                  <option value="">选择素材…</option>
                  {assetOptions.map((asset) => (
                    <option key={asset.id} value={asset.id}>
                      {asset.name}
                    </option>
                  ))}
                </select>
                <input
                  value={sampleCaption}
                  placeholder="训练任务 / 说明"
                  onChange={(event) =>
                    setSampleCaption(event.target.value)
                  }
                />
                <button
                  type="button"
                  className="secondary"
                  onClick={addLoraSample}
                  disabled={!samplePickerAsset}
                >
                  添加
                </button>
              </div>
              {loraSamples.length > 0 && (
                <ul className="lora-sample-edit-list">
                  {loraSamples.map((sample) => {
                    const asset = assetOptions.find(
                      (item) => item.id === sample.asset_id,
                    );
                    return (
                      <li key={sample.asset_id}>
                        <img
                          src={`/api/assets/image?asset_id=${sample.asset_id}`}
                          alt={asset?.name ?? String(sample.asset_id)}
                        />
                        <input
                          value={sample.caption}
                          placeholder="训练任务 / 说明"
                          onChange={(event) =>
                            setLoraSamples((prev) =>
                              prev.map((item) =>
                                item.asset_id === sample.asset_id
                                  ? {
                                      ...item,
                                      caption: event.target.value,
                                    }
                                  : item,
                              ),
                            )
                          }
                        />
                        <button
                          type="button"
                          className="secondary"
                          onClick={() => removeLoraSample(sample.asset_id)}
                        >
                          移除
                        </button>
                      </li>
                    );
                  })}
                </ul>
              )}
            </fieldset>
            <div className="actions">
              <button
                onClick={() => void saveLoraManual(entry)}
                disabled={loraSaveBusy}
              >
                保存资料
              </button>
              <button
                className="secondary"
                onClick={cancelLoraEdit}
                disabled={loraSaveBusy}
              >
                取消
              </button>
            </div>
          </div>
        ) : (
          <>
            <button
              className="secondary"
              onClick={() => startLoraEdit(entry)}
            >
              编辑资料
            </button>
            <button
              className="secondary"
              disabled={loraSaveBusy}
              onClick={() => void toggleLoraHidden(entry)}
            >
              {meta?.hidden ? "恢复显示" : "从清单隐藏"}
            </button>
          </>
        )}
      </li>
    );
  };

  async function ensureAssetOptions() {
    if (assetOptions.length > 0) return;
    try {
      const body = await apiGet<{
        items: { id: number; name: string; exists: boolean }[];
      }>("/api/assets/items");
      setAssetOptions(
        (body.items || [])
          .filter((item) => item.exists)
          .map(({ id, name }) => ({ id, name })),
      );
    } catch {
      setAssetOptions([]);
    }
  }

  function startLoraEdit(entry: CatalogEntry) {
    const meta = entry.meta ?? null;
    setEditingLora(entry.name);
    setLoraTriggersText((meta?.trigger_words ?? []).join("，"));
    setLoraDescription(meta?.description ?? "");
    setLoraBaseModel(meta?.base_model ?? "");
    setLoraTagsText((meta?.tags ?? []).join(", "));
    setLoraSamples(meta?.samples ?? []);
    setSamplePickerAsset("");
    setSampleCaption("");
    void ensureAssetOptions();
  }

  function addLoraSample() {
    const assetId = Number(samplePickerAsset);
    if (!Number.isInteger(assetId) || assetId <= 0) return;
    if (loraSamples.some((sample) => sample.asset_id === assetId)) return;
    setLoraSamples((prev) => [
      ...prev,
      { asset_id: assetId, caption: sampleCaption.trim() },
    ]);
    setSamplePickerAsset("");
    setSampleCaption("");
  }

  function removeLoraSample(assetId: number) {
    setLoraSamples((prev) =>
      prev.filter((sample) => sample.asset_id !== assetId),
    );
  }

  function cancelLoraEdit() {
    setEditingLora(null);
  }

  async function saveLoraManual(entry: CatalogEntry) {
    setLoraSaveBusy(true);
    try {
      const trigger_words = loraTriggersText
        .split(/[\n,，]/)
        .map((term) => term.trim())
        .filter(Boolean);
      const tags = loraTagsText
        .split(/[\n,，]/)
        .map((term) => term.trim())
        .filter(Boolean);
      await apiPost(
        `/api/models/loras/${encodeURIComponent(entry.name)}/manual`,
        {
          trigger_words,
          description: loraDescription.trim(),
          base_model: loraBaseModel.trim(),
          combos: entry.meta?.combos ?? [],
          tags,
          samples: loraSamples,
          hidden: entry.meta?.hidden ?? false,
        },
      );
      setEditingLora(null);
      setMessage(`已保存「${entry.name}」手动资料（仅写入本地知识目录）。`);
      await refresh();
    } catch (error) {
      setMessage(`保存手动资料失败：${String(error)}`);
    } finally {
      setLoraSaveBusy(false);
    }
  }

  async function toggleLoraHidden(entry: CatalogEntry) {
    const meta = entry.meta;
    const next = !(meta?.hidden ?? false);
    setLoraSaveBusy(true);
    try {
      await apiPost(
        `/api/models/loras/${encodeURIComponent(entry.name)}/manual`,
        {
          trigger_words: meta?.trigger_words ?? [],
          description: meta?.description ?? "",
          base_model: meta?.base_model ?? "",
          combos: meta?.combos ?? [],
          tags: meta?.tags ?? [],
          samples: meta?.samples ?? [],
          hidden: next,
        },
      );
      setMessage(
        next
          ? `已在清单中隐藏「${entry.name}」（文件与节点使用不受影响）。`
          : `已恢复显示「${entry.name}」。`,
      );
      await refresh();
    } catch (error) {
      setMessage(`更新隐藏状态失败：${String(error)}`);
    } finally {
      setLoraSaveBusy(false);
    }
  }

  async function hashModelIdentity(category: HashableCategory, entry: CatalogEntry) {
    const key = `${category}:${entry.name}`;
    setHashingIdentity(key);
    try {
      const result = await apiPost<{ identity: IdentitySummary }>(
        "/api/models/identity/hash",
        { category, name: entry.name },
      );
      setCatalog((prev) => {
        if (!prev) return prev;
        const listKey =
          category === "lora" ? "loras" : category === "clip" ? "clips" : "vaes";
        const list = prev[listKey].map((item) =>
          item.name === entry.name ? { ...item, identity: result.identity } : item,
        );
        return { ...prev, [listKey]: list };
      });
      setMessage(`已计算并缓存「${entry.name}」哈希。`);
    } catch (error) {
      setMessage(`计算哈希失败：${String(error)}`);
    } finally {
      setHashingIdentity(null);
    }
  }

  async function removeRule(rule: RuleRecord) {
    const identity = ruleIdentity(rule);
    if (deletingRule !== identity) {
      // 两步确认第一步：进入确认态，不直接删除；不用原生弹窗。
      setDeletingRule(identity);
      return;
    }
    try {
      await apiDelete<{ removed: boolean }>("/api/rules", {
        level: rule.level,
        scope_key: rule.scope_key,
        rule_key: rule.rule_key,
      });
      const ruleResult = await apiGet<{ items: RuleRecord[] }>("/api/rules");
      setRules(ruleResult.items);
      setMessage("规则已删除。");
    } catch (error) {
      setMessage(`规则删除失败：${String(error)}`);
    } finally {
      setDeletingRule(null);
    }
  }

  async function loadRuleHistory(rule: RuleRecord) {
    const query = new URLSearchParams({
      level: rule.level,
      scope_key: rule.scope_key ?? "",
      rule_key: rule.rule_key,
    });
    const result = await apiGet<{ items: RuleHistoryItem[] }>(
      `/api/rules/history?${query.toString()}`,
    );
    setRuleHistory(result.items);
  }

  async function toggleRuleHistory(rule: RuleRecord) {
    const identity = ruleIdentity(rule);
    if (openRuleHistory === identity) {
      setOpenRuleHistory(null);
      setRuleHistory([]);
      return;
    }
    setOpenRuleHistory(identity);
    setRestoringHistory(null);
    try {
      await loadRuleHistory(rule);
    } catch (error) {
      setRuleHistory([]);
      setMessage(`读取规则历史失败：${String(error)}`);
    }
  }

  async function restoreRuleHistory(rule: RuleRecord, item: RuleHistoryItem) {
    if (restoringHistory !== item.history_id) {
      // 两步确认第一步：进入确认态，不直接回退；不用原生弹窗。
      setRestoringHistory(item.history_id);
      return;
    }
    try {
      await apiPost<RuleRecord>(
        `/api/rules/history/${item.history_id}/restore`,
        {},
      );
      const ruleResult = await apiGet<{ items: RuleRecord[] }>("/api/rules");
      setRules(ruleResult.items);
      await loadRuleHistory(rule);
      setMessage(`已回退到 v${item.version}。`);
    } catch (error) {
      setMessage(`规则回退失败：${String(error)}`);
    } finally {
      setRestoringHistory(null);
    }
  }

  async function saveSettings() {
    if (!settings) return;

    setBusy(true);

    try {
      const { root: _fixedRoot, ...editableStorage } =
        settings.storage;

      const updated = await apiPost<Settings>(
        "/api/settings",
        {
          storage: editableStorage,
          connections: settings.connections,
          prompt: {
            protected_terms: settings.prompt?.protected_terms ?? [],
          },
        },
      );

      setSettings(updated);
      setMessage("设置已保存");
    } catch (error) {
      setMessage(`保存失败：${String(error)}`);
    } finally {
      setBusy(false);
    }
  }

  async function exportDiagnostics() {
    setBusy(true);
    try {
      const report = await apiGet<Record<string, unknown>>("/api/diagnostics");
      const blob = new Blob([JSON.stringify(report, null, 2)], {
        type: "application/json;charset=utf-8",
      });
      const url = URL.createObjectURL(blob);
      const stamp = new Date().toISOString().replace(/[-:T]/g, "").slice(0, 12);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `workbench-diagnostics-${stamp}.json`;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);
      setMessage("诊断报告已导出（仅本地，未上传）。");
    } catch (error) {
      setMessage(`导出诊断报告失败：${String(error)}`);
    } finally {
      setBusy(false);
    }
  }

  function exportSettings() {
    if (!settings) return;
    // 只导出白名单配置（本地路径与回环连接地址）；不含模型权重、原图、历史记录或遗留杂项。
    const payload = {
      version: 2,
      storage: settings.storage,
      connections: settings.connections,
      prompt: {
        protected_terms: settings.prompt?.protected_terms ?? [],
      },
    };
    const blob = new Blob([JSON.stringify(payload, null, 2)], {
      type: "application/json;charset=utf-8",
    });
    const url = URL.createObjectURL(blob);
    const stamp = new Date().toISOString().replace(/[-:T]/g, "").slice(0, 12);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `workbench-settings-${stamp}.json`;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(url);
    setMessage("设置已导出（仅本地，未上传；不含模型权重与原图）。");
  }

  async function exportBackup() {
    setBusy(true);
    try {
      const blob = await apiGetBlob("/api/backup/export");
      const url = URL.createObjectURL(blob);
      const stamp = new Date().toISOString().replace(/[-:T]/g, "").slice(0, 12);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `workbench-backup-${stamp}.zip`;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);
      setMessage("整库备份已导出（仅本地 ZIP；不含模型权重与原图）。");
    } catch (error) {
      setMessage(`导出整库备份失败：${String(error)}`);
    } finally {
      setBusy(false);
    }
  }

  function pickBackupFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    setRestoreOverwrite(false);
    setRestoreFile(file);
  }

  async function confirmRestore() {
    if (!restoreFile) return;
    setBusy(true);
    try {
      const buffer = await restoreFile.arrayBuffer();
      const query = restoreOverwrite ? "?overwrite=true" : "";
      const report = await apiPostBinary(`/api/backup/restore${query}`, buffer);
      const restored = Number(report.restored_count ?? 0);
      const skipped = Number(
        (report.skipped_existing as unknown[] | undefined)?.length ?? 0,
      );
      const overwritten = Number(report.overwritten_count ?? 0);
      setMessage(
        `恢复完成：写入 ${restored} 项，保留现有 ${skipped} 项，覆盖 ${overwritten} 项；` +
          `恢复前备份：${String(report.pre_backup ?? "")}。建议刷新页面。`,
      );
      setRestoreFile(null);
    } catch (error) {
      setMessage(`恢复失败：${String(error)}`);
    } finally {
      setBusy(false);
    }
  }

  function cancelRestore() {
    setRestoreFile(null);
    setRestoreOverwrite(false);
  }

  function importSettingsFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file || !settings) return;

    const reader = new FileReader();
    reader.onload = () => {
      try {
        const parsed = JSON.parse(String(reader.result)) as Record<string, unknown>;
        if (
          typeof parsed !== "object" ||
          parsed === null ||
          Array.isArray(parsed)
        ) {
          throw new Error("设置文件根节点必须是对象");
        }

        const incomingConnections =
          (parsed.connections as Record<string, unknown>) ?? {};
        const incomingStorage =
          (parsed.storage as Record<string, unknown>) ?? {};

        const nextConnections = { ...settings.connections };
        let applied = 0;

        for (const key of ["ollama_url", "comfyui_url"] as const) {
          const value = incomingConnections[key];
          if (typeof value === "string" && /^https?:\/\//.test(value.trim())) {
            nextConnections[key] = value.trim().replace(/\/+$/, "");
            applied += 1;
          }
        }

        const nextStorage = { ...settings.storage };
        for (const key of EXTERNAL_STORAGE_KEYS) {
          const value = incomingStorage[key];
          if (typeof value === "string") {
            nextStorage[key] = value.trim();
            applied += 1;
          }
        }

        // Protected terms: whitelist a string array, strip/dedupe/limit the
        // same way the backend does; anything else is ignored conservatively.
        const incomingPrompt =
          (parsed.prompt as Record<string, unknown>) ?? {};
        const incomingTerms = incomingPrompt.protected_terms;
        let nextTerms: string[] = settings.prompt?.protected_terms ?? [];
        if (Array.isArray(incomingTerms)) {
          const normalized = incomingTerms
            .filter((term): term is string => typeof term === "string")
            .map((term) => term.trim())
            .filter(Boolean)
            .map((term) => term.slice(0, 60));
          nextTerms = Array.from(new Set(normalized)).slice(0, 50);
          applied += 1;
        }

        // Managed storage paths and root are rebuilt against the current data
        // root; values from the imported file are not trusted for those keys.
        setSettings({
          ...settings,
          storage: nextStorage,
          connections: nextConnections,
          prompt: { protected_terms: nextTerms },
        });
        setMessage(
          applied > 0
            ? `已从文件载入 ${applied} 项配置；请检查后点「保存设置」落库。`
            : "文件中没有可采信的连接地址或外部模型目录。",
        );
      } catch (error) {
        setMessage(`设置导入失败：${String(error)}`);
      }
    };
    reader.onerror = () => setMessage("设置导入失败：无法读取文件。");
    reader.readAsText(file);
  }

  function updateStorage(key: string, value: string) {
    if (!settings || key === "root") return;

    setSettings({
      ...settings,
      storage: {
        ...settings.storage,
        [key]: value,
      },
    });

    setChecks((old) => ({
      ...old,
      [key]: "路径已修改，请重新检查",
    }));
  }

  function updateConnection(
    key: "ollama_url" | "comfyui_url",
    value: string,
  ) {
    if (!settings) return;

    setSettings({
      ...settings,
      connections: {
        ...settings.connections,
        [key]: value,
      },
    });
  }

  function updateProtectedTerms(raw: string) {
    if (!settings) return;
    const terms = raw
      .split(/[\n,，]/)
      .map((term) => term.trim())
      .filter(Boolean);
    setSettings({
      ...settings,
      prompt: { protected_terms: terms },
    });
  }

  const pageTitle =
    activePage === "workflows"
      ? "工作流工作区"
      : activePage === "history"
        ? "生成历史"
        : activePage === "assets"
          ? "素材库"
          : activePage === "loras"
            ? "LoRA 资料"
            : "服务与设置";
  const primaryDevice = comfyStatus?.devices?.[0] ?? null;

  return (
    <div className="w-app">
      <aside className="w-sidebar">
        <div>
          <div className="w-brand-title">ComfyUI Prompt Workbench</div>
          <div className="w-brand-sub">本地 AI 绘图提示词工作台</div>
        </div>

        <nav className="w-nav-group">
          <div className="w-nav-label">主导航</div>
          <div
            className={`w-nav-item ${activePage === "workflows" ? "active" : ""}`}
            onClick={() => switchPage("workflows")}
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none"><path d="M4 6h16M4 12h16M4 18h10" stroke="currentColor" stroke-width="2" stroke-linecap="round" /></svg>
            工作流工作区
          </div>
          {activePage === "workflows" && (
            <div className="w-steps">
              <div className="w-step done" onClick={() => goToStep("01")}><span className="num">01</span>选择工作流</div>
              <div className="w-step done" onClick={() => goToStep("02")}><span className="num">02</span>识别结果</div>
              <div className="w-step done" onClick={() => goToStep("03")}><span className="num">03</span>规则比对</div>
              <div className="w-step done" onClick={() => goToStep("04")}><span className="num">04</span>Prompt 确认</div>
              <div className="w-step active" onClick={() => goToStep("05")}><span className="num">05</span>提示词候选</div>
              <div className="w-step" onClick={() => goToStep("06")}><span className="num">06</span>编辑工作副本</div>
              <div className="w-step" onClick={() => goToStep("07")}><span className="num">07</span>草稿与版本</div>
              <div className="w-step" onClick={() => goToStep("08")}><span className="num">08</span>显存检查与提交</div>
              <div className="w-step" onClick={() => goToStep("09")}><span className="num">09</span>生成历史</div>
            </div>
          )}
          <div
            className={`w-nav-item ${activePage === "history" ? "active" : ""}`}
            onClick={() => switchPage("history")}
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none"><rect x="3" y="3" width="18" height="18" rx="3" stroke="currentColor" stroke-width="2" /><circle cx="9" cy="9" r="2" /><path d="m21 15-4.5-4.5L6 21" stroke="currentColor" stroke-width="2" /></svg>
            生成历史
          </div>
          <div
            className={`w-nav-item ${activePage === "assets" ? "active" : ""}`}
            onClick={() => switchPage("assets")}
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none"><rect x="3" y="3" width="18" height="18" rx="3" stroke="currentColor" stroke-width="2" /><circle cx="8.5" cy="8.5" r="1.6" /><path d="m21 15-4.5-4.5L6 21" stroke="currentColor" stroke-width="2" stroke-linejoin="round" /></svg>
            素材库
          </div>
          <div
            className={`w-nav-item ${activePage === "loras" ? "active" : ""}`}
            onClick={() => switchPage("loras")}
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none"><path d="M12 2.5 3.5 7v10L12 21.5 20.5 17V7L12 2.5Z" stroke="currentColor" stroke-width="2" stroke-linejoin="round" /><path d="M12 12 3.5 7M12 12l8.5-5M12 12v9.5" stroke="currentColor" stroke-width="2" stroke-linejoin="round" /></svg>
            LoRA 资料
          </div>
          <div
            className={`w-nav-item ${activePage === "settings" ? "active" : ""}`}
            onClick={() => switchPage("settings")}
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none"><circle cx="12" cy="12" r="3" stroke="currentColor" stroke-width="2" /><path d="M19 12a7 7 0 0 0-.1-1.2l2-1.6-2-3.4-2.4 1a7 7 0 0 0-2-1.2L14 3h-4l-.5 2.6a7 7 0 0 0-2 1.2l-2.4-1-2 3.4 2 1.6A7 7 0 0 0 5 12c0 .4 0 .8.1 1.2l-2 1.6 2 3.4 2.4-1a7 7 0 0 0 2 1.2L10 21h4l.5-2.6a7 7 0 0 0 2-1.2l2.4 1 2-3.4-2-1.6c.1-.4.1-.8.1-1.2Z" stroke="currentColor" stroke-width="2" /></svg>
            服务与设置
          </div>
        </nav>

        <div className="w-side-foot">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none"><path d="M12 3 4 7v6c0 4 3.4 6.5 8 8 4.6-1.5 8-4 8-8V7l-8-4Z" stroke="currentColor" stroke-width="2" stroke-linejoin="round" /></svg>
          数据全部保存在本机
        </div>
      </aside>

      <div className="w-main">
        <header className="w-topbar">
          <div className="w-brand-title" style={{ fontSize: "15px" }}>{pageTitle}</div>
          <div className="w-statuses">
            <span className={`w-status ${services?.ollama.online ? "online" : "offline"}`}>
              <span className="w-dot" />Ollama {services?.ollama.online ? "在线" : "离线"}
            </span>
            <span className={`w-status ${services?.comfyui.online ? "online" : "offline"}`}>
              <span className="w-dot" />ComfyUI {services?.comfyui.online ? "在线" : "离线"}{comfyStatus?.version ? ` · ${comfyStatus.version}` : ""}
            </span>
          </div>
          <div className="w-vram">
            显存
            <span className="w-vram-bar">
              <i style={{ width: primaryDevice && primaryDevice.vram_total ? `${Math.round(100 * (1 - (primaryDevice.vram_free ?? 0) / primaryDevice.vram_total))}%` : "0%" }} />
            </span>
            <span>{primaryDevice ? `${formatGiB(primaryDevice.vram_free)} 空闲` : "显存未知"}</span>
          </div>
          <div className="w-queue-chip">队列 {(comfyQueue?.running.length ?? 0) + (comfyQueue?.pending.length ?? 0)}</div>
          <div className="w-head-actions">
            <select
              className="w-select"
              style={{ width: "auto" }}
              value={theme}
              onChange={(event) => setTheme(event.target.value as Theme)}
              title="深浅主题"
            >
              <option value="light">浅色</option>
              <option value="dark">深色</option>
            </select>
            <select
              className="w-select"
              style={{ width: "auto" }}
              value={fontSize}
              onChange={(event) => setFontSize(event.target.value as FontSize)}
              title="界面字号"
            >
              <option value="small">小字号</option>
              <option value="normal">标准字号</option>
              <option value="large">大字号</option>
            </select>
            <button className="w-btn w-btn-ghost" onClick={refreshStatus} disabled={busy} title="刷新状态">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none"><path d="M21 12a9 9 0 1 1-2.64-6.36M21 3v6h-6" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" /></svg>
            </button>
          </div>
        </header>

        <div className="w-content">
      {activePage === "workflows" ? (
        <WorkflowWorkbench
          comfyuiUrl={settings?.connections.comfyui_url ?? ""}
          protectedTerms={settings?.prompt?.protected_terms ?? []}
        />
      ) : activePage === "history" ? (
        <HistoryPanel />
      ) : activePage === "assets" ? (
        <AssetLibrary />
      ) : activePage === "loras" ? (
        <section className="panel" id="page-loras">
          <div className="section-heading">
            <div>
              <h2>LoRA 资料</h2>
              <p>
                只读扫描本机 LoRA 的旁车元数据与手动资料；可补充触发词、底座与说明。
                这里不复制权重、不向 ComfyUI 目录写角色卡，身份归并仅作只读提示。
              </p>
            </div>
          </div>

          {catalog ? (
            catalog.online ? (
              <>
                <div className="lora-filter">
                  <input
                    type="text"
                    className="lora-filter-input"
                    placeholder="搜索名称、标签、触发词、底座或说明…"
                    value={loraKeyword}
                    onChange={(event) => setLoraKeyword(event.target.value)}
                  />
                  {allLoraTags.length > 0 && (
                    <div className="lora-tag-filters">
                      <button
                        type="button"
                        className={`lora-tag-chip${
                          loraActiveTag === null ? " active" : ""
                        }`}
                        onClick={() => setLoraActiveTag(null)}
                      >
                        全部
                      </button>
                      {allLoraTags.map((tag) => (
                        <button
                          key={tag}
                          type="button"
                          className={`lora-tag-chip${
                            loraActiveTag === tag ? " active" : ""
                          }`}
                          onClick={() =>
                            setLoraActiveTag(loraActiveTag === tag ? null : tag)
                          }
                        >
                          {tag}
                        </button>
                      ))}
                    </div>
                  )}
                  {hiddenLoraCount > 0 && (
                    <button
                      type="button"
                      className={`lora-tag-chip${
                        loraShowHidden ? " active" : ""
                      }`}
                      onClick={() => setLoraShowHidden((v) => !v)}
                    >
                      显示已隐藏（{hiddenLoraCount}）
                    </button>
                  )}
                </div>
                <ul className="catalog-list lora-list">
                  {visibleLoras.map((entry) => renderLoraEntry(entry))}
                  {visibleLoras.length === 0 && (
                    <li className="catalog-empty">
                      {catalog.loras.length === 0
                        ? "ComfyUI 未列出 LoRA"
                        : "没有匹配筛选条件的 LoRA"}
                    </li>
                  )}
                </ul>
              </>
            ) : (
              <p className="catalog-offline">
                LoRA 资料不可用：{catalog.error ?? "ComfyUI 未连接"}
              </p>
            )
          ) : (
            <p>正在读取 LoRA 资料…</p>
          )}
        </section>
      ) : (
        <div className="w-settings">
          <nav className="w-settings-nav" aria-label="设置分类">
            <button
              type="button"
              onClick={() =>
                document
                  .getElementById("settings-svc")
                  ?.scrollIntoView({ behavior: "smooth", block: "start" })
              }
            >
              服务状态
            </button>
            <button
              type="button"
              onClick={() =>
                document
                  .getElementById("settings-storage")
                  ?.scrollIntoView({ behavior: "smooth", block: "start" })
              }
            >
              存储位置
            </button>
            <button
              type="button"
              onClick={() =>
                document
                  .getElementById("settings-catalog")
                  ?.scrollIntoView({ behavior: "smooth", block: "start" })
              }
            >
              模型清单
            </button>
            <button
              type="button"
              onClick={() =>
                document
                  .getElementById("settings-rules")
                  ?.scrollIntoView({ behavior: "smooth", block: "start" })
              }
            >
              规则库
            </button>
            <button
              type="button"
              onClick={() =>
                document
                  .getElementById("settings-profiles")
                  ?.scrollIntoView({ behavior: "smooth", block: "start" })
              }
            >
              旧档案候选
            </button>
            <button
              type="button"
              onClick={() =>
                document
                  .getElementById("settings-connection")
                  ?.scrollIntoView({ behavior: "smooth", block: "start" })
              }
            >
              连接、术语与备份
            </button>
          </nav>
          <div className="w-settings-body">
      <section className="status-grid" id="settings-svc">
        <article className="panel status-card">
          <div className="card-title">工作台本地服务</div>
          <div className="status-line">
            <StatusDot online={health?.ok === true} />
            <strong>
              {health?.ok ? "已连接" : "未连接"}
            </strong>
          </div>
          <small>
            {health
              ? `Python ${health.python}`
              : message}
          </small>
        </article>

        <article className="panel status-card">
          <div className="card-title">Ollama</div>
          <div className="status-line">
            <StatusDot
              online={services?.ollama.online === true}
            />
            <strong>
              {services?.ollama.online ? "在线" : "未连接"}
            </strong>
          </div>
          <small>
            {settings?.connections.ollama_url ?? "等待读取设置"}
          </small>
        </article>

        <article className="panel status-card">
          <div className="card-title">ComfyUI</div>
          <div className="status-line">
            <StatusDot
              online={services?.comfyui.online === true}
            />
            <strong>
              {services?.comfyui.online ? "在线" : "未连接"}
            </strong>
          </div>
          <small>
            {settings?.connections.comfyui_url ?? "等待读取设置"}
          </small>
          {comfyStatus?.version ? (
            <small>版本 {comfyStatus.version}</small>
          ) : null}
          {comfyStatus?.online === true && comfyStatus.devices.length > 0 ? (
            comfyStatus.devices.map((device) => (
              <small key={device.index ?? device.name ?? "device"}>
                {device.name ?? device.type ?? `设备 ${device.index ?? "?"}`}
                ：显存 {formatGiB(device.vram_free)} 空闲 /{" "}
                {formatGiB(device.vram_total)}
                <span
                  className={
                    device.vram_free !== null &&
                    device.vram_free < VRAM_FREE_MIN_BYTES
                      ? "workflow-warning"
                      : "workflow-meta"
                  }
                >
                  （{vramGateLabel(device.vram_free)}）
                </span>
              </small>
            ))
          ) : comfyStatus?.online === true ? (
            <small>显存信息未知</small>
          ) : null}
          <small>
            {comfyQueue?.online === true
              ? `队列：运行中 ${comfyQueue.running.length} · 待执行 ${comfyQueue.pending.length}`
              : "队列状态未知"}
          </small>
        </article>
      </section>

      <section className="panel" id="settings-storage">
        <div className="section-heading">
          <div>
            <h2>存储位置</h2>
            <p>
              根目录由本地宿主固定。本版本只允许修改子目录，
              不会自动迁移已有数据。
            </p>
          </div>
        </div>

        {settings ? (
          <div className="path-list">
            {Object.entries(settings.storage).map(
              ([key, value]) => {
                const fixed = key === "root";
                const optional = isExternalStorageKey(key);

                return (
                  <div className="path-row" key={key}>
                    <label className="path-field">
                      <span>
                        {storageLabels[key] ?? key}
                      </span>

                      <input
                        value={value}
                        readOnly={fixed}
                        placeholder={
                          optional ? "可留空" : "请输入目录路径"
                        }
                        onChange={(event) =>
                          updateStorage(key, event.target.value)
                        }
                      />

                      {checks[key] && (
                        <small>{checks[key]}</small>
                      )}
                    </label>

                    <button
                      className="secondary"
                      disabled={fixed || busy}
                      onClick={() => void checkPath(key)}
                    >
                      检查
                    </button>
                  </div>
                );
              },
            )}
          </div>
        ) : (
          <p>正在读取存储配置…</p>
        )}
      </section>

      <section className="panel" id="settings-catalog">
        <div className="section-heading">
          <div>
            <h2>模型清单（只读）</h2>
            <p>
              名单来自 ComfyUI 已加载的模型；勾选目录后可核对本机文件是否存在。
              这里不复制、不修改模型权重。
            </p>
          </div>
        </div>

        {catalog ? (
          catalog.online ? (
            <div className="catalog-grid">
              <div className="catalog-col">
                <div className="catalog-title">
                  Checkpoint（{catalog.checkpoints.length}）
                </div>
                <ul className="catalog-list">
                  {catalog.checkpoints.map((entry) => (
                    <li key={entry.name}>
                      <span>{entry.name}</span>
                      <small>{identityLabel(entry)}</small>
                    </li>
                  ))}
                  {catalog.checkpoints.length === 0 && (
                    <li className="catalog-empty">ComfyUI 未列出 Checkpoint</li>
                  )}
                </ul>
              </div>
              <div className="catalog-col">
                <div className="catalog-title">
                  CLIP（{catalog.clips.length}）
                </div>
                <ul className="catalog-list">
                  {catalog.clips.map((entry) => (
                    <li key={entry.name}>
                      <span>{entry.name}</span>
                      <small>{identityLabel(entry)}</small>
                      <HashButton
                        category="clip"
                        entry={entry}
                        busyKey={hashingIdentity}
                        onHash={(cat, item) => void hashModelIdentity(cat, item)}
                      />
                    </li>
                  ))}
                  {catalog.clips.length === 0 && (
                    <li className="catalog-empty">ComfyUI 未列出 CLIP</li>
                  )}
                </ul>
              </div>
              <div className="catalog-col">
                <div className="catalog-title">
                  VAE（{catalog.vaes.length}）
                </div>
                <ul className="catalog-list">
                  {catalog.vaes.map((entry) => (
                    <li key={entry.name}>
                      <span>{entry.name}</span>
                      <small>{identityLabel(entry)}</small>
                      <HashButton
                        category="vae"
                        entry={entry}
                        busyKey={hashingIdentity}
                        onHash={(cat, item) => void hashModelIdentity(cat, item)}
                      />
                    </li>
                  ))}
                
                  {catalog.vaes.length === 0 && (
                    <li className="catalog-empty">ComfyUI 未列出 VAE</li>
                  )}
                </ul>
              </div>
            </div>
          ) : (
            <p className="catalog-offline">
              模型清单不可用：{catalog.error ?? "ComfyUI 未连接"}
            </p>
          )
        ) : (
          <p>正在读取模型清单…</p>
        )}
      </section>

      <section className="panel" id="settings-rules">
        <div className="section-heading">
          <div>
            <h2>规则库</h2>
            <p>
              规则记录来自档案导入；可删除已入库规则做纠错。
              前端不提供手动新增或修改，新增与覆盖仍走档案导入。
            </p>
          </div>
        </div>

        {rules === null ? (
          <p>正在读取规则库…</p>
        ) : rules.length === 0 ? (
          <p className="catalog-empty">规则库为空。</p>
        ) : (
          RULE_LEVEL_ORDER.map((level) => {
            const group = rules.filter((rule) => rule.level === level);
            if (group.length === 0) return null;
            return (
              <div className="rule-group" key={level}>
                <div className="catalog-title">
                  {RULE_LEVEL_LABEL[level] ?? level}（{group.length}）
                </div>
                <div className="rule-list">
                  {group.map((rule) => {
                    const identity = ruleIdentity(rule);
                    const historyOpen = openRuleHistory === identity;
                    const { section, title } = splitRuleKey(rule.rule_key);
                    const sectionLabel =
                      RULE_SECTION_LABEL[section] ?? section;
                    const scopeLabel = rule.scope_key || "全局";
                    const hasSub = Boolean(rule.structured || rule.note);
                    return (
                      <div className="rule-row-wrap" key={rule.id}>
                        <div className="rule-row">
                          <div className="rule-row-main">
                            <div className="rule-row-head">
                              <strong className="rule-row-title">{title}</strong>
                              {(sectionLabel || scopeLabel) && (
                                <span className="rule-row-eyebrow">
                                  {sectionLabel}
                                  {sectionLabel && scopeLabel ? " · " : ""}
                                  {scopeLabel}
                                </span>
                              )}
                            </div>
                            <div className="rule-row-value">
                              {formatRuleValue(rule.value)}
                            </div>
                            {hasSub && (
                              <small className="rule-row-sub">
                                {rule.structured
                                  ? `结构：${formatStructured(rule.structured)}`
                                  : ""}
                                {rule.structured && rule.note ? " · " : ""}
                                {rule.note ? `备注：${rule.note}` : ""}
                              </small>
                            )}
                          </div>
                          <div className="rule-row-meta">
                            <div className="rule-row-badges">
                              <span className={provenanceTagClass(rule.provenance)}>
                                {RULE_PROVENANCE_LABEL[rule.provenance] ??
                                  rule.provenance}
                              </span>
                              <span className={confidenceTagClass(rule.confidence)}>
                                {RULE_CONFIDENCE_LABEL[rule.confidence] ??
                                  rule.confidence}
                              </span>
                              <span className="rule-tag rule-version">
                                v{rule.version}
                              </span>
                            </div>
                            <div className="rule-row-actions">
                              <button
                                onClick={() => void toggleRuleHistory(rule)}
                              >
                                {historyOpen ? "收起历史" : "历史版本"}
                              </button>
                              <button
                                onClick={() => void removeRule(rule)}
                                className={
                                  deletingRule === identity ? "danger" : ""
                                }
                              >
                                {deletingRule === identity
                                  ? "确认删除？"
                                  : "删除"}
                              </button>
                            </div>
                          </div>
                        </div>
                        {historyOpen && (
                          <div className="rule-history">
                            {ruleHistory.length === 0 ? (
                              <p className="catalog-empty">暂无历史版本。</p>
                            ) : (
                              ruleHistory.map((item) => (
                                <div className="rule-history-item" key={item.history_id}>
                                  <div className="rule-row-main">
                                    <small>
                                      <span className="rule-tag">v{item.version}</span>{" "}
                                      {formatRuleValue(item.value)}
                                      {item.note ? ` · 备注：${item.note}` : ""}
                                      {item.reason === "restore"
                                        ? " · 由回退归档"
                                        : ""}
                                    </small>
                                    <small className="rule-history-time">
                                      {item.snapshot_at.replace("T", " ").slice(0, 19)}
                                    </small>
                                  </div>
                                  <div className="rule-row-meta">
                                    <button
                                      onClick={() => void restoreRuleHistory(rule, item)}
                                      className={
                                        restoringHistory === item.history_id
                                          ? "danger"
                                          : ""
                                      }
                                    >
                                      {restoringHistory === item.history_id
                                        ? "确认回退？"
                                        : "回退到此版本"}
                                    </button>
                                  </div>
                                </div>
                              ))
                            )}
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              </div>
            );
          })
        )}
      </section>

      <section className="panel" id="settings-profiles">
        <div className="section-heading">
          <div>
            <h2>旧档案候选</h2>
            <p>
              名单来自 legacy 档案文本解析；可整档导入为规则，落库前由后端逐条校验，
              非法候选在报告中拒绝。逐候选勾选 / 驳回与差异预览留后续版本。
            </p>
          </div>
          <div className="section-actions">
            {profileDir && (
              <small className="dir-path" title={profileDir.path}>
                {profileDir.path}
              </small>
            )}
            <button
              type="button"
              className="secondary"
              onClick={() => void openProfileDir()}
              disabled={openingDir || (profileDir !== null && !profileDir.exists)}
            >
              {openingDir ? "正在打开…" : "打开档案目录"}
            </button>
          </div>
        </div>

        {profiles === null ? (
          <p>正在读取旧档案…</p>
        ) : profiles.length === 0 ? (
          <p className="catalog-empty">未发现旧档案。</p>
        ) : (
          <div className="rule-list">
            {profiles.map((profile) => {
              const expanded = openProfile === profile.file;
              return (
                <div className="rule-row" key={profile.file}>
                  <div className="rule-row-main">
                    <button
                      className="profile-toggle"
                      onClick={() => void toggleProfile(profile.file)}
                      disabled={Boolean(profile.error)}
                    >
                      <strong>
                        {profile.filename ?? profile.display_name ?? profile.file}
                      </strong>
                      <small>
                        {profile.architecture ?? "架构未识别"} · 候选{" "}
                        {profile.candidates} · 未识别{" "}
                        {profile.unknown_fields.length}
                      </small>
                    </button>
                    {profile.error && (
                      <small className="catalog-empty">
                        解析失败：{profile.error}
                      </small>
                    )}
                    {expanded && preview && preview.file === profile.file && (
                      <div className="profile-preview">
                        {(() => {
                          const dismissed = new Set(
                            dismissedByProfile[profile.file] || [],
                          );
                          const visibleCandidates = preview.candidates.filter(
                            (candidate) =>
                              !dismissed.has(profileCandidateKey(candidate)),
                          );
                          const selected = selectedByProfile[profile.file] || [];
                          const busy = importingProfile === profile.file;
                          if (visibleCandidates.length === 0) {
                            return (
                              <p className="catalog-empty">
                                无可导入候选（其余已在本次预览驳回）。
                              </p>
                            );
                          }
                          return (
                            <>
                              {visibleCandidates.map((candidate, index) => {
                                const key = profileCandidateKey(candidate);
                                const checked = selected.includes(key);
                                const { section, title } = splitRuleKey(
                                  candidate.rule_key,
                                );
                                const sectionLabel =
                                  RULE_SECTION_LABEL[section] ?? section;
                                return (
                                  <div className="rule-row" key={key || index}>
                                    <label className="rule-row-check">
                                      <input
                                        type="checkbox"
                                        checked={checked}
                                        disabled={busy}
                                        onChange={() =>
                                          toggleCandidateSelected(
                                            profile.file,
                                            key,
                                          )
                                        }
                                      />
                                    </label>
                                    <div className="rule-row-main">
                                      <strong>{title}</strong>
                                      <small>
                                        {sectionLabel ||
                                          (RULE_LEVEL_LABEL[candidate.level] ??
                                            candidate.level)}
                                        {candidate.scope_key
                                          ? ` · ${candidate.scope_key}`
                                          : " · 作用域待定"}
                                        {" · 值："}
                                        {formatRuleValue(candidate.value)}
                                        {candidate.structured
                                          ? ` · 结构：${formatStructured(candidate.structured)}`
                                          : ""}
                                        {candidate.note
                                          ? ` · 备注：${candidate.note}`
                                          : ""}
                                      </small>
                                    </div>
                                    <div className="rule-row-meta">
                                      <span className={provenanceTagClass(candidate.provenance)}>
                                        {RULE_PROVENANCE_LABEL[candidate.provenance] ??
                                          candidate.provenance}
                                      </span>
                                      <span className={confidenceTagClass(candidate.confidence)}>
                                        {RULE_CONFIDENCE_LABEL[candidate.confidence] ??
                                          candidate.confidence}
                                      </span>
                                      {candidate.diff ? (
                                        <span
                                          className={
                                            candidate.diff === "override"
                                              ? "rule-tag workflow-warning"
                                              : "rule-tag workflow-meta"
                                          }
                                        >
                                          {candidate.diff === "new"
                                            ? "新增"
                                            : candidate.diff === "override"
                                              ? "将覆盖"
                                              : "一致"}
                                          {candidate.diff === "override" &&
                                          candidate.stored_value !== undefined
                                            ? `：${formatRuleValue(candidate.stored_value)}`
                                            : ""}
                                        </span>
                                      ) : null}
                                      <span className="rule-tag">
                                        v{candidate.version}
                                      </span>
                                      <button
                                        type="button"
                                        disabled={busy}
                                        onClick={() =>
                                          void importProfile(profile.file, [
                                            key,
                                          ])
                                        }
                                      >
                                        导入此条
                                      </button>
                                    </div>
                                  </div>
                                );
                              })}
                              <div className="profile-import-actions">
                                <button
                                  type="button"
                                  disabled={busy || selected.length === 0}
                                  onClick={() =>
                                    void importProfile(profile.file, selected)
                                  }
                                >
                                  {busy
                                    ? "正在导入…"
                                    : `导入所选（${selected.length}）`}
                                </button>
                                <button
                                  type="button"
                                  disabled={busy || selected.length === 0}
                                  onClick={() =>
                                    dismissSelectedCandidates(profile.file)
                                  }
                                >
                                  驳回所选（{selected.length}）
                                </button>
                                <button
                                  type="button"
                                  disabled={busy || Boolean(profile.error)}
                                  onClick={() => void importProfile(profile.file)}
                                >
                                  整档导入为规则
                                </button>
                              </div>
                            </>
                          );
                        })()}
                        {importReports[profile.file] && (
                          <div className="profile-import-report">
                            <p>
                              已导入 {importReports[profile.file].imported} 条
                              规则
                              {importReports[profile.file].rejected.length > 0 &&
                                `；拒绝 ${importReports[profile.file].rejected.length} 条`}
                            </p>
                            {importReports[profile.file].rejected.map((item) => (
                              <p
                                className="catalog-empty"
                                key={item.rule_key}
                              >
                                {item.rule_key}：{item.reason}
                              </p>
                            ))}
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </section>

      <section className="panel" id="settings-connection">
        <div className="section-heading">
          <div>
            <h2>服务连接</h2>
            <p>
              这里只配置和检测连接，不会自动启动、
              关闭或重启 ComfyUI。
            </p>
          </div>
        </div>

        {settings && (
          <div className="connection-list">
            <label>
              <span>Ollama 地址</span>
              <input
                value={settings.connections.ollama_url}
                onChange={(event) =>
                  updateConnection(
                    "ollama_url",
                    event.target.value,
                  )
                }
              />
            </label>

            <label>
              <span>ComfyUI 地址</span>
              <input
                value={settings.connections.comfyui_url}
                onChange={(event) =>
                  updateConnection(
                    "comfyui_url",
                    event.target.value,
                  )
                }
              />
            </label>

            <label>
              <span>受保护术语（逗号或换行分隔）</span>
              <textarea
                rows={3}
                value={(settings.prompt?.protected_terms ?? []).join("，")}
                onChange={(event) => updateProtectedTerms(event.target.value)}
                placeholder="例如：角色名、特定风格词、LoRA 触发词"
              />
            </label>
          </div>
        )}

        <div className="actions">
          <button
            onClick={saveSettings}
            disabled={busy || !settings}
          >
            保存设置
          </button>

          <button
            className="secondary"
            onClick={() => void exportDiagnostics()}
            disabled={busy}
          >
            导出诊断报告
          </button>

          <button
            className="secondary"
            onClick={exportSettings}
            disabled={busy || !settings}
          >
            导出设置
          </button>

          <button
            className="secondary"
            onClick={() => void exportBackup()}
            disabled={busy}
          >
            导出整库备份
          </button>

          <button
            className="secondary"
            onClick={() => backupFileRef.current?.click()}
            disabled={busy}
          >
            恢复备份
          </button>
          <input
            ref={backupFileRef}
            type="file"
            accept="application/zip,.zip"
            style={{ display: "none" }}
            onChange={pickBackupFile}
          />

          <button
            className="secondary"
            onClick={() => settingsFileRef.current?.click()}
            disabled={busy || !settings}
          >
            导入设置
          </button>
          <input
            ref={settingsFileRef}
            type="file"
            accept="application/json,.json"
            style={{ display: "none" }}
            onChange={importSettingsFile}
          />

          {restoreFile && (
            <div className="panel">
              <p className="muted">
                已选择备份：{restoreFile.name}。恢复前会先自动备份当前数据；
                默认保留同名现有文件，不触碰模型权重与原图。
              </p>
              <label>
                <input
                  type="checkbox"
                  checked={restoreOverwrite}
                  disabled={busy}
                  onChange={(event) =>
                    setRestoreOverwrite(event.target.checked)
                  }
                />
                覆盖同名现有文件（谨慎）
              </label>
              <div className="actions">
                <button
                  onClick={() => void confirmRestore()}
                  disabled={busy}
                >
                  {busy ? "恢复中…" : "确认恢复"}
                </button>
                <button onClick={cancelRestore} disabled={busy}>
                  取消
                </button>
              </div>
            </div>
          )}

          <span className="message" role="status">
            {message}
          </span>
        </div>
      </section>
          </div>
        </div>
      )}
        </div>
      </div>
    </div>
  );
}
