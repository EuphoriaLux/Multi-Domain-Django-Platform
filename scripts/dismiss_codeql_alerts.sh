#!/bin/bash
# Dismiss CodeQL alerts in bulk
# Usage: ./scripts/dismiss_codeql_alerts.sh <rule-id> <reason> <comment>
#
# Example:
#   ./scripts/dismiss_codeql_alerts.sh "AZR-000202" "wont fix" "Infrastructure as code - reviewed and accepted"
#
# Valid reasons: false positive, wont fix, used in tests

RULE_ID="$1"
REASON="$2"
COMMENT="$3"

if [ -z "$RULE_ID" ] || [ -z "$REASON" ] || [ -z "$COMMENT" ]; then
  echo "Usage: $0 <rule-id> <reason> <comment>"
  echo ""
  echo "Valid reasons: 'false positive', 'wont fix', 'used in tests'"
  echo ""
  echo "Example:"
  echo "  $0 'AZR-000202' 'wont fix' 'Infrastructure reviewed and accepted'"
  exit 1
fi

REPO="EuphoriaLux/Multi-Domain-Django-Platform"

# Get all open alerts for this rule
echo "Finding open alerts for rule: $RULE_ID"
ALERT_NUMBERS=$(gh api "repos/$REPO/code-scanning/alerts?state=open&per_page=100" \
  --jq ".[] | select(.rule.id == \"$RULE_ID\") | .number")

if [ -z "$ALERT_NUMBERS" ]; then
  echo "No open alerts found for rule: $RULE_ID"
  exit 0
fi

COUNT=$(echo "$ALERT_NUMBERS" | wc -l)
echo "Found $COUNT alert(s) to dismiss"
echo ""

# Confirm
read -p "Dismiss $COUNT alerts for '$RULE_ID'? (y/N) " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
  echo "Cancelled"
  exit 0
fi

# Dismiss each alert
for ALERT_NUM in $ALERT_NUMBERS; do
  echo "Dismissing alert #$ALERT_NUM..."
  gh api --method PATCH \
    "repos/$REPO/code-scanning/alerts/$ALERT_NUM" \
    -f state='dismissed' \
    -f dismissed_reason="$REASON" \
    -f dismissed_comment="$COMMENT" \
    --silent
  echo "  ✅ Dismissed #$ALERT_NUM"
done

echo ""
echo "✅ Dismissed $COUNT alerts for rule: $RULE_ID"
