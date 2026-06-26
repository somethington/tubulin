"""Fork-server worker — amortize the heavy per-job import.

Normally the runtime spawns ``python -m kinesin.worker`` per job, and each process
pays the full cold import of the user's module (polars/scipy/matplotlib, ~1-3s). For
a parallel job that is paid by *every* shard.

The fork-server imports the user's module **once** in a resident parent, then
``fork()``s a fresh child per job. The child inherits the warm import via
copy-on-write, runs exactly one job, and exits — so memory is still reclaimed by
process exit and **no worker is ever reused** (this is *not* a warm worker pool: the
resident parent never runs job code). Only the import is amortized.

Protocol (one connection == one job == one child): a client connects to the Unix
socket, sends one job descriptor as a JSON line then half-closes its write side, and
reads the worker protocol lines (``log``/``progress``/``completed``/``failed``) back
on the same connection until EOF.

Fork-without-exec is a Linux strength and a macOS hazard — native thread pools in
polars/numpy can deadlock a forked child, and Apple's frameworks are fork-hostile.
This is a server-side (Linux) optimization; callers should fall back to the plain
worker where fork is unavailable or unsafe.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import traceback

from . import protocol
from .worker import import_user_module, run_job_descriptor

READY = "KINESIN_FORKSERVER_READY"


def fork_supported() -> bool:
    """Whether the fork-server can be used safely on this platform.

    Requires POSIX ``os.fork`` and is gated **on** only for Linux by default: on
    macOS, scipy/numpy LAPACK calls go through Apple's Accelerate framework, which
    segfaults in a forked child regardless of thread settings (a documented platform
    limitation — it's why CPython's macOS multiprocessing default is ``spawn``).
    Linux with OpenBLAS (forced single-threaded here) is fork-safe.

    Override with ``KINESIN_FORKSERVER=1`` (force on, e.g. for a fork-safe app on
    macOS) or ``KINESIN_FORKSERVER=0`` (force off). Otherwise callers should fall back
    to the plain ``kinesin.worker`` when this returns False.
    """
    flag = os.environ.get("KINESIN_FORKSERVER")
    if flag == "0" or not hasattr(os, "fork"):
        return False
    if flag == "1":
        return True
    return sys.platform.startswith("linux")


def serve(module_file: str, socket_path: str) -> None:
    """Import ``module_file`` once, then fork a child per job arriving on the socket."""
    # Must be set before the fork-hostile frameworks import (best-effort on macOS).
    os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")
    # Force native libs single-threaded so there is no background thread pool to
    # corrupt across fork() (polars/rayon, numpy/OpenBLAS, OpenMP). A forked child
    # that touches a pool created in the parent segfaults otherwise. Parallelism is
    # preserved at the *process* level (one child per shard); only intra-process
    # native threading is disabled, which our per-shard work doesn't need.
    for var in ("POLARS_MAX_THREADS", "RAYON_NUM_THREADS", "OMP_NUM_THREADS",
                "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ.setdefault(var, "1")

    module = import_user_module(module_file)  # the one warm import

    if os.path.exists(socket_path):
        os.unlink(socket_path)
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(socket_path)
    server.listen(128)

    # Readiness signal: the import is done and the socket is accepting.
    sys.stdout.write(READY + "\n")
    sys.stdout.flush()

    try:
        while True:
            _reap()
            try:
                conn, _ = server.accept()
            except (KeyboardInterrupt, OSError):
                break
            pid = os.fork()
            if pid == 0:  # child: owns the connection, runs one job, exits
                server.close()
                try:
                    _run_child(conn, module)
                finally:
                    os._exit(0)
            conn.close()  # parent: the child owns it now
    finally:
        server.close()
        if os.path.exists(socket_path):
            os.unlink(socket_path)


def _run_child(conn: socket.socket, module) -> None:
    rfile = conn.makefile("r")
    wfile = conn.makefile("w")
    protocol._OUT = wfile  # protocol lines now stream to the client over the socket
    sys.stdout = sys.stderr  # keep any user prints off the protocol channel
    try:
        descriptor = json.loads(rfile.readline())
        run_job_descriptor(descriptor, module=module)
    except Exception:
        try:
            protocol.failed(traceback.format_exc())
        except Exception:
            pass
    finally:
        try:
            wfile.flush()
        except Exception:
            pass
        try:
            conn.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        conn.close()


def _reap() -> None:
    """Non-blocking reap of any finished children (no zombies)."""
    try:
        while True:
            pid, _ = os.waitpid(-1, os.WNOHANG)
            if pid == 0:
                break
    except ChildProcessError:
        pass


def request(socket_path: str, descriptor: dict, timeout: float = 300) -> list:
    """Client: submit one descriptor, return the list of protocol messages.

    Mirrors what the Rust runtime will do — included here so the fork-server is usable
    (and benchmarkable) from Python on its own. ``timeout`` guards against a forked
    child that deadlocks (a real risk on macOS) instead of hanging forever.
    """
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    sock.connect(socket_path)
    sock.sendall((json.dumps(descriptor) + "\n").encode())
    sock.shutdown(socket.SHUT_WR)
    messages = []
    with sock.makefile("r") as fh:
        for line in fh:
            line = line.strip()
            if line:
                messages.append(json.loads(line))
    sock.close()
    return messages


def main(argv=None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) < 2:
        sys.stderr.write("usage: python -m kinesin.forkserver <module_file> <socket_path>\n")
        raise SystemExit(2)
    serve(argv[0], argv[1])


if __name__ == "__main__":
    main()
