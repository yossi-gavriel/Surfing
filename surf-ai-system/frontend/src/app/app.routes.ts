import { Routes } from '@angular/router';
import { authGuard } from './core/auth.guard';
import { LoginComponent } from './pages/login/login.component';
import { MyVideosComponent } from './pages/my-videos/my-videos.component';
import { UploadFaceComponent } from './pages/upload-face/upload-face.component';

export const routes: Routes = [
  { path: '', redirectTo: 'my-videos', pathMatch: 'full' },
  { path: 'login', component: LoginComponent },
  {
    path: 'admin',
    canActivate: [authGuard],
    data: { roles: ['admin'] },
    loadChildren: () => import('./pages/admin/admin.routes').then((module) => module.ADMIN_ROUTES),
  },
  { path: 'upload-face', component: UploadFaceComponent, canActivate: [authGuard], data: { roles: ['user', 'admin'] } },
  { path: 'my-videos', component: MyVideosComponent, canActivate: [authGuard], data: { roles: ['user', 'admin'] } },
  { path: '**', redirectTo: 'my-videos' },
];
