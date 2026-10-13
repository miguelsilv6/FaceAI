import type {
  AuditEvent, Case, Detection, Job, JobParams, ReferenceImage, ReviewStatus, Segment, SystemInfo, Track, Video,
} from "./types";

const ANALYST_KEY = "faceai.analyst";

export function getAnalyst(): string {
  try {
    return localStorage.getItem(ANALYST_KEY) ?? "";
  } catch {
    return "";
  }
}

export function setAnalyst(name: string): void {
  try {
    localStorage.setItem(ANALYST_KEY, name);
  } catch {
    /* armazenamento indisponível */
  }
}

function headers(extra?: Record<string, string>): Record<string, string> {
  const h: Record<string, string> = { ...extra };
  const analyst = getAnalyst();
  if (analyst) h["X-Analyst"] = encodeURIComponent(analyst);
  return h;
}

async function parseError(res: Response): Promise<string> {
  try {
    const body = await res.json();
    if (typeof body.detail === "string") return body.detail;
    if (Array.isArray(body.detail)) return body.detail.map((d: { msg: string }) => d.msg).join("; ");
  } catch {
    /* sem corpo JSON */
  }
  return `${res.status} ${res.statusText}`;
}

async function request<T>(method: string, url: string, body?: unknown): Promise<T> {
  const res = await fetch(url, {
    method,
    headers: headers(body !== undefined ? { "Content-Type": "application/json" } : undefined),
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) throw new Error(await parseError(res));
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

interface UploadStatus {
  id: string;
  received_bytes: number;
  size_bytes: number;
  chunk_size: number;
  completed: boolean;
}

class HttpError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

function storage(op: "get" | "set" | "remove", key: string, value?: string): string | null {
  try {
    if (op === "get") return localStorage.getItem(key);
    if (op === "set") localStorage.setItem(key, value ?? "");
    else localStorage.removeItem(key);
  } catch {
    /* armazenamento indisponível: perde-se só a retoma após recarregar a página */
  }
  return null;
}

/**
 * Upload em blocos com retoma. Cada bloco é um pedido separado (abaixo do limite de 100 MB do Cloudflare).
 * Falhas de rede são repetidas com espera crescente; ao voltar a largar o mesmo ficheiro (mesmo depois de
 * recarregar a página), o upload continua de onde ficou.
 */
async function uploadChunked<T>(caseId: number, kind: "video" | "reference", file: File,
  onBytes: (sent: number) => void): Promise<T> {
  const key = `faceai.upload.${caseId}.${kind}.${file.name}.${file.size}.${file.lastModified}`;
  let status: UploadStatus | null = null;
  const saved = storage("get", key);
  if (saved) {
    try {
      status = await request<UploadStatus>("GET", `/api/uploads/${saved}`);
      if (status.completed) status = null;
    } catch {
      status = null;
    }
  }
  if (!status) {
    status = await request<UploadStatus>("POST", `/api/cases/${caseId}/uploads`,
      { kind, filename: file.name, size_bytes: file.size });
    storage("set", key, status.id);
  }
  let offset = status.received_bytes;
  onBytes(offset);
  while (offset < file.size) {
    const blob = file.slice(offset, offset + status.chunk_size);
    for (let attempt = 1; ; attempt++) {
      try {
        const res = await fetch(`/api/uploads/${status.id}?offset=${offset}`, {
          method: "PUT", headers: headers({ "Content-Type": "application/octet-stream" }), body: blob,
        });
        const body = await res.json().catch(() => ({}));
        if ((res.ok || res.status === 409) && typeof body.received_bytes === "number") {
          offset = body.received_bytes; // 409: o servidor indica de onde continuar
          break;
        }
        throw new HttpError(res.status, typeof body.detail === "string" ? body.detail : `${res.status}`);
      } catch (e) {
        const permanent = e instanceof HttpError && e.status >= 400 && e.status < 500;
        if (permanent || attempt >= 6) throw e;
        await sleep(Math.min(30_000, 1000 * 2 ** attempt));
      }
    }
    onBytes(offset);
  }
  const result = await request<T>("POST", `/api/uploads/${status.id}/complete`);
  storage("remove", key);
  return result;
}

async function uploadMany<T>(caseId: number, kind: "video" | "reference", files: File[],
  onProgress?: (fraction: number) => void): Promise<T[]> {
  const total = files.reduce((n, f) => n + f.size, 0) || 1;
  let done = 0;
  const results: T[] = [];
  for (const f of files) {
    results.push(await uploadChunked<T>(caseId, kind, f, (sent) => onProgress?.((done + sent) / total)));
    done += f.size;
  }
  return results;
}

export const api = {
  system: () => request<SystemInfo>("GET", "/api/system"),
  cases: () => request<Case[]>("GET", "/api/cases"),
  case: (id: number) => request<Case>("GET", `/api/cases/${id}`),
  createCase: (body: { name: string; description: string; analyst: string }) =>
    request<Case>("POST", "/api/cases", body),
  audit: (caseId: number) => request<AuditEvent[]>("GET", `/api/cases/${caseId}/audit`),

  references: (caseId: number) => request<ReferenceImage[]>("GET", `/api/cases/${caseId}/references`),
  uploadReferences: (caseId: number, files: File[], onProgress?: (f: number) => void) =>
    uploadMany<ReferenceImage>(caseId, "reference", files, onProgress),
  deleteReference: (id: number) => request<void>("DELETE", `/api/references/${id}`),
  referenceImageUrl: (id: number, face = false) => `/api/references/${id}/image${face ? "?face=true" : ""}`,

  videos: (caseId: number) => request<Video[]>("GET", `/api/cases/${caseId}/videos`),
  uploadVideos: (caseId: number, files: File[], onProgress?: (f: number) => void) =>
    uploadMany<Video>(caseId, "video", files, onProgress),
  videoUrl: (id: number) => `/api/videos/${id}/stream`,

  jobs: (caseId: number) => request<Job[]>("GET", `/api/cases/${caseId}/jobs`),
  job: (id: number) => request<Job>("GET", `/api/jobs/${id}`),
  createJob: (caseId: number, params: Partial<JobParams>, videoIds: number[], referenceIds: number[]) =>
    request<Job>("POST", `/api/cases/${caseId}/jobs`, {
      params,
      video_ids: videoIds,
      reference_ids: referenceIds,
    }),
  cancelJob: (id: number) => request<Job>("POST", `/api/jobs/${id}/cancel`),
  detections: (jobId: number) => request<Detection[]>("GET", `/api/jobs/${jobId}/detections`),
  segments: (jobId: number) => request<Segment[]>("GET", `/api/jobs/${jobId}/segments`),
  tracks: (jobId: number) => request<Track[]>("GET", `/api/jobs/${jobId}/tracks`),
  reviewTrack: (trackId: number, status: ReviewStatus, note = "") =>
    request<Track>("PATCH", `/api/tracks/${trackId}/review`, { status, note }),
  review: (detId: number, status: ReviewStatus, note = "") =>
    request<Detection>("PATCH", `/api/detections/${detId}/review`, { status, note }),
  detectionImageUrl: (id: number, kind: "crop" | "frame") => `/api/detections/${id}/${kind}`,
  exportUrl: (jobId: number, fmt: string) =>
    `/api/jobs/${jobId}/export/${fmt}?analyst=${encodeURIComponent(getAnalyst())}`,
};

export function formatTime(seconds: number): string {
  const ms = Math.round(seconds * 1000);
  const h = Math.floor(ms / 3_600_000);
  const m = Math.floor((ms % 3_600_000) / 60_000);
  const s = Math.floor((ms % 60_000) / 1000);
  const rest = ms % 1000;
  const pad = (n: number, w = 2) => String(n).padStart(w, "0");
  return `${pad(h)}:${pad(m)}:${pad(s)}.${pad(rest, 3)}`;
}

export function formatBytes(bytes: number): string {
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  let v = bytes;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(i ? 1 : 0)} ${units[i]}`;
}

export function formatDate(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso.endsWith("Z") || iso.includes("+") ? iso : iso + "Z");
  return d.toLocaleString("pt-PT");
}
