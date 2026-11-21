// Standalone vision panel: ask a local Ollama VL model to describe a picture.
// Workbench only — there is no ComfyUI-node counterpart here.
//
// Pictures arrive in two ways: handed over from the asset library ("图生文识别"
// on an asset's detail card, referenced by asset_id) or picked up as a
// temporary local upload (kept on this page only, never stored on disk).
// After a description comes back, library assets can be tagged / annotated in
// place, and temporary uploads can be filed into the library with or without
// tags.

import { useEffect, useRef, useState } from "react";
import { apiGet, apiPost } from "./api";

type VisionModelsResponse = {
  online: boolean;
  models: string[];
  model_details?: Record<string, string>;
  model_families?: Record<string, string[]>;
  model_vision?: Record<string, boolean>;
  error: string | null;
};

type VisionResponse = {
  model: string;
  description: string;
  image_count: number;
  tags?: string[];
};

type AssetGroup = { id: number; name: string; is_default: boolean; count: number };

type AssetItemBrief = {
  id: number;
  name: string;
  tags: string[];
  note: string;
  exists: boolean;
};

type VisionSeed = { asset_id: number; name: string };

type VisionPick = {
  key: string;
  asset_id: number | null;
  name: string;
  dataUrl: string;
  tags: string[];
  note: string;
};

type VisionPanelProps = {
  /** An asset handed over from the library page. */
  seed?: VisionSeed | null;
  /** Called once the seed has been taken into the pick list. */
  onSeedUsed?: () => void;
};

const MAX_IMAGES = 2;
const LIBRARY_SUFFIXES = [".png", ".jpg", ".jpeg", ".webp"];

function readStoredParam(key: string): string {
  try {
    return localStorage.getItem(key) || "";
  } catch {
    return "";
  }
}

function persistParam(key: string, value: string): void {
  try {
    localStorage.setItem(key, value);
  } catch {
    // 忽略本地存储写入失败。
  }
}

function readFileAsDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}

function blobToDataUrl(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(blob);
  });
}

function isVisionModel(
  name: string,
  families: Record<string, string[]>,
  vision?: Record<string, boolean>,
): boolean {
  // Prefer the backend's projector-level probe; fall back to family tags when
  // the server does not expose that detail.
  if (vision && Object.prototype.hasOwnProperty.call(vision, name)) {
    return Boolean(vision[name]);
  }
  const tags = (families[name] ?? []).map((tag) => tag.toLowerCase());
  return tags.some((tag) => tag.includes("vl") || tag.includes("vision"));
}

function splitTags(text: string): string[] {
  return text
    .split(/[,，]/)
    .map((entry) => entry.trim())
    .filter(Boolean);
}

/** Suffix implied by a data URL's mime type, used when the file name has none. */
function dataUrlSuffix(dataUrl: string): string {
  const end = dataUrl.indexOf(";");
  const mime = end > 5 ? dataUrl.slice(5, end) : "";
  if (mime === "image/jpeg") return ".jpg";
  if (mime === "image/webp") return ".webp";
  if (mime === "image/gif") return ".gif";
  return ".png";
}

function resolveUploadName(name: string, dataUrl: string): { name: string; ext: string } {
  const match = /\.([A-Za-z0-9]+)$/.exec(name.trim());
  if (match) {
    const ext = `.${match[1].toLowerCase()}`;
    return { name: name.trim(), ext };
  }
  const ext = dataUrlSuffix(dataUrl);
  const base = name.trim() || "临时图片";
  return { name: `${base}${ext}`, ext };
}

let pickSeq = 0;
function nextPickKey(): string {
  pickSeq += 1;
  return `p${pickSeq}`;
}

