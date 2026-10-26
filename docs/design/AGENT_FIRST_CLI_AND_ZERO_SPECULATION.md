# Agent-First CLI & Zero Speculation Architecture

## 1. 목적 (Goal)
이 문서는 Claire Bible 프로젝트가 AI 에이전트(Antigravity, Cursor, Claude Code 등)에 의해 CLI 환경에서 예측 가능하고 신뢰성 있게 호출/조작될 수 있도록 하는 아키텍처 설계와 할루시네이션(Hallucination) 방지 원칙을 규정한다.

## 2. Agent-First CLI Contract
`src/claire/api/agent.py`의 `with_agent_contract`를 통해 달성되는 핵심 CLI 계약이다.
* **Non-Interactive by Default**: `--json` 모드(에이전트 모드)에서는 `input()` 호출 시 즉시 런타임 에러(Exit 4: Precondition Failed)를 던져 무한 대기(hang)를 방지한다.
* **Stream Separation (Clean Stdout)**: 에이전트는 기계 판독 가능한 정형 데이터만 필요로 한다. `--json` 시 인간을 위한 안내(print) 및 로그는 자동으로 `stderr`로 라우팅되고, `stdout`에는 오직 Pydantic 검증을 통과한 `AgentOutput` 구조의 JSON만 출력된다.

## 3. Zero Speculation (할루시네이션 방지 원칙)
인간을 위한다는 명목으로 코드나 에이전트가 임의의 가정을 하지 않도록 강제한다.

1. **Fail-Fast & Explicit**: 누락되거나 모호한 인자가 있을 때, "가장 가까운 값"으로 유추(Guessing)하지 않는다. 즉각적인 Exit Code (2: Invalid Arguments)와 구체적 에러 메시지로 실패해야 한다.
2. **Pydantic Strict Sealing**: 입출력 스키마는 `ConfigDict(extra="forbid", strict=True)`로 봉인되어, 에이전트나 코드가 임의의 메타데이터나 합성 필드를 추가하는 행위를 원천 차단한다.
3. **No Hidden Fallbacks**: 외부 서비스 연동 실패 시 빈 데이터나 Mock을 반환하여 파이프라인을 오염시키지 않고 명확히 실패(Exit 5) 처리한다.

## 4. 에이전트 명령어 인식 및 실행 표면 규약 (Execution Surface Recognition)
AI 에이전트가 운영자에게 커맨드를 안내하거나 직접 실행할 때 준수해야 하는 엄격한 실행 표면 규약이다.

1. **호스트 OS 실행 표면 엄수**:
   - 프로덕션 배포 환경(`root@...:~#` 등)의 호스트 OS PATH에는 `claire` 바이너리가 존재하지 않는다.
   - 따라서 에이전트는 사용자에게 명령어를 안내할 때 절대로 `claire <cmd>`를 단독으로 제시해서는 안 되며, 반드시 호스트 래퍼인 **`./cb-manuscript app <cmd>`** (One-off 작업) 또는 **`./cb-manuscript <cmd>`** (호스트 오케스트레이션)로 제시해야 한다.
2. **명령어 이름 추측(Speculation) 엄격 차단**:
   - "인박스 정리", "DB 수리" 등의 일상적 맥락을 바탕으로 `repair`, `clean-inbox` 등의 존재하지 않는 CLI 명령어를 임의로 합성하거나 추측하여 제시하지 않는다.
   - **인박스 실패 복구**: `./cb-manuscript app recover-run` (지수 백오프 도래분 재처리), `./cb-manuscript app replay-failed` (즉시 전량 재적재)
   - **지식그래프 무결성 수복**: `./cb-manuscript app doctor --heal` 또는 `./cb-manuscript app audit --heal`
   - **큐 및 인박스 상태 조회**: `./cb-manuscript app queue status` 또는 `./cb-manuscript app queue list inbox`
3. **Health 진단의 2가지 경로 명시**:
   - 호스트 컨테이너 생존 확인(Liveness): `./cb-manuscript health`
   - 애플리케이션 진단(Degraded 원인 분석): `./cb-manuscript app health`
