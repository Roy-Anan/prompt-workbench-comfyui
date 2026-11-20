// Standalone writing / prompt panel: candidate generation without a bound
// workflow. Posts to the backend's standalone candidate route and only keeps
// results in local state; nothing here writes a working copy, draft or history.

import { useEffect, useState } from "react";
import { apiGet, apiPost } from "./api";

const CANDIDATE_MODE_OPTIONS: Array<{ value: string; label: string }> = [
  { value: "draft", label: "起草" },
  { value: "polish", label: "润色" },
  { value: "trim", label: "精简" },
  { value: "bilingual", label: "中英对照" },
  { value: "negative", label: "负向词" },
];

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

function parseModelParamSize(params: string | undefined): number {
  if (!params) return 0;
  const match = params.toLowerCase().match(/(\d+(?:\.\d+)?)/);
  return match ? Number(match[1]) : 0;
}

function defaultModel(models: string[], details: Record<string, string>): string {
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

type CandidateScope = "positive" | "negative";

type CandidateItem = {
  text: string;
  mode: string;
  scope: CandidateScope;
};

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
};

type Persona = {
  id: string;
  display_name: string;
  kind: "prompt" | "writing" | string;
  enabled?: boolean;
  exists?: boolean;
};

type LayerItem = { id: string; display_name: string; exists?: boolean };

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

type OllamaModelsResponse = {
  online: boolean;
  models: string[];
  model_details?: Record<string, string>;
  error: string | null;
};

type PersonasResponse = { items: Persona[] };
type LayersResponse = { items: LayerItem[] };
type PromptProfilesResponse = { items: PromptProfile[] };

