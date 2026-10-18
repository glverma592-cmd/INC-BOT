#!/usr/bin/env bash
# GitHub <-> Discord webhook connect karne ka one-shot setup.
#
# Zaroori:
#   - GITHUB_TOKEN  : PAT with `repo` scope (+ Actions secrets write)
#   - DISCORD_WEBHOOK : https://discord.com/api/webhooks/ID/TOKEN
#   - python3 + pynacl (pip install pynacl)
#
# Usage:
#   export GITHUB_TOKEN=ghp_xxx
#   export DISCORD_WEBHOOK='https://discord.com/api/webhooks/ID/TOKEN'
#   ./setup.sh OWNER/REPO
set -euo pipefail

REPO="${1:?usage: ./setup.sh OWNER/REPO}"
: "${GITHUB_TOKEN:?export GITHUB_TOKEN=<PAT> pehle set karo}"
: "${DISCORD_WEBHOOK:?export DISCORD_WEBHOOK=<discord webhook url> pehle set karo}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

API="https://api.github.com"
AUTH=(-H "Authorization: Bearer $GITHUB_TOKEN"
      -H "Accept: application/vnd.github+json"
      -H "X-GitHub-Api-Version: 2022-11-28")

echo "==> 1/5  repo verify: $REPO"
curl -sf "${AUTH[@]}" "$API/repos/$REPO" \
  | jq -r '"    \(.full_name)  private=\(.private)  default=\(.default_branch)"'

echo "==> 2/5  python deps"
python3 -c "import nacl" 2>/dev/null || pip3 install --quiet pynacl

echo "==> 3/5  DISCORD_WEBHOOK secret set kar raha hoon"
KEYJSON=$(curl -sf "${AUTH[@]}" "$API/repos/$REPO/actions/secrets/public-key")
KEY=$(jq -r .key    <<<"$KEYJSON")
KEYID=$(jq -r .key_id <<<"$KEYJSON")
SEALED=$(GH_KEY="$KEY" GH_VAL="$DISCORD_WEBHOOK" python3 -c '
import base64, os
from nacl.public import PublicKey, SealedBox
box = SealedBox(PublicKey(base64.b64decode(os.environ["GH_KEY"])))
print(base64.b64encode(box.encrypt(os.environ["GH_VAL"].encode())).decode())
')
CODE=$(curl -s -o /tmp/put.json -w "%{http_code}" -X PUT "${AUTH[@]}" \
  -d "{\"encrypted_value\":\"$SEALED\",\"key_id\":\"$KEYID\"}" \
  "$API/repos/$REPO/actions/secrets/DISCORD_WEBHOOK")
echo "    secret PUT -> HTTP $CODE $([ "$CODE" = 201 ] || [ "$CODE" = 204 ] && echo OK || cat /tmp/put.json)"

echo "==> 4/5  workflow file push kar raha hoon"
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
git clone -q "https://x-access-token:$GITHUB_TOKEN@github.com/$REPO.git" "$TMP/repo"
cd "$TMP/repo"
mkdir -p .github/workflows
cp "$SCRIPT_DIR/.github/workflows/discord-notify.yml" .github/workflows/
if git diff --quiet && git diff --cached --quiet; then
  echo "    workflow already up to date, kuch push nahi karna"
else
  git -c user.email="bot@arena.local" -c user.name="Arena setup" \
      commit -aqm "ci: Discord webhook notifications"
  git push -q origin HEAD
  echo "    pushed ✅"
fi

echo "==> 5/5  manual test trigger"
CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST "${AUTH[@]}" \
  "$API/repos/$REPO/actions/workflows/discord-notify.yml/dispatches" \
  -d '{"ref":"'"$(git rev-parse --abbrev-ref HEAD)"'"}')
echo "    workflow_dispatch -> HTTP $CODE (204 = queued)"

echo
echo "DONE. https://github.com/$REPO/actions  pe run dekho, Discord me embed aayega."
