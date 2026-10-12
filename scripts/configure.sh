#!/usr/bin/env bash
# Fill in the placeholders in gitops/ with your AWS account, region and repo URL.
#   ./scripts/configure.sh <aws-account-id> <region> <https://github.com/you/ares-aiops.git>
set -euo pipefail
ACCOUNT=${1:?aws account id}; REGION=${2:?region}; REPO=${3:?git repo https url}
[[ "$ACCOUNT" =~ ^[0-9]{12}$ ]] || { echo "account id must be 12 digits" >&2; exit 1; }
cd "$(dirname "$0")/.."
grep -rl --include='*.yaml' -e '__AWS_ACCOUNT_ID__' -e '__AWS_REGION__' -e '__GIT_REPO_URL__' gitops | while read -r f; do
  sed -i.bak -e "s#__AWS_ACCOUNT_ID__#${ACCOUNT}#g" -e "s#__AWS_REGION__#${REGION}#g" -e "s#__GIT_REPO_URL__#${REPO}#g" "$f"
  rm -f "$f.bak"; echo "configured $f"
done
