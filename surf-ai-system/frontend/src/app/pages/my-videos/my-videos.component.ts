import { CommonModule } from '@angular/common';
import { HttpClient } from '@angular/common/http';
import { Component, inject, signal } from '@angular/core';
import { Router } from '@angular/router';

import { AuthService } from '../../core/auth.service';

interface VideoMatch {
  track_id: string;
  video_id: string | null;
  keyframe: string | null;
  timestamp: string | null;
  confidence: number;
  score: number;
  download_url: string | null;
  preview_url: string | null;
}

@Component({
  selector: 'app-my-videos-page',
  standalone: true,
  imports: [CommonModule],
  template: `
    <section class="page-header">
      <div>
        <p class="eyebrow">Matched clips</p>
        <h2>My videos</h2>
        <p class="subcopy">Every confirmed appearance from the matches database, ready to review.</p>
      </div>
      <button (click)="loadVideos()" [disabled]="loading()">{{ loading() ? 'Refreshing...' : 'Refresh' }}</button>
    </section>

    <section class="state-card" *ngIf="loading()">Loading matched videos...</section>
    <section class="state-card error" *ngIf="errorMessage()">{{ errorMessage() }}</section>
    <section class="state-card" *ngIf="!loading() && !errorMessage() && videos().length === 0">
      No matches yet. Upload a face or check back after more videos are processed.
    </section>

    <section class="videos-grid" *ngIf="videos().length > 0">
      <article class="video-card" *ngFor="let video of videos()">
        <img *ngIf="video.keyframe; else placeholder" [src]="video.keyframe" alt="Video keyframe" />
        <ng-template #placeholder>
          <div class="placeholder">No keyframe available</div>
        </ng-template>

        <div class="card-body">
          <div class="card-meta">
            <span class="pill">{{ video.video_id || 'Unknown video' }}</span>
            <span class="timestamp">{{ formatTimestamp(video.timestamp) }}</span>
          </div>

          <div class="scores">
            <div>
              <strong>{{ (video.confidence * 100).toFixed(0) }}%</strong>
              <span>confidence</span>
            </div>
            <div>
              <strong>{{ (video.score * 100).toFixed(0) }}%</strong>
              <span>score</span>
            </div>
          </div>

          <div class="actions">
            <a class="button" [href]="video.download_url || video.preview_url || video.keyframe || '#'" target="_blank" rel="noopener">
              Download
            </a>
            <span class="track">Track {{ video.track_id }}</span>
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
    .track {
      color: var(--ink-soft);
      font-size: 0.85rem;
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

  readonly videos = signal<VideoMatch[]>([]);
  readonly loading = signal(false);
  readonly errorMessage = signal('');

  constructor() {
    this.loadVideos();
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
          this.errorMessage.set(error.error?.detail || 'Unable to load videos right now.');
          if (error.status === 401) {
            this.auth.clearSession();
            this.router.navigate(['/login']);
          }
        },
      });
  }

  formatTimestamp(timestamp: string | null): string {
    if (!timestamp) {
      return 'Timestamp unavailable';
    }

    const date = new Date(timestamp);
    return Number.isNaN(date.getTime()) ? timestamp : date.toLocaleString();
  }
}
