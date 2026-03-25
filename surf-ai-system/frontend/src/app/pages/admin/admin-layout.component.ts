import { CommonModule } from '@angular/common';
import { Component, inject } from '@angular/core';
import { Router, RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';

import { AdminCalibrationService } from './admin-calibration.service';
import { AdminContextService } from './admin-context.service';
import { AdminDebugCacheService } from './admin-debug-cache.service';
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
  private readonly calibration = inject(AdminCalibrationService);
  private readonly router = inject(Router);

  protected isRefreshing(): boolean {
    return this.system.loading() || this.videos.loading() || this.calibration.loading();
  }

  protected refresh(): void {
    this.system.refresh();
    this.calibration.refresh();
  }

  logout(): void {
    this.system.auth.clearSession();
    this.router.navigate(['/login']);
  }
}
