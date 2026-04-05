PYTHON ?= python3

.PHONY: install test coverage lint build-firmware build-config

install:
	./build install --python $(PYTHON)

test:
	./build test --python $(PYTHON)

coverage:
	./build coverage --python $(PYTHON)

lint:
	./build lint --python $(PYTHON)

build-config:
	./build render-config --python $(PYTHON)

build-firmware:
	./build build-firmware --python $(PYTHON)
