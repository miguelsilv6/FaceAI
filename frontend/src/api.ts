import type {
  CaseDashboard,
  Activity, AuditChainCheck, EvidenceCheck, CasePerson, Location, PersonAlert, ValidatedItem, CaseRoute, SimilarResult, AuditEvent, AuthState, Case, CaseMember, Detection, MfaSetup, UserAccount, UserRole, UserSessionInfo, Job, JobParams, OcrTest, Person, ReferenceImage, ReviewStatus, Segment, SystemInfo,
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
  /** duplicateOf: vídeo do caso igual ao enviado (id) ou "upload" (o mesmo ficheiro já está a ser importado). */
  constructor(message: string, public detail?: unknown, public duplicateOf?: string | null) {
    super(message);
  }
}

async function parseError(res: Response): Promise<ApiError> {
  const dup = res.headers.get("X-FaceAI-Duplicate-Of");
  try {
    const body = await res.json();
    if (typeof body.detail === "string") return new ApiError(body.detail, undefined, dup);
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
  onBytes: (sent: number) => void, locationId?: number | null): Promise<T> {
  const loc = locationId != null ? `.l${locationId}` : "";
  const key = `faceai.upload.${caseId}.${kind}${loc}.${file.name}.${file.size}.${file.lastModified}`;
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
      { kind, filename: file.name, size_bytes: file.size, location_id: locationId ?? null,
        quick_hash: kind === "video" ? await quickHash(file).catch(() => null) : null });
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

/**
 * Impressão rápida do ficheiro (igual à do servidor): SHA-256(tamanho em 8 bytes LE ‖ 1.º MiB ‖ último MiB).
 * Permite ao servidor recusar um vídeo já importado sem o enviar. Sem crypto.subtle (HTTP fora de localhost)
 * devolve null — o SHA-256 completo no servidor continua a impedir o duplicado no fim do envio.
 */
export async function quickHash(file: File): Promise<string | null> {
  if (!globalThis.crypto?.subtle) return null;
  const MIB = 1024 * 1024;
  const size = new Uint8Array(8);
  new DataView(size.buffer).setBigUint64(0, BigInt(file.size), true);
  const head = new Uint8Array(await file.slice(0, Math.min(MIB, file.size)).arrayBuffer());
  const tail = new Uint8Array(await file.slice(Math.max(0, file.size - MIB)).arrayBuffer());
  const buf = new Uint8Array(8 + head.length + tail.length);
  buf.set(size, 0);
  buf.set(head, 8);
  buf.set(tail, 8 + head.length);
  const digest = await crypto.subtle.digest("SHA-256", buf);
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

/**
 * Vídeos: primeiro valida o lote inteiro (impressão rápida de cada ficheiro → o servidor diz quais já existem no
 * processo e quais se repetem dentro do lote) e só depois envia, um a um, os que não existem. O SHA-256 completo
 * no servidor continua a confirmar no fim de cada envio (e cobre browsers sem crypto.subtle).
 */
async function uploadVideoFiles(caseId: number, files: File[], onProgress?: (fraction: number) => void,
  locationId?: number | null): Promise<{ created: Video[]; duplicates: { file: string; message: string }[] }> {
  const duplicates: { file: string; message: string }[] = [];
  let todo = files;
  const hashes = await Promise.all(files.map((f) => quickHash(f).catch(() => null)));
  if (hashes.every((h) => h)) {
    const check = await request<{ name: string; duplicate_of: number | null; same_as?: string; message?: string }[]>(
      "POST", `/api/cases/${caseId}/videos/check`,
      { files: files.map((f, i) => ({ name: f.name, size: f.size, quick_hash: hashes[i] })) });
    todo = files.filter((f, i) => {
      const r = check[i];
      if (r && (r.duplicate_of != null || r.same_as)) {
        duplicates.push({ file: f.name, message: r.message ?? `${f.name}: já existe.` });
        return false;
      }
      return true;
    });
  }
  const total = todo.reduce((n, f) => n + f.size, 0) || 1;
  let done = 0;
  const created: Video[] = [];
  for (const f of todo) {
    try {
      created.push(await uploadChunked<Video>(caseId, "video", f, (sent) => onProgress?.((done + sent) / total),
        locationId));
    } catch (e) {
      if (e instanceof ApiError && e.duplicateOf) duplicates.push({ file: f.name, message: e.message });
      else throw e;
    }
    done += f.size;
  }
  return { created, duplicates };
}

async function uploadMany<T>(caseId: number, kind: "video" | "reference", files: File[],
  onProgress?: (fraction: number) => void, locationId?: number | null): Promise<T[]> {
  const total = files.reduce((n, f) => n + f.size, 0) || 1;
  let done = 0;
  const results: T[] = [];
  for (const f of files) {
    results.push(await uploadChunked<T>(caseId, kind, f, (sent) => onProgress?.((done + sent) / total), locationId));
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
  similarToTrack: (trackId: number, scope: "all" | "case", threshold?: number) =>
    request<SimilarResult>("GET", `/api/tracks/${trackId}/similar?scope=${scope}${threshold != null ? `&threshold=${threshold}` : ""}`),
  similarToPerson: (jobId: number, personId: number, scope: "all" | "case", threshold?: number) =>
    request<SimilarResult>("GET",
      `/api/jobs/${jobId}/persons/${personId}/similar?scope=${scope}${threshold != null ? `&threshold=${threshold}` : ""}`),
  searchCasePerson: (id: number, scope: "all" | "case", threshold?: number) =>
    request<SimilarResult>("GET", `/api/case-persons/${id}/search?scope=${scope}${threshold != null ? `&threshold=${threshold}` : ""}`),
  searchReference: (id: number, scope: "all" | "case", threshold?: number) =>
    request<SimilarResult>("GET", `/api/references/${id}/search?scope=${scope}${threshold != null ? `&threshold=${threshold}` : ""}`),
  caseDashboard: (caseId: number, jobIds: number[]) =>
    request<CaseDashboard>("GET", `/api/cases/${caseId}/dashboard${jobIds.length ? `?job_ids=${jobIds.join(",")}` : ""}`),
  route: (caseId: number, jobIds: number[], only: "matches" | "confirmed") =>
    request<CaseRoute>("GET", `/api/cases/${caseId}/route?only=${only}${jobIds.length ? `&job_ids=${jobIds.join(",")}` : ""}`),
  activity: () => request<Activity>("GET", "/api/activity"),
  validatedSummary: (caseId: number) =>
    request<{ detections: number; frames: number; saved_frames: number }>("GET", `/api/cases/${caseId}/frames/validated`),
  /** Com sel, só os itens indicados (deteções confirmadas e frames guardados). */
  validatedFramesUrl: (scope: "jobs" | "cases", id: number, sel?: { detections: number[]; frames: number[] }) =>
    `/api/${scope}/${id}/frames/validated.zip`
    + (sel ? `?detection_ids=${sel.detections.join(",")}&frame_ids=${sel.frames.join(",")}` : ""),
  jobAlerts: (jobId: number) => request<PersonAlert[]>("GET", `/api/jobs/${jobId}/alerts`),
  caseAlerts: (caseId: number, status?: string) =>
    request<PersonAlert[]>("GET", `/api/cases/${caseId}/alerts${status ? `?status=${status}` : ""}`),
  reviewAlert: (id: number, status: PersonAlert["status"], note = "") =>
    request<PersonAlert>("PATCH", `/api/alerts/${id}`, { status, note }),
  saveFrame: (jobId: number, videoId: number, t: number, note = "") =>
    request<{ id: number; frame_index: number; timestamp_s: number }>("POST", `/api/jobs/${jobId}/saved-frames`,
      { video_id: videoId, t, note }),
  savedFrameUrl: (id: number, download = false) => `/api/saved-frames/${id}/image${download ? "?download=1" : ""}`,
  deleteSavedFrame: (id: number) => request<void>("DELETE", `/api/saved-frames/${id}`),
  validatedItems: (caseId: number) => request<ValidatedItem[]>("GET", `/api/cases/${caseId}/frames/validated/items`),
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
  uploadVideos: (caseId: number, files: File[], onProgress?: (f: number) => void, locationId?: number | null) =>
    uploadVideoFiles(caseId, files, onProgress, locationId),
  updateVideo: (id: number, body: { location_id?: number; camera_name?: string }) =>
    request<Video>("PATCH", `/api/videos/${id}`, body),
  locations: (caseId: number) => request<Location[]>("GET", `/api/cases/${caseId}/locations`),
  createLocation: (caseId: number, name: string) =>
    request<Location>("POST", `/api/cases/${caseId}/locations`, { name }),
  renameLocation: (id: number, name: string) => request<Location>("PATCH", `/api/locations/${id}`, { name }),
  deleteLocation: (id: number) => request<void>("DELETE", `/api/locations/${id}`),
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
  /** Com locationIds, a análise abrange todas as câmaras desses locais (os videoIds são ignorados). */
  createJob: (caseId: number, params: Partial<JobParams>, videoIds: number[], referenceIds: number[],
    locationIds?: number[]) =>
    request<Job>("POST", `/api/cases/${caseId}/jobs`, {
      params,
      ...(locationIds ? { location_ids: locationIds } : { video_ids: videoIds }),
      reference_ids: referenceIds,
    }),
  cancelJob: (id: number) => request<Job>("POST", `/api/jobs/${id}/cancel`),
  retryJob: (id: number) => request<Job>("POST", `/api/jobs/${id}/retry`),
  verifyAudit: (caseId: number) => request<AuditChainCheck>("GET", `/api/cases/${caseId}/audit/verify`),
  verifyEvidence: (caseId: number) => request<EvidenceCheck>("POST", `/api/cases/${caseId}/evidence/verify`),
  evidenceStatus: (caseId: number) => request<EvidenceCheck>("GET", `/api/cases/${caseId}/evidence/verify`),
  updateQueue: (id: number, body: { priority?: number; held?: boolean }) =>
    request<Job>("PATCH", `/api/jobs/${id}/queue`, body),
  deleteVideo: (id: number, reason: string) =>
    request<void>("DELETE", `/api/videos/${id}`, { confirm_video_id: id, reason }),
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
  enhancedUrl: (id: number, o: { src: "crop" | "original"; ops: string[]; upscale: number; gamma: number }, download = false) =>
    `/api/detections/${id}/enhanced?src=${o.src}&ops=${o.ops.join(",")}&upscale=${o.upscale}&gamma=${o.gamma}`
    + (download ? "&download=1" : ""),
  /** Frame completo: "annotated" (JPEG com a caixa) ou "original" (PNG extraído do vídeo, sem marcações). */
  detectionFrameUrl: (id: number, variant: "annotated" | "original", download = false) => {
    const base = variant === "original" ? `/api/detections/${id}/frame/original` : `/api/detections/${id}/frame`;
    return download ? `${base}?download=1` : base;
  },
  detectionAsReference: (id: number, casePersonId?: number | null) =>
    request<ReferenceImage>("POST", `/api/detections/${id}/as-reference${casePersonId != null ? `?case_person_id=${casePersonId}` : ""}`),
  casePersons: (caseId: number) => request<CasePerson[]>("GET", `/api/cases/${caseId}/persons`),
  createPerson: (caseId: number, name: string, notes = "") =>
    request<CasePerson>("POST", `/api/cases/${caseId}/persons`, { name, notes }),
  updatePerson: (id: number, body: { name?: string; notes?: string }) =>
    request<CasePerson>("PATCH", `/api/case-persons/${id}`, body),
  deletePerson: (id: number) => request<void>("DELETE", `/api/case-persons/${id}`),
  setReferencePerson: (refId: number, casePersonId: number | null) =>
    request<ReferenceImage>("PATCH", `/api/references/${refId}`, { case_person_id: casePersonId }),
  removeFromPerson: (refId: number) =>
    request<{ action: "deleted" | "detached" }>("POST", `/api/references/${refId}/remove-from-person`),
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
