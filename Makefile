# USDA gNATSGO -> Icechunk Zarr pipeline
#
# Store target: set ACCOUNT to commit directly into the Source Coop product, or
# leave it unset to use STORE (a local path / s3://bucket/prefix, default
# ./gnatsgo_store_local) - so a bare `make rasterize` never touches the
# published store.
#
#   make inspect-release
#   make extract
#   make intermediates
#   make init-store ACCOUNT=chill
#   make rasterize ACCOUNT=chill REGIONS=conus VARIABLES=mukey,aws
#   make status ACCOUNT=chill
#   make validate ACCOUNT=chill REGIONS=guam
#   make validate ACCOUNT=chill REGIONS=conus VALUE_SAMPLES=40 WORKERS=32
#   make release ACCOUNT=chill

STORE        ?= ./gnatsgo_store_local
ACCOUNT      ?=
SOURCE       ?= data
WORK_DIR     ?= work
REGIONS      ?=
VARIABLES    ?=
WORKERS      ?=
COMMIT_EVERY ?=
SAMPLES      ?= 8
WINDOW       ?=
VALUE_SAMPLES?=
SEED         ?=
CREDS_FILE   ?=
OVERWRITE    ?=
GC_HOURS     ?=

CLI = uv run usda-gnatsgo

ifeq ($(ACCOUNT),)
  STORE_FLAGS = --store $(STORE)
else
  STORE_FLAGS = --source-coop-account $(ACCOUNT)
endif
STORE_FLAGS += $(CREDS_FLAG)
REGIONS_FLAG   = $(if $(REGIONS),--regions $(REGIONS))
VARIABLES_FLAG = $(if $(VARIABLES),--variables $(VARIABLES))
WORKERS_FLAG   = $(if $(WORKERS),--workers $(WORKERS))
OVERWRITE_FLAG = $(if $(OVERWRITE),--overwrite)
COMMIT_FLAG    = $(if $(COMMIT_EVERY),--commit-every $(COMMIT_EVERY))
GC_HOURS_FLAG  = $(if $(GC_HOURS),--older-than-hours $(GC_HOURS))
WINDOW_FLAG    = $(if $(WINDOW),--window $(WINDOW))
VALUE_SAMPLES_FLAG = $(if $(VALUE_SAMPLES),--value-samples $(VALUE_SAMPLES))
SEED_FLAG      = $(if $(SEED),--seed $(SEED))
# only pass a creds file when explicitly requested; otherwise the source-coop
# CLI's cached login is used (a stale creds.json must not shadow a fresh login)
CREDS_FLAG     = $(if $(CREDS_FILE),--credentials-file $(CREDS_FILE))

.DEFAULT_GOAL := help

.PHONY: help setup test lint inspect-release extract intermediates init-store rasterize status \
	validate release info garbage-collect publish-readme upload-audit clean-local-store clean-work \
	clean-remote-store

help: ## Show this help
	@grep -E '^[a-zA-Z0-9_-]+:.*## ' $(MAKEFILE_LIST) | awk -F ':.*## ' '{printf "  \033[1m%-18s\033[0m %s\n", $$1, $$2}'
	@echo ""
	@echo "  Variables: STORE=$(STORE)  ACCOUNT=$(ACCOUNT)  SOURCE=$(SOURCE)"
	@echo "             REGIONS=$(REGIONS)  VARIABLES=$(VARIABLES)  WORKERS=$(WORKERS)"
	@echo "             COMMIT_EVERY=$(COMMIT_EVERY)  SAMPLES=$(SAMPLES)  WINDOW=$(WINDOW)"
	@echo "             VALUE_SAMPLES=$(VALUE_SAMPLES)  SEED=$(SEED)"

setup: ## Install dependencies (uv sync)
	uv sync

test: ## Run the test suite
	uv run pytest -q

lint: ## Fix lint issues and reformat code with ruff
	uv run ruff check --fix src tests
	uv run ruff format src tests

inspect-release: ## Phase 1: verify the local release (grids, schema, manifest)
	$(CLI) inspect-release $(SOURCE) --work-dir $(WORK_DIR)

extract: ## Phase 2: stream required GeoPackage tables to Parquet
	$(CLI) extract $(SOURCE) --work-dir $(WORK_DIR)

intermediates: ## Phase 3: derive valu1/horizon/map-unit intermediates + gates
	$(CLI) build-intermediates $(SOURCE) --work-dir $(WORK_DIR)

init-store: ## Phase 4: create (or additively extend) the store structure
	$(CLI) init-store $(STORE_FLAGS) $(REGIONS_FLAG) --work-dir $(WORK_DIR)

rasterize: ## Phase 5: fill (region, variable) pairs; one commit each, checkpointed; resumable
	$(CLI) rasterize $(SOURCE) $(STORE_FLAGS) $(REGIONS_FLAG) $(VARIABLES_FLAG) \
		--work-dir $(WORK_DIR) $(WORKERS_FLAG) $(COMMIT_FLAG) $(OVERWRITE_FLAG)

status: ## Show the region x variable completion matrix
	$(CLI) status $(STORE_FLAGS)

validate: ## Phase 6: verify store structure and sampled contents
	$(CLI) validate $(SOURCE) $(STORE_FLAGS) $(REGIONS_FLAG) --work-dir $(WORK_DIR) --samples $(SAMPLES) \
		$(WINDOW_FLAG) $(VALUE_SAMPLES_FLAG) $(WORKERS_FLAG) $(SEED_FLAG)

release: ## Phase 7: tag the release (refuses while pairs are missing)
	$(CLI) release $(STORE_FLAGS)

info: ## Show store structure, tags, and recent snapshots
	$(CLI) info $(STORE_FLAGS)

garbage-collect: ## Reclaim objects orphaned by checkpoint amends (never while rasterizing)
	$(CLI) garbage-collect $(STORE_FLAGS) $(GC_HOURS_FLAG)

publish-readme: ## Upload product/README.md as the Source Coop landing page
	$(CLI) publish-readme --source-coop-account $(or $(ACCOUNT),chill) $(CREDS_FLAG)

upload-audit: ## Upload reports + diagnostics Parquet to audit/{release}/
	$(CLI) upload-audit --source-coop-account $(or $(ACCOUNT),chill) $(CREDS_FLAG) --work-dir $(WORK_DIR)

clean-local-store: ## Remove the local icechunk store ($(STORE))
	rm -rf $(STORE)

clean-work: ## Remove Parquet intermediates and reports ($(WORK_DIR))
	rm -rf $(WORK_DIR)

clean-remote-store: ## DESTRUCTIVE: delete the published store (data, history, tags); confirms twice
	$(CLI) clean-remote-store --source-coop-account $(or $(ACCOUNT),chill) $(CREDS_FLAG) $(WORKERS_FLAG)
