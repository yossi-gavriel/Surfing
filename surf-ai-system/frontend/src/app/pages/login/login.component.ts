import { CommonModule } from '@angular/common';
import { HttpClient } from '@angular/common/http';
import { Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';

import { AuthResponse, AuthService } from '../../core/auth.service';

@Component({
  selector: 'app-login-page',
  standalone: true,
  imports: [CommonModule, FormsModule],
  template: `
    <section class="hero-card">
      <div class="hero-copy">
        <p class="eyebrow">Surf AI</p>
        <h1>Find every wave you appeared in.</h1>
        <p class="subtitle">
          Sign in, upload a clear face photo, and we will surface the matched clips from the
          production pipeline.
        </p>
      </div>

      <form class="auth-card" (ngSubmit)="submit()">
        <p class="mode">{{ isSignup() ? 'Create account' : 'Welcome back' }}</p>
        <label>
          <span>Email</span>
          <input [(ngModel)]="email" name="email" type="email" placeholder="you@example.com" required />
        </label>

        <label>
          <span>Password</span>
          <input [(ngModel)]="password" name="password" type="password" placeholder="At least 6 characters" required />
        </label>

        <button type="submit" [disabled]="submitting()">
          {{ submitting() ? 'Working...' : (isSignup() ? 'Sign up' : 'Log in') }}
        </button>

        <p class="error" *ngIf="errorMessage()">{{ errorMessage() }}</p>

        <button type="button" class="link-button" (click)="toggleMode()">
          {{ isSignup() ? 'Already have an account? Log in' : 'Need an account? Sign up' }}
        </button>
      </form>
    </section>
  `,
  styles: [`
    :host {
      display: block;
    }

    .hero-card {
      min-height: calc(100vh - 180px);
      display: grid;
      grid-template-columns: 1.2fr 0.9fr;
      gap: 2rem;
      align-items: center;
    }

    .hero-copy {
      padding: 2rem 0;
    }

    .eyebrow {
      text-transform: uppercase;
      letter-spacing: 0.18em;
      color: var(--accent-deep);
      font-size: 0.82rem;
      margin-bottom: 1rem;
    }

    h1 {
      margin: 0;
      font-size: clamp(3rem, 6vw, 5rem);
      line-height: 0.96;
      font-family: 'Space Grotesk', sans-serif;
      color: var(--ink-strong);
    }

    .subtitle {
      max-width: 34rem;
      margin-top: 1.25rem;
      font-size: 1.05rem;
      color: var(--ink-soft);
      line-height: 1.7;
    }

    .auth-card {
      background: rgba(255, 249, 240, 0.88);
      border: 1px solid rgba(20, 60, 68, 0.12);
      border-radius: 28px;
      padding: 2rem;
      backdrop-filter: blur(12px);
      box-shadow: 0 24px 60px rgba(13, 40, 45, 0.12);
    }

    .mode {
      margin: 0 0 1rem;
      font-family: 'Space Grotesk', sans-serif;
      font-size: 1.4rem;
      color: var(--ink-strong);
    }

    label {
      display: block;
      margin-bottom: 1rem;
    }

    span {
      display: block;
      margin-bottom: 0.4rem;
      color: var(--ink-soft);
      font-size: 0.92rem;
    }

    input {
      width: 100%;
      border: 1px solid rgba(20, 60, 68, 0.18);
      border-radius: 16px;
      padding: 0.95rem 1rem;
      background: rgba(255, 255, 255, 0.88);
      font: inherit;
      color: var(--ink-strong);
    }

    button {
      width: 100%;
      border: none;
      border-radius: 999px;
      padding: 0.95rem 1.2rem;
      background: linear-gradient(135deg, var(--accent-deep), var(--accent));
      color: white;
      font: inherit;
      font-weight: 600;
      cursor: pointer;
      transition: transform 0.2s ease, box-shadow 0.2s ease;
      box-shadow: 0 14px 30px rgba(20, 82, 96, 0.22);
    }

    button:hover:not(:disabled) {
      transform: translateY(-1px);
    }

    button:disabled {
      opacity: 0.72;
      cursor: wait;
    }

    .link-button {
      width: auto;
      margin-top: 1rem;
      background: transparent;
      color: var(--accent-deep);
      box-shadow: none;
      padding: 0;
    }

    .error {
      margin: 1rem 0 0;
      color: #a7341b;
    }

    @media (max-width: 900px) {
      .hero-card {
        grid-template-columns: 1fr;
        min-height: auto;
      }
    }
  `],
})
export class LoginComponent {
  private readonly http = inject(HttpClient);
  private readonly router = inject(Router);
  private readonly auth = inject(AuthService);

  email = '';
  password = '';
  readonly isSignup = signal(false);
  readonly submitting = signal(false);
  readonly errorMessage = signal('');

  toggleMode(): void {
    this.isSignup.update((value) => !value);
    this.errorMessage.set('');
  }

  submit(): void {
    this.submitting.set(true);
    this.errorMessage.set('');

    const endpoint = this.isSignup() ? 'signup' : 'login';
    this.http
      .post<AuthResponse>(`/api/auth/${endpoint}`, {
        email: this.email,
        password: this.password,
      })
      .subscribe({
        next: (response) => {
          this.auth.setSession(response);
          this.submitting.set(false);
          this.router.navigate([response.role === 'admin' ? '/admin' : '/upload-face']);
        },
        error: (error) => {
          this.submitting.set(false);
          this.errorMessage.set(error.error?.detail || 'Unable to authenticate right now.');
        },
      });
  }
}
