"""
Facade exposing serein-installer/installer_engine.py as serein_installer.installer_engine module.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Add serein-installer to sys.path
_REPO_ROOT = Path(__file__).resolve().parent.parent
_INSTALLER_DIR = _REPO_ROOT / "serein-installer"
if str(_INSTALLER_DIR) not in sys.path:
    sys.path.insert(0, str(_INSTALLER_DIR))

from installer_engine import (
    InstallerEngine,
    DiskInfo,
    PartitionSpec,
    PartitionPlan,
    DryRunResult,
    InstallProgress,
    installer_engine,
    cli_main,
    DISK_REGEX,
    HOSTNAME_REGEX,
    USERNAME_REGEX,
)

__all__ = [
    "InstallerEngine",
    "DiskInfo",
    "PartitionSpec",
    "PartitionPlan",
    "DryRunResult",
    "InstallProgress",
    "installer_engine",
    "cli_main",
    "DISK_REGEX",
    "HOSTNAME_REGEX",
    "USERNAME_REGEX",
]

if __name__ == "__main__":
    sys.exit(cli_main())
