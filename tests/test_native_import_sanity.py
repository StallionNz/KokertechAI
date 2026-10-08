import pytest

@pytest.mark.native_stack
def test_native_stack_import_succeeds():
    """
    Regression test for Windows native dependency stability.

    Previously, the full pytest run could crash with a Windows fatal
    access violation during import of the native stack:
      pyarrow -> pandas -> sklearn

    This test makes that failure explicit and catchable as a test result,
    instead of an interpreter-level crash.
    """
    import pyarrow  # noqa: F401
    import pandas  # noqa: F401
    import sklearn  # noqa: F401

    # Basic sanity: ensure versions are available
    assert hasattr(pyarrow, "__version__")
    assert hasattr(pandas, "__version__")
    assert hasattr(sklearn, "__version__")
