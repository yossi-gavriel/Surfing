import { CommonModule } from '@angular/common';
import { HttpClient } from '@angular/common/http';
import { Component, DestroyRef, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';
import { forkJoin } from 'rxjs';

import { AuthService, MeResponse } from '../../core/auth.service';
import { I18nService } from '../../core/i18n.service';

type AdminTab = 'overview' | 'videos' | 'debug' | 'calibration' | 'config' | 'users' | 'cameras' | 'pools';
type VideoStatus = 'uploaded' | 'processing' | 'completed' | 'failed';
type PipelineStageState = 'pending' | 'processing' | 'completed';
type SystemConfigKey =
  | 'min_similarity'
  | 'min_margin'
  | 'min_frames_per_track'
  | 'top_k_embeddings'
  | 'min_quality_score'
  | 'retention_days';

interface AdminVideo {
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

interface CameraRecord {
  camera_id: string;
  name: string;
  url: string;
  pool_id?: string | null;
  active: boolean;
  created_at: string;
  updated_at: string;
}

interface PoolRecord {
  id: string;
  pool_id: string;
  name: string;
}

interface AdminUser {
  user_id: string;
  email: string;
  role: 'admin' | 'user';
  pool_id: string | null;
  reference_images_count: number;
  latest_reference_image_url: string | null;
}

interface SystemConfigValues {
  min_similarity: number;
  min_margin: number;
  min_frames_per_track: number;
  top_k_embeddings: number;
  min_quality_score: number;
  retention_days: number;
}

interface FaceComparisonResponse {
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

interface ConfigHistoryEntry {
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

interface ConfigStatusResponse {
  cooldown_seconds: number;
  cooldown_remaining_seconds: number;
  latest_change: ConfigHistoryEntry | null;
}

interface AdminMetricVideoSummary {
  video_id: string;
  status: VideoStatus;
  matches_count: number;
  tracks_matched: number;
  tracks_unmatched: number;
  progress_percent: number;
  avg_similarity: number | null;
  avg_margin: number | null;
}

interface AdminMetricsResponse {
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

const DEFAULT_SYSTEM_CONFIG: SystemConfigValues = {
  min_similarity: 0.75,
  min_margin: 0.05,
  min_frames_per_track: 3,
  top_k_embeddings: 5,
  min_quality_score: 0.5,
  retention_days: 7,
};

const EMPTY_ADMIN_METRICS: AdminMetricsResponse = {
  matching: {
    average_match_similarity: null,
    average_match_margin: null,
    rejection_rate: null,
  },
  videos: {
    matches_per_video: [],
  },
};

const SYSTEM_CONFIG_FIELDS: Array<{
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

@Component({
  selector: 'app-admin-page',
  standalone: true,
  imports: [CommonModule, FormsModule],
  template: `
    <section class="hero">
      <div>
        <p class="eyebrow">{{ i18n.t('admin.heroEyebrow') }}</p>
        <h2>{{ i18n.t('admin.heroTitle') }}</h2>
        <p class="subcopy">{{ i18n.t('admin.heroSubtitle') }}</p>
      </div>

      <div class="hero-actions">
        <span class="pill live" *ngIf="isPolling()">{{ i18n.t('admin.pipelineLive') }}</span>
        <button (click)="refresh()" [disabled]="loading()">
          {{ loading() ? i18n.t('common.refreshing') : i18n.t('common.refresh') }}
        </button>
      </div>
    </section>

    <section class="feedback error" *ngIf="errorMessage()">{{ errorMessage() }}</section>
    <section class="feedback success" *ngIf="successMessage()">{{ successMessage() }}</section>

    <section class="feedback" *ngIf="poolSelectionDirty()">
      {{ i18n.t('admin.poolSelectionPending') }}
    </section>

    <nav class="tabs" aria-label="Admin sections">
      <button
        *ngFor="let tab of tabs"
        type="button"
        class="tab-button"
        [class.active]="activeTab() === tab.id"
        (click)="activeTab.set(tab.id)"
      >
        {{ i18n.t(tab.label) }}
      </button>
    </nav>

    <section class="state-card" *ngIf="loading()">{{ i18n.t('common.loading') }}</section>

    <ng-container *ngIf="!loading()">
      <section class="panel" *ngIf="activeTab() === 'overview'">
        <div class="section-header">
          <div>
            <p class="panel-label">{{ i18n.t('admin.tabs.overview') }}</p>
            <h3>{{ activePoolName() || i18n.t('admin.noActivePool') }}</h3>
          </div>
          <button type="button" class="secondary" (click)="activeTab.set('pools')">
            {{ i18n.t('admin.openPoolsTab') }}
          </button>
        </div>

        <div class="summary-grid">
          <article class="summary-card">
            <span>{{ i18n.t('admin.videos') }}</span>
            <strong>{{ videos().length }}</strong>
            <small>{{ i18n.t('admin.pendingPipeline', { count: pendingVideosCount() }) }}</small>
          </article>
          <article class="summary-card">
            <span>{{ i18n.t('admin.poolUsers') }}</span>
            <strong>{{ users().length }}</strong>
            <small>{{ i18n.t('admin.usersCount', { count: users().length }) }}</small>
          </article>
          <article class="summary-card">
            <span>{{ i18n.t('admin.cameras') }}</span>
            <strong>{{ cameras().length }}</strong>
            <small>{{ i18n.t('admin.activeSources') }}</small>
          </article>
          <article class="summary-card">
            <span>{{ i18n.t('admin.debugPrimary') }}</span>
            <strong>{{ debugVideos().length }}</strong>
            <small>{{ i18n.t('admin.metricsOnlyInDebug') }}</small>
          </article>
        </div>

        <div class="overview-grid">
          <article class="overview-card">
            <div class="section-header compact">
              <div>
                <p class="panel-label">{{ i18n.t('admin.videos') }}</p>
                <h3>{{ i18n.t('admin.processingStatus') }}</h3>
              </div>
              <button type="button" class="secondary" (click)="activeTab.set('videos')">
                {{ i18n.t('admin.tabs.videos') }}
              </button>
            </div>

            <div class="status-grid">
              <div class="status-card">
                <span>{{ i18n.t('admin.status.uploaded') }}</span>
                <strong>{{ uploadedCount() }}</strong>
              </div>
              <div class="status-card">
                <span>{{ i18n.t('admin.status.processing') }}</span>
                <strong>{{ processingCount() }}</strong>
              </div>
              <div class="status-card">
                <span>{{ i18n.t('admin.status.completed') }}</span>
                <strong>{{ completedCount() }}</strong>
              </div>
              <div class="status-card">
                <span>{{ i18n.t('admin.status.failed') }}</span>
                <strong>{{ failedCount() }}</strong>
              </div>
            </div>
          </article>

          <article class="overview-card">
            <div class="section-header compact">
              <div>
                <p class="panel-label">{{ i18n.t('admin.debugPrimary') }}</p>
                <h3>{{ i18n.t('admin.tabs.debug') }}</h3>
              </div>
              <button type="button" (click)="activeTab.set('debug')">
                {{ i18n.t('admin.quickOpenDebug') }}
              </button>
            </div>

            <div class="empty" *ngIf="debugVideos().length === 0">{{ i18n.t('admin.noDebugVideos') }}</div>
            <div class="debug-quick-list" *ngIf="debugVideos().length > 0">
              <button
                type="button"
                class="debug-link"
                *ngFor="let video of debugVideos().slice(0, 3)"
                (click)="openVideoDebug(video.video_id)"
              >
                <strong>{{ video.video_id }}</strong>
                <span>
                  {{
                    i18n.t('admin.debugQuickStats', {
                      embeddings: video.video_embeddings_count ?? 0,
                      similarity: formatMetric(video.best_similarity),
                    })
                  }}
                </span>
              </button>
            </div>
          </article>
        </div>

        <article class="overview-card metrics-board">
          <div class="section-header compact">
            <div>
              <p class="panel-label">{{ i18n.t('admin.metricsDashboard') }}</p>
              <h3>{{ i18n.t('admin.pipelineVisibilityTitle') }}</h3>
            </div>
            <button type="button" class="secondary" (click)="activeTab.set('videos')">
              {{ i18n.t('admin.tabs.videos') }}
            </button>
          </div>

          <div class="status-grid">
            <div class="status-card">
              <span>{{ i18n.t('admin.matchesPerVideo') }}</span>
              <strong>{{ formatInteger(totalMatchesCount()) }}</strong>
            </div>
            <div class="status-card">
              <span>{{ i18n.t('admin.rejectionRate') }}</span>
              <strong>{{ formatPercent(metrics().matching.rejection_rate) }}</strong>
            </div>
            <div class="status-card">
              <span>{{ i18n.t('admin.avgSimilarity') }}</span>
              <strong>{{ formatMetric(metrics().matching.average_match_similarity) }}</strong>
            </div>
            <div class="status-card">
              <span>{{ i18n.t('admin.avgMargin') }}</span>
              <strong>{{ formatMetric(metrics().matching.average_match_margin) }}</strong>
            </div>
          </div>

          <div class="metrics-list" *ngIf="metrics().videos.matches_per_video.length > 0; else noVideoMetrics">
            <article class="metric-row" *ngFor="let item of metrics().videos.matches_per_video.slice(0, 5)">
              <div>
                <strong>{{ item.video_id }}</strong>
                <small>{{ videoStatusLabel(item.status) }}</small>
              </div>
              <div class="metrics compact">
                <span>{{ i18n.t('admin.matchesLabel', { count: item.matches_count }) }}</span>
                <span>{{ i18n.t('admin.matchedCount', { count: item.tracks_matched }) }}</span>
                <span>{{ i18n.t('admin.unmatchedCount', { count: item.tracks_unmatched }) }}</span>
                <span>{{ i18n.t('admin.progressLabel', { value: item.progress_percent }) }}</span>
              </div>
            </article>
          </div>
          <ng-template #noVideoMetrics>
            <div class="empty">{{ i18n.t('admin.noMetricsYet') }}</div>
          </ng-template>
        </article>
      </section>

      <section class="panel" *ngIf="activeTab() === 'videos'">
        <div class="section-header">
          <div>
            <p class="panel-label">{{ i18n.t('admin.videoUpload') }}</p>
            <h3>{{ i18n.t('admin.processingStatus') }}</h3>
          </div>
          <button type="button" class="secondary" (click)="activeTab.set('debug')">
            {{ i18n.t('admin.quickOpenDebug') }}
          </button>
        </div>

        <div class="video-upload-card">
          <label class="dropzone">
            <input type="file" accept="video/*" (change)="onVideoSelected($event)" />
            <span>{{ selectedVideoName() || i18n.t('admin.chooseVideoFile') }}</span>
            <small>{{ i18n.t('admin.videoUploadHint') }}</small>
          </label>

          <div class="actions">
            <button (click)="uploadVideo()" [disabled]="!selectedVideo() || uploadingVideo()">
              {{ uploadingVideo() ? i18n.t('common.uploading') : i18n.t('admin.uploadVideo') }}
            </button>
            <button type="button" class="secondary" (click)="activeTab.set('pools')">
              {{ i18n.t('admin.openPoolsTab') }}
            </button>
          </div>
        </div>

        <div class="empty" *ngIf="!selectedPoolId">{{ i18n.t('admin.selectPoolToContinue') }}</div>
        <div class="empty" *ngIf="selectedPoolId && videos().length === 0">{{ i18n.t('admin.noVideos') }}</div>

        <div class="video-list" *ngIf="videos().length > 0">
          <article class="video-row" *ngFor="let video of videos()">
            <div class="video-copy">
              <div class="video-headline">
                <strong>{{ video.video_id }}</strong>
                <span class="status" [class.completed]="video.status === 'completed'" [class.failed]="video.status === 'failed'">
                  {{ videoStatusLabel(video.status) }}
                </span>
              </div>

              <small>{{ i18n.t('admin.createdAt', { value: formatTimestamp(video.created_at) }) }}</small>
              <small>{{ i18n.t('admin.updatedAt', { value: formatTimestamp(video.updated_at) }) }}</small>
              <a *ngIf="video.source_video_url" [href]="video.source_video_url" target="_blank" rel="noopener">
                {{ i18n.t('admin.openSource') }}
              </a>
              <small class="error-copy" *ngIf="video.error_message">{{ video.error_message }}</small>

              <div class="progress-row">
                <div class="progress-track">
                  <span class="progress-fill" [style.width.%]="video.progress_percent ?? 0"></span>
                </div>
                <strong>{{ i18n.t('admin.progressLabel', { value: video.progress_percent ?? 0 }) }}</strong>
              </div>

              <div class="pipeline-stages">
                <span class="stage" [ngClass]="pipelineStageClass(video.stage_status?.upload)">
                  {{ i18n.t('admin.stage.upload') }} · {{ pipelineStageStatusLabel(video.stage_status?.upload) }}
                </span>
                <span class="stage" [ngClass]="pipelineStageClass(video.stage_status?.frame)">
                  {{ i18n.t('admin.stage.frame') }} · {{ pipelineStageStatusLabel(video.stage_status?.frame) }}
                </span>
                <span class="stage" [ngClass]="pipelineStageClass(video.stage_status?.embedding)">
                  {{ i18n.t('admin.stage.embedding') }} · {{ pipelineStageStatusLabel(video.stage_status?.embedding) }}
                </span>
                <span class="stage" [ngClass]="pipelineStageClass(video.stage_status?.matching)">
                  {{ i18n.t('admin.stage.matching') }} · {{ pipelineStageStatusLabel(video.stage_status?.matching) }}
                </span>
              </div>

              <div class="metrics">
                <span>{{ i18n.t('admin.tracksTotal', { count: video.tracks_total ?? 0 }) }}</span>
                <span>{{ i18n.t('admin.processedCount', { count: video.tracks_processed ?? 0 }) }}</span>
                <span>{{ i18n.t('admin.pendingCount', { count: video.tracks_pending ?? 0 }) }}</span>
                <span>{{ i18n.t('admin.matchedCount', { count: video.tracks_matched ?? 0 }) }}</span>
                <span>{{ i18n.t('admin.unmatchedCount', { count: video.tracks_unmatched ?? 0 }) }}</span>
                <span>{{ i18n.t('admin.rejectedCount', { count: video.tracks_rejected ?? 0 }) }}</span>
              </div>

              <div class="metrics compact" *ngIf="hasVideoTimings(video)">
                <span>{{ i18n.t('admin.uploadTiming', { value: formatSeconds(video.stage_timings?.upload_seconds) }) }}</span>
                <span>{{ i18n.t('admin.queueDelayTiming', { value: formatSeconds(video.stage_timings?.queue_delay_seconds) }) }}</span>
                <span>{{ i18n.t('admin.frameTiming', { value: formatSeconds(video.stage_timings?.frame_processing_seconds) }) }}</span>
                <span>{{ i18n.t('admin.embeddingTiming', { value: formatSeconds(video.stage_timings?.embedding_processing_seconds) }) }}</span>
                <span>{{ i18n.t('admin.matchingTiming', { value: formatSeconds(video.stage_timings?.matching_processing_seconds) }) }}</span>
                <span>{{ i18n.t('admin.totalTiming', { value: formatSeconds(video.stage_timings?.total_pipeline_seconds) }) }}</span>
              </div>

              <small class="hint">{{ videoOutcomeSummary(video) }}</small>
              <small class="hint" *ngIf="video.avg_similarity !== null || video.avg_margin !== null">
                {{
                  i18n.t('admin.similarityMarginSummary', {
                    similarity: formatMetric(video.avg_similarity),
                    margin: formatMetric(video.avg_margin),
                  })
                }}
              </small>
              <small class="hint" *ngIf="qualityGuardSummary(video)">
                {{ qualityGuardSummary(video) }}
              </small>

              <div class="assign-bar" *ngIf="users().length > 0">
                <select [ngModel]="assignmentSelection(video)" (ngModelChange)="setAssignmentSelection(video.video_id, $event)">
                  <option [ngValue]="''">{{ i18n.t('admin.noAssignment') }}</option>
                  <option *ngFor="let user of users()" [ngValue]="user.user_id">{{ user.email }}</option>
                </select>
                <button
                  class="secondary"
                  type="button"
                  (click)="assignVideo(video)"
                  [disabled]="assigningVideoId() === video.video_id"
                >
                  {{ assigningVideoId() === video.video_id ? i18n.t('common.saving') : i18n.t('admin.saveAssignment') }}
                </button>
              </div>
            </div>

            <div class="video-actions">
              <button
                class="secondary"
                type="button"
                (click)="triggerProcessing(video.video_id)"
                [disabled]="processingVideoId() === video.video_id"
              >
                {{ processingVideoId() === video.video_id ? i18n.t('admin.queueing') : i18n.t('admin.triggerPipeline') }}
              </button>
              <button type="button" (click)="openVideoDebug(video.video_id)">
                {{ i18n.t('admin.quickOpenDebug') }}
              </button>
            </div>
          </article>
        </div>
      </section>

      <section class="panel" *ngIf="activeTab() === 'debug'">
        <div class="section-header">
          <div>
            <p class="panel-label">{{ i18n.t('admin.debugPrimary') }}</p>
            <h3>{{ i18n.t('admin.tabs.debug') }}</h3>
            <p class="subcopy compact-copy">{{ i18n.t('admin.metricsOnlyInDebug') }}</p>
          </div>
        </div>

        <div class="empty" *ngIf="!selectedPoolId">{{ i18n.t('admin.selectPoolToContinue') }}</div>
        <div class="empty" *ngIf="selectedPoolId && debugVideos().length === 0">{{ i18n.t('admin.noDebugVideos') }}</div>

        <div class="debug-list" *ngIf="debugVideos().length > 0">
          <article class="debug-row" *ngFor="let video of debugVideos()">
            <div class="video-copy">
              <div class="video-headline">
                <strong>{{ video.video_id }}</strong>
                <span class="status" [class.completed]="video.status === 'completed'" [class.failed]="video.status === 'failed'">
                  {{ videoStatusLabel(video.status) }}
                </span>
              </div>

              <small>{{ i18n.t('admin.createdAt', { value: formatTimestamp(video.created_at) }) }}</small>

              <div class="metrics">
                <span>{{ i18n.t('admin.poolEmbeddings', { count: video.user_embeddings_count ?? 0 }) }}</span>
                <span>{{ i18n.t('admin.videoEmbeddings', { count: video.video_embeddings_count ?? 0 }) }}</span>
                <span>{{ i18n.t('admin.poolUsersCount', { count: video.pool_users_count ?? 0 }) }}</span>
                <span>{{ i18n.t('admin.bestSimilarity', { value: formatMetric(video.best_similarity) }) }}</span>
              </div>

              <small class="hint" *ngIf="video.best_match_user_email">
                {{ i18n.t('admin.bestCandidate', { email: video.best_match_user_email }) }}
              </small>
              <small class="hint" *ngIf="video.confirmed_match_user_email">
                {{ i18n.t('admin.confirmedMatch', { email: video.confirmed_match_user_email }) }}
              </small>
            </div>

            <div class="video-actions">
              <button type="button" (click)="openVideoDebug(video.video_id)">
                {{ i18n.t('admin.quickOpenDebug') }}
              </button>
            </div>
          </article>
        </div>
      </section>

      <section class="panel" *ngIf="activeTab() === 'calibration'">
        <div class="section-header">
          <div>
            <p class="panel-label">{{ i18n.t('admin.faceCalibrationLabel') }}</p>
            <h3>{{ i18n.t('admin.faceCalibrationTitle') }}</h3>
            <p class="subcopy compact-copy">{{ i18n.t('admin.faceCalibrationSubtitle') }}</p>
          </div>
          <button
            type="button"
            class="secondary"
            (click)="saveCalibrationAsSystemConfig()"
            [disabled]="savingSystemConfig()"
          >
            {{ savingSystemConfig() ? i18n.t('common.saving') : i18n.t('admin.saveAsSystemConfig') }}
          </button>
        </div>

        <div class="calibration-upload-grid">
          <label class="dropzone">
            <input type="file" accept="image/*" (change)="onCalibrationFileSelected($event, 'A')" />
            <span>{{ calibrationFileNameA() || i18n.t('admin.uploadImageA') }}</span>
            <small>{{ i18n.t('admin.faceCalibrationHint') }}</small>
          </label>

          <label class="dropzone">
            <input type="file" accept="image/*" (change)="onCalibrationFileSelected($event, 'B')" />
            <span>{{ calibrationFileNameB() || i18n.t('admin.uploadImageB') }}</span>
            <small>{{ i18n.t('admin.faceCalibrationHint') }}</small>
          </label>
        </div>

        <div class="actions calibration-actions">
          <button type="button" (click)="compareFaces()" [disabled]="!canCompareFaces() || comparingFaces()">
            {{ comparingFaces() ? i18n.t('common.working') : i18n.t('admin.compareFaces') }}
          </button>
        </div>

        <div class="feedback warning calibration-warning">
          {{ calibrationGlobalImpactWarning() }}
        </div>

        <div class="calibration-grid">
          <article class="config-card">
            <div class="section-header compact">
              <div>
                <p class="panel-label">{{ calibrationLiveThresholdsLabel() }}</p>
                <h3>{{ calibrationThresholdPreviewTitle() }}</h3>
              </div>
            </div>

            <div class="metrics live-thresholds-copy">
              <span>{{ i18n.t('admin.minSimilarity') }} {{ formatMetric(systemConfig().min_similarity, 2) }}</span>
              <span>{{ i18n.t('admin.minMargin') }} {{ formatMetric(systemConfig().min_margin, 2) }}</span>
            </div>

            <label>
              <span>{{ i18n.t('admin.minSimilarity') }}: {{ formatMetric(calibrationThresholds().min_similarity, 2) }}</span>
              <input
                type="range"
                min="0.5"
                max="0.95"
                step="0.01"
                [ngModel]="calibrationThresholds().min_similarity"
                (ngModelChange)="updateCalibrationThreshold('min_similarity', $event)"
              />
            </label>

            <label>
              <span>{{ i18n.t('admin.minMargin') }}: {{ formatMetric(calibrationThresholds().min_margin, 2) }}</span>
              <input
                type="range"
                min="0.01"
                max="0.2"
                step="0.01"
                [ngModel]="calibrationThresholds().min_margin"
                (ngModelChange)="updateCalibrationThreshold('min_margin', $event)"
              />
            </label>

            <small class="hint">{{ calibrationPreviewHint() }}</small>
          </article>

          <article class="config-card" *ngIf="calibrationResult() as result; else calibrationEmpty">
            <div class="section-header compact">
              <div>
                <p class="panel-label">{{ i18n.t('admin.faceCalibrationResult') }}</p>
                <h3>{{ i18n.t('admin.faceCalibrationVerdict') }}</h3>
              </div>
              <span class="status verdict" [class.completed]="calibrationVerdict() === 'match'" [class.failed]="calibrationVerdict() === 'no_match'">
                {{ calibrationVerdictLabel() }}
              </span>
            </div>

            <div class="calibration-metrics">
              <article class="summary-card">
                <span>{{ i18n.t('admin.similarity') }}</span>
                <strong>{{ formatMetric(result.similarity, 3) }}</strong>
              </article>
              <article class="summary-card">
                <span>{{ i18n.t('admin.distance') }}</span>
                <strong>{{ formatMetric(result.distance, 3) }}</strong>
              </article>
              <article class="summary-card">
                <span>Margin</span>
                <strong>{{ formatMetric(calibrationEstimatedMargin(), 3) }}</strong>
              </article>
            </div>

            <div class="metrics calibration-decision-copy">
              <span>With these thresholds -> {{ calibrationVerdictLabel() }}</span>
              <span>Threshold {{ formatMetric(calibrationThresholds().min_similarity, 2) }}</span>
              <span>Margin threshold {{ formatMetric(calibrationThresholds().min_margin, 2) }}</span>
              <span>Similarity {{ calibrationPreviewPassesSimilarity() ? 'passes' : 'fails' }}</span>
              <span>Margin {{ calibrationPreviewPassesMarginEstimate() ? 'passes' : 'fails' }}</span>
            </div>
            <small class="hint">{{ calibrationDecisionExplanation() }}</small>
            <small class="hint warning-copy" *ngIf="result.warning">{{ result.warning }}</small>

            <div class="image-compare-grid">
              <article class="image-card">
                <img *ngIf="calibrationPreviewA(); else missingCalibrationA" [src]="calibrationPreviewA() || ''" alt="Image A preview" />
                <ng-template #missingCalibrationA>
                  <div class="user-placeholder">{{ i18n.t('common.imageUnavailable') }}</div>
                </ng-template>
                <small>{{ i18n.t('admin.imageA') }}</small>
              </article>

              <article class="image-card">
                <img *ngIf="calibrationPreviewB(); else missingCalibrationB" [src]="calibrationPreviewB() || ''" alt="Image B preview" />
                <ng-template #missingCalibrationB>
                  <div class="user-placeholder">{{ i18n.t('common.imageUnavailable') }}</div>
                </ng-template>
                <small>{{ i18n.t('admin.imageB') }}</small>
              </article>
            </div>
          </article>

          <ng-template #calibrationEmpty>
            <article class="config-card empty-state-card">
              <p class="empty">{{ i18n.t('admin.faceCalibrationEmpty') }}</p>
            </article>
          </ng-template>
        </div>
      </section>

      <section class="panel" *ngIf="activeTab() === 'config'">
        <div class="section-header">
          <div>
            <p class="panel-label">{{ i18n.t('admin.systemConfigLabel') }}</p>
            <h3>{{ i18n.t('admin.systemConfigTitle') }}</h3>
            <p class="subcopy compact-copy">{{ i18n.t('admin.systemConfigSubtitle') }}</p>
          </div>
          <button
            type="button"
            (click)="saveSystemConfig()"
            [disabled]="savingSystemConfig() || !systemConfigDirty()"
          >
            {{ savingSystemConfig() ? i18n.t('common.saving') : i18n.t('admin.saveSystemConfig') }}
          </button>
        </div>

        <div class="config-grid">
          <article class="config-card">
            <div class="section-header compact">
              <div>
                <p class="panel-label">Change audit</p>
                <h3>Last config change</h3>
              </div>
              <span class="pill" *ngIf="configStatus().cooldown_remaining_seconds > 0">
                Cooldown {{ configStatus().cooldown_remaining_seconds }}s
              </span>
            </div>

            <div *ngIf="configStatus().latest_change as latest; else noConfigHistory" class="metrics">
              <span>{{ latest.key }}</span>
              <span>{{ latest.old_value }} -> {{ latest.new_value }}</span>
              <span>{{ latest.updated_by }}</span>
              <span>{{ formatTimestamp(latest.changed_at) }}</span>
            </div>

            <ng-template #noConfigHistory>
              <p class="empty">No config changes yet.</p>
            </ng-template>
          </article>

          <article class="config-card" *ngFor="let field of systemConfigFields">
            <div class="section-header compact">
              <div>
                <p class="panel-label">{{ i18n.t('admin.systemConfigLabel') }}</p>
                <h3>{{ field.label }}</h3>
              </div>
              <span class="pill">
                {{ field.type === 'int' ? formatInteger(systemConfigDraft()[field.key]) : formatMetric(systemConfigDraft()[field.key], 2) }}
              </span>
            </div>

            <label>
              <span>{{ field.label }}</span>
              <input
                type="number"
                [attr.min]="field.min"
                [attr.max]="field.max"
                [attr.step]="field.step"
                [ngModel]="systemConfigDraft()[field.key]"
                (ngModelChange)="updateSystemConfigField(field.key, $event)"
              />
            </label>

            <label *ngIf="field.type === 'float'">
              <span>{{ i18n.t('admin.livePreview') }}</span>
              <input
                type="range"
                [attr.min]="field.min"
                [attr.max]="field.max"
                [attr.step]="field.step"
                [ngModel]="systemConfigDraft()[field.key]"
                (ngModelChange)="updateSystemConfigField(field.key, $event)"
              />
            </label>

            <small class="hint">
              {{ i18n.t('admin.allowedRange', { min: field.min, max: field.max }) }}
            </small>
          </article>
        </div>
      </section>

      <section class="panel" *ngIf="activeTab() === 'users'">
        <div class="section-header">
          <div>
            <p class="panel-label">{{ i18n.t('admin.poolUsers') }}</p>
            <h3>{{ i18n.t('admin.usersInActivePool') }}</h3>
          </div>
          <span class="pill">{{ i18n.t('admin.usersCount', { count: users().length }) }}</span>
        </div>

        <div class="empty" *ngIf="!selectedPoolId">{{ i18n.t('admin.selectPoolForUsers') }}</div>
        <div class="empty" *ngIf="selectedPoolId && users().length === 0">{{ i18n.t('admin.noUsersInPool') }}</div>

        <div class="user-list" *ngIf="users().length > 0">
          <article class="user-row" *ngFor="let user of users()">
            <img *ngIf="user.latest_reference_image_url; else noUserImage" [src]="user.latest_reference_image_url" alt="Latest reference image" />
            <ng-template #noUserImage>
              <div class="user-placeholder">{{ i18n.t('admin.noImage') }}</div>
            </ng-template>

            <div class="user-copy">
              <strong>{{ user.email }}</strong>
              <span>{{ roleLabel(user.role) }}</span>
              <small>{{ i18n.t('admin.referenceImagesCount', { count: user.reference_images_count }) }}</small>
            </div>
          </article>
        </div>
      </section>

      <section class="panel" *ngIf="activeTab() === 'cameras'">
        <div class="section-header">
          <div>
            <p class="panel-label">{{ i18n.t('admin.cameraSource') }}</p>
            <h3>{{ i18n.t('admin.registerOrUpdateCamera') }}</h3>
            <p class="subcopy compact-copy">Use an RTSP URL or a local file path. Active sources go through the same ingestion pipeline as live system inputs.</p>
          </div>
        </div>

        <label>
          <span>{{ i18n.t('admin.name') }}</span>
          <input [(ngModel)]="cameraForm.name" [placeholder]="i18n.t('admin.namePlaceholder')" />
        </label>

        <label>
          <span>{{ i18n.t('admin.url') }}</span>
          <input [(ngModel)]="cameraForm.url" [placeholder]="i18n.t('admin.urlPlaceholder')" />
        </label>

        <label class="checkbox">
          <input type="checkbox" [(ngModel)]="cameraForm.active" />
          <span>{{ i18n.t('admin.activeCheckbox') }}</span>
        </label>

        <div class="actions">
          <button (click)="saveCamera()" [disabled]="savingCamera()">
            {{ savingCamera() ? i18n.t('common.saving') : i18n.t('admin.saveCamera') }}
          </button>
        </div>

        <div class="camera-list" *ngIf="cameras().length > 0">
          <article class="camera-row" *ngFor="let camera of cameras()">
            <div>
              <strong>{{ camera.name }}</strong>
              <span>{{ camera.url }}</span>
            </div>
            <span class="status" [class.completed]="camera.active" [class.failed]="!camera.active">
              {{ camera.active ? i18n.t('common.active') : i18n.t('common.inactive') }}
            </span>
          </article>
        </div>

        <div class="empty" *ngIf="selectedPoolId && cameras().length === 0">{{ i18n.t('admin.noCameras') }}</div>
      </section>

      <section class="panel" *ngIf="activeTab() === 'pools'">
        <div class="section-header">
          <div>
            <p class="panel-label">{{ i18n.t('admin.poolManagement') }}</p>
            <h3>{{ i18n.t('admin.createAndActivatePools') }}</h3>
          </div>
        </div>

        <label>
          <span>{{ i18n.t('admin.activePool') }}</span>
          <select [ngModel]="selectedPoolId" (ngModelChange)="onPoolSelectionChange($event)">
            <option [ngValue]="''">{{ i18n.t('common.choosePool') }}</option>
            <option *ngFor="let pool of pools()" [ngValue]="pool.pool_id">{{ pool.name }}</option>
          </select>
        </label>

        <div class="actions">
          <button (click)="saveActivePool()" [disabled]="savingPool() || !selectedPoolId">
            {{ savingPool() ? i18n.t('common.saving') : i18n.t('admin.setActivePool') }}
          </button>
        </div>

        <label>
          <span>{{ i18n.t('admin.newPoolName') }}</span>
          <input [(ngModel)]="newPoolName" [placeholder]="i18n.t('admin.newPoolPlaceholder')" />
        </label>

        <div class="actions">
          <button (click)="createPool()" [disabled]="creatingPool() || !newPoolName.trim()">
            {{ creatingPool() ? i18n.t('common.working') : i18n.t('admin.createPool') }}
          </button>
        </div>

        <div class="pool-list" *ngIf="pools().length > 0">
          <article class="pool-row" *ngFor="let pool of pools()">
            <div>
              <strong>{{ pool.name }}</strong>
              <span>{{ pool.pool_id }}</span>
            </div>
            <span class="pill" *ngIf="pool.pool_id === selectedPoolId">{{ i18n.t('admin.activePoolTag') }}</span>
          </article>
        </div>
      </section>
    </ng-container>
  `,
  styles: [`
    :host {
      display: block;
    }

    .metrics-board,
    .metric-row,
    .progress-row,
    .progress-track,
    .metrics-list {
      display: block;
    }

    .metrics-board {
      margin-top: 1.5rem;
    }

    .metrics-list {
      display: grid;
      gap: 0.85rem;
      margin-top: 1rem;
    }

    .metric-row {
      display: flex;
      justify-content: space-between;
      gap: 1rem;
      padding: 0.9rem 1rem;
      border: 1px solid rgba(20, 60, 68, 0.1);
      border-radius: 18px;
      background: rgba(255, 255, 255, 0.65);
    }

    .progress-row {
      display: flex;
      align-items: center;
      gap: 0.85rem;
      margin-top: 0.85rem;
    }

    .progress-track {
      position: relative;
      flex: 1;
      height: 10px;
      border-radius: 999px;
      overflow: hidden;
      background: rgba(20, 60, 68, 0.12);
    }

    .progress-fill {
      display: block;
      height: 100%;
      border-radius: inherit;
      background: linear-gradient(90deg, var(--accent-deep), var(--accent));
    }

    .compact {
      gap: 0.45rem;
    }

    .stage.pending {
      opacity: 0.7;
    }

    .stage.processing {
      background: rgba(20, 82, 96, 0.12);
      border-radius: 999px;
      padding: 0.2rem 0.55rem;
    }

    .stage.completed {
      background: rgba(29, 109, 79, 0.12);
      border-radius: 999px;
      padding: 0.2rem 0.55rem;
    }
  `],
})
export class AdminComponent {
  private readonly http = inject(HttpClient);
  private readonly auth = inject(AuthService);
  private readonly router = inject(Router);
  private readonly destroyRef = inject(DestroyRef);
  protected readonly i18n = inject(I18nService);

  readonly tabs: Array<{ id: AdminTab; label: Parameters<I18nService['t']>[0] }> = [
    { id: 'overview', label: 'admin.tabs.overview' },
    { id: 'videos', label: 'admin.tabs.videos' },
    { id: 'debug', label: 'admin.tabs.debug' },
    { id: 'calibration', label: 'admin.tabs.calibration' },
    { id: 'config', label: 'admin.tabs.config' },
    { id: 'users', label: 'admin.tabs.users' },
    { id: 'cameras', label: 'admin.tabs.cameras' },
    { id: 'pools', label: 'admin.tabs.pools' },
  ];
  readonly systemConfigFields = SYSTEM_CONFIG_FIELDS;

  readonly activeTab = signal<AdminTab>('overview');
  readonly videos = signal<AdminVideo[]>([]);
  readonly debugVideos = signal<AdminVideo[]>([]);
  readonly cameras = signal<CameraRecord[]>([]);
  readonly pools = signal<PoolRecord[]>([]);
  readonly users = signal<AdminUser[]>([]);
  readonly systemConfig = signal<SystemConfigValues>({ ...DEFAULT_SYSTEM_CONFIG });
  readonly systemConfigDraft = signal<SystemConfigValues>({ ...DEFAULT_SYSTEM_CONFIG });
  readonly configHistory = signal<ConfigHistoryEntry[]>([]);
  readonly configStatus = signal<ConfigStatusResponse>({
    cooldown_seconds: 0,
    cooldown_remaining_seconds: 0,
    latest_change: null,
  });
  readonly metrics = signal<AdminMetricsResponse>({ ...EMPTY_ADMIN_METRICS });
  readonly loading = signal(false);
  readonly uploadingVideo = signal(false);
  readonly savingCamera = signal(false);
  readonly savingPool = signal(false);
  readonly creatingPool = signal(false);
  readonly savingSystemConfig = signal(false);
  readonly comparingFaces = signal(false);
  readonly processingVideoId = signal<string | null>(null);
  readonly assigningVideoId = signal<string | null>(null);
  readonly selectedVideo = signal<File | null>(null);
  readonly selectedVideoName = signal('');
  readonly calibrationFileA = signal<File | null>(null);
  readonly calibrationFileB = signal<File | null>(null);
  readonly calibrationFileNameA = signal('');
  readonly calibrationFileNameB = signal('');
  readonly calibrationPreviewA = signal<string | null>(null);
  readonly calibrationPreviewB = signal<string | null>(null);
  readonly calibrationResult = signal<FaceComparisonResponse | null>(null);
  readonly calibrationThresholds = signal<Pick<SystemConfigValues, 'min_similarity' | 'min_margin'>>({
    min_similarity: DEFAULT_SYSTEM_CONFIG.min_similarity,
    min_margin: DEFAULT_SYSTEM_CONFIG.min_margin,
  });
  readonly successMessage = signal('');
  readonly errorMessage = signal('');
  readonly assignmentSelections = signal<Record<string, string>>({});
  readonly isPolling = signal(false);

  readonly activePoolName = computed(
    () => this.pools().find((pool) => pool.pool_id === this.selectedPoolId)?.name ?? '',
  );
  readonly poolSelectionDirty = computed(
    () => !!this.selectedPoolId && this.selectedPoolId !== (this.auth.poolId() ?? ''),
  );
  readonly pendingVideosCount = computed(
    () => this.videos().filter((video) => video.status === 'uploaded' || video.status === 'processing').length,
  );
  readonly uploadedCount = computed(() => this.videos().filter((video) => video.status === 'uploaded').length);
  readonly processingCount = computed(() => this.videos().filter((video) => video.status === 'processing').length);
  readonly completedCount = computed(() => this.videos().filter((video) => video.status === 'completed').length);
  readonly failedCount = computed(() => this.videos().filter((video) => video.status === 'failed').length);
  readonly totalMatchesCount = computed(() =>
    this.metrics().videos.matches_per_video.reduce((sum, item) => sum + (item.matches_count ?? 0), 0),
  );
  readonly systemConfigDirty = computed(() =>
    this.systemConfigFields.some(
      (field) => this.systemConfigDraft()[field.key] !== this.systemConfig()[field.key],
    ),
  );
  readonly calibrationPreviewPassesSimilarity = computed(() => {
    const result = this.calibrationResult();
    if (!result) {
      return false;
    }
    return (result.best_similarity ?? result.similarity) >= this.calibrationThresholds().min_similarity;
  });
  readonly calibrationEstimatedMargin = computed(() => {
    const result = this.calibrationResult();
    if (!result) {
      return null;
    }
    if (result.margin !== undefined) {
      return result.margin ?? null;
    }
    if (result.second_best_similarity === null || result.second_best_similarity === undefined) {
      return null;
    }
    return (result.best_similarity ?? result.similarity) - result.second_best_similarity;
  });
  readonly calibrationPreviewPassesMarginEstimate = computed(() => {
    const result = this.calibrationResult();
    const estimatedMargin = this.calibrationEstimatedMargin();
    if (!result) {
      return false;
    }
    if (result.second_best_similarity === null || result.second_best_similarity === undefined) {
      return true;
    }
    if (estimatedMargin === null) {
      return false;
    }
    return estimatedMargin >= this.calibrationThresholds().min_margin;
  });
  readonly calibrationVerdict = computed<'match' | 'no_match' | null>(() => {
    const result = this.calibrationResult();
    if (!result) {
      return null;
    }
    return this.calibrationPreviewPassesSimilarity() && this.calibrationPreviewPassesMarginEstimate()
      ? 'match'
      : 'no_match';
  });

  selectedPoolId = this.auth.selectedPoolId() ?? this.auth.poolId() ?? '';
  newPoolName = '';

  readonly cameraForm = {
    name: '',
    url: '',
    active: true,
  };

  private pollHandle: number | null = null;
  private pollInFlight = false;

  constructor() {
    this.destroyRef.onDestroy(() => {
      this.stopPolling();
      this.revokePreviewUrl(this.calibrationPreviewA());
      this.revokePreviewUrl(this.calibrationPreviewB());
    });
    this.refresh();
  }

  refresh(): void {
    this.loading.set(true);
    this.errorMessage.set('');
    this.loadMe();
  }

  loadMe(): void {
    this.http
      .get<MeResponse>('/api/me', {
        headers: this.auth.authHeaders(),
      })
      .subscribe({
        next: (me) => {
          this.auth.setProfile(me);
          if (me.pool_id) {
            this.selectedPoolId = me.pool_id;
          }
          this.loadPools();
        },
        error: (error) => this.handleHttpError(error, 'admin.profileLoadFailed'),
      });
  }

  loadPools(): void {
    this.http
      .get<PoolRecord[]>('/api/admin/pools', {
        headers: this.auth.authHeaders(),
      })
      .subscribe({
        next: (pools) => {
          this.pools.set(pools);
          if (this.selectedPoolId && !pools.some((pool) => pool.pool_id === this.selectedPoolId)) {
            this.selectedPoolId = '';
            this.auth.setSelectedPoolId(null);
          }
          if (!this.selectedPoolId) {
            this.selectedPoolId = this.auth.selectedPoolId() ?? this.auth.poolId() ?? '';
          }
          this.loadPoolData();
        },
        error: (error) => this.handleHttpError(error, 'admin.loadPoolsFailed'),
      });
  }

  loadPoolData(): void {
    if (!this.selectedPoolId) {
      forkJoin({
        systemConfig: this.fetchSystemConfig(),
        configHistory: this.http.get<ConfigHistoryEntry[]>('/api/admin/config/history?limit=10', {
          headers: this.auth.authHeaders(),
        }),
        configStatus: this.http.get<ConfigStatusResponse>('/api/admin/config/status', {
          headers: this.auth.authHeaders(),
        }),
      }).subscribe({
        next: ({ systemConfig, configHistory, configStatus }) => {
          this.videos.set([]);
          this.debugVideos.set([]);
          this.cameras.set([]);
          this.users.set([]);
          this.metrics.set({ ...EMPTY_ADMIN_METRICS });
          this.configHistory.set(configHistory);
          this.configStatus.set(configStatus);
          this.applySystemConfig(systemConfig);
          this.loading.set(false);
          this.stopPolling();
        },
        error: (error) => this.handleHttpError(error, 'admin.loadConfigFailed'),
      });
      return;
    }

    forkJoin({
      videos: this.fetchVideos(false),
      debugVideos: this.fetchVideos(true),
      cameras: this.http.get<CameraRecord[]>('/api/admin/cameras', {
        headers: this.auth.authHeaders(),
      }),
      users: this.http.get<AdminUser[]>('/api/admin/users', {
        headers: this.auth.authHeaders(),
      }),
      metrics: this.http.get<AdminMetricsResponse>('/api/admin/metrics', {
        headers: this.auth.authHeaders(),
      }),
      systemConfig: this.fetchSystemConfig(),
      configHistory: this.http.get<ConfigHistoryEntry[]>('/api/admin/config/history?limit=10', {
        headers: this.auth.authHeaders(),
      }),
      configStatus: this.http.get<ConfigStatusResponse>('/api/admin/config/status', {
        headers: this.auth.authHeaders(),
      }),
    }).subscribe({
      next: ({ videos, debugVideos, cameras, users, metrics, systemConfig, configHistory, configStatus }) => {
        this.videos.set(videos);
        this.debugVideos.set(debugVideos);
        this.cameras.set(cameras);
        this.users.set(users);
        this.metrics.set(metrics);
        this.configHistory.set(configHistory);
        this.configStatus.set(configStatus);
        this.applySystemConfig(systemConfig);
        this.syncAssignmentSelections(videos);
        this.loading.set(false);
        this.syncPollingState();
      },
      error: (error) => this.handleHttpError(error, 'admin.loadVideosFailed'),
    });
  }

  onPoolSelectionChange(poolId: string): void {
    this.selectedPoolId = poolId || '';
    this.auth.setSelectedPoolId(this.selectedPoolId || null);
    this.successMessage.set('');
    this.errorMessage.set('');
    if (!this.selectedPoolId || this.selectedPoolId !== (this.auth.poolId() ?? '')) {
      this.videos.set([]);
      this.debugVideos.set([]);
      this.cameras.set([]);
      this.users.set([]);
      this.metrics.set({ ...EMPTY_ADMIN_METRICS });
      this.stopPolling();
      return;
    }

    this.loadPoolData();
  }

  saveActivePool(afterSave?: () => void): void {
    if (!this.selectedPoolId) {
      return;
    }
    if (this.selectedPoolId === (this.auth.poolId() ?? '')) {
      afterSave?.();
      return;
    }

    this.savingPool.set(true);
    this.successMessage.set('');
    this.errorMessage.set('');

    this.http
      .put<MeResponse>('/api/me/pool', { pool_id: this.selectedPoolId }, {
        headers: this.auth.authHeaders(),
      })
      .subscribe({
        next: (response) => {
          this.auth.setProfile(response);
          this.selectedPoolId = response.pool_id ?? this.selectedPoolId;
          this.savingPool.set(false);
          this.successMessage.set(this.i18n.t('admin.activePoolUpdated'));
          this.loadPoolData();
          afterSave?.();
        },
        error: (error) => {
          this.savingPool.set(false);
          this.handleHttpError(error, 'admin.updatePoolFailed');
        },
      });
  }

  createPool(): void {
    if (!this.newPoolName.trim()) {
      return;
    }

    this.creatingPool.set(true);
    this.successMessage.set('');
    this.errorMessage.set('');

    this.http
      .post<PoolRecord>('/api/admin/pools', { name: this.newPoolName.trim() }, {
        headers: this.auth.authHeaders(),
      })
      .subscribe({
        next: (pool) => {
          this.creatingPool.set(false);
          this.newPoolName = '';
          this.selectedPoolId = pool.pool_id;
          this.auth.setSelectedPoolId(pool.pool_id);
          this.successMessage.set(this.i18n.t('admin.poolCreated', { name: pool.name }));
          this.loadPools();
          this.saveActivePool();
        },
        error: (error) => {
          this.creatingPool.set(false);
          this.handleHttpError(error, 'admin.createPoolFailed');
        },
      });
  }

  onVideoSelected(event: Event): void {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0] ?? null;
    this.selectedVideo.set(file);
    this.selectedVideoName.set(file?.name ?? '');
    this.successMessage.set('');
    this.errorMessage.set('');
  }

  uploadVideo(): void {
    const file = this.selectedVideo();
    if (!file) {
      return;
    }

    this.ensureActivePoolSynced(() => {
      const formData = new FormData();
      formData.append('file', file);

      this.uploadingVideo.set(true);
      this.successMessage.set('');
      this.errorMessage.set('');

      this.http
        .post<AdminVideo>('/api/admin/upload-video', formData, {
          headers: this.auth.authHeaders(),
        })
        .subscribe({
          next: (response) => {
            this.uploadingVideo.set(false);
            this.selectedVideo.set(null);
            this.selectedVideoName.set('');
            this.successMessage.set(this.i18n.translateApiMessage(response.message, 'admin.videoUpload'));
            this.upsertVideo(response);
            this.activeTab.set('videos');
            this.syncPollingState();
            this.pollVideoState();
          },
          error: (error) => {
            this.uploadingVideo.set(false);
            this.handleHttpError(error, 'admin.videoUploadFailed');
          },
        });
    });
  }

  saveCamera(): void {
    if (!this.cameraForm.name.trim() || !this.cameraForm.url.trim()) {
      this.errorMessage.set(this.i18n.t('admin.cameraFieldsRequired'));
      return;
    }

    this.ensureActivePoolSynced(() => {
      this.savingCamera.set(true);
      this.successMessage.set('');
      this.errorMessage.set('');

      this.http
        .post<CameraRecord>(
          '/api/admin/camera',
          {
            name: this.cameraForm.name.trim(),
            url: this.cameraForm.url.trim(),
            active: this.cameraForm.active,
          },
          {
            headers: this.auth.authHeaders(),
          },
        )
        .subscribe({
          next: (camera) => {
            this.savingCamera.set(false);
            this.cameraForm.name = '';
            this.cameraForm.url = '';
            this.cameraForm.active = true;
            this.successMessage.set(this.i18n.t('admin.cameraSaved', { name: camera.name }));
            this.loadPoolData();
          },
          error: (error) => {
            this.savingCamera.set(false);
            this.handleHttpError(error, 'admin.cameraSaveFailed');
          },
        });
    });
  }

  triggerProcessing(videoId: string): void {
    this.processingVideoId.set(videoId);
    this.successMessage.set('');
    this.errorMessage.set('');

    this.http
      .post<{ message: string }>(`/api/admin/videos/${videoId}/process`, {}, {
        headers: this.auth.authHeaders(),
      })
      .subscribe({
        next: (response) => {
          this.processingVideoId.set(null);
          this.successMessage.set(this.i18n.translateApiMessage(response.message, 'admin.triggerPipeline'));
          this.updateVideoStatusLocally(videoId, 'uploaded');
          this.syncPollingState();
          this.pollVideoState();
        },
        error: (error) => {
          this.processingVideoId.set(null);
          this.handleHttpError(error, 'admin.triggerProcessingFailed');
        },
      });
  }

  setAssignmentSelection(videoId: string, userId: string): void {
    this.assignmentSelections.update((current) => ({ ...current, [videoId]: userId }));
  }

  assignmentSelection(video: AdminVideo): string {
    return this.assignmentSelections()[video.video_id] ?? video.assigned_user_id ?? '';
  }

  assignVideo(video: AdminVideo): void {
    const userId = this.assignmentSelection(video) || null;
    this.assigningVideoId.set(video.video_id);
    this.successMessage.set('');
    this.errorMessage.set('');

    this.http
      .post<{ message: string }>(`/api/admin/videos/${video.video_id}/assign`, { user_id: userId }, {
        headers: this.auth.authHeaders(),
      })
      .subscribe({
        next: (response) => {
          this.assigningVideoId.set(null);
          this.successMessage.set(this.i18n.translateApiMessage(response.message, 'admin.saveAssignment'));
          this.loadPoolData();
        },
        error: (error) => {
          this.assigningVideoId.set(null);
          this.handleHttpError(error, 'admin.assignmentSaveFailed');
        },
      });
  }

  openVideoDebug(videoId: string): void {
    this.router.navigate(['/admin/videos', videoId]);
  }

  onCalibrationFileSelected(event: Event, slot: 'A' | 'B'): void {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0] ?? null;
    const objectUrl = file ? URL.createObjectURL(file) : null;

    if (slot === 'A') {
      this.revokePreviewUrl(this.calibrationPreviewA());
      this.calibrationFileA.set(file);
      this.calibrationFileNameA.set(file?.name ?? '');
      this.calibrationPreviewA.set(objectUrl);
    } else {
      this.revokePreviewUrl(this.calibrationPreviewB());
      this.calibrationFileB.set(file);
      this.calibrationFileNameB.set(file?.name ?? '');
      this.calibrationPreviewB.set(objectUrl);
    }

    this.calibrationResult.set(null);
    this.successMessage.set('');
    this.errorMessage.set('');
  }

  canCompareFaces(): boolean {
    return !!this.calibrationFileA() && !!this.calibrationFileB();
  }

  compareFaces(): void {
    const fileA = this.calibrationFileA();
    const fileB = this.calibrationFileB();
    if (!fileA || !fileB) {
      this.errorMessage.set(this.i18n.t('admin.faceCalibrationFilesRequired'));
      return;
    }

    const formData = new FormData();
    formData.append('file1', fileA);
    formData.append('file2', fileB);

    this.comparingFaces.set(true);
    this.successMessage.set('');
    this.errorMessage.set('');

    this.http
      .post<FaceComparisonResponse>('/api/admin/compare-faces', formData, {
        headers: this.auth.authHeaders(),
      })
      .subscribe({
        next: (response) => {
          this.comparingFaces.set(false);
          this.calibrationResult.set(response);
          this.calibrationThresholds.set({ ...response.thresholds });
          this.successMessage.set(this.i18n.t('admin.faceComparisonReady'));
        },
        error: (error) => {
          this.comparingFaces.set(false);
          this.handleHttpError(error, 'admin.faceComparisonFailed');
        },
      });
  }

  updateCalibrationThreshold(key: 'min_similarity' | 'min_margin', value: unknown): void {
    const numericValue = Number(value);
    if (!Number.isFinite(numericValue)) {
      return;
    }
    this.calibrationThresholds.update((current) => ({
      ...current,
      [key]: numericValue,
    }));
  }

  saveCalibrationAsSystemConfig(): void {
    this.saveConfigValues({
      min_similarity: this.calibrationThresholds().min_similarity,
      min_margin: this.calibrationThresholds().min_margin,
    });
  }

  saveSystemConfig(): void {
    this.saveConfigValues(this.systemConfigDraft());
  }

  updateSystemConfigField(key: SystemConfigKey, value: unknown): void {
    const numericValue = Number(value);
    if (!Number.isFinite(numericValue)) {
      return;
    }
    this.systemConfigDraft.update((current) => ({
      ...current,
      [key]: numericValue,
    }));
  }

  formatTimestamp(value: string): string {
    return this.i18n.formatDateTime(value);
  }

  formatMetric(value: number | null | undefined, digits = 3): string {
    if (value === null || value === undefined || !Number.isFinite(value)) {
      return this.i18n.t('common.notAvailable');
    }
    return value.toFixed(digits);
  }

  formatInteger(value: number | null | undefined): string {
    if (value === null || value === undefined || !Number.isFinite(value)) {
      return this.i18n.t('common.notAvailable');
    }
    return String(Math.round(value));
  }

  formatPercent(value: number | null | undefined): string {
    if (value === null || value === undefined || !Number.isFinite(value)) {
      return this.i18n.t('common.notAvailable');
    }
    return `${Number(value).toFixed(1)}%`;
  }

  formatSeconds(value: number | null | undefined): string {
    if (value === null || value === undefined || !Number.isFinite(value)) {
      return this.i18n.t('common.notAvailable');
    }
    return `${Number(value).toFixed(2)}s`;
  }

  calibrationVerdictLabel(): string {
    const verdict = this.calibrationVerdict();
    if (verdict === 'match') {
      return this.i18n.t('admin.match');
    }
    if (verdict === 'no_match') {
      return this.i18n.t('admin.noMatch');
    }
    return this.i18n.t('common.notAvailable');
  }

  calibrationDecisionExplanation(): string {
    if (!this.calibrationResult()) {
      return this.i18n.t('admin.faceCalibrationEmpty');
    }
    if (!this.calibrationPreviewPassesSimilarity()) {
      return 'Match rejected because similarity < threshold';
    }
    if (!this.calibrationPreviewPassesMarginEstimate()) {
      return 'Match rejected because margin too small';
    }
    if (this.calibrationEstimatedMargin() === null) {
      return 'Match accepted. Margin was not needed because there was no competing identity in this calibration check.';
    }
    return 'Match accepted because similarity and margin both passed';
  }

  calibrationGlobalImpactWarning(): string {
    return 'Changes here affect ALL system matching';
  }

  calibrationLiveThresholdsLabel(): string {
    return 'System Thresholds (LIVE)';
  }

  calibrationThresholdPreviewTitle(): string {
    return 'Threshold preview';
  }

  calibrationPreviewHint(): string {
    return 'Slider changes recompute the verdict instantly using the same similarity and estimated margin rules.';
  }

  roleLabel(role: AdminUser['role']): string {
    return this.i18n.t(role === 'admin' ? 'common.role.admin' : 'common.role.user');
  }

  videoStatusLabel(status: VideoStatus): string {
    return this.i18n.t(`admin.status.${status}` as Parameters<I18nService['t']>[0]);
  }

  pipelineStageClass(status: PipelineStageState | undefined): string {
    return status ?? 'pending';
  }

  pipelineStageStatusLabel(status: PipelineStageState | undefined): string {
    return this.i18n.t(`admin.stageStatus.${status ?? 'pending'}` as Parameters<I18nService['t']>[0]);
  }

  hasVideoTimings(video: AdminVideo): boolean {
    const timings = video.stage_timings;
    if (!timings) {
      return false;
    }
    return Object.values(timings).some((value) => value !== null && value !== undefined);
  }

  videoOutcomeSummary(video: AdminVideo): string {
    if (video.status === 'failed') {
      return this.i18n.t('admin.finalResultFailed');
    }
    if (video.status !== 'completed') {
      return this.i18n.t('admin.finalResultPending');
    }
    if ((video.tracks_matched ?? 0) > 0) {
      return this.i18n.t('admin.finalResultMatched', {
        count: video.tracks_matched ?? 0,
        matches: video.matches_count ?? 0,
      });
    }
    return this.i18n.t('admin.finalResultUnmatched', {
      count: video.tracks_unmatched ?? 0,
    });
  }

  qualityGuardSummary(video: AdminVideo): string {
    const rejectionCounts = video.quality_guard?.rejection_counts ?? {};
    const parts = Object.entries(rejectionCounts)
      .filter(([, count]) => Number(count) > 0)
      .map(([reason, count]) => `${reason.replace(/_/g, ' ')} ${count}`);
    if (parts.length === 0) {
      return '';
    }
    return this.i18n.t('admin.qualityGuardSummary', {
      count: video.tracks_rejected ?? 0,
      reasons: parts.join(', '),
    });
  }

  isUploadedStage(status: VideoStatus): boolean {
    return status === 'uploaded' || status === 'processing' || status === 'completed' || status === 'failed';
  }

  isProcessingStage(status: VideoStatus): boolean {
    return status === 'processing' || status === 'completed' || status === 'failed';
  }

  private ensureActivePoolSynced(nextAction: () => void): void {
    if (!this.selectedPoolId) {
      this.errorMessage.set(this.i18n.t('admin.selectPoolToContinue'));
      this.activeTab.set('pools');
      return;
    }

    if (this.selectedPoolId === (this.auth.poolId() ?? '')) {
      nextAction();
      return;
    }

    this.saveActivePool(nextAction);
  }

  private fetchVideos(includeDebug: boolean) {
    return this.http.get<AdminVideo[]>(`/api/admin/videos?include_debug=${includeDebug}`, {
      headers: this.auth.authHeaders(),
    });
  }

  private fetchSystemConfig() {
    return this.http.get<SystemConfigValues>('/api/admin/config', {
      headers: this.auth.authHeaders(),
    });
  }

  private syncAssignmentSelections(videos: AdminVideo[]): void {
    this.assignmentSelections.set(
      Object.fromEntries(videos.map((video) => [video.video_id, video.assigned_user_id ?? ''])),
    );
  }

  private upsertVideo(video: AdminVideo): void {
    const merge = (existing: AdminVideo[]) => {
      const next = [...existing];
      const index = next.findIndex((item) => item.video_id === video.video_id);
      if (index >= 0) {
        next[index] = { ...next[index], ...video };
      } else {
        next.unshift(video);
      }
      return next;
    };

    this.videos.update(merge);
    this.debugVideos.update(merge);
  }

  private updateVideoStatusLocally(videoId: string, status: VideoStatus): void {
    const update = (items: AdminVideo[]) =>
      items.map((video) =>
        video.video_id === videoId
          ? {
              ...video,
              status,
              updated_at: new Date().toISOString(),
            }
          : video,
      );

    this.videos.update(update);
    this.debugVideos.update(update);
  }

  private syncPollingState(): void {
    const hasPendingVideos = [...this.videos(), ...this.debugVideos()].some(
      (video) => video.status === 'uploaded' || video.status === 'processing',
    );

    if (hasPendingVideos || this.uploadingVideo() || !!this.processingVideoId()) {
      this.startPolling();
      return;
    }

    this.stopPolling();
  }

  private startPolling(): void {
    if (this.pollHandle !== null) {
      this.isPolling.set(true);
      return;
    }

    this.isPolling.set(true);
    this.pollHandle = window.setInterval(() => this.pollVideoState(), 4000);
  }

  private stopPolling(): void {
    if (this.pollHandle !== null) {
      window.clearInterval(this.pollHandle);
      this.pollHandle = null;
    }
    this.isPolling.set(false);
  }

  private pollVideoState(): void {
    if (!this.selectedPoolId || this.pollInFlight) {
      return;
    }

    this.pollInFlight = true;
    forkJoin({
      videos: this.fetchVideos(false),
      debugVideos: this.fetchVideos(true),
      metrics: this.http.get<AdminMetricsResponse>('/api/admin/metrics', {
        headers: this.auth.authHeaders(),
      }),
    }).subscribe({
      next: ({ videos, debugVideos, metrics }) => {
        this.videos.set(videos);
        this.debugVideos.set(debugVideos);
        this.metrics.set(metrics);
        this.syncAssignmentSelections(videos);
        this.pollInFlight = false;
        this.syncPollingState();
      },
      error: (error) => {
        this.pollInFlight = false;
        this.handleHttpError(error, 'admin.loadVideosFailed');
        this.stopPolling();
      },
    });
  }

  private handleHttpError(error: any, fallbackKey: Parameters<I18nService['t']>[0]): void {
    this.loading.set(false);
    this.errorMessage.set(this.i18n.translateApiMessage(error?.error?.detail, fallbackKey));
    if (error?.status === 401) {
      this.auth.clearSession();
      this.router.navigate(['/login']);
    }
  }

  private applySystemConfig(config: SystemConfigValues): void {
    this.systemConfig.set({ ...config });
    this.systemConfigDraft.set({ ...config });
    this.calibrationThresholds.set({
      min_similarity: config.min_similarity,
      min_margin: config.min_margin,
    });
  }

  private validateConfigValues(values: Partial<SystemConfigValues>): string | null {
    for (const field of this.systemConfigFields) {
      if (!(field.key in values)) {
        continue;
      }

      const rawValue = values[field.key];
      const numericValue = Number(rawValue);
      if (!Number.isFinite(numericValue)) {
        return `${field.label} must be numeric`;
      }
      if (numericValue < field.min || numericValue > field.max) {
        return `${field.label} must be between ${field.min} and ${field.max}`;
      }
      if (field.type === 'int' && !Number.isInteger(numericValue)) {
        return `${field.label} must be a whole number`;
      }
    }

    return null;
  }

  private saveConfigValues(values: Partial<SystemConfigValues>): void {
    const validationError = this.validateConfigValues(values);
    if (validationError) {
      this.errorMessage.set(validationError);
      return;
    }

    this.savingSystemConfig.set(true);
    this.successMessage.set('');
    this.errorMessage.set('');

    this.http
      .put<SystemConfigValues>('/api/admin/config', values, {
        headers: this.auth.authHeaders(),
      })
      .subscribe({
        next: (response) => {
          this.savingSystemConfig.set(false);
          this.applySystemConfig(response);
          this.successMessage.set(this.i18n.t('admin.systemConfigSaved'));
          this.loadPoolData();
        },
        error: (error) => {
          this.savingSystemConfig.set(false);
          this.handleHttpError(error, 'admin.saveSystemConfigFailed');
        },
      });
  }

  private revokePreviewUrl(value: string | null): void {
    if (value) {
      URL.revokeObjectURL(value);
    }
  }
}
