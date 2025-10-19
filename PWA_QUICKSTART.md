# PWA Quick Start Guide - Crush.lu Implementation

This guide documents the **actual PWA implementation** completed for Crush.lu on the `feature/progressive-web-app` branch.

## What Was Implemented

✅ **Completed Features**:
- Web app manifest with Crush.lu branding
- Service worker with intelligent caching
- Offline fallback page
- PWA meta tags and iOS support
- Custom install prompt banner
- Domain-specific architecture (Crush.lu only, other domains unaffected)

## Files Created

### 1. Manifest File
**Location**: `static/crush_lu/manifest.json`
- Defines app name, colors, icons, and shortcuts
- Theme color: Purple (#9B59B6)
- Shortcuts for Events, Dashboard, and Connections

### 2. Service Worker
**Location**: `static/crush_lu/service-worker.js`
- Cache-first for static assets (CSS, JS, icons)
- Network-first for dynamic content (events, profiles)
- No cache for API, auth, and admin endpoints
- Offline fallback to `/offline/` page

### 3. Offline Page
**Location**: `crush_lu/templates/crush_lu/offline.html`
- Beautiful branded offline experience
- Shows what works offline vs what doesn't
- Retry button with gradient Crush.lu styling

### 4. PWA Install Prompt
**Location**: `static/crush_lu/js/pwa-install.js`
- Custom install banner (auto-shows when installable)
- Dismissable with localStorage memory
- Success notification on installation

### 5. Updated Base Template
**Location**: `crush_lu/templates/crush_lu/base.html`
- PWA meta tags (lines 33-45)
- Manifest link
- Apple touch icons
- Service worker registration script (lines 227-282)

### 6. Django Integration
- Added `offline_view()` to `crush_lu/views.py` (line 2226)
- Added `/offline/` URL route to `crush_lu/urls.py` (line 21)

## Icon Requirements

**Status**: Icons need to be generated! 📸

**Required Sizes** (all in `static/crush_lu/icons/`):
- 72x72, 96x96, 128x128, 144x144 pixels
- 152x152, 192x192, 384x384, 512x512 pixels

**Shortcut Icons** (96x96 pixels):
- `shortcut-events.png` - Calendar icon
- `shortcut-dashboard.png` - Dashboard icon
- `shortcut-connections.png` - Heart/connection icon

### Generating Icons

**Option 1: Automated (Recommended)**
```bash
npx pwa-asset-generator logo.svg static/crush_lu/icons \
  --background "#9B59B6" \
  --padding "10%" \
  --manifest static/crush_lu/manifest.json
```

**Option 2: Online Tools**
- [PWA Builder Image Generator](https://www.pwabuilder.com/imageGenerator)
- [RealFaviconGenerator](https://realfavicongenerator.net/)

**Design Guidelines**:
- Use Crush.lu colors: Purple (#9B59B6) and Pink (#FF6B9D)
- Simple heart or "C" logo design
- 10% padding for maskable icons (Android adaptive icons)
- Works on both light and dark backgrounds

## Testing the PWA

### Local Development Testing

1. **Start server**:
   ```bash
   python manage.py runserver
   ```

2. **Open Chrome DevTools** (F12):
   - Go to **Application** tab
   - Check **Manifest** section (verify all properties)
   - Check **Service Workers** section (should show registered)
   - Go to **Lighthouse** tab → Run PWA audit

3. **Test offline mode**:
   - DevTools → Network tab → Check "Offline"
   - Refresh page
   - Navigate to different pages
   - Verify offline page appears correctly

4. **Test installation**:
   - Look for install icon in Chrome address bar
   - Click to install
   - Open installed app (should open in standalone window)

### Production Testing (After Deployment)

1. **Access via HTTPS** (required for PWA):
   ```
   https://crush.lu
   ```

2. **Mobile Testing**:
   - **Android Chrome**: Look for "Add to Home Screen" banner
   - **iOS Safari**: Share → "Add to Home Screen"

3. **Verify Features**:
   - ✅ Install prompt appears
   - ✅ App installs correctly
   - ✅ Offline page loads when disconnected
   - ✅ Cached pages work offline
   - ✅ Icons display correctly

## How It Works

### Caching Strategy

**Static Assets** (Cache-First):
```
/static/crush_lu/css/crush.css
/static/bootstrap/css/bootstrap.min.css
/static/crush_lu/icons/*
```
→ Loads instantly from cache, updates in background

**Dynamic Content** (Network-First):
```
/events/
/dashboard/
/connections/
```
→ Always tries network first, falls back to cache if offline

**Never Cached** (Always Fresh):
```
/api/*
/accounts/*
/admin/*
/coach/*
```
→ Always requires internet connection

### Offline Behavior

1. **User goes offline**
2. Service worker intercepts requests
3. Serves cached content when available
4. Shows `/offline/` page for uncached content
5. User reconnects → Service worker syncs automatically

## Domain-Specific Design

### Current Implementation
- ✅ **Crush.lu**: Full PWA support
- ⏳ **VinsDelux**: Not implemented
- ⏳ **Entreprinder**: Not implemented
- ⏳ **PowerUP**: Not implemented

### Why Domain-Specific?

Each platform can have:
- **Different branding** (colors, icons, name)
- **Different features** (shortcuts, cached content)
- **Independent implementation** (won't break other apps)
- **Separate caching rules** (VinsDelux might cache wine images differently)

### Adding PWA to Other Platforms

To add PWA support to VinsDelux, Entreprinder, or PowerUP:

1. Copy and customize manifest:
   ```bash
   cp static/crush_lu/manifest.json static/vinsdelux/manifest.json
   ```

2. Update manifest with VinsDelux branding:
   - Change `name`, `short_name`, `theme_color`
   - Update icon paths
   - Customize shortcuts

3. Copy and customize service worker:
   ```bash
   cp static/crush_lu/service-worker.js static/vinsdelux/service-worker.js
   ```

4. Update cache names and URLs in service worker

5. Add PWA meta tags to VinsDelux base template

6. Create VinsDelux offline page

7. Generate VinsDelux-branded icons

**No changes needed to Crush.lu!** Each domain is independent.

## Deployment Checklist

Before deploying to production:

- [ ] Generate all required icons (72x72 to 512x512)
- [ ] Create shortcut icons (events, dashboard, connections)
- [ ] Test PWA installation locally
- [ ] Run Lighthouse audit (target: 90+ PWA score)
- [ ] Test offline functionality
- [ ] Verify manifest.json is accessible
- [ ] Verify service-worker.js is accessible
- [ ] Test on real mobile devices (Android + iOS)
- [ ] Deploy to Azure
- [ ] Test HTTPS is working (PWA requires HTTPS)
- [ ] Test installation on production domain

## Troubleshooting

### Service Worker Not Registering

**Symptom**: Console shows registration error

**Solutions**:
- Check HTTPS is enabled (required in production)
- Verify service worker path is correct
- Check for JavaScript syntax errors
- Clear browser cache and hard refresh (Ctrl+Shift+R)

### Install Prompt Not Showing

**Symptom**: No "Add to Home Screen" banner

**Solutions**:
- Verify all required icons exist (192x192 and 512x512 are mandatory)
- Check manifest.json is valid (use Chrome DevTools → Application → Manifest)
- Ensure app isn't already installed
- User must interact with site first (visit multiple pages)

### Offline Page Not Loading

**Symptom**: Blank page or error when offline

**Solutions**:
- Verify `/offline/` is in `STATIC_CACHE_URLS` array in service worker
- Check service worker is active (DevTools → Application → Service Workers)
- Unregister service worker and re-register
- Clear all caches and try again

### Icons Not Displaying

**Symptom**: Generic gray icon instead of Crush.lu logo

**Solutions**:
- Verify icon files exist at paths specified in manifest.json
- Check icon sizes match manifest exactly
- Ensure icons are PNG format
- Clear app data and reinstall

## Performance Tips

### Optimize Cache Size
- Don't cache everything - be selective
- Set cache expiration policies
- Clean up old caches on service worker update

### Minimize Service Worker
- Keep service worker < 100KB
- Avoid importing large libraries
- Use vanilla JavaScript for best performance

### Preload Critical Assets
Only cache essential assets on install:
- Homepage
- CSS/JS
- Essential icons
- Offline page

## Future Enhancements

### Phase 2 Features (Not Yet Implemented)
- [ ] Push notifications for event reminders
- [ ] Background sync for offline message sending
- [ ] Periodic background sync for event updates
- [ ] Share target API (share to Crush.lu from other apps)
- [ ] App shortcuts with badges
- [ ] Enhanced install prompt with preview

### Analytics Tracking
Track PWA usage:
```javascript
// Track installations
window.addEventListener('appinstalled', (e) => {
  gtag('event', 'pwa_install');
});

// Track PWA launches
if (window.matchMedia('(display-mode: standalone)').matches) {
  gtag('event', 'pwa_launch');
}
```

## Resources

- [Chrome DevTools PWA Documentation](https://developer.chrome.com/docs/devtools/progressive-web-apps/)
- [PWA Builder](https://www.pwabuilder.com/) - Test and package PWAs
- [Lighthouse PWA Checklist](https://web.dev/pwa-checklist/)
- [Service Worker Cookbook](https://serviceworke.rs/)
- [MDN PWA Guide](https://developer.mozilla.org/en-US/docs/Web/Progressive_web_apps)

## Implementation Notes

**Date**: 2025-01-19
**Branch**: `feature/progressive-web-app`
**Platform**: Crush.lu (primary implementation)
**Status**: Core PWA features complete, icons pending generation
**Next Steps**: Generate icons, test on mobile, deploy to production
