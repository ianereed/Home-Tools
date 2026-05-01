"""
Job kind definitions. One Python module per kind. Each module imports
the shared huey instance and decorates a function with @huey.task or
@huey.periodic_task.

Kinds in `_internal/` are framework-internal (e.g. migration_verifier);
they are NOT user-callable via HTTP enqueue.
"""
