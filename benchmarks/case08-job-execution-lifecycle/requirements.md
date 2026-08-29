# Job Lifecycle Requirements

REQ-08: A background job that is already 'running' or 'completed' cannot be restarted. Attempting to start a non-queued job must raise IllegalJobStateError.
