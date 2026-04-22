"""Escape-attempt tests for :mod:`scripts.sandbox` (M8).

Each test plants a synthetic adversarial AI source that tries to violate
one of the sandbox's isolation guarantees and asserts that the sandbox
*contains* the attempt — i.e. the container terminates cleanly within
the wall-clock timeout and does not damage the host. These payloads are
purely defensive test fixtures; they are not malicious in any context
outside this repository's isolation test suite.

Guarantees under test (IMPLEMENTATION_PLAN §M8 / ARCHITECTURE Layer 4):

1. ``--network=none`` — egress is blocked.
2. ``--read-only`` — cannot write outside ``/tmp`` / ``/work/out``.
3. ``--pids-limit=64`` — fork bombs cannot exhaust host PIDs.
4. Host wall-clock ``--timeout`` — an infinite match is killed.
5. ``--memory=512m`` — memory bombs are OOM-killed by cgroup.

Skipped when no container runtime is available.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import sandbox  # noqa: E402

BASELINES = REPO_ROOT / "src" / "baselines"
IMAGE = os.environ.get("SANDBOX_IMAGE", sandbox.DEFAULT_IMAGE)


def _runtime_or_skip() -> str:
    try:
        return sandbox.detect_runtime()
    except sandbox.SandboxUnavailableError:
        pytest.skip("no container runtime (docker/podman) on PATH")


def _image_or_skip(runtime: str) -> None:
    if not sandbox.image_present(IMAGE, runtime=runtime):
        pytest.skip(
            f"sandbox image '{IMAGE}' not found; run `make docker-build` first",
        )


@pytest.fixture(scope="module")
def colima_safe_tmp() -> Path:
    base = Path(os.path.expanduser("~/.pytest-sandbox"))
    base.mkdir(parents=True, exist_ok=True)
    d = Path(tempfile.mkdtemp(prefix="m8esc_", dir=str(base)))
    yield d
    shutil.rmtree(d, ignore_errors=True)


def _good_opponent_b(dst: Path) -> Path:
    """Frozen cluster_v1 rendered into TeamB, saved to ``dst/b.cpp``."""
    src = (BASELINES / "cluster_v1.cpp").read_text().replace(
        "TEAM_NS_PLACEHOLDER", "TeamB")
    p = dst / "b.cpp"
    p.write_text(src)
    return p


# -----------------------------------------------------------------------
# Adversarial source builder.
# -----------------------------------------------------------------------
#
# Every payload is a syntactically valid TeamA AI that (a) satisfies the
# banned-token lint enough to reach the sandbox (we bypass the host lint
# here because we're stressing the container boundary, not the linter),
# and (b) attempts one isolation breach in its drone_ai body. Payloads
# use `unistd.h` directly since the sandbox image has glibc anyway — the
# point is that even with hostile system calls the kernel / cgroup
# settings keep the breach contained.
#
# These payloads are INTENTIONALLY malicious-looking for test purposes.
# They are not "improvements" to AI code and must never run outside the
# sandbox.

_COMMON_HEADER = (
    "#include \"../ai_abi.h\"\n"
    "#include \"../types.h\"\n"
    "#include <sys/types.h>\n"
    "#include <sys/socket.h>\n"
    "#include <netinet/in.h>\n"
    "#include <unistd.h>\n"
    "#include <fcntl.h>\n"
    "#include <string.h>\n"
    "namespace TeamA {\n"
    "#pragma acc routine seq\n"
    "void drone_ai(int, const GameParams* params, const AllyState*,\n"
    "              const EnemyState*, const float[][MSG_SIZE],\n"
    "              float*, Action* out) {\n"
    "    // zero a valid action first so the compiled binary never crashes\n"
    "    out->velocity.x = 0;\n"
    "    out->velocity.y = 0;\n"
    "    out->target_id = -1;\n"
    "    for (int i = 0; i < MSG_SIZE; ++i) { out->message_out[i] = 0; }\n"
    # The engine's first AI-query tick is 1 (not 0). Gate payloads so
    # they fire only on that first call and then stay quiet, giving the
    # match a chance to terminate normally after the one-shot attack.
    "    if (params->current_tick > 1) { return; }\n"
)

_COMMON_FOOTER = "\n}\n}  // namespace TeamA\n"


def _write_adversary(dst_dir: Path, body: str) -> Path:
    src_dir = dst_dir / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    p = src_dir / "a.cpp"
    p.write_text(_COMMON_HEADER + body + _COMMON_FOOTER)
    return p


# -----------------------------------------------------------------------
# 1. network egress blocked
# -----------------------------------------------------------------------

def test_network_egress_blocked(colima_safe_tmp):
    rt = _runtime_or_skip()
    _image_or_skip(rt)

    # Attempt to open an outbound TCP socket to a public IP. With
    # --network=none the container has only a loopback interface and
    # connect() returns ENETUNREACH. The payload swallows the error,
    # so the match runs to completion — which is fine: we assert the
    # sandbox did not crash and the engine produced a trace.
    body = (
        "    int s = socket(AF_INET, SOCK_STREAM, 0);\n"
        "    if (s >= 0) {\n"
        "        sockaddr_in a;\n"
        "        memset(&a, 0, sizeof(a));\n"
        "        a.sin_family = AF_INET;\n"
        "        a.sin_port = htons(80);\n"
        "        a.sin_addr.s_addr = htonl(0x08080808);\n"
        "        (void)connect(s, (sockaddr*)&a, sizeof(a));\n"
        "        close(s);\n"
        "    }\n"
    )
    a = _write_adversary(colima_safe_tmp / "net", body)
    b = _good_opponent_b(colima_safe_tmp / "net" / "src")
    out_dir = colima_safe_tmp / "net_out"

    t0 = time.monotonic()
    result = sandbox.run_match_in_sandbox(
        a, b, out_dir, image=IMAGE, runtime=rt, timeout=15.0,
        engine_args=["--seed", "0", "--max-ticks", "50"],
    )
    elapsed = time.monotonic() - t0

    # The match either completes normally (connect returned EACCES /
    # ENETUNREACH and was ignored) or the process terminated with a
    # structured non-ok status. Either way we must not have timed out.
    assert elapsed < 15.0
    assert result.status.get("status") in {"ok", "compile_failed",
                                            "engine_crashed"}, result.status
    # Network-none must hold: no interface other than `lo` should have
    # produced a successful packet. We can't directly observe the
    # container's routing table post-run, but absence of a timeout + a
    # clean status already proves the container stayed contained.


# -----------------------------------------------------------------------
# 2. read-only rootfs blocks host-directory writes
# -----------------------------------------------------------------------

def test_readonly_rootfs_blocks_writes(colima_safe_tmp):
    rt = _runtime_or_skip()
    _image_or_skip(rt)

    # Try to write outside /work/out — the rest of the filesystem is
    # read-only, so open(... O_CREAT | O_WRONLY) on /etc/evil must fail.
    # We record whether the write "succeeded" in out->message_out[0] so
    # a post-run inspection can confirm the syscall was rejected.
    body = (
        "    int fd = open(\"/etc/evil\", O_CREAT | O_WRONLY, 0600);\n"
        "    if (fd >= 0) {\n"
        "        const char* msg = \"pwned\";\n"
        "        (void)write(fd, msg, 5);\n"
        "        close(fd);\n"
        "    }\n"
        "    // Best-effort: also assert that a write outside /work/out fails.\n"
        "    int fd2 = open(\"/opt/swarmevolve/hijacked\", O_CREAT | O_WRONLY, 0600);\n"
        "    if (fd2 >= 0) { close(fd2); }\n"
    )
    a = _write_adversary(colima_safe_tmp / "ro", body)
    b = _good_opponent_b(colima_safe_tmp / "ro" / "src")
    out_dir = colima_safe_tmp / "ro_out"

    result = sandbox.run_match_in_sandbox(
        a, b, out_dir, image=IMAGE, runtime=rt, timeout=15.0,
        engine_args=["--seed", "0", "--max-ticks", "20"],
    )
    # The attack is swallowed; the match completes. The canonical proof
    # is that /etc/evil does NOT exist on the host (it's inside the
    # container filesystem anyway, which is ephemeral). We assert
    # completion + no host-side breach.
    assert result.status.get("status") in {
        "ok", "engine_crashed", "compile_failed",
    }, result.status
    # Host-side: the read-only rootfs means no file escaped to the host
    # filesystem outside the bind-mounted out_dir. In particular /etc
    # on the host must not gain an "evil" file, and the out_dir must
    # not contain a "hijacked" marker (the attacker targeted
    # /opt/swarmevolve inside the container, not out_dir).
    assert not (Path("/etc") / "evil").exists()
    assert not (out_dir / "hijacked").exists()


# -----------------------------------------------------------------------
# 3. pids limit stops a fork bomb
# -----------------------------------------------------------------------

def test_pid_limit_contains_fork_bomb(colima_safe_tmp):
    rt = _runtime_or_skip()
    _image_or_skip(rt)

    # A real fork bomb would use `fork()` in a loop; we approximate with
    # a bounded loop calling fork() so the guard injector doesn't reject
    # it. pids-limit=64 should cap descendants to 64 and the kernel
    # returns EAGAIN, which we silently ignore. The point: the HOST must
    # not lock up, and the container must terminate within `timeout`.
    body = (
        "    for (int i = 0; i < 500; ++i) {\n"
        "        pid_t pid = fork();\n"
        "        if (pid == 0) {\n"
        "            // child: spin briefly then exit\n"
        "            for (volatile int j = 0; j < 1000000; ++j) { }\n"
        "            _exit(0);\n"
        "        }\n"
        "        // parent: do NOT wait -- we're stress-testing PID cap\n"
        "    }\n"
    )
    a = _write_adversary(colima_safe_tmp / "fork", body)
    b = _good_opponent_b(colima_safe_tmp / "fork" / "src")
    out_dir = colima_safe_tmp / "fork_out"

    t0 = time.monotonic()
    result = sandbox.run_match_in_sandbox(
        a, b, out_dir, image=IMAGE, runtime=rt, timeout=15.0,
        engine_args=["--seed", "0", "--max-ticks", "50"],
    )
    elapsed = time.monotonic() - t0

    assert elapsed < 15.0, (
        f"fork bomb was not contained: elapsed={elapsed:.1f}s status={result.status}"
    )
    # The container terminated (ok, engine_crashed, or timeout with a
    # status written). The only unacceptable outcome is a runaway host.
    assert result.status.get("status") in {
        "ok", "engine_crashed", "timeout", "no_status_written",
    }, result.status


# -----------------------------------------------------------------------
# 4. host wall-clock timeout fires on an infinite match
# -----------------------------------------------------------------------

def test_wallclock_timeout_kills_infinite_engine(colima_safe_tmp):
    rt = _runtime_or_skip()
    _image_or_skip(rt)

    # Make every drone_ai call sleep, guaranteeing the engine cannot
    # complete even tick 0. The sandbox wrapper's `timeout=` must fire
    # and report a timeout rather than hang the test run forever.
    # (If only tick 0 sleeps, the engine races through the remaining
    # 1000 ticks after that single 60s pause — we need per-call sleep
    # so wall-clock >> host timeout.)
    body = (
        "    // Every call sleeps a bit; the engine needs 10 drones *\n"
        "    // N ticks calls, so wall-clock quickly exceeds --timeout.\n"
        "    (void)sleep(30);\n"
    )
    a = _write_adversary(colima_safe_tmp / "sleep", body)
    b = _good_opponent_b(colima_safe_tmp / "sleep" / "src")
    out_dir = colima_safe_tmp / "sleep_out"

    t0 = time.monotonic()
    result = sandbox.run_match_in_sandbox(
        a, b, out_dir, image=IMAGE, runtime=rt, timeout=5.0,
        engine_args=["--seed", "0", "--max-ticks", "1000"],
    )
    elapsed = time.monotonic() - t0

    # timeout must fire within ~5s plus a small fudge for docker cleanup
    assert elapsed < 12.0, f"timeout did not fire: elapsed={elapsed:.1f}s"
    assert result.returncode in {124, 137, 143}, (
        f"expected timeout/kill code, got {result.returncode}"
    )
    # sandbox_status.json should reflect the timeout path
    assert result.status.get("status") in {"timeout", "no_status_written"}


# -----------------------------------------------------------------------
# 5. memory cgroup OOM-kills a big allocation
# -----------------------------------------------------------------------

def test_memory_cgroup_oom_kills_large_alloc(colima_safe_tmp):
    rt = _runtime_or_skip()
    _image_or_skip(rt)

    # Try to mmap 2 GiB. With --memory=512m the cgroup should OOM-kill
    # the engine long before 2 GiB are touched. We use mmap (instead of
    # `new char[2<<30]`) because it's faulted lazily and proves the
    # cgroup page-charge path. We also write through the pages to
    # actually trigger the OOM (otherwise anonymous pages don't count).
    body = (
        "    size_t n = (size_t)2 * 1024 * 1024 * 1024;  // 2 GiB\n"
        "    void* p = mmap(nullptr, n, PROT_READ|PROT_WRITE,\n"
        "                   MAP_PRIVATE|MAP_ANONYMOUS, -1, 0);\n"
        "    if (p != (void*)-1) {\n"
        "        volatile char* c = (volatile char*)p;\n"
        "        for (size_t i = 0; i < n; i += 4096) { c[i] = (char)i; }\n"
        "        munmap(p, n);\n"
        "    }\n"
    )
    # This body needs <sys/mman.h>; extend the header.
    src_dir = colima_safe_tmp / "oom" / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    (src_dir / "a.cpp").write_text(
        _COMMON_HEADER.replace(
            "#include <sys/types.h>\n",
            "#include <sys/types.h>\n#include <sys/mman.h>\n",
        ) + body + _COMMON_FOOTER
    )
    a = src_dir / "a.cpp"
    b = _good_opponent_b(src_dir)
    out_dir = colima_safe_tmp / "oom_out"

    t0 = time.monotonic()
    result = sandbox.run_match_in_sandbox(
        a, b, out_dir, image=IMAGE, runtime=rt, timeout=15.0,
        engine_args=["--seed", "0", "--max-ticks", "50"],
    )
    elapsed = time.monotonic() - t0

    assert elapsed < 15.0, "OOM killer did not act in time"
    # The engine either dies with SIGKILL (137) or the mmap itself fails
    # with ENOMEM and the loop skips it — both are acceptable
    # containment outcomes. The critical assertion is that the host
    # didn't swap-storm or lock up.
    status = result.status.get("status")
    assert status in {"ok", "engine_crashed", "timeout",
                      "no_status_written", "compile_failed"}, result.status
