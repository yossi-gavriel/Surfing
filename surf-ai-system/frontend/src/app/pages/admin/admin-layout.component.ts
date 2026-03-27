import { CommonModule } from '@angular/common';
import { Component, inject } from '@angular/core';
import { Router, RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';

import { AdminCalibrationService } from './admin-calibration.service';
import { AdminContextService } from './admin-context.service';
import { AdminDebugCacheService } from './admin-debug-cache.service';
import { AdminEc2ControlService } from './admin-ec2-control.service';
import { AdminResultsService } from './admin-results.service';
import { AdminSystemService } from './admin-system.service';
import { AdminVideosService } from './admin-videos.service';

@Component({
  selector: 'app-admin-layout',
  standalone: true,
  imports: [CommonModule, RouterLink, RouterLinkActive, RouterOutlet],
  providers: [
    AdminContextService,
    AdminSystemService,
    AdminVideosService,
    AdminDebugCacheService,
    AdminCalibrationService,
    AdminResultsService,
    AdminEc2ControlService,
  ],
  template: `
    <div class="admin-shell">
      <aside class="admin-sidebar">
        <a routerLink="/admin/dashboard" class="brand">Surf AI Admin</a>

        <nav class="admin-nav">
          <a routerLink="/admin/dashboard" routerLinkActive="active">Dashboard</a>
          <a routerLink="/admin/results" routerLinkActive="active">Results</a>
          <a routerLink="/admin/calibration" routerLinkActive="active">Calibration</a>
        </nav>

        <div class="sidebar-meta">
          <span class="meta-label">Active Pool</span>
          <strong>{{ system.activePoolName() || 'Not selected' }}</strong>
          <span class="meta-copy" *ngIf="system.poolSelectionDirty()">Pool selection has unsaved changes.</span>
        </div>

        <!-- ── EC2 System Control ── -->
        <div class="ec2-control" *ngIf="ec2.apiBase">
          <div class="ec2-status-row">
            <span class="ec2-dot" [ngClass]="ec2.ec2State()"></span>
            <span class="ec2-label">{{ ec2.ec2Label() }}</span>
            <button class="ec2-refresh" type="button" (click)="ec2.loadStatus()" title="Refresh status">↻</button>
          </div>

          <p class="ec2-msg" *ngIf="ec2.actionMessage()">{{ ec2.actionMessage() }}</p>
          <p class="ec2-err" *ngIf="ec2.statusError()">{{ ec2.statusError() }}</p>

          <div class="ec2-buttons">
            <button
              type="button"
              class="ec2-btn start"
              (click)="ec2.startSystem()"
              [disabled]="ec2.starting() || ec2.ec2State() === 'running' || ec2.ec2State() === 'pending'"
            >
              {{ ec2.starting() ? 'Starting…' : '▶ Start System' }}
            </button>

            <button
              type="button"
              class="ec2-btn stop"
              (click)="confirmStop()"
              [disabled]="ec2.stopping() || ec2.ec2State() === 'stopped' || ec2.ec2State() === 'stopping'"
            >
              {{ ec2.stopping() ? 'Stopping…' : '■ Stop System' }}
            </button>
          </div>
        </div>

        <button type="button" class="ghost-button" (click)="logout()">Log out</button>
      </aside>

      <div class="admin-main">
        <header class="admin-topbar">
          <div>
            <span class="topbar-label">Scalable Admin</span>
            <strong>{{ system.activePoolName() || 'Choose a pool to begin' }}</strong>
          </div>

          <div class="topbar-actions">
            <span class="live-pill" *ngIf="videos.isPolling()">Live</span>
            <button type="button" (click)="refresh()" [disabled]="isRefreshing()">
              {{ isRefreshing() ? 'Refreshing...' : 'Refresh' }}
            </button>
          </div>
        </header>

        <main class="admin-content">
          <router-outlet></router-outlet>
        </main>
      </div>
    </div>
  `,
  styles: [`
    :host {
      display: block;
      min-height: 100vh;
    }

    .admin-shell {
      min-height: 100vh;
      display: grid;
      grid-template-columns: 260px minmax(0, 1fr);
    }

    .admin-sidebar {
      display: grid;
      grid-template-rows: auto auto 1fr auto;
      gap: 1.25rem;
      padding: 1.5rem;
      background: rgba(255, 252, 245, 0.86);
      border-right: 1px solid rgba(20, 60, 68, 0.08);
      backdrop-filter: blur(12px);
    }

    .brand {
      color: var(--ink-strong);
      text-decoration: none;
      font-family: 'Space Grotesk', sans-serif;
      font-size: 1.3rem;
      font-weight: 700;
    }

    .admin-nav {
      display: grid;
      gap: 0.5rem;
    }

    .admin-nav a,
    .ghost-button,
    .topbar-actions button {
      border: none;
      border-radius: 999px;
      padding: 0.8rem 1rem;
      background: transparent;
      color: var(--ink-soft);
      font: inherit;
      text-decoration: none;
      cursor: pointer;
    }

    .admin-nav a.active {
      background: linear-gradient(135deg, var(--accent-deep), var(--accent));
      color: white;
      box-shadow: 0 14px 30px rgba(20, 82, 96, 0.22);
    }

    .sidebar-meta {
      align-self: end;
      display: grid;
      gap: 0.35rem;
      padding: 1rem;
      border-radius: 22px;
      background: rgba(20, 82, 96, 0.06);
    }

    .meta-label,
    .meta-copy,
    .topbar-label {
      color: var(--ink-soft);
      font-size: 0.82rem;
    }

    .admin-main {
      min-width: 0;
      display: grid;
      grid-template-rows: auto 1fr;
    }

    .admin-topbar {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 1rem;
      padding: 1.25rem 1.5rem 0;
    }

    .topbar-actions {
      display: flex;
      align-items: center;
      gap: 0.75rem;
    }

    .topbar-actions button {
      background: linear-gradient(135deg, var(--accent-deep), var(--accent));
      color: white;
    }

    .admin-content {
      padding: 1.5rem;
    }

    .live-pill {
      border-radius: 999px;
      padding: 0.35rem 0.7rem;
      background: rgba(29, 109, 79, 0.12);
      color: #1d6d4f;
      font-size: 0.82rem;
      font-weight: 600;
    }

    /* ── EC2 Control Panel ── */
    .ec2-control {
      display: grid;
      gap: 0.6rem;
      padding: 1rem;
      border-radius: 18px;
      background: rgba(20, 82, 96, 0.06);
      border: 1px solid rgba(20, 60, 68, 0.1);
    }

    .ec2-status-row {
      display: flex;
      align-items: center;
      gap: 0.5rem;
    }

    .ec2-dot {
      width: 8px;
      height: 8px;
      border-radius: 50%;
      flex-shrink: 0;
      background: #94a3b8;
      transition: background 0.3s;
    }
    .ec2-dot.running  { background: #22c55e; box-shadow: 0 0 6px rgba(34,197,94,0.5); }
    .ec2-dot.stopped  { background: #ef4444; }
    .ec2-dot.pending  { background: #f59e0b; animation: pulse 1s infinite; }
    .ec2-dot.stopping { background: #f97316; animation: pulse 1s infinite; }

    @keyframes pulse {
      0%, 100% { opacity: 1; }
      50%       { opacity: 0.4; }
    }

    .ec2-label {
      font-size: 0.82rem;
      font-weight: 600;
      color: var(--ink-strong);
      flex: 1;
    }

    .ec2-refresh {
      background: none;
      border: none;
      cursor: pointer;
      font-size: 1rem;
      color: var(--ink-soft);
      padding: 0.1rem 0.3rem;
      border-radius: 6px;
      line-height: 1;
    }
    .ec2-refresh:hover { background: rgba(20, 60, 68, 0.08); }

    .ec2-msg {
      margin: 0;
      font-size: 0.78rem;
      color: var(--ink-soft);
      line-height: 1.4;
    }

    .ec2-err {
      margin: 0;
      font-size: 0.78rem;
      color: #ef4444;
    }

    .ec2-buttons {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 0.45rem;
    }

    .ec2-btn {
      border: none;
      border-radius: 999px;
      padding: 0.55rem 0.6rem;
      font: inherit;
      font-size: 0.78rem;
      font-weight: 600;
      cursor: pointer;
      transition: opacity 0.15s, transform 0.1s;
    }
    .ec2-btn:active { transform: scale(0.97); }
    .ec2-btn:disabled { opacity: 0.4; cursor: not-allowed; }

    .ec2-btn.start {
      background: linear-gradient(135deg, #166534, #22c55e);
      color: white;
    }

    .ec2-btn.stop {
      background: linear-gradient(135deg, #7f1d1d, #ef4444);
      color: white;
    }

    @media (max-width: 980px) {
      .admin-shell {
        grid-template-columns: 1fr;
      }

      .admin-sidebar {
        grid-template-rows: auto;
      }

      .admin-topbar {
        flex-direction: column;
        align-items: stretch;
      }
    }
  `],
})
export class AdminLayoutComponent {
  protected readonly system = inject(AdminSystemService);
  protected readonly videos = inject(AdminVideosService);
  protected readonly ec2 = inject(AdminEc2ControlService);
  private readonly calibration = inject(AdminCalibrationService);
  private readonly router = inject(Router);

  protected isRefreshing(): boolean {
    return this.system.loading() || this.videos.loading() || this.calibration.loading();
  }

  protected refresh(): void {
    this.system.refresh();
    this.calibration.refresh();
  }

  protected confirmStop(): void {
    if (confirm('Stop the surf system? The pipeline will drain its queues before EC2 shuts down.')) {
      this.ec2.stopSystem();
    }
  }

  logout(): void {
    this.system.auth.clearSession();
    this.router.navigate(['/login']);
  }
}
