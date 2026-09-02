# 비디오 음성 자막(전사) 생성 및 지식 적재 파이프라인 설계 (`VIDEO_AUDIO_TRANSCRIPTION_AND_INGESTION_DESIGN.md`)

> **상태**: 구현 및 검증 완료 (Implemented & Verified)  
> **대상 플랫폼 예시**: VMware Explore Video (`https://www.vmware.com/explore/video/6403821753112`)  
> **적용 모듈**: `claire.ingest.fetchers.video`, `claire.extract.transcript`, `claire.config`, `claire.ingest.router`

---

## 1. 개요 및 배경

기존 Claire 인제스트 시스템은 텍스트 중심 웹페이지(`fetch_web`), PDF 문서(`fetch_pdf`), 그리고 공식 자막 API가 제공되는 YouTube(`fetch_youtube` via `youtube-transcript-api`)를 지원합니다.

그러나 기술 세미나, 컨퍼런스(예: VMware Explore, AWS re:Invent), 엔터프라이즈 미디어 포털 등의 웹 비디오 플랫폼은 다음과 같은 특성을 가집니다:
1. **HTML 본문 부재**: 웹페이지 내에 발표 본문 텍스트가 거의 없고, 세션 제목과 짧은 설명문만 존재합니다.
2. **내장 텍스트 자막 부재**: 플레이어 매니페스트에 썸네일 탐색용 VTT(`thumbnail.webvtt`)만 포함되어 있을 뿐, 실제 음성 전사(Closed Caption) 텍스트가 제공되지 않습니다.
3. **음성 트랙 기반 지식화 필수**: 영상의 오디오 스트림에서 음성을 추출하여 고품질 전사(STT)를 수행한 뒤 지식 그래프로 적재해야 합니다.

본 문서는 **웹 비디오의 오디오 추출 $\rightarrow$ 다중 프로바이더 기반 자막 생성 $\rightarrow$ Claire 지식 베이스 적재 파이프라인**을 설계하고, **Gemini / Antigravity 환경에서의 모델 자원 소모율을 정밀 예측**합니다.

---

## 2. 대상 영상 실측 분석 (`VMware Explore 6403821753112`)

* **URL**: `https://www.vmware.com/explore/video/6403821753112`
* **세션명**: *Introducing VMware AI Factory: The Software-Defined Foundation of VMware Private AI Cloud* (세션 코드: `CLOB1244LV`)
* **플랫폼 아키텍처**: Broadcom / Brightcove Video Cloud 기반
  * Account ID: `6164421911001`
  * Video ID: `6403821753112`
* **영상 재생 시간**: **2,609.152초 (43분 29.15초)**
* **기존 자막 상태**: `text_tracks`에 오직 썸네일 미리보기 트랙만 존재하며, **음성 텍스트 자막이 0건**임을 확인.
* **미디어 스트림**:
  * HLS 매니페스트: `https://manifest.prod.boltdns.net/manifest/v1/hls/...`
  * 서명된 MP4 스트림: `https://fastly-signed-us-east-1-prod.brightcovecdn.com/...`

---

## 3. 전체 아키텍처 설계

시스템은 **(1) 미디어 추출**, **(2) 플러그형 전사(STT) 프로바이더**, **(3) 지식 그래프 인제스트**의 3단계로 구성됩니다.

