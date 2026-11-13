# Third-Party Licenses of the Fedify Sidecar

This file lists each dependency in `deno.lock`. The rule is in Section 4.2 of the design [`ACCOUNT_IDP_AND_FEDIFY_SIDECAR_INTEGRATION_DESIGN.md`](../../docs/design/ACCOUNT_IDP_AND_FEDIFY_SIDECAR_INTEGRATION_DESIGN.md): a sidecar dependency must have the MIT, Apache-2.0, BSD, or ISC license. Other licenses need owner approval.

Update this file when `deno.lock` changes. The test `tests/test_federation_compose.py` fails when a package in `deno.lock` is not in the tables below.

## License Files in the Image

- The image build copies the license files of each npm package from the Deno module cache to `/app/licenses/{package}/{version}/`.
- The JSR packages do not have a license file in their published file list. Their `deno.json` metadata gives the license. The image keeps this file as `/app/THIRD_PARTY_LICENSES.md`.

## JSR Packages

The source of the license is the field `license` in the `deno.json` file of the package version on the JSR registry.

| Package | Version | License |
| :--- | :--- | :--- |
| `@fedify/fedify` | 2.4.5 | MIT |
| `@fedify/sqlite` | 2.4.5 | MIT |
| `@fedify/uri-template` | 2.4.5 | MIT |
| `@fedify/vocab-runtime` | 2.4.5 | MIT |
| `@fedify/vocab` | 2.4.5 | MIT |
| `@fedify/webfinger` | 2.4.5 | MIT |
| `@logtape/logtape` | 2.4.2 | MIT |
| `@standard-schema/spec` | 1.1.0 | MIT |

## npm Packages

The source of the license is the field `license` in `package.json`. The license file of each package agrees with that field.

| Package | Version | License | License files |
| :--- | :--- | :--- | :--- |
| `@digitalbazaar/http-client` | 4.4.0 | BSD-3-Clause | LICENSE |
| `@multiformats/base-x` | 4.0.1 | MIT | LICENSE.md |
| `@noble/hashes` | 1.8.0 | MIT | LICENSE |
| `@opentelemetry/api` | 1.9.1 | Apache-2.0 | LICENSE |
| `@opentelemetry/core` | 2.7.1 | Apache-2.0 | LICENSE |
| `@opentelemetry/resources` | 2.7.1 | Apache-2.0 | LICENSE |
| `@opentelemetry/sdk-metrics` | 2.7.1 | Apache-2.0 | LICENSE |
| `@opentelemetry/semantic-conventions` | 1.45.0 | Apache-2.0 | LICENSE |
| `asn1js` | 3.1.0 | BSD-3-Clause | LICENSE |
| `byte-encodings` | 1.0.11 | MIT | LICENSE |
| `bytestreamjs` | 2.0.1 | BSD-3-Clause | LICENSE |
| `canonicalize` | 2.1.0 | Apache-2.0 | LICENSE |
| `devalue` | 5.9.4 | MIT | LICENSE |
| `es-toolkit` | 1.53.2 | MIT | LICENSE, NOTICE |
| `json-canon` | 1.0.1 | Apache-2.0 | LICENSE |
| `jsonld` | 9.0.0 | BSD-3-Clause | LICENSE |
| `ky` | 1.14.3 | MIT | license |
| `lru-cache` | 6.0.0 | ISC | LICENSE |
| `pkijs` | 3.4.3 | BSD-3-Clause | LICENSE |
| `pvtsutils` | 1.3.6 | MIT | LICENSE |
| `pvutils` | 1.2.0 | MIT | LICENSE |
| `rdf-canonize` | 5.0.0 | BSD-3-Clause | LICENSE |
| `setimmediate` | 1.0.5 | MIT | LICENSE.txt |
| `structured-field-values` | 2.0.4 | MIT | LICENSE |
| `tslib` | 2.9.1 | 0BSD | LICENSE.txt |
| `undici` | 6.29.1 | MIT | LICENSE |
| `undici` | 7.30.2 | MIT | LICENSE |
| `yallist` | 4.0.0 | ISC | LICENSE |

## Items for the Owner

- `tslib` 2.9.1 has the license 0BSD (BSD Zero Clause). It is a BSD-family license without the attribution condition. The design list names "BSD" and does not name 0BSD: the owner must confirm that 0BSD is in the permitted set.
- The JSR packages (`@fedify/*`, `@logtape/logtape`) declare MIT but publish no license file. Thus the image has no copy of the MIT text with their copyright lines.

## Runtime

The base image is `denoland/deno`. The Deno runtime has the MIT license. The base image keeps the license data of its own components.
