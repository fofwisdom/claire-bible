// Renditions of a published document in the sidecar (DOCUMENT_RENDITIONS_AND_PUBLICATION_DESIGN.md Section 5.5, phase E4b, contract 1.20).
//
// - The context document https://w3id.org/claire-bible/ns/v2 is part of the code: claire_context_v2.json is a copy of src/bible/static/ns/v2.jsonld of Claire, and a Python test fails when the two are different. The loaders of Fedify get it from the table of contexts, never from the network.
// - The object at /ap/objects/{id} has the claire: fields (the template, the body address and hash, the source key, and the collection of the renditions). Fedify gives a dispatched object without compaction, so the fields stay. A delivered Create or Update has no claire: field: Fedify compacts each activity before it sends it and drops the terms that its vocabulary does not know. A Claire server reads the object at its ID (Section 5.4 rule 2: pull first).

import type { DocumentLoader } from "@fedify/fedify";
import { Article } from "@fedify/fedify/vocab";
import { preloadedContexts } from "jsr:@fedify/vocab-runtime@^2.4.6";
import claireContextV2 from "./claire_context_v2.json" with { type: "json" };
import type { ObjectRecord } from "./internal_api.ts";

export const CLAIRE_CONTEXT_V2_IRI = "https://w3id.org/claire-bible/ns/v2";
(preloadedContexts as Record<string, unknown>)[CLAIRE_CONTEXT_V2_IRI] = claireContextV2;

// The claire: fields of an own published object. objectUri gives the ID of an object by its number.
export function renditionFields(id: URL, record: ObjectRecord, objectUri: (publicId: string) => URL): Record<string, unknown> {
  const fields: Record<string, unknown> = {};
  if (record.rendition_of !== null) fields["claire:renditionOf"] = objectUri(record.rendition_of).href;
  else fields["claire:renditions"] = `${id.href}/renditions`;
  if (record.template !== null) fields["claire:template"] = record.template;
  if (record.template_version !== null) fields["claire:templateVersion"] = record.template_version;
  if (record.body_hash !== null) {
    fields["claire:body"] = `${id.href}/body`;
    fields["claire:bodyHash"] = record.body_hash;
  }
  if (record.source_key !== null && record.source_id_version !== null) {
    fields["claire:sourceKey"] = record.source_key;
    fields["claire:sourceIdVersion"] = record.source_id_version;
  }
  return fields;
}

// The Article of the object path: the Article of Fedify with the claire: fields and the context of version 2.
export async function withRenditionFields(article: Article, fields: Record<string, unknown>, loaders: { contextLoader: DocumentLoader }): Promise<Article> {
  const compact = await article.toJsonLd({ format: "compact", contextLoader: loaders.contextLoader }) as Record<string, unknown>;
  const context = Array.isArray(compact["@context"]) ? compact["@context"] : [compact["@context"]];
  return await Article.fromJsonLd({ ...compact, "@context": [...context, CLAIRE_CONTEXT_V2_IRI], ...fields }, { contextLoader: loaders.contextLoader });
}
