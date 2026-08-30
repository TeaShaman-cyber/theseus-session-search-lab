# Wiki bootstrap

GitHub Wiki has a one-time manual initialization boundary. Enabling Wiki in repository settings is not sufficient evidence that the `.wiki.git` remote exists.

Before manual initialization this repository correctly recorded:

```text
Wiki: ENABLED / MANUAL_FIRST_PAGE_REQUIRED
```

A human then created the initial `Home` page in the GitHub UI. The Wiki remote became observable at:

```text
master @ 951d87bc256881edef361c87a725e3d485379008
```

The remaining Wiki structure was committed and published through the governed wrapper:

```text
/workspace/tools/wiki-push.sh /workspace/theseus-session-search-lab.wiki
```

Current verified state:

```text
Wiki: WIKI_GIT_REMOTE_VERIFIED
branch: master
commit: fab484e1e22c982229d2aab2d80c933f4c5c1d93
```

Independent readback cloned the remote again, matched the exact SHA, and observed these pages:

- `Home.md`
- `Terminology.md`
- `Architecture.md`
- `Capture-Adapters.md`
- `Session-Artifact-Contract.md`
- `Session-Search-Contract.md`
- `Experiment-Traceability.md`
- `Research-Lifecycle.md`

Future Wiki changes remain governed Git writes with write preflight, push, remote SHA readback, and independent verification for important structural changes.

Wiki is navigation; the main repository remains canonical for research contracts, experiments, receipts, tests, and source history.
