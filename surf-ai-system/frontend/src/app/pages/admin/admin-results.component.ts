import { CommonModule } from '@angular/common';
import { Component, computed, effect, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { AdminContextService, AdminResultsFilters } from './admin-context.service';
import { AdminResultsService } from './admin-results.service';
import { AdminSystemService } from './admin-system.service';
import { AdminVideosService } from './admin-videos.service';

@Component({
  selector: 'app-admin-results-page',
  standalone: true,
  imports: [CommonModule, FormsModule],
  host: { class: 'admin-page' },
  template: `
    <section class="hero">
      <div>
        <p class="eyebrow">Moderation</p>
        <h2>Review candidate decisions</h2>
        <p class="subcopy">Results load video summaries first and only fetch track summaries when a moderator opens a video.</p>
      </div>

      <div class="hero-actions">
        <button type="button" (click)="refresh()" [disabled]="loading()">
          {{ loading() ? system.i18n.t('common.refreshing') : system.i18n.t('common.refresh') }}
        </button>
      </div>
    </section>

    <section class="feedback error" *ngIf="system.errorMessage()">{{ system.errorMessage() }}</section>
    <section class="feedback success" *ngIf="system.successMessage()">{{ system.successMessage() }}</section>
    <section class="state-card" *ngIf="loading()">{{ system.i18n.t('common.loading') }}</section>

    <section class="panel" *ngIf="!loading()">
      <div class="filters-bar">
        <label>
          <span>Search</span>
          <input
            [ngModel]="context.filters().query"
            (ngModelChange)="updateQuery($event)"
            placeholder="Video ID or candidate email"
          />
        </label>

        <label>
          <span>Video status</span>
          <select [ngModel]="context.filters().videoStatus" (ngModelChange)="updateVideoStatus($event)">
            <option value="all">All</option>
            <option value="uploaded">Uploaded</option>
            <option value="processing">Processing</option>
            <option value="completed">Completed</option>
            <option value="failed">Failed</option>
          </select>
        </label>

        <label>
          <span>Track status</span>
          <select [ngModel]="context.filters().trackStatus" (ngModelChange)="updateTrackStatus($event)">
            <option value="all">All</option>
            <option value="match">Match</option>
            <option value="no_match">No match</option>
            <option value="pending">Pending</option>
          </select>
        </label>
      </div>

      <div class="section-header">
        <div>
          <p class="panel-label">{{ system.i18n.t('admin.debugPrimary') }}</p>
          <h3>{{ system.i18n.t('admin.tabs.debug') }}</h3>
        </div>
        <span class="pill">{{ results.visibleTrackCount() }} tracks visible</span>
      </div>

      <div class="empty" *ngIf="!system.selectedPoolId()">{{ system.i18n.t('admin.selectPoolToContinue') }}</div>
      <div class="empty" *ngIf="system.selectedPoolId() && filteredVideos().length === 0">{{ system.i18n.t('admin.noDebugVideos') }}</div>

      <div class="results-groups" *ngIf="filteredVideos().length > 0">
        <article class="result-group" *ngFor="let video of filteredVideos()" [class.expanded]="results.isExpanded(video.video_id)">
          <div class="video-head" (click)="toggleVideo(video.video_id)">
            <div>
              <strong>{{ video.video_id }}</strong>
              <small>{{ system.i18n.t('admin.createdAt', { value: system.formatTimestamp(video.created_at) }) }}</small>
            </div>
            <div class="video-head-actions">
              <span class="status" [class.completed]="video.status === 'completed'" [class.failed]="video.status === 'failed'">
                {{ system.videoStatusLabel(video.status) }}
              </span>
              <button type="button" class="secondary" (click)="openVideoDebug($event, video.video_id)">Video Debug</button>
              <button type="button" (click)="toggleVideo(video.video_id, $event)">
                {{ results.isExpanded(video.video_id) ? 'Hide tracks' : 'Load tracks' }}
              </button>
            </div>
          </div>

          <div class="metrics">
            <span>Best match {{ video.best_match_user_email || system.i18n.t('common.notAvailable') }}</span>
            <span>Similarity {{ system.formatMetric(video.best_similarity) }}</span>
            <span>Threshold {{ system.formatMetric(video.threshold) }}</span>
            <span>Tracks matched {{ system.formatInteger(video.tracks_matched) }}</span>
          </div>

          <div class="empty" *ngIf="results.isExpanded(video.video_id) && results.loadingTrackSummaries()[video.video_id]">
            Loading track summaries...
          </div>
          <div class="empty" *ngIf="results.isExpanded(video.video_id) && results.trackSummaryErrors()[video.video_id]">
            {{ results.trackSummaryErrors()[video.video_id] }}
          </div>
          <div class="empty" *ngIf="results.isExpanded(video.video_id) && !results.loadingTrackSummaries()[video.video_id] && results.trackSummaries(video.video_id).length === 0">
            No track-level decisions available.
          </div>

          <div class="track-list" *ngIf="results.isExpanded(video.video_id) && results.trackSummaries(video.video_id).length > 0">
            <article class="track-card" *ngFor="let track of results.trackSummaries(video.video_id)" [class.selected]="results.isTrackSelected(track.trackId)" (click)="openTrack(track.videoId, track.trackId)">
              <div>
                <strong>{{ track.trackId }}</strong>
                <small *ngIf="track.userEmail">Best candidate: {{ track.userEmail }}</small>
              </div>
              <div class="metrics">
                <span>Decision {{ track.status }}</span>
                <span>Similarity {{ system.formatMetric(track.similarity) }}</span>
                <span>Margin {{ system.formatMetric(track.margin) }}</span>
                <span *ngIf="track.threshold !== null">Threshold {{ system.formatMetric(track.threshold) }}</span>
              </div>
              <div class="track-actions">
                <button type="button" (click)="openTrack(track.videoId, track.trackId, $event)">Open Track</button>
                <button type="button" class="secondary" (click)="openVideoDebug($event, track.videoId)">Open Video</button>
              </div>
            </article>
          </div>
        </article>
      </div>
    </section>
  `,
  styles: [`
    .filters-bar {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 1rem;
      margin-bottom: 1rem;
    }

    .results-groups,
    .track-list {
      display: grid;
      gap: 1rem;
      margin-top: 1rem;
    }

    .result-group,
    .track-card {
      border-radius: 22px;
      background: rgba(255, 255, 255, 0.82);
      border: 1px solid rgba(20, 60, 68, 0.08);
      padding: 1rem;
      transition: border-color 160ms ease, transform 160ms ease;
    }

    .result-group.expanded {
      border-color: rgba(20, 82, 96, 0.22);
    }

    .video-head,
    .video-head-actions,
    .track-actions {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 0.75rem;
    }

    .video-head,
    .track-card {
      cursor: pointer;
    }

    .track-card {
      display: grid;
      gap: 0.75rem;
    }

    .track-card.selected {
      border-color: rgba(20, 82, 96, 0.28);
      transform: translateY(-1px);
    }

    .track-card small {
      display: block;
      margin-top: 0.25rem;
      color: var(--ink-soft);
    }

    @media (max-width: 720px) {
      .filters-bar {
        grid-template-columns: 1fr;
      }

      .video-head,
      .video-head-actions,
      .track-actions {
        flex-direction: column;
        align-items: stretch;
      }
    }
  `],
})
export class AdminResultsComponent {
  protected readonly context = inject(AdminContextService);
  protected readonly system = inject(AdminSystemService);
  protected readonly videos = inject(AdminVideosService);
  protected readonly results = inject(AdminResultsService);
  protected readonly loading = computed(() => this.system.loading() || this.videos.loading());
  protected readonly filteredVideos = computed(() => {
    const { query, videoStatus } = this.context.filters();
    const normalizedQuery = query.trim().toLowerCase();
    return this.videos.debugVideos().filter((video) => {
      const statusMatches = videoStatus === 'all' || video.status === videoStatus;
      const queryMatches =
        !normalizedQuery ||
        video.video_id.toLowerCase().includes(normalizedQuery) ||
        (video.best_match_user_email ?? '').toLowerCase().includes(normalizedQuery) ||
        (video.assigned_user_email ?? '').toLowerCase().includes(normalizedQuery);
      return statusMatches && queryMatches;
    });
  });

  constructor() {
    effect(() => {
      const selectedVideoId = this.context.selectedVideoId();
      const availableVideoIds = this.videos.debugVideos().map((video) => video.video_id);
      this.results.ensureVideoExpanded(selectedVideoId, availableVideoIds);
    });
  }

  protected updateQuery(query: string): void {
    this.context.updateFilters({ query });
  }

  protected updateVideoStatus(videoStatus: string): void {
    this.context.updateFilters({ videoStatus: videoStatus as AdminResultsFilters['videoStatus'] });
  }

  protected updateTrackStatus(trackStatus: string): void {
    this.context.updateFilters({ trackStatus: trackStatus as AdminResultsFilters['trackStatus'] });
  }

  protected toggleVideo(videoId: string, event?: Event): void {
    event?.stopPropagation();
    this.results.toggleVideo(videoId);
  }

  protected openVideoDebug(event: Event, videoId: string): void {
    event.stopPropagation();
    this.videos.openVideoDebug(videoId);
  }

  protected openTrack(videoId: string, trackId: string, event?: Event): void {
    event?.stopPropagation();
    this.results.selectTrack(videoId, trackId);
    this.videos.openTrackDecision(videoId, trackId);
  }

  protected refresh(): void {
    this.system.refresh();
  }
}
