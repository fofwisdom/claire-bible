"""Development test only: scenarios against DrFed 0.1.0 in Docker (FEDERATION_REMOTE_READING_DESIGN.md unit FP4-10).

The stack of ``tests/drfed_stack.py`` is a model of a remote ActivityPub server: DrFed is behind a TLS gateway with a wildcard certificate of a private certificate authority, and each DrFed instance has a subdomain of the root origin. DrFed 0.1.0 records the deliveries that arrive at an inbox; it does not send activities to a remote inbox.

The tests start containers and build an image from the npm registry, thus they run only with ``CB_DRFED_TEST=1`` and a Docker daemon.
"""

from __future__ import annotations

import os
import secrets

import httpx
import pytest

from tests import drfed_stack

pytestmark = pytest.mark.skipif(os.environ.get("CB_DRFED_TEST") != "1", reason="set CB_DRFED_TEST=1 to run the DrFed tests in Docker")

AS_JSON = {"accept": "application/activity+json"}
PUBLIC = "https://www.w3.org/ns/activitystreams#Public"
CREATE_OBJECT = "mutation($actor: ID!, $html: String!, $to: [URL!]!) { createObject(actor: $actor, contentHtml: $html, addressing: {to: $to}) { __typename ... on Object { id iri } ... on CreateObjectError { type message } } }"


@pytest.fixture(scope="module")
def stack(tmp_path_factory):
    if not drfed_stack.docker_available():
        pytest.skip("Docker is not available")
    running = drfed_stack.start(tmp_path_factory.mktemp("drfed") / "run", project=f"claire-drfed-pytest-{secrets.token_hex(4)}")
    yield running
    drfed_stack.stop(running.run_dir, running.project)


# --- The stack ------------------------------------------------------------------


def test_helper_signs_in_and_makes_an_instance_with_actors(stack):
    """The helper does all steps without the web UI: the login link comes from the log of DrFed, and GraphQL makes the instance and the actors."""
    assert stack.access_token and stack.instance_id and len(stack.actors) == 2
    viewer = stack.graphql("{ viewer { email } }")["viewer"]
    assert viewer["email"] == stack.email
    # Without the access token DrFed knows no account.
    assert stack.graphql("{ viewer { email } }", token=False)["viewer"] is None
    for actor in stack.actors:
        assert actor.iri.startswith(stack.instance_origin + "/") and actor.inbox.startswith(stack.instance_origin + "/") and actor.handle.endswith(f"@{stack.instance_slug}.{stack.root_name}")


def test_each_name_below_the_root_resolves_and_only_the_gateway_port_is_open(stack):
    """A container on the network ``edge`` with the DNS server of the stack: each name below the root origin is the gateway, and only port 443 is open. DrFed does not listen on its address of ``edge``."""
    names = stack.probe(f"for n in {stack.root_name} {stack.instance_slug}.{stack.root_name} a-new-name.{stack.root_name}; do nslookup $n | awk '/^Address/ && !/:53/ {{print $2}}' | tail -1; done").stdout.split()
    assert names == [stack.gateway_ip] * 3
    ports = stack.probe(f"for p in 443 80 8888; do nc -z -w 2 any.{stack.root_name} $p; echo $p=$?; done; nc -z -w 2 {stack.edge_prefix}.4 8888; echo drfed=$?").stdout.split()
    assert ports == ["443=0", "80=1", "8888=1", "drfed=1"]
    published = stack.compose("ps", "--format", "json").stdout
    assert "0.0.0.0" not in published and "[::]" not in published and f"127.0.0.1:{stack.loopback_port}" in published


def test_tls_uses_the_wildcard_certificate_of_the_private_authority(stack):
    with stack.client(token=False) as trusted:
        assert trusted.get(stack.instance_origin + "/.well-known/nodeinfo").status_code == 200
    # A client that does not know the certificate authority stops before a request goes out.
    with httpx.Client(transport=httpx.HTTPTransport(), timeout=10) as unknown, pytest.raises(httpx.ConnectError):
        unknown.get(f"https://{stack.gateway_ip}/", headers={"host": stack.root_name})


def test_the_gateway_keeps_the_host_header(stack):
    """DrFed selects the surface from the Host header: an instance name serves ActivityPub only, and the root name serves GraphQL."""
    actor = stack.actors[0]
    with stack.client(token=False) as client:
        assert client.get(actor.iri, headers=AS_JSON).json()["id"] == actor.iri
        assert client.post(stack.instance_origin + "/graphql", json={"query": "{ __typename }"}).status_code == 404
        assert client.post(stack.root_origin + "/graphql", json={"query": "{ __typename }"}).status_code == 200


# --- What a remote server reads from DrFed ----------------------------------------


def test_actor_webfinger_and_a_public_note_are_readable(stack):
    actor = stack.actors[0]
    user = actor.handle.strip("@").split("@")[0]
    with stack.client(token=False) as client:
        finger = client.get(stack.instance_origin + "/.well-known/webfinger", params={"resource": f"acct:{user}@{stack.instance_slug}.{stack.root_name}"}).json()
        assert {"rel": "self", "href": actor.iri, "type": "application/activity+json"} in finger["links"]
        document = client.get(actor.iri, headers=AS_JSON).json()
        assert document["type"] == "Person" and document["inbox"] == actor.inbox and document["preferredUsername"] == user
        assert document["publicKey"]["owner"] == actor.iri and "BEGIN PUBLIC KEY" in document["publicKey"]["publicKeyPem"]
    made = stack.graphql(CREATE_OBJECT, {"actor": actor.id, "html": "<p>A public note of DrFed.</p>", "to": [PUBLIC]})["createObject"]
    assert made["__typename"] == "Object", made
    with stack.client(token=False) as client:
        note = client.get(made["iri"], headers=AS_JSON).json()
        assert note["type"] == "Note" and note["attributedTo"] == actor.iri and note["content"] == "<p>A public note of DrFed.</p>"
        # DrFed writes the public collection in the short form. The three forms have the same meaning (design Section 6.2 rule 1).
        assert {PUBLIC, "as:Public", "Public"} & set([note["to"]] if isinstance(note["to"], str) else note["to"])
        # DrFed 0.1.0 gives no ``replies`` and no ``context`` collection: the thread fetch (unit FP4-6) has nothing to read there. A later DrFed release that adds one makes this line fail; add the thread fetch to the scenarios then.
        assert "replies" not in note and "context" not in note
        outbox = client.get(document["outbox"], headers=AS_JSON).json()
        assert outbox["type"] == "OrderedCollection" and outbox["totalItems"] >= 1
