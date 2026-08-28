"""실시간 진행률 및 세부 단계 추적, 사용자 중단(Ctrl+C) 컨텍스트 보고 모듈.

장시간 실행되는 배치 작업(regenerate, reextract, backfill, queue drain 등)에서
전체 진행률, 현재 대상 문서, 세부 실행 단계(LLM 추출, 본문 렌더링, 엔티티 해소/판정,
그래프 적재 등)를 실시간으로 터미널에 표시하고, 예기치 않은 중단(SIGINT/오류) 시
정확한 중단 위치와 잔여 현황, 재개 가이드를 제공합니다.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
import sys
import time
from typing import Callable, Iterator


def _format_duration(seconds: float) -> str:
    """초 단위를 사람이 읽기 쉬운 문자열(예: '1m 23s', '45s')로 변환."""
    if seconds < 0:
        seconds = 0
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h}h {m:02d}m {s:02d}s"
    if m > 0:
        return f"{m}m {s:02d}s"
    return f"{seconds:.1f}s"


@dataclass
class ItemContext:
    """단일 항목(문서 또는 큐 레코드)의 실행 컨텍스트."""

    index: int
    total: int
    item_id: str
    title: str = ""
    url: str = ""
    current_stage: str = "시작"
    sub_detail: str = ""
    start_time: float = field(default_factory=time.time)
    end_time: float | None = None
    status: str = "running"  # running | ok | failed | skipped
    error: str | None = None


class ProgressReporter:
    """실시간 작업 진행률 및 세부 단계 터미널 리포터."""

    def __init__(
        self,
        task_name: str,
        total: int,
        *,
        enabled: bool = True,
        stream=sys.stderr,
    ) -> None:
        self.task_name = task_name
        self.total = max(0, total)
        self.enabled = enabled
        self.stream = stream

        self.start_time = time.time()
        self.current_item: ItemContext | None = None
        self.completed_count = 0
        self.failed_count = 0
        self.skipped_count = 0
        self.items_history: list[ItemContext] = []

    @property
    def is_interactive(self) -> bool:
        return bool(self.enabled and hasattr(self.stream, "isatty") and self.stream.isatty())

    def print_banner(self, description: str = "") -> None:
        """작업 시작 배너 출력."""
        if not self.enabled:
            return
        desc_str = f" - {description}" if description else ""
        print(f"\n🚀 [{self.task_name}] 총 {self.total}건 작업 시작{desc_str}", file=self.stream, flush=True)
        print("=" * 64, file=self.stream, flush=True)

    @contextmanager
    def item(
        self,
        index: int,
        item_id: str,
        title: str = "",
        url: str = "",
    ) -> Iterator[Callable[[str, str], None]]:
        """단일 항목 처리 컨텍스트 매니저.

        yield 로 단계 보고 콜백 ``on_step(stage, detail)`` 을 제공합니다.
        """
        ctx = ItemContext(
            index=index,
            total=self.total,
            item_id=item_id,
            title=title or "(제목 없음)",
            url=url,
        )
        self.current_item = ctx
        pct = (index / self.total * 100) if self.total > 0 else 100.0

        if self.enabled:
            elapsed = time.time() - self.start_time
            eta_str = ""
            if index > 1 and self.completed_count > 0:
                avg_time = elapsed / self.completed_count
                rem_time = avg_time * (self.total - index + 1)
                eta_str = f" | 잔여예상: {_format_duration(rem_time)}"

            title_preview = ctx.title[:35] + ("…" if len(ctx.title) > 35 else "")
            print(
                f"\n▶ [{index}/{self.total}] ({pct:5.1f}%) {ctx.item_id} | {title_preview}{eta_str}",
                file=self.stream,
                flush=True,
            )

        def on_step(stage: str, detail: str = "") -> None:
            ctx.current_stage = stage
            ctx.sub_detail = detail
            if self.enabled:
                detail_str = f" ({detail})" if detail else ""
                print(f"   ↳ {stage}{detail_str}", file=self.stream, flush=True)

        try:
            yield on_step
            ctx.status = "ok"
            ctx.end_time = time.time()
            self.completed_count += 1
            if self.enabled:
                item_duration = _format_duration(ctx.end_time - ctx.start_time)
                print(f"   ✅ 완료 ({item_duration})", file=self.stream, flush=True)
        except KeyboardInterrupt:
            ctx.status = "interrupted"
            ctx.end_time = time.time()
            self.items_history.append(ctx)
            raise
        except Exception as e:
            ctx.status = "failed"
            ctx.error = str(e)
            ctx.end_time = time.time()
            self.failed_count += 1
            self.items_history.append(ctx)
            if self.enabled:
                print(f"   ❌ 실패: {e}", file=self.stream, flush=True)
            raise
        else:
            self.items_history.append(ctx)
            self.current_item = None

    def print_summary(self, extra_message: str = "") -> None:
        """전체 작업 정상 완료 요약 출력."""
        if not self.enabled:
            return
        total_time = _format_duration(time.time() - self.start_time)
        print("=" * 64, file=self.stream, flush=True)
        print(f"✨ [{self.task_name}] 작업 완료 (총 소요: {total_time})", file=self.stream, flush=True)
        print(
            f"• 처리 결과 : 전체 {self.total}건 중 완료 {self.completed_count}건 "
            f"· 실패 {self.failed_count}건 · 건너뜀 {self.skipped_count}건",
            file=self.stream,
            flush=True,
        )
        if extra_message:
            print(f"• 추가 정보 : {extra_message}", file=self.stream, flush=True)
        print("=" * 64, file=self.stream, flush=True)

    def format_interruption_report(self, reason: str = "사용자 중단 (Ctrl+C / SIGINT)") -> str:
        """중단 시점의 위치와 잔여 현황, 재개 가이드가 담긴 보고서 텍스트 생성."""
        elapsed = _format_duration(time.time() - self.start_time)
        ctx = self.current_item

        lines: list[str] = [
            "\n" + "=" * 64,
            f"🛑 [{self.task_name}] 작업이 중단되었습니다 ({reason})",
            "=" * 64,
            f"• 진행 상황     : {self.completed_count}/{self.total}건 완료 "
            f"({(self.completed_count / self.total * 100) if self.total else 0:.1f}%) · 소요 시간: {elapsed}",
        ]

        if ctx:
            lines.append(f"• 중단된 문서   : {ctx.item_id}")
            if ctx.title:
                lines.append(f"  - 제목        : {ctx.title}")
            if ctx.url:
                lines.append(f"  - URL         : {ctx.url}")
            lines.append(f"  - 실행 중 단계: {ctx.current_stage}")
            if ctx.sub_detail:
                lines.append(f"  - 세부 상태   : {ctx.sub_detail}")

        rem = max(0, self.total - self.completed_count)
        lines.append(f"• 잔여 대상     : {rem}건 미처리")
        lines.append("• 데이터 보존   : 이미 완료된 항목은 SQLite 및 Vault에 정상 커밋되어 보존되었습니다.")
        lines.append("-" * 64)
        lines.append("💡 [재개 및 상태 확인 안내]")
        lines.append("  1. 현재 DB 및 큐 전체 현황 확인:")
        lines.append("     ./cb-manuscript app status")
        lines.append("     ./cb-manuscript app queue status")
        lines.append("  2. 미완료 문서 또는 특정 컴포넌트 재생성 재개:")
        lines.append("     ./cb-manuscript app regenerate --tables --all --apply")
        lines.append("=" * 64 + "\n")

        return "\n".join(lines)


@contextmanager
def track_batch_progress(
    task_name: str,
    total: int,
    *,
    enabled: bool = True,
    stream=sys.stderr,
) -> Iterator[ProgressReporter]:
    """배치 작업용 상위 컨텍스트 매니저.

    KeyboardInterrupt 를 캡처하여 상세 보고서를 출력하고 다시 raise 합니다.
    """
    reporter = ProgressReporter(task_name=task_name, total=total, enabled=enabled, stream=stream)
    reporter.print_banner()
    try:
        yield reporter
    except KeyboardInterrupt:
        report_text = reporter.format_interruption_report(reason="사용자 중단 (Ctrl+C / SIGINT)")
        print(report_text, file=stream, flush=True)
        raise
    except Exception as exc:
        report_text = reporter.format_interruption_report(reason=f"오류 발생: {exc}")
        print(report_text, file=stream, flush=True)
        raise
