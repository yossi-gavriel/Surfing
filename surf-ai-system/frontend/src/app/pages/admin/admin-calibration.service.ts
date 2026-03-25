import { DestroyRef, computed, inject, Injectable, signal } from '@angular/core';
import { HttpClient } from '@angular/common/http';

import {
  ConfigHistoryEntry,
  ConfigStatusResponse,
  DEFAULT_SYSTEM_CONFIG,
  FaceComparisonResponse,
  SYSTEM_CONFIG_FIELDS,
  SystemConfigKey,
  SystemConfigValues,
} from './admin.models';
import { AdminDebugCacheService } from './admin-debug-cache.service';
import { AdminSystemService } from './admin-system.service';
import { AdminVideosService } from './admin-videos.service';

@Injectable()
export class AdminCalibrationService {
  private readonly http = inject(HttpClient);
  private readonly destroyRef = inject(DestroyRef);
  private readonly system = inject(AdminSystemService);
  private readonly videos = inject(AdminVideosService);
  private readonly debugCache = inject(AdminDebugCacheService);

  readonly systemConfigFields = SYSTEM_CONFIG_FIELDS;
  readonly loading = signal(false);
  readonly systemConfig = signal<SystemConfigValues>({ ...DEFAULT_SYSTEM_CONFIG });
  readonly systemConfigDraft = signal<SystemConfigValues>({ ...DEFAULT_SYSTEM_CONFIG });
  readonly configHistory = signal<ConfigHistoryEntry[]>([]);
  readonly configStatus = signal<ConfigStatusResponse>({
    cooldown_seconds: 0,
    cooldown_remaining_seconds: 0,
    latest_change: null,
  });
  readonly savingSystemConfig = signal(false);
  readonly comparingFaces = signal(false);
  readonly calibrationFileA = signal<File | null>(null);
  readonly calibrationFileB = signal<File | null>(null);
  readonly calibrationFileNameA = signal('');
  readonly calibrationFileNameB = signal('');
  readonly calibrationPreviewA = signal<string | null>(null);
  readonly calibrationPreviewB = signal<string | null>(null);
  readonly calibrationResult = signal<FaceComparisonResponse | null>(null);
  readonly calibrationThresholds = signal<Pick<SystemConfigValues, 'min_similarity' | 'min_margin'>>({
    min_similarity: DEFAULT_SYSTEM_CONFIG.min_similarity,
    min_margin: DEFAULT_SYSTEM_CONFIG.min_margin,
  });

  readonly systemConfigDirty = computed(() =>
    this.systemConfigFields.some(
      (field) => this.systemConfigDraft()[field.key] !== this.systemConfig()[field.key],
    ),
  );
  readonly calibrationPreviewPassesSimilarity = computed(() => {
    const result = this.calibrationResult();
    if (!result) {
      return false;
    }
    return (result.best_similarity ?? result.similarity) >= this.calibrationThresholds().min_similarity;
  });
  readonly calibrationEstimatedMargin = computed(() => {
    const result = this.calibrationResult();
    if (!result) {
      return null;
    }
    if (result.margin !== undefined) {
      return result.margin ?? null;
    }
    if (result.second_best_similarity === null || result.second_best_similarity === undefined) {
      return null;
    }
    return (result.best_similarity ?? result.similarity) - result.second_best_similarity;
  });
  readonly calibrationPreviewPassesMarginEstimate = computed(() => {
    const result = this.calibrationResult();
    const estimatedMargin = this.calibrationEstimatedMargin();
    if (!result) {
      return false;
    }
    if (result.second_best_similarity === null || result.second_best_similarity === undefined) {
      return true;
    }
    if (estimatedMargin === null) {
      return false;
    }
    return estimatedMargin >= this.calibrationThresholds().min_margin;
  });
  readonly calibrationVerdict = computed<'match' | 'no_match' | null>(() => {
    const result = this.calibrationResult();
    if (!result) {
      return null;
    }
    return this.calibrationPreviewPassesSimilarity() && this.calibrationPreviewPassesMarginEstimate()
      ? 'match'
      : 'no_match';
  });

