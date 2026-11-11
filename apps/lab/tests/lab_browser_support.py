"""Portable process observations used by browser acceptance tests."""
import json
from queue import Queue, Empty
from threading import Thread

import psutil


def identities(pid):
    """Observe only QEMU children belonging to this test's backend."""
    result = []
    try:
        children = psutil.Process(pid).children(recursive=True)
    except psutil.NoSuchProcess:
        return result
    for child in children:
        try:
            if "-machine" in child.cmdline():
                result.append((child.pid, child.create_time()))
        except psutil.NoSuchProcess:
            continue
    return sorted(result)


def identity_alive(identity):
    pid, created = identity
    try:
        return psutil.Process(pid).create_time() == created
    except psutil.NoSuchProcess:
        return False


def read_ready(process, timeout):
    output = Queue()
    Thread(target=lambda: output.put(process.stdout.readline()), daemon=True).start()
    try:
        line = output.get(timeout=timeout)
    except Empty as error:
        raise TimeoutError("backend readiness deadline expired") from error
    if not line:
        raise AssertionError(f"backend exited before readiness: {process.poll()}")
    ready = json.loads(line)
    assert ready["event"] == "lab-ready", ready
    assert ready["protocol"] == "dm-mc02-lab/v1", ready
    assert ready["pid"] == process.pid, ready
    assert ready["url"].startswith("http://127.0.0.1:"), ready
    return ready
