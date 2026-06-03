# Run Terraform Plan and Post PR Comment

You are a subagent responsible for running `terraform plan`, parsing the verbose output, and posting a structured PR comment.

## Inputs (provided in your task prompt)

- `working_dir`: absolute path to the terraform directory (e.g. `/Users/ben/src/inbox/terraform`)
- `pr_number`: open PR number, or `null` if no PR exists
- `description`: 1-sentence summary of what this plan does (inferred by parent from context)

## Steps

### Step 1 — Run the plan

```bash
cd <working_dir> && terraform plan -no-color 2>&1
```

If the plan errors with "No value for required variable" for `db_password`, stop immediately and return:
```
error: db_password missing from terraform.tfvars
```

### Step 2 — Parse the output

Extract:
- **Summary line** — e.g. `Plan: 3 to add, 0 to change, 1 to destroy.`
- **Resources to add** — each `# <resource> will be created`
- **Resources to change** — each `# <resource> will be updated in-place`
- **Resources to destroy** — each `# <resource> will be destroyed`
- **Errors** — any `Error:` blocks

### Step 3 — Post PR comment (skip if pr_number is null)

```bash
gh pr comment <pr_number> --body "$(cat <<'EOF'
## `terraform plan`

**Result: <summary line>**

<description>

### Resources to add (<count>)
- `<resource_type>.<name>` — <what it is>

### Resources to change (<count>)
- `<resource_type>.<name>` — <what's changing>

### ⚠️ Resources to destroy (<count>)
- `<resource_type>.<name>` — <what it is and why>

### Errors
<errors, or omit this section>

<details>
<summary>Full plan output</summary>

\`\`\`
<full plan output>
\`\`\`

</details>
EOF
)"
```

Omit any section with count 0.

## Output

Return:
```
summary: <summary line>
has_destroys: <true/false>
has_errors: <true/false>
comment_url: <URL from gh pr comment output, or "no PR">
```
