import { CommonModule } from '@angular/common';
import { HttpClient } from '@angular/common/http';
import { Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';

import { AuthService, MeResponse } from '../../core/auth.service';

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

@Component({
  selector: 'app-upload-face-page',
  standalone: true,
  imports: [CommonModule, FormsModule],
  template: `
    <section class="page-grid">
      <article class="panel">
        <p class="eyebrow">Profile</p>
        <h2>Pool membership and reference images.</h2>
        <p>
          Choose the pool you belong to, upload multiple reference photos, and manage the images the
          matcher uses for your account.
        </p>

        <label class="field">
          <span>My pool</span>
          <select [(ngModel)]="selectedPoolId">
            <option [ngValue]="''">Choose a pool</option>
            <option *ngFor="let pool of pools()" [ngValue]="pool.pool_id">{{ pool.name }}</option>
          </select>
        </label>

        <div class="actions">
          <button type="button" (click)="savePool()" [disabled]="savingPool() || !selectedPoolId">
            {{ savingPool() ? 'Saving...' : 'Save pool' }}
          </button>
          <button class="secondary" type="button" (click)="goToVideos()">My videos</button>
        </div>
      </article>

      <article class="panel">
        <p class="eyebrow">Reference images</p>
        <h2>Upload clear face photos.</h2>
        <p>Multiple uploads are supported. The newest valid images are kept as your active references.</p>

        <label class="dropzone">
          <input type="file" accept="image/*" multiple (change)="onFilesSelected($event)" />
          <span>{{ selectedFilesLabel() || 'Choose one or more images' }}</span>
          <small>Exactly one face per image, well lit, close-up, and not blurry.</small>
        </label>

        <div class="preview-grid" *ngIf="previewUrls().length > 0">
          <img *ngFor="let preview of previewUrls()" [src]="preview" alt="Selected face preview" class="preview" />
        </div>

        <div class="actions">
          <button type="button" (click)="upload()" [disabled]="selectedFiles().length === 0 || uploading()">
            {{ uploading() ? 'Uploading...' : 'Upload reference images' }}
          </button>
          <button class="secondary" type="button" (click)="refresh()">Refresh</button>
        </div>
      </article>
    </section>

    <section class="feedback success" *ngIf="successMessage()">{{ successMessage() }}</section>
    <section class="feedback error" *ngIf="errorMessage()">{{ errorMessage() }}</section>

    <section class="panel gallery-panel">
      <div class="gallery-header">
        <div>
          <p class="eyebrow">Gallery</p>
          <h2>My reference images</h2>
        </div>
        <span class="count-pill">{{ referenceImages().length }} stored</span>
      </div>

      <div class="empty" *ngIf="referenceImages().length === 0">No reference images uploaded yet.</div>

      <div class="gallery-grid" *ngIf="referenceImages().length > 0">
        <article class="image-card" *ngFor="let image of referenceImages(); let index = index">
          <img *ngIf="image.image_url; else missingImage" [src]="image.image_url" alt="Reference image" />
          <ng-template #missingImage>
            <div class="placeholder">Image unavailable</div>
          </ng-template>

          <div class="image-copy">
            <strong>{{ index === 0 ? 'Oldest retained' : index === referenceImages().length - 1 ? 'Newest retained' : 'Reference image' }}</strong>
            <span>{{ formatTimestamp(image.created_at) }}</span>
          </div>

          <button class="danger" type="button" (click)="deleteImage(image.reference_image_id)">
            Delete
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

  readonly pools = signal<PoolOption[]>([]);
  readonly referenceImages = signal<ReferenceImage[]>([]);
  readonly selectedFiles = signal<File[]>([]);
  readonly previewUrls = signal<string[]>([]);
  readonly uploading = signal(false);
  readonly savingPool = signal(false);
  readonly successMessage = signal('');
  readonly errorMessage = signal('');
  selectedPoolId = '';

  constructor() {
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
          this.selectedPoolId = response.pool_id ?? '';
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
        error: (error) => this.handleError(error, 'Unable to load pools.'),
      });
  }

  loadReferenceImages(): void {
    this.http
      .get<ReferenceImage[]>('/api/me/reference-images', {
        headers: this.auth.authHeaders(),
      })
      .subscribe({
        next: (response) => this.referenceImages.set(response),
        error: (error) => this.handleError(error, 'Unable to load reference images.'),
      });
  }

  onFilesSelected(event: Event): void {
    const input = event.target as HTMLInputElement;
    const files = Array.from(input.files ?? []);
    this.selectedFiles.set(files);
    this.successMessage.set('');
    this.errorMessage.set('');
    this.previewUrls.set(files.map((file) => URL.createObjectURL(file)));
  }

  selectedFilesLabel(): string {
    const files = this.selectedFiles();
    if (files.length === 0) {
      return '';
    }
    if (files.length === 1) {
      return files[0].name;
    }
    return `${files.length} images selected`;
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
          this.savingPool.set(false);
          this.successMessage.set('Pool updated successfully.');
        },
        error: (error) => {
          this.savingPool.set(false);
          this.handleError(error, 'Unable to update pool.');
        },
      });
  }

  upload(): void {
    const files = this.selectedFiles();
    if (files.length === 0) {
      return;
    }

    this.uploading.set(true);
    this.successMessage.set('');
    this.errorMessage.set('');

    const formData = new FormData();
    files.forEach((file) => formData.append('files', file));

    this.http
      .post<{ uploaded: number; message: string }>('/api/me/reference-images', formData, {
        headers: this.auth.authHeaders(),
      })
      .subscribe({
        next: (response) => {
          this.uploading.set(false);
          this.successMessage.set(`${response.message}. Uploaded ${response.uploaded}.`);
          this.selectedFiles.set([]);
          this.previewUrls.set([]);
          this.loadReferenceImages();
          this.loadMe();
        },
        error: (error) => {
          this.uploading.set(false);
          this.handleError(error, 'Upload failed.');
        },
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
          this.successMessage.set(response.message);
          this.loadReferenceImages();
          this.loadMe();
        },
        error: (error) => this.handleError(error, 'Unable to delete reference image.'),
      });
  }

  goToVideos(): void {
    this.router.navigate(['/my-videos']);
  }

  formatTimestamp(value: string): string {
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
  }

  private handleUnauthorized(error: any): void {
    if (error?.status === 401) {
      this.auth.clearSession();
      this.router.navigate(['/login']);
    }
  }

  private handleError(error: any, fallbackMessage: string): void {
    this.handleUnauthorized(error);
    const detail = error?.error?.detail;
    this.errorMessage.set(detail?.message || detail || fallbackMessage);
  }
}
