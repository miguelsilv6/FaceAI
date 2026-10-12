import type {
  AuditEvent, Case, Detection, Job, JobParams, ReferenceImage, ReviewStatus, Segment, SystemInfo, Video,
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

/** Upload multipart com progresso (XMLHttpRequest, porque o fetch não expõe progresso de envio). */
export function uploadFiles<T>(url: string, files: File[], onProgress?: (fraction: number) => void): Promise<T> {
  return new Promise((resolve, reject) => {
    const form = new FormData();
    files.forEach((f) => form.append("files", f, f.name));
    const xhr = new XMLHttpRequest();
    xhr.open("POST", url);
    Object.entries(headers()).forEach(([k, v]) => xhr.setRequestHeader(k, v));
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress?.(e.loaded / e.total);
    xhr.onload = () => {
      let body: unknown = null;
      try {
        body = JSON.parse(xhr.responseText);
      } catch {
        /* resposta vazia */
      }
      if (xhr.status >= 200 && xhr.status < 300) resolve(body as T);
      else {
        const detail = (body as { detail?: unknown } | null)?.detail;
        reject(new Error(typeof detail === "string" ? detail : `${xhr.status} ${xhr.statusText}`));
      }
    };
    xhr.onerror = () => reject(new Error("Falha de rede durante o envio."));
    xhr.send(form);
  });
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
    uploadFiles<ReferenceImage[]>(`/api/cases/${caseId}/references`, files, onProgress),
  deleteReference: (id: number) => request<void>("DELETE", `/api/references/${id}`),
  referenceImageUrl: (id: number, face = false) => `/api/references/${id}/image${face ? "?face=true" : ""}`,

  videos: (caseId: number) => request<Video[]>("GET", `/api/cases/${caseId}/videos`),
  uploadVideos: (caseId: number, files: File[], onProgress?: (f: number) => void) =>
    uploadFiles<Video[]>(`/api/cases/${caseId}/videos`, files, onProgress),
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
