IMAGE ?= gyrox/solvers:local
TEST_IMAGE ?= gyrox/solvers:test-local
CI_TEST_IMAGE ?= gyrox/solvers:test-ci-local
JUNIT_OUT ?= $(CURDIR)/m1/s4-campaign/make-test-report.xml
CI_JUNIT_OUT ?= $(CURDIR)/m1/s4-campaign/make-test-ci-report.xml
CONTRACT_MOUNT = $(if $(GYROX_CONTRACT_ROOT),-v $(abspath $(GYROX_CONTRACT_ROOT)):/opt/contract-e1:ro -e GYROX_CONTRACT_ROOT=/opt/contract-e1,)

.PHONY: build require-gyrox-root test test-ci

build:
	docker build --target runtime -t $(IMAGE) .

require-gyrox-root:
	@test -n "$(strip $(GYROX_ROOT))" || { echo "GYROX_ROOT is required (absolute path to the Gyrox source worktree)" >&2; exit 2; }
	@test -d "$(abspath $(GYROX_ROOT))" || { echo "GYROX_ROOT is not a directory: $(abspath $(GYROX_ROOT))" >&2; exit 2; }

test: require-gyrox-root
	@mkdir -p $(dir $(JUNIT_OUT))
	@set -eu; \
		runtime_id=$$(docker image inspect "$(IMAGE)" --format '{{.Id}}'); \
		runtime_ref=gyrox/solvers:frozen-$${runtime_id#sha256:}; \
		docker image tag "$$runtime_id" "$$runtime_ref"; \
		docker build --build-arg "BASE_IMAGE=$(IMAGE)" --build-context "$(IMAGE)=docker-image://$$runtime_ref" --build-context "gyrox=$(abspath $(GYROX_ROOT))" --target test -t $(TEST_IMAGE) .
	docker run --rm $(CONTRACT_MOUNT) -v $(dir $(JUNIT_OUT)):/out $(TEST_IMAGE) pytest /opt/solvers/tests --junitxml=/out/$(notdir $(JUNIT_OUT))

test-ci:
	@mkdir -p $(dir $(CI_JUNIT_OUT))
	docker build --target test-ci -t $(CI_TEST_IMAGE) .
	docker run --rm $(CONTRACT_MOUNT) -v $(dir $(CI_JUNIT_OUT)):/out $(CI_TEST_IMAGE) pytest /opt/solvers/tests -m "not m0_assets and not realtime_cancel" --junitxml=/out/$(notdir $(CI_JUNIT_OUT))
