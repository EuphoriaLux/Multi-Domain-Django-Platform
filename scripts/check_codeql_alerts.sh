#!/bin/bash
# Check CodeQL Alert Status
# Usage: ./scripts/check_codeql_alerts.sh

set -e

REPO="EuphoriaLux/Multi-Domain-Django-Platform"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  CodeQL Security Alert Status"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# Get summary counts
TOTAL=$(gh api "repos/$REPO/code-scanning/alerts?per_page=100" --jq 'length')
OPEN=$(gh api "repos/$REPO/code-scanning/alerts?state=open&per_page=100" --jq 'length')
FIXED=$(gh api "repos/$REPO/code-scanning/alerts?state=fixed&per_page=100" --jq 'length')
DISMISSED=$(gh api "repos/$REPO/code-scanning/alerts?state=dismissed&per_page=100" --jq 'length')

echo "📊 Summary:"
echo "  Total alerts:     $TOTAL"
echo "  🔴 Open:          $OPEN"
echo "  ✅ Fixed:         $FIXED"
echo "  🔕 Dismissed:     $DISMISSED"
echo ""

# Group by rule type
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Open Alerts by Type"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
gh api "repos/$REPO/code-scanning/alerts?state=open&per_page=100" \
  --jq '.[] | "\(.rule.id)"' | sort | uniq -c | sort -rn | \
  awk '{printf "  %-50s %3d\n", $2, $1}'

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Recent Activity (Last 5 Fixed)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
gh api "repos/$REPO/code-scanning/alerts?state=fixed&per_page=5" \
  --jq '.[] | "  ✅ #\(.number) - \(.rule.id) - \(.fixed_at | fromdatedate | strftime("%Y-%m-%d %H:%M"))"'

echo ""
echo "🔗 View all alerts: https://github.com/$REPO/security/code-scanning"
echo ""
