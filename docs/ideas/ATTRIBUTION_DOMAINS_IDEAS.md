# Ideas: `attributionDomains` in the Actor Document

- **Status**: Ideas only. They have no design for implementation. Do not implement from this document. A design starts only after an owner decision.
- **Date**: 2026-11-26
- **Basis**: owner decision O23 of [`DOCUMENT_RENDITIONS_AND_PUBLICATION_DESIGN.md`](../design/DOCUMENT_RENDITIONS_AND_PUBLICATION_DESIGN.md) (2026-11-26: "attributionDomains는 구상 문서에 넣어두고 Fedify의 업데이트를 기다려보지요"). It was the phase E2b of that design.
- **Related**: [`RENDITIONS_HANDOFF.md`](../implementation/RENDITIONS_HANDOFF.md) Section 8, [`FEDERATION.md`](../implementation/FEDERATION.md) Sections 4.1 (the actor document) and 8.5 (the `update_actor` task).

---

## 1. The Idea

Mastodon shows an author byline in the link preview of a page when the page has `<meta name="fediverse:creator" content="@{username}@{host}">` and the actor of that handle lists the domain of the page in `attributionDomains`. Claire has the meta element on the Silvercord page of a published document since phase E2. The actor document of an account would list the host of the server in `attributionDomains`, so that a link to a published document shows the byline of its author.

## 2. Facts (2026-11-26)

1. Fedify 2.4.6 has no `attributionDomains` property of `Person`. The latest release on JSR on 2026-11-26 was 2.4.6; only development builds of 2.5.0 existed.
2. A probe with a temporary sidecar test: a `Person` made with `Person.fromJsonLd` keeps `attributionDomains` at its ID (the actor dispatcher can serve it). A delivered `Update(Person)` loses it in both forms that were tried (a new `Update` with that person, and `Update.fromJsonLd` of the whole activity): Fedify serializes a delivery again from its typed model.
3. Mastodon stores the list from the actor document and from each `Update(Person)`. Thus an `Update(Person)` without the property clears the list on Mastodon: a change of the served document alone gives a state that changes with each profile update.

## 3. Ways When the Owner Takes It Up

1. **Fedify support (the chosen wait).** When a Fedify release has the property of `Person`, the sidecar sets it in `#person` (`sidecar/fedify/src/sidecar.ts`), so that the actor document and each `Update(Person)` have it. Check each new Fedify release.
2. **Own delivery of `Update(Person)`.** The sidecar sends `Update(Person)` outside Fedify: its own signature, address check, retries, and queue for each follower inbox. Not taken: it replaces a working delivery with retries for a byline label.
3. **`Update` with the actor address only** (unverified). The object of the `Update` is the address of the actor, and each receiver fetches the actor document. Mastodon may do this fetch; other software may not, and then their profile updates stop.
