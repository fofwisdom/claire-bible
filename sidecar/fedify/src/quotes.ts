// Quotes of the sidecar (FEP-044f; FEDERATION_REMOTE_READING_DESIGN.md Sections 5.4 rules 5 and 6, 6.6 rule 5, and the result RT9; contract 1.7; unit FP4-9Q).
//
// Rules of this module:
// - Claire is canonical: it has the quote policy of each own object, the state of each request, and the state of each quote of an own note. The sidecar converts activities and stores nothing.
// - canQuote of the interaction policy comes from the quote policy (RT9): public → automatic approval for the public collection; followers → automatic approval for the followers collection; manual → automatic approval for the author and manual approval for the public collection; nobody → automatic approval for the author only.
// - A QuoteAuthorization is at /ap/quote-authorizations/{id}. It has the IDs of the quoting object and of the quoted object, and it does not embed them.
// - An Accept or a Reject of a quote request of an own note is matched by `objectId`, which is {note}#quote-request. `getObject()` is never called for it: Fedify fetches an embedded object of another origin again (RT9).
// - The QuoteAuthorization in `result` of an Accept is read with the loader of Fedify: an embedded object of another origin is fetched again, and a private network address is refused. Claire makes the final checks (the author, the origin, the quoting note, the quoted object).
// - Each activity has a stable ID, so that a second handoff of the same task makes the same activity: {note}#quote-request, {actor}#quote-accepts/{n}, {actor}#quote-rejects/{n}, {actor}#quote-revokes/{n}.

import type { Context, DocumentLoader } from "@fedify/fedify";
import { Accept, type Activity, type Actor, Delete, InteractionPolicy, InteractionRule, type Note, PUBLIC_COLLECTION, QuoteAuthorization, QuoteRequest, Reject } from "@fedify/fedify/vocab";
import type { InteractionTask, QuoteAuthorizationRecord, QuoteEvent, QuotePolicy } from "./internal_api.ts";

// The fragment of the ID of the QuoteRequest of an own note.
export const QUOTE_REQUEST_FRAGMENT = "#quote-request";

// The interaction policy of an own object with canQuote from its quote policy.
export function quoteInteractionPolicy(ctx: Context<void>, actorPublicId: string, policy: QuotePolicy): InteractionPolicy {
  const author = ctx.getActorUri(actorPublicId);
  const rules: Record<QuotePolicy, InteractionRule> = {
    public: new InteractionRule({ automaticApprovals: [PUBLIC_COLLECTION] }),
    followers: new InteractionRule({ automaticApprovals: [ctx.getFollowersUri(actorPublicId)] }),
    manual: new InteractionRule({ automaticApprovals: [author], manualApprovals: [PUBLIC_COLLECTION] }),
    nobody: new InteractionRule({ automaticApprovals: [author] }),
  };
  return new InteractionPolicy({ canQuote: rules[policy] });
}

// The public address of an authorization.
export function quoteAuthorizationUri(ctx: Context<void>, id: string): URL {
  return ctx.getObjectUri(QuoteAuthorization, { id });
}

// The QuoteAuthorization of an approved request. The same form is in the Accept and at the public path.
export function buildQuoteAuthorization(ctx: Context<void>, record: QuoteAuthorizationRecord): QuoteAuthorization {
  return new QuoteAuthorization({
    id: quoteAuthorizationUri(ctx, record.id),
    attribution: ctx.getActorUri(record.actor_public_id),
    interactingObject: new URL(record.quoting_object_iri),
    interactionTarget: new URL(`/ap/objects/${record.object_public_id}`, ctx.getActorUri(record.actor_public_id)),
  });
}

// The QuoteRequest of an own note for the author of the quoted object. `instrument` is the quoting Note.
export function buildQuoteRequest(noteId: URL, actor: URL, quoted: URL, author: URL, note: Note): QuoteRequest {
  return new QuoteRequest({ id: new URL(QUOTE_REQUEST_FRAGMENT, noteId), actor, object: quoted, instrument: note, to: author });
}

// The activity of a quote_authorize, quote_reject, or quote_revoke task for the quoting actor, or the reason why the task has none.
export function buildQuoteAnswer(ctx: Context<void>, task: InteractionTask, quoting: Actor): Activity | "no_quote" {
  if (task.quote === null || task.object_iri === null || quoting.id === null) return "no_quote";
  const actor = ctx.getActorUri(task.actor_public_id);
  const authorization = quoteAuthorizationUri(ctx, task.ref_id);
  // The request of the remote actor, with IDs only. The receiver finds its request by the ID.
  const request = new QuoteRequest({ id: new URL(task.quote.request_iri), actor: quoting.id, object: new URL(task.object_iri), instrument: new URL(task.quote.quoting_object_iri) });
  if (task.kind === "quote_authorize") {
    const result = new QuoteAuthorization({ id: authorization, attribution: actor, interactingObject: new URL(task.quote.quoting_object_iri), interactionTarget: new URL(task.object_iri) });
    return new Accept({ id: new URL(`#quote-accepts/${task.ref_id}`, actor), actor, to: quoting.id, object: request, result });
  }
  if (task.kind === "quote_reject") return new Reject({ id: new URL(`#quote-rejects/${task.ref_id}`, actor), actor, to: quoting.id, object: request });
  // quote_revoke: the Delete of the authorization. The public path gives 410 from now on.
  return new Delete({ id: new URL(`#quote-revokes/${task.ref_id}`, actor), actor, to: quoting.id, object: authorization });
}

type EventBase = Pick<QuoteEvent, "event_id" | "recipient_public_id" | "actor" | "verified_by">;

// The quote event of a verified activity, or null when the activity is not a part of the quote protocol for this server (then it goes to Claire as before).
// QuoteRequest for an object of this server; Accept or Reject of the quote request of an own note.
// An error (the authorization of an Accept is not available now) goes to the caller: the Fedify inbox queue tries again.
export async function quoteEventOf(activity: Activity, origin: string, base: EventBase, loaders: { documentLoader: DocumentLoader; contextLoader: DocumentLoader }): Promise<QuoteEvent | null> {
  const objectId = activity.objectId;
  if (objectId === null || !objectId.href.startsWith(`${origin}/`)) return null;
  // The fields are in the sequence of the contract.
  const event = (type: QuoteEvent["type"], instrument: string | null, authorization: QuoteEvent["authorization"]): QuoteEvent => ({ event_id: base.event_id, type, recipient_public_id: base.recipient_public_id, actor: base.actor, object_iri: objectId.href, instrument_iri: instrument, authorization, verified_by: base.verified_by });
  if (activity instanceof QuoteRequest) return event("QuoteRequest", activity.instrumentId?.href ?? null, null);
  if (!(activity instanceof Accept || activity instanceof Reject) || objectId.hash !== QUOTE_REQUEST_FRAGMENT) return null;
  if (activity instanceof Reject) return event("Reject", null, null);
  const resultId = activity.resultId;
  if (resultId === null) return event("Accept", null, null);
  const result = await activity.getResult({ ...loaders, suppressError: true });
  if (result === null) throw new Error(`the quote authorization ${resultId.href} is not available`);
  // An object of another type, or with another ID, is not an authorization: Claire refuses the Accept.
  const valid = result instanceof QuoteAuthorization && result.id?.href === resultId.href;
  return event("Accept", null, { iri: resultId.href, interacting_object: valid ? result.interactingObjectId?.href ?? null : null, interaction_target: valid ? result.interactionTargetId?.href ?? null : null, attributed_to: valid ? result.attributionId?.href ?? null : null });
}
