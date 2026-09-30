# Extra CA Certificates

이 디렉터리는 사설 루트 CA(Private Root CA)나 내부망용 커스텀 SSL/TLS 인증서(`.crt`, `.pem`)를 보관하는 곳입니다.

### 동작 방식
1. 컨테이너 기동 시 `docker-compose.yml`을 통해 이 디렉터리가 컨테이너의 `/extra-certs`로 읽기 전용(`ro`) 마운트됩니다.
2. 컨테이너 내부의 `docker-entrypoint.sh`가 이 디렉터리의 `.crt` / `.pem` 파일을 감지하면, 컨테이너의 시스템 신뢰 저장소(`/usr/local/share/ca-certificates/`)로 복사 후 `update-ca-certificates`를 자동 실행합니다.
3. 인증서 파일이 없으면 기본 공인 CA 인증서만 사용하여 즉시 기동됩니다.

### 사용 예시
- 사내망/홈랩 내부 도메인용 사설 CA 인증서 (예: `nsa-r1.crt`)를 이 디렉터리에 넣으면 컨테이너가 자동으로 신뢰합니다.
- 특정 호스트 OS(Ubuntu, macOS, Windows 등)의 시스템 경로에 의존하지 않고 어디서나 일관되게 동작합니다.
