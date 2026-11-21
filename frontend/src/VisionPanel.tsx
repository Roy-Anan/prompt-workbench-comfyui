// Standalone vision panel: ask a local Ollama VL model to describe a picture.
// Workbench only — there is no ComfyUI-node counterpart here.
//
// Pictures may come from the asset library (referenced by asset_id, archived to
// history together with the description) or be picked up as a temporary local
// upload (kept on this page only, never written to history or stored on disk).

import { useEffect, useState } from "react";
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
};

type AssetOption = { id: number; name: string; exists: boolean };

type VisionPick = {
  asset_id: number | null;
  name: string;
  dataUrl: string;
};

const MAX_IMAGES = 2;

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

function assetImageUrl(assetId: number): string {
  return `/api/assets/image?asset_id=${assetId}`;
}

export default function VisionPanel() {
  const [modelsOnline, setModelsOnline] = useState(true);
  const [models, setModels] = useState<string[]>([]);
  const [modelDetails, setModelDetails] = useState<Record<string, string>>({});
  const [selectedModel, setSelectedModel] = useState("");
  const [images, setImages] = useState<VisionPick[]>([]);
  const [assets, setAssets] = useState<AssetOption[]>([]);
  const [pickedAsset, setPickedAsset] = useState("");
  const [promptText, setPromptText] = useState(() =>
    readStoredParam("vision.prompt"),
  );
  const [busy, setBusy] = useState(false);
  const [saveBusy, setSaveBusy] = useState(false);
  const [description, setDescription] = useState("");
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

    async function loadAssets() {
      try {
        const result = await apiGet<{ items: AssetOption[] }>(
          "/api/assets/items",
        );
        setAssets(
          (result.items || []).filter((item) => item.exists !== false),
        );
      } catch {
        setAssets([]);
      }
    }

    void loadModels();
    void loadAssets();
  }, []);

  async function addFiles(fileList: FileList | null) {
    if (!fileList) return;
    const slots = MAX_IMAGES - images.length;
    if (slots <= 0) return;
    const files = Array.from(fileList)
      .filter((file) => file.type.startsWith("image/"))
      .slice(0, slots);
    const loaded: VisionPick[] = [];
    for (const file of files) {
      const dataUrl = await readFileAsDataUrl(file);
      loaded.push({ asset_id: null, name: file.name, dataUrl });
    }
    setImages((prev) => [...prev, ...loaded].slice(0, MAX_IMAGES));
  }

  async function addAsset() {
    const assetId = Number(pickedAsset);
    if (!Number.isFinite(assetId)) return;
    const option = assets.find((item) => item.id === assetId);
    if (!option) return;
    if (images.some((image) => image.asset_id === assetId)) {
      setMessage("这张素材已经在待识别列表中。");
      return;
    }
    if (images.length >= MAX_IMAGES) {
      setMessage(`最多选择 ${MAX_IMAGES} 张图片。`);
      return;
    }
    try {
      const response = await fetch(`/api/assets/image?asset_id=${assetId}`, {
        credentials: "include",
      });
      if (!response.ok) throw new Error(`素材图片不可用（${response.status}）`);
      const blob = await response.blob();
      const dataUrl = await blobToDataUrl(blob);
      setImages((prev) =>
        [...prev, { asset_id: assetId, name: option.name, dataUrl }].slice(
          0,
          MAX_IMAGES,
        ),
      );
      setPickedAsset("");
      setMessage("");
    } catch (error) {
      setMessage(`读取素材图片失败：${String(error)}`);
    }
  }

  function removeImage(index: number) {
    setImages((prev) => prev.filter((_, i) => i !== index));
  }

  async function describe() {
    if (!selectedModel || images.length === 0) return;
    setBusy(true);
    setDescription("");
    setSaved(false);
    try {
      const body: Record<string, unknown> = {
        model: selectedModel,
        prompt: promptText.trim() || null,
        images: images.map((image) => image.dataUrl),
      };
      const result = await apiPost<VisionResponse>(
        "/api/vision/describe",
        body,
      );
      setDescription(result.description);
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
          选择本地 VL 模型识别图片；素材库图片可连同描述存入历史，本地上传的临时图只留在本页、不进历史。
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
              <button
                type="button"
                className="secondary"
                disabled={busy || !pickedAsset}
                onClick={() => void addAsset()}
              >
                添加素材图
              </button>
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
            {assets.length > 0 ? (
              <div className="vision-asset-picker">
                <div className="vision-asset-grid">
                  {assets.map((asset) => {
                    const added = images.some(
                      (image) => image.asset_id === asset.id,
                    );
                    const selected = pickedAsset === String(asset.id);
                    return (
                      <button
                        key={asset.id}
                        type="button"
                        className={[
                          "vision-asset-card",
                          selected ? "selected" : "",
                          added ? "added" : "",
                        ]
                          .filter(Boolean)
                          .join(" ")}
                        disabled={busy || added}
                        onClick={() =>
                          setPickedAsset(selected ? "" : String(asset.id))
                        }
                        title={asset.name}
                      >
                        <span className="vision-asset-thumb">
                          <img
                            src={assetImageUrl(asset.id)}
                            alt={asset.name}
                            loading="lazy"
                            onError={(event) => {
                              event.currentTarget.style.display = "none";
                            }}
                          />
                        </span>
                        <span className="vision-asset-name">{asset.name}</span>
                        {added && (
                          <span className="vision-asset-badge">已添加</span>
                        )}
                      </button>
                    );
                  })}
                </div>
              </div>
            ) : (
              <p className="muted">
                素材库暂无可用图片，可点「本地上传」临时选图。
              </p>
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
            {images.length > 0 && (
              <div className="vision-images">
                {images.map((image, index) => (
                  <figure key={index}>
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
                      : "仅素材库图片可存入历史；本地上传的临时图不会归档"
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
              本次含本地临时图，不会进入历史；改用素材库图片后可归档识别结果。
            </small>
          )}
        </div>
      )}
    </section>
  );
}
