# Vendored third-party JavaScript

These files are byte-for-byte copies of pinned, published CDN releases. They are
served through the normal static pipeline (WhiteNoise + `CompressedManifest`
storage), so they get content-hashed filenames and far-future cache headers.

## Why they are vendored, not fetched at build time

HTMX and Alpine are load-bearing for crush.lu (onboarding wizard, event lobby,
check-in UI, projector displays). Loading them from a CDN meant a jsDelivr
outage or a network-path failure degraded the live site for real members.
Subresource Integrity guarded *integrity* but never *availability* — and on a
tampered file SRI blocks the load, which is still a broken page.

Vendoring also removes a per-visitor privacy leak: the CDN no longer sees the IP
of everyone who opens the site.

A build-time fetch would reintroduce exactly the availability dependency this
removes, so the bytes are committed instead. Note that `package.json` lists
`htmx.org` and `alpinejs` for tooling only; those entries are a *different*
package set at different versions (`alpinejs` is not the CSP build) and are
**not** the source of these files.

## Provenance

| File | Upstream |
| --- | --- |
| `htmx-2.0.4.min.js` | `https://cdn.jsdelivr.net/npm/htmx.org@2.0.4/dist/htmx.min.js` |
| `alpinejs-csp-3.13.3.min.js` | `https://cdn.jsdelivr.net/npm/@alpinejs/csp@3.13.3/dist/cdn.min.js` |
| `sortable-1.15.2.min.js` | `https://cdn.jsdelivr.net/npm/sortablejs@1.15.2/Sortable.min.js` |

`chart.umd.min.js` predates this note and has no recorded upstream pin.

⚠️ `alpinejs-csp-3.13.3.min.js` must stay the **`@alpinejs/csp`** build. The
standard `alpinejs` build evaluates expression strings and needs `unsafe-eval`,
which the site's CSP does not grant.

## Bumping a version

1. Download the new version and verify it against the hash published by the CDN.
2. Name the file with its version, delete the old one, and update the `src` in
   every template that references it (`grep -rn "js/vendor/" crush_lu/templates/`).
3. Update the table above.

To re-verify a file against its upstream at any time:

```bash
curl -fsSL <upstream-url> | openssl dgst -sha384 -binary | openssl base64 -A
openssl dgst -sha384 -binary <local-file> | openssl base64 -A
```

Both commands must print the same digest. The three files above were verified
this way against the `integrity` attributes that were previously on the script
tags, so the vendored bytes are provably the same code that was being served.
