# Naming Conventions & Folder Structure

Consistent file names and folder layout let you infer a document's type and find
the current version. Names also drive doc-type detection: reading the filename to
decide which rules to apply.

## File naming convention

Use: `YYYY-MM-DD_<type>_<short-name>_v<version>.<ext>`

Examples:
- `2026-04-12_policy_information-security_v3.docx`
- `2025-11-01_contract_acme-msa_v1.pdf`
- `2026-03-15_status_project-atlas-weekly.xlsx`

Rules:
- Start with the ISO date (`YYYY-MM-DD`) of the last meaningful change.
- Avoid `FINAL`, `FINAL2`, `latest`, `copy`, `borrador`, `revised` — version with
  `v1/v2/v3` instead. Two files that differ only by `FINAL`/`v1` are duplicates and
  will be flagged.
- One canonical version per document; archive superseded versions.

## Type keywords detected in filenames

To be auto-classified correctly, include one of these keywords:

| Detected type | Filename keywords |
|---|---|
| Policy | policy |
| Procedure | procedure, sop |
| Contract | contract, agreement, nda, msa |
| Data dictionary | data dictionary, data_dictionary |
| Data contract | data contract, data_contract |
| Project charter | charter |
| Status report | status, weekly, report |
| RAID log | raid, risk log |
| OKR | okr, objectives |
| Asana task export | asana, task_export |
| Business Central record | business central, bc_, navision, dynamics |

A filename with no recognised keyword is treated as `unknown` and gets the default
540-day staleness threshold and `default` metadata requirements.

## Folder structure

Organise by department, then by document type:

```
/<Department>/<DocumentType>/<files>
  HR/Contracts/        HR/Policies/
  Engineering/Specs/   Engineering/Procedures/
  Finance/Reports/     Finance/Budgets/
  Legal/Contracts/     Legal/Policies/
```

Do not leave documents in the department root or in personal folders; misfiled
documents escape ownership and review.
