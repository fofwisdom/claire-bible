https://github.com/D4Vinci/Scrapling

GeekNews 최신글 예전글 댓글 Ask Show GN⁺ Weekly GeekBots GeekBadge | 글등록
 로그인
▲
Scrapling - 적응형 웹 스크래핑 프레임워크 (github.com/D4Vinci)
52P by xguru 3달전 | ★ favorite | 댓글 3개
현대 웹의 복잡한 구조와 안티-봇 시스템을 우회하며 단일 요청부터 대규모 크롤링까지 처리
웹사이트 구조 변경 시 자동으로 요소를 재탐색하는 지능형 파서(parser) 내장
Cloudflare Turnstile 등 주요 보안 시스템을 기본적으로 우회하는 Fetcher 모듈 내장
Spider 프레임워크를 통해 동시성, 세션 관리, 일시 중지/재개, 프록시 회전 등 대규모 크롤링 기능 지원
Scrapy와 유사한 API: start_urls, 비동기 parse 콜백, Request/Response 객체를 활용
동시 크롤링 및 세션 분리: 여러 브라우저 세션을 병렬 실행 가능
Checkpoint 기반 일시 중지 및 재개 기능 : 장시간 크롤링 시에도 안정적
실시간 스트리밍 모드: 수집 데이터를 즉시 처리하거나 UI에 반영 가능
차단된 요청을 자동 인지하고, 커스텀 로직으로 재시도 가능
Hook을 이용해 자신의 파이프라인으로 결과 내보내기 가능(JSON/JSONL)
세션을 지원하는 고급 웹사이트 Fetching
Fetcher 클래스가 HTTP/3, TLS 지문 위조, 헤더 위장 등 고급 요청 기능 지원
DynamicFetcher를 통해 Playwright/Chrome 기반 브라우저 자동화 수행
StealthyFetcher는 Cloudflare Turnstile 등 반봇 방어를 자동 우회
ProxyRotator로 요청 단위 프록시 교체 및 도메인 차단 제어 가능
모든 Fetcher가 비동기(async) 방식으로 동작하며, 세션 클래스(FetcherSession, DynamicSession 등) 제공
적응형 스크래핑(Adaptive Scraping) 으로 웹사이트 변경 후에도 요소를 자동 재탐색
유사도 기반 요소 추적 알고리듬: 구조 변경에 강한 데이터 수집 가능
CSS/XPath/텍스트/정규식 기반 선택자를 모두 지원
AI 통합용 MCP 서버 내장: Claude, Cursor 등과 연동해 AI 보조 데이터 추출 수행
AI 호출 전 Scrapling이 대상 콘텐츠를 선별해 토큰 사용량 절감 및 속도 향상
고성능 아키텍처
대부분의 Python 스크래핑 라이브러리보다 빠른 처리 속도 제공
메모리 효율적 구조와 지연 로딩(lazy loading) 으로 경량화된 실행
JSON 직렬화 속도 10배 향상, 92% 테스트 커버리지 및 정적 타입 힌트 완비
다수의 웹 스크래퍼 커뮤니티에서 실전 검증(battle-tested) 완료
개발자/웹 스크래퍼 친화적인 경험 제공
대화형 Web Scraping Shell 내장: IPython 기반 실시간 탐색 및 요청 변환 지원
CLI 명령어를 통해 코드 작성 없이 URL 스크래핑 및 파일 추출 가능
DOM 탐색 API로 부모/형제/자식 관계 탐색 및 유사 요소 탐색 기능 제공
자동 선택자 생성기로 안정적인 CSS/XPath 선택자 자동 생성
Scrapy/BeautifulSoup 유사 API: 기존 사용자에게 익숙한 개발 경험 제공
PyRight/MyPy 기반 정적 분석과 Docker 이미지 자동 빌드로 배포 편의성도 강화
성능 벤치마크
Scrapling 파서는 Parsel/Scrapy보다 약간 빠르고,
BeautifulSoup4 (bs4) 대비 최대 700배 이상 빠른 처리 속도 기록
요소 유사도 탐색 성능도 AutoScraper 대비 5배 이상 빠른 결과 달성
pip install scrapling으로 설치하거나
Docker 이미지를 제공하여 브라우저 포함 완전한 실행 환경 구성 가능 docker pull pyd4vinci/scrapling
BSD-3-Clause 라이선스
GeekNews Weekly에 포함된 글입니다. 에디터 코멘트 보기
함께 보면 좋은 글
β
GoScrapy - Go기반 초고속 웹 스크래핑 프레임워크
봇 감지 우회하기 : 차단당하지 않고 웹 스크레핑 하는 법
Scrapeghost - GPT를 이용한 웹 스크래핑 라이브러리
Crawlee for Python – 웹 스크래핑 및 브라우저 자동화 라이브러리
2021년 웹 스크래핑 현황
댓글과 토론
• 친절하고 점잖은 댓글을 남겨주세요
• 주제와 관련된 내용으로 작성해주세요
▲
eyelove 3달전  [-]
법적으로 문제는 없는건가요??? 온라인정보를 가져가는건 문제가 없었다고 나온걸 보긴했는데..
사이트에서 크롤링못하게 막은 내용을 우회해서 읽으면 피해가 없을지 궁금하네요.

로그인 후 조회하는 내용만 위험한 걸까요?

답변달기
▲
crawler 3달전  [-]
with FetcherSession(impersonate='chrome') as session: # Use latest version of Chrome's TLS fingerprint

재밌네요 항상 검색해서 수동으로 넣어줬는데 이런 라이브러리는 처음 봐요. 편할 거 같네요

답변달기
▲
crawler 3달전  [-]
그런데 cloudflare를 어떻게 우회한다는 건지 궁금하네요. 코드를 한번 봐야 알 거 같아요

답변달기
처음 오셨나요 사이트 이용법 FAQ About 후원하기 이용약관 개인정보 처리방침   | Blog Lists RSS   | Bookmarklet
X (Twitter) Facebook   |   긱뉴스봇 : Slack 잔디 Discord Teams Dooray! Google Chat Swit
검색 
