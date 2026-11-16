// The normal form of a remote object for the timelines of the accounts (FEDERATION_REMOTE_READING_DESIGN.md Sections 6.2 and 6.3, unit FP4-4).
//
// Rules of this module:
// - The sidecar sends a normal form to Claire, not the raw JSON-LD. Claire stores the HTML as it is and sanitizes it only when it renders it.
// - An object is public when `to` or `cc` has the public collection. Fedify reads the short forms `as:Public` and `Public` as the same IRI.
// - The handle of an actor is `@preferredUsername@host of the actor ID`. No WebFinger request is made for it.
// - An attachment that is not in the object document is not fetched.
// - The limits of Section 6.3 apply here and in Claire: an object with more content than the limit has no normal form, and only the first attachments within the limit are kept.
// - A mention of an actor of this server is read from the object document only (unit FP4-8): a Mention tag, or the actor in `to` or `cc`. No tag is fetched.
// - The quoted object of a post is read from the object document only (unit FP4-9Q): `quote` first, then the other keys (RT9).

import type { DocumentLoader } from "@fedify/fedify";
import { type Actor, Article, Document, getTypeId, LanguageString, Link, Mention, Note, type Object as ASObject, Page, PUBLIC_COLLECTION, Question } from "@fedify/fedify/vocab";
import type { TimelineActor, TimelineEvent, TimelineObject } from "./internal_api.ts";

export const CONTENT_MAX_BYTES = 64 * 1024;
export const MAX_ATTACHMENTS = 8;
// The largest number of actors of this server in `mentioned` of one event. Claire refuses a longer list.
export const MAX_MENTIONED = 16;

export type Post = Note | Article | Page | Question;

export interface LoadOptions {
  documentLoader?: DocumentLoader;
  contextLoader?: DocumentLoader;
}

// The post types of Section 6.1. Page is the post type of FEP-1b12 forums. A Question is stored as text without votes.
export function isPost(value: unknown): value is Post {
  return value instanceof Note || value instanceof Article || value instanceof Page || value instanceof Question;
}

export function isPublic(object: ASObject): boolean {
  return [...object.toIds, ...object.ccIds].some((id) => id.href === PUBLIC_COLLECTION.href);
}

function plain(value: string | LanguageString | null, limit: number): string | null {
  return value === null ? null : value.toString().slice(0, limit);
}

function href(value: URL | Link | null): string | null {
  if (value instanceof URL) return value.href;
  return value instanceof Link ? value.href?.href ?? null : null;
}

function seconds(value: Temporal.Instant | null): number | null {
  return value === null ? null : value.epochMilliseconds / 1000;
}

export function actorProfile(actor: Actor): TimelineActor | null {
  if (actor.id === null) return null;
  const username = actor.preferredUsername?.toString() ?? null;
  return { iri: actor.id.href, name: plain(actor.name, 512), handle: username === null ? null : `@${username}@${actor.id.hostname}`.slice(0, 512) };
}

// A loader that refuses each request: only data that is in the document is read.
export const noFetch: DocumentLoader = (url) => Promise.reject(new Error(`no fetch of ${url}`));

// The type of an actor for Claire, or null for a type outside the list of the contract.
export function actorType(actor: Actor): TimelineEvent["actor_type"] {
  const typeId = getTypeId(actor);
  const name = typeId.hash.slice(1) || typeId.href.split("/").pop() || "";
  return (["Person", "Group", "Service", "Application", "Organization"] as const).find((known) => known === name) ?? null;
}

// The actors of this server (`{origin}/ap/actors/{id}`) that a post mentions with a Mention tag or addresses in `to` or `cc`, MAX_MENTIONED at most.
export async function localMentions(post: Post, origin: string, options: LoadOptions): Promise<string[]> {
  const prefix = `${origin}/ap/actors/`;
  const isActor = (iri: string) => iri.startsWith(prefix) && /^[1-9][0-9]{0,18}$/.test(iri.slice(prefix.length));
  const found = new Set<string>();
  for await (const tag of post.getTags({ documentLoader: noFetch, contextLoader: options.contextLoader, suppressError: true })) {
    if (tag instanceof Mention && tag.href !== null && isActor(tag.href.href)) found.add(tag.href.href);
  }
  for (const id of [...post.toIds, ...post.ccIds]) if (isActor(id.href)) found.add(id.href);
  return [...found].slice(0, MAX_MENTIONED);
}

// The object that a post quotes, or null (unit FP4-9Q, RT9). FEP-044f `quote` is first; `quoteUrl` is the fallback, and Fedify reads the keys `quoteUrl`, `_misskey_quote`, and `quoteUri` as that property. A Page has no quote property. Nothing is fetched, and the authorization of the quote is not checked: Claire shows a link only.
export function quotedObject(post: Post): string | null {
  if (post instanceof Page) return null;
  const quoted = post.quoteId ?? post.quoteUrl;
  return quoted === null || (quoted.protocol !== "https:" && quoted.protocol !== "http:") ? null : quoted.href.slice(0, 2048);
}

// The normal form of a post with the given author, or the reason why it has none.
export async function normalObject(post: Post, author: TimelineActor, options: LoadOptions): Promise<TimelineObject | "no_id" | "too_large"> {
  if (post.id === null) return "no_id";
  const content = post.content?.toString() ?? "";
  if (new TextEncoder().encode(content).byteLength > CONTENT_MAX_BYTES) return "too_large";
  const attachments: TimelineObject["attachments"] = [];
  // The loader refuses each request: only an attachment that is in the document is read.
  for await (const attachment of post.getAttachments({ documentLoader: noFetch, contextLoader: options.contextLoader, suppressError: true })) {
    if (attachments.length >= MAX_ATTACHMENTS) break;
    if (!(attachment instanceof Document)) continue;
    const url = href(attachment.url);
    if (url === null) continue;
    attachments.push({ url, media_type: attachment.mediaType?.slice(0, 128) ?? null, name: plain(attachment.name, 4096), width: attachment.width, height: attachment.height, blurhash: null });
  }
  const typeId = getTypeId(post);
  return {
    object_iri: post.id.href,
    url: href(post.url),
    type: (typeId.hash.slice(1) || typeId.href) as TimelineObject["type"],
    author,
    name: plain(post.name, 4096),
    summary: plain(post.summary, 16384),
    sensitive: post.sensitive ?? false,
    content_html: content,
    language: post.content instanceof LanguageString ? post.content.locale.baseName.slice(0, 64) : null,
    published_at: seconds(post.published),
    updated_at: seconds(post.updated),
    attachments,
    context: post.contextIds[0]?.href ?? null,
    in_reply_to: post.replyTargetId?.href ?? null,
    quote: quotedObject(post),
  };
}
