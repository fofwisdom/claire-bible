// The Claire context documents with a JSON-LD processor (KNOWLEDGE_FEDERATION.md Sections 8 and 9: the check of the context before a server serves it).
//
// Each wire fixture of tests/fixtures/knowledge_federation (the outputs of the builders of Claire) is expanded with jsonld.js 9 (the build that Fedify uses) and compacted again with the same context (design KA4). Each event of jsonld.js (for example a dropped value) is an error. The AS2 context has "@vocab": "_:", so a key that no context defines becomes a blank IRI and is not dropped; the test therefore needs an http or https IRI for each key and each type. Thus each key of each fixture is a term of the AS2 context or of the Claire context. The contexts come from the table of Fedify (the AS2 context of Fedify, the two Claire contexts of the sidecar), never from the network.
// The IRIs of the terms of ForgeFed (Grant, Revoke, capability) are the ones of the ForgeFed specification 1.0 (https://forgefed.org/spec, read on 2026-11-26).
// One event is not an error: `formerType` of a Tombstone has a bare AS2 type name ("Article"), as in the examples of the AS2 vocabulary (Example 142: "formerType": "Image"). The AS2 context defines formerType with "@type": "@id", so a JSON-LD processor reads that value as a relative IRI. This is a limit of the AS2 context, not of the Claire context.

import assert from "node:assert/strict";
import { preloadedContexts } from "jsr:@fedify/vocab-runtime@^2.4.6";
import jsonld from "jsr:@fedify/vocab-runtime@^2.4.6/jsonld";
import "../src/knowledge.ts";
import { renditionFields } from "../src/renditions.ts";
import type { ObjectRecord } from "../src/internal_api.ts";

const FIXTURES = new URL("../../../tests/fixtures/knowledge_federation/", import.meta.url);
const CONTRACT = new URL("../../../tests/fixtures/federation_contract/", import.meta.url);
const CLAIRE = "https://w3id.org/claire-bible/ns#";
const FORGEFED = "https://forgefed.org/ns#";

// deno-lint-ignore no-explicit-any
const documentLoader = (url: string): Promise<any> => {
  const document = (preloadedContexts as Record<string, unknown>)[url];
  if (document === undefined) return Promise.reject(new Error(`a context that is not in the table: ${url}`));
  return Promise.resolve({ contextUrl: null, documentUrl: url, document });
};

// Each event of jsonld.js is an error (stricter than safe mode), except the relative IRI of a bare AS2 type name in formerType (see the header).
const FORMER_TYPES = new Set(["Article", "Note", "Person"]);
let formerTypeEvents = 0;
// deno-lint-ignore no-explicit-any
const eventHandler = ({ event }: { event: any }) => {
  if (event.code === "relative @id reference" && FORMER_TYPES.has(event.details?.id)) {
    formerTypeEvents += 1;
    return;
  }
  throw new Error(`${event.code}: ${JSON.stringify(event.details ?? {})}`);
};

async function fixtures(): Promise<[string, Record<string, unknown>][]> {
  const found: [string, Record<string, unknown>][] = [];
  for (const folder of ["activities", "objects"]) {
    for await (const entry of Deno.readDir(new URL(`${folder}/`, FIXTURES))) {
      if (entry.isFile && entry.name.endsWith(".json")) found.push([`${folder}/${entry.name}`, JSON.parse(await Deno.readTextFile(new URL(`${folder}/${entry.name}`, FIXTURES)))]);
    }
  }
  return found.sort(([a], [b]) => a.localeCompare(b));
}

// deno-lint-ignore no-explicit-any
async function expand(document: Record<string, unknown>): Promise<any[]> {
  return await jsonld.expand(document, { documentLoader, eventHandler } as never);
}

// A document in one form for the comparison: an array of one item is the item (the compaction of JSON-LD gives a single value without an array).
function normal(node: unknown): unknown {
  if (Array.isArray(node)) return node.length === 1 ? normal(node[0]) : node.map(normal);
  if (node !== null && typeof node === "object") return Object.fromEntries(Object.entries(node as Record<string, unknown>).map(([key, value]) => [key, normal(value)]));
  return node;
}

// The IRIs of the expanded document: each key and each type, without the values of @json.
function iris(node: unknown, found = new Set<string>()): Set<string> {
  if (Array.isArray(node)) {
    for (const item of node) iris(item, found);
  } else if (node !== null && typeof node === "object") {
    const record = node as Record<string, unknown>;
    if (record["@type"] === "@json") return found;
    for (const [key, value] of Object.entries(record)) {
      if (!key.startsWith("@")) found.add(key);
      if (key === "@type") for (const type of [value].flat()) found.add(String(type));
      iris(value, found);
    }
  }
  return found;
}

