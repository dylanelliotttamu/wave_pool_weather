/**
 * nav.js — Wave Pool Weather
 * Dynamically adds "Learn More" to the navbar (if not already present)
 * and highlights the active page link.
 */
(function () {
    'use strict';

    document.addEventListener('DOMContentLoaded', function () {
        var navbar = document.querySelector('.navbar');
        if (!navbar) return;

        // ── 1. Inject "Learn More" before "Feedback" if missing ──────────
        var links = navbar.querySelectorAll('a');
        var hasLearnMore = false;
        var feedbackLink = null;

        links.forEach(function (a) {
            var href = (a.getAttribute('href') || '').toLowerCase();
            if (href.indexOf('learn_more') !== -1) hasLearnMore = true;
            if (href.indexOf('feedback') !== -1) feedbackLink = a;
        });

        if (!hasLearnMore) {
            var learnLink = document.createElement('a');
            learnLink.href = 'learn_more.html';
            learnLink.textContent = 'Learn More';
            if (feedbackLink) {
                navbar.insertBefore(learnLink, feedbackLink);
            } else {
                navbar.appendChild(learnLink);
            }
        }

        // ── 2. Highlight active page ──────────────────────────────────────
        var currentPath = window.location.pathname.split('/').pop() || 'index.html';
        if (currentPath === '') currentPath = 'index.html';

        navbar.querySelectorAll('a').forEach(function (a) {
            var linkFile = (a.getAttribute('href') || '').split('/').pop();
            if (linkFile === currentPath) {
                a.classList.add('active');
            }
        });
    });
}());
