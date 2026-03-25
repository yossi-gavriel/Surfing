import { Component, inject } from '@angular/core';
import { ActivatedRoute, Router } from '@angular/router';

@Component({
  selector: 'app-admin-legacy-video-redirect',
  standalone: true,
  template: '',
})
export class AdminLegacyVideoRedirectComponent {
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);

  constructor() {
    const videoId = this.route.snapshot.paramMap.get('videoId') ?? '';
    this.router.navigate(['/admin/debug', videoId], { replaceUrl: true });
  }
}
