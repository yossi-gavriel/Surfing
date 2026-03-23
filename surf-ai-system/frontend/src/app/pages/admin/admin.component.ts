import { CommonModule } from '@angular/common';
import { HttpClient } from '@angular/common/http';
import { Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';

import { AuthService } from '../../core/auth.service';

interface AdminVideo {
  video_id: string;
  s3_path: string;
  status: 'uploaded' | 'processing' | 'completed' | 'failed';
  error_message: string | null;
  user_embeddings_count?: number;
  video_embeddings_count?: number;
  min_distance?: number | null;
  best_similarity?: number | null;
  threshold?: number;
  diagnostics?: {
    frame_processor?: {
      sampled_frames?: number;
      detections?: number;
      tracks_seen?: number;
      output_tracks?: number;
      keyframes_uploaded?: number;
      processing_seconds?: number;
    };
    embedding_service?: {
      tracks_received?: number;
      tracks_with_embeddings?: number;
      tracks_without_faces?: number;
      tracks_below_matching_threshold?: number;
      valid_faces_detected?: number;
      last_confidence?: number;
    };
  };
  created_at: string;
  updated_at: string;
  source_video_url: string | null;
}

interface CameraRecord {
  camera_id: string;
  name: string;
  url: string;
  active: boolean;
  created_at: string;
  updated_at: string;
}

@Component({
  selector: 'app-admin-page',
  standalone: true,
  imports: [CommonModule, FormsModule],
  template: `
    <section class="hero">
      <div>
        <p class="eyebrow">Admin control</p>
        <h2>Run real ingestion from uploaded videos and live cameras.</h2>
        <p class="subcopy">
          Upload a file, register RTSP sources, and watch processing status move through the pipeline.
        </p>
      </div>
      <button (click)="refresh()" [disabled]="loading()">{{ loading() ? 'Refreshing...' : 'Refresh' }}</button>
    </section>

    <section class="feedback error" *ngIf="errorMessage()">{{ errorMessage() }}</section>
    <section class="feedback success" *ngIf="successMessage()">{{ successMessage() }}</section>

    <section class="admin-grid">
      <article class="panel">
        <p class="panel-label">Video upload</p>
        <h3>Queue a new video</h3>
        <label class="dropzone">
          <input type="file" accept="video/*" (change)="onVideoSelected($event)" />
          <span>{{ selectedVideoName() || 'Choose video file' }}</span>
          <small>Uploads to S3, creates a DB record, and pushes the job into the processing queue.</small>
        </label>

        <div class="actions">
          <button (click)="uploadVideo()" [disabled]="!selectedVideo() || uploadingVideo()">
            {{ uploadingVideo() ? 'Uploading...' : 'Upload video' }}
          </button>
        </div>
      </article>

      <article class="panel">
        <p class="panel-label">Camera source</p>
        <h3>Register or update camera</h3>

        <label>
          <span>Name</span>
          <input [(ngModel)]="cameraForm.name" placeholder="Front Gate" />
        </label>

        <label>
          <span>RTSP / URL</span>
          <input [(ngModel)]="cameraForm.url" placeholder="rtsp://..." />
        </label>

        <label class="checkbox">
          <input type="checkbox" [(ngModel)]="cameraForm.active" />
          <span>Active</span>
        </label>

        <div class="actions">
          <button (click)="saveCamera()" [disabled]="savingCamera()">
            {{ savingCamera() ? 'Saving...' : 'Save camera' }}
          </button>
        </div>
      </article>
    </section>

    <section class="lists-grid">
      <article class="panel">
        <div class="section-header">
          <div>
            <p class="panel-label">Videos</p>
            <h3>Processing status</h3>
          </div>
        </div>

        <div class="empty" *ngIf="videos().length === 0 && !loading()">No videos uploaded yet.</div>

        <div class="video-list" *ngIf="videos().length > 0">
          <div class="video-row" *ngFor="let video of videos()">
            <div class="video-copy">
              <strong>{{ video.video_id }}</strong>
              <span>{{ formatTimestamp(video.created_at) }}</span>
              <a *ngIf="video.source_video_url" [href]="video.source_video_url" target="_blank" rel="noopener">Open source</a>
              <small *ngIf="video.error_message">{{ video.error_message }}</small>
              <div
                class="metrics"
                *ngIf="video.diagnostics || video.user_embeddings_count !== undefined || video.video_embeddings_count !== undefined"
              >
                <span *ngIf="video.diagnostics?.frame_processor?.sampled_frames">Frames {{ video.diagnostics?.frame_processor?.sampled_frames }}</span>
                <span *ngIf="video.diagnostics?.frame_processor?.detections !== undefined">Detections {{ video.diagnostics?.frame_processor?.detections }}</span>
                <span *ngIf="video.diagnostics?.frame_processor?.output_tracks !== undefined">Tracks {{ video.diagnostics?.frame_processor?.output_tracks }}</span>
                <span *ngIf="video.diagnostics?.embedding_service?.tracks_with_embeddings !== undefined">Embedded tracks {{ video.diagnostics?.embedding_service?.tracks_with_embeddings }}</span>
                <span *ngIf="video.diagnostics?.embedding_service?.tracks_without_faces !== undefined">No-face tracks {{ video.diagnostics?.embedding_service?.tracks_without_faces }}</span>
                <span *ngIf="video.diagnostics?.embedding_service?.tracks_below_matching_threshold !== undefined">Below match threshold {{ video.diagnostics?.embedding_service?.tracks_below_matching_threshold }}</span>
                <span *ngIf="video.user_embeddings_count !== undefined">User embeddings {{ video.user_embeddings_count }}</span>
                <span *ngIf="video.video_embeddings_count !== undefined">Video embeddings {{ video.video_embeddings_count }}</span>
                <span *ngIf="video.best_similarity !== null && video.best_similarity !== undefined">Best similarity {{ formatMetric(video.best_similarity) }}</span>
              </div>
              <small class="debug" *ngIf="video.min_distance !== null && video.min_distance !== undefined">
                Best match distance: {{ formatMetric(video.min_distance) }} (threshold {{ formatMetric(video.threshold ?? 0) }})
              </small>
              <small class="hint" *ngIf="matchingHint(video)">{{ matchingHint(video) }}</small>
            </div>

            <div class="video-meta">
              <span class="status" [class.failed]="video.status === 'failed'" [class.completed]="video.status === 'completed'">
                {{ video.status }}
              </span>
              <button
                class="secondary"
                (click)="triggerProcessing(video.video_id)"
                [disabled]="processingVideoId() === video.video_id"
              >
                {{ processingVideoId() === video.video_id ? 'Queueing...' : 'Trigger pipeline' }}
              </button>
            </div>
          </div>
        </div>
      </article>

      <article class="panel">
        <div class="section-header">
          <div>
            <p class="panel-label">Cameras</p>
            <h3>Active sources</h3>
          </div>
        </div>

        <div class="empty" *ngIf="cameras().length === 0 && !loading()">No cameras configured yet.</div>

        <div class="camera-list" *ngIf="cameras().length > 0">
          <div class="camera-row" *ngFor="let camera of cameras()">
            <div>
              <strong>{{ camera.name }}</strong>
              <span>{{ camera.url }}</span>
            </div>
            <span class="status" [class.completed]="camera.active" [class.failed]="!camera.active">
              {{ camera.active ? 'active' : 'inactive' }}
            </span>
          </div>
        </div>
      </article>
    </section>
  `,
  styles: [`
    :host {
      display: block;
    }

    .hero,
    .admin-grid,
    .lists-grid {
      display: grid;
      gap: 1.25rem;
    }

    .hero {
      grid-template-columns: 1fr auto;
      align-items: end;
      margin-bottom: 1.5rem;
    }

    .admin-grid,
    .lists-grid {
      grid-template-columns: repeat(2, minmax(0, 1fr));
      margin-bottom: 1.25rem;
    }

    .eyebrow,
    .panel-label {
      margin: 0 0 0.5rem;
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
      font-size: 2.5rem;
      max-width: 12ch;
    }

    h3 {
      font-size: 1.45rem;
    }

    .subcopy {
      margin: 0.8rem 0 0;
      color: var(--ink-soft);
      max-width: 60ch;
      line-height: 1.7;
    }

    .panel,
    .feedback {
      border-radius: 28px;
      border: 1px solid rgba(20, 60, 68, 0.12);
      background: rgba(255, 252, 245, 0.9);
      box-shadow: 0 24px 60px rgba(13, 40, 45, 0.08);
    }

    .panel {
      padding: 1.5rem;
    }

    .feedback {
      padding: 1rem 1.2rem;
      margin-bottom: 1rem;
    }

    .feedback.error {
      color: #a7341b;
    }

    .feedback.success {
      color: #1d6d4f;
    }

    .dropzone {
      display: grid;
      gap: 0.5rem;
      border: 1.5px dashed rgba(20, 82, 96, 0.35);
      border-radius: 24px;
      padding: 1.5rem;
      background: linear-gradient(180deg, rgba(235, 248, 246, 0.9), rgba(255, 255, 255, 0.95));
      cursor: pointer;
      margin-top: 1rem;
      color: var(--ink-strong);
    }

    .dropzone input {
      display: none;
    }

    .dropzone span {
      font-weight: 600;
      font-size: 1rem;
    }

    .dropzone small,
    .video-copy span,
    .video-copy small,
    .camera-row span,
    label span {
      color: var(--ink-soft);
    }

    label {
      display: grid;
      gap: 0.45rem;
      margin-top: 1rem;
    }

    input[type='text'],
    input:not([type='checkbox']) {
      width: 100%;
      border: 1px solid rgba(20, 60, 68, 0.16);
      border-radius: 16px;
      padding: 0.9rem 1rem;
      background: rgba(255, 255, 255, 0.92);
      color: var(--ink-strong);
    }

    .checkbox {
      grid-template-columns: auto 1fr;
      align-items: center;
      gap: 0.75rem;
    }

    .actions,
    .section-header,
    .video-row,
    .camera-row,
    .video-meta {
      display: flex;
      justify-content: space-between;
      gap: 0.75rem;
    }

    .actions {
      margin-top: 1.25rem;
    }

    .video-list,
    .camera-list {
      display: grid;
      gap: 0.85rem;
      margin-top: 1rem;
    }

    .video-row,
    .camera-row {
      align-items: center;
      padding: 1rem;
      border-radius: 20px;
      background: rgba(255, 255, 255, 0.78);
      border: 1px solid rgba(20, 60, 68, 0.08);
    }

    .video-copy,
    .camera-row div {
      display: grid;
      gap: 0.2rem;
      min-width: 0;
    }

    .metrics {
      display: flex;
      flex-wrap: wrap;
      gap: 0.45rem;
      margin-top: 0.4rem;
    }

    .metrics span {
      border-radius: 999px;
      background: rgba(20, 82, 96, 0.08);
      color: var(--ink-soft);
      padding: 0.28rem 0.6rem;
      font-size: 0.78rem;
    }

    .video-copy strong,
    .camera-row strong {
      overflow-wrap: anywhere;
    }

    .video-copy a {
      color: var(--accent-deep);
      text-decoration: none;
    }

    .hint {
      margin-top: 0.35rem;
      color: #8a5c13;
      line-height: 1.5;
    }

    .debug {
      margin-top: 0.35rem;
      color: var(--accent-deep);
      line-height: 1.5;
    }

    .status {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      border-radius: 999px;
      padding: 0.4rem 0.75rem;
      background: rgba(20, 82, 96, 0.12);
      color: var(--accent-deep);
      text-transform: capitalize;
      font-size: 0.85rem;
      min-width: 96px;
    }

    .status.completed {
      background: rgba(29, 109, 79, 0.14);
      color: #1d6d4f;
    }

    .status.failed {
      background: rgba(167, 52, 27, 0.12);
      color: #a7341b;
    }

    .empty {
      margin-top: 1rem;
      color: var(--ink-soft);
    }

    button {
      border: none;
      border-radius: 999px;
      padding: 0.9rem 1.2rem;
      background: linear-gradient(135deg, var(--accent-deep), var(--accent));
      color: white;
      font: inherit;
      font-weight: 600;
      cursor: pointer;
    }

    button.secondary {
      background: rgba(20, 60, 68, 0.08);
      color: var(--ink-strong);
    }

    button:disabled {
      opacity: 0.7;
      cursor: wait;
    }

    @media (max-width: 920px) {
      .hero,
      .admin-grid,
      .lists-grid {
        grid-template-columns: 1fr;
      }

      .hero,
      .video-row,
      .camera-row,
      .video-meta {
        align-items: stretch;
      }

      .video-row,
      .camera-row,
      .video-meta {
        flex-direction: column;
      }
    }
  `],
})
export class AdminComponent {
  private readonly http = inject(HttpClient);
  private readonly auth = inject(AuthService);
  private readonly router = inject(Router);

  readonly videos = signal<AdminVideo[]>([]);
  readonly cameras = signal<CameraRecord[]>([]);
  readonly loading = signal(false);
  readonly uploadingVideo = signal(false);
  readonly savingCamera = signal(false);
  readonly processingVideoId = signal<string | null>(null);
  readonly selectedVideo = signal<File | null>(null);
  readonly selectedVideoName = signal('');
  readonly successMessage = signal('');
  readonly errorMessage = signal('');

  readonly cameraForm = {
    name: '',
    url: '',
    active: true,
  };

  constructor() {
    this.refresh();
  }

  refresh(): void {
    this.loading.set(true);
    this.errorMessage.set('');

    this.http
      .get<AdminVideo[]>('/api/admin/videos', {
        headers: this.auth.authHeaders(),
      })
      .subscribe({
        next: (videos) => {
          this.videos.set(videos);
          this.loadCameras();
        },
        error: (error) => this.handleHttpError(error, 'Unable to load admin videos.'),
      });
  }

  loadCameras(): void {
    this.http
      .get<CameraRecord[]>('/api/admin/cameras', {
        headers: this.auth.authHeaders(),
      })
      .subscribe({
        next: (cameras) => {
          this.cameras.set(cameras);
          this.loading.set(false);
        },
        error: (error) => this.handleHttpError(error, 'Unable to load cameras.'),
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

    const formData = new FormData();
    formData.append('file', file);

    this.uploadingVideo.set(true);
    this.successMessage.set('');
    this.errorMessage.set('');

    this.http
      .post<{ message: string }>('/api/admin/upload-video', formData, {
        headers: this.auth.authHeaders(),
      })
      .subscribe({
        next: (response) => {
          this.uploadingVideo.set(false);
          this.selectedVideo.set(null);
          this.selectedVideoName.set('');
          this.successMessage.set(response.message);
          this.refresh();
        },
        error: (error) => {
          this.uploadingVideo.set(false);
          this.handleHttpError(error, 'Video upload failed.');
        },
      });
  }

  saveCamera(): void {
    if (!this.cameraForm.name.trim() || !this.cameraForm.url.trim()) {
      this.errorMessage.set('Camera name and URL are required.');
      return;
    }

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
          this.successMessage.set(`Camera ${camera.name} saved.`);
          this.refresh();
        },
        error: (error) => {
          this.savingCamera.set(false);
          this.handleHttpError(error, 'Camera save failed.');
        },
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
          this.successMessage.set(response.message);
          this.refresh();
        },
        error: (error) => {
          this.processingVideoId.set(null);
          this.handleHttpError(error, 'Unable to trigger processing.');
        },
      });
  }

  formatTimestamp(value: string): string {
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
  }

  formatMetric(value: number, digits = 3): string {
    return Number.isFinite(value) ? value.toFixed(digits) : 'n/a';
  }

  matchingHint(video: AdminVideo): string {
    const diagnostics = video.diagnostics;
    const outputTracks = diagnostics?.frame_processor?.output_tracks ?? 0;
    const embeddedTracks = diagnostics?.embedding_service?.tracks_with_embeddings ?? 0;
    const belowThreshold = diagnostics?.embedding_service?.tracks_below_matching_threshold ?? 0;
    const noFaceTracks = diagnostics?.embedding_service?.tracks_without_faces ?? 0;

    if (video.status !== 'completed') {
      return '';
    }
    if (outputTracks === 0) {
      return 'The frame processor completed, but it did not produce any qualifying tracks.';
    }
    if (embeddedTracks === 0 && noFaceTracks > 0) {
      return 'Tracks were created, but the face embedder could not extract a usable face from the keyframes.';
    }
    if (belowThreshold > 0) {
      return 'At least one track had too little face evidence for matching, so no match could be created.';
    }
    return '';
  }

  private handleHttpError(error: any, fallbackMessage: string): void {
    this.loading.set(false);
    const detail = error?.error?.detail;
    this.errorMessage.set(detail?.message || detail || fallbackMessage);
    if (error?.status === 401) {
      this.auth.clearSession();
      this.router.navigate(['/login']);
    }
  }
}
