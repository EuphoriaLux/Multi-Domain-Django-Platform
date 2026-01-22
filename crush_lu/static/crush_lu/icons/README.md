# PWA Icons for Crush.lu

## Required Icons

Generate the following icons from the Crush.lu logo (purple/pink gradient theme):

- `icon-72x72.png` - 72x72 pixels
- `icon-96x96.png` - 96x96 pixels
- `icon-128x128.png` - 128x128 pixels
- `icon-144x144.png` - 144x144 pixels
- `icon-152x152.png` - 152x152 pixels
- `icon-192x192.png` - 192x192 pixels
- `icon-384x384.png` - 384x384 pixels
- `icon-512x512.png` - 512x512 pixels

## Shortcuts Icons

- `shortcut-events.png` - 96x96 pixels (calendar/event icon)
- `shortcut-dashboard.png` - 96x96 pixels (dashboard icon)
- `shortcut-connections.png` - 96x96 pixels (connection/heart icon)

## Design Guidelines

- Use Crush.lu brand colors: Purple (#9B59B6) and Pink (#FF6B9D)
- Icons should work with both light and dark backgrounds
- Consider using a simple heart or "C" logo design
- Ensure icons are "maskable" (safe area in center for different device masks)

## Tools to Generate Icons

- [PWA Asset Generator](https://github.com/elegantapp/pwa-asset-generator)
- [RealFaviconGenerator](https://realfavicongenerator.net/)
- [PWA Builder Image Generator](https://www.pwabuilder.com/imageGenerator)

## Command to Generate (using pwa-asset-generator):

```bash
npx pwa-asset-generator logo.svg static/crush_lu/icons --background "#9B59B6" --padding "10%"
```
