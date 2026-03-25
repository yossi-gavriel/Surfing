import { DestroyRef, computed, effect, inject, Injectable, signal } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Router } from '@angular/router';
import { forkJoin } from 'rxjs';

import { I18nService } from '../../core/i18n.service';
import { AdminDebugCacheService } from './admin-debug-cache.service';
import { AdminMetricsResponse, AdminVideo, VideoStatus } from './admin.models';
import { AdminContextService } from './admin-context.service';
import { AdminSystemService } from './admin-system.service';

@Injectable()
export class AdminVideosService {
  private readonly http = inject(HttpClient);
  private readonly router = inject(Router);
  private readonly i18n = inject(I18nService);
  private readonly destroyRef = inject(DestroyRef);
  private readonly context = inject(AdminContextService);
  private readonly debugCache = inject(AdminDebugCacheService);
  readonly system = inject(AdminSystemService);

  readonly videos = signal<AdminVideo[]>([]);
  readonly debugVideos = signal<AdminVideo[]>([]);
  readonly loading = signal(false);
  readonly uploadingVideo = signal(false);
  readonly processingVideoId = signal<string | null>(null);
  readonly assigningVideoId = signal<string | null>(null);
  readonly selectedVideo = signal<File | null>(null);
  readonly selectedVideoName = signal('');
  readonly assignmentSelections = signal<Record<string, string>>({});
  readonly isPolling = signal(false);

  readonly pendingVideosCount = computed(
    () => this.videos().filter((video) => video.status === 'uploaded' || video.status === 'processing').length,
  );
  readonly uploadedCount = computed(() => this.videos().filter((video) => video.status === 'uploaded').length);
  readonly processingCount = computed(() => this.videos().filter((video) => video.status === 'processing').length);
  readonly completedCount = computed(() => this.videos().filter((video) => video.status === 'completed').length);
  readonly failedCount = computed(() => this.videos().filter((video) => video.status === 'failed').length);

  private pollHandle: number | null = null;
  private pollInFlight = false;

  constructor() {
    effect(() => {
      if (!this.system.contextReady()) {
        return;
      }

      if (!this.system.selectedPoolId() || this.system.poolSelectionDirty()) {
        this.clearState();
        this.stopPolling();
        return;
      }

      this.refresh();
    });

    this.destroyRef.onDestroy(() => this.stopPolling());
  }

  refresh(): void {
    if (!this.system.selectedPoolId() || this.system.poolSelectionDirty()) {
      this.clearState();
      this.stopPolling();
      return;
    }

    this.loading.set(true);
    this.http
      .get<AdminVideo[]>('/api/admin/videos?include_debug=false', {
        headers: this.system.auth.authHeaders(),
      })
      .subscribe({
        next: (videos) => {
          this.videos.set(videos);
          this.syncAssignmentSelections(videos);
          this.loading.set(false);
          this.syncPollingState();
        },
        error: (error) => {
          this.loading.set(false);
          this.system.handleHttpError(error, 'admin.loadVideosFailed');
        },
      });

    this.refreshDebugVideos();
  }

  refreshDebugVideos(): void {
    if (!this.system.selectedPoolId() || this.system.poolSelectionDirty()) {
      this.debugVideos.set([]);
      return;
    }

    this.http
      .get<AdminVideo[]>('/api/admin/videos?include_debug=true', {
        headers: this.system.auth.authHeaders(),
      })
      .subscribe({
        next: (videos) => {
          this.debugVideos.set(videos);
          this.syncPollingState();
        },
        error: (error) => this.system.handleHttpError(error, 'admin.loadVideosFailed'),
      });
  }

