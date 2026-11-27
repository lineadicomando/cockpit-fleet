# Maintenance: history

Archive: what was tried, measured and found while building maintenance;
not needed to work on it.

- Every database made before phase 8 was below 64 MB, so the one-time
  `VACUUM` that turns on `auto_vacuum = INCREMENTAL` reached them all.
