// Outbound interactions of the sidecar (FEDERATION_REMOTE_READING_DESIGN.md Sections 5.2 and 5.4, contract 1.6, unit FP4-9): the Note of an own note, and the handoff of the tasks note, update_note, delete_note, like, undo_like, announce, and undo_announce.
//
// Rules of this module:
// - Claire is canonical. The HTML of a note comes from Claire, made from plain text and sanitized. The sidecar adds the addressing, the tags, and the language map.
// - A note is public: `to` is the public collection, `cc` has the followers collection, the mentioned actors, and the group of the thread. A reply has `inReplyTo` and a Mention of the author of the parent (Section 5.4 rule 1).
// - A handle without an actor IRI is resolved with WebFinger. A handle that does not resolve, or an actor on a blocked domain, gets no tag and no delivery; the note is sent without it. The resolved actors go to Claire with the acknowledgement.
// - With a language, the Note has `content` and `contentMap` (RT8).
// - Each activity has a stable ID, so that a second handoff of the same task makes the same activity: {object}#create, {object}#updates/{ms}, {object}#delete, {actor}#likes/{n}, {actor}#announces/{n}, and {...}/undo for the Undo of the last two.
// - A fetch of a remote actor is signed with the key of the actor (servers with authorized fetch).

import type { Context } from "@fedify/fedify";
import { type Activity, type Actor, Announce, Create, Delete, Hashtag, isActor, LanguageString, Like, Mention, Note, PUBLIC_COLLECTION, type Recipient, Tombstone, Undo, Update } from "@fedify/fedify/vocab";
import type { InteractionTask, InternalApi, NoteRecord, ResolvedMention } from "./internal_api.ts";

// The parts of the Sidecar that this module uses.
export interface InteractionDeps {
  api: InternalApi;
  // True when a blocked domain applies to the host.
  isBlocked(host: string): Promise<boolean>;
  // The followers of an actor that can get a delivery.
  deliveryRecipients(identifier: string): Promise<Recipient[]>;
}

// A mention that has an actor IRI: it becomes a Mention tag and an entry of `cc`.
export interface NoteMention {
  handle: string | null;
  iri: string;
}

function instant(seconds: number): Temporal.Instant {
  return Temporal.Instant.fromEpochMilliseconds(Math.round(seconds * 1000));
}

// The ID of an own note: the object path of the published documents (RT7).
export function noteUri(ctx: Context<void>, record: Pick<NoteRecord, "public_id" | "actor_public_id">): URL {
  return new URL(`/ap/objects/${record.public_id}`, ctx.getActorUri(record.actor_public_id));
}

// The mentions of a record that have an actor IRI. The object path uses them: it makes no network request.
export function knownMentions(record: NoteRecord): NoteMention[] {
  return record.mentions.filter((m): m is NoteMention => m.iri !== null);
}

// The Note of an own note. The same form is in the delivery and at the object path.
export function buildNote(ctx: Context<void>, record: NoteRecord, mentions: NoteMention[]): Note {
  const id = noteUri(ctx, record);
  const ccs = [ctx.getFollowersUri(record.actor_public_id), ...mentions.map((m) => new URL(m.iri))];
  if (record.audience !== null) ccs.push(new URL(record.audience));
  return new Note({
    id,
    attribution: ctx.getActorUri(record.actor_public_id),
    contents: record.language === null ? [record.content] : [record.content, new LanguageString(record.content, record.language)],
    summary: record.summary,
    sensitive: record.sensitive,
    replyTarget: record.in_reply_to === null ? null : new URL(record.in_reply_to),
    audience: record.audience === null ? null : new URL(record.audience),
    tags: [...mentions.map((m) => new Mention({ href: new URL(m.iri), name: m.handle ?? m.iri })), ...record.tags.map((t) => new Hashtag({ href: new URL(t.href), name: t.name }))],
    url: id,
    published: instant(record.published_at),
    // Only an edited note has `updated` (Section 5.4 rule 12).
    updated: record.edited_at === null ? null : instant(record.edited_at),
    to: PUBLIC_COLLECTION,
    ccs,
  });
}

