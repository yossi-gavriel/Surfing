import { CommonModule } from '@angular/common';
import { HttpClient } from '@angular/common/http';
import { Component, inject, signal } from '@angular/core';
import { ActivatedRoute, Router } from '@angular/router';

import { AuthService } from '../../core/auth.service';

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
  has_face?: boolean;
  used_for_embedding?: boolean;
  is_valid?: boolean;
  hasFaceLabel?: string;
  validLabel?: string;
  usedLabel?: string;
  distance: number | null;
  similarity: number | null;
  is_match_under_threshold: boolean;
}

interface DebugCompareResponse {
  video_id: string;
  user_embeddings: number;
  video_embeddings: number;
  pool_id?: string | null;
  pool_users?: number;
  threshold: number;
  best_match_user_id?: string | null;
  best_match_user_email?: string | null;
  best_reference_user_embedding_id: string | null;
  best_reference_image_url: string | null;
  reference_images: DebugReferenceImage[];
  video_frames: DebugVideoFrame[];
  debug_frames: DebugVideoFrame[];
  matches?: Array<{
    user_id: string;
    email: string;
    score: number;
    confidence: number;
    distance: number;
  }>;
  assigned_user_id?: string | null;
  assigned_user_email?: string | null;
  summary: {
    total_frames: number;
    valid_frames: number;
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
        <button class="back" type="button" (click)="goBack()">Back to videos</button>
        <p class="eyebrow">Video debug</p>
        <h2>{{ debugData()?.video_id || videoId }}</h2>
        <p class="subcopy">
          Best pool reference image on top, then every stored video frame ranked against the active pool.
        </p>
      </div>
      <button type="button" (click)="loadDebug()" [disabled]="loading()">
        {{ loading() ? 'Refreshing...' : 'Refresh' }}
      </button>
    </section>

    <section class="feedback error" *ngIf="errorMessage()">{{ errorMessage() }}</section>

    <section class="state-card" *ngIf="loading()">Loading debug data...</section>

    <section class="debug-layout" *ngIf="!loading() && debugData() as debug">
      <article class="panel reference-panel">
        <div class="panel-header">
          <div>
            <p class="panel-label">Reference image</p>
            <h3>Best reference image</h3>
          </div>
          <div class="pill-group">
            <span class="pill">Pool users {{ debug.pool_users ?? 0 }}</span>
            <span class="pill">User embeddings {{ debug.user_embeddings }}</span>
            <span class="pill">Video embeddings {{ debug.video_embeddings }}</span>
            <span class="pill">Threshold {{ formatMetric(debug.threshold) }}</span>
            <span class="pill">Frames {{ debug.summary.total_frames }}</span>
            <span class="pill">Valid {{ debug.summary.valid_frames }}</span>
          </div>
        </div>

        <img
          *ngIf="debug.best_reference_image_url; else noReferenceImage"
          [src]="debug.best_reference_image_url"
          alt="Reference face"
          class="reference-image"
        />

        <ng-template #noReferenceImage>
          <div class="placeholder">No stored uploaded face image is available yet for this user.</div>
        </ng-template>
      </article>

      <article class="panel frames-panel">
        <div class="panel-header">
          <div>
            <p class="panel-label">Video frames</p>
            <h3>All stored debug frames</h3>
          </div>
        </div>

          <div class="summary-row">
          <span class="pill">Best similarity {{ formatNullableMetric(debug.summary.best_similarity) }}</span>
          <span class="pill">Best distance {{ formatNullableMetric(debug.summary.best_distance) }}</span>
          <span class="pill" *ngIf="debug.best_match_user_email">Best user {{ debug.best_match_user_email }}</span>
          <span class="pill" *ngIf="debug.assigned_user_email">Assigned {{ debug.assigned_user_email }}</span>
          <span class="pill" [class.force]="debug.summary.force_match">Force match {{ debug.summary.force_match ? 'yes' : 'no' }}</span>
        </div>

        <div class="empty" *ngIf="debug.debug_frames.length === 0">
          No stored debug frames were found for this video yet.
        </div>

        <div class="frames-grid" *ngIf="debug.debug_frames.length > 0">
          <article class="frame-card" *ngFor="let frame of debug.debug_frames" [class.used]="frame.used_for_embedding">
            <img *ngIf="frame.image_url || frame.keyframe_url; else noFrameImage" [src]="frame.image_url || frame.keyframe_url || ''" alt="Video frame" class="frame-image" />

            <ng-template #noFrameImage>
              <div class="placeholder frame-placeholder">No frame image available</div>
            </ng-template>

            <div class="frame-body">
              <div class="frame-meta">
                <strong>{{ frame.track_id }} / #{{ frame.frame_index ?? 0 }}</strong>
                <span class="status" [class.matched]="frame.is_match_under_threshold" [class.unmatched]="!frame.is_match_under_threshold">
                  {{ frame.is_match_under_threshold ? 'under threshold' : 'over threshold' }}
                </span>
              </div>

              <div class="metrics">
                <span>Distance {{ formatNullableMetric(frame.distance) }}</span>
                <span>Similarity {{ formatNullableMetric(frame.similarity) }}</span>
                <span *ngIf="frame.user_email">User {{ frame.user_email }}</span>
                <span>Has face {{ frame.hasFaceLabel }}</span>
                <span>Valid {{ frame.validLabel }}</span>
                <span>Used {{ frame.usedLabel }}</span>
              </div>

              <small class="time" *ngIf="frame.frame_timestamp || frame.start_time || frame.end_time">
                {{ frame.frame_timestamp ? formatTimestamp(frame.frame_timestamp) : formatRange(frame.start_time ?? null, frame.end_time ?? null) }}
              </small>

              <small class="time" *ngIf="frame.face_bbox?.length">
                Face bbox {{ formatBbox(frame.face_bbox || null) }}
              </small>

              <small class="time" *ngIf="frame.bbox?.length">
                Track bbox {{ formatBbox(frame.bbox || null) }}
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

  readonly loading = signal(false);
  readonly errorMessage = signal('');
  readonly debugData = signal<DebugCompareResponse | null>(null);

  readonly videoId = this.route.snapshot.paramMap.get('videoId') ?? '';

  constructor() {
    this.loadDebug();
  }

  loadDebug(): void {
    if (!this.videoId) {
      this.errorMessage.set('Missing video id.');
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
          const mappedDebugFrames = response.debug_frames.map((frame) => ({
            ...frame,
            hasFaceLabel: frame.has_face ? 'yes' : 'no',
            validLabel: frame.is_valid ? 'yes' : 'no',
            usedLabel: frame.used_for_embedding ? 'yes' : 'no',
          }));
          this.debugData.set({
            ...response,
            debug_frames: mappedDebugFrames,
          });
          this.loading.set(false);
        },
        error: (error) => {
          this.loading.set(false);
          const detail = error?.error?.detail;
          this.errorMessage.set(detail?.message || detail || 'Unable to load video debug details.');
          if (error?.status === 401) {
            this.auth.clearSession();
            this.router.navigate(['/login']);
          }
        },
      });
  }

  goBack(): void {
    this.router.navigate(['/admin']);
  }

  formatMetric(value: number, digits = 3): string {
    return Number.isFinite(value) ? value.toFixed(digits) : 'n/a';
  }

  formatNullableMetric(value: number | null, digits = 3): string {
    return value === null ? 'n/a' : this.formatMetric(value, digits);
  }

  formatRange(start: string | null, end: string | null): string {
    const startLabel = start ? this.formatTimestamp(start) : 'unknown start';
    const endLabel = end ? this.formatTimestamp(end) : 'unknown end';
    return `${startLabel} - ${endLabel}`;
  }

  formatTimestamp(value: string): string {
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
  }

  formatBbox(values: number[] | null): string {
    if (!values || values.length !== 4) {
      return 'n/a';
    }
    return values.map((value) => value.toFixed(0)).join(', ');
  }
}