```mermaid
flowchart TD
    A[입력 URL / 미디어 소스] --> B[ingest/router.py]
    B -->|비디오 플랫폼/스트림 감지| C[ingest/fetchers/video.py]
    
    subgraph S1 [1. 미디어 & 오디오 추출]
        C --> D[Brightcove / YouTube / HTML5 Stream Resolver]
        D --> E[ffmpeg 경량 모노 오디오 변환<br>16kHz AAC/MP3 ~10MB]
    end
    
    subgraph S2 [2. Pluggable Transcript Provider Layer]
        E --> F[TranscriptProvider Protocol]
        F --> G[AntigravityTranscriptProvider<br>Gemini 3.7 / 2.5 Flash]
        F -.-> H[WhisperTranscriptProvider<br>OpenAI / Groq]
        F -.-> I[GoogleCloudSTTProvider<br>Chirp v2]
        F -.-> J[LocalWhisperProvider<br>faster-whisper]
    end
    
    subgraph S3 [3. Claire Ingest & Graph Pipeline]
        G & H & I & J --> K[TranscriptResult<br>전사 텍스트 + 타임스탬프]
        K --> L[Document source_type=video]
        L --> M[extract_resolve_store<br>엔티티/관계/요약/가독 본문 생성]
        M --> N[(SQLite DB & Obsidian Vault)]
    end
```

---

## 4. 컴포넌트별 상세 설계

### 4.1. 전사 프로바이더 추상 인터페이스 (`TranscriptProvider`)

전략 패턴(Strategy Pattern)을 적용하여 초기에는 Antigravity CLI를 사용하고, 이후 설정값(`CLAIRE_STT_PROVIDER`) 하나로 다양한 외부 프로바이더로 교체할 수 있도록 설계합니다.

```python
from __future__ import annotations
from pathlib import Path
from typing import Protocol
from pydantic import BaseModel, Field

class TranscriptSegment(BaseModel):
    start_sec: float
    end_sec: float
    text: str

class TranscriptResult(BaseModel):
    full_text: str
    segments: list[TranscriptSegment] = Field(default_factory=list)
    language: str = "en"
    duration_sec: float = 0.0
    provider: str = ""
    model: str = ""

class TranscriptProvider(Protocol):
    name: str

    def transcribe(
        self,
        audio_path: str | Path,
        *,
        language: str | None = None,
        timestamps: bool = True,
    ) -> TranscriptResult:
        """오디오 파일을 입력받아 전체 텍스트 및 타임스탬프 자막 세그먼트를 반환한다."""
        ...
```

### 4.2. 프로바이더 구현체 계획

1. **`AntigravityTranscriptProvider` (초기 기본 구현)**:
   * `agy` CLI 또는 Google GenAI SDK의 네이티브 오디오 멀티모달 기능을 활용합니다.
   * 프롬프트: 전문 IT 용어(VCF, vSAN, GPU, Kubernetes 등) 보존 지침 및 타임스탬프 포맷 강제.
   * 장점: 별도의 외부 STT 서비스 결제나 API 키 추가 없이 현재 계정 인프라에서 즉시 실행 가능.
2. **`WhisperTranscriptProvider` (확장 - OpenAI / Groq)**:
   * Groq Whisper v3 (분당 초고속 처리, 극저비용) 또는 OpenAI Whisper API 연동.
3. **`GoogleCloudSTTProvider` (확장 - Google Cloud Speech-to-Text v2)**:
   * 대규모 엔터프라이즈 배치 전사 및 실시간 스트리밍 지원.
4. **`LocalWhisperProvider` (확장 - 에어갭/로컬 처리)**:
   * `faster-whisper` 기반 CTranslate2 로컬 GPU/CPU 추론.

### 4.3. 미디어 스트림 리졸버 아키텍처 (`MediaStreamResolver`)

웹페이지 URL로부터 실제 재생 가능한 오디오/비디오 스트림 URL 및 영상 메타데이터를 정밀하게 추출하는 전용 리졸버 체인을 구성합니다.

