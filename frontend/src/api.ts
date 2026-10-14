import type {
  Activity, AuditEvent, AuthState, Case, CaseMember, Detection, MfaSetup, UserAccount, UserRole, UserSessionInfo, Job, JobParams, OcrTest, Person, ReferenceImage, ReviewStatus, Segment, SystemInfo,
  Track, Video, VideoProbe,
} from "./types";

// Sessão: cookie HttpOnly (enviado automaticamente, também em <img>/<video>/downloads). Os pedidos que alteram
// dados levam o token CSRF da sessão no cabeçalho X-CSRF-Token.
let csrfToken = "";

export function setCsrfToken(token: string): void {
  csrfToken = token;
}

/** Evento disparado quando a API responde 401 (sessão expirada ou terminada noutro lado). */
export const UNAUTHORIZED_EVENT = "faceai:unauthorized";

function headers(extra?: Record<string, string>): Record<string, string> {
  const h: Record<string, string> = { ...extra };
  if (csrfToken) h["X-CSRF-Token"] = csrfToken;
  return h;
}

/** Erro da API; `detail` guarda o corpo estruturado quando existe (ex.: amostras de uma calibração falhada). */
export class ApiError extends Error {
  constructor(message: string, public detail?: unknown) {
    super(message);
  }
}

async function parseError(res: Response): Promise<ApiError> {
  try {
    const body = await res.json();
    if (typeof body.detail === "string") return new ApiError(body.detail);
    if (Array.isArray(body.detail)) return new ApiError(body.detail.map((d: { msg: string }) => d.msg).join("; "));
    if (body.detail && typeof body.detail.message === "string") return new ApiError(body.detail.message, body.detail);
  } catch {
    /* sem corpo JSON */
  }
  return new ApiError(`${res.status} ${res.statusText}`);
}

