# Documentation Rules for Claire Bible

## 문서 저장 및 관리 규칙

AI 에이전트와 개발자는 프로젝트 관련 문서를 작성하거나 저장할 때 다음 규칙을 반드시 준수해야 합니다.

1. **설계 내역과 구현 내역 분리**:
   - `docs/` 최상위 디렉터리에 문서를 직접 저장하지 않습니다 (`docs/README.md` 제외).
   - 모든 문서는 성격에 따라 `docs/design/` 또는 `docs/implementation/` 하위에 배치합니다.

2. **설계 내역 (`docs/design/`)**:
   - 아키텍처 구상, 기능 기획/재설계, 알고리즘 및 파이프라인 설계, 시스템 설계 아티클, 기술 조사/리서치 자료 등은 반드시 `docs/design/` 디렉터리에 저장합니다.
   - 예시: `docs/design/ONEHOP_MERGE_DESIGN.md`, `docs/design/SYNTHESIS_REDESIGN.md`, `docs/design/search.md`

3. **구현 및 운영 내역 (`docs/implementation/`)**:
   - 실제 코드베이스에 구현된 기능의 동작 명세, 호스트 및 컨테이너 운영 가이드, 네트워크/인증/CORS 설정 명세, 배포 가이드는 반드시 `docs/implementation/` 디렉터리에 저장합니다.
   - 예시: `docs/implementation/EXTERNAL_ACCESS.md`, `docs/implementation/OPERATIONS.md`

4. **링크 및 상대 경로**:
   - 프로젝트 루트 문서를 참조할 때는 `../../<FILE>` 형식을 사용합니다.
   - 문서 간 링크가 깨지지 않도록 상대 경로를 정확히 유지합니다.
