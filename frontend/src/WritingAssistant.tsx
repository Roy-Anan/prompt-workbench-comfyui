// Standalone writing panel: candidate generation without a bound workflow.
// This page is the writing / bare-model entry point: the identity selector only
// offers the bare model and writing-class personas (the prompt-class assistant
// stays fixed in the workflow workbench). Results are kept in local state only;
// nothing here writes a working copy, draft or history.

import { useEffect, useState } from "react";
import { apiGet, apiPost } from "./api";

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

// 旧版英文人设 id 迁移到新的中文文件名 id；提示词类身份串到本页回退裸模型。
const LEGACY_PERSONA_IDS: Record<string, string> = {
  prompt_assistant: "none",
  writing_assistant: "写作助手",
  polish_assistant: "润色改写助手",
};

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
  model: string;
  persona_id: string | null;
  persona_name: string | null;
  genre: string | null;
  style: string | null;
  task: string;
  saved?: boolean;
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
  profile_warning?: string | null;
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

type OllamaModelsResponse = {
  online: boolean;
  models: string[];
  model_details?: Record<string, string>;
  error: string | null;
};

type PersonasResponse = { items: Persona[] };
type LayersResponse = { items: LayerItem[] };
type ModesResponse = { items: ModeItem[] };

export default function WritingAssistant() {
  const [modelsOnline, setModelsOnline] = useState(true);
  const [ollamaModels, setOllamaModels] = useState<string[]>([]);
  const [modelDetails, setModelDetails] = useState<Record<string, string>>({});
  const [selectedModel, setSelectedModel] = useState("");

  const [personas, setPersonas] = useState<Persona[]>([]);
  const [selectedPersona, setSelectedPersona] = useState(() => {
    const stored = readStoredParam("writing.selectedPersona");
    // 旧记忆若指向提示词类身份，回退裸模型；本页只保留写作类与裸模型。
    return (LEGACY_PERSONA_IDS[stored] ?? stored) || "none";
  });
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
  const [modes, setModes] = useState<ModeItem[]>([]);
  const [candidateMode, setCandidateMode] = useState(() =>
    resolveStoredMode(readStoredParam("writing.mode")),
  );

  const [candidates, setCandidates] = useState<CandidateItem[]>([]);
  const [busy, setBusy] = useState(false);
  const [savingIndex, setSavingIndex] = useState<number | null>(null);
  const [message, setMessage] = useState("");

  // 本页只列出写作类身份；提示词类助手在工作流工作区。
  const writingPersonas = personas.filter((p) => p.kind !== "prompt");
  // 写作页不提供负向词模式。
  const candidateModeOptions = modes.filter((m) => !m.negative_output);

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

  useEffect(() => {
    void loadModels();
    void loadPersonas();
    void loadGenres();
    void loadStyles();
    void loadModes();
  }, []);

  // 模式清单就绪后，记忆值若已失效（改名 / 删除 / 旧英文 slug）回退默认起草。
  useEffect(() => {
    if (!modes.length) return;
    const available = modes.filter((m) => !m.negative_output);
    if (!available.some((m) => m.id === candidateMode)) {
      const fallback = available.some((m) => m.id === DEFAULT_MODE)
        ? DEFAULT_MODE
        : available[0].id;
      setCandidateMode(fallback);
      persistParam("writing.mode", fallback);
    }
  }, [modes, candidateMode]);

  // 人设列表就绪后，记忆值若已失效（改名 / 删除 / 跨类串入）回退裸模型。
  useEffect(() => {
    if (!selectedPersona || selectedPersona === "none") return;
    if (!writingPersonas.some((p) => p.id === selectedPersona)) {
      setSelectedPersona("none");
      persistParam("writing.selectedPersona", "none");
    }
  }, [writingPersonas, selectedPersona]);

  async function generateCandidate() {
    if (!selectedModel) return;
    setBusy(true);
    try {
      const selectedMode = modes.find((m) => m.id === candidateMode);
      const effectiveMode =
        selectedMode?.negative_output ? DEFAULT_MODE : candidateMode;
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

      body.persona = (selectedPersona || "none").trim() || "none";
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
          scope: "positive",
          model: result.model,
          persona_id: result.persona_id ?? null,
          persona_name: result.persona_name ?? null,
          genre: result.genre ?? null,
          style: result.style ?? null,
          task: taskText.trim(),
        };
        setCandidates((prev) => [...prev, item]);
      }

      const parts: string[] = [];
      if (result.persona_name) parts.push(`身份：${result.persona_name}`);
      if (result.genre) parts.push(`题材：${result.genre}`);
      if (result.style) parts.push(`文风：${result.style}`);
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

  async function saveCandidate(index: number) {
    const item = candidates[index];
    if (!item || savingIndex !== null) return;
    setSavingIndex(index);
    try {
      await apiPost("/api/history/writing", {
        model: item.model,
        persona_id: item.persona_id,
        persona_name: item.persona_name,
        genre: item.genre,
        style: item.style,
        mode: item.mode,
        task: item.task,
        output_text: item.text,
      });
      setCandidates((prev) =>
        prev.map((entry, i) => (i === index ? { ...entry, saved: true } : entry)),
      );
      setMessage("候选已存入写作历史（仅在主动保存时归档）。");
    } catch (error) {
      setMessage(`存入历史失败：${String(error)}`);
    } finally {
      setSavingIndex(null);
    }
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
          不绑定工作流，由本地 LLM 按写作身份或裸模型起草内容；可选题材与文风。候选仅在本页使用，点「存入历史」才归档，不写入工作副本与草稿。
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
                {writingPersonas.map((persona) => (
                  <option
                    key={persona.id}
                    value={persona.id}
                    disabled={persona.enabled === false}
                  >
                    {persona.display_name}
                  </option>
                ))}
              </select>
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
              <select
                value={candidateMode}
                disabled={busy}
                onChange={(event) => {
                  setCandidateMode(event.target.value);
                  persistParam("writing.mode", event.target.value);
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
              modes.find((o) => o.id === item.mode)?.display_name ?? item.mode;
            return (
              <div key={index} className="candidate-card">
                <div className="candidate-card-head">
                  <button className="candidate-title" type="button">
                    <span className="candidate-mode">{modeLabel}</span>
                    <span className="candidate-scope positive">写入正向</span>
                  </button>
                  <div className="actions">
                    {item.saved ? (
                      <small className="workflow-meta">已存历史</small>
                    ) : (
                      <button
                        onClick={() => void saveCandidate(index)}
                        disabled={savingIndex !== null}
                      >
                        {savingIndex === index ? "保存中…" : "存入历史"}
                      </button>
                    )}
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
