import { CommonModule } from '@angular/common';
import { HttpClient } from '@angular/common/http';
import { Component, inject } from '@angular/core';
import { Router, RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';

import { AuthService, MeResponse } from './core/auth.service';

@Component({
  selector: 'app-root',
  standalone: true,
  imports: [CommonModule, RouterOutlet, RouterLink, RouterLinkActive],
  template: `
    <div class="shell">
      <header class="app-header">
        <a routerLink="/my-videos" class="brand">Surf AI</a>

        <nav *ngIf="auth.isAuthenticated()">
          <a *ngIf="auth.isAdmin()" routerLink="/admin" routerLinkActive="active">Admin</a>
          <a routerLink="/upload-face" routerLinkActive="active">My Profile</a>
          <a routerLink="/my-videos" routerLinkActive="active">My Videos</a>
          <button type="button" (click)="logout()">Log out</button>
        </nav>
      </header>

      <main class="container">
        <router-outlet></router-outlet>
      </main>
    </div>
  `,
  styles: [`
    .shell {
      min-height: 100vh;
    }

    .app-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 1rem;
      padding: 1.25rem 2rem;
      margin: 1rem auto 0;
      width: min(1180px, calc(100% - 2rem));
      border-radius: 999px;
      background: rgba(255, 250, 244, 0.72);
      border: 1px solid rgba(20, 60, 68, 0.12);
      backdrop-filter: blur(12px);
      box-shadow: 0 14px 34px rgba(13, 40, 45, 0.08);
    }

    .brand {
      color: var(--ink-strong);
      text-decoration: none;
      font-family: 'Space Grotesk', sans-serif;
      font-size: 1.35rem;
      font-weight: 700;
      letter-spacing: 0.04em;
    }

    nav {
      display: flex;
      align-items: center;
      gap: 0.5rem;
      flex-wrap: wrap;
    }

    nav a,
    nav button {
      border: none;
      background: transparent;
      color: var(--ink-soft);
      text-decoration: none;
      font: inherit;
      padding: 0.65rem 1rem;
      border-radius: 999px;
      cursor: pointer;
    }

    nav a.active {
      background: rgba(20, 82, 96, 0.1);
      color: var(--accent-deep);
    }

    .container {
      max-width: 1180px;
      margin: 0 auto;
      padding: 2rem 1rem 3rem;
    }

    @media (max-width: 720px) {
      .app-header {
        border-radius: 28px;
        padding: 1rem;
        align-items: start;
        flex-direction: column;
      }
    }
  `],
})
export class AppComponent {
  protected readonly auth = inject(AuthService);
  private readonly http = inject(HttpClient);
  private readonly router = inject(Router);

  constructor() {
    if (this.auth.isAuthenticated()) {
      this.http
        .get('/api/me', { headers: this.auth.authHeaders() })
        .subscribe({
          next: (profile) => this.auth.setProfile(profile as MeResponse),
          error: () => {
            this.auth.clearSession();
            this.router.navigate(['/login']);
          },
        });
    }
  }

  logout(): void {
    this.auth.clearSession();
    this.router.navigate(['/login']);
  }
}