export default function VisionPanel({ seed, onSeedUsed }: VisionPanelProps) {
  const [modelsOnline, setModelsOnline] = useState(true);
  const [models, setModels] = useState<string[]>([]);
  const [modelDetails, setModelDetails] = useState<Record<string, string>>({});
  const [selectedModel, setSelectedModel] = useState("");
  const [images, setImages] = useState<VisionPick[]>([]);
  const [groups, setGroups] = useState<AssetGroup[]>([]);
  const [promptText, setPromptText] = useState(() =>
    readStoredParam("vision.prompt"),
  );
  const [busy, setBusy] = useState(false);
  const [saveBusy, setSaveBusy] = useState(false);
  const [description, setDescription] = useState("");
  const [suggestedTags, setSuggestedTags] = useState<string[]>([]);
  const [lastModel, setLastModel] = useState("");
  const [saved, setSaved] = useState(false);
  const [message, setMessage] = useState("");

  useEffect(() => {
    async function loadModels() {
      try {
        const result = await apiGet<VisionModelsResponse>("/api/ollama/models");
        const families = result.model_families ?? {};
        const vision = result.model_vision;
        const visionModels = result.models.filter((name) =>
          isVisionModel(name, families, vision),
        );
        setModels(visionModels);
        setModelDetails(result.model_details ?? {});
        setModelsOnline(result.online);
        if (visionModels.length > 0) {
          const remembered = readStoredParam("vision.selectedModel");
          setSelectedModel(
            visionModels.includes(remembered) ? remembered : visionModels[0],
          );
        }
      } catch (error) {
        setModelsOnline(false);
        setMessage(`读取本地模型列表失败：${String(error)}`);
      }
    }

    async function loadGroups() {
      try {
        const result = await apiGet<{ groups: AssetGroup[] }>(
          "/api/assets/groups",
        );
        setGroups(result.groups || []);
      } catch {
        setGroups([]);
      }
    }

    void loadModels();
    void loadGroups();
  }, []);

  // Take the asset handed over from the library, once per hand-over.
  useEffect(() => {
    if (!seed) return;
    const handed = seed;
    let alive = true;

    async function takeSeed() {
      try {
        const response = await fetch(`/api/assets/image?asset_id=${handed.asset_id}`, {
          credentials: "include",
        });
        if (!response.ok) throw new Error(`素材图片不可用（${response.status}）`);
        const dataUrl = await blobToDataUrl(await response.blob());
        const listing = await apiGet<{ items: AssetItemBrief[] }>("/api/assets/items");
        const meta = (listing.items || []).find((item) => item.id === handed.asset_id);
        if (!alive) return;
        setImages((prev) => {
          if (prev.some((pick) => pick.asset_id === handed.asset_id)) {
            setMessage("这张素材已经在待识别列表中。");
            return prev;
          }
          if (prev.length >= MAX_IMAGES) {
            setMessage(`最多选择 ${MAX_IMAGES} 张图片，已忽略带入的素材。`);
            return prev;
          }
          return [
            ...prev,
            {
              key: nextPickKey(),
              asset_id: handed.asset_id,
              name: handed.name,
              dataUrl,
              tags: meta?.tags ?? [],
              note: meta?.note ?? "",
            },
          ];
        });
        setMessage("");
      } catch (error) {
        if (alive) setMessage(`读取素材图片失败：${String(error)}`);
      } finally {
        if (alive) onSeedUsed?.();
      }
    }

    void takeSeed();
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [seed]);

  async function addFiles(fileList: FileList | null) {
    if (!fileList) return;
    const slots = MAX_IMAGES - images.length;
    if (slots <= 0) {
      setMessage(`最多选择 ${MAX_IMAGES} 张图片。`);
      return;
    }
    const files = Array.from(fileList)
      .filter((file) => file.type.startsWith("image/"))
      .slice(0, slots);
    const loaded: VisionPick[] = [];
    for (const file of files) {
      const dataUrl = await readFileAsDataUrl(file);
      loaded.push({
        key: nextPickKey(),
        asset_id: null,
        name: file.name,
        dataUrl,
        tags: [],
        note: "",
      });
    }
    setImages((prev) => [...prev, ...loaded].slice(0, MAX_IMAGES));
  }

  function removeImage(index: number) {
    setImages((prev) => prev.filter((_, i) => i !== index));
  }

  function patchPick(index: number, patch: Partial<VisionPick>) {
    setImages((prev) =>
      prev.map((pick, i) => (i === index ? { ...pick, ...patch } : pick)),
    );
  }

  async function describe() {
    if (!selectedModel || images.length === 0) return;
    setBusy(true);
    setDescription("");
    setSuggestedTags([]);
    setSaved(false);
    setMessage("");
    try {
      const body: Record<string, unknown> = {
        model: selectedModel,
        prompt: promptText.trim() || null,
        images: images.map((image) => image.dataUrl),
        want_tags: true,
      };
      const result = await apiPost<VisionResponse>("/api/vision/describe", body);
      setDescription(result.description);
      setSuggestedTags(result.tags ?? []);
      setLastModel(result.model);
      setMessage(
        `已由 ${result.model} 识别 ${result.image_count} 张图片；结果只读，未写入工作副本。`,
      );
    } catch (error) {
      setMessage(`图片识别失败：${String(error)}`);
    } finally {
      setBusy(false);
    }
  }

  async function copyDescription() {
    if (!description) return;
    try {
      await navigator.clipboard.writeText(description);
      setMessage("识别结果已复制到剪贴板。");
    } catch (error) {
      setMessage(`复制失败：${String(error)}`);
    }
  }

  const assetPicks = images.filter((image) => image.asset_id !== null);
  const canSave = Boolean(description) && assetPicks.length > 0;

  async function saveHistory() {
    if (!canSave || saveBusy) return;
    setSaveBusy(true);
    try {
      await apiPost("/api/history/vision", {
        model: lastModel || selectedModel,
        prompt: promptText.trim() || null,
        description,
        assets: assetPicks.map((image) => ({
          asset_id: image.asset_id as number,
          name: image.name,
        })),
      });
      setSaved(true);
      setMessage("识别结果已存入历史（仅引用素材库图片，不保存副本）。");
    } catch (error) {
      setMessage(`存入历史失败：${String(error)}`);
    } finally {
      setSaveBusy(false);
    }
  }

  return (
    <section className="panel">
      <div className="candidate-heading">
        <h3>图片识别（本地视觉模型）</h3>
        <p className="candidate-subtitle">
          在素材库点某张素材的「图生文识别」把它带到这里，或本地上传临时图；识别完可给素材打标签，临时图也能连标签一起存入素材库。
        </p>
      </div>

      {message && <p className="workflow-warning">{message}</p>}

      {!modelsOnline ? (
        <p className="muted">本地 Ollama 不可用，无法识别图片。</p>
      ) : models.length === 0 ? (
        <p className="muted">
          未发现可用的本地视觉（VL）模型；纯文本模型不能读取图片。若某模型架构标注为
          VL 却未列出，通常是未挂载视觉投影器（mmproj），需补齐投影器权重后才能识别图片。
        </p>
      ) : (
        <div className="candidate-box">
          <div className="candidate-controls">
            <div className="candidate-primary">
              <select
                value={selectedModel}
                disabled={busy}
                onChange={(event) => {
                  setSelectedModel(event.target.value);
                  persistParam("vision.selectedModel", event.target.value);
                }}
                title="本地视觉（VL）模型"
              >
                {models.map((model) => {
                  const params = modelDetails[model];
                  const label = params ? `${model}（${params}）` : model;
                  return (
                    <option key={model} value={model}>
                      {label}
                    </option>
                  );
                })}
              </select>
              <label className="vision-file-button">
                本地上传（临时）
                <input
                  type="file"
                  accept="image/png,image/jpeg,image/webp,image/gif"
                  multiple
                  disabled={busy}
                  onChange={(event) => {
                    void addFiles(event.target.files);
                    event.target.value = "";
                  }}
                  style={{ display: "none" }}
                />
              </label>
            </div>

            {images.length === 0 ? (
              <p className="muted">
                还没有待识别图片：去素材库详情点「图生文识别」，或点上方「本地上传」选一张临时图。
              </p>
            ) : (
              <div className="vision-images">
                {images.map((image, index) => (
                  <figure key={image.key}>
                    <img src={image.dataUrl} alt={image.name} />
                    <figcaption>
                      {image.name}
                      <small className="muted">
                        {image.asset_id !== null ? "素材引用" : "临时图"}
                      </small>
                    </figcaption>
                    <div className="actions">
                      <button onClick={() => removeImage(index)} disabled={busy}>
                        移除
                      </button>
                    </div>
                  </figure>
                ))}
              </div>
            )}

            <textarea
              className="candidate-input"
              rows={3}
              placeholder="想让模型重点看什么？留空则按默认视角描述主体、构图、光线与风格。"
              value={promptText}
              disabled={busy}
              onChange={(event) => {
                setPromptText(event.target.value);
                persistParam("vision.prompt", event.target.value);
              }}
            />
            <button
              className="candidate-submit"
              disabled={busy || !selectedModel || images.length === 0}
              onClick={() => void describe()}
            >
              {busy ? "识别中…" : "识别图片"}
            </button>
          </div>
        </div>
      )}

      {description && (
        <>
          <div className="candidate-list">
            <div className="candidate-list-head">
              <span>识别结果（只读）</span>
              <div className="actions">
                {saved ? (
                  <small className="workflow-meta">已存入历史</small>
                ) : (
                  <button
                    onClick={() => void saveHistory()}
                    disabled={!canSave || saveBusy}
                    title={
                      canSave
                        ? "把这次识别结果连同素材引用存入历史"
                        : "只有素材库图片可以存入历史；临时图请先存入素材库"
                    }
                  >
                    {saveBusy ? "保存中…" : "存入历史"}
                  </button>
                )}
                <button onClick={() => void copyDescription()}>复制</button>
              </div>
            </div>
            <textarea readOnly rows={8} value={description} />
            {!canSave && (
              <small className="muted">
                本次含本地临时图，不会进入历史；把它存入素材库后即可归档识别结果。
              </small>
            )}
          </div>

          <div className="vision-care">
            <div className="vision-care-head">
              <span>本次图片处理</span>
              {suggestedTags.length > 0 && (
                <small className="muted">
                  模型建议标签：{suggestedTags.join("、")}
                </small>
              )}
            </div>
            {images.map((image, index) => (
              <CareCard
                key={image.key}
                pick={image}
                description={description}
                suggestedTags={suggestedTags}
                groups={groups}
                onPatch={(patch) => patchPick(index, patch)}
                onMessage={setMessage}
              />
            ))}
          </div>
        </>
      )}
    </section>
  );
}

type CareCardProps = {
  pick: VisionPick;
  description: string;
  suggestedTags: string[];
  groups: AssetGroup[];
  onPatch: (patch: Partial<VisionPick>) => void;
  onMessage: (text: string) => void;
};

/** Existing tags first, then any model suggestion the asset does not carry. */
function prefillTags(pick: VisionPick, suggestions: string[]): string {
  if (pick.asset_id === null) return suggestions.join(", ");
  const merged = [...pick.tags];
  for (const tag of suggestions) {
    if (!merged.includes(tag)) merged.push(tag);
  }
  return merged.join(", ");
}

function CareCard({
  pick,
  description,
  suggestedTags,
  groups,
  onPatch,
  onMessage,
}: CareCardProps) {
  const isAsset = pick.asset_id !== null;
  const suggestionsKey = suggestedTags.join("|");
  const [tagText, setTagText] = useState(() => prefillTags(pick, suggestedTags));
  const [noteText, setNoteText] = useState(pick.note);
  const [groupId, setGroupId] = useState("");
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState("");

  const tagsKey = pick.tags.join("|");
  const firstRun = useRef(true);

  // Re-fill the inputs when the pick turns from a temp upload into a library
  // asset, when its stored tags change, or when a new description brings new
  // suggestions. The very first run is skipped: mount already prefilled them.
  useEffect(() => {
    if (firstRun.current) {
      firstRun.current = false;
      return;
    }
    setTagText(prefillTags(pick, suggestedTags));
    setNoteText(pick.note);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pick.asset_id, tagsKey, suggestionsKey]);

  async function saveTags() {
    if (!isAsset || busy) return;
    setBusy(true);
    setDone("");
    try {
      const tags = splitTags(tagText);
      await apiPost(`/api/assets/items/${pick.asset_id}`, {
        tags,
        note: noteText,
      });
      onPatch({ tags, note: noteText });
      setDone("已保存到素材");
      onMessage("标签与备注已保存到素材。");
    } catch (error) {
      onMessage(`保存素材标签失败：${String(error)}`);
    } finally {
      setBusy(false);
    }
  }

  async function importIntoLibrary(withTags: boolean) {
    if (isAsset || busy) return;
    const { name, ext } = resolveUploadName(pick.name, pick.dataUrl);
    if (!LIBRARY_SUFFIXES.includes(ext)) {
      onMessage("GIF 等格式暂不支持存入素材库，请先转成 PNG / JPG / WebP。");
      return;
    }
    const comma = pick.dataUrl.indexOf(",");
    const data = comma >= 0 ? pick.dataUrl.slice(comma + 1) : pick.dataUrl;
    if (!data) {
      onMessage("临时图读取失败，请重新上传。");
      return;
    }
    const tags = withTags ? splitTags(tagText) : [];
    setBusy(true);
    setDone("");
    try {
      const result = await apiPost<{ items: AssetItemBrief[] }>(
        "/api/assets/import-copy",
        {
          files: [{ name, data }],
          group_id: groupId ? Number(groupId) : null,
          tags,
          note: description,
        },
      );
      const created = result.items?.[0];
      if (!created) throw new Error("入库未返回素材记录");
      onPatch({
        asset_id: created.id,
        name: created.name,
        tags: created.tags ?? tags,
        note: created.note ?? description,
      });
      setDone("已存入素材库");
      onMessage(
        tags.length > 0
          ? "临时图已带标签存入素材库。"
          : "临时图已存入素材库。",
      );
    } catch (error) {
      onMessage(`存入素材库失败：${String(error)}`);
    } finally {
      setBusy(false);
    }
  }

  function appendDescription() {
    setNoteText((prev) => (prev.trim() ? `${prev}\n\n${description}` : description));
  }

  return (
    <div className="vision-care-card">
      <div className="vision-care-top">
        <span className="vision-care-name" title={pick.name}>
          {pick.name}
        </span>
        <span className={`compare-badge ${isAsset ? "match" : ""}`}>
          {isAsset ? "素材引用" : "临时图"}
        </span>
        {done && <small className="workflow-meta">{done}</small>}
      </div>

      {isAsset ? (
        <>
          <label className="vision-care-field">
            标签（逗号分隔）
            <input
              type="text"
              value={tagText}
              disabled={busy}
              onChange={(event) => setTagText(event.target.value)}
              placeholder="人物, 外景"
            />
          </label>
          <label className="vision-care-field">
            备注
            <textarea
              rows={3}
              value={noteText}
              disabled={busy}
              onChange={(event) => setNoteText(event.target.value)}
            />
          </label>
          <div className="actions">
            <button
              type="button"
              className="secondary"
              disabled={busy}
              onClick={appendDescription}
            >
              追加识别描述到备注
            </button>
            <button type="button" disabled={busy} onClick={() => void saveTags()}>
              {busy ? "保存中…" : "保存到素材"}
            </button>
          </div>
        </>
      ) : (
        <>
          <label className="vision-care-field">
            标签（逗号分隔，可留空）
            <input
              type="text"
              value={tagText}
              disabled={busy}
              onChange={(event) => setTagText(event.target.value)}
              placeholder="留空则直接入库"
            />
          </label>
          <label className="vision-care-field">
            分组
            <select
              value={groupId}
              disabled={busy}
              onChange={(event) => setGroupId(event.target.value)}
            >
              <option value="">默认分组</option>
              {groups.map((group) => (
                <option key={group.id} value={group.id}>
                  {group.name}
                </option>
              ))}
            </select>
          </label>
          <div className="actions">
            <button
              type="button"
              disabled={busy || splitTags(tagText).length === 0}
              onClick={() => void importIntoLibrary(true)}
              title="标签同时写进素材库"
            >
              带标签入库
            </button>
            <button
              type="button"
              className="secondary"
              disabled={busy}
              onClick={() => void importIntoLibrary(false)}
            >
              直接入库
            </button>
          </div>
        </>
      )}
    </div>
  );
}