export default function WritingAssistant() {
  const [modelsOnline, setModelsOnline] = useState(true);
  const [ollamaModels, setOllamaModels] = useState<string[]>([]);
  const [modelDetails, setModelDetails] = useState<Record<string, string>>({});
  const [selectedModel, setSelectedModel] = useState("");

  const [personas, setPersonas] = useState<Persona[]>([]);
  const [selectedPersona, setSelectedPersona] = useState(() =>
    readStoredParam("writing.selectedPersona") || "none",
  );
  const [profiles, setProfiles] = useState<PromptProfile[]>([]);
  const [selectedProfile, setSelectedProfile] = useState(() =>
    readStoredParam("writing.selectedProfile") || "auto",
  );
  const [genres, setGenres] = useState<LayerItem[]>([]);
  const [selectedGenre, setSelectedGenre] = useState(() =>
    readStoredParam("writing.selectedGenre"),
  );
  const [styles, setStyles] = useState<LayerItem[]>([]);
  const [selectedStyle, setSelectedStyle] = useState(() =>
    readStoredParam("writing.selectedStyle"),
  );

  const [taskText, setTaskText] = useState(() =>
    readStoredParam("writing.task"),
  );
  const [candidateTemp, setCandidateTemp] = useState(() =>
    readStoredParam("writing.temp"),
  );
  const [candidateTopP, setCandidateTopP] = useState(() =>
    readStoredParam("writing.topP"),
  );
  const [candidateCtx, setCandidateCtx] = useState(() =>
    readStoredParam("writing.ctx"),
  );
  const [candidateMode, setCandidateMode] = useState(() => {
    const stored = readStoredParam("writing.mode");
    return CANDIDATE_MODE_OPTIONS.some((opt) => opt.value === stored)
      ? stored
      : "draft";
  });

  const [candidates, setCandidates] = useState<CandidateItem[]>([]);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");

  const currentPersona = personas.find((p) => p.id === selectedPersona);
  const personaKind: string = currentPersona?.kind ?? "generic";
  const isWritingIdentity = personaKind === "writing";
  const candidateModeOptions = CANDIDATE_MODE_OPTIONS.filter(
    (opt) => !isWritingIdentity || opt.value !== "negative",
  );

  async function loadModels() {
    try {
      const result = await apiGet<OllamaModelsResponse>("/api/ollama/models");
      const details = result.model_details ?? {};
      setOllamaModels(result.models);
      setModelDetails(details);
      setModelsOnline(result.online);
      if (result.models.length > 0) {
        const remembered = readStoredParam("writing.selectedModel");
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

  async function loadPersonas() {
    try {
      const result = await apiGet<PersonasResponse>("/api/personas");
      const items = result.items ?? [];
      setPersonas(items);
      const remembered = readStoredParam("writing.selectedPersona");
      if (!remembered && items.some((p) => p.id === "prompt_assistant")) {
        setSelectedPersona("prompt_assistant");
      }
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

  async function loadProfiles() {
    try {
      const result = await apiGet<PromptProfilesResponse>("/api/prompt-profiles");
      setProfiles(result.items ?? []);
    } catch {
      // 规则套可选：失败时保留自动匹配。
      setProfiles([]);
    }
  }

  useEffect(() => {
    void loadModels();
    void loadPersonas();
    void loadGenres();
    void loadStyles();
    void loadProfiles();
  }, []);

  async function generateCandidate() {
    if (!selectedModel) return;
    setBusy(true);
    try {
      // 写作身份没有负向模式；切身份后若记忆仍是 negative，回退起草。
      const effectiveMode =
        isWritingIdentity && candidateMode === "negative" ? "draft" : candidateMode;
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

      const personaChoice = (selectedPersona || "none").trim();
      body.persona = personaChoice || "none";

      // 规则套仅作用于提示词类身份；三态：auto（省略）、指定 id、none（跳过）。
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
        "/api/workflows/prompt/candidates",
        body,
      );
      const text = result.candidates[0] ?? "";
      if (text) {
        const item: CandidateItem = {
          text,
          mode: effectiveMode,
          scope: effectiveMode === "negative" ? "negative" : "positive",
        };
        setCandidates((prev) => [...prev, item]);
      }

      const parts: string[] = [];
      if (result.persona_name) parts.push(`身份：${result.persona_name}`);
      if (result.genre) parts.push(`题材：${result.genre}`);
      if (result.style) parts.push(`文风：${result.style}`);
      if (!isWritingIdentity && result.selected_profile_name) {
        parts.push(`Profile：${result.selected_profile_name}`);
      }
      const profileWarning = result.profile_warning
        ? `；${result.profile_warning}`
        : "";
      setMessage(
        `已生成候选（规则状态：${result.rule_status}）${parts.length ? `，${parts.join("、")}` : ""}${profileWarning}。仅本地展示，未写入工作副本。`,
      );
    } catch (error) {
      setMessage(`候选生成失败：${String(error)}`);
    } finally {
      setBusy(false);
    }
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
  }

  function clearCandidates() {
    setCandidates([]);
    setMessage("候选已清空（未写入工作副本）。");
  }

  return (
    <section className="panel">
      <div className="candidate-heading">
        <h3>写作助手（无工作流裸跑）</h3>
        <p className="candidate-subtitle">
          不绑定工作流，直接由本地 LLM 起草提示词或写作内容；候选仅在本页复制使用，不进入工作副本、草稿与历史。
        </p>
      </div>

      {message && <p className="workflow-warning">{message}</p>}

      {!modelsOnline ? (
        <p className="muted">本地 Ollama 不可用，无法生成候选。</p>
      ) : ollamaModels.length === 0 ? (
        <p className="muted">未发现本地模型。</p>
      ) : (
        <div className="candidate-box">
          <textarea
            className="candidate-input"
            rows={5}
            placeholder="输入提示词要求或描写（可选）"
            value={taskText}
            disabled={busy}
            onChange={(event) => {
              setTaskText(event.target.value);
              persistParam("writing.task", event.target.value);
            }}
          />
          <div className="candidate-controls">
            <div className="candidate-primary">
              <select
                value={selectedModel}
                disabled={busy}
                onChange={(event) => {
                  setSelectedModel(event.target.value);
                  persistParam("writing.selectedModel", event.target.value);
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
                disabled={busy}
                onChange={(event) => {
                  setSelectedPersona(event.target.value);
                  persistParam("writing.selectedPersona", event.target.value);
                }}
                title="LLM 身份定位；裸模型表示不注入身份系统提示词"
              >
                <option value="none">裸模型（不注入身份）</option>
                {personas.map((persona) => (
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
                  disabled={busy}
                  onChange={(event) => {
                    setSelectedProfile(event.target.value);
                    persistParam("writing.selectedProfile", event.target.value);
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
                    disabled={busy}
                    onChange={(event) => {
                      setSelectedGenre(event.target.value);
                      persistParam("writing.selectedGenre", event.target.value);
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
                    disabled={busy}
                    onChange={(event) => {
                      setSelectedStyle(event.target.value);
                      persistParam("writing.selectedStyle", event.target.value);
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
                disabled={busy}
                onChange={(event) => {
                  setCandidateMode(event.target.value);
                  persistParam("writing.mode", event.target.value);
                }}
              >
                {candidateModeOptions.map((opt) => (
                  <option key={opt.value} value={opt.value}>
                    {opt.label}
                  </option>
                ))}
              </select>
            </div>
            <div className="candidate-params">
              <input
                type="text"
                placeholder="温度（0–2）"
                value={candidateTemp}
                disabled={busy}
                onChange={(event) => {
                  setCandidateTemp(event.target.value);
                  persistParam("writing.temp", event.target.value);
                }}
              />
              <input
                type="text"
                placeholder="Top P（0–1）"
                value={candidateTopP}
                disabled={busy}
                onChange={(event) => {
                  setCandidateTopP(event.target.value);
                  persistParam("writing.topP", event.target.value);
                }}
              />
              <input
                type="text"
                placeholder="上下文长度"
                value={candidateCtx}
                disabled={busy}
                onChange={(event) => {
                  setCandidateCtx(event.target.value);
                  persistParam("writing.ctx", event.target.value);
                }}
              />
            </div>
            <button
              className="candidate-submit"
              disabled={busy || !selectedModel}
              onClick={() => void generateCandidate()}
            >
              {busy ? "生成中…" : "生成候选"}
            </button>
          </div>
        </div>
      )}

      {candidates.length > 0 && (
        <div className="candidate-list">
          <div className="candidate-list-head">
            <span>候选 {candidates.length} 条（只读，复制使用）</span>
            <button onClick={clearCandidates}>清空全部</button>
          </div>
          {candidates.map((item, index) => {
            const modeLabel =
              CANDIDATE_MODE_OPTIONS.find((o) => o.value === item.mode)?.label ??
              item.mode;
            return (
              <div key={index} className="candidate-card">
                <div className="candidate-card-head">
                  <button className="candidate-title" type="button">
                    <span className="candidate-mode">{modeLabel}</span>
                    <span className={`candidate-scope ${item.scope}`}>
                      {item.scope === "negative" ? "写入负向" : "写入正向"}
                    </span>
                  </button>
                  <div className="actions">
                    <button onClick={() => void copyCandidate(item)}>复制</button>
                    <button onClick={() => removeCandidate(index)}>移除</button>
                  </div>
                </div>
                <textarea readOnly rows={6} value={item.text} />
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}
