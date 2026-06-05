# Required Sections by Document Type

Each document type must contain a defined set of sections. The auditor checks the document
text for these section headings; missing sections lower the **standards compliance**
dimension of the trust score and produce a finding naming the missing sections.

## Section checklist

| Document type | Required sections |
|---|---|
| Policy | Purpose, Scope, Owner, Effective Date, Review Date |
| Procedure (SOP) | Purpose, Scope, Steps, Owner, Last Updated |
| Contract | Parties, Term, Effective Date, Termination, Signatures |
| Data dictionary | Field Name, Data Type, Description, Owner, Classification |
| Data contract | Schema, Owner, SLA, Classification, Retention |
| Project charter | Objective, Scope, Sponsor, Milestones, Budget |
| Status report | Period, Status, Risks, Next Steps |
| RAID log | Risks, Assumptions, Issues, Dependencies |
| OKR | Objective, Key Results, Owner, Target |

## Notes

- Section names are matched case-insensitively; use the exact wording above as
  headings so the check is unambiguous.
- A contract without **Signatures** or **Termination**, or a policy without a
  **Review Date**, is incomplete and will be flagged.
- Document types not listed here are not section-checked, but still go through the
  staleness, governance, and obsolete-marker checks.
