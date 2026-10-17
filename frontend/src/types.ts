export interface Case {
  id: number;
  name: string;
  description: string;
  analyst: string;
  created_at: string;
  size_bytes?: number; // só na lista de processos
  alerts_new?: number; // só na lista de processos: alertas por rever
}

export interface ReferenceImage {
  id: number;
  case_id: number;
  filename: string;
  size_bytes: number;
  width: number;
  height: number;
  face_count: number;
  face_crop_path: string | null;
  valid: boolean;
  message: string;
  uploaded_at: string;
  person_label: string; // obsoleto (convertido em CasePerson)
  case_person_id: number | null;
  source_detection_id: number | null;
}

export interface Location {
  id: number;
  case_id: number;
  name: string;
  created_by: string;
  created_at: string;
  video_ids: number[];
  duration_s: number;
  size_bytes: number; // espaço em disco: vídeos originais + cópias de reprodução
  originals_bytes: number;
  playback_bytes: number;
}

export interface Video {
  id: number;
  case_id: number;
  location_id: number | null;
  camera_name: string;
  filename: string;
  sha256: string; // integridade e deteção de duplicados ("" em vídeos antigos até ser calculado)
  size_bytes: number;
  duration_s: number;
  fps: number;
  frame_count: number;
  width: number;
  height: number;
  codec: string;
  uploaded_at: string;
  verified_at?: string | null; // última verificação de integridade (SHA-256 recalculado)
  verified_ok?: boolean | null;
  playback_status: "" | "not_needed" | "pending" | "running" | "ready" | "failed" | "unavailable";
  playback_progress: number;
  playback_error: string;
  playback_format: "" | "mp4" | "webm";
  dvr_start: string | null;
  dvr_rate: number;
  clock_offset_s: number;
  time_source: "" | "manual" | "ocr";
  time_note: string;
  time_calibration: ClockCalibration | Record<string, never>;
  clock_auto_status: "" | "pending" | "running" | "done" | "failed" | "unavailable";
  clock_auto_message: string;
}

export interface VideoProbe {
  format_name?: string;
  codec_name?: string;
  profile?: string;
  pix_fmt?: string;
  width?: number;
  height?: number;
}

export interface ClockSample {
  video_s: number;
  text: string;
  dvr: string | null;
  inlier?: boolean;
  date_inferred?: boolean;
}

export interface ClockCalibration {
  roi?: number[];
  date_order?: string;
  samples?: ClockSample[];
  dvr_start?: string;
  rate?: number;
  max_residual_s?: number;
  inliers?: number;
  valid_readings?: number;
  error?: string;
  video_s?: number;
  dvr_time?: string;
}

export interface OcrTest {
  roi: number[] | null;
  text: string;
  dvr: string | null;
  auto: boolean;
}

export type JobMode = "match" | "discover";

export interface SearchedPerson {
  id: number;
  label: string;
  reference_ids: number[];
}

export interface JobParams {
  mode: JobMode;
  persons?: SearchedPerson[]; // modo procurar: pessoas (grupos de referências)
  locations?: { id: number | null; name: string; video_ids: number[] }[]; // locais abrangidos (nome na altura)
  model: string | null;
  detector: string | null;
  sample_fps: number;
  threshold: number | null;
  candidate_margin: number;
  min_face_size: number;
  min_confidence: number;
  min_quality: number;
  upscale: number;
  segment_gap_s: number;
  motion_filter: boolean;
  motion_threshold: number;
  keyframe_interval_s: number;
  cascade: boolean;
  fast_detector: string | null;
  tracking: boolean;
  clothing: boolean;
  watch_persons?: boolean; // alertas: comparar com as pessoas do processo
  watch_person_ids?: number[] | null;
  alert_min_locations?: number;
  clothing_window_h: number;
  reid_threshold: number | null;
  color_threshold: number;
  person_detector: string | null;
  min_body_height: number;
  track_gap_s: number;
}

export type JobStatus = "queued" | "running" | "completed" | "failed" | "cancelled";

