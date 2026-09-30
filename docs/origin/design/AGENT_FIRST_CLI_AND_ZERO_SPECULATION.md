# Agent-First CLI & Zero Speculation Architecture

## 1. 목적 (Goal)
이 문서는 Claire Bible 프로젝트가 AI 에이전트(Antigravity, Cursor, Claude Code 등)에 의해 CLI 환경에서 예측 가능하고 신뢰성 있게 호출/조작될 수 있도록 하는 아키텍처 설계와 할루시네이션(Hallucination) 방지 원칙을 규정합니다.

## 2. Agent-First CLI Contract
`src/claire/api/agent.py`의 `with_agent_contract`를 통해 달성되는 핵심 CLI 계약입니다.
* **Non-Interactive by Default**: `--json` 모드(에이전트 모드)에서는 `input()` 호출 시 즉시 런타임 에러(Exit 4: Precondition Failed)를 던져 무한 대기(hang)를 방지합니다.
* **Stream Separation (Clean Stdout)**: 에이전트는 기계 판독 가능한 정형 데이터만 필요로 합니다. `--json` 시 인간을 위한 안내(print) 및 로그는 자동으로 `stderr`로 라우팅되고, `stdout`에는 오직 Pydantic 검증을 통과한 `AgentOutput` 구조의 JSON만 출력됩니다.

## 3. Zero Speculation (할루시네이션 방지 원칙)
인간을 위한다는 명목으로 코드나 에이전트가 임의의 가정을 하지 않도록 강제합니다.

1. **Fail-Fast & Explicit**: 누락되거나 모호한 인자가 있을 때, "가장 가까운 값"으로 유추(Guessing)하지 않습니다. 즉각적인 Exit Code (2: Invalid Arguments)와 구체적 에러 메시지로 실패해야 합니다.
2. **Pydantic Strict Sealing**: 입출력 스키마는 `ConfigDict(extra="forbid", strict=True)`로 봉인되어, 에이전트나 코드가 임의의 메타데이터나 합성 필드를 추가하는 행위를 원천 차단합니다.
3. **No Hidden Fallbacks**: 외부 서비스 연동 실패 시 빈 데이터나 Mock을 반환하여 파이프라인을 오염시키지 않고 명확히 실패(Exit 5) 처리합니다.
