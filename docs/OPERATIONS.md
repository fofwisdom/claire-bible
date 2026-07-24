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
- 이번 운영 범위에서 제외한 기존 명령: `backup`, `backup-loop`

구현 조사나 긴급 복구처럼 raw 앱 명령이 반드시 필요하면 명령 바로 뒤에
`--advanced`를 명시할 수 있다.

```bash
./cb-manuscript app --advanced <claire-command> [args...]
```

이는 차단만 해제하는 전문가용 탈출구다. 인스턴스 잠금은 유지하지만 이미 실행 중인
서비스를 중지하지 않으며, migration 순서, 데이터 백업 또는 복구 가능성을 보장하지
않는다. 특히 기존 `backup` 명령에 접근할 수 있다는 사실을 지원되는 백업 workflow로
간주하지 않는다.

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

백업·복원은 현재 통합 운영 명령의 범위가 아니다. 기존 앱 내부 구현 여부와 관계없이
새로운 `cb-manuscript` 운영 명령으로 간주하거나 문서화하지 않으며, 별도 설계에서
보존 정책과 복구 검증을 처음부터 정한다.
