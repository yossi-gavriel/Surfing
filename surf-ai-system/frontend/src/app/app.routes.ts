import { Routes } from '@angular/router';
import { authGuard } from './core/auth.guard';
import { AdminComponent } from './pages/admin/admin.component';
import { AdminVideoDebugComponent } from './pages/admin/admin-video-debug.component';
import { LoginComponent } from './pages/login/login.component';
import { MyVideosComponent } from './pages/my-videos/my-videos.component';
import { UploadFaceComponent } from './pages/upload-face/upload-face.component';

export const routes: Routes = [
  { path: '', redirectTo: 'my-videos', pathMatch: 'full' },
  { path: 'login', component: LoginComponent },
  { path: 'admin', component: AdminComponent, canActivate: [authGuard], data: { roles: ['admin'] } },
  { path: 'admin/videos/:videoId', component: AdminVideoDebugComponent, canActivate: [authGuard], data: { roles: ['admin'] } },
  { path: 'upload-face', component: UploadFaceComponent, canActivate: [authGuard], data: { roles: ['user', 'admin'] } },
  { path: 'my-videos', component: MyVideosComponent, canActivate: [authGuard], data: { roles: ['user', 'admin'] } },
  { path: '**', redirectTo: 'my-videos' },
];
