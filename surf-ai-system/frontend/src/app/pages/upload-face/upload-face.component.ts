import { CommonModule } from '@angular/common';
import { HttpClient } from '@angular/common/http';
import { Component, DestroyRef, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';

import { AuthService, MeResponse } from '../../core/auth.service';
import { I18nService } from '../../core/i18n.service';

interface PoolOption {
  id: string;
  pool_id: string;
  name: string;
}

interface ReferenceImage {
  id: string;
  reference_image_id: string;
  image_url: string | null;
  created_at: string;
}

interface BackfillStatusResponse {
  status: 'idle' | 'running' | 'done' | 'failed';
  message: string;
  job_ids: string[];
  jobs_total: number;
  jobs_completed: number;
  jobs_failed: number;
  jobs_running: number;
  jobs_pending: number;
}

interface ReferenceImageUploadResponse {
  uploaded: number;
  message: string;
  backfill_job_ids?: string[];
  backfill_status?: BackfillStatusResponse;
}

@Component({
  selector: 'app-upload-face-page',
  standalone: true,
  imports: [CommonModule, FormsModule],
  template: `
    <section class="page-grid">
      <article class="panel">
        <p class="eyebrow">{{ i18n.t('uploadFace.profileEyebrow') }}</p>
        <h2>{{ i18n.t('uploadFace.profileTitle') }}</h2>
        <p>{{ i18n.t('uploadFace.profileSubtitle') }}</p>

        <label class="field">
          <span>{{ i18n.t('uploadFace.myPool') }}</span>
          <select [ngModel]="selectedPoolId" (ngModelChange)="onPoolSelectionChange($event)">
            <option [ngValue]="''">{{ i18n.t('common.choosePool') }}</option>
            <option *ngFor="let pool of pools()" [ngValue]="pool.pool_id">{{ pool.name }}</option>
          </select>
        </label>

        <div class="actions">
          <button type="button" (click)="savePool()" [disabled]="savingPool() || !selectedPoolId">
            {{ savingPool() ? i18n.t('common.saving') : i18n.t('uploadFace.savePool') }}
          </button>
          <button class="secondary" type="button" (click)="goToVideos()">
            {{ i18n.t('nav.myVideos') }}
          </button>
        </div>
      </article>

      <article class="panel">
        <p class="eyebrow">{{ i18n.t('uploadFace.referenceEyebrow') }}</p>
        <h2>{{ i18n.t('uploadFace.referenceTitle') }}</h2>
        <p>{{ i18n.t('uploadFace.referenceSubtitle') }}</p>

        <label class="dropzone">
          <input type="file" accept="image/*" multiple (change)="onFilesSelected($event)" />
          <span>{{ selectedFilesLabel() || i18n.t('uploadFace.chooseImages') }}</span>
          <small>{{ i18n.t('uploadFace.chooseImagesHint') }}</small>
        </label>

        <div class="preview-grid" *ngIf="previewUrls().length > 0">
          <img *ngFor="let preview of previewUrls()" [src]="preview" alt="Selected face preview" class="preview" />
        </div>

        <div class="actions">
          <button type="button" (click)="upload()" [disabled]="selectedFiles().length === 0 || uploading()">
            {{ uploading() ? i18n.t('common.uploading') : i18n.t('uploadFace.uploadReferenceImages') }}
          </button>
          <button class="secondary" type="button" (click)="refresh()">
            {{ i18n.t('common.refresh') }}
          </button>
        </div>
      </article>
    </section>

    <section class="feedback success" *ngIf="successMessage()">{{ successMessage() }}</section>
    <section class="feedback error" *ngIf="errorMessage()">{{ errorMessage() }}</section>
    <section class="feedback" *ngIf="backfillMessage()" [class.success]="backfillStatus() === 'done'" [class.error]="backfillStatus() === 'failed'">
      {{ backfillMessage() }}
    </section>

    <section class="panel gallery-panel">
      <div class="gallery-header">
        <div>
          <p class="eyebrow">{{ i18n.t('uploadFace.galleryEyebrow') }}</p>
          <h2>{{ i18n.t('uploadFace.galleryTitle') }}</h2>
        </div>
        <span class="count-pill">{{ i18n.t('uploadFace.storedCount', { count: referenceImages().length }) }}</span>
      </div>

      <div class="empty" *ngIf="referenceImages().length === 0">{{ i18n.t('uploadFace.empty') }}</div>

      <div class="gallery-grid" *ngIf="referenceImages().length > 0">
        <article class="image-card" *ngFor="let image of referenceImages(); let index = index">
          <img *ngIf="image.image_url; else missingImage" [src]="image.image_url" alt="Reference image" />
          <ng-template #missingImage>
            <div class="placeholder">{{ i18n.t('common.imageUnavailable') }}</div>
          </ng-template>

          <div class="image-copy">
            <strong>{{ imageTitle(index) }}</strong>
            <span>{{ formatTimestamp(image.created_at) }}</span>
          </div>

          <button class="danger" type="button" (click)="deleteImage(image.reference_image_id)">
            {{ i18n.t('common.delete') }}
          </button>
        </article>
      </div>
    </section>
  `,
  styles: [`
    :host {
      display: block;
    }

    .page-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 1.5rem;
      margin-bottom: 1.5rem;
    }

    .panel,
    .feedback {
      background: rgba(255, 252, 245, 0.88);
      border: 1px solid rgba(20, 60, 68, 0.12);
      border-radius: 28px;
      padding: 2rem;
      box-shadow: 0 24px 60px rgba(13, 40, 45, 0.08);
    }

    .eyebrow {
      margin: 0 0 0.8rem;
      text-transform: uppercase;
      letter-spacing: 0.18em;
      color: var(--accent-deep);
      font-size: 0.8rem;
    }

    h2 {
      margin: 0 0 0.9rem;
      font-family: 'Space Grotesk', sans-serif;
      font-size: 2rem;
      color: var(--ink-strong);
    }

    p,
    .field span,
    .image-copy span,
    .empty {
      color: var(--ink-soft);
      line-height: 1.7;
    }

    .field {
      display: grid;
      gap: 0.45rem;
      margin-top: 1.25rem;
    }

    select {
      width: 100%;
      border: 1px solid rgba(20, 60, 68, 0.18);
      border-radius: 16px;
      padding: 0.95rem 1rem;
      background: rgba(255, 255, 255, 0.92);
      color: var(--ink-strong);
      font: inherit;
    }

    .dropzone {
      display: grid;
      gap: 0.5rem;
      border: 1.5px dashed rgba(20, 82, 96, 0.35);
      border-radius: 24px;
      padding: 2rem;
      background: linear-gradient(180deg, rgba(235, 248, 246, 0.9), rgba(255, 255, 255, 0.95));
      cursor: pointer;
      text-align: center;
      color: var(--ink-strong);
      margin-top: 1rem;
    }

    .dropzone input {
      display: none;
    }

    .dropzone span {
      font-weight: 600;
      font-size: 1.05rem;
    }

    .actions,
    .gallery-header {
      display: flex;
      gap: 0.9rem;
      justify-content: space-between;
      align-items: center;
      margin-top: 1.25rem;
    }

    .preview-grid,
    .gallery-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 1rem;
      margin-top: 1rem;
    }

    .preview,
    .image-card img,
    .placeholder {
      width: 100%;
      height: 200px;
      object-fit: cover;
      border-radius: 20px;
      background: linear-gradient(135deg, rgba(193, 230, 223, 0.75), rgba(255, 238, 210, 0.8));
    }

    .image-card {
      display: grid;
      gap: 0.9rem;
      background: rgba(255, 255, 255, 0.82);
      border: 1px solid rgba(20, 60, 68, 0.1);
      border-radius: 24px;
      padding: 1rem;
    }

    .placeholder {
      display: grid;
      place-items: center;
    }

    .image-copy {
      display: grid;
      gap: 0.25rem;
    }

    .count-pill {
      border-radius: 999px;
      padding: 0.35rem 0.75rem;
      background: rgba(20, 82, 96, 0.08);
      color: var(--accent-deep);
      font-size: 0.85rem;
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

    button.danger {
      background: rgba(167, 52, 27, 0.12);
      color: #a7341b;
    }

    button:disabled {
      opacity: 0.7;
      cursor: wait;
    }

    .feedback {
      margin-bottom: 1rem;
    }

    .feedback.success {
      color: #1d6d4f;
    }

    .feedback.error {
      color: #a7341b;
    }

    @media (max-width: 900px) {
      .page-grid {
        grid-template-columns: 1fr;
      }

      .actions,
      .gallery-header {
        flex-direction: column;
        align-items: stretch;
      }
    }
  `],
})
export class UploadFaceComponent {
  private readonly http = inject(HttpClient);
  private readonly auth = inject(AuthService);
  private readonly router = inject(Router);
  private readonly destroyRef = inject(DestroyRef);
  protected readonly i18n = inject(I18nService);

  readonly pools = signal<PoolOption[]>([]);
  readonly referenceImages = signal<ReferenceImage[]>([]);
  readonly selectedFiles = signal<File[]>([]);
  readonly previewUrls = signal<string[]>([]);
  readonly uploading = signal(false);
  readonly savingPool = signal(false);
  readonly successMessage = signal('');
  readonly errorMessage = signal('');
  readonly backfillStatus = signal<'idle' | 'running' | 'done' | 'failed'>('idle');
  readonly backfillMessage = signal('');
  selectedPoolId = this.auth.selectedPoolId() ?? this.auth.poolId() ?? '';
  private backfillPollHandle: number | null = null;
  private backfillPollInFlight = false;
  private backfillJobIds: string[] = [];

  constructor() {
    this.destroyRef.onDestroy(() => {
      this.stopBackfillPolling();
      this.previewUrls().forEach((url) => URL.revokeObjectURL(url));
    });
    this.refresh();
  }

  refresh(): void {
    this.loadMe();
    this.loadPools();
    this.loadReferenceImages();
  }

  loadMe(): void {
    this.http
      .get<MeResponse>('/api/me', {
        headers: this.auth.authHeaders(),
      })
      .subscribe({
        next: (response) => {
          this.auth.setProfile(response);
          if (response.pool_id) {
            this.selectedPoolId = response.pool_id;
          }
        },
        error: (error) => this.handleUnauthorized(error),
      });
  }

  loadPools(): void {
    this.http
      .get<PoolOption[]>('/api/pools', {
        headers: this.auth.authHeaders(),
      })
      .subscribe({
        next: (response) => this.pools.set(response),
        error: (error) => this.handleError(error, 'uploadFace.loadPoolsFailed'),
      });
  }

  loadReferenceImages(): void {
    this.http
      .get<ReferenceImage[]>('/api/me/reference-images', {
        headers: this.auth.authHeaders(),
      })
      .subscribe({
        next: (response) => this.referenceImages.set(response),
        error: (error) => this.handleError(error, 'uploadFace.loadReferenceImagesFailed'),
      });
  }

  onFilesSelected(event: Event): void {
    const input = event.target as HTMLInputElement;
    const files = Array.from(input.files ?? []);
    this.previewUrls().forEach((url) => URL.revokeObjectURL(url));
    this.selectedFiles.set(files);
    this.successMessage.set('');
    this.errorMessage.set('');
    this.backfillMessage.set('');
    this.backfillStatus.set('idle');
    this.previewUrls.set(files.map((file) => URL.createObjectURL(file)));
  }

  onPoolSelectionChange(poolId: string): void {
    this.selectedPoolId = poolId || '';
    this.auth.setSelectedPoolId(this.selectedPoolId || null);
    this.successMessage.set('');
    this.errorMessage.set('');
    this.backfillMessage.set('');
  }

  selectedFilesLabel(): string {
    const files = this.selectedFiles();
    if (files.length === 0) {
      return '';
    }
    if (files.length === 1) {
      return this.i18n.t('uploadFace.filesSelected.single', { name: files[0].name });
    }
    return this.i18n.t('uploadFace.filesSelected.multiple', { count: files.length });
  }

  savePool(): void {
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
          this.auth.setSelectedPoolId(response.pool_id);
          this.savingPool.set(false);
          this.successMessage.set(this.i18n.t('uploadFace.poolUpdated'));
        },
        error: (error) => {
          this.savingPool.set(false);
          this.handleError(error, 'uploadFace.updatePoolFailed');
        },
      });
  }

  upload(): void {
    const files = this.selectedFiles();
    if (files.length === 0) {
      return;
    }

    this.ensurePoolSynced(() => {
      this.uploading.set(true);
      this.successMessage.set('');
      this.errorMessage.set('');
      this.stopBackfillPolling();
      this.backfillMessage.set('');
      this.backfillStatus.set('idle');

      const formData = new FormData();
      files.forEach((file) => formData.append('files', file));

      this.http
        .post<ReferenceImageUploadResponse>('/api/me/reference-images', formData, {
          headers: this.auth.authHeaders(),
        })
        .subscribe({
          next: (response) => {
            this.uploading.set(false);
            this.successMessage.set(this.i18n.t('uploadFace.uploadSucceeded', { count: response.uploaded }));
            this.selectedFiles.set([]);
            this.previewUrls().forEach((url) => URL.revokeObjectURL(url));
            this.previewUrls.set([]);
            this.loadReferenceImages();
            this.loadMe();
            if ((response.backfill_job_ids ?? []).length > 0) {
              this.backfillJobIds = response.backfill_job_ids ?? [];
              this.applyBackfillStatus(response.backfill_status ?? {
                status: 'running',
                message: 'Backfill running...',
                job_ids: this.backfillJobIds,
                jobs_total: this.backfillJobIds.length,
                jobs_completed: 0,
                jobs_failed: 0,
                jobs_running: 0,
                jobs_pending: this.backfillJobIds.length,
              });
              this.startBackfillPolling();
            }
          },
          error: (error) => {
            this.uploading.set(false);
            this.handleError(error, 'uploadFace.uploadFailed');
          },
        });
    });
  }

  deleteImage(referenceImageId: string): void {
    this.successMessage.set('');
    this.errorMessage.set('');
    this.http
      .delete<{ message: string }>(`/api/me/reference-images/${referenceImageId}`, {
        headers: this.auth.authHeaders(),
      })
      .subscribe({
        next: (response) => {
          this.successMessage.set(this.i18n.translateApiMessage(response.message, 'uploadFace.referenceImage'));
          this.loadReferenceImages();
          this.loadMe();
        },
        error: (error) => this.handleError(error, 'uploadFace.deleteFailed'),
      });
  }

  goToVideos(): void {
    this.router.navigate(['/my-videos']);
  }

  formatTimestamp(value: string): string {
    return this.i18n.formatDateTime(value);
  }

  imageTitle(index: number): string {
    if (index === 0) {
      return this.i18n.t('uploadFace.oldestRetained');
    }
    if (index === this.referenceImages().length - 1) {
      return this.i18n.t('uploadFace.newestRetained');
    }
    return this.i18n.t('uploadFace.referenceImage');
  }

  private ensurePoolSynced(nextAction: () => void): void {
    if (!this.selectedPoolId) {
      this.errorMessage.set(this.i18n.t('uploadFace.selectPoolRequired'));
      return;
    }

    if (this.selectedPoolId === (this.auth.poolId() ?? '')) {
      nextAction();
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
          this.auth.setSelectedPoolId(response.pool_id);
          this.savingPool.set(false);
          nextAction();
        },
        error: (error) => {
          this.savingPool.set(false);
          this.handleError(error, 'uploadFace.updatePoolFailed');
        },
      });
  }

  private handleUnauthorized(error: any): void {
    if (error?.status === 401) {
      this.stopBackfillPolling();
      this.auth.clearSession();
      this.router.navigate(['/login']);
    }
  }

  private startBackfillPolling(): void {
    if (this.backfillJobIds.length === 0) {
      return;
    }
    if (this.backfillPollHandle !== null) {
      return;
    }
    this.backfillPollHandle = window.setInterval(() => this.pollBackfillStatus(), 3000);
    this.pollBackfillStatus();
  }

  private stopBackfillPolling(): void {
    if (this.backfillPollHandle !== null) {
      window.clearInterval(this.backfillPollHandle);
      this.backfillPollHandle = null;
    }
    this.backfillPollInFlight = false;
  }

  private pollBackfillStatus(): void {
    if (this.backfillPollInFlight || this.backfillJobIds.length === 0) {
      return;
    }
    this.backfillPollInFlight = true;
    this.http
      .get<BackfillStatusResponse>(`/api/me/backfill-status?job_ids=${encodeURIComponent(this.backfillJobIds.join(','))}`, {
        headers: this.auth.authHeaders(),
      })
      .subscribe({
        next: (response) => {
          this.backfillPollInFlight = false;
          this.applyBackfillStatus(response);
          if (response.status === 'done' || response.status === 'failed' || response.status === 'idle') {
            this.stopBackfillPolling();
          }
        },
        error: (error) => {
          this.backfillPollInFlight = false;
          this.backfillStatus.set('failed');
          this.backfillMessage.set(this.i18n.translateApiMessage(error?.error?.detail, 'uploadFace.backfillFailed'));
          this.errorMessage.set(this.i18n.translateApiMessage(error?.error?.detail, 'uploadFace.backfillFailed'));
          this.stopBackfillPolling();
          this.handleUnauthorized(error);
        },
      });
  }

  private applyBackfillStatus(status: BackfillStatusResponse): void {
    this.backfillStatus.set(status.status);
    if (status.status === 'running') {
      this.backfillMessage.set(this.i18n.t('uploadFace.backfillRunning'));
      return;
    }
    if (status.status === 'done') {
      this.backfillMessage.set(this.i18n.t('uploadFace.backfillDone'));
      return;
    }
    if (status.status === 'failed') {
      this.backfillMessage.set(this.i18n.t('uploadFace.backfillFailed'));
      return;
    }
    this.backfillMessage.set('');
  }

  private handleError(error: any, fallbackKey: Parameters<I18nService['t']>[0]): void {
    this.handleUnauthorized(error);
    this.errorMessage.set(this.i18n.translateApiMessage(error?.error?.detail, fallbackKey));
  }
}
