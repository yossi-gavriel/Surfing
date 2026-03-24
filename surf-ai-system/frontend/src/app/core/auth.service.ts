import { Injectable, signal } from '@angular/core';
import { HttpHeaders } from '@angular/common/http';

export interface AuthResponse {
  user_id: string;
  token: string;
  email?: string;
  role?: 'admin' | 'user';
  pool_id?: string | null;
}

export interface MeResponse {
  user_id: string;
  email: string;
  role: 'admin' | 'user';
  pool_id: string | null;
  pool: {
    pool_id: string;
    name: string;
  } | null;
  reference_images_count: number;
}

@Injectable({ providedIn: 'root' })
export class AuthService {
  private readonly tokenKey = 'surf-ai-token';
  private readonly userIdKey = 'surf-ai-user-id';
  private readonly emailKey = 'surf-ai-email';
  private readonly roleKey = 'surf-ai-role';
  private readonly poolIdKey = 'surf-ai-pool-id';
  private readonly selectedPoolIdKey = 'surf-ai-selected-pool-id';

  readonly token = signal<string | null>(localStorage.getItem(this.tokenKey));
  readonly userId = signal<string | null>(localStorage.getItem(this.userIdKey));
  readonly email = signal<string | null>(localStorage.getItem(this.emailKey));
  readonly role = signal<'admin' | 'user' | null>((localStorage.getItem(this.roleKey) as 'admin' | 'user' | null) ?? null);
  readonly poolId = signal<string | null>(localStorage.getItem(this.poolIdKey));
  readonly selectedPoolId = signal<string | null>(localStorage.getItem(this.selectedPoolIdKey) ?? localStorage.getItem(this.poolIdKey));

  setSession(response: AuthResponse): void {
    localStorage.setItem(this.tokenKey, response.token);
    localStorage.setItem(this.userIdKey, response.user_id);
    if (response.email) {
      localStorage.setItem(this.emailKey, response.email);
    }
    if (response.role) {
      localStorage.setItem(this.roleKey, response.role);
    }
    if (response.pool_id !== undefined) {
      if (response.pool_id === null) {
        localStorage.removeItem(this.poolIdKey);
      } else {
        localStorage.setItem(this.poolIdKey, response.pool_id);
      }
      this.setSelectedPoolId(response.pool_id);
    }
    this.token.set(response.token);
    this.userId.set(response.user_id);
    this.email.set(response.email ?? this.email());
    this.role.set(response.role ?? this.role());
    if (response.pool_id !== undefined) {
      this.poolId.set(response.pool_id);
    }
  }

  setProfile(profile: MeResponse): void {
    localStorage.setItem(this.userIdKey, profile.user_id);
    localStorage.setItem(this.emailKey, profile.email);
    localStorage.setItem(this.roleKey, profile.role);
    if (profile.pool_id === null) {
      localStorage.removeItem(this.poolIdKey);
    } else {
      localStorage.setItem(this.poolIdKey, profile.pool_id);
    }
    this.setSelectedPoolId(profile.pool_id);
    this.userId.set(profile.user_id);
    this.email.set(profile.email);
    this.role.set(profile.role);
    this.poolId.set(profile.pool_id);
  }

  setSelectedPoolId(poolId: string | null): void {
    if (poolId === null || poolId === '') {
      localStorage.removeItem(this.selectedPoolIdKey);
      this.selectedPoolId.set(null);
      return;
    }

    localStorage.setItem(this.selectedPoolIdKey, poolId);
    this.selectedPoolId.set(poolId);
  }

  clearSession(): void {
    localStorage.removeItem(this.tokenKey);
    localStorage.removeItem(this.userIdKey);
    localStorage.removeItem(this.emailKey);
    localStorage.removeItem(this.roleKey);
    localStorage.removeItem(this.poolIdKey);
    localStorage.removeItem(this.selectedPoolIdKey);
    this.token.set(null);
    this.userId.set(null);
    this.email.set(null);
    this.role.set(null);
    this.poolId.set(null);
    this.selectedPoolId.set(null);
  }

  isAuthenticated(): boolean {
    return !!this.token();
  }

  isAdmin(): boolean {
    return this.role() === 'admin';
  }

  authHeaders(): HttpHeaders {
    const token = this.token();
    return token ? new HttpHeaders({ Authorization: `Bearer ${token}` }) : new HttpHeaders();
  }
}
