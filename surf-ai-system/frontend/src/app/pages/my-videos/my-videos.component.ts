import { CommonModule } from '@angular/common';
import { HttpClient } from '@angular/common/http';
import { Component, inject, signal } from '@angular/core';
import { Router } from '@angular/router';

import { AuthService, MeResponse } from '../../core/auth.service';
import { I18nService } from '../../core/i18n.service';

interface VideoMatch {
  track_id: string;
  video_id: string | null;
  keyframe: string | null;
  timestamp: string | null;
  confidence: number;
  score: number;
  download_url: string | null;
  preview_url: string | null;
  source_video_url?: string | null;
}

@Component({
  selector: 'app-my-videos-page',
  standalone: true,
  imports: [CommonModule],
  template: `
    <section class="page-header">
      <div>
        <p class="eyebrow">{{ i18n.t('myVideos.eyebrow') }}</p>
        <h2>{{ i18n.t('myVideos.title') }}</h2>
        <p class="subcopy">{{ i18n.t('myVideos.subtitle') }}</p>
      </div>
      <button (click)="loadVideos()" [disabled]="loading()">
        {{ loading() ? i18n.t('common.refreshing') : i18n.t('common.refresh') }}
      </button>
    </section>

    <section class="state-card" *ngIf="loading()">{{ i18n.t('myVideos.loading') }}</section>
    <section class="state-card error" *ngIf="errorMessage()">{{ errorMessage() }}</section>
    <section class="state-card" *ngIf="!loading() && !errorMessage() && videos().length === 0">
      {{ i18n.t('myVideos.empty') }}
    </section>

    <section class="videos-grid" *ngIf="videos().length > 0">
      <article class="video-card" *ngFor="let video of videos()">
        <video *ngIf="video.preview_url; else previewImage" [src]="video.preview_url" controls muted playsinline preload="metadata"></video>
        <ng-template #previewImage>
          <img *ngIf="video.keyframe; else placeholder" [src]="video.keyframe" alt="Video keyframe" />
        </ng-template>
        <ng-template #placeholder>
          <div class="placeholder">{{ i18n.t('myVideos.noKeyframe') }}</div>
        </ng-template>

        <div class="card-body">
          <div class="card-meta">
            <span class="pill">{{ video.video_id || i18n.t('myVideos.unknownVideo') }}</span>
            <span class="timestamp">{{ formatTimestamp(video.timestamp) }}</span>
          </div>

          <div class="scores">
            <div>
              <strong>{{ (video.confidence * 100).toFixed(0) }}%</strong>
              <span>{{ i18n.t('myVideos.confidence') }}</span>
            </div>
            <div>
              <strong>{{ (video.score * 100).toFixed(0) }}%</strong>
              <span>{{ i18n.t('myVideos.score') }}</span>
            </div>
          </div>

          <div class="actions">
            <a
              class="button"
              [href]="video.download_url || video.preview_url || video.keyframe || '#'"
              target="_blank"
              rel="noopener"
            >
              {{ i18n.t('common.download') }}
            </a>
            <a
              *ngIf="video.source_video_url"
              class="secondary-link"
              [href]="video.source_video_url"
              target="_blank"
              rel="noopener"
            >
              {{ i18n.t('myVideos.openSource') }}
            </a>
            <span class="track">{{ i18n.t('myVideos.track', { trackId: video.track_id }) }}</span>
          </div>
        </div>
      </article>
    </section>
  `,
  styles: [`
    :host {
      display: block;
    }

    .page-header {
      display: flex;
      justify-content: space-between;
      gap: 1rem;
      align-items: end;
      margin-bottom: 1.5rem;
    }

    .eyebrow {
      margin: 0 0 0.5rem;
      text-transform: uppercase;
      letter-spacing: 0.18em;
      color: var(--accent-deep);
      font-size: 0.8rem;
    }

    h2 {
      margin: 0;
      font-family: 'Space Grotesk', sans-serif;
      font-size: 2.3rem;
      color: var(--ink-strong);
    }

    .subcopy {
      margin: 0.5rem 0 0;
      color: var(--ink-soft);
    }

    button,
    .button {
      border: none;
      border-radius: 999px;
      padding: 0.9rem 1.2rem;
      background: linear-gradient(135deg, var(--accent-deep), var(--accent));
      color: white;
      text-decoration: none;
      font: inherit;
      font-weight: 600;
      cursor: pointer;
    }

    .state-card {
      border-radius: 24px;
      border: 1px solid rgba(20, 60, 68, 0.12);
      background: rgba(255, 252, 245, 0.88);
      padding: 1.5rem;
      color: var(--ink-soft);
    }

    .error {
      color: #a7341b;
    }

    .videos-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
      gap: 1.25rem;
    }

    .video-card {
      overflow: hidden;
      border-radius: 28px;
      background: rgba(255, 252, 245, 0.92);
      border: 1px solid rgba(20, 60, 68, 0.12);
      box-shadow: 0 22px 50px rgba(13, 40, 45, 0.08);
    }

    img,
    video,
    .placeholder {
      width: 100%;
      height: 220px;
      object-fit: cover;
      background: linear-gradient(135deg, rgba(193, 230, 223, 0.75), rgba(255, 238, 210, 0.8));
    }

    .placeholder {
      display: grid;
      place-items: center;
      color: var(--ink-soft);
    }

    .card-body {
      padding: 1.1rem;
    }

    .card-meta {
      display: flex;
      justify-content: space-between;
      gap: 0.75rem;
      align-items: center;
    }

    .pill {
      display: inline-flex;
      align-items: center;
      border-radius: 999px;
      background: rgba(20, 82, 96, 0.1);
      color: var(--accent-deep);
      padding: 0.35rem 0.7rem;
      font-size: 0.82rem;
    }

    .timestamp {
      color: var(--ink-soft);
      font-size: 0.88rem;
    }

    .scores {
      display: grid;
      grid-template-columns: repeat(2, 1fr);
      gap: 0.8rem;
      margin: 1rem 0;
    }

    .scores div {
      background: rgba(255, 255, 255, 0.82);
      border-radius: 18px;
      padding: 0.85rem;
    }

    .scores strong {
      display: block;
      font-size: 1.5rem;
      color: var(--ink-strong);
      font-family: 'Space Grotesk', sans-serif;
    }

    .scores span,
    .track,
    .secondary-link {
      color: var(--ink-soft);
      font-size: 0.85rem;
    }

    .secondary-link {
      text-decoration: none;
    }

    .actions {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 0.75rem;
    }

    @media (max-width: 720px) {
      .page-header,
      .actions,
      .card-meta {
        flex-direction: column;
        align-items: stretch;
      }
    }
  `],
})
export class MyVideosComponent {
  private readonly http = inject(HttpClient);
  private readonly auth = inject(AuthService);
  private readonly router = inject(Router);
  protected readonly i18n = inject(I18nService);

