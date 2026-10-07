# Job Scout Discord Bot

Linkareer 채용 공고를 자동으로 수집하고, 관심 키워드가 포함된 공고를 Discord 채널로 전송하는 자동화 봇입니다.

GitHub Actions를 이용하여 별도 서버 없이 정기적으로 실행되도록 구성했으며, Discord Webhook 주소는 GitHub Secrets로 관리합니다. 성공한 발송과 결과가 불확실한 발송을 구분해 기록하고, 상태 파일을 저장소에 보관하여 다음 실행에서도 중복 확인에 사용합니다.

---

# 주요 기능

* Linkareer 최신순 목록 기본 5페이지 수집 및 회사명·제목·직무 분류 추출
* 키워드 기반 공고 필터링 및 영어 부분 문자열 오탐 완화
* Discord Webhook 알림 전송과 성공 응답 확인
* HTTP 타임아웃, 조회 재시도 및 Discord 429 처리
* GitHub Actions를 통한 정기 실행과 중복 실행 제어
* `sent_jobs.json` 기반 이미 처리한 공고 제외
* `pending_jobs.json` 기반 발송 결과 미확정 공고 보류
* 전송 없는 미리보기, 현재 목록 기준선 등록 및 보류 상태 수동 해소
* 오프라인 테스트와 push/PR 테스트 workflow

---

# 검색 키워드

```python
["백엔드", "backend", "전산직", "it", "db", "java", "python"]
```

`main.py`의 `TARGET_KEYWORDS`에서 변경합니다. 제목을 기준으로 검사하며, 영어 키워드는 대소문자를 구분하지 않습니다. 제목이 일치하지 않아도 직무 분류가 아래 조건과 맞으면 후보로 포함합니다. 영문·숫자 토큰 경계를 사용해 `digital`의 `it`, `JavaScript`의 `java`와 같은 오탐을 줄입니다. 반대로 `backenddeveloper`처럼 붙여 쓴 표현은 매칭되지 않을 수 있습니다.

직무 분류 키워드 (`CATEGORY_KEYWORDS`): `IT/개발`, `백엔드`, `서버개발`, `전산`, `정보보안`, `데이터엔지니어`, `DBA`. 목록 HTML의 구조화 데이터에 포함된 하위·상위 분류와 화면에 표시된 분류를 확인합니다. 따라서 백엔드 외 IT 직무도 포함하며, 신입·인턴만으로 제한하지 않습니다. 제목 키워드만 맞는 경우 IT 회사의 비개발 직무 등 오탐도 남을 수 있습니다.

---

# 핵심 구현 내용

## 채용 공고 수집

* Requests로 최신순 목록을 기본 5페이지까지 조회 (`--pages 1~20`으로 변경 가능)
* 페이지 사이 1초 대기, 마지막 페이지에서는 조기 종료
* HTML의 `__NEXT_DATA__`에 포함된 페이지 번호·일반 공고 ID·전체 개수로 응답 확인
* 요청과 응답 페이지 불일치, 반복 페이지 또는 파싱 누락 시 전송 전에 중단
* BeautifulSoup으로 `a.recruit-link`의 `.recruit-name`과 같은 표 행의 `.company-name` 추출
* 직무 분류·추천 표시를 제목으로 취급하지 않도록 텍스트 순서 추측 제거
* 공고 URL의 쿼리·fragment·마지막 슬래시 등을 정규화하고 같은 공고 링크 중복 제거
* 파싱 결과가 0개이면 오류로 처리하며, 구조화 데이터가 빈 목록임을 명시하는 경우만 정상 종료
* 수집한 모든 페이지를 검증한 뒤에만 발송 단계 진행
* 페이지별 수집 수·중복 제거 후 추가 수·매칭 수와 전체 후보 선정 근거를 로그로 기록

기본 범위는 최신순 5페이지이며 전체 공고를 빠짐없이 수집한다는 의미는 아닙니다. 광고 공고도 함께 수집하되 URL로 중복 제거합니다. 목록 HTML의 직무 분류가 ‘외 N개’로 생략되어도 내장 데이터에 있는 분류는 확인합니다. 상세 본문·이미지에만 적힌 직무, 마감 여부의 별도 확인은 구현하지 않았습니다. 수집 중 목록이 변경되면 페이지 이동에 따른 누락 가능성도 남습니다.

---

## 공고 필터링

* 제목 키워드 또는 IT 관련 직무 분류가 일치하는 공고 선별
* 이미 처리한 URL과 발송 결과 미확정 URL은 전송 대상에서 제외
* 여러 키워드가 맞아도 하나의 공고는 한 번만 전송
* Discord 메시지에 `제목:backend`, `직무:IT/개발`처럼 선정 근거 표시

---

## 전송 확인과 오류 처리

