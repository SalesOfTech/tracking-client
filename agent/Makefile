ifeq ($(OS),Windows_NT)
  PYTHON ?= python
else
  PYTHON ?= python3
endif

.PHONY: test deps build

test:
	$(PYTHON) -m unittest discover -s tests -v

deps:
	$(PYTHON) -m pip install -r requirements-build.txt

# VERSION, SIGNING_KEY and KEY_ID must be supplied by the release operator.
build:
	$(PYTHON) ../tools/release.py build --version "$(VERSION)" --key "$(SIGNING_KEY)" --key-id "$(KEY_ID)" --profile "$(or $(PROFILE),modern)"