export interface Job {
  id: number;
  case_id: number;
  status: JobStatus;
  priority: number; // 1 alta, 0 normal, -1 baixa
  held: boolean;
  params: JobParams;
  video_ids: number[];
  reference_ids: number[];
  environment: Record<string, unknown>;
  progress: number;
  current_video_id: number | null;
  frames_processed: number;
  faces_detected: number;
  matches: number;
  frames_sampled: number;
  frames_skipped_motion: number;
  frames_skipped_noface: number;
  tracks: number;
  persons: number;
  clothing_samples: number;
  clothing_summary: ClothingSummary | null;
  eta_s: number | null;
  message: string;
  partial?: boolean; // interrompida a meio: resultados incompletos
  created_by: string;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export type ReviewStatus = "pending" | "confirmed" | "rejected";

export interface ClothingSummary {
  body_tracks?: number;
  persons_detected?: number;
  samples?: number;
  known_negatives?: number;
  excluded_by_window?: number;
  clothing_matches?: number;
  gallery_colors?: Record<string, string>;
  calibration?: { threshold_used?: number; negatives?: number; negatives_min?: number; positives_median?: number };
  note?: string;
}

export type Evidence = "face" | "vestuário" | "face+vestuário";

export interface Detection {
  id: number;
  job_id: number;
  person_id: number | null; // várias pessoas por análise (modo procurar)
  video_id: number;
  reference_id: number | null;
  frame_index: number;
  timestamp_s: number;
  x: number;
  y: number;
  w: number;
  h: number;
  face_confidence: number;
  distance: number;
  similarity: number;
  is_match: boolean;
  track_id: number | null;
  kind: "face" | "body";
  colors: Record<string, string> | null;
  quality: number;
  sharpness: number;
  size_score: number;
  frontal: number | null;
  review_status: ReviewStatus;
  review_note: string;
  reviewed_by: string;
  reviewed_at: string | null;
}

export interface Track {
  id: number;
  job_id: number;
  video_id: number;
  reference_id: number | null;
  person_id: number | null;
  kind: "face" | "body";
  evidence: "" | "face+vestuário" | "vestuário";
  reid_distance: number | null;
  color_distance: number | null;
  face_track_id: number | null;
  colors: Record<string, string> | null;
  note: string;
  start_s: number;
  end_s: number;
  n_faces: number;
  n_saved: number;
  best_detection_id: number | null;
  best_distance: number;
  track_distance: number;
  best_quality: number;
  is_match: boolean;
  review_status: ReviewStatus;
  review_note: string;
  reviewed_by: string;
  reviewed_at: string | null;
}

export interface Segment {
  video_id: number;
  start_s: number;
  end_s: number;
  duration_s: number;
  best_distance: number;
  best_similarity: number;
  best_detection_id: number;
  detection_ids: number[];
  count: number;
  person_id?: number | null;
  evidence?: Evidence;
}

/** Modo descoberta: pessoa distinta = grupo de tracks com faces semelhantes. */
export interface Person {
  person_id: number;
  track_ids: number[];
  n_tracks: number;
  n_faces: number;
  n_saved: number;
  video_ids: number[];
  first: { video_id: number; start_s: number };
  duration_s: number;
  best_detection_id: number | null;
  best_quality: number;
  max_distance: number;
  review_status: ReviewStatus | "mixed";
}

export interface AuditEvent {
  id: number;
  timestamp: string;
  case_id: number | null;
  actor: string;
  ip?: string;
  action: string;
  details: Record<string, unknown>;
  hash?: string;
}

export interface AuditChainCheck {
  ok: boolean;
  events: number;
  unsealed: number;
  broken_at: number | null;
  reason: string;
  last_hash: string;
  case_events: number;
}

export interface EvidenceCheck {
  running: boolean;
  done?: number;
  total?: number;
  current?: string;
  ok?: number;
  failed?: { video_id: number; filename: string; reason: string }[];
  finished_at?: string;
  error?: string;
}

export interface SystemInfo {
  version: string;
  engine: string;
  environment: Record<string, unknown> & { device?: string };
  models: { name: string; default_threshold: number; description: string }[];
  detectors: string[];
  person_detectors: string[];
  defaults: JobParams;
  upload_chunk_bytes: number;
}

export type UserRole = "admin" | "analyst";
export type AuthStage = "mfa_setup" | "mfa" | "password" | "ok";

export interface Me {
  id: number;
  username: string;
  display_name: string;
  role: UserRole;
  totp_enabled: boolean;
  must_change_password: boolean;
  recovery_codes_left: number;
  last_login_at: string | null;
  password_changed_at: string | null;
}

export interface AuthState {
  stage: AuthStage;
  csrf_token: string;
  user: Me;
  idle_minutes: number;
  require_mfa: boolean;
  recovery_codes?: string[];
}

export interface MfaSetup {
  otpauth_uri: string;
  qr_svg: string;
  secret: string;
}

export interface UserSessionInfo {
  id: string;
  current: boolean;
  created_at: string;
  last_seen_at: string;
  expires_at: string;
  ip: string;
  user_agent: string;
}

export interface UserAccount {
  id: number;
  username: string;
  display_name: string;
  role: UserRole;
  active: boolean;
  totp_enabled: boolean;
  must_change_password: boolean;
  locked: boolean;
  created_at: string;
  last_login_at: string | null;
  cases: number;
}

export interface CaseMember {
  user_id: number;
  username: string;
  display_name: string;
  role: "owner" | "member";
  active: boolean;
  added_by: string;
  added_at: string;
}

export interface ActivityJob {
  id: number;
  case_id: number;
  case_name: string;
  status: "queued" | "running";
  mode: string;
  progress: number;
  eta_s: number | null;
  message: string;
  created_by: string;
  started_at: string | null;
  priority: number;
  held: boolean;
  queue_position: number | null;
}

export interface ActivityConversion {
  video_id: number;
  case_id: number;
  case_name: string;
  filename: string;
  status: string;
  progress: number;
}

export interface ActivityUpload {
  id: string;
  case_id: number;
  case_name: string;
  kind: string;
  filename: string;
  received_bytes: number;
  size_bytes: number;
  created_by: string;
}

export interface DiskStatus {
  level: "ok" | "warning" | "critical";
  percent: number;
  free: number;
  total?: number; // só para administradores
  used?: number;
  warn_percent?: number;
  critical_percent?: number;
}

export interface Activity {
  disk?: DiskStatus;
  jobs: ActivityJob[];
  conversions: ActivityConversion[];
  uploads: ActivityUpload[];
}

export interface SimilarTrack {
  case_id: number;
  case_name: string;
  job_id: number;
  job_mode: string;
  track_id: number;
  person_id: number | null;
  video_id: number;
  video_filename: string;
  location_id: number | null;
  location_name: string;
  camera_name: string;
  camera_label: string;
  start_s: number;
  end_s: number;
  real_start: string | null;
  distance: number;
  similarity: number;
  best_detection_id: number | null;
  review_status: ReviewStatus;
  is_match: boolean;
  threshold?: number | null;
}

export interface SimilarResult {
  model: string;
  threshold: number;
  results: SimilarTrack[];
  jobs_searched: number;
  jobs_not_indexed: number;
  jobs_other_model: number;
  jobs_not_searched?: number;
  warnings?: string[];
}

export interface RouteSegment {
  job_id: number;
  job_mode: string;
  person_id: number | null;
  person_label: string;
  video_id: number;
  video_filename: string;
  location_id: number | null;
  location_name: string;
  camera_name: string;
  camera_label: string;
  start_s: number;
  end_s: number;
  real_start: string | null;
  real_end: string | null;
  evidence: string;
  best_detection_id: number | null;
  best_similarity: number | null;
}

export interface CaseRoute {
  segments: RouteSegment[];
  unsynced: RouteSegment[];
  jobs: number[];
  videos: { id: number; filename: string; location_id: number | null; location_name: string; camera_name: string;
    camera_label: string; synced: boolean; appearances: number }[];
  locations: { id: number; name: string }[];
}

export interface CasePerson {
  id: number;
  case_id: number;
  name: string;
  notes: string;
  created_by: string;
  created_at: string;
  reference_ids: number[];
  valid_references: number;
}

export interface CaseDashboard {
  jobs_considered: number[];
  material: { locations: number; videos: number; duration_s: number; videos_synced: number; references: number; references_valid: number;
    persons: number; size_bytes: number };
  jobs: { total: number; by_status: Partial<Record<JobStatus, number>> };
  review: { matches: number; confirmed: number; rejected: number; pending: number; clothing: number; tracks: number;
    tracks_confirmed: number; tracks_rejected: number; tracks_pending: number };
  per_location: { location_id: number; name: string; cameras: number; matches: number; confirmed: number;
    rejected: number; pending: number; clothing: number }[];
  per_camera: { video_id: number; filename: string; location_id: number | null; location_name: string;
    camera_name: string; camera_label: string; synced: boolean; matches: number; confirmed: number; rejected: number;
    pending: number; clothing: number; first: string | null; last: string | null; first_s: number | null;
    last_s: number | null; job_ids: number[] }[];
  per_hour: { hour: string; matches: number }[];
  unsynced_matches: number;
  per_person: { label: string; matches: number; confirmed: number }[];
  pending_by_job: { job_id: number; pending: number }[];
  discover: { jobs: number; faces: number; persons: number };
  alerts?: { new: number; new_highlighted: number; total: number };
  recent: { id: number; timestamp: string; actor: string; action: string }[];
}

/** Deteção confirmada (frame validado), como aparece no separador "Resultados" do caso. */
export interface ValidatedItem {
  type: "detection" | "saved_frame";
  detection_id: number | null; // deteção confirmada
  saved_frame_id: number | null; // frame guardado no leitor
  job_id: number;
  job_mode: string;
  video_id: number;
  video_filename: string;
  location_id: number | null;
  location_name: string;
  camera_name: string;
  camera_label: string;
  frame_index: number;
  timestamp_s: number;
  real_time: string | null;
  kind: string;
  evidence: string;
  person_id: number | null;
  person_label: string;
  similarity: number | null;
  reviewed_by: string;
  reviewed_at: string | null;
  review_note: string;
}

/** Alerta: uma pessoa do processo pode ter aparecido na análise (descoberta) ou aparece em vários locais. */
export interface PersonAlert {
  id: number;
  case_id: number;
  job_id: number;
  case_person_id: number;
  case_person_name: string;
  reference_id: number | null;
  reference_has_crop: boolean;
  discovered_person_id: number | null;
  distance: number;
  similarity: number;
  location_ids: number[];
  location_names: string[];
  video_ids: number[];
  n_tracks: number;
  first_real: string | null;
  last_real: string | null;
  best_detection_id: number | null;
  highlighted: boolean;
  status: "new" | "confirmed" | "dismissed";
  note: string;
  reviewed_by: string;
  reviewed_at: string | null;
}

/** Importação em bloco a partir da pasta do servidor (FACEAI_IMPORT_DIR): cada pasta → um local. */
export interface ServerImportGroup {
  path: string;
  location_name: string;
  existing_location_id: number | null;
  videos: { name: string; path: string; size: number; subfolder: string }[];
}

export interface ServerImportScan {
  enabled: boolean;
  root?: string;
  path?: string;
  parent?: string | null;
  dirs?: { name: string; path: string }[];
  plan?: ServerImportGroup[];
  total_bytes?: number;
  truncated?: boolean;
}

export interface ServerImportState {
  running: boolean;
  done?: number;
  total?: number;
  bytes_done?: number;
  bytes_total?: number;
  current?: string;
  imported?: number;
  duplicates?: { path: string; message: string }[];
  errors?: { path: string; message: string }[];
  locations_created?: string[];
  finished_at?: string;
  error?: string;
}
