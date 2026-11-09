"""Install only checksum-pinned official standalone review tools; no source upload."""

import argparse
import hashlib
import http.client
import io
import json
import os
import platform
import signal
import socket
import tarfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

DOWNLOAD_BUDGET_SECONDS = 90.0
SOCKET_TIMEOUT_SECONDS = 30.0
DOWNLOAD_ATTEMPTS = 3
BACKOFF_SECONDS = (1.0, 2.0)
MAX_ARCHIVE_BYTES = 100 * 1024 * 1024
TOOL_NAMES = frozenset({"reviewdog", "zizmor", "gitleaks", "actionlint"})


class DownloadBudgetExpired(Exception):
    pass


def download_notice(name, phase, attempt, category=None):
    # No URL, response body, header, exception text or arbitrary tool name.
    record = {
        "tool": name if name in TOOL_NAMES else "unknown",
        "phase": phase,
        "attempt": attempt,
    }
    if category is not None:
        record["category"] = category
    print(json.dumps(record, sort_keys=True), flush=True)


def remaining_budget(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise DownloadBudgetExpired()
    return remaining


def transport_failure(error):
    if isinstance(error, urllib.error.HTTPError):
        if error.code in {408, 429, 500, 502, 503, 504}:
            return "http_transient", True
        return "http_terminal", False
    reason = error.reason if isinstance(error, urllib.error.URLError) else error
    if isinstance(reason, TimeoutError):
        return "timeout", True
    if isinstance(reason, (ConnectionError, http.client.IncompleteRead)):
        return "connection", True
    if isinstance(reason, socket.gaierror) and reason.errno == socket.EAI_AGAIN:
        return "dns_temporary", True
    return "transport_terminal", False


def download(name, url):
    if not url.startswith("https://github.com/"):
        raise ValueError("Review tools must come from a pinned official GitHub release")
    # These locked tools support Unix CLI execution. Do not replace a caller's
    # active deadline or install a process-wide signal handler from a thread.
    if (
        platform.system() not in {"Linux", "Darwin"}
        or threading.current_thread() is not threading.main_thread()
        or not hasattr(signal, "setitimer")
    ):
        raise ValueError("Review tool download deadline unavailable")
    if any(signal.getitimer(signal.ITIMER_REAL)):
        raise ValueError("Review tool download deadline already active")
    previous_handler = signal.getsignal(signal.SIGALRM)
    deadline = time.monotonic() + DOWNLOAD_BUDGET_SECONDS
    attempt = 0

    def expired(_signum, _frame):
        raise DownloadBudgetExpired()

    signal.signal(signal.SIGALRM, expired)
    try:
        # Unlike a socket timeout, this bounds redirects, body reads and backoff
        # together. Never pass zero to setitimer as a purported deadline.
        signal.setitimer(signal.ITIMER_REAL, remaining_budget(deadline))
        for attempt in range(1, DOWNLOAD_ATTEMPTS + 1):
            timeout = min(SOCKET_TIMEOUT_SECONDS, remaining_budget(deadline))
            download_notice(name, "request", attempt)
            try:
                with urllib.request.urlopen(url, timeout=timeout) as response:
                    download_notice(name, "body", attempt)
                    data = response.read(MAX_ARCHIVE_BYTES + 1)
            except (urllib.error.URLError, OSError, http.client.HTTPException) as error:
                category, retryable = transport_failure(error)
                if isinstance(error, urllib.error.HTTPError):
                    error.close()
                download_notice(name, "failure", attempt, category)
                remaining_budget(deadline)
                if not retryable or attempt == DOWNLOAD_ATTEMPTS:
                    raise ValueError(
                        "Review tool download failed: " + category
                    ) from None
                pause = BACKOFF_SECONDS[attempt - 1]
                if remaining_budget(deadline) <= pause:
                    raise DownloadBudgetExpired()
                download_notice(name, "backoff", attempt, category)
                time.sleep(pause)
            else:
                remaining_budget(deadline)
                download_notice(name, "downloaded", attempt)
                return data
    except DownloadBudgetExpired:
        download_notice(name, "failure", attempt, "budget_exhausted")
        raise ValueError("Review tool download budget exhausted") from None
    finally:
        try:
            signal.setitimer(signal.ITIMER_REAL, 0)
        finally:
            signal.signal(signal.SIGALRM, previous_handler)


def install(target, names=None):
    system = platform.system().lower()
    machine = {"x86_64": "amd64", "aarch64": "arm64"}.get(
        platform.machine(), platform.machine()
    )
    key = system + "-" + machine
    lock = json.loads((ROOT / "scripts/ci/tools.lock.json").read_text())
    target.mkdir(parents=True, exist_ok=True)
    for name in names or lock:
        item = lock[name]["assets"].get(key)
        if item is None:
            raise ValueError("Unsupported review-tool platform: " + key)
        destination = target / name
        if (
            destination.is_file()
            and hashlib.sha256(destination.read_bytes()).hexdigest()
            == item["binary_sha256"]
        ):
            continue
        data = download(name, item["url"])
        if (
            len(data) > MAX_ARCHIVE_BYTES
            or hashlib.sha256(data).hexdigest() != item["sha256"]
        ):
            raise ValueError("Review tool checksum/size verification failed: " + name)
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
            members = [
                m
                for m in archive.getmembers()
                if m.isfile() and Path(m.name).name == name
            ]
            if len(members) != 1 or members[0].size > MAX_ARCHIVE_BYTES:
                raise ValueError("Unexpected review tool archive")
            binary = archive.extractfile(members[0]).read()
        if hashlib.sha256(binary).hexdigest() != item["binary_sha256"]:
            raise ValueError("Review tool binary integrity failed")
        temporary = target / (name + ".tmp")
        temporary.write_bytes(binary)
        temporary.chmod(0o755)
        temporary.replace(destination)
    return target


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--directory",
        type=Path,
        default=Path(os.getenv("RUNNER_TEMP", "/private/tmp")) / "onesa-review-tools",
    )
    parser.add_argument("--only", nargs="+")
    args = parser.parse_args()
    target = install(args.directory, args.only)
    if os.getenv("GITHUB_PATH"):
        with open(os.environ["GITHUB_PATH"], "a") as output:
            output.write(str(target.resolve()) + "\n")
    print(
        "Checksum-verified review tools:",
        ", ".join(args.only or ["reviewdog", "zizmor", "gitleaks", "actionlint"]),
    )


if __name__ == "__main__":
    main()
