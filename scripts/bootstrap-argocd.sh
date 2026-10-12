#!/usr/bin/env bash
# One-time cluster bootstrap, run from your laptop after `terraform apply`:
#   1. namespaces + the two secrets that must never live in Git
#   2. Argo CD (Helm)
#   3. the AppProject and the root "app of apps" - from here on, Git drives the cluster
# Optional: GIT_USERNAME + GIT_TOKEN (read-only PAT) if your repo is private.
set -euo pipefail
cd "$(dirname "$0")/.."
ARGOCD_CHART_VERSION=${ARGOCD_CHART_VERSION:-"^8.0.0"}

grep -rq '__GIT_REPO_URL__' gitops && { echo "Run scripts/configure.sh first." >&2; exit 1; }
kubectl cluster-info >/dev/null

echo "==> namespaces"
kubectl create namespace ares --dry-run=client -o yaml | kubectl apply -f -
kubectl label namespace ares pod-security.kubernetes.io/enforce=restricted --overwrite
kubectl create namespace monitoring --dry-run=client -o yaml | kubectl apply -f -

echo "==> secrets (random, generated locally, never committed)"
if ! kubectl -n ares get secret ares-chaos-token >/dev/null 2>&1; then
  kubectl -n ares create secret generic ares-chaos-token --from-literal=token="$(openssl rand -hex 24)"
fi
if ! kubectl -n monitoring get secret grafana-admin >/dev/null 2>&1; then
  kubectl -n monitoring create secret generic grafana-admin \
    --from-literal=admin-user=admin --from-literal=admin-password="$(openssl rand -base64 18)"
fi

echo "==> Argo CD"
helm repo add argo https://argoproj.github.io/argo-helm >/dev/null 2>&1 || true
helm repo update argo >/dev/null
helm upgrade --install argocd argo/argo-cd --namespace argocd --create-namespace \
  --version "$ARGOCD_CHART_VERSION" -f gitops/bootstrap/argocd-values.yaml --wait --timeout 10m

if [[ -n "${GIT_TOKEN:-}" ]]; then
  REPO_URL=$(awk '/repoURL:/ {print $2; exit}' gitops/bootstrap/root-app.yaml)
  kubectl -n argocd create secret generic ares-repo --dry-run=client -o yaml \
    --from-literal=type=git --from-literal=url="$REPO_URL" \
    --from-literal=username="${GIT_USERNAME:-git}" --from-literal=password="$GIT_TOKEN" \
    | kubectl label --local -f - argocd.argoproj.io/secret-type=repository -o yaml | kubectl apply -f -
fi

echo "==> AppProject + root application"
kubectl apply -f gitops/bootstrap/project.yaml
kubectl apply -f gitops/bootstrap/root-app.yaml

cat <<MSG

Bootstrap complete. Argo CD is now syncing everything under gitops/apps.

  Argo CD UI:   kubectl -n argocd port-forward svc/argocd-server 8443:443   -> https://localhost:8443
  admin pass:   kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d; echo
  Grafana:      kubectl -n monitoring port-forward svc/kube-prometheus-stack-grafana 3000:80 -> http://localhost:3000
  Grafana pass: kubectl -n monitoring get secret grafana-admin -o jsonpath='{.data.admin-password}' | base64 -d; echo
  Mission Ctl:  kubectl -n ares port-forward svc/mission-control 8080:80   -> http://localhost:8080
MSG
