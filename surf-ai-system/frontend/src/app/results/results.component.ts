import { Component, OnInit, inject, signal, OnDestroy } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { ActivatedRoute, Router } from '@angular/router';
import { CommonModule } from '@angular/common';

@Component({
  selector: 'app-results',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './results.component.html',
  styles: [`
    .featured-video { width: 100%; border-radius: 12px; overflow: hidden; margin-bottom: 2rem; box-shadow: 0 8px 16px rgba(0,0,0,0.1); border: 1px solid #e2e8f0; position: relative; }
    .featured-video video { width: 100%; height: 60vh; max-height: 500px; object-fit: cover; background: #000; cursor: pointer; transition: opacity 0.3s ease;}
    .featured-card-body { padding: 1.5rem; background: white; }
    
    .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 1.5rem; }
    .video-card { background: white; border-radius: 8px; overflow: hidden; box-shadow: 0 4px 6px rgba(0,0,0,0.05); }
    .video-card video { width: 100%; height: 200px; object-fit: cover; background: #000; cursor: pointer; }
    .card-body { padding: 1rem; }

    .btn-share { background: #3b82f6; color: white; border: none; padding: 0.5rem 1rem; border-radius: 6px; cursor: pointer; font-family: inherit; font-size: 0.9rem; font-weight: 500; transition: background 0.2s;}
    .btn-share:hover { background: #2563eb; }
    .btn-retry { background: #64748b; color: white; border: none; padding: 0.75rem 1.5rem; border-radius: 6px; cursor: pointer; margin-top: 1rem; font-size: 1rem; transition: background 0.2s;}
    .btn-retry:hover { background: #475569; }
    .btn-secondary { background: #f1f5f9; color: #475569; margin-left:1rem; border: 1px solid #cbd5e1;}
    .btn-secondary:hover { background: #e2e8f0; }

    .spinner { border: 4px solid #f1f5f9; border-top: 4px solid #3b82f6; border-radius: 50%; width: 40px; height: 40px; animation: spin 1s linear infinite; margin: 0 auto 1rem; }
    @keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }

    .rtl-container { direction: rtl; text-align: right; }
    
    .message-box { padding: 3rem; background: white; border-radius: 12px; text-align: center; box-shadow: 0 4px 6px rgba(0,0,0,0.05); max-width: 500px; margin: 2rem auto; border: 1px solid #f1f5f9;}
    .message-box h3 { color: #1e293b; margin-bottom: 1rem; margin-top:0; font-size: 1.5rem;}
    .message-box p { color: #64748b; font-size: 1.1rem; margin-bottom: 1.5rem;}
    
    .conf-high { color: #16a34a; font-weight: 700; }
    .conf-medium { color: #d97706; font-weight: 700; }
    .conf-low { color: #dc2626; font-weight: 700; }
  `]
})
export class ResultsComponent implements OnInit, OnDestroy {
  private http = inject(HttpClient);
  private route = inject(ActivatedRoute);
  private router = inject(Router);

  userId = signal('');
  rides = signal<any[]>([]);
  status = signal('processing'); 
  
  loadingMessage = signal('🔍 מזהים אותך...');
  errorMessage = signal('');

  pollTimer: any;
  loadingMessageTimer: any;
  startTime: number = 0;
  
  ngOnInit() {
    this.userId.set(this.route.snapshot.paramMap.get('id') || '');
    this.startPollingProcess();
  }

  ngOnDestroy() {
    this.stopPolling();
  }
  
  startPollingProcess() {
    this.startTime = Date.now();
    this.status.set('processing');
    this.loadingMessage.set('🔍 מזהים אותך...');
    
    this.loadingMessageTimer = setInterval(() => {
      const elapsed = (Date.now() - this.startTime) / 1000;
      if (elapsed > 5 && elapsed <= 10) {
        this.loadingMessage.set('🎥 מעבדים את הסרטונים...');
      } else if (elapsed > 10) {
        this.loadingMessage.set('📦 כמעט מוכן...');
      }
    }, 1000);

    // Initial fetch bounds securely efficiently immediately
    this.fetchRides();
    
    this.pollTimer = setInterval(() => {
      const elapsed = (Date.now() - this.startTime) / 1000;
      if (elapsed > 60 && this.status() === 'processing') {
        this.status.set('timeout');
        this.stopPolling();
        return;
      }
      this.fetchRides();
    }, 3000);
  }

  stopPolling() {
    if (this.pollTimer) clearInterval(this.pollTimer);
    if (this.loadingMessageTimer) clearInterval(this.loadingMessageTimer);
  }

  fetchRides() {
    // Leverages exact Nginx proxy matrices directly parsing relative routing efficiently naturally
    this.http.get<{user_id: string, rides: any[], status: string, error?: string}>(`/api/users/${this.userId()}/rides`)
      .subscribe({
        next: (res) => {
          if (res.error) {
             this.status.set('error');
             this.stopPolling();
             return;
          }
          if (res.status === 'ready' && res.rides.length > 0) {
            this.rides.set(res.rides);
            this.status.set('ready');
            this.stopPolling();
          }
        },
        error: (err) => {
          console.error(err);
          this.status.set('error');
          this.errorMessage.set('שגיאה בטעינת הנתונים מהשרת.');
          this.stopPolling();
        }
      });
  }

  refresh() {
    this.startPollingProcess();
  }
  
  tryAnotherImage() {
    this.router.navigate(['/upload']);
  }

  async shareRide(ride: any) {
    if (navigator.share) {
      try {
        await navigator.share({
          title: 'הגלישה שלי',
          url: ride.video_url
        });
      } catch (err) {
        console.error('Share action gracefully escaped:', err);
      }
    } else {
      navigator.clipboard.writeText(ride.video_url);
      alert('הקישור הועתק ללוח במכשירך!');
    }
  }
}
