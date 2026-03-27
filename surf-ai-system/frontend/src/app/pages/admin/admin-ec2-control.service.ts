import { DestroyRef, inject, Injectable, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { HttpClient } from '@angular/common/http';
import { interval, switchMap, catchError, of } from 'rxjs';

// ── Types ────────────────────────────────────────────────────────────────────

export type Ec2State =
  | 'running'
  | 'stopped'
  | 'stopping'
  | 'pending'
  | 'shutting-down'
  | 'terminated'
  | 'unknown';

export interface SystemStatusResponse {
  state: Ec2State;
  instance_id: string;
  label: string;
  message: string;
}

export interface SystemActionResponse {
  status: string;
  instance_id: string;
  message: string;
}

// ── Service ──────────────────────────────────────────────────────────────────

/**
 * Calls the always-on AWS API Gateway to start/stop/status the EC2 instance.
 * This API Gateway is independent of EC2 — it works even when the instance is stopped.
 *
 * The API_BASE_URL is injected at build time from environment.ts.
 * After deploying the API Gateway, update SURF_AI_CONTROL_API_URL in environment.ts.
 */
@Injectable({ providedIn: 'root' })
export class AdminEc2ControlService {
  private readonly http = inject(HttpClient);
  private readonly destroyRef = inject(DestroyRef);

  // AWS API Gateway — always available regardless of EC2 state
  readonly apiBase: string =
    (window as any).__SURF_AI_CONTROL_API__ ??
    'https://djcqadh3mg.execute-api.us-east-1.amazonaws.com';

  readonly ec2State = signal<Ec2State>('unknown');
  readonly ec2Label = signal('Unknown');
  readonly ec2Message = signal('');
  readonly starting = signal(false);
  readonly stopping = signal(false);
  readonly statusError = signal('');
  readonly actionMessage = signal('');

  constructor() {
    if (this.apiBase) {
      this.loadStatus();
      this.startPolling();
    }
  }

  // ── Public API ─────────────────────────────────────────────────────────────

  loadStatus(): void {
    this.http
      .get<SystemStatusResponse>(`${this.apiBase}/system-status`)
      .pipe(catchError(() => of(null)))
      .subscribe((resp) => {
        if (resp) {
          this.ec2State.set(resp.state);
          this.ec2Label.set(resp.label);
          this.ec2Message.set(resp.message);
          this.statusError.set('');
        } else {
          this.statusError.set('Could not reach control API.');
        }
      });
  }

  startSystem(): void {
    if (this.starting()) return;
    this.starting.set(true);
    this.actionMessage.set('Sending start command…');
    this.statusError.set('');

    this.http
      .post<SystemActionResponse>(`${this.apiBase}/start-system`, {})
      .pipe(catchError((err) => of({ status: 'error', instance_id: '', message: err?.message ?? 'Network error' })))
      .subscribe((resp) => {
        this.starting.set(false);
        this.actionMessage.set(resp.message);
        if (resp.status === 'already_running') {
          this.ec2State.set('running');
        } else if (resp.status === 'starting') {
          this.ec2State.set('pending');
        }
      });
  }

  stopSystem(): void {
    if (this.stopping()) return;
    this.stopping.set(true);
    this.actionMessage.set('Sending stop command…');
    this.statusError.set('');

    this.http
      .post<SystemActionResponse>(`${this.apiBase}/stop-system`, {})
      .pipe(catchError((err) => of({ status: 'error', instance_id: '', message: err?.message ?? 'Network error' })))
      .subscribe((resp) => {
        this.stopping.set(false);
        this.actionMessage.set(resp.message);
        if (resp.status === 'stopping') {
          this.ec2State.set('stopping');
        }
      });
  }

  // ── Internals ──────────────────────────────────────────────────────────────

  private startPolling(): void {
    // Poll every 15 seconds to keep state fresh
    interval(15_000)
      .pipe(
        takeUntilDestroyed(this.destroyRef),
        switchMap(() =>
          this.http
            .get<SystemStatusResponse>(`${this.apiBase}/system-status`)
            .pipe(catchError(() => of(null))),
        ),
      )
      .subscribe((resp) => {
        if (resp) {
          this.ec2State.set(resp.state);
          this.ec2Label.set(resp.label);
          this.ec2Message.set(resp.message);
        }
      });
  }
}
