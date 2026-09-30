import builtins
import io
import json
import sys
from functools import wraps
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict


class AgentOutput(BaseModel):
    """에이전트가 CLI를 호출했을 때 반환되는 표준 기계 판독용 응답 구조."""
    model_config = ConfigDict(extra="forbid", strict=True)
    status: str
    exit_code: int
    data: dict[str, Any] | list[Any] | None = None
    error: str | None = None
    error_detail: str | None = None


def with_agent_contract(func: Callable) -> Callable:
    """CLI 서브커맨드를 에이전트 친화적(Agent-first)으로 실행하는 래퍼.
    
    1. --json 옵션이 켜져있으면 stdout을 캡처하여 기존 명령어의 JSON 출력을 보존하고 일반 텍스트는 stderr로 격리.
    2. 모든 대화형 프롬프트(input)는 에이전트 환경에서 예외를 던지도록 오버라이드.
    3. 최종 결과를 AgentOutput Pydantic 모델로 검증 후 stdout에 단일 JSON 출력.
    """
    @wraps(func)
    def wrapper(args) -> int:
        is_json = getattr(args, "json", False)

        # Disable interactive input if --json is passed.
        original_input = builtins.input

        def non_interactive_input(prompt: str = "") -> str:
            if is_json:
                raise RuntimeError(
                    f"Interactive input is disabled in agent/JSON mode. Use --yes or appropriate flags. Prompt was: {prompt}"
                )
            return original_input(prompt)

        builtins.input = non_interactive_input

        exit_code = 0
        error_msg = None
        data = None

        try:
            if is_json:
                # Capture stdout to distinguish between structured JSON output and human-readable text
                captured_stdout = io.StringIO()
                original_stdout = sys.stdout
                sys.stdout = captured_stdout

                try:
                    result = func(args)
                finally:
                    sys.stdout = original_stdout

                raw_output = captured_stdout.getvalue().strip()

                # CLI 함수들이 dict를 직접 리턴한 경우 우선 적용
                if isinstance(result, dict):
                    data = result
                    exit_code = 0
                elif isinstance(result, int):
                    exit_code = result
                else:
                    exit_code = 0

                # 만약 출력된 내용이 유효한 JSON이면 data로 채택
                if raw_output:
                    try:
                        parsed_json = json.loads(raw_output)
                        if data is None:
                            data = parsed_json
                    except json.JSONDecodeError:
                        # JSON이 아닌 일반 텍스트 출력은 stderr로 라우팅
                        sys.stderr.write(captured_stdout.getvalue())
            else:
                # 일반 모드 (인간 사용자)
                result = func(args)
                if isinstance(result, int):
                    return result
                return 0

        except RuntimeError as e:
            if is_json:
                exit_code = 4  # Precondition failed (e.g. interactive prompt blocked)
                error_msg = str(e)
            else:
                print(f"[오류] {e}", file=sys.stderr)
                return 4
        except ValueError as e:
            if is_json:
                exit_code = 2  # Invalid arguments
                error_msg = str(e)
            else:
                print(f"[오류] {e}", file=sys.stderr)
                return 2
        except Exception as e:
            if is_json:
                exit_code = 5  # Provider/Internal error
                error_msg = f"{type(e).__name__}: {str(e)}"
            else:
                raise
        finally:
            builtins.input = original_input

        if is_json:
            if exit_code != 0 and error_msg is None:
                # If command exited with failure but threw no exception
                error_msg = f"Command failed with exit code {exit_code}"
                if isinstance(data, dict) and "error" in data:
                    error_msg = str(data["error"])

            output = AgentOutput(
                status="success" if exit_code == 0 else "error",
                exit_code=exit_code,
                data=data,
                error=error_msg,
            )
            # 순수 JSON 결과만 stdout으로 출력
            print(output.model_dump_json(exclude_none=True))
            return exit_code

    return wrapper
