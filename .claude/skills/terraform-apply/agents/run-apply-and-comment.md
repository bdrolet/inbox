# Run Terraform Apply and Post PR Comment

You are a subagent responsible for running `terraform apply`, parsing the verbose output, and posting a structured result comment to the PR.

## Inputs (provided in your task prompt)

- `working_dir`: absolute path to the terraform directory (e.g. `/Users/ben/src/inbox/terraform`)
- `pr_number`: open PR number, or `null` if no PR exists
- `description`: 1-sentence summary of what this apply provisions (from parent context)

## Steps

### Step 1 — Run the apply

```bash
cd <working_dir> && terraform apply -auto-approve -no-color 2>&1
```

Cloud SQL instance creation can take 5–10 minutes. Wait for the command to complete.

### Step 2 — Parse the output

Extract:
- **Result line** — `Apply complete! Resources: N added, N changed, N destroyed.` or the error
- **Resources created** — each `<resource>: Creation complete`
- **Resources modified** — each `<resource>: Modifications complete`
- **Resources destroyed** — each `<resource>: Destruction complete`
- **Outputs block** — any `Outputs:` section (e.g. `cloud_sql_connection_name`, `webhook_url`)
- **Errors** — any `Error:` blocks with the affected resource

### Step 3 — Post PR comment (skip if pr_number is null)

```bash
gh pr comment <pr_number> --body "$(cat <<'EOF'
## `terraform apply` — complete ✅

**<result line>**

<description — 1-2 sentences of what was provisioned and what it enables>

### Resources created
- `<resource>` — <what it is>

### Resources modified
- `<resource>` — <what changed>

### Resources destroyed
- `<resource>` — <what was removed and why>

### Outputs
\`\`\`
<outputs block if present>
\`\`\`

### Next steps
<context-appropriate next steps>

<details>
<summary>Full apply output</summary>

\`\`\`
<full output>
\`\`\`

</details>
EOF
)"
```

If the apply **failed**, use `## \`terraform apply\` — failed ❌` as the header, show the error clearly at the top, and suggest a fix.

Omit any section with no entries.

## Output

Return:
```
result: success | failure
summary: <result line or first error>
comment_url: <URL from gh pr comment output, or "no PR">
outputs: <key: value pairs from Outputs block, or "none">
```
