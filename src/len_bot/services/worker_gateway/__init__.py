"""LenBot's Worker Gateway: the component that owns container execution.

Importing this package starts nothing.  It is deployed and run separately from
the Bot process, with its own configuration, its own journal and the only
container runtime in the deployment.
"""

__all__ = ['config', 'store', 'runner', 'app']