```
 [ URL 입력 ]
      │
      ▼
┌──────────────────────────────────────────────────────────────┐
│ MediaStreamResolver Registry (우선순위 기반 체인)                  │
├──────────────────────────────────────────────────────────────┤
│ 1. BrightcoveResolver: VMware Explore / Brightcove Video Cloud │
│    - 페이지 스크립트에서 Account ID, Video ID, Policy Key 추출   │
│    - Playback API (/playback/v1/accounts/.../videos/...) 호출 │
│    - 최적 MP4/HLS 스트림 URL, 공식 세션명, 설명문, 썸네일 확보     │
├──────────────────────────────────────────────────────────────┤
│ 2. YouTubeAudioResolver: 유튜브 자막 부재 영상 폴백               │
│    - youtube-transcript-api 자막 획득 실패 시 활성화            │
│    - yt-dlp / oEmbed 기반 오디오 스트림 URL 해석                │
├──────────────────────────────────────────────────────────────┤
│ 3. GenericWebVideoResolver: 일반 웹 비디오                     │
│    - <video src="...">, <source type="application/x-mpegURL">│
│    - og:video, twitter:player:stream, JSON-LD VideoObject    │
├──────────────────────────────────────────────────────────────┤
│ 4. DirectMediaResolver: 직접 미디어 파일 (.mp4, .m3u8, .mp3 등)  │
└──────────────────────────────────────────────────────────────┘
```

### 4.4. 비디오 페처 동작 생명주기 (`fetch_video`)

1. **URL 라우팅 (`router.py`)**:
   - `vmware.com/explore/video/`, `brightcove.net`, `.mp4`, `.m3u8` 등의 패턴 감지 시 `fetch_video`로 라우팅.
2. **스트림 해석 및 메타데이터 확보**:
   - `MediaStreamResolver`를 실행하여 `StreamInfo(stream_url, title, author, description, duration_sec, thumbnail_url)` 획득.
3. **환경변수 분기 처리 (`CLAIRE_ENABLE_VIDEO_TRANSCRIPTION`)**:
   - **`CLAIRE_ENABLE_VIDEO_TRANSCRIPTION=0` (비활성)**:
     - 오디오 다운로드 및 STT를 건너뛰고, 페이지 메타데이터(제목, 발표자, 설명문)만으로 경량 `Document(source_type="video", raw_text=..., partial=True)` 생성.
   - **`CLAIRE_ENABLE_VIDEO_TRANSCRIPTION=1` (활성)**:
     - `ffmpeg` 스트림 파이프로 원본 비디오 다운로드 없이 16kHz 모노 MP3 임시 파일(`/tmp/claire_audio_<hash>.mp3`) 추출.
     - `TranscriptProvider.transcribe(audio_file)` 호출로 타임스탬프 전사 획득.
     - 임시 오디오 파일 즉시 안전 삭제 (`try ... finally`).
4. **`Document` 생성 및 포맷팅**:
   - 본문(`raw_text`)에 메타데이터 헤더와 타임스탬프 전사문(`[05:20] 발화 내용...`) 결합.
   - `meta` 딕셔너리에 `duration_sec`, `has_transcript=True`, `transcript_segments`, `platform` 저장.

### 4.5. 지식 그래프 및 엔티티 해소 연동 (`resolver.py` & Pipeline)

1. **화자(Person) 및 발표 주체(Org) 자동 식별**:
   - 영상 메타데이터의 `author` 및 전사 본문 도입부 발화에서 화자/소속 기업 엔티티 추출.
2. **타임스탬프 관찰(Observation) 앵커링**:
   - 추출된 엔티티의 `observations`에 타임스탬프 앵커(`[12:34]`)가 보존되어, 향후 지식 검색 및 UI 조회 시 원본 영상의 재생 시각 딥링크(`https://...#t=754`)로 연결.
3. **약어 ↔ 풀네임 결정론적 수렴 (Zero-Quota Resolution)**:
   - 발표에서 자주 언급되는 IT/인프라 약어(VCF $\leftrightarrow$ VMware Cloud Foundation, GPU, vSAN, LLM 등)는 `resolver.py`의 약어 수렴 규칙(§2.5)에 따라 별도의 임베딩/판정 비용 없이 기존 그래프 노드와 즉시 병합.

### 4.6. 환경변수 기반 활성화 및 런타임 제어

기능의 활성화 여부 및 프로바이더 선택은 환경변수를 통해 결정론적으로 제어됩니다.

