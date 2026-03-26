export type VideoStatus = 'uploaded' | 'processing' | 'completed' | 'failed';
export type PipelineStageState = 'pending' | 'processing' | 'completed';
export type SystemConfigKey =
  | 'min_similarity'
  | 'min_margin'
  | 'min_frames_per_track'
  | 'top_k_embeddings'
  | 'min_quality_score'
  | 'retention_days';

export interface AdminVideo {
  video_id: string;
  s3_path: string;
  status: VideoStatus;
  error_message: string | null;
  pool_id?: string | null;
  pool_users_count?: number;
  user_embeddings_count?: number;
  video_embeddings_count?: number;
  min_distance?: number | null;
  best_similarity?: number | null;
  best_match_user_id?: string | null;
  best_match_user_email?: string | null;
  confirmed_match_user_id?: string | null;
  confirmed_match_user_email?: string | null;
  confirmed_match_score?: number | null;
  assigned_user_id?: string | null;
  assigned_user_email?: string | null;
  threshold?: number;
  progress_percent?: number;
  stage_status?: {
    upload: PipelineStageState;
    frame: PipelineStageState;
    embedding: PipelineStageState;
    matching: PipelineStageState;
  };
  stage_timings?: {
    upload_seconds?: number | null;
    queue_delay_seconds?: number | null;
    frame_processing_seconds?: number | null;
    embedding_processing_seconds?: number | null;
    matching_processing_seconds?: number | null;
    total_pipeline_seconds?: number | null;
  };
  tracks_total?: number;
  tracks_processed?: number;
  tracks_pending?: number;
  tracks_matched?: number;
  tracks_unmatched?: number;
  tracks_rejected?: number;
  matches_count?: number;
  rejection_rate?: number | null;
  avg_similarity?: number | null;
  avg_margin?: number | null;
  quality_guard?: {
    min_frames_per_track: number;
    min_track_consistency: number;
    min_quality_score: number;
    rejection_counts: Record<string, number>;
  };
  created_at: string;
  updated_at: string;
  source_video_url: string | null;
  message?: string;
}

export interface CameraRecord {
  camera_id: string;
  name: string;
  url: string;
  pool_id?: string | null;
  active: boolean;
  created_at: string;
  updated_at: string;
}

export interface PoolRecord {
  id: string;
  pool_id: string;
  name: string;
}

export interface AdminUser {
  user_id: string;
  email: string;
  role: 'admin' | 'user';
  pool_id: string | null;
  reference_images_count: number;
  latest_reference_image_url: string | null;
}

export interface SystemConfigValues {
  min_similarity: number;
  min_margin: number;
  min_frames_per_track: number;
  top_k_embeddings: number;
  min_quality_score: number;
  retention_days: number;
}

export interface FaceComparisonResponse {
  similarity: number;
  distance: number | null;
  best_similarity?: number | null;
  second_best_similarity?: number | null;
  margin?: number | null;
  passes_similarity: boolean;
  passes_margin?: boolean;
  passes_margin_estimate: boolean;
  estimated_margin?: number | null;
  final_verdict: 'match' | 'no_match';
  rejection_reason?: string | null;
  decision_reason?: string | null;
  explanation?: string;
  decision_explanation?: string;
  verdict: 'match' | 'no_match';
  threshold?: number;
  threshold_used?: number;
  margin_threshold?: number;
  margin_threshold_used?: number;
  warning?: string | null;
  thresholds: Pick<SystemConfigValues, 'min_similarity' | 'min_margin'>;
}

export interface ConfigHistoryEntry {
  audit_id: number;
  batch_id: string;
  key: string;
  old_value: number;
  new_value: number;
  changed_at: string;
  admin_id: string;
  updated_by: string;
  change_reason: string;
}

export interface ConfigStatusResponse {
  cooldown_seconds: number;
  cooldown_remaining_seconds: number;
  latest_change: ConfigHistoryEntry | null;
}

export interface AdminMetricVideoSummary {
  video_id: string;
  status: VideoStatus;
  matches_count: number;
  tracks_matched: number;
  tracks_unmatched: number;
  progress_percent: number;
  avg_similarity: number | null;
  avg_margin: number | null;
}

export interface AdminMetricsResponse {
  matching: {
    average_match_similarity?: number | null;
    average_match_margin?: number | null;
    rejection_rate?: number | null;
    [key: string]: number | string | null | undefined;
  };
  videos: {
    matches_per_video: AdminMetricVideoSummary[];
  };
}

export const DEFAULT_SYSTEM_CONFIG: SystemConfigValues = {
  min_similarity: 0.75,
  min_margin: 0.05,
  min_frames_per_track: 3,
  top_k_embeddings: 5,
  min_quality_score: 0.5,
  retention_days: 7,
};

export const EMPTY_ADMIN_METRICS: AdminMetricsResponse = {
  matching: {
    average_match_similarity: null,
    average_match_margin: null,
    rejection_rate: null,
  },
  videos: {
    matches_per_video: [],
  },
};

export const SYSTEM_CONFIG_FIELDS: Array<{
  key: SystemConfigKey;
  label: string;
  type: 'int' | 'float';
  min: number;
  max: number;
  step: number;
}> = [
  { key: 'min_similarity', label: 'Min Similarity', type: 'float', min: 0.5, max: 0.95, step: 0.01 },
  { key: 'min_margin', label: 'Min Margin', type: 'float', min: 0.01, max: 0.2, step: 0.01 },
  { key: 'min_frames_per_track', label: 'Min Frames per Track', type: 'int', min: 2, max: 10, step: 1 },
  { key: 'top_k_embeddings', label: 'Top K Embeddings', type: 'int', min: 1, max: 10, step: 1 },
  { key: 'min_quality_score', label: 'Min Quality Score', type: 'float', min: 0.1, max: 1, step: 0.01 },
  { key: 'retention_days', label: 'Retention Days', type: 'int', min: 1, max: 30, step: 1 },
];
