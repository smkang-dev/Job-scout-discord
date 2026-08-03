# Job Scout Discord Bot

Linkareer 채용 공고를 자동으로 수집하고, 관심 키워드가 포함된 공고를 Discord 채널로 전송하는 자동화 봇입니다.

GitHub Actions를 이용하여 별도 서버 없이 정기적으로 프로그램이 실행되도록 구성했으며, Discord Webhook 주소는 GitHub Secrets를 통해 안전하게 관리했습니다.

---

# 주요 기능

* Linkareer 채용 공고 크롤링
* 키워드 기반 공고 필터링
* Discord Webhook 알림 전송
* GitHub Actions를 통한 자동 실행
* `sent_jobs.json` 기반 중복 발송 방지
* GitHub Secrets를 이용한 Webhook 보안 관리

---

# 검색 키워드

```python
["백엔드", "backend", "전산직", "it", "db", "java", "python"]
```

---

# 핵심 구현 내용

## 채용 공고 수집

* Linkareer 채용 공고 페이지 크롤링
* BeautifulSoup을 이용한 HTML 데이터 파싱
* 공고 제목을 기준으로 키워드 검색

---

## 공고 필터링

* 관심 키워드가 포함된 공고만 선별
* 조건에 맞는 공고만 Discord 채널로 전송

---

## 자동화

* GitHub Actions의 cron 스케줄러를 이용하여 프로그램이 매일 자동 실행되도록 구성
* 별도 서버를 운영하지 않고 GitHub Actions를 실행 환경으로 활용

---

## 중복 알림 방지

* 전송한 공고 URL을 `sent_jobs.json`에 저장
* 이미 발송한 공고는 다시 전송되지 않도록 구현

---

## 보안

* Discord Webhook URL을 GitHub Secrets로 관리
* 민감한 정보를 코드와 분리하여 저장소에 노출되지 않도록 구성

---

# 기술 스택

* Python 3.10
* Requests
* BeautifulSoup4
* Discord Webhook
* GitHub Actions
* Git / GitHub

---

# 프로젝트 구조

```text
Job-scout-discord
├─ .github
│  └─ workflows
│     └─ run_bot.yml
├─ main.py
├─ sent_jobs.json
└─ README.md
```

---

# 실행 방식

GitHub Actions의 cron 스케줄러를 이용하여 한국 시간 기준 매일 오전 9시에 자동 실행되도록 설정했습니다.

```yaml
schedule:
  - cron: '0 0 * * *'
```

또한 저장소의 **Actions** 탭에서 **Run workflow**를 선택하여 수동 실행도 가능합니다.

---

# 프로젝트에서 배운 점

* BeautifulSoup을 이용하여 HTML 구조를 분석하고 필요한 데이터를 추출하는 방법을 익혔습니다.
* GitHub Actions를 활용하여 별도 서버 없이 프로그램을 정기적으로 실행하는 자동화 환경을 구축했습니다.
* Discord Webhook을 이용해 외부 서비스와 연동하는 방법을 익혔습니다.
* GitHub Secrets를 활용하여 Webhook URL과 같은 민감 정보를 코드와 분리하여 관리하는 방법을 익혔습니다.
