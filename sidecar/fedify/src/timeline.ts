// The normal form of a remote object for the timelines of the accounts (FEDERATION_REMOTE_READING_DESIGN.md Sections 6.2 and 6.3, unit FP4-4).
//
// Rules of this module:
// - The sidecar sends a normal form to Claire, not the raw JSON-LD. Claire stores the HTML as it is and sanitizes it only when it renders it.
// - An object is public when `to` or `cc` has the public collection. Fedify reads the short forms `as:Public` and `Public` as the same IRI.
// - The handle of an actor is `@preferredUsername@host of the actor ID`. No WebFinger request is made for it.
// - An attachment that is not in the object document is not fetched.
// - The limits of Section 6.3 apply here and in Claire: an object with more content than the limit has no normal form, and only the first attachments within the limit are kept.

import type { DocumentLoader } from "@fedify/fedify";
import { type Actor, Article, Document, getTypeId, LanguageString, Link, Note, type Object as ASObject, Page, PUBLIC_COLLECTION, Question } from "@fedify/fedify/vocab";
import type { TimelineActor, TimelineObject } from "./internal_api.ts";

export const CONTENT_MAX_BYTES = 64 * 1024;
export const MAX_ATTACHMENTS = 8;

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

const noFetch: DocumentLoader = (url) => Promise.reject(new Error(`no fetch of ${url}`));

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
  };
}
