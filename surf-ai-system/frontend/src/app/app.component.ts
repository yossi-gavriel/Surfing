import { CommonModule } from '@angular/common';
import { HttpClient } from '@angular/common/http';
import { Component, inject } from '@angular/core';
import { Router, RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';

import { AuthService, MeResponse } from './core/auth.service';
import { AppLanguage, I18nService } from './core/i18n.service';

@Component({
  selector: 'app-root',
  standalone: true,
  imports: [CommonModule, RouterOutlet, RouterLink, RouterLinkActive],
  template: `
    <div class="shell">
      <header class="app-header">
        <a [routerLink]="homeRoute()" class="brand">Surf AI</a>

        <div class="header-actions">
          <div class="language-picker" role="group" aria-label="Language switcher">
            <span class="language-label">🌐 {{ currentLanguageLabel() }}</span>

            <div class="language-options">
              <button
                *ngFor="let language of i18n.availableLanguages"
                type="button"
                class="language-option"
                [class.active]="i18n.getCurrentLanguage() === language.code"
                [attr.aria-pressed]="i18n.getCurrentLanguage() === language.code"
                (click)="setLanguage(language.code)"
              >
                <span class="flag">{{ language.flag }}</span>
                <span>{{ language.label }}</span>
              </button>
            </div>
          </div>

          <nav *ngIf="auth.isAuthenticated()">
            <a *ngIf="auth.isAdmin()" routerLink="/admin" routerLinkActive="active">
              {{ i18n.t('nav.admin') }}
            </a>
            <a *ngIf="!auth.isAdmin()" routerLink="/upload-face" routerLinkActive="active">
              {{ i18n.t('nav.myProfile') }}
            </a>
            <a *ngIf="!auth.isAdmin()" routerLink="/my-videos" routerLinkActive="active">
              {{ i18n.t('nav.myVideos') }}
            </a>
            <button type="button" (click)="logout()">{{ i18n.t('nav.logOut') }}</button>
          </nav>
        </div>
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

    .header-actions,
    nav {
      display: flex;
      align-items: center;
      gap: 0.75rem;
      flex-wrap: wrap;
    }

    .header-actions {
      justify-content: flex-end;
    }

    .language-picker {
      display: inline-flex;
      align-items: center;
      gap: 0.75rem;
      padding: 0.4rem 0.5rem 0.4rem 0.85rem;
      border-radius: 999px;
      background: rgba(255, 255, 255, 0.82);
      border: 1px solid rgba(20, 60, 68, 0.12);
      box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.5);
    }

    .language-label {
      color: var(--ink-soft);
      font-size: 0.92rem;
      white-space: nowrap;
    }

    .language-options {
      display: inline-flex;
      align-items: center;
      gap: 0.35rem;
      padding: 0.2rem;
      border-radius: 999px;
      background: rgba(20, 82, 96, 0.06);
    }

    .language-option {
      display: inline-flex;
      align-items: center;
      gap: 0.45rem;
      border: none;
      border-radius: 999px;
      padding: 0.55rem 0.85rem;
      background: transparent;
      color: var(--ink-soft);
      font: inherit;
      font-size: 0.92rem;
      font-weight: 600;
      cursor: pointer;
      transition: background 0.2s ease, color 0.2s ease, transform 0.2s ease;
    }

    .language-option:hover {
      background: rgba(20, 82, 96, 0.08);
      color: var(--ink-strong);
    }

    .language-option.active {
      background: linear-gradient(135deg, var(--accent-deep), var(--accent));
      color: white;
      box-shadow: 0 10px 24px rgba(20, 82, 96, 0.22);
    }

    .language-option.active:hover {
      transform: translateY(-1px);
    }

    .flag {
      font-size: 1rem;
      line-height: 1;
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

      .header-actions {
        width: 100%;
        justify-content: space-between;
        align-items: stretch;
      }

      .language-picker {
        width: 100%;
        justify-content: space-between;
      }

      .language-options {
        flex: 1;
        justify-content: center;
      }
    }
  `],
})
export class AppComponent {
  protected readonly auth = inject(AuthService);
  protected readonly i18n = inject(I18nService);
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

  setLanguage(language: AppLanguage): void {
    this.i18n.setLanguage(language);
  }

  currentLanguageLabel(): string {
    const current = this.i18n.availableLanguages.find(
      (language) => language.code === this.i18n.getCurrentLanguage(),
    );

    return current ? `${current.flag} ${current.label}` : this.i18n.t('language.label');
  }

  homeRoute(): string {
    return this.auth.isAdmin() ? '/admin' : '/my-videos';
  }

  logout(): void {
    this.auth.clearSession();
    this.router.navigate(['/login']);
  }
}
