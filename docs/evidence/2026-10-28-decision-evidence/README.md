# Evidence provenance

All PostgreSQL runs provision fresh test-only databases and synthetic venue state.
No operational account/database credentials or model keys are included.
`source_commit` in each manifest pins the exact tested tree. Prior failed runs
are retained alongside final qualification, rather than overwritten.
Raw disposable resources remain under the worktree `.runtime` directory;
only sanitized logs/manifests/JUnit and summaries are copied here.
