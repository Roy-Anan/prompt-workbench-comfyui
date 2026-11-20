// Standalone vision panel: ask a local Ollama VL model to describe an uploaded
// picture. Workbench only — there is no ComfyUI-node counterpart here. Images
// and the description stay on this page; nothing is written to a workflow,
// draft or history.

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

type VisionImage = {
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

export default function VisionPanel() {
  const [modelsOnline, setModelsOnline] = useState(true);
  const [models, setModels] = useState<string[]>([]);
  const [modelDetails, setModelDetails] = useState<Record<string, string>>({});
  const [selectedModel, setSelectedModel] = useState("");
  const [images, setImages] = useState<VisionImage[]>([]);
  const [promptText, setPromptText] = useState(() =>
    readStoredParam("vision.prompt"),
  );
  const [busy, setBusy] = useState(false);
  const [description, setDescription] = useState("");
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
    void loadModels();
  }, []);

  async function addFiles(fileList: FileList | null) {
    if (!fileList) return;
    const slots = MAX_IMAGES - images.length;
    if (slots <= 0) return;
    const files = Array.from(fileList)
      .filter((file) => file.type.startsWith("image/"))
      .slice(0, slots);
    const loaded: VisionImage[] = [];
    for (const file of files) {
      const dataUrl = await readFileAsDataUrl(file);
      loaded.push({ name: file.name, dataUrl });
    }
    setImages((prev) => [...prev, ...loaded].slice(0, MAX_IMAGES));
  }

  function removeImage(index: number) {
    setImages((prev) => prev.filter((_, i) => i !== index));
  }

  async function describe() {
    if (!selectedModel || images.length === 0) return;
    setBusy(true);
    setDescription("");
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
      setMessage(
        `已由 ${result.model} 识别 ${result.image_count} 张图片；结果只读，未写入工作副本或历史。`,
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

  return (
    <section className="panel">
      <div className="candidate-heading">
        <h3>图片识别（本地视觉模型）</h3>
        <p className="candidate-subtitle">
          选择本地 VL 模型上传图片进行描述，图片与结果都只留在本机；不写入工作副本、草稿与历史。
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
                选择图片（最多 {MAX_IMAGES} 张）
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
                    <figcaption>{image.name}</figcaption>
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
            <button onClick={() => void copyDescription()}>复制</button>
          </div>
          <textarea readOnly rows={8} value={description} />
        </div>
      )}
    </section>
  );
}
