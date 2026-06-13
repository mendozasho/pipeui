# gh command reference (to-issues write step)

Requires `gh` v2.94.0+ — native sub-issue, type, and dependency support.

**No `gh`?** In remote/web environments without the CLI, use the GitHub MCP
equivalents: `issue_write` (create/edit, labels), `sub_issue_write` (parenting),
`issue_read`/`list_issues` (reconciliation read-back). Dependency (blocked-by)
edges without `gh` may need the GraphQL API via the MCP server; if neither path
supports them, record the edges in the issue body's dependency summary and tell
the user — do not silently drop them.

## Parent issue (one per slice)
```bash
gh issue create --type Feature \
  --title "<slice_name>" --body-file <parent_body.md> \
  --label "feature:<slug>,slice:<id>"
```

## Sub-issue (work unit within a slice)
```bash
gh issue create --type Task --parent <parent#> \
  --title "<work unit>" --body-file <sub_body.md> \
  --label "feature:<slug>,slice:<id>,layer:<layer>"
```

## Dependency edge (reader slice blocked by the slice it depends on)
```bash
gh issue edit <reader_parent#> --add-blocked-by <dependency_parent#>
#   (or pass --blocked-by <#> at create time)
```

## Read back for reconciliation (idempotent re-runs)
```bash
gh issue list --search "label:feature:<slug>" \
  --json number,title,parent,type,blockedBy,labels,state
```

## Type fallback
Issue **types** are configured at the organization level. If the org has not
defined them, omit `--type` and fall back to `type:feature` / `type:task` /
`type:bug` labels. Everything else is unchanged.
