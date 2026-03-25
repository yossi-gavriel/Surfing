import { CommonModule } from '@angular/common';
import { Component, computed, DestroyRef, effect, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { ActivatedRoute, Router } from '@angular/router';

import { I18nService } from '../../core/i18n.service';
import { AdminContextService } from './admin-context.service';
import { AdminDebugCacheService } from './admin-debug-cache.service';
import { DebugCompareResponse, DebugVideoFrame } from './admin-debug.models';
import { AdminResultsService } from './admin-results.service';

@Component({
  selector: 'app-admin-track-page',
  standalone: true,
  imports: [CommonModule],
  host: { class: 'admin-page' },
  template: `
    <section class="header-card">
      <div class="header-copy">
        <button class="back" type="button" (click)="goBack()">Back to results</button>
        <p class="eyebrow">Decision</p>
        <h2>{{ trackId() }}</h2>
        <p class="subcopy">Stable deep link for a single track inside video {{ videoId() }}.</p>
      </div>
      <button type="button" (click)="loadTrack(true)" [disabled]="loading()">
        {{ loading() ? i18n.t('common.refreshing') : i18n.t('common.refresh') }}
      </button>
    </section>

    <section class="feedback error" *ngIf="errorMessage()">{{ errorMessage() }}</section>
    <section class="state-card" *ngIf="loading()">{{ i18n.t('common.loading') }}</section>

    <ng-container *ngIf="!loading() && debugData() as debug">
      <section class="panel" *ngIf="trackSummary() as track; else missingTrack">
        <div class="section-header">
          <div>
            <p class="panel-label">Track summary</p>
            <h3>{{ track.track_id }}</h3>
          </div>
          <span class="status" [class.completed]="track.is_match_under_threshold" [class.failed]="!track.is_match_under_threshold">
            {{ track.decision ?? track.final_verdict ?? 'pending' }}
          </span>
        </div>

        <div class="status-grid">
          <article class="summary-card">
            <span>Similarity</span>
            <strong>{{ formatNullableMetric(track.similarity) }}</strong>
          </article>
          <article class="summary-card">
            <span>Distance</span>
            <strong>{{ formatNullableMetric(track.distance) }}</strong>
          </article>
          <article class="summary-card">
            <span>Margin</span>
            <strong>{{ formatNullableMetric(track.margin ?? track.similarity_margin ?? null) }}</strong>
          </article>
          <article class="summary-card">
            <span>Threshold</span>
            <strong>{{ formatNullableMetric(track.threshold_used ?? debug.threshold) }}</strong>
          </article>
        </div>

        <div class="metrics">
          <span *ngIf="track.best_user_email">Best user {{ track.best_user_email }}</span>
          <span *ngIf="track.frames_count !== undefined">Frames {{ track.frames_count }}</span>
          <span *ngIf="track.embeddings_count !== undefined">Embeddings {{ track.embeddings_count }}</span>
          <span *ngIf="track.quality_avg !== undefined">Quality {{ formatNullableMetric(track.quality_avg ?? null) }}</span>
          <span *ngIf="track.consistency !== undefined">Consistency {{ formatNullableMetric(track.consistency ?? null) }}</span>
        </div>

        <small class="hint" *ngIf="track.decision_reason">Reason: {{ track.decision_reason }}</small>
        <small class="hint" *ngIf="track.decision_explanation">{{ track.decision_explanation }}</small>
      </section>

      <ng-template #missingTrack>
        <section class="panel">
          <p class="empty">Track {{ trackId() }} was not found inside video {{ videoId() }}.</p>
        </section>
      </ng-template>

      <section class="panel">
        <div class="section-header">
          <div>
            <p class="panel-label">Evidence</p>
            <h3>Track frames</h3>
          </div>
          <button type="button" class="secondary" (click)="openVideoDebug()">Open full video debug</button>
        </div>

        <div class="frames-grid" *ngIf="trackFrames().length > 0; else noFrames">
          <article class="frame-card" *ngFor="let frame of trackFrames()">
            <img *ngIf="frame.image_url || frame.keyframe_url; else noFrameImage" [src]="frame.image_url || frame.keyframe_url || ''" alt="Track frame" class="frame-image" />
            <ng-template #noFrameImage>
              <div class="placeholder frame-placeholder">{{ i18n.t('common.imageUnavailable') }}</div>
            </ng-template>

            <div class="frame-body">
              <div class="frame-meta">
                <strong>{{ frame.track_id }} / #{{ frame.frame_index ?? 0 }}</strong>
                <span class="status" [class.completed]="frame.used_for_embedding || frame.used_for_track_embedding" [class.failed]="!(frame.used_for_embedding || frame.used_for_track_embedding)">
                  {{ frame.used_for_embedding || frame.used_for_track_embedding ? 'used' : 'discarded' }}
                </span>
              </div>
              <div class="metrics">
                <span>Similarity {{ formatNullableMetric(frame.similarity) }}</span>
                <span>Distance {{ formatNullableMetric(frame.distance) }}</span>
                <span *ngIf="frame.quality_score !== undefined">Quality {{ formatNullableMetric(frame.quality_score ?? null) }}</span>
              </div>
            </div>
          </article>
        </div>

        <ng-template #noFrames>
          <div class="empty">No frames found for this track.</div>
        </ng-template>
      </section>

      <section class="panel" *ngIf="relatedComparisons().length > 0">
        <div class="section-header">
          <div>
            <p class="panel-label">Comparisons</p>
            <h3>Best comparisons</h3>
          </div>
        </div>

        <div class="match-list">
          <article class="result-row" *ngFor="let comparison of relatedComparisons()">
            <strong>{{ comparison.user_email }}</strong>
            <span>{{ comparison.video_embedding_id }}</span>
            <span>Similarity {{ formatMetric(comparison.similarity) }}</span>
            <span>Distance {{ formatMetric(comparison.distance) }}</span>
          </article>
        </div>
      </section>

      <section class="panel" *ngIf="relatedMatches().length > 0">
        <div class="section-header">
          <div>
            <p class="panel-label">Matches</p>
            <h3>Confirmed outcomes</h3>
          </div>
        </div>

        <div class="match-list">
          <article class="result-row" *ngFor="let match of relatedMatches()">
            <strong>{{ match.email }}</strong>
            <span>Score {{ formatMetric(match.score) }}</span>
            <span>Confidence {{ formatMetric(match.confidence) }}</span>
            <span>Distance {{ formatMetric(match.distance) }}</span>
          </article>
        </div>
      </section>
    </ng-container>
  `,
  styles: [`
    :host {
      display: block;
    }

    .header-copy,
    .header-card {
      display: grid;
      gap: 1rem;
    }

    .header-card {
      display: grid;
      grid-template-columns: 1fr auto;
      align-items: end;
      margin-bottom: 1.5rem;
    }

    .frames-grid,
    .match-list {
      display: grid;
      gap: 1rem;
      margin-top: 1rem;
    }

    .back {
      width: max-content;
      border: none;
      background: transparent;
      color: var(--accent-deep);
      font: inherit;
      cursor: pointer;
      padding: 0;
    }

    .frame-card,
    .result-row {
      display: grid;
      gap: 0.75rem;
      padding: 1rem;
      border-radius: 22px;
      background: rgba(255, 255, 255, 0.82);
      border: 1px solid rgba(20, 60, 68, 0.08);
    }

    .frame-image,
    .placeholder {
      width: 100%;
      border-radius: 20px;
      background: linear-gradient(135deg, rgba(193, 230, 223, 0.78), rgba(255, 238, 210, 0.82));
    }

    .frame-image {
      height: 220px;
      object-fit: cover;
    }

    .frame-placeholder {
      height: 220px;
      display: grid;
      place-items: center;
      color: var(--ink-soft);
    }

    .frame-body,
    .result-row {
      min-width: 0;
    }

    .frame-meta {
      display: flex;
      justify-content: space-between;
      gap: 0.75rem;
      align-items: center;
    }

    @media (max-width: 720px) {
      .header-card {
        grid-template-columns: 1fr;
      }

      .frame-meta {
        flex-direction: column;
        align-items: stretch;
      }
    }
  `],
})
export class AdminTrackDecisionComponent {
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);
  private readonly destroyRef = inject(DestroyRef);
  private readonly context = inject(AdminContextService);
  private readonly debugCache = inject(AdminDebugCacheService);
  private readonly results = inject(AdminResultsService);
  protected readonly i18n = inject(I18nService);

  protected readonly videoId = signal('');
  protected readonly trackId = signal('');
  protected readonly errorMessage = signal('');
  protected readonly loading = computed(() => this.debugCache.isLoading(this.videoId()));
  protected readonly debugData = computed<DebugCompareResponse | null>(() => this.debugCache.response(this.videoId()));
  protected readonly trackSummary = computed(() => {
    const debug = this.debugData();
    const trackId = this.trackId();
    if (!debug) {
      return null;
    }
    return (
      debug.track_summaries?.find((item) => item.track_id === trackId) ??
      debug.video_frames.find((item) => item.track_id === trackId) ??
      debug.debug_frames.find((item) => item.track_id === trackId) ??
      null
    );
  });
  protected readonly trackFrames = computed(() => {
    const debug = this.debugData();
    const trackId = this.trackId();
    if (!debug) {
      return [];
    }
    return debug.debug_frames.filter((item) => item.track_id === trackId);
  });
  protected readonly relatedComparisons = computed(() => {
    const debug = this.debugData();
    const track = this.trackSummary();
    if (!debug?.comparisons?.length) {
      return [];
    }
    if (!track?.video_embedding_id) {
      return debug.comparisons.slice(0, 6);
    }
    return debug.comparisons.filter((item) => item.video_embedding_id === track.video_embedding_id);
  });
  protected readonly relatedMatches = computed(() => {
    const debug = this.debugData();
    const track = this.trackSummary();
    if (!debug?.matches?.length) {
      return [];
    }
    if (!track?.best_user_email) {
      return debug.matches.slice(0, 3);
    }
    return debug.matches.filter((item) => item.email === track.best_user_email);
  });

  constructor() {
    this.route.paramMap.pipe(takeUntilDestroyed(this.destroyRef)).subscribe((params) => {
      const videoId = params.get('videoId') ?? '';
      const trackId = params.get('trackId') ?? '';
      this.videoId.set(videoId);
      this.trackId.set(trackId);
      this.results.selectTrack(videoId, trackId);
      this.loadTrack();
    });

    effect(() => {
      const videoId = this.videoId();
      const trackId = this.trackId();
      const debug = this.debugData();
      const loading = this.loading();
      const cacheError = this.debugCache.error(videoId);
      if (!videoId || !trackId) {
        return;
      }
      if (loading) {
        this.errorMessage.set('');
        return;
      }
      if (cacheError) {
        this.errorMessage.set(cacheError);
        return;
      }
      if (debug && !this.trackSummary()) {
        this.errorMessage.set(`Track ${trackId} was not found for video ${videoId}.`);
        return;
      }
      this.errorMessage.set('');
    });
  }

  protected loadTrack(force = false): void {
    this.errorMessage.set('');
    this.debugCache.ensure(this.videoId(), force);
  }

  protected openVideoDebug(): void {
    this.context.selectVideo(this.videoId());
    this.router.navigate(['/admin/debug', this.videoId()]);
  }

  protected goBack(): void {
    this.router.navigate(['/admin/results']);
  }

  protected formatMetric(value: number, digits = 3): string {
    return value.toFixed(digits);
  }

  protected formatNullableMetric(value: number | null | undefined, digits = 3): string {
    if (value === null || value === undefined || !Number.isFinite(value)) {
      return this.i18n.t('common.notAvailable');
    }
    return value.toFixed(digits);
  }
}
