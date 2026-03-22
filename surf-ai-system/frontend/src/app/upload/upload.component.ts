import { Component, inject, signal } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Router } from '@angular/router';
import { CommonModule } from '@angular/common';

@Component({
  selector: 'app-upload',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './upload.component.html',
  styles: [`
    .upload-container { max-width: 600px; margin: 0 auto; direction: rtl; text-align: right; }
    .card { background: white; padding: 2.5rem; border-radius: 12px; box-shadow: 0 8px 16px rgba(0,0,0,0.05); text-align: center; border: 1px solid #f1f5f9; }
    h2 { margin-top: 0; color: #1e293b; font-weight: 700; margin-bottom: 2rem;}
    .file-drop-area { border: 2px dashed #cbd5e1; padding: 3.5rem 2rem; border-radius: 8px; margin-bottom: 1.5rem; transition: all 0.2s; background: #fafafa;}
    .file-drop-area:hover { border-color: #3b82f6; background: #eff6ff; }
    input[type=file] { margin-top: 1rem; cursor: pointer; font-size: 1rem; font-family: inherit;}
    .loading { margin-top: 2rem; color: #3b82f6; font-weight: bold; }
    .spinner { border: 4px solid #f1f5f9; border-top: 4px solid #3b82f6; border-radius: 50%; width: 40px; height: 40px; animation: spin 1s linear infinite; margin: 0 auto 1rem; }
    @keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
    .error { margin-top: 1.5rem; color: #dc2626; background: #fef2f2; padding: 1.2rem; border-radius: 6px; border: 1px solid #fca5a5; font-weight:500;}
  `]
})
export class UploadComponent {
  private http = inject(HttpClient);
  private router = inject(Router);

  isUploading = signal(false);
  errorMessage = signal('');

  onFileSelected(event: any) {
    const file = event.target.files[0];
    if (file) {
      this.uploadImage(file);
    }
  }

  uploadImage(file: File) {
    this.isUploading.set(true);
    this.errorMessage.set('');

    const formData = new FormData();
    formData.append('file', file);

    // Uses structural reverse-proxied /api/ resolving dynamically internally naturally
    this.http.post<any>('/api/users', formData)
      .subscribe({
        next: (res) => {
          this.isUploading.set(false);
          if (res.error) {
             this.errorMessage.set(res.message || 'התמונה לא זוהתה. נסה תמונה אחרת.');
          } else {
             this.router.navigate(['/rides', res.user_id]);
          }
        },
        error: (err) => {
          this.isUploading.set(false);
          this.errorMessage.set('שגיאה בתקשורת עם השרת. אנא בדוק אם השרת עובד כראוי.');
          console.error('API Context Extensively Blocked Mapping Native HTTP requests:', err);
        }
      });
  }
}
