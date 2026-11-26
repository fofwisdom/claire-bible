"""Nginx gateway configurations for development network fault profiles (docs/design/CONTRIBUTOR_DEVELOPMENT_ENVIRONMENT_DESIGN.md Section 6.5, unit KF6).

The module writes Nginx server configuration fragments mounted at ``/etc/nginx/conf.d/default.conf``.
"""

from __future__ import annotations

PROFILES: tuple[str, ...] = ("normal", "gf8", "gf9", "gf10", "gf14", "gf15", "gf16", "gf17")


def _proxy_block(*, indent: str = "        ", host_header: str = "$http_host", body_limit: str | None = None, hide_cors: bool = False) -> list[str]:
    lines: list[str] = [f"{indent}if ($devnet_edge = \"\") {{", f"{indent}    return 421;", f"{indent}}}"]
    if body_limit is not None:
        lines.append(f"{indent}client_max_body_size {body_limit};")
    lines.extend(
        [
            f"{indent}proxy_pass http://$devnet_edge;",
            f"{indent}proxy_http_version 1.1;",
            f"{indent}proxy_set_header Host {host_header};",
            f"{indent}proxy_set_header Connection \"\";",
            f"{indent}proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;",
            f"{indent}proxy_set_header X-Forwarded-Proto https;",
        ]
    )
    if hide_cors:
        lines.append(f"{indent}proxy_hide_header Access-Control-Allow-Origin;")
    lines.extend(
        [
            f"{indent}proxy_buffering off;",
            f"{indent}proxy_request_buffering off;",
            f"{indent}proxy_read_timeout 300s;",
            f"{indent}proxy_send_timeout 300s;",
        ]
    )
    return lines


def _fault_locations(profile: str) -> list[str]:
    if profile == "normal":
        return []
    if profile == "gf8":
        return [
            "    # Fault GF8: block the self-check knowledge path /ap/kb/0 at the gateway.",
            "    location = /ap/kb/0 {",
            "        if ($devnet_edge = \"\") {",
            "            return 421;",
            "        }",
            "        return 404;",
            "    }",
            "",
        ]
    if profile == "gf9":
        return [
            "    # Fault GF9: overwrite the Host header on the signed self-check path.",
            "    location = /ap/self-check {",
            *_proxy_block(host_header="$devnet_edge"),
            "    }",
            "",
        ]
    if profile == "gf10":
        return [
            "    # Fault GF10: set a 64 KiB request body limit on /ap/inbox.",
            "    location = /ap/inbox {",
            *_proxy_block(body_limit="64k"),
            "    }",
            "",
        ]
    if profile == "gf14":
        return [
            "    # Fault GF14: intercept /nodeinfo/2.1 when the request has no sidecar self-check header.",
            "    location = /nodeinfo/2.1 {",
            "        if ($http_x_claire_self_check = \"\") {",
            "            return 404;",
            "        }",
            *_proxy_block(),
            "    }",
            "",
        ]
    if profile == "gf15":
        return [
            "    # Fault GF15: block knowledge base actor documents (/ap/kb/{number} for positive numbers).",
            "    location ~ ^/ap/kb/[1-9][0-9]*$ {",
            "        if ($devnet_edge = \"\") {",
            "            return 421;",
            "        }",
            "        return 404;",
            "    }",
            "",
        ]
    if profile == "gf16":
        return [
            "    # Fault GF16: answer unsigned /ap/kb reads with HTTP 200 when the request has no sidecar self-check header.",
            "    location = /ap/kb {",
            "        if ($http_x_claire_self_check = \"\") {",
            "            return 200 '{\"type\":\"OrderedCollection\",\"totalItems\":0}';",
            "        }",
            *_proxy_block(),
            "    }",
            "",
        ]
    if profile == "gf17":
        return [
            "    # Fault GF17: strip Access-Control-Allow-Origin from /ns/v1.",
            "    location = /ns/v1 {",
            *_proxy_block(hide_cors=True),
            "    }",
            "",
        ]
    raise ValueError(f"unknown gateway fault profile: {profile!r}")


def render_gateway_conf(profile: str = "normal") -> str:
    """Return the Nginx server configuration for one gateway profile. Raise ``ValueError`` for an unknown profile."""
    if profile not in PROFILES:
        raise ValueError(f"unknown gateway fault profile: {profile!r}")
    lines = [
        "# Development network only (docs/design/CONTRIBUTOR_DEVELOPMENT_ENVIRONMENT_DESIGN.md Section 6.5).",
        "# The gateway is the only end of TLS, as the external reverse proxy of a production server. It sends each request of a server name to the edge service of that server (alias {server}-edge on the network claire-devnet-edge) with the Host header as it came. The edge service has the reverse proxy rules of Claire (ops/edge/claire/routes.conf, FEDERATION.md Section 10.1): this file has no path rule.",
        "server_tokens off;",
        "access_log off;",
        "# The resolver of Docker gives the address of an edge service at each request, so the gateway runs also when a server does not run.",
        "resolver 127.0.0.11 valid=5s ipv6=off;",
        "",
        "map $host $devnet_edge {",
        "    default \"\";",
        "    ~^(?:[a-z0-9-]+\\.)*a\\.claire\\.test$ a-edge:8080;",
        "    ~^(?:[a-z0-9-]+\\.)*b\\.claire\\.test$ b-edge:8080;",
        "}",
        "",
        "server {",
        "    listen 443 ssl default_server;",
        "    ssl_certificate /tls/server.crt;",
        "    ssl_certificate_key /tls/server.key;",
        "    ssl_protocols TLSv1.2 TLSv1.3;",
        "    # The edge service has the body limit of the federation paths, and Claire has its own limits.",
        "    client_max_body_size 0;",
        "",
        *_fault_locations(profile),
        "    location / {",
        *_proxy_block(),
        "    }",
        "}",
        "",
    ]
    return "\n".join(lines)