| 환경변수 | 기본값 | 허용값 / 설명 |
| :--- | :--- | :--- |
| `CLAIRE_ENABLE_VIDEO_TRANSCRIPTION` | `1` | `0` (비활성) \| `1` (활성). `0`일 경우 무거운 오디오 STT를 수행하지 않고 메타데이터만 수집하거나 안내 반환. |
| `CLAIRE_STT_PROVIDER` | `antigravity` | `antigravity` \| `whisper` \| `groq` \| `gcp` \| `local` \| `mock` |
| `CLAIRE_STT_MODEL` | `""` | 프로바이더별 모델명 오버라이드 (예: `gemini-3.7-flash`, `whisper-large-v3`) |
| `CLAIRE_STT_LANGUAGE` | `ko` | 전사 기본/선호 언어 코드 (`ko`, `en` 등 또는 빈 값 시 자동 감지) |
| `CLAIRE_FFMPEG_BIN` | `ffmpeg` | 시스템 `ffmpeg` 실행 파일 경로 또는 바이너리명 |

### 4.7. 빌드 환경 및 컨테이너 통합 (Build Specifications)

1. **컨테이너 이미지 (`Dockerfile`)**:
   - `apt-get install`에 `ffmpeg` 패키지를 추가하여 스트림 오디오 트랜스코딩 바이너리를 내장합니다.
2. **패키지 명세 (`pyproject.toml`)**:
   - `[project.optional-dependencies]`에 `audio` 그룹을 정의하고, 빌드 시 `uv sync --extra audio`로 필요한 라이브러리를 설치합니다.
3. **Graceful Fallback**:
   - `ffmpeg`가 누락되었거나 `CLAIRE_ENABLE_VIDEO_TRANSCRIPTION=0`인 환경에서도 시스템 전체가 실패하지 않고 안전하게 대체 경로로 동작하도록 설계합니다.

---

## 5. 모델 사용량 및 계정 쿼터 소모율 예측

### 5.1. 대상 영상(43분 29초 / 2,609초) 기준 토큰 환산

| 항목 | 산출 공식 및 근거 | 예상 토큰 수 |
| :--- | :--- | :--- |
| **오디오 입력 (Audio Input)** | Gemini 오디오 인코딩: **초당 약 32 토큰** (분당 ~1,920 토큰)<br>$2,609.15\text{초} \times 32\text{ tokens/sec}$ | **약 83,500 토큰** |
| **전사 시스템 프롬프트** | 자막 구조화 및 기술 용어 보존 지시문 | **약 500 ~ 1,000 토큰** |
| **자막 출력 (Transcript Output)** | 43.5분 발화량 (분당 약 140단어 $\approx$ 6,100단어)<br>타임스탬프 포함 텍스트 생성 | **약 8,000 ~ 12,000 토큰** |
| **지식 그래프 적재 (KG Ingest)** | 요약 + 엔티티/관계 추출 + 가독 본문(detail) 렌더링 | **입력 ~10,000 / 출력 ~4,000 토큰** |
| **총계 (1회 적재 전체)** | **음성 전사 + 지식 그래프 변환** | **입력 약 94,000 / 출력 약 16,000 토큰** |

---

### 5.2. 계정 쿼터 소모율 분석 (Gemini 3.7 / 2.5 Flash 기준)

1. **컨텍스트 윈도우 점유율 (Context Window)**:
   * Gemini Flash 단일 호출 컨텍스트 용량: **1,000,000 토큰**
   * 43.5분 영상 입력: 약 84,000 토큰 $\rightarrow$ **용량의 약 8.4%만 점유**.
   * 영상을 10분 단위로 잘라서 처리(chunking)할 필요 없이 **단일 API 호출로 문맥 손실 없이 완벽 처리 가능**.
2. **분당 토큰 한도 소모율 (TPM - Tokens Per Minute)**:
   * Gemini Flash 표준 TPM 한도: **1,000,000 ~ 4,000,000 TPM**
   * 1회 작업 소모량: 약 95,000 토큰 $\rightarrow$ **1분 쿼터의 약 2.4% ~ 9.5% 소모**.
   * 단건 적재 시 Rate Limit(429) 위험이 없으며 안전 마진이 매우 높음.