  constructor() {
    this.destroyRef.onDestroy(() => {
      this.revokePreviewUrl(this.calibrationPreviewA());
      this.revokePreviewUrl(this.calibrationPreviewB());
    });
  }

  refresh(): void {
    this.loading.set(true);
    this.http
      .get<SystemConfigValues>('/api/admin/config', {
        headers: this.system.auth.authHeaders(),
      })
      .subscribe({
        next: (config) => {
          this.applySystemConfig(config);
          this.loadConfigMeta();
        },
        error: (error) => {
          this.loading.set(false);
          this.system.handleHttpError(error, 'admin.loadConfigFailed');
        },
      });
  }

  onCalibrationFileSelected(event: Event, slot: 'A' | 'B'): void {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0] ?? null;
    const objectUrl = file ? URL.createObjectURL(file) : null;

    if (slot === 'A') {
      this.revokePreviewUrl(this.calibrationPreviewA());
      this.calibrationFileA.set(file);
      this.calibrationFileNameA.set(file?.name ?? '');
      this.calibrationPreviewA.set(objectUrl);
    } else {
      this.revokePreviewUrl(this.calibrationPreviewB());
      this.calibrationFileB.set(file);
      this.calibrationFileNameB.set(file?.name ?? '');
      this.calibrationPreviewB.set(objectUrl);
    }

