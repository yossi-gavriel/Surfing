import { computed, inject, Injectable, signal } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Router } from '@angular/router';
import { forkJoin } from 'rxjs';

import { AuthService, MeResponse } from '../../core/auth.service';
import { I18nService } from '../../core/i18n.service';
import {
  AdminMetricsResponse,
  AdminUser,
  CameraRecord,
  EMPTY_ADMIN_METRICS,
  PipelineStageState,
  PoolRecord,
  VideoStatus,
} from './admin.models';

@Injectable()
export class AdminSystemService {
  readonly http = inject(HttpClient);
  readonly auth = inject(AuthService);
  readonly router = inject(Router);
  readonly i18n = inject(I18nService);

  readonly pools = signal<PoolRecord[]>([]);
  readonly cameras = signal<CameraRecord[]>([]);
  readonly users = signal<AdminUser[]>([]);
  readonly metrics = signal<AdminMetricsResponse>({ ...EMPTY_ADMIN_METRICS });
  readonly loading = signal(false);
  readonly savingCamera = signal(false);
  readonly savingPool = signal(false);
  readonly creatingPool = signal(false);
  readonly successMessage = signal('');
  readonly errorMessage = signal('');
  readonly contextReady = signal(false);
  readonly selectedPoolId = signal(this.auth.selectedPoolId() ?? this.auth.poolId() ?? '');

