<RULE[agentic-cli]>
# Claire Bible: Zero-Speculation & Agent-First CLI Guidelines

1. **Zero Speculation (추론적 편의/가정 금지)**
   - 사용자가 명시적으로 요구하지 않은 '편의 기능', '임의의 기본값(Fallback)', '데이터 보정/합성' 코드를 작성하지 마세요. (YAGNI 엄수)
   - 필수 인자나 문맥이 누락/모호할 경우, "알아서 유추"하지 말고 즉시 `ValueError` 또는 구체적 에러 메시지와 함께 Fail-Fast 하도록 작성하세요.

2. **Agent-First CLI Contract**
   - CLI 출력은 기본적으로 에이전트 파싱을 고려해야 합니다. 모든 명령어는 `--json` 모드 지원 및 비대화형(`--yes`, `-y`) 무인 실행이 가능해야 합니다.
   - Pydantic 모델 반환 시 항상 `extra="forbid", strict=True`를 설정하여 스키마 외의 필드가 생성(Hallucination)되는 것을 방지하세요.

3. **Execution Surface Clarity (실행 환경 및 진입점 명확화)**
   - 호스트 환경(배포 서버)에는 `claire` 바이너리가 직접 존재하지 않습니다. 사용자에게 안내하거나 명령어를 제시할 때 절대로 `claire <cmd>`를 단독으로 제시하지 마세요.
   - 호스트에서 실행할 명령어는 항상 `./cb-manuscript app <cmd>` (애플리케이션 one-off) 또는 `./cb-manuscript <cmd>` (호스트 수명주기) 형식으로 안내하세요.
   - `repair` 등 존재하지 않는 명령어를 추측하지 마세요.
     - 인박스 실패 복구: `./cb-manuscript app recover-run` (도래분 백오프 재시도), `./cb-manuscript app replay-failed` (즉시 전량 재적재)
     - 큐 상태 조회: `./cb-manuscript app queue status` 또는 `./cb-manuscript app queue list inbox`
     - 지식그래프 무결성 수복: `./cb-manuscript app doctor --heal` 또는 `./cb-manuscript app audit --heal`
     - 컨테이너 Liveness 확인: `./cb-manuscript health`, 전체 애플리케이션 건강 진단: `./cb-manuscript app health`
</RULE[agentic-cli]>
