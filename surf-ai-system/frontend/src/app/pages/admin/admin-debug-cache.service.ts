import { effect, inject, Injectable, signal } from '@angular/core';
import { HttpClient } from '@angular/common/http';

import { DebugCompareResponse } from './admin-debug.models';
import { AdminContextService } from './admin-context.service';
import { AdminSystemService } from './admin-system.service';

@Injectable()
export class AdminDebugCacheService {
  private readonly http = inject(HttpClient);
  private readonly system = inject(AdminSystemService);
  private readonly context = inject(AdminContextService);

  readonly responses = signal<Record<string, DebugCompareResponse>>({});
  readonly loading = signal<Record<string, boolean>>({});
  readonly errors = signal<Record<string, string>>({});

  private lastScope = '';

  constructor() {
    effect(() => {
      const scope = `${this.system.auth.poolId() ?? ''}|${this.system.selectedPoolId()}|${this.system.poolSelectionDirty()}`;
      if (this.lastScope && this.lastScope !== scope) {
        this.clear();
        this.context.clearForPoolChange();
      }
      this.lastScope = scope;
    });
  }

  response(videoId: string): DebugCompareResponse | null {
    return this.responses()[videoId] ?? null;
  }

  isLoading(videoId: string): boolean {
    return !!this.loading()[videoId];
  }

  error(videoId: string): string {
    return this.errors()[videoId] ?? '';
  }

  invalidate(videoId: string): void {
    if (!videoId) {
      return;
    }
    this.responses.update((current) => this.omitKey(current, videoId));
    this.loading.update((current) => this.omitKey(current, videoId));
    this.errors.update((current) => this.omitKey(current, videoId));
  }

  invalidateAll(): void {
    this.clear();
  }

  ensure(videoId: string, force = false): void {
    if (!videoId) {
      return;
    }
    if (!force && (this.response(videoId) || this.isLoading(videoId))) {
      return;
    }

    this.loading.update((current) => ({ ...current, [videoId]: true }));
    this.errors.update((current) => ({ ...current, [videoId]: '' }));
    this.http
      .get<DebugCompareResponse>(`/api/admin/debug/compare/${videoId}`, {
        headers: this.system.auth.authHeaders(),
      })
      .subscribe({
        next: (response) => {
          this.responses.update((current) => ({ ...current, [videoId]: response }));
          this.loading.update((current) => ({ ...current, [videoId]: false }));
        },
        error: (error) => {
          const message = this.system.i18n.translateApiMessage(error?.error?.detail, 'admin.loadVideosFailed');
          this.errors.update((current) => ({ ...current, [videoId]: message }));
          this.loading.update((current) => ({ ...current, [videoId]: false }));
          if (error?.status === 401) {
            this.system.auth.clearSession();
            this.system.router.navigate(['/login']);
          }
        },
      });
  }

  clear(): void {
    this.responses.set({});
    this.loading.set({});
    this.errors.set({});
  }

  private omitKey<T>(record: Record<string, T>, key: string): Record<string, T> {
    const next = { ...record };
    delete next[key];
    return next;
  }
}