  onVideoSelected(event: Event): void {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0] ?? null;
    this.selectedVideo.set(file);
    this.selectedVideoName.set(file?.name ?? '');
    this.system.clearMessages();
  }

  uploadVideo(): void {
    const file = this.selectedVideo();
    if (!file) {
      return;
    }

    this.system.ensureActivePoolSynced(() => {
      const formData = new FormData();
      formData.append('file', file);

      this.uploadingVideo.set(true);
      this.system.clearMessages();

      this.http
        .post<AdminVideo>('/api/admin/upload-video', formData, {
          headers: this.system.auth.authHeaders(),
        })
        .subscribe({
          next: (response) => {
            this.uploadingVideo.set(false);
            this.selectedVideo.set(null);
            this.selectedVideoName.set('');
            this.system.successMessage.set(this.i18n.translateApiMessage(response.message, 'admin.videoUpload'));
            this.upsertVideo(response);
            this.syncPollingState();
            this.pollVideoState();
          },
          error: (error) => {
            this.uploadingVideo.set(false);
            this.system.handleHttpError(error, 'admin.videoUploadFailed');
          },
        });
    });
  }

  triggerProcessing(videoId: string): void {
    this.processingVideoId.set(videoId);
    this.system.clearMessages();

    this.http
      .post<{ message: string }>(`/api/admin/videos/${videoId}/process`, {}, {
        headers: this.system.auth.authHeaders(),
      })
      .subscribe({
        next: (response) => {
          this.processingVideoId.set(null);
          this.debugCache.invalidate(videoId);
          this.system.successMessage.set(this.i18n.translateApiMessage(response.message, 'admin.triggerPipeline'));
          this.updateVideoStatusLocally(videoId, 'uploaded');
          this.syncPollingState();
          this.pollVideoState();
        },
        error: (error) => {
          this.processingVideoId.set(null);
          this.system.handleHttpError(error, 'admin.triggerProcessingFailed');
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
    this.system.clearMessages();

    this.http
      .post<{ message: string }>(`/api/admin/videos/${video.video_id}/assign`, { user_id: userId }, {
        headers: this.system.auth.authHeaders(),
      })
      .subscribe({
        next: (response) => {
          this.assigningVideoId.set(null);
          this.debugCache.invalidate(video.video_id);
          this.system.successMessage.set(this.i18n.translateApiMessage(response.message, 'admin.saveAssignment'));
          this.refresh();
        },
        error: (error) => {
          this.assigningVideoId.set(null);
          this.system.handleHttpError(error, 'admin.assignmentSaveFailed');
        },
      });
  }

  openVideoDebug(videoId: string): void {
    this.context.selectVideo(videoId);
    this.router.navigate(['/admin/debug', videoId]);
  }

  openTrackDecision(videoId: string, trackId: string): void {
    this.context.selectVideo(videoId);
    this.router.navigate(['/admin/videos', videoId, 'tracks', trackId]);
  }

  hasVideoTimings(video: AdminVideo): boolean {
    const timings = video.stage_timings;
    if (!timings) {
      return false;
    }
    return Object.values(timings).some((value) => value !== null && value !== undefined);
  }

  videoOutcomeSummary(video: AdminVideo): string {
    if (video.status === 'failed') {
      return this.system.i18n.t('admin.finalResultFailed');
    }
    if (video.status !== 'completed') {
      return this.system.i18n.t('admin.finalResultPending');
    }
    if ((video.tracks_matched ?? 0) > 0) {
      return this.system.i18n.t('admin.finalResultMatched', {
        count: video.tracks_matched ?? 0,
        matches: video.matches_count ?? 0,
      });
    }
    return this.system.i18n.t('admin.finalResultUnmatched', {
      count: video.tracks_unmatched ?? 0,
    });
  }

  qualityGuardSummary(video: AdminVideo): string {
    const rejectionCounts = video.quality_guard?.rejection_counts ?? {};
    const parts = Object.entries(rejectionCounts)
      .filter(([, count]) => Number(count) > 0)
      .map(([reason, count]) => `${reason.replace(/_/g, ' ')} ${count}`);
    if (parts.length === 0) {
      return '';
    }
    return this.system.i18n.t('admin.qualityGuardSummary', {
      count: video.tracks_rejected ?? 0,
      reasons: parts.join(', '),
    });
  }

  isUploadedStage(status: VideoStatus): boolean {
    return status === 'uploaded' || status === 'processing' || status === 'completed' || status === 'failed';
  }

  isProcessingStage(status: VideoStatus): boolean {
    return status === 'processing' || status === 'completed' || status === 'failed';
  }

  private clearState(): void {
    this.videos.set([]);
    this.debugVideos.set([]);
    this.assignmentSelections.set({});
  }

  private syncAssignmentSelections(videos: AdminVideo[]): void {
    this.assignmentSelections.set(
      Object.fromEntries(videos.map((video) => [video.video_id, video.assigned_user_id ?? ''])),
    );
  }

  private upsertVideo(video: AdminVideo): void {
    const merge = (existing: AdminVideo[]) => {
      const next = [...existing];
      const index = next.findIndex((item) => item.video_id === video.video_id);
      if (index >= 0) {
        next[index] = { ...next[index], ...video };
      } else {
        next.unshift(video);
      }
      return next;
    };

    this.videos.update(merge);
    this.debugVideos.update(merge);
  }

  private updateVideoStatusLocally(videoId: string, status: VideoStatus): void {
    const update = (items: AdminVideo[]) =>
      items.map((video) =>
        video.video_id === videoId
          ? {
              ...video,
              status,
              updated_at: new Date().toISOString(),
            }
          : video,
      );

    this.videos.update(update);
    this.debugVideos.update(update);
  }

  private syncPollingState(): void {
    const hasPendingVideos = [...this.videos(), ...this.debugVideos()].some(
      (video) => video.status === 'uploaded' || video.status === 'processing',
    );

    if (hasPendingVideos || this.uploadingVideo() || !!this.processingVideoId()) {
      this.startPolling();
      return;
    }

    this.stopPolling();
  }

  private startPolling(): void {
    if (this.pollHandle !== null) {
      this.isPolling.set(true);
      return;
    }

    this.isPolling.set(true);
    this.pollHandle = window.setInterval(() => this.pollVideoState(), 4000);
  }

  private stopPolling(): void {
    if (this.pollHandle !== null) {
      window.clearInterval(this.pollHandle);
      this.pollHandle = null;
    }
    this.isPolling.set(false);
  }

  private pollVideoState(): void {
    if (!this.system.selectedPoolId() || this.system.poolSelectionDirty() || this.pollInFlight) {
      return;
    }

    this.pollInFlight = true;
    forkJoin({
      videos: this.http.get<AdminVideo[]>('/api/admin/videos?include_debug=false', {
        headers: this.system.auth.authHeaders(),
      }),
      debugVideos: this.http.get<AdminVideo[]>('/api/admin/videos?include_debug=true', {
        headers: this.system.auth.authHeaders(),
      }),
      metrics: this.http.get<AdminMetricsResponse>('/api/admin/metrics', {
        headers: this.system.auth.authHeaders(),
      }),
    }).subscribe({
      next: ({ videos, debugVideos, metrics }) => {
        this.videos.set(videos);
        this.debugVideos.set(debugVideos);
        this.system.metrics.set(metrics);
        this.syncAssignmentSelections(videos);
        this.pollInFlight = false;
        this.syncPollingState();
      },
      error: (error) => {
        this.pollInFlight = false;
        this.system.handleHttpError(error, 'admin.loadVideosFailed');
        this.stopPolling();
      },
    });
  }
}
