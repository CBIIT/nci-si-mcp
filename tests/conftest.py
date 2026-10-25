import os

import pytest


@pytest.fixture(autouse=True)
def _clean_settings_environment(monkeypatch):
    """Remove every NCI_SI_* variable for each test.

    A developer's own settings (a fixture upstream mode, a licence key, a credential) must not
    decide a unit test's result. A test that needs a setting sets it itself, and monkeypatch
    restores the variables afterwards.
    """
    for name in [name for name in os.environ if name.startswith("NCI_SI_")]:
        monkeypatch.delenv(name)
    # Git hooks export repository-local paths. Tests that create their own repositories must
    # not redirect `git init`, commits, refs or installed hooks into the caller's checkout.
    for name in [name for name in os.environ if name.startswith("GIT_")]:
        monkeypatch.delenv(name)
