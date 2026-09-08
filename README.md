# A-share opening volume collector

Minimal collection runtime for an opening-volume dashboard. New collection uses
only Sina through AKShare. The current trading day uses one bounded whole-market
snapshot after 15:30; historical gaps use Sina daily bars. Results keep their
original source and calculate opening volume divided by daily volume without
reconciliation. Only the recent six months are restored for active collection;
older saved dates remain queryable without repeated downloads.

The source probe runs without AWS permissions and prints aggregate diagnostics.
The production workflow writes to private S3 through a repository-scoped OIDC
role. No accounts, credentials, private market snapshots, or website are included.

Scheduled collection is enabled by default after the previous writer is retired.
Set DISABLE_DAILY_UPDATE to true before a review or alternate writer is started.
Configure STOCK_BUCKET and AWS_ROLE_ARN separately.
Use only a public repository and a standard GitHub-hosted runner for free compute.
GitHub execution and scheduling limits still apply. This is not a real-time feed
or a guarantee that all source records are available by a fixed clock time.
