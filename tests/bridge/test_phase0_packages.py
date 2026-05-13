"""Phase 0 smoke tests for the four CCR packages and their backwards-compat surface."""

from __future__ import annotations


def test_subsystem_packages_preserve_legacy_metadata_for_porting_workspace() -> None:
    """WI-0.4 must preserve ARCHIVE_NAME etc. for tests/test_porting_workspace.py:73-79."""
    from src import bridge, remote, server, upstreamproxy

    for pkg in (bridge, remote, server, upstreamproxy):
        assert pkg.MODULE_COUNT > 0, f'{pkg.__name__}.MODULE_COUNT is 0/missing'
        assert pkg.ARCHIVE_NAME, f'{pkg.__name__}.ARCHIVE_NAME is empty'
        assert pkg.SAMPLE_FILES, f'{pkg.__name__}.SAMPLE_FILES is empty'
        assert pkg.PORTING_NOTE, f'{pkg.__name__}.PORTING_NOTE is empty'


def test_legacy_services_bridge_emits_deprecation_warning() -> None:
    """WI-0.2: importing src.services.bridge fires a DeprecationWarning."""
    import importlib
    import sys
    import warnings

    # Force reimport so the warning fires inside the recording context.
    sys.modules.pop('src.services.bridge', None)
    sys.modules.pop('src.services.bridge.session', None)
    sys.modules.pop('src.services.bridge.transport', None)
    sys.modules.pop('src.services.bridge.auth', None)

    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter('always')
        importlib.import_module('src.services.bridge')

    assert any(
        issubclass(w.category, DeprecationWarning)
        and 'src.services.bridge is deprecated' in str(w.message)
        for w in captured
    ), 'expected DeprecationWarning from src.services.bridge import'


def test_legacy_remote_runtime_emits_deprecation_warning() -> None:
    """WI-0.3 + ch01 round-2 P3: importing scripts.audit.remote_runtime
    (formerly src.remote_runtime) fires a DeprecationWarning."""
    import importlib
    import sys
    import warnings

    sys.modules.pop('scripts.audit.remote_runtime', None)
    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter('always')
        importlib.import_module('scripts.audit.remote_runtime')

    assert any(
        issubclass(w.category, DeprecationWarning)
        and 'scripts.audit.remote_runtime is a placeholder' in str(w.message)
        for w in captured
    ), 'expected DeprecationWarning from scripts.audit.remote_runtime import'
