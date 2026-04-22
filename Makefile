# SwarmEvolve - top-level Makefile
# See IMPLEMENTATION_PLAN.md for milestone ordering.

# -------- toolchain --------
# On macOS, the stock Command Line Tools sometimes ship without the libc++
# headers that `<cstdint>`, `<cstdio>` resolve to. If you hit "file not found"
# errors, install Homebrew LLVM and run:
#   make CXX_MACOS=/opt/homebrew/opt/llvm/bin/clang++ ...
CXX_MACOS  ?= clang++
CXX_LINUX  ?= g++
CXX_GPU    ?= nvc++
PYTHON     ?= python3

# -------- flags --------
# -Wno-unknown-pragmas: `#pragma acc routine seq` is legal under OpenACC
# (nvc++) and mandated by spec to be silently ignored by other compilers.
# Apple clang already does; g++ warns, and -Werror would promote to failure.
# nvc++ does not accept -Wno-unknown-pragmas (it natively understands `acc`
# pragmas and never warns on them) so CXXFLAGS_GPU omits it.
CXXFLAGS_COMMON = -std=c++17 -O2 -Wall -Wextra -Wshadow -Wpedantic -Werror -Wno-unknown-pragmas -Isrc
CXXFLAGS_GPU    = -std=c++17 -O2 -Wall -Wextra -Wshadow -Wpedantic -Werror -Isrc
# Tests default to -O0 -g only. Sanitizers are opt-in via SANITIZE=1 because
# the Homebrew-LLVM ASan runtime on macOS can hang under SIP+dyld restrictions;
# Linux CI sets SANITIZE=1 to exercise the sanitized build.
CXXFLAGS_DEBUG  = -std=c++17 -O0 -g -Wall -Wextra -Wshadow -Wpedantic -Werror -Wno-unknown-pragmas -Isrc
ifeq ($(SANITIZE),1)
  CXXFLAGS_DEBUG += -fsanitize=address,undefined -fno-omit-frame-pointer
endif
ACCFLAGS        = -acc=gpu -gpu=mem:managed -Minfo=accel

