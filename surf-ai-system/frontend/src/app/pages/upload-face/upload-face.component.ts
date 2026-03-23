import { CommonModule } from '@angular/common';
import { HttpClient } from '@angular/common/http';
import { Component, inject, signal } from '@angular/core';
import { Router } from '@angular/router';

import { AuthService } from '../../core/auth.service';

@Component({
  selector: 'app-upload-face-page',
  standalone: true,
  imports: [CommonModule],
  template: `
    <section class="panel">
      <div class="panel-copy">
        <p class="eyebrow">Face enrollment</p>
        <h2>Upload one clear face photo.</h2>
        <p>
          We use the existing InsightFace pipeline and the same blur and face-size filters as the
          backend matching flow.
        </p>
      </div>

      <div class="upload-card">
        <label class="dropzone">
          <input type="file" accept="image/*" (change)="onFileSelected($event)" />
          <span>{{ selectedFileName() || 'Choose image' }}</span>
          <small>Exactly one face, well-lit, close-up, and not blurry.</small>
        </label>

        <img *ngIf="previewUrl()" [src]="previewUrl()!" alt="Selected face preview" class="preview" />

        <div class="actions">
          <button (click)="upload()" [disabled]="!selectedFile() || uploading()">
            {{ uploading() ? 'Uploading...' : 'Upload face' }}
          </button>
          <button class="secondary" (click)="goToVideos()">My videos</button>
        </div>

        <p class="success" *ngIf="successMessage()">{{ successMessage() }}</p>
        <p class="error" *ngIf="errorMessage()">{{ errorMessage() }}</p>
      </div>
    </section>
  `,
  styles: [`
    :host {
      display: block;
    }

    .panel {
      display: grid;
      grid-template-columns: 1fr 1.1fr;
      gap: 1.75rem;
      align-items: start;
    }

    .panel-copy,
    .upload-card {
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
      font-size: 2.2rem;
      color: var(--ink-strong);
    }

    p {
      margin: 0;
      color: var(--ink-soft);
      line-height: 1.7;
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
    }

    .dropzone input {
      display: none;
    }

    .dropzone span {
      font-weight: 600;
      font-size: 1.05rem;
    }

    .dropzone small {
      color: var(--ink-soft);
      font-size: 0.92rem;
    }

    .preview {
      width: 100%;
      max-height: 420px;
      object-fit: cover;
      border-radius: 24px;
      margin-top: 1.25rem;
      box-shadow: 0 18px 34px rgba(13, 40, 45, 0.12);
    }

    .actions {
      display: flex;
      gap: 0.9rem;
      margin-top: 1.25rem;
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

    button:disabled {
      opacity: 0.7;
      cursor: wait;
    }

    .secondary {
      background: rgba(20, 60, 68, 0.08);
      color: var(--ink-strong);
    }

    .success,
    .error {
      margin-top: 1rem;
    }

    .success {
      color: #1d6d4f;
    }

    .error {
      color: #a7341b;
    }

    @media (max-width: 900px) {
      .panel {
        grid-template-columns: 1fr;
      }

      .actions {
        flex-direction: column;
      }
    }
  `],
})
export class UploadFaceComponent {
  private readonly http = inject(HttpClient);
  private readonly auth = inject(AuthService);
  private readonly router = inject(Router);

  readonly selectedFile = signal<File | null>(null);
  readonly selectedFileName = signal('');
  readonly previewUrl = signal<string | null>(null);
  readonly uploading = signal(false);
  readonly successMessage = signal('');
  readonly errorMessage = signal('');

  onFileSelected(event: Event): void {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0] ?? null;
    this.selectedFile.set(file);
    this.selectedFileName.set(file?.name ?? '');
    this.successMessage.set('');
    this.errorMessage.set('');

    if (!file) {
      this.previewUrl.set(null);
      return;
    }

    this.previewUrl.set(URL.createObjectURL(file));
  }

  upload(): void {
    const file = this.selectedFile();
    if (!file) {
      return;
    }

    this.uploading.set(true);
    this.successMessage.set('');
    this.errorMessage.set('');

    const formData = new FormData();
    formData.append('file', file);

    this.http
      .post<{ embeddings_count: number; message: string }>('/api/users/upload-face', formData, {
        headers: this.auth.authHeaders(),
      })
      .subscribe({
        next: (response) => {
          this.uploading.set(false);
          this.successMessage.set(`${response.message}. Stored embeddings: ${response.embeddings_count}.`);
        },
        error: (error) => {
          this.uploading.set(false);
          const detail = error.error?.detail;
          this.errorMessage.set(detail?.message || detail || 'Upload failed.');
          if (error.status === 401) {
            this.auth.clearSession();
            this.router.navigate(['/login']);
          }
        },
      });
  }

  goToVideos(): void {
    this.router.navigate(['/my-videos']);
  }
}
