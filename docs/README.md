# 문서 안내 (`docs/`)

| 위치 | 내용 |
| :--- | :--- |
| [`PRINCIPLES.md`](PRINCIPLES.md) | 구현이 바뀌어도 유지되는 규칙. 어기면 버그다. 원칙 간 충돌과 알려진 위반도 여기에 적는다. |
| [`implementation/`](implementation/) | 현재 코드와 일치하는 동작 명세, 계약, 운영 문서. 치명적이지 않은 구현 상태는 [`IMPLEMENTATION_STATUS.md`](implementation/IMPLEMENTATION_STATUS.md)에 있다. |
| [`design/`](design/) | 작성 당시의 배경, 대안, 결정 기록. 현재 상태를 말하지 않는다. 현재 동작은 `implementation/`이나 코드로 확인한다. |

[`ACCOUNT_IDP_AND_FEDIFY_SIDECAR_INTEGRATION_DESIGN.md`](design/ACCOUNT_IDP_AND_FEDIFY_SIDECAR_INTEGRATION_DESIGN.md)는 소유자가 승인한 설계다(2026-11-10). 구현 범위는 FP1(내부 연합 API와 탐색)이고, 현재 동작은 [`FEDERATION.md`](implementation/FEDERATION.md)에 있다. 팔로워(FP2)와 발행(FP3)은 구현하지 않았다.

## 문서 방침

목적: 에이전트가 변경 작업을 받으면 불필요한 분석 없이 바로 착수할 수 있게 한다. 이를 위해 "지금 어떻게 동작하는가"와 "알고 시작해야 할 한계"를 항상 정해진 자리에 둔다.

1. **현재 동작은 `implementation/`에만 둔다.** 코드를 바꾸는 커밋이 같은 커밋에서 해당 문서(계약, OpenAPI, 가이드, 용어집, 엔진 문서 등)를 고친다. 새 문서를 만들기 전에 기존 문서에 들어갈 자리가 있는지 먼저 찾는다.
2. **치명적이지 않은 구현 상태는 [`IMPLEMENTATION_STATUS.md`](implementation/IMPLEMENTATION_STATUS.md)에 둔다.** 알려진 제약, 소유자가 보류한 결정, 이미 실패하는 테스트가 대상이다. 영역마다 코드 위치를 함께 적는다. 해결하면 같은 커밋에서 지운다.
3. **보안 문제는 문서에 적지 않는다.** 소유자에게 직접 보고한다.
4. **`design/`은 구현 전에 소유자가 설계를 요구할 때만 만든다.** 구현이 끝나면 현재 동작은 `implementation/`으로, 남은 한계는 `IMPLEMENTATION_STATUS.md`로 옮긴다. 결정 경위는 커밋 메시지에 남긴다. 구현을 마친 뒤 같은 내용으로 설계 문서를 따로 만들지 않는다.
5. 근거 태그(`[코드]`, `[실측]`, `[결정]`, `[미검증]`)를 쓴다. 확인하지 않은 원인은 `[미검증]`으로 적는다.
6. 문서 목록이나 소개 문구를 따로 관리하지 않는다. 문서는 디렉터리와 각 문서의 첫머리로 찾는다.

## 문서 정합성 검사

`tests/test_docs_consistency.py`가 `docs/*.md`와 `docs/implementation/*.md`의 상대 링크, 본문에 적힌 `src/`·`tests/` 등의 경로, `ENVIRONMENT_VARIABLES.md`와 `Settings`의 변수 집합·기본값을 검사한다. `design/`은 검사하지 않는다.
