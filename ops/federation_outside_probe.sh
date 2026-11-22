#!/bin/sh
# Outside probe of the federation paths of a Claire server (FEDERATION_GO_LIVE_DESIGN.md Section 7.3).
#
# Run this script on a computer on another network than the server (for example a laptop on a mobile hotspot).
# The readiness checks GF6 to GF17 send their requests from the server to its own public origin. This script sends
# the same unsigned requests from outside, so that the result shows the path of other servers.
#
# Usage: sh federation_outside_probe.sh https://claire.example.com
#
# The script needs only a POSIX shell and curl. It writes no file and sends requests to the given origin only.
# Each line shows the party that answered: sidecar, claire, cdn_challenge, cdn_block, cdn_error, intermediary, or none.
# The exit code is 0 when each path got its answer from the expected party, 1 when not, and 2 for a wrong argument.

set -u

origin=${1:-}
case "$origin" in
  http://*|https://*) ;;
  *) echo "usage: sh federation_outside_probe.sh https://<host>" >&2; exit 2 ;;
esac
origin=${origin%/}
host=${origin#*://}
agent="claire-bible-outside-probe/1 (+$origin/)"
activity='application/activity+json'
ld_activity='application/ld+json; profile="https://www.w3.org/ns/activitystreams"'
failures=0

header_value() {
  # $1: the headers of the answer, $2: a header name in lower case.
  printf '%s\n' "$1" | tr -d '\r' | awk -v name="$2" 'BEGIN { FS = ": *" } tolower($1) == name { sub(/^[^:]*: */, ""); print; exit }'
}

probe() {
  # $1: expected party, $2: method, $3: path, $4: request header, $5: body (optional).
  expected=$1
  method=$2
  path=$3
  if [ $# -ge 5 ]; then
    headers=$(curl -sS -o /dev/null -D - --max-time 15 -A "$agent" -X "$method" -H "$4" --data "$5" "$origin$path" 2>/dev/null)
  else
    headers=$(curl -sS -o /dev/null -D - --max-time 15 -A "$agent" -X "$method" -H "$4" "$origin$path" 2>/dev/null)
  fi
  status=$(printf '%s\n' "$headers" | tr -d '\r' | awk '/^HTTP\// { code = $2 } END { print code }')
  mitigated=$(header_value "$headers" "cf-mitigated")
  ray=$(header_value "$headers" "cf-ray")
  server=$(header_value "$headers" "server")
  if [ -z "$status" ]; then
    party=none
  elif [ -n "$(header_value "$headers" "x-claire-fedify-server")" ]; then
    party=sidecar
  elif [ -n "$(header_value "$headers" "x-claire-server")" ]; then
    party=claire
  elif [ "$mitigated" = "challenge" ]; then
    party=cdn_challenge
  elif [ -n "$mitigated" ]; then
    party=cdn_block
  elif [ -n "$ray" ] && [ "$status" -ge 520 ] && [ "$status" -le 530 ]; then
    party=cdn_error
  else
    party=intermediary
  fi
  if [ "$party" = "$expected" ]; then
    result=OK
  else
    result=FAIL
    failures=$((failures + 1))
  fi
  printf '%-4s %-4s %-44s status=%s answered=%s expected=%s via_cdn=%s cf-mitigated=%s server=%s\n' "$result" "$method" "$path" "${status:--}" "$party" "$expected" "${ray:+cloudflare}" "${mitigated:--}" "${server:--}"
}

# Paths of the sidecar (GF6, GF8, GF10, GF14, GF16). The status is not important: it is 404 while the switch federation is off.
probe sidecar GET "/.well-known/webfinger?resource=acct:claire@$host" "Accept: application/jrd+json"
probe sidecar GET "/.well-known/nodeinfo" "Accept: application/json"
probe sidecar GET "/nodeinfo/2.1" "Accept: application/json"
probe sidecar GET "/ap/actors/server" "Accept: $activity"
probe sidecar GET "/ap/actors/server" "Accept: $ld_activity"
probe sidecar GET "/ap/kb" "Accept: $activity"
probe sidecar GET "/ap/themes" "Accept: $activity"
# An unsigned delivery in each of the two media types. The sidecar refuses it (401, or 404 while the switch is off).
probe sidecar POST "/ap/inbox" "Content-Type: $activity" '{"summary":"x"}'
probe sidecar POST "/ap/inbox" "Content-Type: $ld_activity" '{"summary":"x"}'
# Paths of Claire (GF7, GF17).
probe claire GET "/.well-known/oauth-authorization-server" "Accept: application/json"
probe claire GET "/ns/v1" "Accept: application/ld+json, application/json"
probe claire GET "/ns" "Accept: text/html"

if [ "$failures" -eq 0 ]; then
  echo "outside probe: PASS"
  exit 0
fi
echo "outside probe: FAIL ($failures paths)"
exit 1