* 연결·응답 타임아웃 설정
* 목록 조회의 일시적 네트워크 오류 및 일부 5xx에 최대 3회 시도와 지수 대기 적용
* Discord에 `wait=true`로 요청하고 HTTP 200 및 메시지 ID를 확인한 뒤 성공으로 기록
* Discord 429는 `retry_after`에 따라 대기하며 최대 3회 시도; 유효하지 않거나 60초를 넘는 대기 요청은 이번 실행에서 중단
* 명확한 4xx 거부는 성공 기록에 추가하지 않고 실행 실패로 표시
* POST 타임아웃·5xx·불명확한 응답은 자동 반복 전송하지 않고 결과 미확정 상태로 보류
* 오류 로그에 Webhook URL이나 원본 Requests 예외를 출력하지 않도록 처리

---

## 자동화

* GitHub Actions cron으로 매일 실행 예약
* `permissions: contents: write`를 발송 작업에 명시하여 상태 파일 커밋·push에 사용
* 고정 concurrency 그룹과 `cancel-in-progress: false`로 발송 workflow의 동시 실행 제한
* 매 실행 전 오프라인 회귀 테스트 수행
* 크롤러가 일부 발송 후 실패해도 상태 저장 단계를 실행하도록 구성
* 발송·보류 기록을 Artifact로 30일 보관하여 push 실패 시 복구에 활용
* 별도의 테스트 workflow는 push/PR에서 읽기 권한으로 실행하며 Discord에 전송하지 않음

브랜치 규칙이나 조직 정책이 쓰기를 제한하면 push는 실패할 수 있습니다. Artifact가 생성됐더라도 다음 실행은 저장소의 JSON을 읽으므로, 누락된 기록은 재전송 전에 복구해야 합니다.

---

## 중복 알림 방지

* `sent_jobs.json`: 성공 확인한 공고 및 사용자가 baseline으로 건너뛴 공고의 URL 목록
* `pending_jobs.json`: 요청 전 기록하고, 발송 여부가 불확실하면 유지하는 URL 목록
* 성공한 공고는 즉시 로컬 기록을 저장하며, 이후 공고 처리 실패로 앞선 성공 기록이 사라지지 않도록 구성
* 임시 파일 작성 → flush/fsync → `os.replace`로 JSON 파일 교체
* 손상된 JSON을 빈 목록으로 간주하지 않고 실행 중단
* 같은 로컬 폴더에서의 동시 작업은 lock 파일로 제한

Discord 전송과 GitHub 저장은 하나의 트랜잭션이 아니므로 **정확히 한 번 전송을 보장하지 않습니다.** 원격 기록 저장 전에 runner가 사라지거나 기록이 유실되면 중복 가능성이 남습니다. 결과 미확정 공고는 사용자가 Discord 수신 여부를 확인한 뒤 해소합니다.

---

## 보안

* Webhook은 기존 `DISCORD_WEBHOOK` Secret으로 관리
* 테스트·dry-run·baseline은 Webhook 없이 실행 가능
* 전송 URL의 HTTPS 및 Discord 호스트 검증, POST 리다이렉트 비활성화
* 메시지의 `allowed_mentions`를 비활성화하고 Embed 문자열 길이 제한
* `.env`, 가상환경, 임시 파일은 Git에서 제외

---

# 기술 스택

* Python 3.10 (GitHub Actions)
* Requests
* BeautifulSoup4
* unittest / unittest.mock
* Discord Webhook
* GitHub Actions
* JSON / Git 기반 상태 저장

의존성 버전은 `requirements.txt`에 고정했습니다. MySQL이나 별도 서버는 사용하지 않습니다.

---

# 프로젝트 구조

```text
Job-scout-discord
├─ .github
│  └─ workflows
│     ├─ run_bot.yml
│     └─ tests.yml
├─ tests
│  ├─ test_bot.py
│  └─ test_collection.py
├─ main.py
├─ state.py
├─ requirements.txt
├─ sent_jobs.json
├─ pending_jobs.json
└─ README.md
```

---

# 실행 방식

GitHub Actions에서 한국 시간 기준 매일 오전 9시 실행을 예약합니다. GitHub의 부하 등에 따라 실제 시작 시각은 지연될 수 있습니다.

```yaml
schedule:
  - cron: '0 0 * * *'
```

저장소 **Actions → Linkareer Discord Bot Automation → Run workflow**에서 기본 브랜치를 선택하고 다음 모드로 수동 실행할 수도 있습니다.

| 모드 | 동작 |
|---|---|
| dry-run | 후보 조회만 수행. Discord 전송과 JSON 변경 없음 |
| send | 신규 후보 실제 전송 및 발송·보류 기록 저장 |
| baseline | 현재 매칭 공고를 보내지 않고 처리 기록에 등록 |

예약 실행은 `send`, 수동 실행의 기본 선택은 `dry-run`입니다. 처음 적용할 때는 dry-run 결과를 확인한 뒤 전송합니다. 예전 push 실패로 과거 발송 이력이 누락된 경우 기존 수신 여부를 코드만으로 복원할 수 없습니다. 현재 공고를 모두 건너뛰려는 경우에만 baseline을 선택합니다.

