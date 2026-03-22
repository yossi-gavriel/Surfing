import { Routes } from '@angular/router';
import { UploadComponent } from './upload/upload.component';
import { ResultsComponent } from './results/results.component';

export const routes: Routes = [
  { path: '', redirectTo: 'upload', pathMatch: 'full' },
  { path: 'upload', component: UploadComponent },
  { path: 'rides/:id', component: ResultsComponent }
];
