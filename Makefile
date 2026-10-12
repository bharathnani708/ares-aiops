.PHONY: test lambda plan apply bootstrap ui load local teardown

test:            ## unit tests for all Python components
	cd apps/habitat-api && pytest -q
	cd apps/mission-control && pytest -q
	cd lambda/ai-analyst && pytest -q

lambda:          ## build the analyst Lambda package
	./scripts/package-lambda.sh

plan: lambda     ## terraform plan (local)
	terraform -chdir=infra/terraform plan

apply: lambda    ## terraform apply (local; CI does this on main)
	terraform -chdir=infra/terraform apply

bootstrap:       ## install Argo CD and hand the cluster to GitOps
	./scripts/bootstrap-argocd.sh

ui:              ## port-forward Mission Control, Grafana, Prometheus, Argo CD
	./scripts/ui.sh

load:            ## steady traffic against habitat-api
	./scripts/load.sh 5

local:           ## run habitat-api + mission-control + Prometheus on your laptop
	docker compose -f local/docker-compose.yml up --build

teardown:        ## destroy everything that costs money
	./scripts/teardown.sh
