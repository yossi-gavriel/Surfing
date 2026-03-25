import { CommonModule } from '@angular/common';
import { HttpClient } from '@angular/common/http';
import { Component, inject, signal } from '@angular/core';
import { ActivatedRoute, Router } from '@angular/router';

import { AuthService } from '../../core/auth.service';
import { I18nService } from '../../core/i18n.service';

interface DebugReferenceImage {
  user_embedding_id: string;
  user_id?: string;
  user_email?: string | null;
  image_url: string | null;
  created_at: string;
}

interface DebugVideoFrame {
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

interface DebugCompareResponse {
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

@Component({
  selector: 'app-admin-video-debug-page',
  standalone: true,
  imports: [CommonModule],
  template: `
    <section class="header-card">
      <div class="header-copy">
        <button class="back" type="button" (click)="goBack()">{{ i18n.t('admin.debug.back') }}</button>
        <p class="eyebrow">{{ i18n.t('admin.debug.eyebrow') }}</p>
        <h2>{{ debugData()?.video_id || videoId }}</h2>
        <p class="subcopy">{{ i18n.t('admin.debug.subtitle') }}</p>
      </div>
      <button type="button" (click)="loadDebug()" [disabled]="loading()">
        {{ loading() ? i18n.t('common.refreshing') : i18n.t('common.refresh') }}
      </button>
    </section>

    <section class="feedback error" *ngIf="errorMessage()">{{ errorMessage() }}</section>

    <section class="state-card" *ngIf="loading()">{{ i18n.t('admin.debug.loading') }}</section>

    <section class="debug-layout" *ngIf="!loading() && debugData() as debug">
      <article class="panel reference-panel">
        <div class="panel-header">
          <div>
            <p class="panel-label">{{ i18n.t('admin.debug.referenceImage') }}</p>
            <h3>{{ i18n.t('admin.debug.bestReferenceImage') }}</h3>
          </div>
          <div class="pill-group">
            <span class="pill">{{ i18n.t('admin.debug.poolUsers', { count: debug.pool_users ?? 0 }) }}</span>
            <span class="pill">{{ i18n.t('admin.debug.userEmbeddings', { count: debug.user_embeddings }) }}</span>
            <span class="pill">{{ i18n.t('admin.debug.videoEmbeddings', { count: debug.video_embeddings }) }}</span>
            <span class="pill">{{ i18n.t('admin.debug.threshold', { value: formatMetric(debug.threshold) }) }}</span>
            <span class="pill">{{ i18n.t('admin.debug.frames', { count: debug.summary.total_frames }) }}</span>
            <span class="pill">{{ i18n.t('admin.debug.valid', { count: debug.summary.valid_frames }) }}</span>
            <span class="pill">Tracks {{ debug.summary.tracks ?? debug.video_frames.length }}</span>
            <span class="pill">Used {{ debug.summary.used_frames ?? 0 }}</span>
            <span class="pill">Matched {{ debug.summary.matched_tracks ?? 0 }}</span>
            <span class="pill">Rejected {{ debug.summary.rejected_tracks ?? 0 }}</span>
            <span class="pill">Low sim {{ debug.summary.rejected_low_similarity ?? 0 }}</span>
            <span class="pill">Low margin {{ debug.summary.rejected_low_margin ?? 0 }}</span>
          </div>
        </div>

        <img
          *ngIf="debug.best_reference_image_url; else noReferenceImage"
          [src]="debug.best_reference_image_url"
          alt="Reference face"
          class="reference-image"
        />

        <ng-template #noReferenceImage>
          <div class="placeholder">{{ i18n.t('admin.debug.noReferenceImage') }}</div>
        </ng-template>
      </article>

      <article class="panel">
        <div class="panel-header">
          <div>
            <p class="panel-label">{{ i18n.t('admin.debug.referenceImage') }}</p>
            <h3>{{ i18n.t('admin.debug.referenceGallery') }}</h3>
          </div>
          <span class="pill">{{ debug.reference_images.length }}</span>
        </div>

        <div class="empty" *ngIf="debug.reference_images.length === 0">{{ i18n.t('admin.debug.noReferenceImage') }}</div>

        <div class="reference-grid" *ngIf="debug.reference_images.length > 0">
          <article class="reference-card" *ngFor="let reference of debug.reference_images">
            <img *ngIf="reference.image_url; else missingReference" [src]="reference.image_url" alt="Reference image" class="frame-image" />
            <ng-template #missingReference>
              <div class="placeholder frame-placeholder">{{ i18n.t('common.imageUnavailable') }}</div>
            </ng-template>
            <div class="frame-body">
              <strong>{{ reference.user_email || reference.user_id || reference.user_embedding_id }}</strong>
              <small class="time">{{ formatTimestamp(reference.created_at) }}</small>
            </div>
          </article>
        </div>
      </article>

      <article class="panel">
        <div class="panel-header">
          <div>
            <p class="panel-label">{{ i18n.t('admin.debug.matchesTitle') }}</p>
            <h3>{{ i18n.t('admin.debug.confirmedMatches') }}</h3>
          </div>
          <span class="pill">{{ debug.matches?.length ?? 0 }}</span>
        </div>

        <div class="empty" *ngIf="(debug.matches?.length ?? 0) === 0">{{ i18n.t('admin.debug.noConfirmedMatches') }}</div>

        <div class="match-list" *ngIf="(debug.matches?.length ?? 0) > 0">
          <article class="result-row" *ngFor="let match of debug.matches">
            <strong>{{ match.email }}</strong>
            <span>{{ i18n.t('admin.debug.scoreLabel', { value: formatMetric(match.score) }) }}</span>
            <span>{{ i18n.t('admin.debug.confidenceLabel', { value: formatMetric(match.confidence) }) }}</span>
            <span>{{ i18n.t('admin.debug.distance', { value: formatMetric(match.distance) }) }}</span>
            <span *ngIf="match.margin !== undefined && match.margin !== null">Margin {{ formatMetric(match.margin) }}</span>
            <span *ngIf="match.threshold_used !== undefined && match.threshold_used !== null">Threshold {{ formatMetric(match.threshold_used) }}</span>
            <small *ngIf="match.decision_explanation">{{ match.decision_explanation }}</small>
          </article>
        </div>
      </article>

      <article class="panel">
        <div class="panel-header">
          <div>
            <p class="panel-label">{{ i18n.t('admin.debug.videoFrames') }}</p>
            <h3>{{ i18n.t('admin.debug.videoEmbeddingsPanel') }}</h3>
          </div>
          <span class="pill">{{ debug.video_frames.length }}</span>
        </div>

        <div class="empty" *ngIf="debug.video_frames.length === 0">{{ i18n.t('admin.debug.noVideoEmbeddings') }}</div>

        <div class="frames-grid" *ngIf="debug.video_frames.length > 0">
          <article class="frame-card" *ngFor="let frame of debug.video_frames">
            <img
              *ngIf="frame.keyframe_url; else missingKeyframe"
              [src]="frame.keyframe_url || ''"
              alt="Video embedding keyframe"
              class="frame-image"
            />
            <ng-template #missingKeyframe>
              <div class="placeholder frame-placeholder">{{ i18n.t('common.imageUnavailable') }}</div>
            </ng-template>

            <div class="frame-body">
              <div class="frame-meta">
                <strong>{{ frame.track_id }}</strong>
                <span class="status" [class.matched]="frame.is_match_under_threshold" [class.unmatched]="!frame.is_match_under_threshold">
                  {{ frame.is_match_under_threshold ? i18n.t('admin.debug.underThreshold') : i18n.t('admin.debug.overThreshold') }}
                </span>
              </div>

              <div class="metrics">
                <span>{{ i18n.t('admin.debug.distance', { value: formatNullableMetric(frame.distance) }) }}</span>
                <span>{{ i18n.t('admin.debug.similarity', { value: formatNullableMetric(frame.similarity) }) }}</span>
                <span *ngIf="frame.frames_count !== undefined">Frames {{ frame.frames_count }}</span>
                <span *ngIf="frame.embeddings_count !== undefined">Embeddings {{ frame.embeddings_count }}</span>
                <span *ngIf="frame.frames_received !== undefined">Received {{ frame.frames_received }}</span>
                <span *ngIf="frame.embeddings_created !== undefined">Eligible {{ frame.embeddings_created }}</span>
                <span *ngIf="frame.quality_avg !== undefined">Quality {{ formatNullableMetric(frame.quality_avg ?? null) }}</span>
                <span *ngIf="frame.consistency !== undefined">Consistency {{ formatNullableMetric(frame.consistency ?? null) }}</span>
                <span *ngIf="frame.second_best_similarity !== undefined">2nd {{ formatNullableMetric(frame.second_best_similarity ?? null) }}</span>
                <span *ngIf="frame.margin !== undefined">Margin {{ formatNullableMetric(frame.margin ?? null) }}</span>
                <span *ngIf="frame.decision">Decision {{ frame.decision }}</span>
                <span *ngIf="frame.best_user_email">{{ i18n.t('admin.debug.bestUser', { email: frame.best_user_email }) }}</span>
              </div>

              <small class="time">{{ formatRange(frame.start_time ?? null, frame.end_time ?? null) }}</small>
              <small class="time" *ngIf="frame.aggregation_method">{{ frame.aggregation_method }}</small>
              <small class="time" *ngIf="frame.used_frame_indexes?.length">Used frames: {{ frame.used_frame_indexes?.join(', ') }}</small>
              <small class="time" *ngIf="frame.threshold_used !== undefined && frame.threshold_used !== null">
                Threshold {{ formatNullableMetric(frame.threshold_used) }} / Margin threshold {{ formatNullableMetric(frame.margin_threshold_used ?? null) }}
              </small>
              <small class="time" *ngIf="frame.decision_reason">
                Reason: {{ frame.decision_reason }}
              </small>
              <small class="time" *ngIf="frame.decision_explanation">{{ frame.decision_explanation }}</small>
              <small class="time" *ngIf="frame.last_attempt_outcome">
                Last attempt: {{ frame.last_attempt_outcome }}{{ frame.last_attempt_at ? ' at ' + formatTimestamp(frame.last_attempt_at) : '' }}
              </small>
            </div>
          </article>
        </div>
      </article>

      <article class="panel">
        <div class="panel-header">
          <div>
            <p class="panel-label">{{ i18n.t('admin.debug.distancesTitle') }}</p>
            <h3>{{ i18n.t('admin.debug.bestComparisons') }}</h3>
          </div>
          <span class="pill">{{ debug.comparisons?.length ?? 0 }}</span>
        </div>

        <div class="empty" *ngIf="(debug.comparisons?.length ?? 0) === 0">{{ i18n.t('admin.debug.noComparisons') }}</div>

        <div class="match-list" *ngIf="(debug.comparisons?.length ?? 0) > 0">
          <article class="result-row" *ngFor="let comparison of (debug.comparisons || []).slice(0, 12)">
            <strong>{{ comparison.user_email }}</strong>
            <span>{{ comparison.video_embedding_id }}</span>
            <span>{{ i18n.t('admin.debug.distance', { value: formatMetric(comparison.distance) }) }}</span>
            <span>{{ i18n.t('admin.debug.similarity', { value: formatMetric(comparison.similarity) }) }}</span>
          </article>
        </div>
      </article>

      <article class="panel frames-panel">
        <div class="panel-header">
          <div>
            <p class="panel-label">{{ i18n.t('admin.debug.videoFrames') }}</p>
            <h3>{{ i18n.t('admin.debug.allFrames') }}</h3>
          </div>
        </div>

        <div class="summary-row">
          <span class="pill">{{ i18n.t('admin.debug.bestSimilarity', { value: formatNullableMetric(debug.summary.best_similarity) }) }}</span>
          <span class="pill">{{ i18n.t('admin.debug.bestDistance', { value: formatNullableMetric(debug.summary.best_distance) }) }}</span>
          <span class="pill" *ngIf="debug.best_match_user_email">{{ i18n.t('admin.debug.bestUser', { email: debug.best_match_user_email }) }}</span>
          <span class="pill" *ngIf="debug.assigned_user_email">{{ i18n.t('admin.debug.assigned', { email: debug.assigned_user_email }) }}</span>
          <span class="pill" [class.force]="debug.summary.force_match">
            {{ i18n.t('admin.debug.forceMatch', { value: debug.summary.force_match ? i18n.t('common.yes') : i18n.t('common.no') }) }}
          </span>
        </div>

        <div class="empty" *ngIf="debug.debug_frames.length === 0">{{ i18n.t('admin.debug.noFrames') }}</div>

        <div class="frames-grid" *ngIf="debug.debug_frames.length > 0">
          <article class="frame-card" *ngFor="let frame of debug.debug_frames" [class.used]="frame.used_for_embedding">
            <img
              *ngIf="frame.image_url || frame.keyframe_url; else noFrameImage"
              [src]="frame.image_url || frame.keyframe_url || ''"
              alt="Video frame"
              class="frame-image"
            />

            <ng-template #noFrameImage>
              <div class="placeholder frame-placeholder">{{ i18n.t('common.imageUnavailable') }}</div>
            </ng-template>

            <div class="frame-body">
              <div class="frame-meta">
                <strong>{{ frame.track_id }} / #{{ frame.frame_index ?? 0 }}</strong>
                <span class="status" [class.matched]="frame.is_match_under_threshold" [class.unmatched]="!frame.is_match_under_threshold">
                  {{ frame.is_match_under_threshold ? i18n.t('admin.debug.underThreshold') : i18n.t('admin.debug.overThreshold') }}
                </span>
              </div>

              <div class="metrics">
                <span>{{ i18n.t('admin.debug.distance', { value: formatNullableMetric(frame.distance) }) }}</span>
                <span>{{ i18n.t('admin.debug.similarity', { value: formatNullableMetric(frame.similarity) }) }}</span>
                <span>Quality {{ formatNullableMetric(frame.quality_score ?? null) }}</span>
                <span *ngIf="frame.det_score !== undefined">Det {{ formatNullableMetric(frame.det_score ?? null) }}</span>
                <span *ngIf="frame.face_size !== undefined">Face {{ formatNullableMetric(frame.face_size ?? null, 0) }}</span>
                <span *ngIf="frame.blur_score !== undefined">Blur {{ formatNullableMetric(frame.blur_score ?? null) }}</span>
                <span *ngIf="frame.user_email">{{ i18n.t('admin.debug.user', { email: frame.user_email }) }}</span>
                <span>{{ i18n.t('admin.debug.hasFace', { value: boolLabel(frame.has_face) }) }}</span>
                <span>{{ i18n.t('admin.debug.validLabel', { value: boolLabel(frame.is_valid) }) }}</span>
                <span>{{ i18n.t('admin.debug.used', { value: boolLabel(frame.used_for_embedding) }) }}</span>
              </div>

              <small class="time" *ngIf="frame.frame_timestamp || frame.start_time || frame.end_time">
                {{ frame.frame_timestamp ? formatTimestamp(frame.frame_timestamp) : formatRange(frame.start_time ?? null, frame.end_time ?? null) }}
              </small>

              <small class="time" *ngIf="frame.face_bbox?.length">
                {{ i18n.t('admin.debug.faceBbox', { value: formatBbox(frame.face_bbox || null) }) }}
              </small>

              <small class="time" *ngIf="frame.bbox?.length">
                {{ i18n.t('admin.debug.trackBbox', { value: formatBbox(frame.bbox || null) }) }}
              </small>

              <small class="time" *ngIf="frame.rejection_reason">
                Rejection: {{ frame.rejection_reason }}
              </small>
            </div>
          </article>
        </div>
      </article>
    </section>
  `,
  styles: [`
    :host {
      display: block;
    }

    .header-card,
    .debug-layout {
      display: grid;
      gap: 1.25rem;
    }

    .header-card {
      grid-template-columns: 1fr auto;
      align-items: end;
      margin-bottom: 1.5rem;
    }

    .debug-layout {
      grid-template-columns: 1fr;
    }

    .header-copy {
      display: grid;
      gap: 0.65rem;
    }

    .eyebrow,
    .panel-label {
      margin: 0;
      text-transform: uppercase;
      letter-spacing: 0.18em;
      color: var(--accent-deep);
      font-size: 0.78rem;
    }

    h2,
    h3 {
      margin: 0;
      color: var(--ink-strong);
      font-family: 'Space Grotesk', sans-serif;
    }

    h2 {
      font-size: 2.2rem;
      overflow-wrap: anywhere;
    }

    h3 {
      font-size: 1.4rem;
    }

    .subcopy {
      margin: 0;
      color: var(--ink-soft);
      line-height: 1.7;
      max-width: 64ch;
    }

    .panel,
    .feedback,
    .state-card {
      border-radius: 28px;
      border: 1px solid rgba(20, 60, 68, 0.12);
      background: rgba(255, 252, 245, 0.9);
      box-shadow: 0 24px 60px rgba(13, 40, 45, 0.08);
    }

    .panel,
    .feedback,
    .state-card {
      padding: 1.5rem;
    }

    .feedback.error {
      color: #a7341b;
      margin-bottom: 1rem;
    }

    .panel-header,
    .frame-meta {
      display: flex;
      justify-content: space-between;
      gap: 0.75rem;
      align-items: center;
    }

    .pill-group,
    .metrics {
      display: flex;
      flex-wrap: wrap;
      gap: 0.45rem;
    }

    .pill,
    .metrics span,
    .status {
      border-radius: 999px;
      padding: 0.32rem 0.7rem;
      font-size: 0.8rem;
    }

    .pill,
    .metrics span {
      background: rgba(20, 82, 96, 0.08);
      color: var(--ink-soft);
    }

    .reference-image,
    .frame-image,
    .placeholder {
      width: 100%;
      border-radius: 24px;
      background: linear-gradient(135deg, rgba(193, 230, 223, 0.78), rgba(255, 238, 210, 0.82));
    }

    .reference-image {
      margin-top: 1rem;
      max-height: 420px;
      object-fit: contain;
    }

    .frames-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
      gap: 1rem;
      margin-top: 1rem;
    }

    .reference-grid,
    .match-list {
      display: grid;
      gap: 1rem;
      margin-top: 1rem;
    }

    .reference-grid {
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
    }

    .summary-row {
      display: flex;
      flex-wrap: wrap;
      gap: 0.45rem;
      margin-top: 1rem;
    }

    .frame-card {
      overflow: hidden;
      border-radius: 24px;
      background: rgba(255, 255, 255, 0.8);
      border: 1px solid rgba(20, 60, 68, 0.08);
    }

    .reference-card,
    .result-row {
      border-radius: 24px;
      background: rgba(255, 255, 255, 0.8);
      border: 1px solid rgba(20, 60, 68, 0.08);
    }

    .result-row {
      padding: 1rem;
      display: grid;
      gap: 0.45rem;
      color: var(--ink-soft);
    }

    .result-row strong {
      color: var(--ink-strong);
      overflow-wrap: anywhere;
    }

    .frame-card.used {
      border-color: rgba(29, 109, 79, 0.4);
      box-shadow: inset 0 0 0 1px rgba(29, 109, 79, 0.2);
    }

    .frame-image,
    .frame-placeholder {
      height: 220px;
      object-fit: cover;
    }

    .placeholder {
      display: grid;
      place-items: center;
      min-height: 220px;
      color: var(--ink-soft);
      text-align: center;
      padding: 1rem;
    }

    .frame-body {
      padding: 1rem;
      display: grid;
      gap: 0.65rem;
    }

    .frame-meta strong {
      overflow-wrap: anywhere;
      color: var(--ink-strong);
    }

    .status {
      text-transform: lowercase;
    }

    .status.matched {
      background: rgba(29, 109, 79, 0.14);
      color: #1d6d4f;
    }

    .status.unmatched {
      background: rgba(167, 52, 27, 0.12);
      color: #a7341b;
    }

    .pill.force {
      background: rgba(167, 52, 27, 0.12);
      color: #a7341b;
    }

    .time,
    .empty {
      color: var(--ink-soft);
    }

    button,
    .back {
      border: none;
      border-radius: 999px;
      padding: 0.85rem 1.15rem;
      background: linear-gradient(135deg, var(--accent-deep), var(--accent));
      color: white;
      font: inherit;
      font-weight: 600;
      cursor: pointer;
    }

    .back {
      justify-self: start;
      width: fit-content;
      background: rgba(20, 60, 68, 0.08);
      color: var(--ink-strong);
    }

    button:disabled {
      opacity: 0.7;
      cursor: wait;
    }

    @media (max-width: 900px) {
      .header-card,
      .panel-header,
      .frame-meta {
        grid-template-columns: 1fr;
        flex-direction: column;
        align-items: stretch;
      }
    }
  `],
})
export class AdminVideoDebugComponent {
  private readonly http = inject(HttpClient);
  private readonly auth = inject(AuthService);
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);
  protected readonly i18n = inject(I18nService);

