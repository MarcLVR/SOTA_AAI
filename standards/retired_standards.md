# Retired Standards & Obsolete Content Markers

DAVE flags documents that reference frameworks, regulations, or software versions
that have been superseded, and documents that still contain draft/placeholder
markers. Referencing a retired standard is a compliance and quality risk: the
document is giving guidance that no longer matches the current requirement.

## Superseded standards — do not reference as current

| Retired reference | Replaced by | Why it matters |
|---|---|---|
| ISO 27001:2013 / ISO/IEC 27001:2013 | ISO/IEC 27001:2022 | Information security controls were restructured in 2022. |
| ISO 9001:2008 | ISO 9001:2015 | Quality management requirements changed in 2015. |
| Data Protection Directive 95/46/EC / "GDPR transition period" | GDPR (Regulation 2016/679) | Directive 95/46/EC was repealed; the transition period ended in 2018. |
| "Proposed AI Act" / "draft AI Regulation" / "AI Act proposal 2021" | EU AI Act (in force 2024) | Calling the AI Act a draft/proposal is stale; it is now law. |
| PCI DSS 3.2.1 / PCI-DSS v3 | PCI DSS v4.0 | Payment-card security baseline was updated to v4.0. |
| Windows Server 2012 / Server 2008 | Supported Windows Server release | End of support; references indicate outdated IT procedures. |

If a document cites any of the left-column items as the applicable standard, update
it to the replacement before publishing.

## Obsolete content markers — must be removed before a document is "final"

A document is not finished while it still contains any of these markers:

- `DRAFT — DO NOT DISTRIBUTE`
- `CONFIDENTIAL DRAFT`
- `for review by ...`
- `TODO`
- `TBD`
- `[INSERT ...]` (any bracketed insert placeholder)
- `Lorem ipsum` (filler text)

These markers signal unfinished or placeholder content. A document published with
them is treated as low quality and is flagged for the owner to complete.
