# VCF Operations 로그 파워 유저 가이드: 통합 로그 아키텍처 및 고급 운영 분석

### 통합 로그 아키텍처 및 고급 운영 분석
**Broadcom VCF 9.0 / 9.1 Technical Briefing**

Note:
VCF 9.1에서 새롭게 개편된 통합 로그 관리 아키텍처와 엔터프라이즈 운영 시나리오를 다룹니다.

---

## 1. 개요: VCF 9.x 운영 패러다임 전환

* **기존 환경의 한계**: Aria Operations와 Aria Operations for Logs가 개별 가상 어플라이언스(VA)로 분리되어 LCM 및 UI 단절 발생
* **VCF 9.1의 혁신**: 로그 관리가 독립 어플라이언스를 탈피하고, **VCF Management Services(VMSP)** 상의 통합 마이크로서비스로 전면 재편
* **운영 효과**: 지표(Metrics), 로그(Logs), 네트워크 플로우(Flows)를 단일 글로벌 콘솔에서 상호 연관 분석 &rarr; **MTTR(평균 복구 시간) 획기적 단축**

> **VCF Operations 아키텍처 비전**  
> "파편화된 운영 도구를 단일 콘솔로 수렴하여 컨텍스트 단절 없는 텔레메트리 연관 분석을 완성한다."

---

## 2. 차세대 로그 아키텍처 & 성능 확장성

### 마이크로서비스 내부 아키텍처
* **Envoy Gateway**: 외부 에이전트 및 시스템 인입 API/로그 트래픽 수신, 로드 밸런싱 및 라우팅
* **Custom Log Processor**: 로그 스트림 파싱, 토큰화(Tokenization), 필터링, 마스킹 처리
* **OpenSearch 백엔드**: 대규모 분산 인덱싱 및 고속 다차원 쿼리 엔진

### 성능 지표 획기적 향상
| 성능 및 용량 지표 | 이전 릴리스 (8.x) | VCF Operations 9.1 |
| :--- | :--- | :--- |
| **초당 로그 수집률** | 기준 표준 수집 성능 | **최대 1.14M Events/Sec** (~4배) |
| **클러스터당 저장 용량** | 기준 표준 저장 한도 | **최대 171 TB / Cluster** (~58%) |
| **인덱싱 아키텍처** | 기존 독자 인덱싱 구조 | **OpenSearch 기반 분산 토큰화** |
| **배포 형태** | 독립 VA 가상머신 클러스터 | **VMSP 내부 통합 마이크로서비스** |

---

## 3. 글로벌 플릿(Fleet) 배포 토폴로지

* **중앙 집중형 제어**: 관리 도메인(Management Domain)의 VCF Operations 내부에 로그 코어 서비스 상주
* **대규모 연동**: 복수의 VCF 인스턴스, 워크로드 도메인(Workload Domain), NSX Manager, vCenter가 중앙 파이프라인으로 직결
* **완벽한 하위 호환성**: Syslog, CFAPI(Cloud Foundry API), JSON API 표준 지원 및 Cloud Proxy / 8.x 인스턴스 연속 포워딩 보장

---

## 4. 배포 및 Day 2 설정

### T-Shirt 사이징 사양
| 배포 규모 | 복제본 수 | 적용 대상 및 용도 |
| :--- | :--- | :--- |
| **Small** | 기본 복제본 구성 | 소규모 랩 및 단위 테스트 환경 |
| **Medium** | 3 Replicas | 표준 프로덕션 환경 (가용성 보장) |
| **Large** | 확장 복제본 구성 | 대규모 엔터프라이즈 플릿 (고밀도 인덱싱) |

<div class="admonition important">
  <strong>⚠️ IMPORTANT:</strong> 신규 배포 시 인스턴스의 FQDN은 기존 8.x에서 사용하던 FQDN과 중복되지 않도록 <strong>새로운 FQDN(New FQDN)</strong>을 지정해야 합니다. (서비스 충돌 방지)
