# claire_bible — 공개 아키텍처 계획

관련 문서: [GOALS.md](GOALS.md), [sample.md](sample.md),
[외부 접속 설계](docs/EXTERNAL_ACCESS.md)

## 1. 처리 흐름

```text
Telegram / CLI / local API
          ↓
입력 분류와 원문 보관
          ↓
Fetcher → 정규화 → 중복 확인
          ↓
구조화 추출 → 엔티티 해소 → 관계 검증
          ↓
SQLite 정본 + FTS + vector + vault export
          ↓
검색 / 그래프 UI / 종합 / 공유
```

모든 진입점은 `IngestService`를 공유한다. 입력 경로마다 별도 적재 로직을 만들지 않아
동일한 정규화·중복 제거·복구 정책을 적용한다.

## 2. 주요 구성요소

### 입력과 수집

- `src/claire/telegram_bot.py`: 텔레그램 long-polling 진입점
- `src/claire/cli.py`: 적재·검색·운영 명령
- `src/claire/api/server.py`: 루프백 기본 로컬 API와 웹 UI
- `src/claire/ingest/router.py`: URL·공유 텍스트·파일·메모 분류
- `src/claire/ingest/fetchers/`: 웹, 리다이렉트, YouTube, X, 텍스트 fetcher

Fetcher는 URL, canonical URL, 제목, 작성자, 시각, 원문, 소스 종류와 콘텐츠 해시를
포함하는 정규화된 `Document`를 반환한다. 네트워크 입력은 허용 프로토콜과 목적지 정책을
통과해야 한다.

### 추출과 온톨로지

- `src/claire/extract/`: mock/Gemini provider, 구조화 추출, 엔티티 해소
- `src/claire/ontology/`: 엔티티·관계 모델과 domain/range 레지스트리

모델 출력은 닫힌 타입 집합을 우선 사용한다. 새 타입 제안은 별도 provisional 데이터로
보존하며, 관계는 저장 전에 허용 source/target 타입을 검증한다. 엔티티는 exact·alias·약어·
임베딩 후보와 선택적 모델 판정을 조합해 기존 노드로 해소한다.

### 저장과 검색

- SQLite가 문서·엔티티·관계·inbox·큐의 정본이다.
- FTS5와 벡터 유사도를 RRF로 결합하고 그래프 이웃으로 검색 문맥을 확장한다.
- sqlite-vec를 사용할 수 없으면 저장된 임베딩의 brute-force cosine 검색으로 폴백한다.
- vault Markdown은 SQLite에서 생성하는 단방향 투영이며 정본으로 취급하지 않는다.

### 백그라운드 작업

- recover loop: 일시 실패를 지수 백오프로 재처리하고 영구 실패를 구분한다.
- refresh loop: 오래되거나 빈약한 문서를 원래 ID로 다시 수집한다.
- expand loop: 문서에서 발견한 후보를 제한된 1홉 범위로 조사한다.
- backup loop: `VACUUM INTO` 스냅샷을 만들고 열어서 복원 가능성을 검증한다.

## 3. 중복과 병합 정책

1. 동일 canonical URL은 기존 문서를 갱신한다.
2. 동일 콘텐츠 해시는 중복 문서 생성을 막는다.
3. 1홉 확장 결과가 부모와 같은 주제라고 판정되면 출처와 원문을 보존한 채 부모 문서에
   합칠 수 있다.
4. 병합 실패 시 원래 문서 스냅샷으로 복원한다.
5. 모델 입력에는 길이 상한을 적용하지만 저장 원문은 임의로 절단하지 않는다.

세부 설계는 [ONEHOP_MERGE_DESIGN.md](docs/ONEHOP_MERGE_DESIGN.md)를 참고한다.

## 4. 인증과 외부 접속

- API는 기본적으로 `127.0.0.1`에만 공개한다.
- 쓰기·모델 비용 발생 경로는 owner bearer 또는 owner 세션을 요구한다.
- 읽기 전용 토큰과 읽기 전용 세션은 쓰기 경로를 통과할 수 없다.
- 문서 공유 토큰은 전체 세션 토큰과 분리하고 단일 문서에만 권한을 부여한다.
- 외부 접속은 VPN 또는 인증 프록시를 사용하며, 직접 공개할 때는 읽기 경로까지
  애플리케이션 인증을 적용한다.

실제 호스트명, 계정, 포트와 공개 URL은 `.env` 또는 실행 환경에서만 설정한다.

## 5. 배포와 복구

Docker Compose 서비스는 같은 `data`·`vault` 볼륨을 공유한다. `deploy.sh`는 배포 대상을
환경에서 읽고, 코드 동기화에서 데이터·vault·자격증명 파일을 제외한다. 배포 전 로컬 CI를
통과해야 한다.

복구 순서:

1. 쓰기 서비스를 중지한다.
2. 복원할 검증된 스냅샷을 선택한다.
3. 라이브 DB를 보존한 뒤 스냅샷으로 교체한다.
4. 서비스를 시작하고 `claire health`와 그래프 통계를 확인한다.

## 6. 검증 전략

- `scripts/ci.sh`: lock 파일 동기 여부와 전체 pytest 실행
- 단위 테스트: 라우팅, canonicalization, 스키마 마이그레이션, 인증과 큐 상태 전이
- 통합 테스트: 같은 `IngestService`를 통한 적재·갱신·복구
- 합성 replay: `sample.md`의 공개 입력을 로컬 API에 순차 전송
- 모델 평가: 고정된 공개 fixture로 엔티티 해소·병합·요약의 기대 조건 확인

`replay.jsonl` 같은 실행 로그는 생성물이며 버전 관리하지 않는다.

## 7. 저장소 데이터 경계

다음 항목은 공개 저장소에 포함하지 않는다.

- `.env`와 파생 환경 파일
- `data/`, `vault/`, `research/`
- 재생·배포·운영 로그
- 실제 사용자 메시지, 열람 목록과 운영 DB 식별자
- 실제 배포 계정, 주소, SSH 설정과 서비스 URL

공개 문서와 테스트에는 `example.com`, 합성 ID, 공개 표준 문서 또는 명시적인 테스트
fixture만 사용한다.
