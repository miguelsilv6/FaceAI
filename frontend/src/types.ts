export interface Case {
  id: number;
  name: string;
  description: string;
  analyst: string;
  created_at: string;
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
}

export interface Video {
  id: number;
  case_id: number;
  filename: string;
  size_bytes: number;
  duration_s: number;
  fps: number;
  frame_count: number;
  width: number;
  height: number;
  codec: string;
  uploaded_at: string;
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

export interface JobParams {
  mode: JobMode;
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
  track_gap_s: number;
}

export type JobStatus = "queued" | "running" | "completed" | "failed" | "cancelled";

export interface Job {
  id: number;
  case_id: number;
  status: JobStatus;
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
  eta_s: number | null;
  message: string;
  created_by: string;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export type ReviewStatus = "pending" | "confirmed" | "rejected";

export interface Detection {
  id: number;
  job_id: number;
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
  action: string;
  details: Record<string, unknown>;
}

export interface SystemInfo {
  version: string;
  engine: string;
  environment: Record<string, unknown> & { device?: string };
  models: { name: string; default_threshold: number; description: string }[];
  detectors: string[];
  defaults: JobParams;
  upload_chunk_bytes: number;
}
