"""Linkareer scheduled notifier. Importing this module never sends messages."""

import argparse
import logging
import math
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup

from state import BASE_URL, State, StateError, canonical_url, state_lock

LOG = logging.getLogger("job_scout")
TARGET_KEYWORDS = ["백엔드", "backend", "전산직", "it", "db", "java", "python"]
LIST_URL = BASE_URL + "/list/recruit"
TIMEOUT = (5, 20)


class BotError(RuntimeError):
    pass


class DeliveryRejected(BotError):
    """A definitive rejection: eligible for a future scheduled attempt."""


class DeliveryUnknown(BotError):
    """Delivery may have happened. Never retry this automatically."""


@dataclass(frozen=True)
class Job:
    company: str
    title: str
    url: str


def matching_keywords(title):
    result = []
    for word in TARGET_KEYWORDS:
        # ASCII token boundaries avoid matching 'it' in 'digital', 'java' in 'javascript'.
        match = (
            re.search(r"(?<![a-z0-9])" + re.escape(word) + r"(?![a-z0-9])", title, re.I)
            if word.isascii()
            else word in title
        )
        if match:
            result.append(word)
    return result


def parse_jobs(html):
    soup = BeautifulSoup(html, "html.parser")
    jobs, skipped = {}, 0
    # Observed Linkareer desktop rows: company and title are in separate cells.
    # Never guess from text position: recommendation labels and categories are not titles.
    for tag in soup.select("a.recruit-link[href]"):
        url = canonical_url(tag["href"])
        if url is None:
            continue
        row = tag.find_parent("tr")
        title_node = tag.select_one(".recruit-name")
        company_node = row.select_one(".company-name") if row else None
        if title_node is None or company_node is None:
            skipped += 1
            continue
        company = company_node.get_text(" ", strip=True)
        title = title_node.get_text(" ", strip=True)
        if not company or not title or title.isdigit():
            skipped += 1
            continue
        jobs.setdefault(url, Job(company, title, url))
    if not jobs:
        raise BotError(
            "파싱 가능한 공고가 0개입니다. HTML 구조 변경·접근 차단 여부를 확인하세요."
        )
    LOG.info("parsed=%d skipped_cards=%d", len(jobs), skipped)
    return list(jobs.values())


def fetch_html(session, sleep=time.sleep):
    for attempt in range(3):
        try:
            response = session.get(
                LIST_URL,
                headers={"User-Agent": "Mozilla/5.0 JobScout/1.0"},
                timeout=TIMEOUT,
            )
        except requests.RequestException:
            if attempt == 2:
                raise BotError("공고 목록 요청 실패: 네트워크 또는 타임아웃") from None
        else:
            if response.status_code == 200:
                return response.text
            if response.status_code not in (500, 502, 503, 504):
                raise BotError(
                    f"공고 목록 HTTP {response.status_code}; 접근 조건을 확인하세요."
                )
            if attempt == 2:
                raise BotError(
                    f"공고 목록 HTTP {response.status_code}; 조회 재시도 소진"
                )
        sleep(2**attempt)
    raise BotError("공고 목록 요청 실패")


def webhook_url(value):
    # Never include the URL or the original Requests exception in logs.
    try:
        u = urlsplit(value or "")
        if (
            u.scheme != "https"
            or u.hostname not in ("discord.com", "discordapp.com")
            or u.username
            or u.password
            or u.port not in (None, 443)
            or not re.fullmatch(r"/api(?:/v\d+)?/webhooks/\d+/[\w.-]+", u.path)
        ):
            raise ValueError()
    except ValueError:
        raise BotError(
            "DISCORD_WEBHOOK에 올바른 Discord Webhook URL을 설정하세요."
        ) from None
    query = [(k, v) for k, v in parse_qsl(u.query) if k != "wait"]
    query.append(("wait", "true"))
    return urlunsplit((u.scheme, u.netloc, u.path, urlencode(query), ""))


def clip(value, units):
    return value.encode("utf-16-le")[: units * 2].decode("utf-16-le", errors="ignore")


def payload(job, keywords):
    return {
        "allowed_mentions": {"parse": []},
        "embeds": [
            {
                "title": clip("매칭된 채용 공고: " + job.company, 256),
                "description": clip(job.title, 3500),
                "url": job.url,
                "color": 3447003,
                "fields": [
                    {
                        "name": "감지된 키워드",
                        "value": ", ".join(keywords),
                        "inline": True,
                    }
                ],
                "footer": {"text": "링커리어 채용 알리미 · 일일 수집"},
            }
        ],
    }


