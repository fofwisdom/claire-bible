# Development & Build Workflow Rules for Claire Bible

에이전트는 이 프로젝트에서 코드 수정(기능 개발, 버그 수정, 리팩토링 등) 작업을 완료할 때마다 응답을 마치기 전 다음 워크플로를 **반드시 자동으로 순서대로 실행**해야 합니다.

---

## 1. 테스트 및 CI 게이트 검증 (필수)
코드 변경 후 즉시 CI 스크립트를 실행하여 구문 검사, Compose 모델 일관성, `uv.lock` 동기화, 전체 단위/통합 테스트를 검증합니다.
```bash
bash ./scripts/ci.sh
```
- CI 검사가 실패하면 문제를 즉시 수정하고 다시 실행하여 반드시 통과시킵니다.

---

## 2. 개발 서버 네트워크 설정 및 IP/호스트네임 확인
- 개발 환경 설정 파일(`.env.dev`)의 `CB_API_BIND`와 `CLAIRE_PUBLIC_URL`이 올바르게 설정되어 있는지 확인합니다.
- `CB_API_BIND`는 `ops/cb_manuscript.py` 규칙에 따라 반드시 **단일 IPv4 주소**(예: 호스트 LAN IP 또는 `127.0.0.1`)여야 하며, `CLAIRE_PUBLIC_URL`은 `http://<CB_API_BIND>:<CB_API_PORT>/`와 형식 및 authority가 일치해야 합니다.
- 만약 호스트 IP가 변경되었거나 사용자가 특정 IP/호스트네임으로 빌드하기를 원할 경우, `.env.dev`의 값을 맞추어 갱신합니다.

---

## 3. 개발 서버 빌드 및 컨테이너 기동
- 수정된 코드가 반영되도록 개발 환경 컨테이너 이미지를 빌드하고 백그라운드로 실행합니다:
```bash
./cb-manuscript dev up --build -d
```
- 또는 소스 업데이트 동기화가 필요한 경우:
```bash
./cb-manuscript dev update --no-fetch
```

---

## 4. 서비스 헬스체크 및 최종 보고
- 컨테이너가 정상적으로 동작하는지 liveness/health를 확인합니다:
```bash
./cb-manuscript dev health
```
- 사용자에게 작업 완료를 보고할 때 다음 사항을 반드시 포함합니다:
  1. CI/테스트 통과 결과
  2. 개발 서버 빌드 및 실행 상태
  3. 접속 가능한 개발 서버 웹 UI 주소 (예: `http://<CB_API_BIND>:<CB_API_PORT>/`)
