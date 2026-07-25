# 웹 접속과 reverse proxy

Claire 웹 서비스는 환경에 따라 두 가지 접속 형태만 지원한다.

- `development`: Docker host의 정확한 IPv4와 port로 직접 HTTP 접속
- `production`: 별도 LAN reverse proxy가 public hostname과 클라이언트 TLS를 담당하고,
  Claire에는 HTTP로 전달

Claire 컨테이너에 HTTPS를 구성하거나 인증서를 저장하지 않는다. Claire host에서
Let's Encrypt를 실행하는 절차도 이 문서와 현재 구현의 범위가 아니다. 서비스는
hostname의 root(`/`)에 배치하며 subpath 배포는 지원하지 않는다.

## 공통 네트워크 경계

컨테이너 안의 웹 서버는 `0.0.0.0:CB_API_PORT`에서 듣는다. 외부에 공개되는 주소는
Docker port publish의 host 측 `CB_API_BIND`다. `cb-manuscript`는 `CB_API_BIND`를
단일 IPv4로 검사하고 `0.0.0.0`, multicast, hostname과 IPv6를 거부한다. 따라서
컨테이너 listen 주소를 host 공개 범위로 해석하면 안 된다.

`CLAIRE_PUBLIC_URL`은 링크 생성뿐 아니라 요청 Host 정책의 기준이다.

| 환경 | 필수 형태 |
|---|---|
| development | `http://<CB_API_BIND>:<CB_API_PORT>/` |
| production | `https://<DNS-hostname>/` |

두 환경 모두 path는 root만 허용한다. `CLAIRE_CORS_ALLOWED_ORIGINS`는 path와 wildcard가
없는 정확한 origin의 쉼표 목록이다. 빈 값이면 same-origin만 허용하고, production
목록은 `https` origin만 사용할 수 있다.

기존 `.env`/`.env.dev`를 재사용하는 설치는 첫 기동 전에 `./cb-manuscript init`을 다시
실행해 누락된 environment selector를 보충한다. 이 명령은 production hostname을
추측하지 않는다. 따라서 `.env`의 `CLAIRE_PUBLIC_URL`은 아래 production 형식으로 직접
설정한 뒤 `./cb-manuscript doctor`를 통과시켜야 한다.

애플리케이션은 `Forwarded`와 `X-Forwarded-*`를 신뢰해 scheme, client IP 또는 Host를
바꾸지 않는다. production의 외부 HTTPS 여부는 `CLAIRE_PUBLIC_URL`과 정확한 Host로
결정하며 upstream 연결 자체는 HTTP다.

## Development: IPv4 직접 HTTP

`.env.dev`의 주소와 URL을 같은 authority로 설정한다.

```dotenv
CLAIRE_ENVIRONMENT=development
CB_API_BIND=192.168.10.25
CB_API_PORT=8766
CLAIRE_PUBLIC_URL=http://192.168.10.25:8766/
CLAIRE_CORS_ALLOWED_ORIGINS=
```

```bash
CLAIRE_ENVIRONMENT=development ./cb-manuscript doctor
CLAIRE_ENVIRONMENT=development ./cb-manuscript up
```

브라우저에서는 `http://192.168.10.25:8766/`로 접속한다. 예시 파일의 loopback은 같은
host에서만 접근하는 안전한 초기값이다. 다른 개발 장치에서 접속할 때만 실제 고정 LAN
IPv4로 바꾸고 host firewall의 허용 대역도 필요한 개발 LAN으로 제한한다.

## Production: 별도 LAN reverse proxy

권장 흐름은 다음과 같다.

```text
client -- HTTPS / production hostname --> external reverse proxy
       -- HTTP / fixed LAN addresses --> Claire host:CB_API_PORT
```

Claire host의 `.env`에는 proxy가 도달할 수 있는 고정 LAN IPv4와 사용자가 접속할
hostname을 설정한다.

