import { Injectable, signal } from '@angular/core';
import { HttpHeaders } from '@angular/common/http';

export interface AuthResponse {
  user_id: string;
  token: string;
}

@Injectable({ providedIn: 'root' })
export class AuthService {
  private readonly tokenKey = 'surf-ai-token';
  private readonly userIdKey = 'surf-ai-user-id';

  readonly token = signal<string | null>(localStorage.getItem(this.tokenKey));
  readonly userId = signal<string | null>(localStorage.getItem(this.userIdKey));

  setSession(response: AuthResponse): void {
    localStorage.setItem(this.tokenKey, response.token);
    localStorage.setItem(this.userIdKey, response.user_id);
    this.token.set(response.token);
    this.userId.set(response.user_id);
  }

  clearSession(): void {
    localStorage.removeItem(this.tokenKey);
    localStorage.removeItem(this.userIdKey);
    this.token.set(null);
    this.userId.set(null);
  }

  isAuthenticated(): boolean {
    return !!this.token();
  }

  authHeaders(): HttpHeaders {
    const token = this.token();
    return token ? new HttpHeaders({ Authorization: `Bearer ${token}` }) : new HttpHeaders();
  }
}
