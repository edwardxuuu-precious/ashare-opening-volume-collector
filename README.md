# A-share opening volume collector

Minimal collection runtime for an opening-volume dashboard. Uses AKShare and
BaoStock, keeps historical daily results in private storage, and excludes
unverified ratios. Only the recent six months are restored for active collection;
older saved dates remain queryable without repeated downloads.

The source probe runs without AWS permissions and prints aggregate diagnostics.
The production workflow writes to private S3 through a repository-scoped OIDC
role. No accounts, credentials, private market snapshots, or website are included.

Scheduled collection is disabled until ENABLE_DAILY_UPDATE is set to true after
the previous writer is stopped. Configure STOCK_BUCKET and AWS_ROLE_ARN separately.
Use only a public repository and a standard GitHub-hosted runner for free compute.
GitHub execution and scheduling limits still apply. This is not a real-time feed
or a guarantee that all source records are available by a fixed clock time.