async function request<T>(method: string, url: string, body?: unknown): Promise<T> {
  const res = await fetch(url, {
    method,
    headers: headers(body !== undefined ? { "Content-Type": "application/json" } : undefined),
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (res.status === 401 && !url.startsWith("/api/auth/")) window.dispatchEvent(new Event(UNAUTHORIZED_EVENT));
  if (!res.ok) throw await parseError(res);
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
      if (status.completed) {
        // A resposta de "concluir" perdeu-se da última vez: obtém o resultado sem reenviar o ficheiro.
        const result = await request<T>("POST", `/api/uploads/${status.id}/complete`);
        storage("remove", key);
        onBytes(file.size);
        return result;
      }
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

export const authApi = {
  status: () => request<{ setup_required: boolean }>("GET", "/api/auth/status"),
  me: () => request<AuthState>("GET", "/api/auth/me"),
  login: (username: string, password: string) => request<AuthState>("POST", "/api/auth/login", { username, password }),
  mfaSetup: () => request<MfaSetup>("POST", "/api/auth/mfa/setup"),
  mfaVerify: (code: string) => request<AuthState>("POST", "/api/auth/mfa/verify", { code }),
  mfaEnable: (code: string) => request<{ recovery_codes: string[] }>("POST", "/api/auth/mfa/enable", { code }),
  mfaDisable: (password: string, code: string) =>
    request<AuthState>("POST", "/api/auth/mfa/disable", { password, code }),
  changePassword: (current_password: string, new_password: string) =>
    request<AuthState>("POST", "/api/auth/password", { current_password, new_password }),
  regenerateRecoveryCodes: (password: string) =>
    request<{ recovery_codes: string[] }>("POST", "/api/auth/mfa/recovery-codes", { password }),
  logout: () => request<void>("POST", "/api/auth/logout"),
  sessions: () => request<UserSessionInfo[]>("GET", "/api/auth/sessions"),
  endSession: (id: string) => request<void>("DELETE", `/api/auth/sessions/${id}`),
  endOtherSessions: () => request<void>("DELETE", "/api/auth/sessions"),
};

export const usersApi = {
  list: () => request<UserAccount[]>("GET", "/api/users"),
  create: (body: { username: string; display_name: string; role: UserRole; password: string }) =>
    request<UserAccount>("POST", "/api/users", body),
  update: (id: number, body: { display_name?: string; role?: UserRole; active?: boolean }) =>
    request<UserAccount>("PATCH", `/api/users/${id}`, body),
  resetPassword: (id: number, password: string) =>
    request<UserAccount>("POST", `/api/users/${id}/reset-password`, { password }),
  resetMfa: (id: number) => request<UserAccount>("POST", `/api/users/${id}/reset-mfa`),
  unlock: (id: number) => request<UserAccount>("POST", `/api/users/${id}/unlock`),
  systemAudit: () => request<AuditEvent[]>("GET", "/api/audit/system"),
};

export const api = {
  members: (caseId: number) => request<CaseMember[]>("GET", `/api/cases/${caseId}/members`),
  addMember: (caseId: number, username: string, role: "owner" | "member") =>
    request<CaseMember[]>("POST", `/api/cases/${caseId}/members`, { username, role }),
  removeMember: (caseId: number, userId: number) =>
    request<void>("DELETE", `/api/cases/${caseId}/members/${userId}`),
  system: () => request<SystemInfo>("GET", "/api/system"),
  cases: () => request<Case[]>("GET", "/api/cases"),
  activity: () => request<Activity>("GET", "/api/activity"),
  validatedSummary: (caseId: number) =>
    request<{ detections: number; frames: number }>("GET", `/api/cases/${caseId}/frames/validated`),
  validatedFramesUrl: (scope: "jobs" | "cases", id: number) => `/api/${scope}/${id}/frames/validated.zip`,
  case: (id: number) => request<Case>("GET", `/api/cases/${id}`),
  createCase: (body: { name: string; description: string }) =>
    request<Case>("POST", "/api/cases", body),
  deleteCase: (id: number, reason: string) =>
    request<void>("DELETE", `/api/cases/${id}`, { confirm_case_id: id, reason }),
  createCaseCopies: (caseId: number, format: "mp4" | "webm" = browserPlaybackFormat()) =>
    request<Video[]>("POST", `/api/cases/${caseId}/playback?format=${format}`),
  updateCase: (id: number, body: { name?: string; description?: string }) =>
    request<Case>("PATCH", `/api/cases/${id}`, body),
  audit: (caseId: number) => request<AuditEvent[]>("GET", `/api/cases/${caseId}/audit`),

  references: (caseId: number) => request<ReferenceImage[]>("GET", `/api/cases/${caseId}/references`),
  uploadReferences: (caseId: number, files: File[], onProgress?: (f: number) => void) =>
    uploadMany<ReferenceImage>(caseId, "reference", files, onProgress),
  deleteReference: (id: number) => request<void>("DELETE", `/api/references/${id}`),
  referenceImageUrl: (id: number, face = false) => `/api/references/${id}/image${face ? "?face=true" : ""}`,

  videos: (caseId: number) => request<Video[]>("GET", `/api/cases/${caseId}/videos`),
  uploadVideos: (caseId: number, files: File[], onProgress?: (f: number) => void) =>
    uploadMany<Video>(caseId, "video", files, onProgress),
  /** O parâmetro muda quando a cópia de reprodução muda, para o browser não reutilizar a versão anterior em cache. */
  videoUrl: (v: Pick<Video, "id" | "playback_status" | "playback_format">) =>
    `/api/videos/${v.id}/stream?v=${v.playback_status === "ready" ? `copy-${v.playback_format || "mp4"}` : "original"}`,
  createPlaybackCopy: (id: number, format: "mp4" | "webm" = browserPlaybackFormat()) =>
    request<Video>("POST", `/api/videos/${id}/playback?format=${format}`),
  probeVideo: (id: number) => request<VideoProbe>("GET", `/api/videos/${id}/probe`),
  videoFrameUrl: (id: number, t: number) => `/api/videos/${id}/frame?t=${t.toFixed(2)}`,
  clockOcrTest: (id: number, body: { roi: number[] | null; t: number; date_order: string }) =>
    request<OcrTest>("POST", `/api/videos/${id}/clock/ocr-test`, body),
  clockOcr: (id: number, body: { roi: number[] | null; samples: number; date_order: string;
    clock_offset_s: number; note: string }) => request<Video>("POST", `/api/videos/${id}/clock/ocr`, body),
  clockManual: (id: number, body: { video_s: number; dvr_time: string; clock_offset_s: number; note: string }) =>
    request<Video>("PUT", `/api/videos/${id}/clock`, body),
  clockOffset: (id: number, clock_offset_s: number, note?: string) =>
    request<Video>("PATCH", `/api/videos/${id}/clock/offset`, { clock_offset_s, note }),
  clockReset: (id: number) => request<Video>("DELETE", `/api/videos/${id}/clock`),

  jobs: (caseId: number) => request<Job[]>("GET", `/api/cases/${caseId}/jobs`),
  job: (id: number) => request<Job>("GET", `/api/jobs/${id}`),
  createJob: (caseId: number, params: Partial<JobParams>, videoIds: number[], referenceIds: number[]) =>
    request<Job>("POST", `/api/cases/${caseId}/jobs`, {
      params,
      video_ids: videoIds,
      reference_ids: referenceIds,
    }),
  cancelJob: (id: number) => request<Job>("POST", `/api/jobs/${id}/cancel`),
  deleteJob: (id: number, reason: string) =>
    request<void>("DELETE", `/api/jobs/${id}`, { confirm_job_id: id, reason }),
  detections: (jobId: number) => request<Detection[]>("GET", `/api/jobs/${jobId}/detections`),
  segments: (jobId: number, personId?: number | null) => request<Segment[]>("GET",
    `/api/jobs/${jobId}/segments${personId != null ? `?person_id=${personId}` : ""}`),
  tracks: (jobId: number) => request<Track[]>("GET", `/api/jobs/${jobId}/tracks`),
  reviewTrack: (trackId: number, status: ReviewStatus, note = "") =>
    request<Track>("PATCH", `/api/tracks/${trackId}/review`, { status, note }),
  review: (detId: number, status: ReviewStatus, note = "") =>
    request<Detection>("PATCH", `/api/detections/${detId}/review`, { status, note }),
  detectionImageUrl: (id: number, kind: "crop" | "frame") => `/api/detections/${id}/${kind}`,
  /** Frame completo: "annotated" (JPEG com a caixa) ou "original" (PNG extraído do vídeo, sem marcações). */
  detectionFrameUrl: (id: number, variant: "annotated" | "original", download = false) => {
    const base = variant === "original" ? `/api/detections/${id}/frame/original` : `/api/detections/${id}/frame`;
    return download ? `${base}?download=1` : base;
  },
  detectionAsReference: (id: number) => request<ReferenceImage>("POST", `/api/detections/${id}/as-reference`),
  persons: (jobId: number) => request<Person[]>("GET", `/api/jobs/${jobId}/persons`),
  reviewPerson: (jobId: number, personId: number, status: ReviewStatus, note = "") =>
    request<Person>("PATCH", `/api/jobs/${jobId}/persons/${personId}/review`, { status, note }),
  mergePersons: (jobId: number, personIds: number[]) =>
    request<Person[]>("POST", `/api/jobs/${jobId}/persons/merge`, { person_ids: personIds }),
  moveTrack: (trackId: number, personId: number | null) =>
    request<Track>("PATCH", `/api/tracks/${trackId}/person`, { person_id: personId }),
  exportUrl: (jobId: number, fmt: string) =>
    `/api/jobs/${jobId}/export/${fmt}`,
};

/** Codecs que este browser diz reproduzir (alguns Firefox/Chromium em Linux não têm H.264). */
export function browserCodecs(): { h264: boolean; vp9: boolean } {
  const v = document.createElement("video");
  return {
    h264: v.canPlayType('video/mp4; codecs="avc1.640028"') !== "",
    vp9: v.canPlayType('video/webm; codecs="vp9"') !== "",
  };
}

/** Formato da cópia de reprodução adequado a este browser: H.264/MP4 se o suportar, senão VP9/WebM. */
export function browserPlaybackFormat(): "mp4" | "webm" {
  return browserCodecs().h264 ? "mp4" : "webm";
}

export function formatTime(seconds: number): string {
  const ms = Math.round(seconds * 1000);
  const h = Math.floor(ms / 3_600_000);
  const m = Math.floor((ms % 3_600_000) / 60_000);
  const s = Math.floor((ms % 60_000) / 1000);
  const rest = ms % 1000;
  const pad = (n: number, w = 2) => String(n).padStart(w, "0");
  return `${pad(h)}:${pad(m)}:${pad(s)}.${pad(rest, 3)}`;
}

/** Duração aproximada legível: "45 s", "3 min 20 s", "1 h 05 min". */
export function formatDuration(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s} s`;
  if (s < 3600) return `${Math.floor(s / 60)} min ${String(s % 60).padStart(2, "0")} s`;
  return `${Math.floor(s / 3600)} h ${String(Math.floor((s % 3600) / 60)).padStart(2, "0")} min`;
}

/** Tempo restante de uma análise a decorrer, com a hora prevista de fim (hora local do browser). */
export function formatEta(etaS: number | null | undefined): string {
  if (etaS == null) return "a calcular o tempo restante…";
  const end = new Date(Date.now() + etaS * 1000).toLocaleTimeString("pt-PT", { hour: "2-digit", minute: "2-digit" });
  return `faltam ≈ ${formatDuration(etaS)} (fim ≈ ${end})`;
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

/**
 * Hora real de um instante do vídeo: relógio do DVR (início + t × taxa) + desvio. A hora do DVR é "de parede"
 * (sem fuso), por isso o cálculo é feito em UTC para o browser não aplicar o fuso local.
 */
export function realTime(video: Pick<Video, "dvr_start" | "dvr_rate" | "clock_offset_s"> | null | undefined,
  seconds: number): string {
  if (!video?.dvr_start) return "";
  const m = video.dvr_start.match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d+))?/);
  if (!m) return "";
  const base = Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +m[6], m[7] ? +m[7].padEnd(3, "0").slice(0, 3) : 0);
  const d = new Date(base + (seconds * (video.dvr_rate || 1) + (video.clock_offset_s || 0)) * 1000);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getUTCFullYear()}-${p(d.getUTCMonth() + 1)}-${p(d.getUTCDate())} `
    + `${p(d.getUTCHours())}:${p(d.getUTCMinutes())}:${p(d.getUTCSeconds())}`;
}
