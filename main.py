"""Linkareer scheduled notifier. Importing this module never sends messages."""

import argparse
import json
import logging
import math
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup

from state import BASE_URL, State, StateError, canonical_url, state_lock

LOG = logging.getLogger("job_scout")
TARGET_KEYWORDS = ["백엔드", "backend", "전산직", "it", "db", "java", "python"]
LIST_URL = BASE_URL + "/list/recruit"
TIMEOUT = (10, 25)
DEFAULT_PAGES = 5
CATEGORY_KEYWORDS = ("IT/개발", "백엔드", "서버개발", "전산", "정보보안", "데이터엔지니어", "DBA")


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
    categories: tuple = field(default=(), compare=False)


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


def page_metadata(soup):
    """Read only the public SSR data embedded in the list HTML."""
    node = soup.find("script", id="__NEXT_DATA__")
    if node is None:
        return {}, None
    try:
        data = json.loads(node.string or node.get_text())
        cache = data["props"]["pageProps"]["__APOLLO_STATE__"]
        if not isinstance(cache, dict):
            raise ValueError()
        pages = []
        for key, value in cache.get("ROOT_QUERY", {}).items():
            if not key.startswith("activities(") or not key.endswith(")"):
                continue
            args = json.loads(key[len("activities("):-1])
            if str(args.get("filterBy", {}).get("activityTypeID")) != "5":
                continue
            pagination = args["pagination"]
            page, size, total = pagination["page"], pagination["pageSize"], value["totalCount"]
            if any(type(x) is not int for x in (page, size, total)) or page < 1 or size < 1 or total < 0:
                raise ValueError()
            ids = tuple(x["__ref"] for x in value["nodes"])
            pages.append((page, size, total, ids))
        if len(pages) > 1:
            raise ValueError()
        return cache, pages[0] if pages else None
    except (ValueError, TypeError, KeyError, AttributeError):
        raise BotError("목록의 구조화 데이터가 변경됐습니다. 수집을 중단합니다.") from None


def job_categories(cache, url, tag):
    labels = []
    activity = cache.get("Activity:" + url.rsplit("/", 1)[-1], {})
    def visit(ref, seen):
        if not isinstance(ref, dict):
            return
        key = ref.get("__ref")
        if not key or key in seen or len(seen) >= 6:
            return
        seen = seen | {key}
        category = cache.get(key, {})
        name = category.get("name")
        if isinstance(name, str) and name and name != "전체":
            labels.append(name)
        visit(category.get("parent"), seen)
    for ref in activity.get("categories", []):
        visit(ref, set())
    # Retain the visible classification as a fallback; never use it as a title.
    visible = tag.select_one(".recruit-category")
    if visible:
        labels.append(visible.get_text(" ", strip=True))
    return tuple(dict.fromkeys(label for label in labels if label))


def parse_page(html):
    soup = BeautifulSoup(html, "html.parser")
    cache, meta = page_metadata(soup)
    jobs, skipped = {}, 0
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
        jobs.setdefault(url, Job(company, title, url, job_categories(cache, url, tag)))
    # Empty is valid only if the structured source explicitly says no regular items.
    if not jobs and not (meta is not None and not meta[3]):
        raise BotError("파싱 가능한 공고가 0개입니다. HTML 구조 변경·접근 차단 여부를 확인하세요.")
    if skipped:
        raise BotError(f"회사명 또는 제목을 읽지 못한 공고 {skipped}개가 있습니다.")
    if meta:
        parsed_ids = {"Activity:" + url.rsplit("/", 1)[-1] for url in jobs}
        if not set(meta[3]).issubset(parsed_ids):
            raise BotError("목록 데이터와 파싱 공고가 일치하지 않습니다. 누락 확인이 필요합니다.")
    LOG.info("parsed=%d skipped_cards=%d", len(jobs), skipped)
    return list(jobs.values()), meta


def parse_jobs(html):
    return parse_page(html)[0]


def match_reasons(job):
    reasons = ["제목:" + word for word in matching_keywords(job.title)]
    for word in CATEGORY_KEYWORDS:
        if any(re.search(re.escape(word), category, re.I) for category in job.categories):
            reasons.append("직무:" + word)
    return reasons


def collect_jobs(session, max_pages=DEFAULT_PAGES, sleep=time.sleep):
    if not 1 <= max_pages <= 20:
        raise BotError("수집 페이지 수는 1~20이어야 합니다.")
    collected, signatures = {}, set()
    pages_done = 0
    for page in range(1, max_pages + 1):
        if page > 1:
            sleep(1)
        url = LIST_URL + "?" + urlencode({"orderBy_direction": "DESC", "orderBy_field": "RECENT", "page": page})
        jobs, meta = parse_page(fetch_html(session, sleep, url=url))
        if meta is None:
            raise BotError("페이지 번호 확인용 데이터가 없습니다. 페이지 수집을 검증할 수 없습니다.")
        actual_page, size, total, ids = meta
        if actual_page != page:
            raise BotError(f"요청 페이지 {page}와 응답 페이지 {actual_page}가 다릅니다.")
        if ids and ids in signatures:
            raise BotError("같은 페이지가 반복 반환됐습니다. 발송 전 수집을 중단합니다.")
        signatures.add(ids)
        before = len(collected)
        for job in jobs:
            collected.setdefault(job.url, job)
        pages_done += 1
        LOG.info("page=%d parsed=%d unique_added=%d matched=%d total_listed=%d",
                 page, len(jobs), len(collected)-before, sum(bool(match_reasons(j)) for j in jobs), total)
        if page * size >= total or not ids:
            break
    LOG.info("collection pages=%d unique=%d title_matches=%d category_only=%d",
             pages_done, len(collected), sum(bool(matching_keywords(j.title)) for j in collected.values()),
             sum(bool(match_reasons(j)) and not matching_keywords(j.title) for j in collected.values()))
    return list(collected.values())


def fetch_html(session, sleep=time.sleep, url=LIST_URL):
    for attempt in range(3):
        try:
            response = session.get(
                url,
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
                        "name": "선정 근거 (제목 / 직무)",
                        "value": clip(", ".join(keywords), 1024),
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
    candidates = [(j, match_reasons(j)) for j in jobs]
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
    parser.add_argument("--pages", type=int, choices=range(1, 21), default=DEFAULT_PAGES, metavar="1~20", help="최대 수집 페이지 수 (기본 5)")
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
                jobs = collect_jobs(session, args.pages)
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

