import { CommonModule } from '@angular/common';
import { Component, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { AdminCalibrationService } from './admin-calibration.service';
import { AdminSystemService } from './admin-system.service';

@Component({
  selector: 'app-admin-calibration-page',
  standalone: true,
  imports: [CommonModule, FormsModule],
  host: { class: 'admin-page' },
  template: `
    <section class="hero">
      <div>
        <p class="eyebrow">Control</p>
        <h2>Calibration and system controls</h2>
        <p class="subcopy">Face calibration stays interactive while system configuration remains editable on the same route.</p>
      </div>
    </section>

    <section class="feedback error" *ngIf="admin.errorMessage()">{{ admin.errorMessage() }}</section>
    <section class="feedback success" *ngIf="admin.successMessage()">{{ admin.successMessage() }}</section>

    <section class="panel">
      <div class="section-header">
        <div>
          <p class="panel-label">{{ admin.i18n.t('admin.faceCalibrationLabel') }}</p>
          <h3>{{ admin.i18n.t('admin.faceCalibrationTitle') }}</h3>
          <p class="subcopy compact-copy">{{ admin.i18n.t('admin.faceCalibrationSubtitle') }}</p>
        </div>
        <button type="button" class="secondary" (click)="admin.saveCalibrationAsSystemConfig()" [disabled]="admin.savingSystemConfig()">
          {{ admin.savingSystemConfig() ? admin.i18n.t('common.saving') : admin.i18n.t('admin.saveAsSystemConfig') }}
        </button>
      </div>

      <div class="calibration-upload-grid">
        <label class="dropzone">
          <input type="file" accept="image/*" (change)="admin.onCalibrationFileSelected($event, 'A')" />
          <span>{{ admin.calibrationFileNameA() || admin.i18n.t('admin.uploadImageA') }}</span>
          <small>{{ admin.i18n.t('admin.faceCalibrationHint') }}</small>
        </label>

        <label class="dropzone">
          <input type="file" accept="image/*" (change)="admin.onCalibrationFileSelected($event, 'B')" />
          <span>{{ admin.calibrationFileNameB() || admin.i18n.t('admin.uploadImageB') }}</span>
          <small>{{ admin.i18n.t('admin.faceCalibrationHint') }}</small>
        </label>
      </div>

      <div class="actions calibration-actions">
        <button type="button" (click)="admin.compareFaces()" [disabled]="!admin.canCompareFaces() || admin.comparingFaces()">
          {{ admin.comparingFaces() ? admin.i18n.t('common.working') : admin.i18n.t('admin.compareFaces') }}
        </button>
      </div>

      <div class="feedback warning calibration-warning">{{ admin.calibrationGlobalImpactWarning() }}</div>

      <div class="calibration-grid">
        <article class="config-card">
          <div class="section-header compact">
            <div>
              <p class="panel-label">{{ admin.calibrationLiveThresholdsLabel() }}</p>
              <h3>{{ admin.calibrationThresholdPreviewTitle() }}</h3>
            </div>
          </div>

          <div class="metrics live-thresholds-copy">
            <span>{{ admin.i18n.t('admin.minSimilarity') }} {{ admin.formatMetric(admin.systemConfig().min_similarity, 2) }}</span>
            <span>{{ admin.i18n.t('admin.minMargin') }} {{ admin.formatMetric(admin.systemConfig().min_margin, 2) }}</span>
          </div>

          <label>
            <span>{{ admin.i18n.t('admin.minSimilarity') }}: {{ admin.formatMetric(admin.calibrationThresholds().min_similarity, 2) }}</span>
            <input type="range" min="0.5" max="0.95" step="0.01" [ngModel]="admin.calibrationThresholds().min_similarity" (ngModelChange)="admin.updateCalibrationThreshold('min_similarity', $event)" />
          </label>

          <label>
            <span>{{ admin.i18n.t('admin.minMargin') }}: {{ admin.formatMetric(admin.calibrationThresholds().min_margin, 2) }}</span>
            <input type="range" min="0.01" max="0.2" step="0.01" [ngModel]="admin.calibrationThresholds().min_margin" (ngModelChange)="admin.updateCalibrationThreshold('min_margin', $event)" />
          </label>

          <small class="hint">{{ admin.calibrationPreviewHint() }}</small>
        </article>

        <article class="config-card" *ngIf="admin.calibrationResult() as result; else calibrationEmpty">
          <div class="section-header compact">
            <div>
              <p class="panel-label">{{ admin.i18n.t('admin.faceCalibrationResult') }}</p>
              <h3>{{ admin.i18n.t('admin.faceCalibrationVerdict') }}</h3>
            </div>
            <span class="status verdict" [class.completed]="admin.calibrationVerdict() === 'match'" [class.failed]="admin.calibrationVerdict() === 'no_match'">
              {{ admin.calibrationVerdictLabel() }}
            </span>
          </div>

          <div class="calibration-metrics">
            <article class="summary-card">
              <span>{{ admin.i18n.t('admin.similarity') }}</span>
              <strong>{{ admin.formatMetric(result.similarity, 3) }}</strong>
            </article>
            <article class="summary-card">
              <span>{{ admin.i18n.t('admin.distance') }}</span>
              <strong>{{ admin.formatMetric(result.distance, 3) }}</strong>
            </article>
            <article class="summary-card">
              <span>Margin</span>
              <strong>{{ admin.formatMetric(admin.calibrationEstimatedMargin(), 3) }}</strong>
            </article>
          </div>

          <div class="metrics calibration-decision-copy">
            <span>With these thresholds -> {{ admin.calibrationVerdictLabel() }}</span>
            <span>Threshold {{ admin.formatMetric(admin.calibrationThresholds().min_similarity, 2) }}</span>
            <span>Margin threshold {{ admin.formatMetric(admin.calibrationThresholds().min_margin, 2) }}</span>
            <span>Similarity {{ admin.calibrationPreviewPassesSimilarity() ? 'passes' : 'fails' }}</span>
            <span>Margin {{ admin.calibrationPreviewPassesMarginEstimate() ? 'passes' : 'fails' }}</span>
          </div>
          <small class="hint">{{ admin.calibrationDecisionExplanation() }}</small>
          <small class="hint warning-copy" *ngIf="result.warning">{{ result.warning }}</small>

          <div class="image-compare-grid">
            <article class="image-card">
              <img *ngIf="admin.calibrationPreviewA(); else missingCalibrationA" [src]="admin.calibrationPreviewA() || ''" alt="Image A preview" />
              <ng-template #missingCalibrationA>
                <div class="user-placeholder">{{ admin.i18n.t('common.imageUnavailable') }}</div>
              </ng-template>
              <small>{{ admin.i18n.t('admin.imageA') }}</small>
            </article>

            <article class="image-card">
              <img *ngIf="admin.calibrationPreviewB(); else missingCalibrationB" [src]="admin.calibrationPreviewB() || ''" alt="Image B preview" />
              <ng-template #missingCalibrationB>
                <div class="user-placeholder">{{ admin.i18n.t('common.imageUnavailable') }}</div>
              </ng-template>
              <small>{{ admin.i18n.t('admin.imageB') }}</small>
            </article>
          </div>
        </article>

        <ng-template #calibrationEmpty>
          <article class="config-card empty-state-card">
            <p class="empty">{{ admin.i18n.t('admin.faceCalibrationEmpty') }}</p>
          </article>
        </ng-template>
      </div>
    </section>

    <section class="panel">
      <div class="section-header">
        <div>
          <p class="panel-label">{{ admin.i18n.t('admin.systemConfigLabel') }}</p>
          <h3>{{ admin.i18n.t('admin.systemConfigTitle') }}</h3>
          <p class="subcopy compact-copy">{{ admin.i18n.t('admin.systemConfigSubtitle') }}</p>
        </div>
        <button type="button" (click)="admin.saveSystemConfig()" [disabled]="admin.savingSystemConfig() || !admin.systemConfigDirty()">
          {{ admin.savingSystemConfig() ? admin.i18n.t('common.saving') : admin.i18n.t('admin.saveSystemConfig') }}
        </button>
      </div>

      <div class="config-grid">
        <article class="config-card">
          <div class="section-header compact">
            <div>
              <p class="panel-label">Change audit</p>
              <h3>Last config change</h3>
            </div>
            <span class="pill" *ngIf="admin.configStatus().cooldown_remaining_seconds > 0">
              Cooldown {{ admin.configStatus().cooldown_remaining_seconds }}s
            </span>
          </div>

          <div *ngIf="admin.configStatus().latest_change as latest; else noConfigHistory" class="metrics">
            <span>{{ latest.key }}</span>
            <span>{{ latest.old_value }} -> {{ latest.new_value }}</span>
            <span>{{ latest.updated_by }}</span>
            <span>{{ admin.formatTimestamp(latest.changed_at) }}</span>
          </div>

          <ng-template #noConfigHistory>
            <p class="empty">No config changes yet.</p>
          </ng-template>
        </article>

        <article class="config-card" *ngFor="let field of admin.systemConfigFields">
          <div class="section-header compact">
            <div>
              <p class="panel-label">{{ admin.i18n.t('admin.systemConfigLabel') }}</p>
              <h3>{{ field.label }}</h3>
            </div>
            <span class="pill">
              {{ field.type === 'int' ? admin.formatInteger(admin.systemConfigDraft()[field.key]) : admin.formatMetric(admin.systemConfigDraft()[field.key], 2) }}
            </span>
          </div>

          <label>
            <span>{{ field.label }}</span>
            <input type="number" [attr.min]="field.min" [attr.max]="field.max" [attr.step]="field.step" [ngModel]="admin.systemConfigDraft()[field.key]" (ngModelChange)="admin.updateSystemConfigField(field.key, $event)" />
          </label>

          <label *ngIf="field.type === 'float'">
            <span>{{ admin.i18n.t('admin.livePreview') }}</span>
            <input type="range" [attr.min]="field.min" [attr.max]="field.max" [attr.step]="field.step" [ngModel]="admin.systemConfigDraft()[field.key]" (ngModelChange)="admin.updateSystemConfigField(field.key, $event)" />
          </label>

          <small class="hint">
            {{ admin.i18n.t('admin.allowedRange', { min: field.min, max: field.max }) }}
          </small>
        </article>
      </div>
    </section>
  `,
})
export class AdminCalibrationComponent {
  private readonly calibration = inject(AdminCalibrationService);
  private readonly system = inject(AdminSystemService);

  constructor() {
    this.calibration.refresh();
  }

  protected readonly admin = {
    i18n: this.system.i18n,
    errorMessage: this.system.errorMessage,
    successMessage: this.system.successMessage,
    savingSystemConfig: this.calibration.savingSystemConfig,
    saveCalibrationAsSystemConfig: () => this.calibration.saveCalibrationAsSystemConfig(),
    onCalibrationFileSelected: (event: Event, slot: 'A' | 'B') => this.calibration.onCalibrationFileSelected(event, slot),
    calibrationFileNameA: this.calibration.calibrationFileNameA,
    calibrationFileNameB: this.calibration.calibrationFileNameB,
    compareFaces: () => this.calibration.compareFaces(),
    canCompareFaces: () => this.calibration.canCompareFaces(),
    comparingFaces: this.calibration.comparingFaces,
    calibrationGlobalImpactWarning: () => this.calibration.calibrationGlobalImpactWarning(),
    calibrationLiveThresholdsLabel: () => this.calibration.calibrationLiveThresholdsLabel(),
    calibrationThresholdPreviewTitle: () => this.calibration.calibrationThresholdPreviewTitle(),
    systemConfig: this.calibration.systemConfig,
    calibrationThresholds: this.calibration.calibrationThresholds,
    formatMetric: (value: number | null | undefined, digits?: number) => this.system.formatMetric(value, digits),
    updateCalibrationThreshold: (key: 'min_similarity' | 'min_margin', value: unknown) =>
      this.calibration.updateCalibrationThreshold(key, value),
    calibrationPreviewHint: () => this.calibration.calibrationPreviewHint(),
    calibrationResult: this.calibration.calibrationResult,
    calibrationVerdict: this.calibration.calibrationVerdict,
    calibrationVerdictLabel: () => this.calibration.calibrationVerdictLabel(),
    calibrationEstimatedMargin: this.calibration.calibrationEstimatedMargin,
    calibrationPreviewPassesSimilarity: this.calibration.calibrationPreviewPassesSimilarity,
    calibrationPreviewPassesMarginEstimate: this.calibration.calibrationPreviewPassesMarginEstimate,
    calibrationDecisionExplanation: () => this.calibration.calibrationDecisionExplanation(),
    calibrationPreviewA: this.calibration.calibrationPreviewA,
    calibrationPreviewB: this.calibration.calibrationPreviewB,
    saveSystemConfig: () => this.calibration.saveSystemConfig(),
    systemConfigDirty: this.calibration.systemConfigDirty,
    configStatus: this.calibration.configStatus,
    formatTimestamp: (value: string) => this.system.formatTimestamp(value),
    systemConfigFields: this.calibration.systemConfigFields,
    systemConfigDraft: this.calibration.systemConfigDraft,
    formatInteger: (value: number | null | undefined) => this.system.formatInteger(value),
    updateSystemConfigField: (key: Parameters<AdminCalibrationService['updateSystemConfigField']>[0], value: unknown) =>
      this.calibration.updateSystemConfigField(key, value),
  };
}
