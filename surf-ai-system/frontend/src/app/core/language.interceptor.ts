import { HttpInterceptorFn } from '@angular/common/http';
import { inject } from '@angular/core';

import { I18nService } from './i18n.service';

export const languageInterceptor: HttpInterceptorFn = (req, next) => {
  const i18n = inject(I18nService);

  return next(
    req.clone({
      setHeaders: {
        'X-App-Language': i18n.currentLanguage(),
      },
    }),
  );
};
