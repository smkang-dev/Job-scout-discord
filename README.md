# Job Scout Discord Bot

링커리어 채용 공고를 자동으로 수집하고, 관심 키워드가 포함된 공고를 Discord로 전송하는 자동화 봇입니다.

GitHub Actions를 이용해 매일 자동 실행되며, Discord Webhook 주소는 GitHub Secrets로 관리합니다.

--- 

## 주요 기능

* 링커리어 채용 공고 크롤링
* 키워드 기반 공고 필터링
* Discord Webhook 알림 전송
* GitHub Actions를 통한 자동 실행
* `sent_jobs.json` 기반 발송 이력 관리
* GitHub Secrets를 이용한 웹훅 보안 관리

---

## 검색 키워드

```python
["백엔드", "backend", "전산직", "it", "db", "java", "python"]
```

---

## 동작 과정

1. GitHub Actions가 정해진 시간에 실행됩니다.
2. 링커리어 채용 페이지에서 공고를 수집합니다.
3. 공고 제목에 검색 키워드가 포함되어 있는지 확인합니다.
4. 조건에 맞는 공고를 Discord 채널로 전송합니다.
5. 발송한 공고 링크를 `sent_jobs.json`에 저장하도록 구성했습니다.

---

## 기술 스택

* Python 3.10
* Requests
* BeautifulSoup4
* Discord Webhook
* GitHub Actions
* Git / GitHub

---

## 프로젝트 구조

```text
Job-scout-discord
├─ .github
│  └─ workflows
│     └─ main.yml
├─ main.py
├─ sent_jobs.json
└─ README.md
```

---

## 실행 방식

GitHub Actions에서 매일 자동으로 실행됩니다.

```yaml
schedule:
  - cron: '0 0 * * *'
```

GitHub Actions의 cron은 UTC 기준이며, 위 설정은 한국시간 기준 매일 오전 9시에 실행됩니다.

수동 실행은 저장소의 `Actions` 탭에서 `Run workflow`를 통해 가능합니다.

---

## 프로젝트에서 배운 점

* BeautifulSoup을 이용한 HTML 데이터 파싱
* 키워드 기반 데이터 필터링
* Discord Webhook을 활용한 외부 서비스 연동
* GitHub Actions를 활용한 서버 없는 자동화 실행
* GitHub Secrets를 이용한 민감 정보 관리