# -------- sources --------
SRC_ENGINE   = src/engine.cpp
SRC_AI       = $(wildcard src/a/*.cpp) $(wildcard src/b/*.cpp)
SRC_ALL      = $(SRC_ENGINE) $(SRC_AI)

TARGET       = swarmevolve
BUILD_DIR    = build

# -------- phony targets --------
.PHONY: all help build-macos build-linux-cpu build-linux-gpu \
        test test-cpp test-python lint format clean run-demo visualize-demo \
        docker-build docker-test check doctor

all: help

help:
	@echo "SwarmEvolve Makefile targets:"
	@echo "  build-macos      - Build CPU binary with clang++ (macOS default)"
	@echo "  build-linux-cpu  - Build CPU binary with g++ (Linux)"
	@echo "  build-linux-gpu  - Build GPU binary with nvc++ + OpenACC"
	@echo "  test             - Run full test suite (C++ and Python)"
	@echo "  test-cpp         - Run C++ tests only"
	@echo "  test-python      - Run Python tests only"
	@echo "  lint             - Run ruff, mypy, clang-format check, and AI-token lint"
	@echo "  format           - Auto-format C++ and Python sources"
	@echo "  check            - lint + test (what CI runs)"
	@echo "  run-demo         - Build and run a demo match producing a trace"
	@echo "  visualize-demo   - Render data/traces/demo.jsonl to data/videos/demo.mp4"
	@echo "  docker-build     - Build the sandbox container image (M8)"
	@echo "  docker-test      - Run sandbox test suite (M8)"
	@echo "  clean            - Remove build artifacts and transient data"
	@echo "  doctor           - Print toolchain versions"

# -------- build --------
build-macos: $(BUILD_DIR)
	$(CXX_MACOS) $(CXXFLAGS_COMMON) $(SRC_ALL) -o $(TARGET)

build-linux-cpu: $(BUILD_DIR)
	$(CXX_LINUX) $(CXXFLAGS_COMMON) $(SRC_ALL) -o $(TARGET)

build-linux-gpu: $(BUILD_DIR)
	$(CXX_GPU) $(CXXFLAGS_GPU) $(ACCFLAGS) $(SRC_ALL) -o $(TARGET)

$(BUILD_DIR):
	@mkdir -p $(BUILD_DIR)

# -------- tests --------
# C++ tests: compile any tests/test_*.cpp present and run each.
TEST_CPP_SRCS := $(wildcard tests/test_*.cpp)
TEST_CPP_BINS := $(patsubst tests/%.cpp,$(BUILD_DIR)/%,$(TEST_CPP_SRCS))

$(BUILD_DIR)/%: tests/%.cpp | $(BUILD_DIR)
	$(CXX_MACOS) $(CXXFLAGS_DEBUG) $< -o $@

test-cpp: $(TEST_CPP_BINS)
	@set -e; for t in $(TEST_CPP_BINS); do echo "[test-cpp] $$t"; $$t; done
	@[ -n "$(TEST_CPP_BINS)" ] || echo "[test-cpp] (no C++ tests yet)"

test-python:
	$(PYTHON) -m pytest

test: test-cpp test-python

# -------- lint / format --------
lint:
	$(PYTHON) -m ruff check .
	$(PYTHON) -m mypy
	@echo "[lint] clang-format check"
	@if command -v clang-format >/dev/null 2>&1; then \
	  find src tests -name '*.cpp' -o -name '*.h' -o -name '*.hpp' 2>/dev/null | \
	    xargs -r clang-format --dry-run -Werror; \
	else \
	  echo "clang-format not installed, skipping"; \
	fi
	@echo "[lint] AI-token scan"
	@AI_FILES=$$(find src/a src/b -name '*.cpp' -o -name '*.h' 2>/dev/null); \
	  if [ -n "$$AI_FILES" ]; then $(PYTHON) scripts/lint_ai_tokens.py $$AI_FILES; \
	  else echo "  (no AI source yet)"; fi

format:
	$(PYTHON) -m ruff format .
	@if command -v clang-format >/dev/null 2>&1; then \
	  find src tests -name '*.cpp' -o -name '*.h' -o -name '*.hpp' 2>/dev/null | \
	    xargs -r clang-format -i; \
	else \
	  echo "clang-format not installed, skipping"; \
	fi

check: lint test

# -------- demo --------
run-demo: build-macos
	@mkdir -p data/traces
	./$(TARGET) --record data/traces/demo.jsonl --seed 42
	@echo "Trace written to data/traces/demo.jsonl"

visualize-demo:
	@test -f data/traces/demo.jsonl || (echo "data/traces/demo.jsonl missing (run 'make run-demo' first)"; exit 1)
	@mkdir -p data/videos
	$(PYTHON) scripts/visualizer.py data/traces/demo.jsonl data/videos/demo.mp4
	@echo "Video written to data/videos/demo.mp4"

# -------- docker (M8 sandbox) --------
SANDBOX_IMAGE ?= swarmevolve-sandbox:latest

docker-build:
	@test -f docker/Dockerfile.sandbox || (echo "docker/Dockerfile.sandbox missing"; exit 1)
	docker build -f docker/Dockerfile.sandbox -t $(SANDBOX_IMAGE) .

# Run only the sandbox test modules; other Python tests stay fast-path.
docker-test:
	SANDBOX_IMAGE=$(SANDBOX_IMAGE) $(PYTHON) -m pytest -v \
	    tests/test_sandbox_ok.py tests/test_sandbox_escape.py

# -------- maintenance --------
clean:
	rm -rf $(BUILD_DIR) $(TARGET)
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
	rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage

doctor:
	@echo "=== Toolchain ==="
	@echo "CXX_MACOS : $(CXX_MACOS) $$($(CXX_MACOS) --version 2>/dev/null | head -n1 || echo MISSING)"
	@echo "CXX_LINUX : $(CXX_LINUX) $$($(CXX_LINUX) --version 2>/dev/null | head -n1 || echo MISSING)"
	@echo "CXX_GPU   : $(CXX_GPU) $$($(CXX_GPU) --version 2>/dev/null | head -n1 || echo MISSING)"
	@echo "PYTHON    : $(PYTHON) $$($(PYTHON) --version 2>/dev/null || echo MISSING)"
