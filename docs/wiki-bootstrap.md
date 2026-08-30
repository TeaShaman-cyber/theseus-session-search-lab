# Wiki bootstrap

Current state:

```text
Wiki: ENABLED / MANUAL_FIRST_PAGE_REQUIRED
```

GitHub creates the Wiki Git remote only after the first page is created in the web UI. Until that manual action occurs, a failed `git ls-remote` against `.wiki.git` is a known bootstrap boundary, not a repository failure.

After the first page exists, subsequent Wiki changes use the governed Wiki wrapper and remote SHA readback.