  readonly videos = signal<VideoMatch[]>([]);
  readonly loading = signal(false);
  readonly errorMessage = signal('');

  constructor() {
    this.loadProfile();
    this.loadVideos();
  }

  loadProfile(): void {
    this.http
      .get<MeResponse>('/api/me', {
        headers: this.auth.authHeaders(),
      })
      .subscribe({
        next: (profile) => this.auth.setProfile(profile),
        error: (error) => {
          if (error.status === 401) {
            this.auth.clearSession();
            this.router.navigate(['/login']);
          }
        },
      });
  }

  loadVideos(): void {
    this.loading.set(true);
    this.errorMessage.set('');

    this.http
      .get<VideoMatch[]>('/api/user/videos', {
        headers: this.auth.authHeaders(),
      })
      .subscribe({
        next: (videos) => {
          this.videos.set(videos);
          this.loading.set(false);
        },
        error: (error) => {
          this.loading.set(false);
          this.errorMessage.set(this.i18n.translateApiMessage(error.error?.detail, 'myVideos.loadFailed'));
          if (error.status === 401) {
            this.auth.clearSession();
            this.router.navigate(['/login']);
          }
        },
      });
  }

  formatTimestamp(timestamp: string | null): string {
    return this.i18n.formatDateTime(timestamp);
  }
}
