(function () {
  'use strict';
  if (typeof window !== 'undefined' && 'serviceWorker' in navigator) {
    var registerSW = function () {
      navigator.serviceWorker
        .register('/sw.js', { scope: '/' })
        .catch(function (error) {
          console.warn('PWA Service Worker registration failed:', error);
        });
    };

    if (document.readyState === 'complete') {
      registerSW();
    } else {
      window.addEventListener('load', registerSW);
    }
  }
})();