  readonly activePoolName = computed(
    () => this.pools().find((pool) => pool.pool_id === this.selectedPoolId())?.name ?? '',
  );
  readonly poolSelectionDirty = computed(
    () => !!this.selectedPoolId() && this.selectedPoolId() !== (this.auth.poolId() ?? ''),
  );
  readonly totalMatchesCount = computed(() =>
    this.metrics().videos.matches_per_video.reduce((sum, item) => sum + (item.matches_count ?? 0), 0),
  );

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
    this.contextReady.set(false);
    this.clearMessages();
    this.loadMe();
  }

  refreshContextData(): void {
    this.loading.set(true);
    this.clearMessages();
    this.loadContextData();
  }

  onPoolSelectionChange(poolId: string): void {
    const nextPoolId = poolId || '';
    this.selectedPoolId.set(nextPoolId);
    this.auth.setSelectedPoolId(nextPoolId || null);
    this.clearMessages();

    if (!nextPoolId || nextPoolId !== (this.auth.poolId() ?? '')) {
      this.cameras.set([]);
      this.users.set([]);
      this.metrics.set({ ...EMPTY_ADMIN_METRICS });
      return;
    }

    this.refreshContextData();
  }

  saveActivePool(afterSave?: () => void): void {
    const poolId = this.selectedPoolId();
    if (!poolId) {
      return;
    }
    if (poolId === (this.auth.poolId() ?? '')) {
      afterSave?.();
      return;
    }

    this.savingPool.set(true);
    this.clearMessages();

    this.http
      .put<MeResponse>('/api/me/pool', { pool_id: poolId }, {
        headers: this.auth.authHeaders(),
      })
      .subscribe({
        next: (response) => {
          this.auth.setProfile(response);
          this.selectedPoolId.set(response.pool_id ?? poolId);
          this.savingPool.set(false);
          this.successMessage.set(this.i18n.t('admin.activePoolUpdated'));
          this.refreshContextData();
          afterSave?.();
        },
        error: (error) => {
          this.savingPool.set(false);
          this.handleHttpError(error, 'admin.updatePoolFailed');
        },
      });
  }

  createPool(): void {
    if (!this.newPoolName.trim()) {
      return;
    }

    this.creatingPool.set(true);
    this.clearMessages();

    this.http
      .post<PoolRecord>('/api/admin/pools', { name: this.newPoolName.trim() }, {
        headers: this.auth.authHeaders(),
      })
      .subscribe({
        next: (pool) => {
          this.creatingPool.set(false);
          this.newPoolName = '';
          this.selectedPoolId.set(pool.pool_id);
          this.auth.setSelectedPoolId(pool.pool_id);
          this.successMessage.set(this.i18n.t('admin.poolCreated', { name: pool.name }));
          this.loadPools(() => this.saveActivePool());
        },
        error: (error) => {
          this.creatingPool.set(false);
          this.handleHttpError(error, 'admin.createPoolFailed');
        },
      });
  }

  saveCamera(): void {
    if (!this.cameraForm.name.trim() || !this.cameraForm.url.trim()) {
      this.errorMessage.set(this.i18n.t('admin.cameraFieldsRequired'));
      return;
    }

    this.ensureActivePoolSynced(() => {
      this.savingCamera.set(true);
      this.clearMessages();

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
            this.successMessage.set(this.i18n.t('admin.cameraSaved', { name: camera.name }));
            this.refreshContextData();
          },
          error: (error) => {
            this.savingCamera.set(false);
            this.handleHttpError(error, 'admin.cameraSaveFailed');
          },
        });
    });
  }

  ensureActivePoolSynced(nextAction: () => void): void {
    const poolId = this.selectedPoolId();
    if (!poolId) {
      this.errorMessage.set(this.i18n.t('admin.selectPoolToContinue'));
      return;
    }

    if (poolId === (this.auth.poolId() ?? '')) {
      nextAction();
      return;
    }

    this.saveActivePool(nextAction);
  }

  clearMessages(): void {
    this.successMessage.set('');
    this.errorMessage.set('');
  }

  handleHttpError(error: any, fallbackKey: Parameters<I18nService['t']>[0]): void {
    this.loading.set(false);
    this.errorMessage.set(this.i18n.translateApiMessage(error?.error?.detail, fallbackKey));
    if (error?.status === 401) {
      this.auth.clearSession();
      this.router.navigate(['/login']);
    }
  }

  formatTimestamp(value: string): string {
    return this.i18n.formatDateTime(value);
  }

  formatMetric(value: number | null | undefined, digits = 3): string {
    if (value === null || value === undefined || !Number.isFinite(value)) {
      return this.i18n.t('common.notAvailable');
    }
    return value.toFixed(digits);
  }

  formatInteger(value: number | null | undefined): string {
    if (value === null || value === undefined || !Number.isFinite(value)) {
      return this.i18n.t('common.notAvailable');
    }
    return String(Math.round(value));
  }

  formatPercent(value: number | null | undefined): string {
    if (value === null || value === undefined || !Number.isFinite(value)) {
      return this.i18n.t('common.notAvailable');
    }
    return `${Number(value).toFixed(1)}%`;
  }

  formatSeconds(value: number | null | undefined): string {
    if (value === null || value === undefined || !Number.isFinite(value)) {
      return this.i18n.t('common.notAvailable');
    }
    return `${Number(value).toFixed(2)}s`;
  }

  roleLabel(role: AdminUser['role']): string {
    return this.i18n.t(role === 'admin' ? 'common.role.admin' : 'common.role.user');
  }

  videoStatusLabel(status: VideoStatus): string {
    return this.i18n.t(`admin.status.${status}` as Parameters<I18nService['t']>[0]);
  }

  pipelineStageClass(status: PipelineStageState | undefined): string {
    return status ?? 'pending';
  }

  pipelineStageStatusLabel(status: PipelineStageState | undefined): string {
    return this.i18n.t(`admin.stageStatus.${status ?? 'pending'}` as Parameters<I18nService['t']>[0]);
  }

  private loadMe(): void {
    this.http
      .get<MeResponse>('/api/me', {
        headers: this.auth.authHeaders(),
      })
      .subscribe({
        next: (me) => {
          this.auth.setProfile(me);
          if (me.pool_id) {
            this.selectedPoolId.set(me.pool_id);
          }
          this.loadPools(() => this.loadContextData());
        },
        error: (error) => this.handleHttpError(error, 'admin.profileLoadFailed'),
      });
  }

  private loadPools(next?: () => void): void {
    this.http
      .get<PoolRecord[]>('/api/admin/pools', {
        headers: this.auth.authHeaders(),
      })
      .subscribe({
        next: (pools) => {
          this.pools.set(pools);
          const selectedPoolId = this.selectedPoolId();
          if (selectedPoolId && !pools.some((pool) => pool.pool_id === selectedPoolId)) {
            this.selectedPoolId.set('');
            this.auth.setSelectedPoolId(null);
          }
          if (!this.selectedPoolId()) {
            this.selectedPoolId.set(this.auth.selectedPoolId() ?? this.auth.poolId() ?? '');
          }
          next?.();
        },
        error: (error) => this.handleHttpError(error, 'admin.loadPoolsFailed'),
      });
  }

  private loadContextData(): void {
    if (!this.selectedPoolId()) {
      this.cameras.set([]);
      this.users.set([]);
      this.metrics.set({ ...EMPTY_ADMIN_METRICS });
      this.loading.set(false);
      this.contextReady.set(true);
      return;
    }

    forkJoin({
      cameras: this.http.get<CameraRecord[]>('/api/admin/cameras', {
        headers: this.auth.authHeaders(),
      }),
      users: this.http.get<AdminUser[]>('/api/admin/users', {
        headers: this.auth.authHeaders(),
      }),
      metrics: this.http.get<AdminMetricsResponse>('/api/admin/metrics', {
        headers: this.auth.authHeaders(),
      }),
    }).subscribe({
      next: ({ cameras, users, metrics }) => {
        this.cameras.set(cameras);
        this.users.set(users);
        this.metrics.set(metrics);
        this.loading.set(false);
        this.contextReady.set(true);
      },
      error: (error) => this.handleHttpError(error, 'admin.loadVideosFailed'),
    });
  }
}
