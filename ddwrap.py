#!/usr/bin/env python3
# DDWrap -- A simple QT GUI Wrapper for DD, in Python
# Author: Ben @ LostGeek.NET
# Sunday, Apr 05, 2026 -- Version r0.92
# r0.92 -- Refactoring, putting the general "polish" on .90
# r0.90 -- Debian packaging added (not part of git repo), safer dd exec, device-in-use checks fixed...
# r0.8 -- SMART info for SSD/HDDs in pre-flash warning...
# r0.7 -- Safety Dialog added before write actually starts...
# r0.6 -- Time estimate added to progress bar...
# r0.5 -- Layout improvements, added progress bar...

import sys
import subprocess
import os
import time
import shutil
import json

from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QLabel, QLineEdit,
    QPushButton, QFileDialog, QComboBox, QCheckBox, QTextEdit,
    QHBoxLayout, QMessageBox, QProgressBar
)
from PyQt6.QtCore import QThread, pyqtSignal

# ----------------- Privilege helpers -----------------
def is_root():
    return os.geteuid() == 0

def has_sudo():
    return shutil.which("sudo") is not None

def has_doas():
    return shutil.which("doas") is not None

def has_pkexec():
    return shutil.which("pkexec") is not None


def in_graphical_session():
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def has_passwordless_sudo():
    if not has_sudo() or is_root():
        return False

    result = subprocess.run(
        ["sudo", "-n", "true"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
    return result.returncode == 0

# ----------------- Worker thread to run dd -----------------
class DDWorker(QThread):
    progress = pyqtSignal(str)
    error = pyqtSignal(str)
    finished = pyqtSignal(int)

    def __init__(self, cmd):
        super().__init__()
        self.cmd = cmd

    def run(self):
        env = os.environ.copy()
        env["LC_ALL"] = "C"

        try:
            process = subprocess.Popen(
                self.cmd, stderr=subprocess.PIPE, text=True, env=env
            )
        except OSError as exc:
            self.error.emit(str(exc))
            self.finished.emit(1)
            return

        for line in process.stderr:
            if "bytes" in line:
                self.progress.emit(line.strip())

        returncode = process.wait()
        self.finished.emit(returncode)

# ----------------- Main GUI -----------------
class DDGui(QWidget):
    PROTECTED_MOUNTPOINTS = {"/", "/home", "/boot", "/boot/efi"}
    REMOVABLE_TRANSPORTS = {"usb", "firewire", "mmc"}

    def __init__(self):
        super().__init__()
        self.setWindowTitle("DD Wrapper GUI")
        self.resize(650, 500)

        self.image_size_bytes = 0
        self.last_bytes = 0
        self.last_update_time = 0

        layout = QVBoxLayout()
        top_layout = QVBoxLayout()
        bottom_layout = QVBoxLayout()

        if is_root():
            self.setWindowTitle("DD Wrapper GUI (running as root)")

        # ----------------- Input file -----------------
        top_layout.addWidget(QLabel("Input File:"))
        h_input = QHBoxLayout()
        self.input_edit = QLineEdit()
        self.input_edit.textChanged.connect(self.on_input_changed)
        h_input.addWidget(self.input_edit)
        browse_btn = QPushButton("Browse")
        browse_btn.clicked.connect(self.browse_file)
        h_input.addWidget(browse_btn)
        top_layout.addLayout(h_input)

        self.file_size_label = QLabel("File Size: N/A")
        top_layout.addWidget(self.file_size_label)

        # ----------------- Block size -----------------
        top_layout.addWidget(QLabel("Block Size:"))
        self.bs_combo = QComboBox()
        self.bs_combo.addItems(["64k", "256k", "512k", "1M", "2M"])
        self.bs_combo.setCurrentText("512k")
        top_layout.addWidget(self.bs_combo)

        layout.addLayout(top_layout)

        # ----------------- Progress output -----------------
        self.progress_display = QTextEdit()
        self.progress_display.setReadOnly(True)
        layout.addWidget(self.progress_display, 1)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        layout.addWidget(self.progress_bar)

        self.eta_label = QLabel("Progress: 0% - ETA: N/A")
        layout.addWidget(self.eta_label)

        # ----------------- Device selection -----------------
        self.dev_size_label = QLabel("Target Device Capacity: N/A")
        bottom_layout.addWidget(self.dev_size_label)

        h_dev = QHBoxLayout()
        self.dev_combo = QComboBox()
        self.dev_combo.currentTextChanged.connect(self.update_dev_capacity)
        h_dev.addWidget(self.dev_combo)

        self.unmount_btn = QPushButton("Unmount Device")
        self.unmount_btn.clicked.connect(self.unmount_device)
        h_dev.addWidget(self.unmount_btn)
        bottom_layout.addLayout(h_dev)

        self.advanced_devices_checkbox = QCheckBox("Show internal disks")
        self.advanced_devices_checkbox.toggled.connect(self.refresh_devices)
        bottom_layout.addWidget(self.advanced_devices_checkbox)

        # ----------------- Flags -----------------
        self.sync_checkbox = QCheckBox("oflag=sync  (Default)")
        self.sync_checkbox.setChecked(True)
        bottom_layout.addWidget(self.sync_checkbox)

        self.progress_checkbox = QCheckBox("Show Progress")
        self.progress_checkbox.setChecked(True)
        bottom_layout.addWidget(self.progress_checkbox)

        # ----------------- Start -----------------
        self.start_btn = QPushButton("Start DD")
        self.start_btn.clicked.connect(self.start_dd)
        bottom_layout.addWidget(self.start_btn)

        layout.addLayout(bottom_layout)

        self.setLayout(layout)
        self.refresh_devices()

    # ----------------- File selection -----------------
    def browse_file(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Select Image", "", "Disk Images (*.img *.iso)"
        )
        if file_path:
            self.input_edit.setText(file_path)
            self.show_file_size(file_path)

    def show_file_size(self, path):
        self.image_size_bytes = os.path.getsize(path)
        self.file_size_label.setText(
            f"File Size: {self.human_readable(self.image_size_bytes)}"
        )

    def on_input_changed(self, path):
        path = path.strip()
        if os.path.isfile(path):
            self.show_file_size(path)
            return

        self.image_size_bytes = 0
        self.file_size_label.setText("File Size: N/A")

    @staticmethod
    def human_readable(num, suffix="B"):
        for unit in ["", "K", "M", "G", "T"]:
            if num < 1024:
                return f"{num:.2f} {unit}{suffix}"
            num /= 1024
        return f"{num:.2f} P{suffix}"

    @staticmethod
    def privilege_prefix():
        if is_root():
            return []

        if has_passwordless_sudo():
            return ["sudo"]

        # Desktop launches are more likely to have a PolicyKit agent than a tty.
        if in_graphical_session() and has_pkexec():
            return ["pkexec"]
        if has_sudo():
            return ["sudo"]
        if has_doas():
            return ["doas"]
        if has_pkexec():
            return ["pkexec"]
        return None

    @staticmethod
    def run_privileged(args, **kwargs):
        prefix = DDGui.privilege_prefix()
        if prefix is None:
            raise PermissionError("No privilege escalation helper is available.")

        env = kwargs.pop("env", os.environ.copy())
        env["LC_ALL"] = "C"
        return subprocess.run(prefix + args, env=env, **kwargs)

    @staticmethod
    def run_command(args, **kwargs):
        env = kwargs.pop("env", os.environ.copy())
        env["LC_ALL"] = "C"
        return subprocess.run(args, env=env, **kwargs)

    @staticmethod
    def get_lsblk_json(device=None):
        column_sets = [
            "PATH,NAME,SIZE,TYPE,FSTYPE,MOUNTPOINTS,RM,HOTPLUG,TRAN",
            "PATH,NAME,SIZE,TYPE,FSTYPE,MOUNTPOINTS,RM,TRAN",
            "PATH,NAME,SIZE,TYPE,FSTYPE,MOUNTPOINT,RM,TRAN",
        ]

        last_error = None
        for columns in column_sets:
            cmd = ["lsblk", "-J", "-o", columns]
            if device:
                cmd.append(device)

            try:
                result = DDGui.run_command(
                    cmd,
                    capture_output=True,
                    text=True,
                    check=True
                )
                data = json.loads(result.stdout)
                for entry in data.get("blockdevices", []):
                    DDGui.normalize_lsblk_entry(entry)
                return data
            except (subprocess.CalledProcessError, json.JSONDecodeError) as exc:
                last_error = exc

        raise last_error or RuntimeError("Unable to query lsblk.")

    @staticmethod
    def normalize_lsblk_entry(entry):
        if "mountpoints" not in entry:
            mountpoint = entry.get("mountpoint")
            entry["mountpoints"] = [mountpoint] if mountpoint else []

        for child in entry.get("children", []):
            DDGui.normalize_lsblk_entry(child)

        return entry

    @staticmethod
    def flatten_lsblk_devices(entries):
        flattened = []
        for entry in entries:
            flattened.append(entry)
            flattened.extend(
                DDGui.flatten_lsblk_devices(entry.get("children", []))
            )
        return flattened

    @classmethod
    def device_has_protected_mounts(cls, entry):
        for node in cls.flatten_lsblk_devices([entry]):
            for mountpoint in node.get("mountpoints") or []:
                if mountpoint in cls.PROTECTED_MOUNTPOINTS:
                    return True
        return False

    @classmethod
    def is_removable_device(cls, entry):
        transport = (entry.get("tran") or "").lower()
        return (
            bool(entry.get("rm"))
            or bool(entry.get("hotplug"))
            or transport in cls.REMOVABLE_TRANSPORTS
        )

    # ----------------- Devices -----------------
    def refresh_devices(self):
        self.dev_combo.clear()

        try:
            lsblk = self.get_lsblk_json()
            show_advanced = self.advanced_devices_checkbox.isChecked()
            devices = [
                entry["path"]
                for entry in lsblk.get("blockdevices", [])
                if (
                    entry.get("type") == "disk"
                    and entry.get("path")
                    and not self.device_has_protected_mounts(entry)
                    and (
                        show_advanced
                        or self.is_removable_device(entry)
                    )
                )
            ]
        except Exception:
            devices = []

        self.dev_combo.addItems(devices)
        if devices:
            self.update_dev_capacity()
            return

        self.dev_size_label.setText("Device Capacity: N/A")
        self.start_btn.setEnabled(False)
        self.unmount_btn.setEnabled(False)

    def update_dev_capacity(self):
        device = self.dev_combo.currentText().strip()
        if not device:
            self.dev_size_label.setText("Device Capacity: N/A")
            self.start_btn.setEnabled(False)
            self.unmount_btn.setEnabled(False)
            return

        try:
            result = self.run_command(
                ["lsblk", "-b", "-dn", "-o", "SIZE", device],
                capture_output=True,
                text=True,
                check=True
            )
            size_bytes = int(result.stdout.strip())
            self.dev_size_label.setText(
                f"Device Capacity: {self.human_readable(size_bytes)}"
            )
        except Exception:
            self.dev_size_label.setText("Device Capacity: N/A")

        mounts = self.get_mounted_partitions(device)
        self.start_btn.setEnabled(not mounts)
        self.unmount_btn.setEnabled(bool(mounts))

    def unmount_device(self):
        device = self.dev_combo.currentText().strip()
        partitions = self.get_mounted_partitions(device)
        if not partitions:
            QMessageBox.information(self, "Unmount", f"No mounted partitions found for {device}.")
            self.update_dev_capacity()
            return

        failures = []
        for partition in partitions:
            try:
                self.run_privileged(["umount", partition], check=True)
            except PermissionError:
                QMessageBox.critical(
                    self,
                    "Insufficient Privileges",
                    "Unmounting requires root, sudo, doas, or pkexec."
                )
                return
            except subprocess.CalledProcessError as exc:
                failures.append(f"{partition}: {exc.stderr.strip() if exc.stderr else exc}")

        self.update_dev_capacity()

        if failures:
            QMessageBox.warning(
                self,
                "Unmount Incomplete",
                "Some partitions could not be unmounted:\n\n" + "\n".join(failures)
            )
            return

        QMessageBox.information(self, "Unmount", f"Unmounted partitions for {device}.")

    # ----------------- LSBLK info -----------------
    def get_lsblk_info(self, device):
        try:
            result = self.run_command(
                ["lsblk", "-o", "NAME,SIZE,TYPE,FSTYPE,MOUNTPOINT", device],
                capture_output=True,
                text=True,
                check=True
            )
            return result.stdout.strip()
        except Exception:
            return "Unable to retrieve partition information."

    # ----------------- SMART info -----------------
    def get_smart_info(self, device):
        if shutil.which("smartctl") is None:
            return None

        try:
            result = self.run_command(
                ["smartctl", "-i", device],
                capture_output=True,
                text=True,
                timeout=2
            )

            info_lines = []
            for line in result.stdout.splitlines():
                if (
                    line.startswith("Device Model")
                    or line.startswith("Form Factor")
                    or line.startswith("User Capacity")
                ):
                    info_lines.append(line)

            return "\n".join(info_lines) if info_lines else None

        except subprocess.TimeoutExpired:
            return None
        except Exception:
            return None

    def verify_device_not_in_use(self, device):
        check_code = (
            "import errno, os, sys\n"
            "path = sys.argv[1]\n"
            "flags = os.O_WRONLY | os.O_EXCL\n"
            "try:\n"
            "    fd = os.open(path, flags)\n"
            "except OSError as exc:\n"
            "    print(exc, file=sys.stderr)\n"
            "    sys.exit(exc.errno or 1)\n"
            "else:\n"
            "    os.close(fd)\n"
        )

        try:
            result = self.run_privileged(
                ["python3", "-c", check_code, device],
                capture_output=True,
                text=True
            )
        except PermissionError:
            QMessageBox.critical(
                self,
                "Insufficient Privileges",
                "Writing images requires root, sudo, doas, or pkexec."
            )
            return False

        if result.returncode == 0:
            return True

        error_text = (result.stderr or result.stdout or "").strip()
        QMessageBox.critical(
            self,
            "Device Busy",
            f"Refusing to write to {device} because it appears to be in use.\n\n{error_text}"
        )
        return False

    # ----------------- Confirm destructive write -----------------
    def confirm_destructive_write(self, device, image):
        try:
            result = self.run_command(
                ["lsblk", "-b", "-dn", "-o", "SIZE", device],
                capture_output=True, text=True
            )
            size_bytes = int(result.stdout.strip())
            size_hr = self.human_readable(size_bytes)
        except Exception:
            size_hr = "Unknown size"

        lsblk_info = self.get_lsblk_info(device)
        smart_info = self.get_smart_info(device)
        smart_text = f"\nSMART Info:\n{smart_info}" if smart_info else ""

        message = (
            "WARNING: DESTRUCTIVE OPERATION\n\n"
            f"This operation will DESTROY ALL DATA on the target device.\n\n"
            f"Target device: {device}\n"
            f"Capacity: {size_hr}\n\n"
            f"Current partition layout:\n{lsblk_info}"
            f"{smart_text}\n\n"
            f"The device will be completely wiped and rewritten with:\n{image}\n\n"
            "Click OK to begin. This action CANNOT be undone."
        )

        reply = QMessageBox.warning(
            self,
            "Confirm Disk Write",
            message,
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel
        )
        return reply == QMessageBox.StandardButton.Ok

    # ----------------- Start dd -----------------
    def start_dd(self):
        infile = self.input_edit.text().strip()
        ofile = self.dev_combo.currentText().strip()

        if not os.path.isfile(infile):
            self.progress_display.append("Input file invalid")
            return

        try:
            lsblk = self.get_lsblk_json(ofile)
            blockdevices = lsblk.get("blockdevices", [])
            if blockdevices and self.device_has_protected_mounts(blockdevices[0]):
                self.progress_display.append(
                    f"Refusing to write to protected system device: {ofile}"
                )
                QMessageBox.critical(
                    self,
                    "Protected Device",
                    f"{ofile} backs a core mounted filesystem and cannot be used."
                )
                return
        except Exception:
            self.progress_display.append(
                f"Unable to verify whether {ofile} is a protected system device."
            )
            return

        # Safety confirmation
        if not self.confirm_destructive_write(ofile, infile):
            self.progress_display.append("Operation cancelled by user.")
            return

        mounted_partitions = self.get_mounted_partitions(ofile)
        if mounted_partitions:
            self.progress_display.append(
                "Target device still has mounted partitions: "
                + ", ".join(mounted_partitions)
            )
            return

        if not self.verify_device_not_in_use(ofile):
            self.progress_display.append(f"Target device is busy: {ofile}")
            return

        bs = self.bs_combo.currentText()
        dd_path = shutil.which("dd")
        if dd_path is None:
            QMessageBox.critical(
                self,
                "Missing Dependency",
                "GNU dd was not found in PATH."
            )
            return

        dd_cmd = [dd_path, f"if={infile}", f"of={ofile}", f"bs={bs}"]
        if self.sync_checkbox.isChecked():
            dd_cmd.append("oflag=sync")
        if self.progress_checkbox.isChecked():
            dd_cmd.append("status=progress")

        # Privilege handling
        prefix = self.privilege_prefix()
        if prefix is None:
            QMessageBox.critical(
                self,
                "Insufficient Privileges",
                "Writing images requires elevated privileges. Run as root or use sudo/doas/pkexec."
            )
            return

        cmd = prefix + dd_cmd

        self.progress_display.append(f"Running: {' '.join(cmd)}\n")
        self.start_btn.setEnabled(False)
        self.start_btn.setText("Writing image...")

        self.progress_bar.setValue(0)
        self.eta_label.setText("Progress: 0% - ETA: calculating…")
        self.last_bytes = 0
        self.last_update_time = time.time()

        self.worker = DDWorker(cmd)
        self.worker.progress.connect(self.update_progress)
        self.worker.error.connect(self.handle_worker_error)
        self.worker.finished.connect(self.dd_finished)
        self.worker.start()

    def handle_worker_error(self, message):
        self.progress_display.append(f"Failed to start dd: {message}")

    # ----------------- Progress update -----------------
    def update_progress(self, text):
        self.progress_display.append(text)

        try:
            bytes_written = int(text.split()[0])
            percent = int((bytes_written / self.image_size_bytes) * 100)
            percent = min(percent, 100)
            self.progress_bar.setValue(percent)

            now = time.time()
            delta_bytes = bytes_written - self.last_bytes
            delta_time = now - self.last_update_time

            if delta_bytes > 0 and delta_time > 0:
                speed = delta_bytes / delta_time
                remaining = self.image_size_bytes - bytes_written
                eta = int(remaining / speed)
                mins, secs = divmod(eta, 60)
                eta_str = f"{mins}m {secs}s"
            else:
                eta_str = "calculating…"

            self.eta_label.setText(f"Progress: {percent}% - ETA: {eta_str}")

            self.last_bytes = bytes_written
            self.last_update_time = now

        except Exception:
            pass

        self.progress_display.verticalScrollBar().setValue(
            self.progress_display.verticalScrollBar().maximum()
        )

    # ----------------- Finished -----------------
    def dd_finished(self, returncode):
        self.start_btn.setEnabled(True)
        self.start_btn.setText("Start DD")

        if returncode == 0:
            self.progress_bar.setValue(100)
            self.eta_label.setText("Progress: 100% - Completed")
            QMessageBox.information(self, "DD Completed", "DD Completed Successfully!")
            return

        self.eta_label.setText("Progress: Failed")
        QMessageBox.critical(self, "DD Failed", f"dd exited with status {returncode}.")

    # ----------------- Mount detection -----------------
    @staticmethod
    def get_mounted_partitions(device):
        try:
            lsblk = DDGui.get_lsblk_json(device)
        except Exception:
            return []

        mounted = []
        for entry in DDGui.flatten_lsblk_devices(lsblk.get("blockdevices", [])):
            mountpoints = entry.get("mountpoints") or []
            if any(mountpoint for mountpoint in mountpoints):
                path = entry.get("path")
                if path:
                    mounted.append(path)

        return mounted


# ----------------- Main -----------------
if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = DDGui()
    window.show()
    sys.exit(app.exec())