def send_to_discord(session, endpoint, job, keywords, sleep=time.sleep):
    for attempt in range(3):
        try:
            response = session.post(
                endpoint,
                json=payload(job, keywords),
                timeout=TIMEOUT,
                allow_redirects=False,
            )
        except requests.RequestException:
            raise DeliveryUnknown(
                "Discord 응답을 확인하지 못했습니다. 자동 재전송을 보류합니다."
            ) from None
        if response.status_code == 200:
            try:
                message = response.json()
            except ValueError:
                message = None
            if isinstance(message, dict) and str(message.get("id", "")).isdigit():
                return str(message["id"])
            raise DeliveryUnknown(
                "Discord 성공 응답에 메시지 ID가 없어 결과 확인이 필요합니다."
            )
        if response.status_code == 429:
            try:
                data = response.json()
                delay = float(
                    data.get("retry_after", response.headers.get("Retry-After"))
                )
            except (ValueError, TypeError, AttributeError):
                raise DeliveryRejected(
                    "Discord 429: 재시도 대기 시간을 확인할 수 없습니다."
                ) from None
            if not math.isfinite(delay) or delay < 0 or delay > 60 or attempt == 2:
                raise DeliveryRejected("Discord 429: 이번 실행의 재시도를 중단합니다.")
            sleep(delay + 0.1)
            continue
        if 400 <= response.status_code < 500:
            raise DeliveryRejected(
                f"Discord HTTP {response.status_code}: Webhook·메시지 설정을 확인하세요."
            )
        raise DeliveryUnknown(
            f"Discord HTTP {response.status_code}: 발송 여부를 직접 확인하세요."
        )
    raise DeliveryRejected("Discord 재시도 소진")


def process(jobs, state, session, mode="dry-run", endpoint=None, sleep=time.sleep):
    candidates = [(j, matching_keywords(j.title)) for j in jobs]
    candidates = [(j, k) for j, k in candidates if k]
    fresh = [
        (j, k)
        for j, k in candidates
        if j.url not in state.sent and j.url not in state.pending
    ]
    LOG.info(
        "matched=%d new=%d sent_total=%d pending=%d mode=%s",
        len(candidates),
        len(fresh),
        len(state.sent),
        len(state.pending),
        mode,
    )
    for job, keywords in fresh:
        LOG.info(
            "candidate title=%s url=%s keywords=%s",
            job.title,
            job.url,
            ",".join(keywords),
        )
    if mode == "dry-run":
        return len(fresh)
    if mode == "baseline":
        state.baseline(j.url for j, _ in candidates)
        LOG.info("현재 매칭 공고를 알림 없이 처리 기록에 등록했습니다.")
    elif mode == "send":
        if endpoint is None:
            raise BotError("Webhook 설정이 없습니다.")
        for job, keywords in fresh:
            state.begin(job.url)  # Persist BEFORE the external side effect.
            try:
                message_id = send_to_discord(session, endpoint, job, keywords, sleep)
            except DeliveryRejected:
                state.reject(job.url)  # Known failure is not recorded as sent.
                raise
            state.confirm(job.url)
            LOG.info("sent url=%s message_id=%s", job.url, message_id)
    else:
        raise BotError("알 수 없는 실행 모드")
    if state.pending:
        raise BotError(
            f"발송 여부 확인이 필요한 공고 {len(state.pending)}개가 pending_jobs.json에 있습니다."
        )
    return len(fresh)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Job Scout Discord Bot")
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--dry-run", action="store_true", help="조회만 수행; 전송/기록 변경 없음"
    )
    group.add_argument(
        "--baseline",
        action="store_true",
        help="현재 매칭 공고를 전송 없이 처리 기록에 추가",
    )
    group.add_argument(
        "--resolve-sent", metavar="URL", help="Discord에서 수신을 확인한 보류 공고 처리"
    )
    group.add_argument(
        "--resolve-retry",
        metavar="URL",
        help="미수신을 확인한 보류 공고를 다음 실행에서 재시도 허용",
    )
    parser.add_argument(
        "--state-dir", type=Path, default=Path(__file__).resolve().parent
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        with state_lock(args.state_dir):
            state = State(args.state_dir)
            if args.resolve_sent or args.resolve_retry:
                url = canonical_url(args.resolve_sent or args.resolve_retry)
                if url not in state.pending:
                    raise StateError("지정한 URL은 보류 목록에 없습니다.")
                state.confirm(url) if args.resolve_sent else state.reject(url)
                LOG.info("보류 기록 처리 완료: %s (이번 명령은 전송하지 않음)", url)
                return 0
            mode = (
                "dry-run" if args.dry_run else "baseline" if args.baseline else "send"
            )
            endpoint = (
                webhook_url(os.environ.get("DISCORD_WEBHOOK"))
                if mode == "send"
                else None
            )
            with requests.Session() as session:
                jobs = parse_jobs(fetch_html(session))
                process(jobs, state, session, mode, endpoint)
        return 0
    except (BotError, StateError) as exc:
        LOG.error("%s", exc)
        return 1
    except OSError:
        LOG.error(
            "상태 파일 저장 실패. 실행을 중단합니다. 기존 기록과 보류 기록을 확인하세요."
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
