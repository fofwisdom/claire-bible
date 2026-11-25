// Collections of an own published object: replies, likes, and shares (DOCUMENT_RENDITIONS_AND_PUBLICATION_DESIGN.md Section 5.11 A, phase E6, contract 1.19).
//
// The object (Article or Note) has `replies` as the address of the collection, and `likes` and `shares` as collections with the number of their items (the form of Mastodon).
// Claire makes each collection document (`GET /objects/{id}/collections/{name}` of the internal API); the sidecar gives it at `/ap/objects/{id}/{name}` as it is.
// A server that crawls reply trees (Mastodon 4.5 and later) reads `replies` to get the thread of a Claire object.

import { Collection } from "@fedify/fedify/vocab";

export type ObjectCollectionName = "replies" | "likes" | "shares";

// The public path of a collection of an own object.
export const OBJECT_COLLECTION_PATH = /^\/ap\/objects\/([1-9][0-9]{0,18})\/(replies|likes|shares)$/;

// The three properties of an own object. `id` is the object ID.
export function objectCollections(id: URL, counts: { likes: number; shares: number }): { replies: URL; likes: Collection; shares: Collection } {
  return {
    replies: new URL(`${id.href}/replies`),
    likes: new Collection({ id: new URL(`${id.href}/likes`), totalItems: counts.likes }),
    shares: new Collection({ id: new URL(`${id.href}/shares`), totalItems: counts.shares }),
  };
}
