import { CommonModule } from '@angular/common';
import { Component, computed, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { AdminSystemService } from './admin-system.service';
import { AdminVideosService } from './admin-videos.service';

@Component({
  selector: 'app-admin-dashboard-page',
  standalone: true,
  imports: [CommonModule, FormsModule],
  host: { class: 'admin-page' },
  template: `
    <section class="hero">
      <div>
        <p class="eyebrow">{{ admin.i18n.t('admin.heroEyebrow') }}</p>
        <h2>{{ admin.i18n.t('admin.heroTitle') }}</h2>
        <p class="subcopy">{{ admin.i18n.t('admin.heroSubtitle') }}</p>
      </div>

      <div class="hero-actions">
        <span class="pill live" *ngIf="admin.isPolling()">{{ admin.i18n.t('admin.pipelineLive') }}</span>
        <button type="button" (click)="admin.refresh()" [disabled]="admin.loading()">
          {{ admin.loading() ? admin.i18n.t('common.refreshing') : admin.i18n.t('common.refresh') }}
        </button>
      </div>
    </section>

    <section class="feedback error" *ngIf="admin.errorMessage()">{{ admin.errorMessage() }}</section>
    <section class="feedback success" *ngIf="admin.successMessage()">{{ admin.successMessage() }}</section>
    <section class="feedback" *ngIf="admin.poolSelectionDirty()">{{ admin.i18n.t('admin.poolSelectionPending') }}</section>
    <section class="state-card" *ngIf="admin.loading()">{{ admin.i18n.t('common.loading') }}</section>

    <ng-container *ngIf="!admin.loading()">
      <section class="panel">
        <div class="section-header">
          <div>
            <p class="panel-label">Monitoring</p>
            <h3>{{ admin.activePoolName() || admin.i18n.t('admin.noActivePool') }}</h3>
          </div>
        </div>

        <div class="summary-grid">
          <article class="summary-card">
            <span>{{ admin.i18n.t('admin.videos') }}</span>
            <strong>{{ admin.videos().length }}</strong>
            <small>{{ admin.i18n.t('admin.pendingPipeline', { count: admin.pendingVideosCount() }) }}</small>
          </article>
          <article class="summary-card">
            <span>{{ admin.i18n.t('admin.poolUsers') }}</span>
            <strong>{{ admin.users().length }}</strong>
            <small>{{ admin.i18n.t('admin.usersCount', { count: admin.users().length }) }}</small>
          </article>
          <article class="summary-card">
            <span>{{ admin.i18n.t('admin.cameras') }}</span>
            <strong>{{ admin.cameras().length }}</strong>
            <small>{{ admin.i18n.t('admin.activeSources') }}</small>
          </article>
          <article class="summary-card">
            <span>{{ admin.i18n.t('admin.matchesPerVideo') }}</span>
            <strong>{{ admin.formatInteger(admin.totalMatchesCount()) }}</strong>
            <small>{{ admin.i18n.t('admin.metricsDashboard') }}</small>
          </article>
        </div>

        <div class="overview-grid">
          <article class="overview-card">
            <div class="section-header compact">
              <div>
                <p class="panel-label">{{ admin.i18n.t('admin.videos') }}</p>
                <h3>{{ admin.i18n.t('admin.processingStatus') }}</h3>
              </div>
            </div>

            <div class="status-grid">
              <div class="status-card">
                <span>{{ admin.i18n.t('admin.status.uploaded') }}</span>
                <strong>{{ admin.uploadedCount() }}</strong>
              </div>
              <div class="status-card">
                <span>{{ admin.i18n.t('admin.status.processing') }}</span>
                <strong>{{ admin.processingCount() }}</strong>
              </div>
              <div class="status-card">
                <span>{{ admin.i18n.t('admin.status.completed') }}</span>
                <strong>{{ admin.completedCount() }}</strong>
              </div>
              <div class="status-card">
                <span>{{ admin.i18n.t('admin.status.failed') }}</span>
                <strong>{{ admin.failedCount() }}</strong>
              </div>
            </div>
          </article>

          <article class="overview-card metrics-board">
            <div class="section-header compact">
              <div>
                <p class="panel-label">{{ admin.i18n.t('admin.metricsDashboard') }}</p>
                <h3>{{ admin.i18n.t('admin.pipelineVisibilityTitle') }}</h3>
              </div>
            </div>

            <div class="status-grid">
              <div class="status-card">
                <span>{{ admin.i18n.t('admin.rejectionRate') }}</span>
                <strong>{{ admin.formatPercent(admin.metrics().matching.rejection_rate) }}</strong>
              </div>
              <div class="status-card">
                <span>{{ admin.i18n.t('admin.avgSimilarity') }}</span>
                <strong>{{ admin.formatMetric(admin.metrics().matching.average_match_similarity) }}</strong>
              </div>
              <div class="status-card">
                <span>{{ admin.i18n.t('admin.avgMargin') }}</span>
                <strong>{{ admin.formatMetric(admin.metrics().matching.average_match_margin) }}</strong>
              </div>
              <div class="status-card">
                <span>{{ admin.i18n.t('admin.debugPrimary') }}</span>
                <strong>{{ admin.debugVideos().length }}</strong>
              </div>
            </div>
          </article>
        </div>
      </section>

      <section class="panel">
        <div class="section-header">
          <div>
            <p class="panel-label">{{ admin.i18n.t('admin.videoUpload') }}</p>
            <h3>{{ admin.i18n.t('admin.processingStatus') }}</h3>
          </div>
        </div>

        <div class="video-upload-card">
          <label class="dropzone">
            <input type="file" accept="video/*" (change)="admin.onVideoSelected($event)" />
            <span>{{ admin.selectedVideoName() || admin.i18n.t('admin.chooseVideoFile') }}</span>
            <small>{{ admin.i18n.t('admin.videoUploadHint') }}</small>
          </label>

          <div class="actions">
            <button type="button" (click)="admin.uploadVideo()" [disabled]="!admin.selectedVideo() || admin.uploadingVideo()">
              {{ admin.uploadingVideo() ? admin.i18n.t('common.uploading') : admin.i18n.t('admin.uploadVideo') }}
            </button>
          </div>
        </div>

        <div class="empty" *ngIf="!system.selectedPoolId()">{{ admin.i18n.t('admin.selectPoolToContinue') }}</div>
        <div class="empty" *ngIf="system.selectedPoolId() && admin.videos().length === 0">{{ admin.i18n.t('admin.noVideos') }}</div>

        <div class="video-list" *ngIf="admin.videos().length > 0">
          <article class="video-row" *ngFor="let video of admin.videos()">
            <div class="video-copy">
              <div class="video-headline">
                <strong>{{ video.video_id }}</strong>
                <span class="status" [class.completed]="video.status === 'completed'" [class.failed]="video.status === 'failed'">
                  {{ admin.videoStatusLabel(video.status) }}
                </span>
              </div>

              <small>{{ admin.i18n.t('admin.createdAt', { value: admin.formatTimestamp(video.created_at) }) }}</small>
              <small>{{ admin.i18n.t('admin.updatedAt', { value: admin.formatTimestamp(video.updated_at) }) }}</small>
              <a *ngIf="video.source_video_url" [href]="video.source_video_url" target="_blank" rel="noopener">
                {{ admin.i18n.t('admin.openSource') }}
              </a>
              <small class="error-copy" *ngIf="video.error_message">{{ video.error_message }}</small>

              <div class="progress-row">
                <div class="progress-track">
                  <span class="progress-fill" [style.width.%]="video.progress_percent ?? 0"></span>
                </div>
                <strong>{{ admin.i18n.t('admin.progressLabel', { value: video.progress_percent ?? 0 }) }}</strong>
              </div>

              <div class="pipeline-stages">
                <span class="stage" [ngClass]="admin.pipelineStageClass(video.stage_status?.upload)">
                  {{ admin.i18n.t('admin.stage.upload') }} · {{ admin.pipelineStageStatusLabel(video.stage_status?.upload) }}
                </span>
                <span class="stage" [ngClass]="admin.pipelineStageClass(video.stage_status?.frame)">
                  {{ admin.i18n.t('admin.stage.frame') }} · {{ admin.pipelineStageStatusLabel(video.stage_status?.frame) }}
                </span>
                <span class="stage" [ngClass]="admin.pipelineStageClass(video.stage_status?.embedding)">
                  {{ admin.i18n.t('admin.stage.embedding') }} · {{ admin.pipelineStageStatusLabel(video.stage_status?.embedding) }}
                </span>
                <span class="stage" [ngClass]="admin.pipelineStageClass(video.stage_status?.matching)">
                  {{ admin.i18n.t('admin.stage.matching') }} · {{ admin.pipelineStageStatusLabel(video.stage_status?.matching) }}
                </span>
              </div>

              <div class="metrics">
                <span>{{ admin.i18n.t('admin.tracksTotal', { count: video.tracks_total ?? 0 }) }}</span>
                <span>{{ admin.i18n.t('admin.processedCount', { count: video.tracks_processed ?? 0 }) }}</span>
                <span>{{ admin.i18n.t('admin.pendingCount', { count: video.tracks_pending ?? 0 }) }}</span>
                <span>{{ admin.i18n.t('admin.matchedCount', { count: video.tracks_matched ?? 0 }) }}</span>
                <span>{{ admin.i18n.t('admin.unmatchedCount', { count: video.tracks_unmatched ?? 0 }) }}</span>
                <span>{{ admin.i18n.t('admin.rejectedCount', { count: video.tracks_rejected ?? 0 }) }}</span>
              </div>

              <small class="hint">{{ admin.videoOutcomeSummary(video) }}</small>
              <small class="hint" *ngIf="admin.qualityGuardSummary(video)">{{ admin.qualityGuardSummary(video) }}</small>

              <div class="assign-bar" *ngIf="admin.users().length > 0">
                <select [ngModel]="admin.assignmentSelection(video)" (ngModelChange)="admin.setAssignmentSelection(video.video_id, $event)">
                  <option [ngValue]="''">{{ admin.i18n.t('admin.noAssignment') }}</option>
                  <option *ngFor="let user of admin.users()" [ngValue]="user.user_id">{{ user.email }}</option>
                </select>
                <button class="secondary" type="button" (click)="admin.assignVideo(video)" [disabled]="admin.assigningVideoId() === video.video_id">
                  {{ admin.assigningVideoId() === video.video_id ? admin.i18n.t('common.saving') : admin.i18n.t('admin.saveAssignment') }}
                </button>
              </div>
            </div>

            <div class="video-actions">
              <button class="secondary" type="button" (click)="admin.triggerProcessing(video.video_id)" [disabled]="admin.processingVideoId() === video.video_id">
                {{ admin.processingVideoId() === video.video_id ? admin.i18n.t('admin.queueing') : admin.i18n.t('admin.triggerPipeline') }}
              </button>
              <button type="button" (click)="admin.openVideoDebug(video.video_id)">
                {{ admin.i18n.t('admin.quickOpenDebug') }}
              </button>
            </div>
          </article>
        </div>
      </section>

      <div class="overview-grid">
        <section class="panel">
          <div class="section-header">
            <div>
              <p class="panel-label">{{ admin.i18n.t('admin.poolUsers') }}</p>
              <h3>{{ admin.i18n.t('admin.usersInActivePool') }}</h3>
            </div>
            <span class="pill">{{ admin.i18n.t('admin.usersCount', { count: admin.users().length }) }}</span>
          </div>

          <div class="empty" *ngIf="!system.selectedPoolId()">{{ admin.i18n.t('admin.selectPoolForUsers') }}</div>
          <div class="empty" *ngIf="system.selectedPoolId() && admin.users().length === 0">{{ admin.i18n.t('admin.noUsersInPool') }}</div>

          <div class="user-list" *ngIf="admin.users().length > 0">
            <article class="user-row" *ngFor="let user of admin.users()">
              <img *ngIf="user.latest_reference_image_url; else noUserImage" [src]="user.latest_reference_image_url" alt="Latest reference image" />
              <ng-template #noUserImage>
                <div class="user-placeholder">{{ admin.i18n.t('admin.noImage') }}</div>
              </ng-template>

              <div class="user-copy">
                <strong>{{ user.email }}</strong>
                <span>{{ admin.roleLabel(user.role) }}</span>
                <small>{{ admin.i18n.t('admin.referenceImagesCount', { count: user.reference_images_count }) }}</small>
              </div>
            </article>
          </div>
        </section>

        <section class="panel">
          <div class="section-header">
            <div>
              <p class="panel-label">{{ admin.i18n.t('admin.cameraSource') }}</p>
              <h3>{{ admin.i18n.t('admin.registerOrUpdateCamera') }}</h3>
            </div>
          </div>

          <label>
            <span>{{ admin.i18n.t('admin.name') }}</span>
            <input [(ngModel)]="system.cameraForm.name" [placeholder]="admin.i18n.t('admin.namePlaceholder')" />
          </label>

          <label>
            <span>{{ admin.i18n.t('admin.url') }}</span>
            <input [(ngModel)]="system.cameraForm.url" [placeholder]="admin.i18n.t('admin.urlPlaceholder')" />
          </label>

          <label class="checkbox">
            <input type="checkbox" [(ngModel)]="system.cameraForm.active" />
            <span>{{ admin.i18n.t('admin.activeCheckbox') }}</span>
          </label>

          <div class="actions">
            <button type="button" (click)="admin.saveCamera()" [disabled]="admin.savingCamera()">
              {{ admin.savingCamera() ? admin.i18n.t('common.saving') : admin.i18n.t('admin.saveCamera') }}
            </button>
          </div>

          <div class="camera-list" *ngIf="admin.cameras().length > 0">
            <article class="camera-row" *ngFor="let camera of admin.cameras()">
              <div>
                <strong>{{ camera.name }}</strong>
                <span>{{ camera.url }}</span>
              </div>
              <span class="status" [class.completed]="camera.active" [class.failed]="!camera.active">
                {{ camera.active ? admin.i18n.t('common.active') : admin.i18n.t('common.inactive') }}
              </span>
            </article>
          </div>
        </section>
      </div>

      <section class="panel">
        <div class="section-header">
          <div>
            <p class="panel-label">{{ admin.i18n.t('admin.poolManagement') }}</p>
            <h3>{{ admin.i18n.t('admin.createAndActivatePools') }}</h3>
          </div>
        </div>

        <label>
          <span>{{ admin.i18n.t('admin.activePool') }}</span>
          <select [ngModel]="system.selectedPoolId()" (ngModelChange)="admin.onPoolSelectionChange($event)">
            <option [ngValue]="''">{{ admin.i18n.t('common.choosePool') }}</option>
            <option *ngFor="let pool of admin.pools()" [ngValue]="pool.pool_id">{{ pool.name }}</option>
          </select>
        </label>

        <div class="actions">
          <button type="button" (click)="admin.saveActivePool()" [disabled]="admin.savingPool() || !system.selectedPoolId()">
            {{ admin.savingPool() ? admin.i18n.t('common.saving') : admin.i18n.t('admin.setActivePool') }}
          </button>
        </div>

        <label>
          <span>{{ admin.i18n.t('admin.newPoolName') }}</span>
          <input [(ngModel)]="system.newPoolName" [placeholder]="admin.i18n.t('admin.newPoolPlaceholder')" />
        </label>

        <div class="actions">
          <button type="button" (click)="admin.createPool()" [disabled]="admin.creatingPool() || !system.newPoolName.trim()">
            {{ admin.creatingPool() ? admin.i18n.t('common.working') : admin.i18n.t('admin.createPool') }}
          </button>
        </div>

        <div class="pool-list" *ngIf="admin.pools().length > 0">
          <article class="pool-row" *ngFor="let pool of admin.pools()">
            <div>
              <strong>{{ pool.name }}</strong>
              <span>{{ pool.pool_id }}</span>
            </div>
            <span class="pill" *ngIf="pool.pool_id === system.selectedPoolId()">{{ admin.i18n.t('admin.activePoolTag') }}</span>
          </article>
        </div>
      </section>
    </ng-container>
  `,
  styles: [`
    .metrics-board,
    .metric-row,
    .progress-row,
    .progress-track,
    .metrics-list {
      display: block;
    }

    .metrics-board {
      margin-top: 1.5rem;
    }

    .metrics-list {
      display: grid;
      gap: 0.85rem;
      margin-top: 1rem;
    }

    .metric-row {
      display: flex;
      justify-content: space-between;
      gap: 1rem;
      padding: 0.9rem 1rem;
      border: 1px solid rgba(20, 60, 68, 0.1);
      border-radius: 18px;
      background: rgba(255, 255, 255, 0.65);
    }

    .progress-row {
      display: flex;
      align-items: center;
      gap: 0.85rem;
      margin-top: 0.85rem;
    }

    .progress-track {
      position: relative;
      flex: 1;
      height: 10px;
      border-radius: 999px;
      overflow: hidden;
      background: rgba(20, 60, 68, 0.12);
    }

    .progress-fill {
      display: block;
      height: 100%;
      border-radius: inherit;
      background: linear-gradient(90deg, var(--accent-deep), var(--accent));
    }

    .compact {
      gap: 0.45rem;
    }

    .stage.pending {
      opacity: 0.7;
    }

    .stage.processing,
    .stage.completed {
      border-radius: 999px;
      padding: 0.2rem 0.55rem;
    }

    .stage.processing {
      background: rgba(20, 82, 96, 0.12);
    }

    .stage.completed {
      background: rgba(29, 109, 79, 0.12);
    }
  `],
})
export class AdminComponent {
  protected readonly system = inject(AdminSystemService);
  private readonly videos = inject(AdminVideosService);
  private readonly pageLoading = computed(() => this.system.loading() || this.videos.loading());

  protected readonly admin = {
    auth: this.system.auth,
    i18n: this.system.i18n,
    refresh: () => this.refresh(),
    loading: this.pageLoading,
    errorMessage: this.system.errorMessage,
    successMessage: this.system.successMessage,
    poolSelectionDirty: this.system.poolSelectionDirty,
    activePoolName: this.system.activePoolName,
    videos: this.videos.videos,
    debugVideos: this.videos.debugVideos,
    users: this.system.users,
    cameras: this.system.cameras,
    pools: this.system.pools,
    metrics: this.system.metrics,
    totalMatchesCount: this.system.totalMatchesCount,
    pendingVideosCount: this.videos.pendingVideosCount,
    uploadedCount: this.videos.uploadedCount,
    processingCount: this.videos.processingCount,
    completedCount: this.videos.completedCount,
    failedCount: this.videos.failedCount,
    isPolling: this.videos.isPolling,
    onVideoSelected: (event: Event) => this.videos.onVideoSelected(event),
    selectedVideoName: this.videos.selectedVideoName,
    selectedVideo: this.videos.selectedVideo,
    uploadVideo: () => this.videos.uploadVideo(),
    uploadingVideo: this.videos.uploadingVideo,
    videoStatusLabel: (status: Parameters<AdminSystemService['videoStatusLabel']>[0]) => this.system.videoStatusLabel(status),
    formatTimestamp: (value: string) => this.system.formatTimestamp(value),
    formatInteger: (value: number | null | undefined) => this.system.formatInteger(value),
    formatPercent: (value: number | null | undefined) => this.system.formatPercent(value),
    formatMetric: (value: number | null | undefined, digits?: number) => this.system.formatMetric(value, digits),
    pipelineStageClass: (status: Parameters<AdminSystemService['pipelineStageClass']>[0]) => this.system.pipelineStageClass(status),
    pipelineStageStatusLabel: (status: Parameters<AdminSystemService['pipelineStageStatusLabel']>[0]) => this.system.pipelineStageStatusLabel(status),
    assignmentSelection: (video: Parameters<AdminVideosService['assignmentSelection']>[0]) => this.videos.assignmentSelection(video),
    setAssignmentSelection: (videoId: string, userId: string) => this.videos.setAssignmentSelection(videoId, userId),
    assignVideo: (video: Parameters<AdminVideosService['assignVideo']>[0]) => this.videos.assignVideo(video),
    assigningVideoId: this.videos.assigningVideoId,
    triggerProcessing: (videoId: string) => this.videos.triggerProcessing(videoId),
    processingVideoId: this.videos.processingVideoId,
    openVideoDebug: (videoId: string) => this.videos.openVideoDebug(videoId),
    videoOutcomeSummary: (video: Parameters<AdminVideosService['videoOutcomeSummary']>[0]) => this.videos.videoOutcomeSummary(video),
    qualityGuardSummary: (video: Parameters<AdminVideosService['qualityGuardSummary']>[0]) => this.videos.qualityGuardSummary(video),
    roleLabel: (role: Parameters<AdminSystemService['roleLabel']>[0]) => this.system.roleLabel(role),
    saveCamera: () => this.system.saveCamera(),
    savingCamera: this.system.savingCamera,
    onPoolSelectionChange: (poolId: string) => this.system.onPoolSelectionChange(poolId),
    saveActivePool: () => this.system.saveActivePool(),
    savingPool: this.system.savingPool,
    createPool: () => this.system.createPool(),
    creatingPool: this.system.creatingPool,
  };

  protected refresh(): void {
    this.system.refresh();
  }
}
