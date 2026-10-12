#!/usr/bin/env bash
# Open every UI at once on localhost (Ctrl+C stops them all).
set -euo pipefail
trap 'kill 0' EXIT
kubectl -n ares port-forward svc/mission-control 8080:80 &
kubectl -n ares port-forward svc/habitat-api 8081:80 &
kubectl -n monitoring port-forward svc/kube-prometheus-stack-grafana 3000:80 &
kubectl -n monitoring port-forward svc/kube-prometheus-stack-prometheus 9090:9090 &
kubectl -n argocd port-forward svc/argocd-server 8443:443 &
sleep 2
cat <<MSG
  Mission Control  http://localhost:8080
  Habitat app      http://localhost:8081
  Grafana          http://localhost:3000
  Prometheus       http://localhost:9090
  Argo CD          https://localhost:8443
MSG
wait
