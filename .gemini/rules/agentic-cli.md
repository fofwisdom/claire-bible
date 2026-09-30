<RULE[agentic-cli]>
# Claire Bible: Zero-Speculation & Agent-First CLI Guidelines

1. **Zero Speculation (추론적 편의/가정 금지)**
   - 사용자가 명시적으로 요구하지 않은 '편의 기능', '임의의 기본값(Fallback)', '데이터 보정/합성' 코드를 작성하지 마세요. (YAGNI 엄수)
   - 필수 인자나 문맥이 누락/모호할 경우, "알아서 유추"하지 말고 즉시 `ValueError` 또는 구체적 에러 메시지와 함께 Fail-Fast 하도록 작성하세요.

2. **Agent-First CLI Contract**
   - CLI 출력은 기본적으로 에이전트 파싱을 고려해야 합니다. 모든 명령어는 `--json` 모드 지원 및 비대화형(`--yes`, `-y`) 무인 실행이 가능해야 합니다.
   - Pydantic 모델 반환 시 항상 `extra="forbid", strict=True`를 설정하여 스키마 외의 필드가 생성(Hallucination)되는 것을 방지하세요.
</RULE[agentic-cli]>
