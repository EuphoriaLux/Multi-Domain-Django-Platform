# PR Roadmap: Audit Issue Organization

> Tracking issue: [#344](https://github.com/EuphoriaLux/Multi-Domain-Django-Platform/issues/344)

This document organizes all 54 open audit issues into 11 themed Pull Requests.
Each issue has been labeled with `pr-group-N` on GitHub for easy filtering.

## Quick Reference

| PR | Theme | Issues | Priority | Label |
|----|-------|--------|----------|-------|
| 1 | Critical Security Fixes | #264, #265, #266, #269 | P0 | `pr-group-1` |
| 2 | Dead Code Cleanup | #325, #326, #327, #329, #331, #332 | P2 | `pr-group-2` |
| 3 | CSP Alpine.js Compliance | #294-#302 | P1 | `pr-group-3` |
| 4 | i18n URL Routing | #304, #305 | P1 | `pr-group-4` |
| 5 | i18n Hardcoded Strings | #313-#321 | P2 | `pr-group-5` |
| 6 | i18n Translations (crush_lu) | #306-#309 | P2 | `pr-group-6` |
| 7 | i18n Translations (other apps) | #310, #311, #322-#324 | P2 | `pr-group-7` |
| 8 | Perf: Query Optimization | #267, #268, #283-#286, #289 | P1 | `pr-group-8` |
| 9 | Perf: Indexing & Caching | #287, #288, #290-#293 | P2 | `pr-group-9` |
| 10 | Invitation Flow | #204 | P3 | `pr-group-10` |
| 11 | Admin Panel | #190 | P3 | `pr-group-11` |

## Recommended Merge Order

1. **PR 1** (Security) -- fix vulnerabilities first
2. **PR 4** (i18n Routing) -- API endpoints broken by language prefix
3. **PR 3** (CSP) -- Alpine.js features non-functional under CSP
4. **PR 8** (Query Perf) -- N+1 queries impact live users
5. **PR 2** (Dead Code) -- clean slate before new features
6. **PR 5** (Hardcoded Strings) -- code changes, then translations
7. **PR 9** (Indexing/Caching) -- infrastructure perf
8. **PR 6, PR 7** (Translations) -- .po file updates
9. **PR 10, PR 11** (Standalone) -- after discussion

## Filtering Issues by PR Group

Use GitHub label filters to see all issues in a specific PR:

```
https://github.com/EuphoriaLux/Multi-Domain-Django-Platform/issues?q=label:pr-group-1
```
