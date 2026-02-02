#!/usr/bin/env python3
# DDWrap -- A simple QT GUI Wrapper for DD, in Python
# Author: Ben@LostGeek.NET
# Monday, Jan 19, 2026 -- Revision 0.9
# r0.9 -- Shell injection fix + exclusive device open safety
# r0.8 -- SMART info for SSD/HDDs in pre-flash warning...
# r0.7 -- Safety Dialog added before write actually starts...
# r0.6 -- Time estimate added to progress bar...
# r0.5 -- Layout improvements, added progress bar...

import sys
import subprocess
import os
import time
import shutil

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

# ----------------- Worker thread -----------------
class DDWorker(QThread):
    progress = pyqtSignal(str)
    finished = pyqtSignal()

    def __init__(self, cmd):
        super().__init__()
        self.cmd = cmd

    def run(self):
        process = subprocess.Popen(
            self.cmd,
            stderr=subprocess.PIPE,
            text=True
        )

        for line in process.stderr:
            if "bytes" in line:
                self.progress.emit(line.strip())

        process.wait()
        self.finished.emit()

# ----------------- Main GUI -----------------
class DDGui(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("DD Wrapper GUI")
        self.resize(650, 500)

        self.image_size_bytes = 0
        self.last_bytes = 0
        self.last_update_time = 0

        layout = QVBoxLayout()

        if is_root():
            self.setWindowTitle("DD Wrapper GUI (running as root)")

        # ----------------- Input file -----------------
        layout.addWidget(QLabel("Input File:"))
        h_input = QHBoxLayout()
        self.input_edit = QLineEdit()
        h_input.addWidget(self.input_edit)
        browse_btn = QPushButton("Browse")
        browse_btn.clicked.connect(self.browse_file)
        h_input.addWidget(browse_btn)
        layout.addLayout(h_input)

        self.file_size_label = QLabel("File Size: N/A")
        layout.addWidget(self.file_size_label)

        # ----------------- Block size -----------------
        layout.addWidget(QLabel("Block Size:"))
        self.bs_combo = QComboBox()
        self.bs_combo.addItems(["64k", "256k", "512k", "1M", "2M"])
        self.bs_combo.setCurrentText("512k")
        layout.addWidget(self.bs_combo)

        # ----------------- Progress output -----------------
        self.progress_display = QTextEdit()
        self.progress_display.setReadOnly(True)
        layout.addWidget(self.progress_display)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        layout.addWidget(self.progress_bar)

        self.eta_label = QLabel("Progress: 0% - ETA: N/A")
        layout.addWidget(self.eta_label)

        # ----------------- Device selection -----------------
        self.dev_size_label = QLabel("Target Device Capacity: N/A")
        layout.addWidget(self.dev_size_label)

        h_dev = QHBoxLayout()
        self.dev_combo = QComboBox()
        self.dev_combo.currentTextChanged.connect(self.update_dev_capacity)
        h_dev.addWidget(self.dev_combo)

        self.unmount_btn = QPushButton("Unmount Device")
        self.unmount_btn.clicked.connect(self.unmount_device)
        h_dev.addWidget(self.unmount_btn)
        layout.addLayout(h_dev)

        # ----------------- Flags -----------------
        self.sync_checkbox = QCheckBox("oflag=sync  (Default)")
        self.sync_checkbox.setChecked(True)
        layout.addWidget(self.sync_checkbox)

        self.progress_checkbox = QCheckBox("Show Progress")
        self.progress_checkbox.setChecked(True)
        layout.addWidget(self.progress_checkbox)

        # ----------------- Start -----------------
        self.start_btn = QPushButton("Start DD")
        self.start_btn.clicked.connect(self.start_dd)
        layout.addWidget(self.start_btn)

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

    @staticmethod
    def human_readable(num, suffix="B"):
        for unit in ["", "K", "M", "G", "T"]:
            if num < 1024:
                return f"{num:.2f} {unit}{suffix}"
            num /= 1024
        return f"{num:.2f} P{suffix}"

    # ----------------- Devices -----------------
    def refresh_devices(self):
        self.dev_combo.clear()
        devices = [
            f"/dev/{d}" for d in os.listdir("/dev")
            if d.startswith("sd") and not d[-1].isdigit()
        ]
        self.dev_combo.addItems(devices)
        if devices:
            self.update_dev_capacity()

    def update_dev_capacity(self):
        device = self.dev_combo.currentText().strip()
        if not device:
            return

        try:
            result = subprocess.run(
                ["lsblk", "-b", "-dn", "-o", "SIZE", device],
                capture_output=True, text=True
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
        subprocess.run(["sudo", "umount", f"{device}*"])
        QMessageBox.information(self, "Unmount", f"{device} unmounted.")
        self.update_dev_capacity()

    # ----------------- Confirm destructive write -----------------
    def confirm_destructive_write(self, device, image):
        lsblk_info = self.get_lsblk_info(device)
        smart_info = self.get_smart_info(device)
        smart_text = f"\nSMART Info:\n{smart_info}" if smart_info else ""

        message = (
            "WARNING: DESTRUCTIVE OPERATION\n\n"
            f"Target device: {device}\n\n"
            f"Partition layout:\n{lsblk_info}"
            f"{smart_text}\n\n"
            f"Image: {image}\n\n"
            "Click OK to continue."
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

        if not os.path.exists(infile):
            QMessageBox.critical(self, "Error", "Invalid input file.")
            return

        # Kernel-level exclusive open safety check
        try:
            fd = os.open(ofile, os.O_WRONLY | os.O_EXCL)
            os.close(fd)
        except PermissionError:
            QMessageBox.critical(self, "Permission Error", "Requires root privileges.")
            return
        except OSError:
            QMessageBox.critical(
                self, "Device Busy",
                "Target device is mounted or in use."
            )
            return

        if not self.confirm_destructive_write(ofile, infile):
            return

        cmd = ["dd", f"if={infile}", f"of={ofile}", f"bs={self.bs_combo.currentText()}"]

        if self.sync_checkbox.isChecked():
            cmd.append("oflag=sync")
        if self.progress_checkbox.isChecked():
            cmd.append("status=progress")

        if not is_root():
            if has_sudo():
                cmd.insert(0, "sudo")
            elif has_doas():
                cmd.insert(0, "doas")
            elif has_pkexec():
                cmd.insert(0, "pkexec")
            else:
                QMessageBox.critical(self, "Privileges", "Run as root or install sudo/doas.")
                return

        self.progress_display.append(f"Running: {' '.join(cmd)}\n")

        self.worker = DDWorker(cmd)
        self.worker.progress.connect(self.update_progress)
        self.worker.finished.connect(self.dd_finished)
        self.worker.start()

    # ----------------- Progress update -----------------
    def update_progress(self, text):
        self.progress_display.append(text)

    def dd_finished(self):
        QMessageBox.information(self, "Done", "DD completed successfully.")

    # ----------------- Mount detection -----------------
    @staticmethod
    def get_mounted_partitions(device):
        result = subprocess.run(
            ["lsblk", "-n", "-o", "MOUNTPOINT", device],
            capture_output=True,
            text=True
        )
        return bool(result.stdout.strip())

    def get_lsblk_info(self, device):
        result = subprocess.run(
            ["lsblk", device],
            capture_output=True,
            text=True
        )
        return result.stdout.strip()

    def get_smart_info(self, device):
        if shutil.which("smartctl") is None:
            return None
        return subprocess.run(
            ["smartctl", "-i", device],
            capture_output=True,
            text=True
        ).stdout.strip()

# ----------------- Main -----------------
if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = DDGui()
    window.show()
    sys.exit(app.exec())

