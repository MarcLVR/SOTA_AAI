# Governance Metadata, Classification & PII

This standard covers the governance metadata every document must carry, the valid
data-classification labels, and the rules for personal data (PII). It feeds the
**governance** dimension of the trust score.

## Required governance metadata by document type

Every document must declare, at minimum, the `default` metadata. Specific types
require more.

| Document type | Required metadata |
|---|---|
| default (all documents) | owner, classification, review_date |
| Policy | owner, classification, review_date, effective_date |
| Data dictionary | owner, classification, retention, steward |
| Data contract | owner, classification, retention, sla |
| Contract | owner, counterparty, expiry_date |
| Business Central record | owner, last_validated, responsibility_center |

A document missing required metadata (no owner, no classification, no review/expiry
date, etc.) is flagged for governance.

## Valid classification labels

A document's `classification` must be exactly one of:

- `public`
- `internal`
- `confidential`
- `restricted`
- `secret`

Any other value (or a missing classification) is non-compliant. Use the lowest
classification that still protects the content; do not over-classify routine docs.

## Personal data (PII) — GDPR

Documents shared in repositories or shared folders **must not** expose raw personal
data unless they are classified `restricted` or `secret` and access is controlled.
The following must be redacted or anonymised in documents intended for wide sharing:

- National ID numbers (DNI/NIE), passport numbers, social-security numbers
- Full home addresses and personal phone numbers
- Personal email addresses and bank account / IBAN numbers
- Health, biometric, or other special-category data

A `confidential`-or-lower document that contains raw PII is a GDPR exposure and is
flagged high severity. The fix is to anonymise the PII or raise and lock down the
classification.
