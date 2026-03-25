import { Routes } from '@angular/router';

import { AdminLayoutComponent } from './admin-layout.component';

export const ADMIN_ROUTES: Routes = [
  {
    path: '',
    component: AdminLayoutComponent,
    children: [
      { path: '', redirectTo: 'dashboard', pathMatch: 'full' },
      {
        path: 'dashboard',
        loadComponent: () => import('./admin.component').then((module) => module.AdminComponent),
      },
      {
        path: 'results',
        loadComponent: () => import('./admin-results.component').then((module) => module.AdminResultsComponent),
      },
      {
        path: 'videos/:videoId/tracks/:trackId',
        loadComponent: () =>
          import('./admin-track-decision.component').then((module) => module.AdminTrackDecisionComponent),
      },
      {
        path: 'calibration',
        loadComponent: () =>
          import('./admin-calibration.component').then((module) => module.AdminCalibrationComponent),
      },
      {
        path: 'debug/:videoId',
        loadComponent: () =>
          import('./admin-video-debug.component').then((module) => module.AdminVideoDebugComponent),
      },
      {
        path: 'videos/:videoId',
        loadComponent: () =>
          import('./admin-legacy-video-redirect.component').then((module) => module.AdminLegacyVideoRedirectComponent),
      },
      { path: '**', redirectTo: 'dashboard' },
    ],
  },
];
