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
  { path: 'admin', component: AdminComponent, canActivate: [authGuard] },
  { path: 'admin/videos/:videoId', component: AdminVideoDebugComponent, canActivate: [authGuard] },
  { path: 'upload-face', component: UploadFaceComponent, canActivate: [authGuard] },
  { path: 'my-videos', component: MyVideosComponent, canActivate: [authGuard] },
  { path: '**', redirectTo: 'my-videos' },
];
