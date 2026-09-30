"""Validated URL ledgers and atomic local writes; no network side effects."""

import json
import os
import re
import tempfile
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urljoin, urlsplit

BASE_URL = "https://linkareer.com"


class StateError(RuntimeError):
    pass


def canonical_url(value):
    if not isinstance(value, str):
        return None
    try:
        u = urlsplit(urljoin(BASE_URL, value.strip()))
        if (
            u.scheme not in ("http", "https")
            or u.hostname not in ("linkareer.com", "www.linkareer.com")
            or u.username
            or u.password
            or u.port not in (None, 80, 443)
        ):
            return None
        match = re.fullmatch(r"/(activity|recruit)/(\d+)/?", u.path)
        return BASE_URL + "/" + match[1] + "/" + str(int(match[2])) if match else None
    except ValueError:
        return None


def read_urls(path, required=False):
    path = Path(path)
    if not path.exists():
        if required:
            raise StateError(f"{path.name}이 없습니다. 기존 발송 기록을 복구하세요.")
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise StateError(
            f"{path.name}을 읽을 수 없습니다. 빈 목록으로 초기화하지 않습니다."
        ) from exc
    if not isinstance(data, list):
        raise StateError(f"{path.name}은 URL 배열이어야 합니다.")
    urls = set()
    for value in data:
        url = canonical_url(value)
        if url is None:
            raise StateError(f"{path.name}에 잘못된 공고 URL이 있습니다.")
        urls.add(url)
    return urls


def atomic_write(path, urls):
    path = Path(path)
    fd, temp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as file:
            json.dump(sorted(urls), file, ensure_ascii=False, indent=2)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


class State:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.sent_file = self.directory / "sent_jobs.json"
        self.pending_file = self.directory / "pending_jobs.json"
        self.sent = read_urls(self.sent_file, required=True)
        self.pending = read_urls(self.pending_file) - self.sent

    def begin(self, url):
        self.pending.add(url)
        atomic_write(self.pending_file, self.pending)

    def confirm(self, url):
        self.sent.add(url)
        # Write sent first: a crash before pending cleanup still suppresses repeats.
        atomic_write(self.sent_file, self.sent)
        self.reject(url)

    def reject(self, url):
        self.pending.discard(url)
        atomic_write(self.pending_file, self.pending)

    def baseline(self, urls):
        self.sent.update(set(urls) - self.pending)
        atomic_write(self.sent_file, self.sent)
        atomic_write(self.pending_file, self.pending)


@contextmanager
def state_lock(directory):
    path = Path(directory) / ".job-scout.lock"
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise StateError(
            "같은 폴더에서 다른 작업이 실행 중이거나 이전 lock이 남아 있습니다."
        ) from exc
    try:
        os.close(fd)
        yield
    finally:
        path.unlink(missing_ok=True)
