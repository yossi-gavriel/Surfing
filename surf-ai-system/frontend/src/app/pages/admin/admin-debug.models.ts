import { SystemConfigValues } from './admin.models';

export interface DebugReferenceImage {
  user_embedding_id: string;
  user_id?: string;
  user_email?: string | null;
  image_url: string | null;
  created_at: string;
}

export interface DebugVideoFrame {
  debug_frame_id?: string;
  video_embedding_id?: string;
  track_id: string;
  frame_index?: number;
  frame_timestamp?: string | null;
  image_url?: string | null;
  keyframe_s3?: string | null;
  keyframe_url?: string | null;
  start_time?: string | null;
  end_time?: string | null;
  best_user_embedding_id?: string | null;
  best_user_id?: string | null;
  best_user_email?: string | null;
  user_id?: string | null;
  user_email?: string | null;
  best_reference_image_url?: string | null;
  bbox?: number[] | null;
  face_bbox?: number[] | null;
  quality_score?: number | null;
  has_face?: boolean;
  used_for_embedding?: boolean;
  used_for_track_embedding?: boolean;
  is_valid?: boolean;
  frames_count?: number | null;
  embeddings_count?: number | null;
  consistency?: number | null;
  quality_avg?: number | null;
  aggregation_method?: string | null;
  frames_received?: number | null;
  embeddings_created?: number | null;
  used_frame_indexes?: number[];
  second_best_similarity?: number | null;
  similarity_margin?: number | null;
  margin?: number | null;
  match_rejection_reason?: string | null;
  passes_similarity?: boolean;
  passes_margin?: boolean;
  final_verdict?: string | null;
  decision?: string | null;
  decision_reason?: string | null;
  decision_explanation?: string | null;
  threshold_used?: number | null;
  margin_threshold_used?: number | null;
  last_attempt?: {
    persist_status?: string | null;
    decision_reason?: string | null;
    decision_explanation?: string | null;
    processed_at?: string | null;
    existing_user_id?: string | null;
  } | null;
  last_attempt_outcome?: string | null;
  last_attempt_at?: string | null;
  det_score?: number | null;
  face_size?: number | null;
  blur_score?: number | null;
  rejection_reason?: string | null;
  distance: number | null;
  similarity: number | null;
  is_match_under_threshold: boolean;
}

export interface DebugCompareResponse {
  video_id: string;
  user_embeddings: number;
  video_embeddings: number;
  pool_id?: string | null;
  pool?: {
    pool_id: string;
    name: string;
  } | null;
  pool_users?: number;
  threshold: number;
  margin_threshold?: number | null;
  best_match_user_id?: string | null;
  best_match_user_email?: string | null;
  best_reference_user_embedding_id: string | null;
  best_reference_image_url: string | null;
  reference_images: DebugReferenceImage[];
  track_summaries?: DebugVideoFrame[];
  video_frames: DebugVideoFrame[];
  debug_frames: DebugVideoFrame[];
  comparisons?: Array<{
    video_embedding_id: string;
    user_embedding_id: string;
    user_id: string;
    user_email: string;
    distance: number;
    similarity: number;
    is_match_under_threshold: boolean;
  }>;
  matches?: Array<{
    user_id: string;
    email: string;
    score: number;
    best_similarity?: number | null;
    second_best_similarity?: number | null;
    margin?: number | null;
    threshold_used?: number | null;
    margin_threshold_used?: number | null;
    decision_reason?: string | null;
    decision_explanation?: string | null;
    confidence: number;
    distance: number;
  }>;
  assigned_user_id?: string | null;
  assigned_user_email?: string | null;
  summary: {
    total_frames: number;
    valid_frames: number;
    used_frames?: number;
    tracks?: number;
    matched_tracks?: number;
    rejected_tracks?: number;
    rejected_low_similarity?: number;
    rejected_low_margin?: number;
    rejected_min_frames?: number;
    rejected_track_consistency?: number;
    best_similarity: number | null;
    best_distance: number | null;
    force_match: boolean;
  };
}

export interface TrackDecisionView {
  summary: DebugVideoFrame | null;
  videoFrames: DebugVideoFrame[];
  debugFrames: DebugVideoFrame[];
  comparisons: DebugCompareResponse['comparisons'];
  matches: DebugCompareResponse['matches'];
  thresholds: Pick<SystemConfigValues, 'min_similarity' | 'min_margin'>;
}
