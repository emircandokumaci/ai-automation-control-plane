# Job state machine

`QUEUED -> RUNNING -> SUCCEEDED` is the simple path. Transient failures move to `RETRY_WAIT`; asynchronous providers may use `WAITING_EXTERNAL`; uncertain external side effects use `RECONCILING`; exhausted retryable work becomes `DEAD`; unrecoverable validation/business errors become `FAILED`. Manual redrive creates a new job and preserves the old audit history.
