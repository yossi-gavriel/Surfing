.PHONY: build up down logs run-local deploy-prod rollback deploy-worker logs-analysis

build:
	docker-compose -f infra/docker-compose.yml build

up:
	docker-compose -f infra/docker-compose.yml up -d

down:
	docker-compose -f infra/docker-compose.yml down

logs:
	docker-compose -f infra/docker-compose.yml logs -f ingestion-service

run-local:
	./scripts/local_run.sh

deploy-prod:
	./scripts/prod_deploy.sh

rollback:
	./scripts/rollback.sh

deploy-worker:
	./scripts/deploy_worker.sh $(SERVICE)

logs-analysis:
	docker-compose -f infra/docker-compose.yml logs -f analysis-service
