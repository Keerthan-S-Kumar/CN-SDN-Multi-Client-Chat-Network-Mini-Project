#!/usr/bin/env bash
# One-time repo setup by the repo admin (B). Usage:
#   bash tools/setup_repo.sh <your-gh-user> <janhvi-gh-user> <harsha-gh-user>
# Needs: gh auth login. Run from the repo root (a git repo with the initial commit).
set -e
REPO="CN-SDN-Multi-Client-Chat-Network-Mini-Project"
B="$1"; A="$2"; C="$3"
[ -n "$B" ] && [ -n "$A" ] && [ -n "$C" ] || { echo "usage: $0 <B-user> <A-user> <C-user>"; exit 1; }

# 1. CODEOWNERS usernames
sed -i.bak "s/@USER_A/@$A/g; s/@USER_B/@$B/g; s/@USER_C/@$C/g" .github/CODEOWNERS && rm -f .github/CODEOWNERS.bak
git add -A && git commit -qm "CODEOWNERS: real usernames" || true

# 2. Create private repo and push
gh repo create "$REPO" --private --source=. --push
for u in "$A" "$C"; do
  gh api -X PUT "repos/$B/$REPO/collaborators/$u" -f permission=push >/dev/null && echo "invited $u"
done

# 3. Branch protection (needs GitHub Pro/Education for private repos; warns if unavailable)
gh api -X PUT "repos/$B/$REPO/branches/main/protection" --input - >/dev/null <<'JSON' \
  && echo "main protected" || echo "WARNING: branch protection unavailable on this plan - follow the PR rule by convention"
{"required_status_checks":null,"enforce_admins":false,
 "required_pull_request_reviews":{"required_approving_review_count":1},
 "restrictions":null,"allow_force_pushes":false}
JSON

# 4. Issues
bash tools/create_issues.sh
echo "done"
