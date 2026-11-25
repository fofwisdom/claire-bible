# Ideas: Recent Posts of a Newly Followed Actor

- **Status**: Ideas only. They have no design for implementation. Do not implement from this document. A design starts only after an owner decision.
- **Date**: 2026-11-25
- **Source**: Owner decision O9 of [`DOCUMENT_EDITIONS_AND_PUBLICATION_DESIGN.md`](../design/DOCUMENT_EDITIONS_AND_PUBLICATION_DESIGN.md) (answer A, 2026-11-25): the function is not in that design, because it is not about opinions.

---

## Idea

When an account follows a remote actor, the timeline of the account shows only the posts that the actor makes after the follow. FediFetcher (<https://github.com/nanos/FediFetcher>) fills the profiles of new follows, new followers, accounts in notifications, and accounts added to lists with their recent posts. Mastodon fetches the replies of a post when a person opens it, but its documentation does not describe a fill of profiles.

A Claire form of the idea: after an `Accept(Follow)`, read the first page of the `outbox` of the actor, and store the public posts as timeline items with the limits of the thread fetch ([`FEDERATION.md`](../implementation/FEDERATION.md) Section 24).

## Points without an answer

- Which events start a fill: a new follow only, or also a new follower and a mention.
- How many posts, and how old. The retention run of timeline items deletes old items ([`FEDERATION.md`](../implementation/FEDERATION.md) Section 27.4).
- Many servers do not give the items of an `outbox` to a request without a signature, or give only a count.
- The load on the other server when many accounts of this server follow at one time.
