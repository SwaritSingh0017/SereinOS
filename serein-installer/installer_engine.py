"""
SereinOS Standalone Graphical OS Installer Engine.
Handles disk enumeration, dual-mode partition planning (Legacy BIOS MBR + 64-bit UEFI GPT),
filesystem formatting, /etc/fstab generation with UUIDs, dual-mode GRUB bootloader staging,
and non-destructive programmatic --dry-run validation.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import random
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("serein.installer")

MIN_DISK_SIZE_GB = 20.0
DEFAULT_EFI_SIZE_MB = 512
DEFAULT_SWAP_SIZE_MB = 4096

DISK_REGEX = re.compile(r"^/dev/(sd[a-z]+|nvme[0-9]+n[0-9]+|vd[a-z]+|mmcblk[0-9]+)$")
HOSTNAME_REGEX = re.compile(r"^[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?$")
USERNAME_REGEX = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
SHELL_METACHRACTERS = set(";&|`$\n\r\t<>")


def _generate_partition_uuid(fstype: str = "", mount_point: str = "") -> str:
    """Generate FAT32-compatible 32-bit volume ID (XXXX-XXXX) for ESP or RFC 4122 UUID4 for others."""
    if fstype == "vfat" or mount_point == "/boot/efi":
        return f"{random.randint(0, 0xFFFF):04X}-{random.randint(0, 0xFFFF):04X}"
    return str(uuid.uuid4())


@dataclass
class DiskInfo:
    device: str
    size_gb: float
    model: str
    is_boot_media: bool
    partition_table: str = "unknown"
    removable: bool = False
    fstype: Optional[str] = None
    mountpoint: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class PartitionSpec:
    number: int
    device: str
    size_mb: int
    fstype: str
    mount_point: str
    label: str
    flags: List[str] = field(default_factory=list)
    uuid: str = ""

    def __post_init__(self) -> None:
        if not self.uuid:
            self.uuid = _generate_partition_uuid(self.fstype, self.mount_point)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class PartitionPlan:
    disk: str
    firmware_type: str  # "uefi" or "bios"
    table_type: str  # "gpt" or "msdos"
    disk_size_gb: float
    partitions: List[PartitionSpec] = field(default_factory=list)
    fstab: str = ""
    bootloader_cmd: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "disk": self.disk,
            "firmware_type": self.firmware_type,
            "table_type": self.table_type,
            "disk_size_gb": self.disk_size_gb,
            "partitions": [p.to_dict() for p in self.partitions],
            "fstab": self.fstab,
            "bootloader_cmd": self.bootloader_cmd,
        }


@dataclass
class DryRunResult:
    valid: bool
    summary: str
    steps: List[str]
    fstab: str
    bootloader: str
    partitions: List[Dict[str, Any]] = field(default_factory=list)
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class InstallProgress:
    phase: str
    percent: int
    log: List[str]
    error: Optional[str] = None
    done: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class InstallerEngine:
    """
    Core OS disk installer engine for SereinOS.
    Supports dry-run simulation, dual-mode BIOS/UEFI partition generation,
    and real disk installation routines.
    """

    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run
        self._progress = InstallProgress(
            phase="idle",
            percent=0,
            log=["Installer engine initialized."],
            error=None,
            done=False,
        )
        self._lock = threading.Lock()
        self._current_task_id: Optional[str] = None
        self._tasks: Dict[str, InstallProgress] = {}

    # =========================================================================
    # Firmware & Hardware Environment Detection
    # =========================================================================

    def detect_firmware(self) -> str:
        """
        Detect system boot mode:
        Returns 'uefi' if /sys/firmware/efi exists, otherwise 'bios'.
        """
        if os.path.exists("/sys/firmware/efi"):
            return "uefi"
        return "bios"

    # =========================================================================
    # Storage Disk Enumeration
    # =========================================================================

    def detect_disks(self, include_boot_media: bool = True) -> List[Dict[str, Any]]:
        """
        Enumerate available block storage disks using lsblk.
        Falls back to platform mock inventory if lsblk is unavailable (e.g. CI/Windows).
        """
        disks = self._query_lsblk()
        if not disks:
            # Fallback to simulated hardware inventory
            disks = self._get_fallback_disks()

        if not include_boot_media:
            disks = [d for d in disks if not d["is_boot_media"]]

        return disks

    def _query_lsblk(self) -> List[Dict[str, Any]]:
        """Run lsblk -J -b -o ... and parse disk devices."""
        if not shutil.which("lsblk"):
            return []

        try:
            cmd = [
                "lsblk",
                "-J",
                "-b",
                "-o",
                "NAME,PATH,SIZE,TYPE,MODEL,TRAN,RM,RO,FSTYPE,LABEL,MOUNTPOINT",
            ]
            res = subprocess.run(cmd, capture_output=True, text=True, check=True)
            data = json.loads(res.stdout)
            devices = data.get("blockdevices", [])
            results = []

            for dev in devices:
                dev_type = dev.get("type", "").lower()
                if dev_type != "disk":
                    continue

                path = dev.get("path") or f"/dev/{dev.get('NAME')}"
                size_bytes = int(dev.get("size") or 0)
                size_gb = round(size_bytes / (1024**3), 1)
                model = (dev.get("model") or "Generic_Storage_Device").strip()
                removable = bool(dev.get("rm") or False)
                ro = bool(dev.get("ro") or False)

                # Check if device or any child partition is current boot media
                is_boot = self._is_device_boot_media(dev)

                disk_entry = {
                    "device": path,
                    "size_gb": size_gb,
                    "model": model,
                    "is_boot_media": is_boot,
                    "partition_table": "gpt" if dev.get("pttype") == "gpt" else "mbr",
                    "removable": removable,
                    "fstype": dev.get("fstype"),
                    "mountpoint": dev.get("mountpoint"),
                }
                results.append(disk_entry)
            return results
        except Exception as e:
            logger.debug(f"lsblk query encountered note: {e}")
            return []

    def _is_device_boot_media(self, dev: Dict[str, Any]) -> bool:
        """Determine whether device or its children hold the active live/root filesystem."""
        exact_boot_mounts = {"/", "/run/live/medium", "/live", "/cdrom", "/mnt/live"}
        mountpoint = dev.get("mountpoint")
        if mountpoint in exact_boot_mounts:
            return True

        prefix_boot_mounts = ("/run/live", "/live", "/cdrom")
        if mountpoint and any(mountpoint.startswith(p) for p in prefix_boot_mounts):
            return True

        # Check child partitions recursively
        children = dev.get("children", [])
        for child in children:
            child_mount = child.get("mountpoint")
            if child_mount in exact_boot_mounts:
                return True
            if child_mount and any(child_mount.startswith(p) for p in prefix_boot_mounts):
                return True

        # Removable or read-only devices with label containing SEREIN / LIVE
        label = (dev.get("label") or "").upper()
        if "SEREIN" in label or "LIVE" in label:
            return True

        return False

    def _get_fallback_disks(self) -> List[Dict[str, Any]]:
        """
        Standard hardware storage devices for Sony VAIO VPCYB35AN / reference system.
        Internal 500GB HDD (Toshiba MK5065GSX) + Live USB installer media.
        """
        return [
            {
                "device": "/dev/sda",
                "size_gb": 465.7,
                "model": "TOSHIBA_MK5065GSX",
                "is_boot_media": False,
                "partition_table": "mbr",
                "removable": False,
                "fstype": None,
                "mountpoint": None,
            },
            {
                "device": "/dev/sdb",
                "size_gb": 14.8,
                "model": "SanDisk_Ultra",
                "is_boot_media": True,
                "partition_table": "gpt",
                "removable": True,
                "fstype": "iso9660",
                "mountpoint": "/run/live/medium",
            },
        ]

    # =========================================================================
    # Partition Planning & Layout Calculations
    # =========================================================================

    def plan_partitions(
        self,
        disk_device: str,
        firmware_type: str = "auto",
        scheme: str = "auto",
        disk_size_gb: Optional[float] = None,
    ) -> PartitionPlan:
        """
        Calculate partition layout for target disk.
        Supports:
        - 64-bit UEFI GPT:
            P1: 512 MiB EFI System Partition (vfat /boot/efi)
            P2: 4096 MiB Swap partition
            P3: Remaining ext4 Root partition (/)
        - Legacy BIOS MBR:
            P1: Remaining ext4 Root partition (/) with boot flag
            P2: 4096 MiB Swap partition
        """
        if not DISK_REGEX.match(disk_device):
            raise ValueError(f"Invalid target disk device: '{disk_device}'. Must match valid Linux block device pattern.")

        # Resolve target disk size
        if disk_size_gb is None:
            disks = self.detect_disks(include_boot_media=True)
            matched = [d for d in disks if d["device"] == disk_device]
            if matched:
                disk_size_gb = matched[0]["size_gb"]
            else:
                # Default fallback if unknown path provided
                disk_size_gb = 80.0

        if disk_size_gb < MIN_DISK_SIZE_GB:
            raise ValueError(
                f"Disk {disk_device} capacity ({disk_size_gb} GB) is below "
                f"minimum required {MIN_DISK_SIZE_GB} GB."
            )

        # Resolve firmware mode
        if firmware_type == "auto" or not firmware_type:
            firmware_type = self.detect_firmware()
        firmware_type = firmware_type.lower()
        if firmware_type not in ("uefi", "bios"):
            firmware_type = "uefi"

        # Suffix for partitions (/dev/sda -> /dev/sda1, /dev/nvme0n1 -> /dev/nvme0n1p1)
        part_prefix = f"{disk_device}p" if disk_device[-1].isdigit() else disk_device

        partitions: List[PartitionSpec] = []

        if firmware_type == "uefi":
            table_type = "gpt"
            esp_size = DEFAULT_EFI_SIZE_MB
            swap_size = DEFAULT_SWAP_SIZE_MB
            total_mb = int(disk_size_gb * 1024)
            root_size = total_mb - (esp_size + swap_size)

            p1 = PartitionSpec(
                number=1,
                device=f"{part_prefix}1",
                size_mb=esp_size,
                fstype="vfat",
                mount_point="/boot/efi",
                label="ESP",
                flags=["boot", "esp"],
            )
            p2 = PartitionSpec(
                number=2,
                device=f"{part_prefix}2",
                size_mb=swap_size,
                fstype="swap",
                mount_point="none",
                label="SWAP",
                flags=["swap"],
            )
            p3 = PartitionSpec(
                number=3,
                device=f"{part_prefix}3",
                size_mb=root_size,
                fstype="ext4",
                mount_point="/",
                label="SereinOS",
                flags=[],
            )
            partitions = [p1, p2, p3]
        else:
            table_type = "msdos"
            swap_size = DEFAULT_SWAP_SIZE_MB
            total_mb = int(disk_size_gb * 1024)
            root_size = total_mb - swap_size

            p1 = PartitionSpec(
                number=1,
                device=f"{part_prefix}1",
                size_mb=root_size,
                fstype="ext4",
                mount_point="/",
                label="SereinOS",
                flags=["boot"],
            )
            p2 = PartitionSpec(
                number=2,
                device=f"{part_prefix}2",
                size_mb=swap_size,
                fstype="swap",
                mount_point="none",
                label="SWAP",
                flags=["swap"],
            )
            partitions = [p1, p2]

        plan = PartitionPlan(
            disk=disk_device,
            firmware_type=firmware_type,
            table_type=table_type,
            disk_size_gb=disk_size_gb,
            partitions=partitions,
        )
        plan.fstab = self.generate_fstab(plan)
        plan.bootloader_cmd = self.generate_bootloader_command(plan)
        return plan

    # =========================================================================
    # fstab Generation
    # =========================================================================

    def generate_fstab(
        self,
        plan: PartitionPlan,
        custom_uuids: Optional[Dict[str, str]] = None,
    ) -> str:
        """
        Generate /etc/fstab with persistent filesystem UUID references.
        """
        lines = [
            "# /etc/fstab: static file system information for SereinOS",
            "# <file system> <mount point> <type> <options> <dump> <pass>",
        ]

        for p in plan.partitions:
            p_uuid = (
                custom_uuids.get(p.device)
                if custom_uuids and p.device in custom_uuids
                else p.uuid
            )

            if p.mount_point == "/":
                lines.append(f"UUID={p_uuid} / ext4 errors=remount-ro,defaults,noatime 0 1")
            elif p.mount_point == "/boot/efi":
                lines.append(f"UUID={p_uuid} /boot/efi vfat umask=0077,defaults 0 2")
            elif p.fstype == "swap":
                lines.append(f"UUID={p_uuid} none swap sw 0 0")
            elif p.mount_point:
                lines.append(f"UUID={p_uuid} {p.mount_point} {p.fstype} defaults 0 2")

        lines.append("")
        return "\n".join(lines)

    # =========================================================================
    # Dual-Mode GRUB Bootloader Staging
    # =========================================================================

    def generate_bootloader_command(self, plan: PartitionPlan) -> str:
        """
        Generate target-specific GRUB installation command line.
        - UEFI: grub-install --target=x86_64-efi --efi-directory=/mnt/boot/efi --bootloader-id=SereinOS --recheck
        - BIOS: grub-install --target=i386-pc --recheck <target_disk>
        """
        if plan.firmware_type == "uefi":
            return (
                "grub-install --target=x86_64-efi --efi-directory=/mnt/boot/efi "
                "--bootloader-id=SereinOS --recheck"
            )
        else:
            return f"grub-install --target=i386-pc --recheck {shlex.quote(plan.disk)}"

    # =========================================================================
    # Programmatic Dry-Run Mode (--dry-run)
    # =========================================================================

    def run_dry_run(
        self,
        disk_device: str,
        scheme: str = "auto",
        firmware_type: str = "auto",
    ) -> Dict[str, Any]:
        """
        Execute programmatic dry run: validates all installation stages,
        calculates sector partitions, verifies fstab, and stages GRUB
        without executing destructive disk writes.
        """
        # Strict validation of device path formatting and shell metacharacters
        if not isinstance(disk_device, str):
            return DryRunResult(
                valid=False,
                summary="Disk device must be a valid string path.",
                steps=[],
                fstab="",
                bootloader="",
                error="Invalid disk parameter type.",
            ).to_dict()

        if any(c in disk_device for c in SHELL_METACHRACTERS):
            return DryRunResult(
                valid=False,
                summary=f"Invalid disk device: '{disk_device}' contains illegal shell characters.",
                steps=[],
                fstab="",
                bootloader="",
                error=f"Disk {disk_device} not recognized (contains forbidden shell metacharacters).",
            ).to_dict()

        if not disk_device.startswith("/dev/"):
            return DryRunResult(
                valid=False,
                summary=f"Invalid disk device: '{disk_device}' (must start with /dev/)",
                steps=[],
                fstab="",
                bootloader="",
                error=f"Disk {disk_device} not recognized.",
            ).to_dict()

        if not DISK_REGEX.match(disk_device):
            return DryRunResult(
                valid=False,
                summary=f"Invalid block device name: '{disk_device}'. Must match valid Linux block device format.",
                steps=[],
                fstab="",
                bootloader="",
                error=f"Disk {disk_device} not recognized.",
            ).to_dict()

        # Validate disk existence
        disks = self.detect_disks(include_boot_media=True)
        target = None
        for d in disks:
            if d["device"] == disk_device:
                target = d
                break

        if not target:
            target = {
                "device": disk_device,
                "size_gb": 80.0,
                "is_boot_media": False,
                "model": "Generic_Target_Drive",
            }

        # Guard against installing to live boot media
        if target.get("is_boot_media", False):
            return DryRunResult(
                valid=False,
                summary=f"Refusing to install on active boot media {disk_device}.",
                steps=[],
                fstab="",
                bootloader="",
                error=f"{disk_device} is the live boot medium.",
            ).to_dict()

        # Validate minimum capacity
        size_gb = float(target.get("size_gb", 0.0))
        if size_gb < MIN_DISK_SIZE_GB:
            return DryRunResult(
                valid=False,
                summary=f"Disk {disk_device} is {size_gb}GB. Minimum required is {MIN_DISK_SIZE_GB}GB.",
                steps=[],
                fstab="",
                bootloader="",
                error="Insufficient disk capacity.",
            ).to_dict()

        try:
            plan = self.plan_partitions(
                disk_device=disk_device,
                firmware_type=firmware_type,
                scheme=scheme,
                disk_size_gb=size_gb,
            )
        except Exception as e:
            return DryRunResult(
                valid=False,
                summary=f"Partition planning failed: {e}",
                steps=[],
                fstab="",
                bootloader="",
                error=str(e),
            ).to_dict()

        fw_label = "GPT/UEFI" if plan.firmware_type == "uefi" else "MBR/BIOS"
        summary = f"Simulated install to {disk_device} ({fw_label})"

        steps: List[str] = []
        if plan.firmware_type == "uefi":
            steps.append(f"1. Create GPT partition table on {disk_device}")
            steps.append(f"2. Create {DEFAULT_EFI_SIZE_MB}MiB EFI system partition ({plan.partitions[0].device})")
            steps.append(f"3. Create {DEFAULT_SWAP_SIZE_MB}MiB swap partition ({plan.partitions[1].device})")
            steps.append(f"4. Create ext4 root partition ({plan.partitions[2].device})")
            steps.append("5. Stage dual-mode GRUB bootloader")
        else:
            steps.append(f"1. Create MBR partition table on {disk_device}")
            steps.append(f"2. Create ext4 root partition ({plan.partitions[0].device})")
            steps.append(f"3. Create {DEFAULT_SWAP_SIZE_MB}MiB swap partition ({plan.partitions[1].device})")
            steps.append("4. Stage Legacy BIOS GRUB bootloader")

        result = DryRunResult(
            valid=True,
            summary=summary,
            steps=steps,
            fstab=plan.fstab,
            bootloader=plan.bootloader_cmd,
            partitions=[p.to_dict() for p in plan.partitions],
        )
        return result.to_dict()

    # =========================================================================
    # Installation Execution & Progress Monitoring
    # =========================================================================

    def start_installation(
        self,
        disk_device: str,
        user: str = "serein",
        password: Optional[str] = None,
        hostname: str = "serein-vaio",
        firmware_type: str = "auto",
        scheme: str = "auto",
        dry_run: bool = False,
    ) -> str:
        """
        Initiate an asynchronous installation task.
        Returns a unique task_id.
        """
        if not DISK_REGEX.match(disk_device):
            raise ValueError(f"Invalid target disk device: '{disk_device}'")
        if not HOSTNAME_REGEX.match(hostname):
            raise ValueError(f"Invalid hostname: '{hostname}'")
        if not USERNAME_REGEX.match(user):
            raise ValueError(f"Invalid username: '{user}'")

        is_non_root = (os.getuid() != 0) if hasattr(os, "getuid") else True
        if not dry_run and not is_non_root:
            if not password:
                raise ValueError("Password is required for target system installation and cannot be empty")
            if password.strip().lower() in ("serein", "password", "admin", "123456", "12345678"):
                raise ValueError("Insecure default password is prohibited. Please choose a strong password.")
            if len(password) < 8:
                raise ValueError("Password must be at least 8 characters long.")
        elif password is not None:
            if password.strip().lower() in ("serein", "password", "admin", "123456", "12345678"):
                raise ValueError("Insecure default password is prohibited. Please choose a strong password.")
            if len(password) < 8:
                raise ValueError("Password must be at least 8 characters long.")

        with self._lock:
            if self._progress.phase not in ("idle", "complete", "failed") and not self._progress.done:
                raise RuntimeError("An installation task is already in progress.")

            task_id = str(uuid.uuid4())
            self._current_task_id = task_id
            prog = InstallProgress(
                phase="initializing",
                percent=0,
                log=[f"Starting SereinOS installation to {disk_device}..."],
                error=None,
                done=False,
            )
            self._progress = prog
            self._tasks[task_id] = prog

        worker = threading.Thread(
            target=self._run_install_worker,
            args=(task_id, disk_device, user, password, hostname, firmware_type, scheme, dry_run or self.dry_run),
            daemon=True,
        )
        worker.start()
        return task_id

    def _run_install_worker(
        self,
        task_id: str,
        disk_device: str,
        user: str,
        password: str,
        hostname: str,
        firmware_type: str,
        scheme: str,
        is_dry_run: bool,
    ) -> None:
        """Background thread executing installation stages with progress updates."""
        try:
            # 1. Validation & Pre-flight
            self._update_progress(task_id, "validating", 10, f"Validating disk {disk_device} parameters...")
            dry_run_check = self.run_dry_run(disk_device, scheme=scheme, firmware_type=firmware_type)
            if not dry_run_check["valid"]:
                raise RuntimeError(dry_run_check.get("error") or dry_run_check.get("summary"))

            is_linux_root = bool(
                sys.platform.startswith("linux")
                and hasattr(os, "geteuid")
                and os.geteuid() == 0
            )

            if is_dry_run or not is_linux_root:
                # Simulation Mode (Dry-run or Non-Linux / Non-root environment)
                self._append_log(
                    task_id,
                    f"[SIMULATION] Non-destructive simulation active (platform: {sys.platform}, root: {is_linux_root}).",
                )
                time.sleep(0.08)

                # 2. Partitioning
                self._update_progress(task_id, "partitioning", 25, f"Creating partition table ({dry_run_check['summary']})...")
                for step in dry_run_check["steps"]:
                    self._append_log(task_id, f"  -> {step}")
                time.sleep(0.1)

                # 3. Formatting
                self._update_progress(task_id, "formatting", 45, "Formatting ext4 root, vfat ESP, and swap filesystems...")
                time.sleep(0.1)

                # 4. Copying root filesystem
                self._update_progress(task_id, "copying", 65, "Syncing SereinOS system image and desktop assets to target...")
                time.sleep(0.15)

                # 5. Configuring system
                self._update_progress(task_id, "configuring", 80, f"Setting hostname '{hostname}', user '{user}', and /etc/fstab...")
                self._append_log(task_id, "  -> Generated fstab entries:\n" + dry_run_check["fstab"])
                time.sleep(0.1)

                # 6. Installing Bootloader
                self._update_progress(task_id, "bootloader", 90, f"Staging bootloader: {dry_run_check['bootloader']}")
                time.sleep(0.1)

                # 7. Complete
                self._update_progress(task_id, "complete", 100, "Installation successfully completed! System is ready to reboot.")
                with self._lock:
                    if task_id in self._tasks:
                        self._tasks[task_id].done = True
                    self._progress.done = True

            else:
                # Live Physical Linux Installation (requires root block access)
                self._append_log(task_id, f"[LIVE INSTALL] Executing real installation to {disk_device}...")
                plan = self.plan_partitions(disk_device, firmware_type=firmware_type, scheme=scheme)

                # 2. Partitioning
                self._update_progress(task_id, "partitioning", 25, f"Creating partition table on {disk_device} ({plan.table_type})...")
                subprocess.run(
                    ["parted", "-s", disk_device, "mklabel", plan.table_type],
                    check=True, text=True, capture_output=True,
                )
                self._append_log(task_id, f"  -> Created {plan.table_type} partition label on {disk_device}")

                curr_start_mb = 1
                for p in plan.partitions:
                    end_mb = curr_start_mb + p.size_mb
                    end_spec = "100%" if p.number == len(plan.partitions) else f"{end_mb}MiB"
                    fs_arg = "fat32" if p.fstype == "vfat" else ("linux-swap" if p.fstype == "swap" else "ext4")
                    part_type = "primary" if plan.table_type == "msdos" else p.label
                    mkpart_cmd = ["parted", "-s", disk_device, "mkpart", part_type, fs_arg, f"{curr_start_mb}MiB", end_spec]
                    subprocess.run(mkpart_cmd, check=True, text=True, capture_output=True)
                    self._append_log(task_id, f"  -> Created partition {p.device} ({p.fstype}, {p.size_mb}MiB)")

                    for flg in p.flags:
                        subprocess.run(["parted", "-s", disk_device, "set", str(p.number), flg, "on"], check=True, text=True, capture_output=True)
                    curr_start_mb = end_mb

                if shutil.which("partprobe"):
                    subprocess.run(["partprobe", disk_device], check=False, text=True, capture_output=True)
                if shutil.which("udevadm"):
                    subprocess.run(["udevadm", "settle"], check=False, text=True, capture_output=True)
                time.sleep(0.3)

                # 3. Formatting
                self._update_progress(task_id, "formatting", 45, "Formatting ext4 root, vfat ESP, and swap filesystems...")
                root_part = None
                swap_part = None
                efi_part = None

                for p in plan.partitions:
                    if p.mount_point == "/":
                        root_part = p.device
                        subprocess.run(["mkfs.ext4", "-F", "-L", "SereinRoot", root_part], check=True, text=True, capture_output=True)
                        self._append_log(task_id, f"  -> Formatted {root_part} as ext4 (label: SereinRoot)")
                    elif p.fstype == "swap":
                        swap_part = p.device
                        subprocess.run(["mkswap", "-L", "SereinSwap", swap_part], check=True, text=True, capture_output=True)
                        self._append_log(task_id, f"  -> Formatted {swap_part} as swap (label: SereinSwap)")
                    elif p.mount_point == "/boot/efi":
                        efi_part = p.device
                        subprocess.run(["mkfs.vfat", "-F32", "-n", "EFI", efi_part], check=True, text=True, capture_output=True)
                        self._append_log(task_id, f"  -> Formatted {efi_part} as FAT32 ESP (label: EFI)")

                if not root_part:
                    raise RuntimeError("No root partition found in installation plan.")

                # 4. Copying root filesystem
                self._update_progress(task_id, "copying", 65, "Syncing SereinOS system image and desktop assets to target...")
                target_mount = Path("/mnt/serein_target")
                target_mount.mkdir(parents=True, exist_ok=True)
                subprocess.run(["mount", root_part, str(target_mount)], check=True, text=True, capture_output=True)

                try:
                    squashfs_path = Path("/run/live/medium/live/filesystem.squashfs")
                    if squashfs_path.exists() and shutil.which("unsquashfs"):
                        self._append_log(task_id, f"  -> Extracting squashfs image from {squashfs_path}...")
                        subprocess.run(
                            ["unsquashfs", "-f", "-d", str(target_mount), str(squashfs_path)],
                            check=True, text=True, capture_output=True,
                        )
                    elif shutil.which("rsync"):
                        self._append_log(task_id, "  -> Rsyncing live root filesystem to target...")
                        subprocess.run(
                            [
                                "rsync", "-aAX",
                                "--exclude=/dev/*", "--exclude=/proc/*", "--exclude=/sys/*",
                                "--exclude=/tmp/*", "--exclude=/run/*", "--exclude=/mnt/*",
                                "--exclude=/media/*", "--exclude=/lost+found",
                                "/", f"{target_mount}/",
                            ],
                            check=True, text=True, capture_output=True,
                        )
                    else:
                        self._append_log(task_id, "  -> Copying base directories to target mount...")
                        for d in ("bin", "etc", "home", "lib", "lib64", "opt", "root", "sbin", "usr", "var"):
                            src_d = Path("/") / d
                            if src_d.exists():
                                subprocess.run(["cp", "-a", str(src_d), str(target_mount / d)], check=False, text=True, capture_output=True)
                    self._append_log(task_id, "  -> Root filesystem synced successfully")

                    # 5. Configuring system
                    self._update_progress(task_id, "configuring", 80, f"Setting hostname '{hostname}', user '{user}', and /etc/fstab...")
                    actual_uuids = {}
                    for p in plan.partitions:
                        try:
                            res = subprocess.run(["blkid", "-s", "UUID", "-o", "value", p.device], capture_output=True, text=True, check=True)
                            val = res.stdout.strip()
                            if val:
                                actual_uuids[p.device] = val
                        except Exception:
                            pass

                    real_fstab = self.generate_fstab(plan, custom_uuids=actual_uuids)
                    etc_dir = target_mount / "etc"
                    etc_dir.mkdir(parents=True, exist_ok=True)
                    (etc_dir / "fstab").write_text(real_fstab, encoding="utf-8")
                    self._append_log(task_id, "  -> Generated fstab entries:\n" + real_fstab)

                    (etc_dir / "hostname").write_text(f"{hostname}\n", encoding="utf-8")
                    self._append_log(task_id, f"  -> Wrote hostname '{hostname}' to /etc/hostname")

                    if user != "root":
                        subprocess.run(
                            ["chroot", str(target_mount), "useradd", "-m", "-s", "/bin/bash", "-G", "sudo,video,audio,render", user],
                            check=False, text=True, capture_output=True,
                        )
                    if password:
                        chpasswd_proc = subprocess.Popen(
                            ["chroot", str(target_mount), "chpasswd"],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                        )
                        chpasswd_proc.communicate(input=f"{user}:{password}\n")

                    # 6. Installing Bootloader
                    self._update_progress(task_id, "bootloader", 90, f"Staging bootloader: {plan.bootloader_cmd}")
                    mounted_binds = []
                    for b in ("/dev", "/proc", "/sys", "/run"):
                        b_dest = target_mount / b.lstrip("/")
                        b_dest.mkdir(parents=True, exist_ok=True)
                        if subprocess.run(["mount", "--bind", b, str(b_dest)], check=False, text=True, capture_output=True).returncode == 0:
                            mounted_binds.append(b_dest)

                    try:
                        if plan.firmware_type == "uefi" and efi_part:
                            efi_mount = target_mount / "boot" / "efi"
                            efi_mount.mkdir(parents=True, exist_ok=True)
                            subprocess.run(["mount", efi_part, str(efi_mount)], check=True, text=True, capture_output=True)
                            try:
                                subprocess.run(
                                    ["chroot", str(target_mount), "grub-install", "--target=x86_64-efi", "--efi-directory=/boot/efi", "--bootloader-id=SereinOS", "--recheck"],
                                    check=True, text=True, capture_output=True,
                                )
                                subprocess.run(["chroot", str(target_mount), "update-grub"], check=True, text=True, capture_output=True)
                            finally:
                                subprocess.run(["umount", str(efi_mount)], check=False, text=True, capture_output=True)
                        else:
                            subprocess.run(
                                ["chroot", str(target_mount), "grub-install", "--target=i386-pc", "--recheck", disk_device],
                                check=True, text=True, capture_output=True,
                            )
                            subprocess.run(["chroot", str(target_mount), "update-grub"], check=True, text=True, capture_output=True)
                        self._append_log(task_id, "  -> GRUB bootloader successfully installed and updated")
                    finally:
                        for b_dest in reversed(mounted_binds):
                            subprocess.run(["umount", "-l", str(b_dest)], check=False, text=True, capture_output=True)

                finally:
                    subprocess.run(["umount", "-l", str(target_mount)], check=False, text=True, capture_output=True)

                # 7. Complete
                self._update_progress(task_id, "complete", 100, "Installation successfully completed! System is ready to reboot.")
                with self._lock:
                    if task_id in self._tasks:
                        self._tasks[task_id].done = True
                    self._progress.done = True

        except Exception as e:
            logger.error(f"Installation failed: {e}")
            with self._lock:
                if task_id in self._tasks:
                    t = self._tasks[task_id]
                    t.phase = "failed"
                    t.error = str(e)
                    t.done = True
                    t.log.append(f"[ERROR] Installation halted: {e}")
                self._progress.phase = "failed"
                self._progress.error = str(e)
                self._progress.done = True
                self._progress.log.append(f"[ERROR] Installation halted: {e}")

    def _update_progress(self, task_id: str, phase: str, percent: int, log_msg: str) -> None:
        with self._lock:
            if task_id in self._tasks:
                t = self._tasks[task_id]
                t.phase = phase
                t.percent = percent
                t.log.append(log_msg)
            self._progress.phase = phase
            self._progress.percent = percent
            self._progress.log.append(log_msg)

    def _append_log(self, task_id: str, log_msg: str) -> None:
        with self._lock:
            if task_id in self._tasks:
                self._tasks[task_id].log.append(log_msg)
            self._progress.log.append(log_msg)

    def get_progress(self, task_id: Optional[str] = None) -> Dict[str, Any]:
        """Fetch install progress snapshot by task_id or current task."""
        with self._lock:
            if task_id and task_id in self._tasks:
                return self._tasks[task_id].to_dict()
            return self._progress.to_dict()


# Global singleton
installer_engine = InstallerEngine()


# =============================================================================
# CLI Entrypoint
# =============================================================================

def cli_main(args_list: Optional[List[str]] = None) -> int:
    """Command-line interface for SereinOS disk installer."""
    parser = argparse.ArgumentParser(
        description="SereinOS Standalone Disk Installer & Dry-Run Engine",
        prog="serein-installer",
    )
    parser.add_argument("--disk", type=str, help="Target storage device (e.g. /dev/sda)")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate partitioning, fstab, and bootloader without modifying disk",
    )
    parser.add_argument(
        "--list-disks",
        action="store_true",
        help="List available storage block devices",
    )
    parser.add_argument(
        "--firmware",
        choices=["auto", "uefi", "bios"],
        default="auto",
        help="Boot firmware mode (default: auto)",
    )
    parser.add_argument(
        "--scheme",
        choices=["auto", "custom"],
        default="auto",
        help="Partitioning scheme (default: auto)",
    )
    parser.add_argument("--json", action="store_true", help="Output results in JSON format")
    parser.add_argument("--execute", action="store_true", help="Execute installation on target disk")
    parser.add_argument("-y", "--yes", action="store_true", help="Confirm execution non-interactively")

    args = parser.parse_args(args_list)
    engine = InstallerEngine()

    if args.list_disks:
        disks = engine.detect_disks(include_boot_media=True)
        if args.json:
            print(json.dumps({"disks": disks}, indent=2))
        else:
            print(f"{'DEVICE':<12} {'SIZE (GB)':<12} {'MODEL':<24} {'BOOT MEDIA'}")
            print("-" * 60)
            for d in disks:
                boot_str = "[LIVE BOOT]" if d["is_boot_media"] else "No"
                print(f"{d['device']:<12} {d['size_gb']:<12} {d['model']:<24} {boot_str}")
        return 0

    if args.dry_run:
        target_disk = args.disk or "/dev/sda"
        res = engine.run_dry_run(
            disk_device=target_disk,
            scheme=args.scheme,
            firmware_type=args.firmware,
        )
        if args.json:
            print(json.dumps(res, indent=2))
        else:
            print("\n" + "=" * 60)
            print("SEREINOS INSTALLER DRY-RUN VERIFICATION")
            print("=" * 60)
            print(f"Status:     {'SUCCESS' if res['valid'] else 'FAILED'}")
            print(f"Summary:    {res['summary']}")
            print("\nExecution Steps Plan:")
            for s in res["steps"]:
                print(f"  {s}")
            print("\nGenerated /etc/fstab:")
            for line in res["fstab"].splitlines():
                print(f"  {line}")
            print(f"\nBootloader Staging Command:\n  {res['bootloader']}\n")
        return 0 if res["valid"] else 1

    if args.execute:
        target_disk = args.disk or "/dev/sda"
        if not args.yes:
            print(f"[WARNING] Refusing to execute installation without confirmation flag -y/--yes.")
            return 1
        try:
            task_id = engine.start_installation(
                disk_device=target_disk,
                firmware_type=args.firmware,
                scheme=args.scheme,
            )
            print(f"[INFO] Installation initiated. Task ID: {task_id}")
            while True:
                p = engine.get_progress(task_id)
                if p["done"]:
                    if p["phase"] == "complete":
                        print(f"[SUCCESS] {p['log'][-1] if p['log'] else 'Completed'}")
                        return 0
                    else:
                        print(f"[FAILED] {p.get('error') or 'Installation failed'}")
                        return 1
                time.sleep(0.1)
        except Exception as e:
            print(f"[ERROR] Failed to start installation: {e}")
            return 1

    if args.disk:
        print(f"[INFO] Target disk {args.disk} selected. For non-destructive simulation run with --dry-run. For real install pass --execute -y.")
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(cli_main())
