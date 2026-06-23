import json
import os
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin

# 1. 환경설정
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK")
TARGET_KEYWORDS = ["백엔드", "backend", "전산직", "it", "db", "java", "python"]
DB_FILE = "sent_jobs.json"


# 2. 알림 보낸 채용공고 데이터 로드
def load_sent_jobs():
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []


# 3. 알림 보낸 채용공고 저장
def save_sent_jobs(jobs):
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(jobs, f, ensure_ascii=False, indent=4)


# 4. 디스코드 웹훅으로 데이터 전송
def send_to_discord(company, title, url, matched_keyword):
    payload = {"embeds": [{"title": f"매칭된 채용 공고: {company}", "description": f"**[{title}]({url})**",
                           "color": 3447003, "fields":
                               [{"name": "감지된 키워드", "value": f"{matched_keyword}", "inline": True, }],
                           "footer": {"text": "링커리어 실시간 채용 알리미"}}]}
    try:
        res = requests.post(DISCORD_WEBHOOK_URL, json=payload)
        if res.status_code not in [200, 204]:
            print(f"디스코드 전송 실패 (상태코드 {res.status_code}): {res.text}")
    except Exception as e:
        print(f"디스코드 전송 오류: {e}")


# 5. 메인 크롤링 및 필터링 로직
def main():
    jobs = load_sent_jobs()
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}

    url = "https://linkareer.com/list/recruit"
    response = requests.get(url, headers=headers)
    if response.status_code != 200:
        print("사이트 요청 실패")
        return
    soup = BeautifulSoup(response.text, "html.parser")

    # 공고 페이지 링크를 품고 있는 a 태그 수집
    job_items = [
        tag for tag in soup.find_all("a", href=True)
        if "/activity/" in tag["href"] or "/recruit/" in tag["href"]
    ]
    print(f"총 {len(job_items)}개의 공고를 발견했습니다. 분석을 시작합니다...")

    for item in job_items:
        try:
            # 카드 내부의 텍스트 리스트를 순서대로 추출
            full_text = [t.strip() for t in item.stripped_strings if t.strip()]

            # 링커리어 카테고리 태그 및 마감 찌꺼기 1차 필터링
            ignored_words = ["대외활동", "공모전", "동아리", "인턴/채용", "채용", "교육", "강연", "마감", "오늘마감", "조회", "Q&A", "댓글"]
            cleaned_text = [
                t for t in full_text
                if not (t.startswith("D-") or t.startswith("👁") or t in ignored_words)
            ]

            if len(cleaned_text) < 2:
                continue

            # [핵심 보정] 링커리어 카드 텍스트 배치 특징 저격
            # 항상 카드 텍스트의 마지막에 '조회수'나 '댓글/Q&A 개수' 같은 노이즈가 남거나 밀리는 현상을 방어하기 위해
            # 앞의 2개 요소(회사명, 공고제목)만 명확하게 슬라이싱하여 고정합니다.
            company = cleaned_text[0]
            title = cleaned_text[1]

            # 회사명과 제목이 2글자 이하의 비정상 데이터이거나 숫자로만 이루어진 노이즈라면 패스
            if len(company) <= 1 or len(title) <= 3 or title.isdigit():
                continue

            link = urljoin("https://linkareer.com", item["href"])
            print(f"{company} {title} {link}")

            if link in jobs:
                continue

            search_target_text = " ".join(cleaned_text[1:]).lower()

            for keyword in TARGET_KEYWORDS:
                if keyword and keyword.lower() in title.lower():
                    send_to_discord(company, title, link, keyword)
                    jobs.append(link)
                    print(f"알림 발송 완료: {company} - {title}")
                    break
        except Exception as e:
            print(f"카드 파싱 중 예외 발생: {e}")
            continue
    save_sent_jobs(jobs)
    print("크롤링 및 데이터 저장 완료")


if __name__ == "__main__":
    main()