## 로컬 설치 및 테스트

Windows PowerShell에서 프로젝트 루트를 기준으로 실행합니다.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe main.py --dry-run
```

테스트는 임시 디렉터리와 가짜 HTTP 응답을 사용하여 실제 Discord 전송 없이 수행합니다. 성공·실패·429·타임아웃, 부분 발송 기록, JSON 손상, 원자적 파일 교체, 파싱 및 키워드 필터링 등을 검증하는 기존 30개 및 수집 범위·분류 판별 관련 15개를 포함하여 총 45개 테스트를 포함합니다.

기본 5페이지보다 넓게 미리보려면 `python main.py --dry-run --pages 10`을 사용할 수 있습니다. Actions는 옵션 없이 실행하므로 `main.py`의 `DEFAULT_PAGES` 값(기본 5)을 사용합니다. 수집 범위 확대 직후에는 기존 기록에 없는 공고가 한꺼번에 여러 건 전송될 수 있습니다.

로컬 실제 발송이 필요하면 환경변수 `DISCORD_WEBHOOK`에 본인의 URL을 설정한 뒤 `main.py`를 옵션 없이 실행합니다. 로컬과 Actions에서 동시에 전송하지 않아야 합니다. 로컬에서 변경한 기록은 원격에도 반영해야 다음 Actions 실행에서 사용할 수 있습니다.

## 결과 미확정 공고 처리

Discord 채널에서 해당 공고의 수신 여부를 확인합니다. 최신 원격 기록을 가져오고 다른 발송 실행이 없는 상태에서 다음 중 하나를 수행합니다.

```powershell
# 실제 수신을 확인한 URL
.\.venv\Scripts\python.exe main.py --resolve-sent "https://linkareer.com/activity/공고번호"

# 미수신을 확인하여 다음 수집 시 재시도할 URL
.\.venv\Scripts\python.exe main.py --resolve-retry "https://linkareer.com/activity/공고번호"
```

위 명령은 상태만 변경하고 전송하지 않습니다. 변경한 두 JSON을 커밋·push해야 Actions에도 반영됩니다. 보류 해제한 공고가 목록에서 사라졌다면 자동으로 다시 수집되지는 않습니다.

---

# 프로젝트에서 배운 점

* HTML 텍스트 순서를 추정하는 방식보다 회사명과 제목의 명시적 요소를 구분하는 파싱이 중요하다는 점을 확인했습니다.
* 외부 API 호출 완료와 실제 발송 성공을 구분하고, 실패한 공고를 성공 목록에 넣지 않도록 처리했습니다.
* 응답 유실 상황에서는 무조건적인 재시도와 중복 방지를 동시에 만족시키기 어렵다는 점을 고려해 결과 미확정 상태를 분리했습니다.
* GitHub Actions에서 프로그램의 성공뿐 아니라 상태 파일의 원격 저장 성공까지 확인해야 자동화의 다음 실행이 올바르게 이어진다는 점을 배웠습니다.
* 네트워크 요청을 대체하는 테스트로 오류 경로를 재현하고, 실제 전송 검증과 구분하는 방법을 익혔습니다.

---

# 향후 개선 사항

* 상세 공고 수집 및 마감 공고 제외, 페이지 이동 중 목록 변경에 대한 누락 완화
* HTML 구조 변경을 추적할 파싱 샘플과 모니터링 보강
* 원격 DB와 전달 작업 상태를 이용한 영속화 개선
* 신입·인턴/경력 조건 설정 및 제목·직무 분류 판별의 오탐·누락 측정

---

# 운영 및 검증 기록

## 기존 발송 흐름 (GitHub Actions 로그 기준)

* 2026-10-01: 백엔드 전환형 인턴 공고 1건 Discord 메시지 ID 확인 및 발송 기록 저장 성공
* 2026-10-02~10-07: 예약 실행 성공. 각 실행 시 첫 페이지 24개 중 제목 키워드 일치 0개로 전송하지 않음
* 위 결과는 수집 범위 확대 이전 버전의 기록이며 모든 IT 공고를 수집했다는 의미는 아님

## 수집 범위 확대 수정본 (2026-10-07)

* Python 3.12에서 오프라인 테스트 45개 통과. Actions의 Python 3.10 문법 호환성 확인
* 실제 목록 5페이지 dry-run: 중복 제거 후 104개 수집, 제목 매칭 5개 + 직무 분류로만 추가된 17개 = 후보 22개
* 기존 원격 상태 기록으로 미리보기: sent_total=3, pending=0, new=22. 실행 시점에 따라 결과는 달라짐
* 페이지 1~5 응답의 페이지 번호 및 공고 ID 확인
* 실제 Discord 전송과 상태 변경 없이 검증. 확대 버전의 Actions 실행·실제 전송은 적용 후 확인 필요


