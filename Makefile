IMAGE ?= gyrox/solvers:local
JUNIT_OUT ?= $(CURDIR)/m1/s4-campaign/make-test-report.xml

.PHONY: build test

build:
	docker build --build-context gyrox=../gyrox --target runtime -t $(IMAGE) .

test:
	@mkdir -p $(dir $(JUNIT_OUT))
	docker run --rm -v $(dir $(JUNIT_OUT)):/out $(IMAGE) pytest /opt/solvers/tests --junitxml=/out/$(notdir $(JUNIT_OUT))