    this.calibrationResult.set(null);
    this.system.clearMessages();
  }

  canCompareFaces(): boolean {
    return !!this.calibrationFileA() && !!this.calibrationFileB();
  }

  compareFaces(): void {
    const fileA = this.calibrationFileA();
    const fileB = this.calibrationFileB();
    if (!fileA || !fileB) {
      this.system.errorMessage.set(this.system.i18n.t('admin.faceCalibrationFilesRequired'));
      return;
    }

    const formData = new FormData();
    formData.append('file1', fileA);
    formData.append('file2', fileB);

    this.comparingFaces.set(true);
    this.system.clearMessages();

    this.http
      .post<FaceComparisonResponse>('/api/admin/compare-faces', formData, {
        headers: this.system.auth.authHeaders(),
      })
      .subscribe({
        next: (response) => {
          this.comparingFaces.set(false);
          this.calibrationResult.set(response);
          this.calibrationThresholds.set({ ...response.thresholds });
          this.system.successMessage.set(this.system.i18n.t('admin.faceComparisonReady'));
        },
        error: (error) => {
          this.comparingFaces.set(false);
          this.system.handleHttpError(error, 'admin.faceComparisonFailed');
        },
      });
  }

  updateCalibrationThreshold(key: 'min_similarity' | 'min_margin', value: unknown): void {
    const numericValue = Number(value);
    if (!Number.isFinite(numericValue)) {
      return;
    }
    this.calibrationThresholds.update((current) => ({
      ...current,
      [key]: numericValue,
    }));
  }

  saveCalibrationAsSystemConfig(): void {
    this.saveConfigValues({
      min_similarity: this.calibrationThresholds().min_similarity,
      min_margin: this.calibrationThresholds().min_margin,
    });
  }

  saveSystemConfig(): void {
    this.saveConfigValues(this.systemConfigDraft());
  }

  updateSystemConfigField(key: SystemConfigKey, value: unknown): void {
    const numericValue = Number(value);
    if (!Number.isFinite(numericValue)) {
      return;
    }
    this.systemConfigDraft.update((current) => ({
      ...current,
      [key]: numericValue,
    }));
  }

  calibrationVerdictLabel(): string {
    const verdict = this.calibrationVerdict();
    if (verdict === 'match') {
      return this.system.i18n.t('admin.match');
    }
    if (verdict === 'no_match') {
      return this.system.i18n.t('admin.noMatch');
    }
    return this.system.i18n.t('common.notAvailable');
  }

  calibrationDecisionExplanation(): string {
    if (!this.calibrationResult()) {
      return this.system.i18n.t('admin.faceCalibrationEmpty');
    }
    if (!this.calibrationPreviewPassesSimilarity()) {
      return 'Match rejected because similarity < threshold';
    }
    if (!this.calibrationPreviewPassesMarginEstimate()) {
      return 'Match rejected because margin too small';
    }
    if (this.calibrationEstimatedMargin() === null) {
      return 'Match accepted. Margin was not needed because there was no competing identity in this calibration check.';
    }
    return 'Match accepted because similarity and margin both passed';
  }

  calibrationGlobalImpactWarning(): string {
    return 'Changes here affect ALL system matching';
  }

  calibrationLiveThresholdsLabel(): string {
    return 'System Thresholds (LIVE)';
  }

  calibrationThresholdPreviewTitle(): string {
    return 'Threshold preview';
  }

  calibrationPreviewHint(): string {
    return 'Slider changes recompute the verdict instantly using the same similarity and estimated margin rules.';
  }

  private loadConfigMeta(): void {
    this.http
      .get<ConfigHistoryEntry[]>('/api/admin/config/history?limit=10', {
        headers: this.system.auth.authHeaders(),
      })
      .subscribe({
        next: (history) => {
          this.configHistory.set(history);
          this.http
            .get<ConfigStatusResponse>('/api/admin/config/status', {
              headers: this.system.auth.authHeaders(),
            })
            .subscribe({
              next: (status) => {
                this.configStatus.set(status);
                this.loading.set(false);
              },
              error: (error) => {
                this.loading.set(false);
                this.system.handleHttpError(error, 'admin.loadConfigFailed');
              },
            });
        },
        error: (error) => {
          this.loading.set(false);
          this.system.handleHttpError(error, 'admin.loadConfigFailed');
        },
      });
  }

  private applySystemConfig(config: SystemConfigValues): void {
    this.systemConfig.set({ ...config });
    this.systemConfigDraft.set({ ...config });
    this.calibrationThresholds.set({
      min_similarity: config.min_similarity,
      min_margin: config.min_margin,
    });
  }

  private validateConfigValues(values: Partial<SystemConfigValues>): string | null {
    for (const field of this.systemConfigFields) {
      if (!(field.key in values)) {
        continue;
      }

      const rawValue = values[field.key];
      const numericValue = Number(rawValue);
      if (!Number.isFinite(numericValue)) {
        return `${field.label} must be numeric`;
      }
      if (numericValue < field.min || numericValue > field.max) {
        return `${field.label} must be between ${field.min} and ${field.max}`;
      }
      if (field.type === 'int' && !Number.isInteger(numericValue)) {
        return `${field.label} must be a whole number`;
      }
    }

    return null;
  }

  private saveConfigValues(values: Partial<SystemConfigValues>): void {
    const validationError = this.validateConfigValues(values);
    if (validationError) {
      this.system.errorMessage.set(validationError);
      return;
    }

    this.savingSystemConfig.set(true);
    this.system.clearMessages();

    this.http
      .put<SystemConfigValues>('/api/admin/config', values, {
        headers: this.system.auth.authHeaders(),
      })
      .subscribe({
        next: (config) => {
          this.savingSystemConfig.set(false);
          this.debugCache.invalidateAll();
          this.system.successMessage.set(this.system.i18n.t('admin.systemConfigSaved'));
          this.applySystemConfig(config);
          this.refresh();
          this.system.refreshContextData();
          this.videos.refresh();
        },
        error: (error) => {
          this.savingSystemConfig.set(false);
          this.system.handleHttpError(error, 'admin.saveSystemConfigFailed');
        },
      });
  }

  private revokePreviewUrl(url: string | null): void {
    if (url) {
      URL.revokeObjectURL(url);
    }
  }
}