3. **분당/일일 요청 수 (RPM / RPD)**:
   * 비디오 1건 적재 시 총 3회 LLM 호출(전사 1회 + KG 추출 1회 + 가독 본문 1회).
   * 계정 RPM 한도(15 ~ 1,000 RPM) 대비 **1% 미만** 소모.

---

### 5.3. 비용 예측 (종량제 기준)

| 구분 | Gemini Flash 단가 기준 | 43분 29초 영상 1편 기준 |
| :--- | :--- | :--- |
| **오디오 입력 (83.5k tokens)** | \$0.00002 / 초 (또는 \$0.075 / 1M tokens) | 약 **\$0.0063** (약 8.5원) |
| **자막 텍스트 출력 (10k tokens)** | \$0.30 / 1M tokens | 약 **\$0.0030** (약 4.0원) |
| **지식 그래프 추출 및 렌더링** | 입력 10k / 출력 4k tokens | 약 **\$0.0020** (약 2.7원) |
| **합계 (비디오 1편 전체)** | **자막 생성 + 지식 베이스 완결 적재** | **약 \$0.011 ~ \$0.015 (한화 약 15원 ~ 20원)** |

> *참고: Antigravity 구독/무료 환경에서 실행 시 별도의 API 비용 과금 없이 기본 세션 쿼터 내에서 \$0으로 처리됩니다.*

---

## 6. 구현 단계 및 권장 사항

1. **1단계: 의존성 및 미디어 리졸버 구축**
   * 시스템 환경 내 `ffmpeg` 도구 확보 및 `yt-dlp` 기반 Brightcove/YouTube/HTML5 스트림 URL 파서 작성.
2. **2단계: `TranscriptProvider` 추상화 및 Antigravity 어댑터 구현**
   * `claire.extract.transcript` 패키지 신설 및 `AntigravityTranscriptProvider` 구현.
   * 타임스탬프가 포함된 `TranscriptResult` 스키마 고정.
3. **3단계: 라우터 및 인제스트 파이프라인 통합**
   * `ingest/fetchers/video.py`를 신설하여 `fetch_video` 구현.
   * 자막 생성 결과를 `Document(source_type="video", raw_text=...)`로 변환하여 기존 그래프 적재 파이프라인에 연결.
4. **4단계: 타임스탬프 앵커링 지원**
   * 가독 본문(AsciiDoc/Markdown) 및 지식 그래프 관찰(Observations)에 영상 타임스탬프 링크(예: `[12:34](url#t=754)`)를 보존하여 원본 재생 위치로 바로 이동할 수 있도록 UI 지원.

---

## 7. 구현 완료 및 검증 결과

* **신규 모듈 및 컴포넌트**:
  * `src/claire/extract/transcript/`: `base.py`, `mock_stt.py`, `antigravity_stt.py`, `factory.py`
  * `src/claire/ingest/fetchers/video.py`: `resolve_video_target_url()`, `fetch_video()`
  * `src/claire/config.py`: `enable_video_transcription`, `stt_provider`, `ffmpeg_bin`, `find_ffmpeg_executable()`
  * `src/claire/ingest/router.py` & `src/claire/telegram_bot.py`: `video` 입력 라우팅 및 분류 연동
* **빌드 및 패키징**:
  * `pyproject.toml` `[project.optional-dependencies]`에 `audio = ["yt-dlp>=2024.8.0"]` 추가
  * `Dockerfile`에 `ffmpeg` apt 패키지 설치 및 `uv sync --extra audio` 연동
* **테스트 검증**:
  * 전용 단위 테스트 12건 (`tests/test_config_video.py`, `tests/test_transcript_provider.py`, `tests/test_video_fetcher.py`) 신설 및 전체 테스트 스위트 811건 100% 통과.

