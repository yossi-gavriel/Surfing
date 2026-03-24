import { inject } from '@angular/core';
import { CanActivateFn, Router } from '@angular/router';

import { AuthService } from './auth.service';

export const authGuard: CanActivateFn = (route) => {
  const auth = inject(AuthService);
  const router = inject(Router);
  if (!auth.isAuthenticated()) {
    return router.createUrlTree(['/login']);
  }

  const allowedRoles = route.data?.['roles'] as Array<'admin' | 'user'> | undefined;
  const role = auth.role();
  if (!allowedRoles?.length || !role || allowedRoles.includes(role)) {
    return true;
  }

  return router.createUrlTree([role === 'admin' ? '/admin' : '/my-videos']);
};
