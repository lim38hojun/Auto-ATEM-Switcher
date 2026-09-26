"""Unit tests for DependencyManager (`src/utils/dependency_manager.py`)."""

from src.utils.dependency_manager import DependencyManager


def test_dependency_manager_checks_required_packages() -> None:
    pkgs = DependencyManager.check_all_packages()
    names = {p.pip_name for p in pkgs}
    assert "PyATEMMax" in names
    assert "opencv-python" in names
    assert "PyQt5" in names

    # Ensure core packages are marked installed
    pkg_map = {p.pip_name: p for p in pkgs}
    assert pkg_map["opencv-python"].installed is True
    assert pkg_map["PyQt5"].installed is True