</div>

* **배포 후 필수 활성화 단계:**  
  `Operate -> Administration -> Integrations` &rarr; vCenter 및 NSX 인스턴스에서 **[Activate Log Collection]** 체크박스 필수 활성화

---

## 5. 로그 관리의 3대 핵심 기둥

* **모니터링 (Monitor)**
  * 로그 부재(하트비트 중단) 및 급증 실시간 탐지
  * 성능 지표 이상(CPU 스파이크 등)과 로그 에러 패턴을 묶은 복합 경보(Combined Alert)
* **진단 및 장애 분석 (Troubleshoot)**
  * 다차원 상관분석: 동일 타임스탬프에서 CPU/스토리지 + 로그 + 네트워크 플로우 통합 추적
  * **병렬 로그 비교 (Log Compare)**: 최대 4개 쿼리를 동시 실행하여 패치 전/후 및 호스트 간 대조
* **보안 거버넌스 및 규제 준수 (SecOps)**
  * 스코프 기반 RBAC 및 동적 Log Datasets (테넌트/조직별 격리)
  * 정규식 기반 인메모리 PII 마스킹 (PCI DSS, HIPAA, GDPR 대응)
  * S3(MinIO) 및 NFS 외부 장기 아카이빙 파티션 지원

---

## 6. 고급 운영 기능 상세

* **필드 라이브러리 (Field Library)**
  * 런타임 발견 비정형 필드를 수동으로 **인덱싱 승격(Upgrade to Index)** 가능 &rarr; 토큰화 쿼리 속도 극대화
  * 내장 `SDCompID` 필터 지원 (예: `SDCompID contains NSX` 로 즉각 격리 검색)
* **아카이브 로그 임포트 & 능동 대조**
  * 장기 저장된 아카이브 로그를 임시 파티션으로 재유입
  * **Log Compare**로 현재 활성 로그(Active)와 과거 아카이브(Archived)를 단일 화면에서 직접 비교
* **통합 에이전트 및 보안**
  * Fluent Bit / Fluentd 에이전트 연동 지원
  * 인증 시크릿 토큰 기반 상호 신뢰 통신 및 강제 SSL 암호화 적용

---

## 7. 실전 침해 사고 추적 시나리오 (Audit Trail)

비즈니스 크리티컬 `AI Workloads VM` 원인 불명 삭제 사고 포렌식:

| 단계 | 조사 내용 및 확인 정황 |
| :--- | :--- |
| **1단계: 인증 검토** | 워크로드 vCenter 연속 로그인 실패 후 성공, NSX Manager 비인가 SSH 세션 포착 |
| **2단계: 수명 주기 작업** | `AI Workloads VM` 전원 끄기(Power Off) 및 삭제(Delete VM) 감사 이벤트 확인 |
| **3단계: 권한 변조 특정** | `NOC User 1` 계정이 vCenter 권한 상승 및 `SVC Project 1` 서비스 계정 명의 도용 정황 입증 |

> **감사 추적 분석 결론**  
> 분산된 시스템의 개별 로그 취합 없이, 단일 감사 화면에서 최초 침투 &rarr; 권한 상승 &rarr; 인프라 파괴로 이어지는 공격 체인을 완벽하게 재구성 완료.

---

## 8. 종합 요약

* **콘솔 통합**: 메트릭, 로그, 이벤트, 플로우가 단일 워크벤치로 융합되어 운영 단절 해소
* **성능 혁신**: 초당 114만 건 수집 및 171 TB 저장 용량으로 대규모 플릿 완벽 지원
* **보안/컴플라이언스**: PII 실시간 마스킹, RBAC 스코프 분리, 아카이브 대조 분석 일원화

<div class="admonition note">
  <strong>💡 NOTE:</strong> VCF 9.1 Operations 로그 관리는 단순한 로그 수집기를 넘어 지능형 통합 가시성 플랫폼으로 진화하였습니다.
</div>
