import { computed, inject, Injectable, signal } from '@angular/core';

import { DebugCompareResponse, DebugVideoFrame } from './admin-debug.models';
import { AdminContextService } from './admin-context.service';
import { AdminDebugCacheService } from './admin-debug-cache.service';
import { TrackSummaryCandidate } from './admin-results.models';

@Injectable()
export class AdminResultsService {
  private readonly context = inject(AdminContextService);
  private readonly debugCache = inject(AdminDebugCacheService);

  readonly expandedVideoId = signal<string | null>(null);
  readonly selectedTrackId = signal<string | null>(null);
  readonly loadingTrackSummaries = computed(() => this.debugCache.loading());
  readonly trackSummaryErrors = computed(() => this.debugCache.errors());

  readonly visibleTrackCount = computed(() => {
    const expandedVideoId = this.expandedVideoId();
    if (!expandedVideoId) {
      return 0;
    }
    return this.trackSummaries(expandedVideoId).length;
  });

  toggleVideo(videoId: string): void {
    const nextVideoId = this.expandedVideoId() === videoId ? null : videoId;
    this.expandedVideoId.set(nextVideoId);
    this.context.selectVideo(nextVideoId);
    if (!nextVideoId) {
      this.selectedTrackId.set(null);
    }
    if (nextVideoId) {
      this.debugCache.ensure(nextVideoId);
    }
  }

  isExpanded(videoId: string): boolean {
    return this.expandedVideoId() === videoId;
  }

  trackSummaries(videoId: string): TrackSummaryCandidate[] {
    const response = this.debugCache.response(videoId);
    if (!response) {
      return [];
    }

    const { query, trackStatus } = this.context.filters();
    const normalizedQuery = query.trim().toLowerCase();
    return this.buildTrackCandidates(response).filter((track) => {
      const statusMatches = trackStatus === 'all' || track.status === trackStatus;
      const queryMatches =
        !normalizedQuery ||
        track.trackId.toLowerCase().includes(normalizedQuery) ||
        (track.userEmail ?? '').toLowerCase().includes(normalizedQuery);
      return statusMatches && queryMatches;
    });
  }

  ensureVideoExpanded(videoId: string | null, availableVideoIds: string[]): void {
    if (!videoId) {
      this.expandedVideoId.set(null);
      this.selectedTrackId.set(null);
      return;
    }
    if (!availableVideoIds.includes(videoId)) {
      this.context.selectVideo(null);
      this.expandedVideoId.set(null);
      this.selectedTrackId.set(null);
      return;
    }
    this.expandedVideoId.set(videoId);
    this.debugCache.ensure(videoId);
  }

  selectTrack(videoId: string, trackId: string | null): void {
    this.expandedVideoId.set(videoId);
    this.selectedTrackId.set(trackId);
    this.context.selectVideo(videoId);
  }

  isTrackSelected(trackId: string): boolean {
    return this.selectedTrackId() === trackId;
  }

  private buildTrackCandidates(response: DebugCompareResponse): TrackSummaryCandidate[] {
    const trackMap = new Map<string, DebugVideoFrame>();
    for (const track of response.track_summaries ?? []) {
      trackMap.set(track.track_id, track);
    }
    for (const frame of response.video_frames) {
      if (!trackMap.has(frame.track_id)) {
        trackMap.set(frame.track_id, frame);
      }
    }

    return [...trackMap.values()]
      .map((track) => ({
        videoId: response.video_id,
        trackId: track.track_id,
        status: track.decision ?? track.final_verdict ?? 'pending',
        similarity: track.similarity ?? null,
        margin: track.margin ?? track.similarity_margin ?? null,
        userEmail: track.best_user_email ?? track.user_email ?? null,
        threshold: track.threshold_used ?? response.threshold ?? null,
      }))
      .sort((left, right) => (right.similarity ?? -1) - (left.similarity ?? -1));
  }
}
