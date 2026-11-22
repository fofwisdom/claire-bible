# Path Separation of `/admin` and `/settings` with One Console

- **Document Identifier**: `DESIGN-ADMIN-SETTINGS-CONSOLE-20261104`
- **Status**: Implemented. Design principles only. Current behavior: [`ACCOUNTS.md`](../implementation/ACCOUNTS.md) §5.9, §5.10 and [`API_HTTP_CONTRACT.md`](../implementation/API_HTTP_CONTRACT.md) §2.2.
- **Date**: 2026-11-04 (phases P1 to P5 done by 2026-11-05) · condensed 2026-11-16 · D6 added and implemented 2026-11-22
- **Related**: [`PRINCIPLES.md`](../PRINCIPLES.md), [`MULTI_ACCOUNT_NAMESPACE_AND_SINGLE_FQDN_DESIGN.md`](MULTI_ACCOUNT_NAMESPACE_AND_SINGLE_FQDN_DESIGN.md)

## Goal

Before this design, management screens were in many places: a preferences page of the reader, an account page, a drawer on the root page, and separate administrator pages. The administrator and member controls were mixed on one page. The goal is:

1. One URL prefix for the administrator of the server, and one URL prefix for the settings of the signed-in account.
2. One console shell for both prefixes, so that the user learns one layout.
3. A privilege boundary that the server enforces, not a boundary that only the UI shows.

## Decisions in force

| ID | Decision | Reason |
| :--- | :--- | :--- |
| D1 | The settings of the account are root system paths for the owner, in both server modes. A prefixed path (under `/@{username}` or a theme path) is 404. The word `settings` is a reserved USERNAME. | Settings belong to the credential, not to the namespace in the URL. One canonical URL prevents a second route to the same screen. The reservation stops a handle that collides with the path. |
| D2 | The root page has one console entry for the owner: the administrator console for the administrator, "my settings" for a member. Collaborator, read-only, and anonymous callers do not see it. | The root page is a workspace, not a management page. A member must never see controls that only the administrator can use. |
| D3 | The administrator manages the themes and tokens of the administrator account under the administrator prefix. The same two settings paths redirect the administrator there. The administrator screen shows only server counts for other accounts. | The administrator sees each item one time. The administrator manages the server, not the content of members. |
| D4 | The old account page and the old reader preferences page stay as redirects to the new paths. Each old tab maps to one new section. An unknown tab is 404. | Old bookmarks and links keep working. An unknown value must not fall back to a default tab (C1). |
| D5 | The work was done in phases P1 to P5, each with passing tests. | Process only. Done. |
| D6 | Owner, 2026-11-22. The owner has two prefixes. The account prefix (`/account`) has the pages of the account of the caller. The settings prefix (`/settings`) has the pages of a knowledge base (shared knowledge, syndication). The old account page is two pages: the profile (the data that other persons see) and the security (password, linked identities, deletion). The old key page is the provider page (`/account/providers`); it also has the extraction mode. The MCP and WebMCP pages are divided by the person who acts: the account pages (`/account/mcp`, `/account/webmcp`) for the account, the administrator pages (`/admin/mcp`, `/admin/webmcp`) for the server policy. The old settings paths of the account pages redirect (308). The public account directory is `/directory`; the old path `/accounts` redirects (308). The words `account` and `directory` are reserved USERNAMEs, as `settings` is (D1). | A path name must tell what the page has. `/settings/keys` had more than keys (the server tier, the usage, the extraction mode), and the AI pages are about the account, not about a knowledge base. "Profile" is the name of the public data only, thus it is one page and not the name of the prefix. `/accounts` and `/account` differ by one letter, thus the directory has its own word. |

D2 changed after implementation: the inline drawer panels on the root page were removed. Now the console is the only place for providers, limits, themes, and tokens. Reason: one place for each control.

## Invariants

1. **Stealth 404.** A caller that is not the administrator gets 404 on every administrator path, before any redirect. Never 403 (B1, N5).
2. **No content peeking by the administrator.** Administrator screens manage server configuration, account lifecycle, quotas, and the administrator's own workspace. They never read or change the private themes, feeds, connected tokens, or secret values of a member (K5).
3. **Server-side removal of administrator markup.** A response to a member contains no administrator links and no administrator sections. Do not hide them with CSS. Do not send administrator scripts to a member.
4. **No USERNAME in single mode.** In single mode, the console and the identity endpoint do not show `@username` (S9). The owner withdrew S9 for a later change (CT33, [`KNOWLEDGE_FEDERATION_DESIGN.md`](KNOWLEDGE_FEDERATION_DESIGN.md) §13, not implemented). When that change is implemented, this invariant ends.
5. **Literal route policy.** Each console path is one literal entry in the route policy, so that the route contract test can compare them one to one.
6. **Fail closed.** An unknown section or an invalid theme reference is 404. Never fall back to the default theme or to a default section (C1).

## Rejected alternatives

- **Separation in the UI only** (one page that hides administrator panels for members): a hidden panel still sends markup and scripts to the member, and the server does not enforce the boundary.
- **Settings under the namespace prefix** (`/@{username}/settings`): the settings follow the credential, so a prefix adds a second URL and a possible mismatch between the URL account and the credential account.
- **Keep the drawer panels on the root page as well as the console** (first form of D2): two copies of the same controls drift apart.

## Limits for later changes

- A new administrator section goes under the administrator prefix and follows invariants 1 to 3.
- A new page of the account goes under the account prefix (D6). A new setting of a knowledge base goes under the settings prefix; it validates the theme reference of the caller and fails closed.
- Keep the old redirects (D4) until the owner decides to remove them.
- The layout and the navigation of the console shell (groups, tabs, scroll regions) change in [`CONSOLE_LAYOUT_DESIGN.md`](CONSOLE_LAYOUT_DESIGN.md) (planned). That design keeps the decisions and the invariants of this document.