Deno.test("each wire fixture of the knowledge federation expands without a lost term and compacts to the same names", async () => {
  const all = await fixtures();
  assert(all.length >= 19, `fixtures: ${all.length}`);
  for (const [name, document] of all) {
    const expanded = await expand(document).catch((error) => {
      throw new Error(`${name}: ${error.message} ${JSON.stringify(error.details ?? {})}`);
    });
    assert.deepEqual(expanded.length, 1, name);
    // Each Claire term has the Claire namespace; no key stays a relative or a blank IRI.
    for (const iri of iris(expanded)) {
      assert(/^https?:\/\//.test(iri), `${name}: ${iri}`);
    }
    // The compaction with the same context gives the same compact names and values again (design KA4).
    const compacted = await jsonld.compact(expanded, { "@context": document["@context"] } as never, { documentLoader, eventHandler } as never).catch((error: Error & { details?: unknown }) => {
      throw new Error(`${name}: ${error.message} ${JSON.stringify(error.details ?? {}).slice(0, 600)}`);
    });
    assert.deepEqual(normal(compacted), normal(document), name);
  }
  // The handler gets the events: the Tombstones of the fixtures gave the one accepted event.
  assert(formerTypeEvents > 0);
});

Deno.test("the ForgeFed and Claire terms expand to their IRIs", async () => {
  const all = new Map(await fixtures());
  const grant = (await expand(all.get("activities/grant.json")!))[0];
  assert.deepEqual(grant["@type"], [`${FORGEFED}Grant`]);
  assert.deepEqual(grant["https://www.w3.org/ns/activitystreams#object"], [{ "@id": `${CLAIRE}collaborator` }]);
  const revoke = (await expand(all.get("activities/revoke.json")!))[0];
  assert.deepEqual(revoke["@type"], [`${FORGEFED}Revoke`]);
  const offer = all.get("activities/offer_contribution.json")!;
  const withCapability = (await expand({ ...offer, capability: "https://b.example/ap/kb/1846000000000000100#grants/1" }))[0];
  assert.deepEqual(withCapability[`${FORGEFED}capability`], [{ "@id": "https://b.example/ap/kb/1846000000000000100#grants/1" }]);
  const contribution = withCapability["https://www.w3.org/ns/activitystreams#object"][0];
  assert.deepEqual(contribution["@type"], ["https://www.w3.org/ns/activitystreams#Object", `${CLAIRE}Contribution`]);
  // The entities are a JSON value: their keys (type, norm_name, sameAs) are data, not terms.
  const extraction = contribution[`${CLAIRE}document`][0][`${CLAIRE}extraction`][0];
  assert.deepEqual(extraction[`${CLAIRE}entities`][0]["@type"], "@json");
  assert.deepEqual(extraction[`${CLAIRE}entities`][0]["@value"][0].norm_name, "ada lovelace");
  assert.deepEqual(extraction[`${CLAIRE}keyClaims`][0]["@list"].length, 1);
  // formerType of the Tombstone stays the bare type name: a relative IRI for a JSON-LD processor (see the header).
  const deleted = (await expand(all.get("activities/delete_document.json")!))[0];
  assert.deepEqual(deleted["https://www.w3.org/ns/activitystreams#object"][0]["https://www.w3.org/ns/activitystreams#formerType"], [{ "@id": "Article" }]);
  // The AS2 context has "@vocab": "_:", so an unknown term is not dropped: it becomes a blank IRI. The check of the first test (each IRI is an http or https IRI) finds it.
  assert.ok(iris(await expand({ ...offer, unknownTerm: 1 })).has("_:unknownTerm"));
});

Deno.test("the documents of renditions and +1 with the context of version 2 expand without a lost term", async () => {
  const contract = async (name: string) => JSON.parse(await Deno.readTextFile(new URL(name, CONTRACT)));
  const documents: [string, Record<string, unknown>][] = [];
  for (const name of ["source_index.json", "object_body.json", "object_collection_renditions.json", "object_collection_plus_ones.json"]) {
    documents.push([name, (await contract(name)).response.document]);
  }
  // The object at /ap/objects/{id}: the claire: fields that the sidecar adds to the Article of Fedify (a document and a rendition of it).
  const objectUri = (publicId: string) => new URL(`https://claire.example.com/ap/objects/${publicId}`);
  for (const name of ["object.json", "object_rendition.json"]) {
    const record = (await contract(name)).response as ObjectRecord;
    const id = objectUri(record.public_id);
    documents.push([name, { "@context": ["https://www.w3.org/ns/activitystreams", "https://w3id.org/claire-bible/ns/v2"], id: id.href, type: "Article", name: record.name, ...renditionFields(id, record, objectUri) }]);
  }
  for (const [name, document] of documents) {
    const expanded = await expand(document).catch((error) => {
      throw new Error(`${name}: ${error.message} ${JSON.stringify(error.details ?? {})}`);
    });
    assert.equal(expanded.length, 1, name);
    for (const iri of iris(expanded)) assert(/^https?:\/\//.test(iri), `${name}: ${iri}`);
  }
  const rendition = (await expand(documents.at(-1)![1]))[0];
  assert.deepEqual(Object.keys(rendition).filter((key) => key.startsWith(CLAIRE)).sort(), ["body", "bodyHash", "plusOnes", "renditionOf", "sourceIdVersion", "sourceKey", "template", "templateVersion"].map((key) => `${CLAIRE}${key}`));
});