  readonly loading = signal(false);
  readonly errorMessage = signal('');
  readonly debugData = signal<DebugCompareResponse | null>(null);

  readonly videoId = this.route.snapshot.paramMap.get('videoId') ?? '';

  constructor() {
    this.loadDebug();
  }

  loadDebug(): void {
    if (!this.videoId) {
      this.errorMessage.set(this.i18n.t('admin.debug.missingVideoId'));
      return;
    }

    this.loading.set(true);
    this.errorMessage.set('');

    this.http
      .get<DebugCompareResponse>(`/api/admin/debug/compare/${this.videoId}`, {
        headers: this.auth.authHeaders(),
      })
      .subscribe({
        next: (response) => {
          this.debugData.set(response);
          this.loading.set(false);
        },
        error: (error) => {
          this.loading.set(false);
          this.errorMessage.set(this.i18n.translateApiMessage(error?.error?.detail, 'admin.debug.loadFailed'));
          if (error?.status === 401) {
            this.auth.clearSession();
            this.router.navigate(['/login']);
          }
        },
      });
  }

  goBack(): void {
    this.router.navigate(['/admin/results']);
  }

  boolLabel(value: boolean | undefined): string {
    return value ? this.i18n.t('common.yes') : this.i18n.t('common.no');
  }

  formatMetric(value: number, digits = 3): string {
    return Number.isFinite(value) ? value.toFixed(digits) : this.i18n.t('common.notAvailable');
  }

  formatNullableMetric(value: number | null, digits = 3): string {
    return value === null ? this.i18n.t('common.notAvailable') : this.formatMetric(value, digits);
  }

  formatRange(start: string | null, end: string | null): string {
    const startLabel = start ? this.formatTimestamp(start) : this.i18n.t('admin.debug.unknownStart');
    const endLabel = end ? this.formatTimestamp(end) : this.i18n.t('admin.debug.unknownEnd');
    return `${startLabel} - ${endLabel}`;
  }

  formatTimestamp(value: string): string {
    return this.i18n.formatDateTime(value);
  }

  formatBbox(values: number[] | null): string {
    if (!values || values.length !== 4) {
      return this.i18n.t('common.notAvailable');
    }
    return values.map((value) => value.toFixed(0)).join(', ');
  }
}
