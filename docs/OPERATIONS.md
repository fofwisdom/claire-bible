# 운영 명령 경계

배포된 Claire 인스턴스는 `cb-manuscript`를 호스트 운영 진입점으로 사용한다.
애플리케이션 CLI인 `claire`는 유지하되 실행 환경에 따라 다음 표면을 구분한다.

| 명령 표면 | 용도 | 실행 환경 |
|---|---|---|
| `./cb-manuscript <command>` | 설정, 설치·업데이트, 컨테이너 수명주기 | 배포 호스트 |
| `./cb-manuscript app <command>` | 배포 설정·데이터를 사용하는 앱 one-off 작업 | 임시 `api` 컨테이너 |
| `uv run claire <command>` | 현재 checkout의 로컬 개발·테스트 | 호스트 가상환경 |
| `claire <command>` | 서비스 프로세스와 앱 기능의 내부 진입점 | 컨테이너 내부 |

## 호스트 운영

다음 작업은 `cb-manuscript` 최상위 명령으로 수행한다.

```bash
./cb-manuscript init
./cb-manuscript doctor
./cb-manuscript install
./cb-manuscript update
./cb-manuscript up
./cb-manuscript down
./cb-manuscript restart
./cb-manuscript backup
./cb-manuscript restore backups/cb-YYYYMMDD --yes
./cb-manuscript status
./cb-manuscript logs -f api
./cb-manuscript health
```

이 계층은 `.env` 또는 `.env.dev`, Compose project, 서비스 profile, 업데이트 잠금과
migration 순서를 일관되게 적용한다. 배포 인스턴스를 직접 `docker compose`나
호스트의 `uv run claire`로 관리하지 않는다.

`./cb-manuscript dev <command>`는 Docker 개발 overlay를 선택한다. 이는 현재 checkout에서
직접 실행하는 `uv run claire <command>`와 다른 환경이며, 별도의 project·데이터 경로를
사용한다.

## 배포된 앱의 one-off 명령

배포된 인스턴스와 같은 설정·데이터로 앱 명령을 한 번 실행할 때
`cb-manuscript app`을 사용한다.

```bash
./cb-manuscript app status
./cb-manuscript app stats
./cb-manuscript app search "키워드"
./cb-manuscript app health
./cb-manuscript app --help
```

`app`에서 실행이 허용된 앱 명령은 인수와 종료 코드를 그대로 전달한다. `bot`,
`serve-api`, `*-loop`처럼 계속 실행되는 서비스는 `app`으로 중복 기동하지 않고
Compose 수명주기에 맡긴다. 설치·업데이트·migration처럼 서비스 정지 순서가 필요한
작업도 해당 `cb-manuscript` 최상위 흐름을 사용한다.

모든 `app` 실행은 인스턴스 잠금을 획득하므로 `install`, `update`, `up`, `down`,
`restart`와 동시에 진행되지 않는다. 다음 작업은 일반 one-off가 아니므로 기본
차단된다.

- lifecycle: `migrate`
- Compose 관리 서비스: `bot`, `serve-api`, `recover-loop`, `refresh-loop`,
  `expand-loop`
- 파괴적·영속 유지보수: `reextract`, `dedup-merge --apply`,
  적용 모드의 `recanonicalize`

구현 조사나 긴급 복구처럼 raw 앱 명령이 반드시 필요하면 명령 바로 뒤에
`--advanced`를 명시할 수 있다.

```bash
./cb-manuscript app --advanced <claire-command> [args...]
```

이는 차단만 해제하는 전문가용 탈출구다. 인스턴스 잠금은 유지하지만 이미 실행 중인
서비스를 중지하지 않으며, migration 순서, 데이터 백업 또는 복구 가능성을 보장하지
않는다. 인스턴스 백업·복원은 최상위 `backup`, `restore`만 사용한다.

## health의 두 의미

```bash
./cb-manuscript health
./cb-manuscript app health
```

- `cb-manuscript health`는 실행 중인 `api` 컨테이너에서 `claire liveness`를 호출한다.
  DB 접근과 현재 schema가 정상이면 성공한다. 출력에 `degraded` 진단이 있더라도
  liveness 성공 여부에는 반영하지 않는다.
- `cb-manuscript app health`는 임시 컨테이너에서 전체 애플리케이션 health를 계산한다.
  DB·큐·inbox 상태를 출력하며, `degraded`이면 종료 코드 1을 반환한다.

따라서 배포 직후와 감시용 생존 확인에는 전자를, 누적 실패와 사람의 조치가 필요한
상태 진단에는 후자를 사용한다.

## 고급 Compose 탈출구

일반 운영 명령이 제공하지 않는 Compose 기능이 필요할 때만 다음 탈출구를 사용한다.

```bash
./cb-manuscript compose -- ps
./cb-manuscript compose -- config
```

이 경로도 올바른 env 파일과 Compose project를 선택하지만, `install`·`update`가
보장하는 작업 순서까지 대신 적용하지는 않는다. 서비스 기동·중지와 migration은
가능하면 전용 최상위 명령을 사용한다.

## 백업과 복원

```bash
# data + vault, 폴더 산출물
./cb-manuscript backup

# 단일 archive 파일
./cb-manuscript backup --format archive

# component 선택
./cb-manuscript backup --component data

# 파일 또는 폴더 자동 판별
./cb-manuscript restore backups/cb-20260725 --yes
./cb-manuscript restore backups/cb-20260725.tar.gz --component data --yes
```

산출물 이름은 현지 날짜 기준 `backups/cb-YYYYMMDD/` 또는
`backups/cb-YYYYMMDD.tar.gz`다. 같은 날의 파일과 폴더는 하나의 slot으로 간주한다.
기존 backup이 있으면 중단하며, 명시적인 `--replace`만 검증된 새 산출물로 교체한다.
자동 prune이나 보존 개수 정책은 두지 않는다.

기본 component는 `data`와 `vault`다. `data`의 기존 `backups`,
`offsite-backups`, 내부 `checkpoints`는 재귀 backup에서 제외한다. `.env`는 secret과
호스트 경로가 섞여 있으므로 v1 산출물에 포함하지 않는다. 재해 복구 전에 대상 호스트의
`.env`를 별도로 준비해야 한다.

backup은 현재 project와 exact-name legacy writer를 모두 중지하고 SQLite를 일관된
단일 파일로 snapshot한 뒤 전체 파일을 복사한다. manifest는 component, source revision,
DB schema, 파일 크기와 SHA-256을 기록한다. hash는 우발적 손상·단순 변조 탐지이며
서명이나 악의적 재작성 방지는 아니다.

restore는 archive traversal, link·특수 파일, manifest 밖의 파일, hash·DB 오류,
profile/project 불일치를 writer 정지 전에 거부한다. 승인된 component는 같은
filesystem의 sibling staging에서 준비하고 기존 경로와 교체한다. data 복원에는 현재
이미지의 migration을 적용한다. writer 재개와 liveness까지 실패하면 component 교체를
역순으로 rollback하고 이전 실행 상태만 재개한다. rollback 자체가 실패하면 writer를
중지하고 `.cb-manuscript/restore-transaction.json`을 남겨 수동 복구 경로를 보존한다.

`backups/`는 Git, Docker build context와 원격 `rsync --delete`에서 모두 제외한다.
운영 backup을 컨테이너 내부 `claire` 명령으로 만들거나 복원하지 않는다.
