export type Role = "ADMIN" | "ANALYST" | "OPERATOR" | "VIEWER";
export type Review = "UNVERIFIED" | "CONFIRMED" | "REJECTED" | "NEEDS_REVIEW";
export type Severity = "INFO" | "WARNING" | "HIGH" | "CRITICAL";

export interface User {
  id: number; username: string; full_name: string; role: Role; is_active: boolean; is_demo: boolean;
  failed_attempts: number; locked_until: string | null; last_login_at: string | null; created_at: string | null;
}

export interface SessionInfo {
  user: User; permissions: string[];
  session: { ref: string; expires_at: string; idle_timeout_minutes: number };
  demo?: { state: string; message: string };
}

export interface StageState { state: "PENDING" | "RUNNING" | "DONE" | "FAILED" | "SKIPPED"; metrics: Record<string, any>; started?: string; finished?: string }

export interface MediaItem {
  id: number; name: string; kind: "VIDEO" | "IMAGE"; extension: string; size_bytes: number; sha256: string;
  width: number | null; height: number | null; fps: number | null; duration_s: number | null; frame_count: number | null;
  codec: string | null; gps: [number, number, number | null] | null; meta: Record<string, any>; status: string;
  error: string | null; encrypted: boolean; created_at: string | null;
}

export interface Mission {
  id: number; code: string; name: string; operator: string; drone: string; location_name: string;
  latitude: number; longitude: number; altitude_m: number; start_time: string | null; end_time: string | null;
  status: string; progress: number; current_stage: string | null; is_demo: boolean; error_message: string | null;
  created_at: string | null; updated_at: string | null;
  counts?: Record<string, number>;
  notes?: string | null; stage_states?: Record<string, StageState>; processing_metrics?: Record<string, any>;
  scene_summary?: Record<string, any>; zones?: Zone[]; mosaic_bounds?: [[number, number], [number, number]] | null;
  has_mosaic?: boolean; media?: MediaItem[]; queue?: { queued: number[]; running: number | null; depth: number };
}

export interface Zone { id: string; name: string; type: string; polygon: [number, number][] }

export interface Track {
  id: number; mission_id: number; mission_code?: string; track_no: number; label: string; cls: string;
  first_seen_s: number; last_seen_s: number; first_frame: number; last_frame: number; observations: number;
  duration_s: number; avg_confidence: number; max_confidence: number; distance_m: number; avg_speed_mps: number;
  status: string; review_status: Review; analyst_notes: string | null; reviewed_by: string | null; reviewed_at: string | null;
  trajectory?: [number, number, number][]; last_position?: [number, number] | null;
}

export interface AegisEvent {
  id: number; code: string; mission_id: number; mission_code: string | null; type: string; timestamp_s: number;
  frame_index: number; media_id: number | null; confidence: number; lat: number | null; lon: number | null;
  related_tracks: number[]; has_evidence: boolean; description: string; severity: Severity; details: Record<string, any>;
  review_status: Review; analyst_notes: string | null; reviewed_by: string | null; reviewed_at: string | null; created_at: string | null;
}

export interface FrameRow { id: number; media_id: number; frame_index: number; t: number; quality: number | null; scene: string | null; scene_confidence: number | null; detections: number; lat: number | null; lon: number | null }

export interface IntelItem {
  type: "intel"; kind: string; title: string; body: string; mission: string | null; severity: Severity; demo: boolean; ts: string;
  track?: number; event?: string;
}

// Categorical slots from the validated dark palette (identity); BUILDING/OTHER fold to neutral gray.
export const CLASS_COLORS: Record<string, string> = {
  PERSON: "#d95926", VEHICLE: "#3987e5", TRUCK: "#199e70", BUS: "#d55181", MOTORCYCLE: "#9085e9", AIRCRAFT: "#e66767", BOAT: "#008300", BUILDING: "#8c8c8c", ANIMAL: "#c98500", OTHER: "#a0a09a",
};

export const SEVERITY_COLORS: Record<string, string> = { INFO: "#5fb7c2", WARNING: "#d9a21b", HIGH: "#e0703a", CRITICAL: "#d4483f" };
