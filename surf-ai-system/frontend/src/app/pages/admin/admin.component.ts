import { CommonModule } from '@angular/common';
import { HttpClient } from '@angular/common/http';
import { Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';

import { AuthService, MeResponse } from '../../core/auth.service';

interface AdminVideo {
  video_id: string;
  s3_path: string;
  status: 'uploaded' | 'processing' | 'completed' | 'failed';
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

@Component({
  selector: 'app-admin-page',
  standalone: true,
  imports: [CommonModule, FormsModule],
  template: `
    <section class="hero">
      <div>
        <p class="eyebrow">Admin control</p>
        <h2>Manage pools, uploads, and matching review.</h2>
        <p class="subcopy">
          Everything here is scoped to the active pool. Video ingestion, matching review, and manual
          assignment stay inside the same pool.
        </p>
      </div>
      <button (click)="refresh()" [disabled]="loading()">{{ loading() ? 'Refreshing...' : 'Refresh' }}</button>
    </section>

    <section class="feedback error" *ngIf="errorMessage()">{{ errorMessage() }}</section>
    <section class="feedback success" *ngIf="successMessage()">{{ successMessage() }}</section>

    <section class="admin-grid">
      <article class="panel">
        <p class="panel-label">Pool management</p>
        <h3>Create and activate pools</h3>

        <label>
          <span>Active pool</span>
          <select [(ngModel)]="selectedPoolId">
            <option [ngValue]="''">Choose a pool</option>
            <option *ngFor="let pool of pools()" [ngValue]="pool.pool_id">{{ pool.name }}</option>
          </select>
        </label>

        <div class="actions">
          <button (click)="saveActivePool()" [disabled]="savingPool() || !selectedPoolId">
            {{ savingPool() ? 'Saving...' : 'Set active pool' }}
          </button>
        </div>

        <label>
          <span>New pool name</span>
          <input [(ngModel)]="newPoolName" placeholder="Morning Session" />
        </label>

        <div class="actions">
          <button (click)="createPool()" [disabled]="creatingPool() || !newPoolName.trim()">
            {{ creatingPool() ? 'Creating...' : 'Create pool' }}
          </button>
        </div>
      </article>

      <article class="panel">
        <p class="panel-label">Video upload</p>
        <h3>Queue a new video</h3>
        <label class="dropzone">
          <input type="file" accept="video/*" (change)="onVideoSelected($event)" />
          <span>{{ selectedVideoName() || 'Choose video file' }}</span>
          <small>Uploads to S3, creates a DB record, and pushes the job into the processing queue.</small>
        </label>

        <div class="actions">
          <button (click)="uploadVideo()" [disabled]="!selectedVideo() || uploadingVideo() || !selectedPoolId">
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
          <button (click)="saveCamera()" [disabled]="savingCamera() || !selectedPoolId">
            {{ savingCamera() ? 'Saving...' : 'Save camera' }}
          </button>
        </div>
      </article>

      <article class="panel">
        <div class="section-header">
          <div>
            <p class="panel-label">Pool users</p>
            <h3>Users in the active pool</h3>
          </div>
          <span class="status completed">{{ users().length }} users</span>
        </div>

        <div class="empty" *ngIf="!selectedPoolId">Select an active pool to see its users.</div>
        <div class="empty" *ngIf="selectedPoolId && users().length === 0">No users belong to this pool yet.</div>

        <div class="user-list" *ngIf="users().length > 0">
          <div class="user-row" *ngFor="let user of users()">
            <img *ngIf="user.latest_reference_image_url; else noUserImage" [src]="user.latest_reference_image_url" alt="Latest reference image" />
            <ng-template #noUserImage>
              <div class="user-placeholder">No image</div>
            </ng-template>
            <div class="user-copy">
              <strong>{{ user.email }}</strong>
              <span>{{ user.role }}</span>
              <small>Reference images {{ user.reference_images_count }}</small>
            </div>
          </div>
        </div>
      </article>
    </section>

    <section class="lists-grid">
      <article class="panel">
        <div class="section-header">
          <div>
            <p class="panel-label">Videos</p>
            <h3>Processing and matching status</h3>
          </div>
        </div>

        <div class="empty" *ngIf="!selectedPoolId">Set an active pool to start working with videos.</div>
        <div class="empty" *ngIf="selectedPoolId && videos().length === 0 && !loading()">No videos uploaded yet.</div>

        <div class="video-list" *ngIf="videos().length > 0">
          <div class="video-row" *ngFor="let video of videos()">
            <div class="video-copy">
              <strong>{{ video.video_id }}</strong>
              <span>{{ formatTimestamp(video.created_at) }}</span>
              <a *ngIf="video.source_video_url" [href]="video.source_video_url" target="_blank" rel="noopener">Open source</a>
              <small *ngIf="video.error_message">{{ video.error_message }}</small>
              <div class="metrics">
                <span *ngIf="video.user_embeddings_count !== undefined">Pool embeddings {{ video.user_embeddings_count }}</span>
                <span *ngIf="video.video_embeddings_count !== undefined">Video embeddings {{ video.video_embeddings_count }}</span>
                <span *ngIf="video.best_similarity !== null && video.best_similarity !== undefined">Best similarity {{ formatMetric(video.best_similarity) }}</span>
                <span *ngIf="video.pool_users_count !== undefined">Pool users {{ video.pool_users_count }}</span>
              </div>
              <small class="debug" *ngIf="video.min_distance !== null && video.min_distance !== undefined">
                Best match distance: {{ formatMetric(video.min_distance) }} (threshold {{ formatMetric(video.threshold ?? 0) }})
              </small>
              <small class="hint" *ngIf="video.best_match_user_email">Best candidate: {{ video.best_match_user_email }}</small>
              <small class="hint" *ngIf="video.confirmed_match_user_email">Confirmed match: {{ video.confirmed_match_user_email }}</small>
              <small class="hint" *ngIf="video.assigned_user_email">Assigned to: {{ video.assigned_user_email }}</small>

              <div class="assign-bar" *ngIf="users().length > 0">
                <select [ngModel]="assignmentSelection(video)" (ngModelChange)="setAssignmentSelection(video.video_id, $event)">
                  <option [ngValue]="''">No assignment</option>
                  <option *ngFor="let user of users()" [ngValue]="user.user_id">{{ user.email }}</option>
                </select>
                <button class="secondary" type="button" (click)="assignVideo(video)" [disabled]="assigningVideoId() === video.video_id">
                  {{ assigningVideoId() === video.video_id ? 'Saving...' : 'Save assignment' }}
                </button>
              </div>
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
              <button class="secondary" type="button" (click)="openVideoDebug(video.video_id)">
                Open debug view
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

        <div class="empty" *ngIf="!selectedPoolId">Select an active pool to see cameras.</div>
        <div class="empty" *ngIf="selectedPoolId && cameras().length === 0 && !loading()">No cameras configured yet.</div>

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

    .admin-grid {
      grid-template-columns: repeat(2, minmax(0, 1fr));
      margin-bottom: 1.25rem;
    }

    .lists-grid {
      grid-template-columns: 1.4fr 0.8fr;
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
      max-width: 14ch;
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

    label {
      display: grid;
      gap: 0.45rem;
      margin-top: 1rem;
    }

    input[type='text'],
    input:not([type='checkbox']),
    select {
      width: 100%;
      border: 1px solid rgba(20, 60, 68, 0.16);
      border-radius: 16px;
      padding: 0.9rem 1rem;
      background: rgba(255, 255, 255, 0.92);
      color: var(--ink-strong);
      font: inherit;
    }

    .checkbox {
      grid-template-columns: auto 1fr;
      align-items: center;
      gap: 0.75rem;
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
    .user-copy span,
    label span {
      color: var(--ink-soft);
    }

    .actions,
    .section-header,
    .video-row,
    .camera-row,
    .video-meta,
    .user-row,
    .assign-bar {
      display: flex;
      justify-content: space-between;
      gap: 0.75rem;
    }

    .actions {
      margin-top: 1.25rem;
    }

    .video-list,
    .camera-list,
    .user-list {
      display: grid;
      gap: 0.85rem;
      margin-top: 1rem;
    }

    .video-row,
    .camera-row,
    .user-row {
      align-items: center;
      padding: 1rem;
      border-radius: 20px;
      background: rgba(255, 255, 255, 0.78);
      border: 1px solid rgba(20, 60, 68, 0.08);
    }

    .video-copy,
    .camera-row div,
    .user-copy {
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
    .camera-row strong,
    .user-copy strong {
      overflow-wrap: anywhere;
    }

    .video-copy a {
      color: var(--accent-deep);
      text-decoration: none;
    }

    .hint,
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

    .user-row img,
    .user-placeholder {
      width: 72px;
      height: 72px;
      border-radius: 18px;
      object-fit: cover;
      background: linear-gradient(135deg, rgba(193, 230, 223, 0.75), rgba(255, 238, 210, 0.8));
    }

    .user-placeholder {
      display: grid;
      place-items: center;
      color: var(--ink-soft);
      font-size: 0.8rem;
    }

    .assign-bar {
      align-items: center;
      margin-top: 0.8rem;
    }

    .assign-bar select {
      min-width: 220px;
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
      .video-meta,
      .user-row,
      .assign-bar {
        align-items: stretch;
      }

      .video-row,
      .camera-row,
      .video-meta,
      .user-row,
      .assign-bar {
        flex-direction: column;
      }

      .assign-bar select {
        min-width: 0;
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
  readonly pools = signal<PoolRecord[]>([]);
  readonly users = signal<AdminUser[]>([]);
  readonly loading = signal(false);
  readonly uploadingVideo = signal(false);
  readonly savingCamera = signal(false);
  readonly savingPool = signal(false);
  readonly creatingPool = signal(false);
  readonly processingVideoId = signal<string | null>(null);
  readonly assigningVideoId = signal<string | null>(null);
  readonly selectedVideo = signal<File | null>(null);
  readonly selectedVideoName = signal('');
  readonly successMessage = signal('');
  readonly errorMessage = signal('');
  readonly assignmentSelections = signal<Record<string, string>>({});

  selectedPoolId = '';
  newPoolName = '';

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
          this.selectedPoolId = me.pool_id ?? '';
          this.loadPools();
        },
        error: (error) => this.handleHttpError(error, 'Unable to load admin profile.'),
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
          if (!this.selectedPoolId && pools.length > 0 && this.auth.poolId()) {
            this.selectedPoolId = this.auth.poolId() ?? '';
          }
          this.loadVideos();
        },
        error: (error) => this.handleHttpError(error, 'Unable to load pools.'),
      });
  }

  loadVideos(): void {
    if (!this.selectedPoolId) {
      this.videos.set([]);
      this.cameras.set([]);
      this.users.set([]);
      this.loading.set(false);
      return;
    }

    this.http
      .get<AdminVideo[]>('/api/admin/videos', {
        headers: this.auth.authHeaders(),
      })
      .subscribe({
        next: (videos) => {
          this.videos.set(videos);
          this.assignmentSelections.set(
            Object.fromEntries(videos.map((video) => [video.video_id, video.assigned_user_id ?? ''])),
          );
          this.loadCameras();
          this.loadUsers();
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

  loadUsers(): void {
    this.http
      .get<AdminUser[]>('/api/admin/users', {
        headers: this.auth.authHeaders(),
      })
      .subscribe({
        next: (users) => this.users.set(users),
        error: (error) => this.handleHttpError(error, 'Unable to load pool users.'),
      });
  }

  saveActivePool(): void {
    if (!this.selectedPoolId) {
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
          this.savingPool.set(false);
          this.successMessage.set('Active pool updated.');
          this.loadVideos();
        },
        error: (error) => {
          this.savingPool.set(false);
          this.handleHttpError(error, 'Unable to update active pool.');
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
          this.successMessage.set(`Pool ${pool.name} created.`);
          this.loadPools();
          this.saveActivePool();
        },
        error: (error) => {
          this.creatingPool.set(false);
          this.handleHttpError(error, 'Unable to create pool.');
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
          this.loadVideos();
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
          this.loadCameras();
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
          this.loadVideos();
        },
        error: (error) => {
          this.processingVideoId.set(null);
          this.handleHttpError(error, 'Unable to trigger processing.');
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
          this.successMessage.set(response.message);
          this.loadVideos();
        },
        error: (error) => {
          this.assigningVideoId.set(null);
          this.handleHttpError(error, 'Unable to save video assignment.');
        },
      });
  }

  openVideoDebug(videoId: string): void {
    this.router.navigate(['/admin/videos', videoId]);
  }

  formatTimestamp(value: string): string {
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
  }

  formatMetric(value: number, digits = 3): string {
    return Number.isFinite(value) ? value.toFixed(digits) : 'n/a';
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