```dotenv
CLAIRE_ENVIRONMENT=production
CB_API_BIND=192.168.10.25
CB_API_PORT=8765
CLAIRE_PUBLIC_URL=https://claire.example.com/
CLAIRE_CORS_ALLOWED_ORIGINS=https://portal.example.com
```

배포 계약은 다음과 같다.

- reverse proxy의 upstream은 `http://192.168.10.25:8765`처럼 고정한다.
- Claire로 보내는 `Host`는 `CLAIRE_PUBLIC_URL`의 authority와 정확히 같아야 한다.
- 알 수 없는 hostname을 Claire upstream으로 보내지 않고 proxy의 기본 virtual host에서
  거부한다.
- NDJSON 응답을 즉시 전달하도록 response buffering을 끄고 upstream read timeout을
  장시간 작업보다 길게 둔다.
- proxy access log에는 query string, `Referer`, `Authorization`과 cookie를 기록하지
  않는다. 기존 인증 진입 query가 proxy 로그로 유출되지 않아야 한다.
- Claire host firewall은 API port의 source를 reverse proxy의 고정 LAN IP로만 허용한다.
  Host 검사만으로 backend 직접 접근을 막을 수는 없다.

## Nginx 예시

다음 server block은 외부 reverse proxy에 병합하는 예시다. proxy의 기존 TLS 인증서
설정과 기본 virtual host 정책은 그대로 사용하며 여기서는 인증서 발급·갱신을 다루지
않는다.

```nginx
log_format claire_safe
    '$remote_addr "$request_method $uri $server_protocol" '
    '$status $body_bytes_sent $request_time';

# http context: 인증 추측과 장시간 worker 고갈을 한 IP가 독점하지 못하게 한다.
limit_req_zone $binary_remote_addr zone=claire_per_ip:10m rate=10r/s;
limit_conn_zone $binary_remote_addr zone=claire_conn_per_ip:10m;

upstream claire_backend {
    server 192.168.10.25:8765;
    keepalive 16;
}

server {
    listen 443 ssl;
    server_name claire.example.com;

    # ssl_* directives are owned by this external proxy's existing TLS policy.
    access_log /var/log/nginx/claire_access.log claire_safe;
    client_max_body_size 1m;
    client_body_timeout 15s;

    location / {
        limit_req zone=claire_per_ip burst=20 nodelay;
        limit_conn claire_conn_per_ip 10;

        proxy_pass http://claire_backend;
        proxy_http_version 1.1;
        proxy_set_header Host claire.example.com;
        proxy_set_header Connection "";
        proxy_set_header Forwarded "";
        proxy_set_header X-Forwarded-For "";
        proxy_set_header X-Forwarded-Host "";
        proxy_set_header X-Forwarded-Proto "";
        proxy_set_header Referer "";

        proxy_buffering off;
        proxy_connect_timeout 5s;
        proxy_read_timeout 300s;
        proxy_send_timeout 300s;
    }
}
```

같은 proxy의 unmatched/default server는 연결을 거부해야 한다. `$request_uri`는 query를
포함하므로 위 안전 로그에서는 사용하지 않는다.

## 적용 확인

development에서는 설정한 IPv4 URL로 직접 접속하고 다른 interface에 port가 게시되지
않았는지 확인한다. production에서는 다음을 각각 확인한다.

1. 올바른 hostname을 통한 HTTPS 요청은 성공한다.
2. 잘못된 Host는 proxy 또는 Claire에서 거부된다.
3. proxy host에서는 Claire HTTP upstream에 접속할 수 있다.
4. proxy 이외의 LAN host에서는 firewall 때문에 같은 upstream port에 접속할 수 없다.
5. 긴 NDJSON 응답이 proxy buffering 없이 순차 전달된다.
6. Claire와 proxy access log에 query string과 인증 정보가 남지 않는다.

실제 Linux 실행 시험은 저장소 작업 경로가 아니라 WSL Ubuntu의 `/home/fow/testbed`
아래에 새로 만든 clone에서 수행한다.
