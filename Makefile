IMAGE ?= gyrox/solvers:local
TEST_IMAGE ?= gyrox/solvers:test-local
CI_TEST_IMAGE ?= gyrox/solvers:test-ci-local
JUNIT_OUT ?= $(CURDIR)/m1/s4-campaign/make-test-report.xml
CI_JUNIT_OUT ?= $(CURDIR)/m1/s4-campaign/make-test-ci-report.xml
CONTRACT_MOUNT = $(if $(GYROX_CONTRACT_ROOT),-v $(abspath $(GYROX_CONTRACT_ROOT)):/opt/contract-e1:ro -e GYROX_CONTRACT_ROOT=/opt/contract-e1,)

.PHONY: build test test-ci

build:
	docker build --target runtime -t $(IMAGE) .

test:
	@mkdir -p $(dir $(JUNIT_OUT))
	docker build --build-context gyrox=../gyrox --target test -t $(TEST_IMAGE) .
	docker run --rm $(CONTRACT_MOUNT) -v $(dir $(JUNIT_OUT)):/out $(TEST_IMAGE) pytest /opt/solvers/tests --junitxml=/out/$(notdir $(JUNIT_OUT))

test-ci:
	@mkdir -p $(dir $(CI_JUNIT_OUT))
	docker build --target test-ci -t $(CI_TEST_IMAGE) .
	docker run --rm $(CONTRACT_MOUNT) -v $(dir $(CI_JUNIT_OUT)):/out $(CI_TEST_IMAGE) pytest /opt/solvers/tests -m "not m0_assets" --junitxml=/out/$(notdir $(CI_JUNIT_OUT))
