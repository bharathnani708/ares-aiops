#!/usr/bin/env bash
# Remove everything that costs money. Order matters: let Argo CD delete its
# workloads first, then destroy the AWS infrastructure.
set -euo pipefail
cd "$(dirname "$0")/.."
if kubectl cluster-info >/dev/null 2>&1; then
  echo "==> deleting Argo CD applications (cascade)"
  kubectl -n argocd delete application root --wait=true --timeout=5m || true
  kubectl -n argocd delete applications --all --wait=true --timeout=5m || true
  helm -n argocd uninstall argocd || true
fi
echo "==> terraform destroy"
./scripts/package-lambda.sh >/dev/null   # archive_file needs the build dir to exist
terraform -chdir=infra/terraform destroy
cat <<MSG

Infrastructure destroyed. The bootstrap stack (state bucket, GitHub OIDC roles) costs
~nothing and is kept. To remove it too: empty the bucket's versions, then
  terraform -chdir=infra/bootstrap destroy
MSG
