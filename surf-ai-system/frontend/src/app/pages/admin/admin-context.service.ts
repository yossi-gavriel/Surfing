import { Injectable, signal } from '@angular/core';

import { VideoStatus } from './admin.models';

export interface AdminResultsFilters {
  query: string;
  videoStatus: VideoStatus | 'all';
  trackStatus: 'all' | 'match' | 'no_match' | 'pending';
}

@Injectable()
export class AdminContextService {
  readonly selectedVideoId = signal<string | null>(null);
  readonly filters = signal<AdminResultsFilters>({
    query: '',
    videoStatus: 'all',
    trackStatus: 'all',
  });

  selectVideo(videoId: string | null): void {
    this.selectedVideoId.set(videoId);
  }

  updateFilters(patch: Partial<AdminResultsFilters>): void {
    this.filters.update((current) => ({ ...current, ...patch }));
  }

  clearForPoolChange(): void {
    this.selectVideo(null);
    this.filters.set({
      query: '',
      videoStatus: 'all',
      trackStatus: 'all',
    });
  }
}
