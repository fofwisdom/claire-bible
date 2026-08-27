# Documentation Rules for Claire Bible

## 문서 저장 및 관리 규칙

AI 에이전트와 개발자는 프로젝트 관련 문서를 작성하거나 저장할 때 다음 규칙을 반드시 준수해야 합니다.

1. **업스트림과 오리진 분리**:
   - 업스트림(`blackan/claire_bible`) 원본 문서는 `docs/upstream/`에 보존하며, 직접 수정하지 않습니다.
   - 오리진(`fofwisdom/claire-bible`)에서 신규 작성하거나 개정/병합하는 모든 문서는 `docs/origin/` 하위에 배치합니다.
   - `docs/` 최상위 디렉터리에는 전체 인덱스(`docs/README.md`) 외에 개별 문서를 직접 저장하지 않습니다.

2. **오리진 설계 내역 (`docs/origin/design/`)**:
   - 아키텍처 구상, 기능 기획/재설계, 알고리즘 및 파이프라인 설계, 시스템 설계 아티클, 기술 조사/리서치 자료 등은 반드시 `docs/origin/design/` 디렉터리에 저장합니다.
   - 예시: `docs/origin/design/DATA_LIFECYCLE_AND_PURGE_DESIGN.md`, `docs/origin/design/DUAL_FORMAT_ADOC_DESIGN.md`, `docs/origin/design/MULTI_PROVIDER_DESIGN.md`

3. **오리진 구현 및 운영 내역 (`docs/origin/implementation/`)**:
   - 실제 코드베이스에 구현된 기능의 동작 명세, 호스트 및 컨테이너 운영 가이드, 네트워크/인증/CORS 설정 명세, 배포 가이드는 반드시 `docs/origin/implementation/` 디렉터리에 저장합니다.
   - 예시: `docs/origin/implementation/EXTERNAL_ACCESS.md`, `docs/origin/implementation/OPERATIONS.md`, `docs/origin/implementation/COMMANDS.md`

4. **링크 및 상대 경로**:
   - `docs/origin/design/` 또는 `docs/origin/implementation/`에서 프로젝트 루트 문서를 참조할 때는 `../../../<FILE>` 형식을 사용합니다.
   - 문서 간 링크가 깨지지 않도록 상대 경로를 정확히 유지합니다.