// Gives one interaction task to Fedify as an activity and sends the acknowledgement. An error (the internal API is not available, the actor has no key) goes to the caller: the task has no acknowledgement and comes again after its lease.
export async function handOffInteraction(deps: InteractionDeps, ctx: Context<void>, task: InteractionTask): Promise<"handed_off" | "rejected"> {
  const reject = async (code: string): Promise<"rejected"> => {
    console.warn(`interaction task ${task.id} (${task.kind}) rejected: ${code}`);
    await deps.api.ackInteractionTask(task.id, "rejected", code);
    return "rejected";
  };
  const sender = { identifier: task.actor_public_id };
  const actorUri = ctx.getActorUri(task.actor_public_id);
  const followersUri = ctx.getFollowersUri(task.actor_public_id);
  const documentLoader = await ctx.getDocumentLoader(sender);
  // A remote actor that can get a delivery, or null: the address does not give an actor with an inbox, or a blocked domain applies to it.
  const lookup = async (target: string): Promise<Actor | null> => {
    let found: unknown = null;
    try {
      found = await ctx.lookupObject(target, { documentLoader });
    } catch {
      found = null;
    }
    if (!isActor(found) || found.id === null || found.inboxId === null) return null;
    const hosts = [found.id, found.inboxId, found.endpoints?.sharedInbox ?? null].filter((url) => url !== null).map((url) => url.hostname);
    for (const host of hosts) if (await deps.isBlocked(host)) return null;
    return found;
  };
  // One delivery for each actor, also when an actor is a follower and a mention.
  const send = async (recipients: (Recipient | Actor)[], activity: Activity, orderingKey: string): Promise<void> => {
    const seen = new Set<string>();
    const unique = recipients.filter((r) => r.id !== null && !seen.has(r.id.href) && seen.add(r.id.href) !== undefined);
    if (unique.length > 0) await ctx.sendActivity(sender, unique, activity, { preferSharedInbox: true, orderingKey });
  };

  if (task.kind === "note" || task.kind === "update_note") {
    const result = await deps.api.note(task.ref_id);
    if (result === null) return await reject("note_not_found");
    // The owner deleted the note before the handoff: its delete_note task follows.
    if (result.state === "gone") return await reject("note_gone");
    const record = result.record;
    if (record.actor_public_id !== task.actor_public_id) return await reject("actor_mismatch");
    const mentions: NoteMention[] = [];
    const resolved: ResolvedMention[] = [];
    const direct: Actor[] = [];
    for (const mention of record.mentions) {
      const target = mention.iri ?? mention.handle;
      if (target === null) continue;
      const actor = await lookup(target);
      if (actor !== null) {
        direct.push(actor);
        mentions.push({ handle: mention.handle, iri: actor.id!.href });
        if (mention.iri === null && mention.handle !== null) resolved.push({ handle: mention.handle, iri: actor.id!.href });
      } else if (mention.iri !== null && !(await deps.isBlocked(new URL(mention.iri).hostname))) {
        // The actor document is not available now. The tag stays: Claire knows the actor.
        mentions.push({ handle: mention.handle, iri: mention.iri });
      }
    }
    // A reply to a thread of a group also goes to the group (FEP-1b12).
    const group = record.audience === null ? null : await lookup(record.audience);
    if (group !== null) direct.push(group);
    const note = buildNote(ctx, record, mentions);
    const objectUri = noteUri(ctx, record);
    const addressing = { actor: actorUri, to: PUBLIC_COLLECTION, ccs: note.ccIds };
    // The ID of an Update has the time of the change: each edit is one activity.
    const activity = task.kind === "note" ? new Create({ id: new URL("#create", objectUri), object: note, published: instant(record.published_at), ...addressing }) : new Update({ id: new URL(`#updates/${Math.round(record.updated_at * 1000)}`, objectUri), object: note, published: instant(record.updated_at), ...addressing });
    await send([...(await deps.deliveryRecipients(task.actor_public_id)), ...direct], activity, objectUri.href);
    await deps.api.ackInteractionTask(task.id, "handed_off", null, resolved);
    return "handed_off";
  }

  if (task.kind === "delete_note") {
    const objectUri = noteUri(ctx, { public_id: task.ref_id, actor_public_id: task.actor_public_id });
    const activity = new Delete({ id: new URL("#delete", objectUri), actor: actorUri, object: new Tombstone({ id: objectUri }), to: PUBLIC_COLLECTION, cc: followersUri });
    const direct: Actor[] = [];
    for (const iri of task.recipients) {
      const actor = await lookup(iri);
      if (actor !== null) direct.push(actor);
    }
    await send([...(await deps.deliveryRecipients(task.actor_public_id)), ...direct], activity, objectUri.href);
    await deps.api.ackInteractionTask(task.id, "handed_off");
    return "handed_off";
  }

  // like, undo_like, announce, undo_announce: the object is a remote object, and its author gets the activity.
  if (task.object_iri === null || task.recipients.length === 0) return await reject("no_object");
  const object = new URL(task.object_iri);
  if (await deps.isBlocked(object.hostname)) return await reject("blocked_domain");
  const author = await lookup(task.recipients[0]);
  if (author === null) return await reject("actor_not_found");
  if (task.kind === "like" || task.kind === "undo_like") {
    const like = new Like({ id: new URL(`#likes/${task.ref_id}`, actorUri), actor: actorUri, object, to: author.id });
    const activity = task.kind === "like" ? like : new Undo({ id: new URL(`#likes/${task.ref_id}/undo`, actorUri), actor: actorUri, object: like, to: author.id });
    await send([author], activity, object.href);
  } else {
    const announce = new Announce({ id: new URL(`#announces/${task.ref_id}`, actorUri), actor: actorUri, object, to: PUBLIC_COLLECTION, ccs: [followersUri, author.id!] });
    const activity = task.kind === "announce" ? announce : new Undo({ id: new URL(`#announces/${task.ref_id}/undo`, actorUri), actor: actorUri, object: announce, to: PUBLIC_COLLECTION, ccs: [followersUri, author.id!] });
    await send([...(await deps.deliveryRecipients(task.actor_public_id)), author], activity, object.href);
  }
  await deps.api.ackInteractionTask(task.id, "handed_off");
  return "handed_off";
}
