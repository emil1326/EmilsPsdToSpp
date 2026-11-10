"""The plugin's menu entry, dialog and import flow (runs inside Painter)."""
import os
import re
import shutil
import tempfile
import traceback

import substance_painter.logging as log
import substance_painter.project as project
import substance_painter.textureset as ts
import substance_painter.ui as ui
from PySide6 import QtCore, QtGui, QtWidgets

from . import deps

TITLE = "Emil's PSD to Painter"
_widgets = []
_settings = QtCore.QSettings("EmilsWork", "EmilsPsdToSpp")


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", s.lower())


def guess_texture_set(psd_path, names):
    stem = os.path.splitext(os.path.basename(psd_path))[0]
    stem = re.sub(r"[_\- ]?psd$", "", stem, flags=re.IGNORECASE)
    key = _norm(stem)
    by_norm = {_norm(n): n for n in names}
    if key in by_norm:
        return by_norm[key]
    for nk, n in by_norm.items():
        if nk.startswith(key) or key.startswith(nk):
            return n
    return None


class ImportDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(TITLE)
        self.resize(720, 460)
        self.sets = [s.name() for s in ts.all_texture_sets()]

        self.table = QtWidgets.QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["PSD file", "Texture set"])
        self.table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeToContents)
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)

        add = QtWidgets.QPushButton("Add PSD files...")
        add.clicked.connect(self.add_files)
        remove = QtWidgets.QPushButton("Remove")
        remove.clicked.connect(self.remove_rows)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(add)
        row.addWidget(remove)
        row.addStretch()

        self.replace = QtWidgets.QCheckBox("Delete the texture set's existing layers first")
        self.direct = QtWidgets.QCheckBox("Put masks and pixels directly in the layers (saves, closes and reopens the project)")
        self.direct.setChecked(True)
        self.direct.setToolTip(
            "Painter's API can only add a bitmap to a mask or a paint layer as a fill effect. With this\n"
            "on, the plugin saves the project, moves each one into its layer inside the .spp file,\n"
            "and reopens it, so it's like you painted it yourself.\n"
            "A backup <project>.spp.bak is kept next to it.")
        self.all_paint = QtWidgets.QCheckBox("Import every layer as a paint layer (exact pixels, no flat colors)")
        self.all_paint.setToolTip(
            "By default each layer becomes a fill layer with one flat color + a mask, and only\n"
            "layers with too many colors become paint layers. Tick this to get paint layers\n"
            "holding the PSD pixels for everything, ready to paint over.")
        self.translate = QtWidgets.QCheckBox("Translate Japanese layer names to English")
        self.translate.setChecked(True)

        from .names import DEFAULT_SKIP
        self.skip = QtWidgets.QLineEdit(_settings.value("skip", DEFAULT_SKIP))
        self.skip.setToolTip("Regular expression. Layers whose name matches are left out "
                             "(UV wireframes, guides, flattened previews...). Empty = keep everything.")
        self.tolerance = QtWidgets.QSpinBox()
        self.tolerance.setRange(0, 40)
        self.tolerance.setValue(int(_settings.value("tolerance", 6)))
        self.tolerance.setToolTip("How far (0-255) a layer's pixels may drift from its flat color(s)\n"
                                  "before it's kept as a texture instead. Higher = more flat colors.")
        form = QtWidgets.QFormLayout()
        form.addRow("Skip layers matching", self.skip)
        form.addRow("Color tolerance", self.tolerance)

        buttons = QtWidgets.QDialogButtonBox()
        self.go = buttons.addButton("Import", QtWidgets.QDialogButtonBox.AcceptRole)
        buttons.addButton(QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        lay = QtWidgets.QVBoxLayout(self)
        lay.addWidget(self.table)
        lay.addLayout(row)
        for w in (self.replace, self.all_paint, self.direct, self.translate):
            lay.addWidget(w)
        lay.addLayout(form)
        lay.addWidget(buttons)

    def add_files(self):
        start = _settings.value("last_dir", "")
        files, _ = QtWidgets.QFileDialog.getOpenFileNames(self, "PSD files", start, "Photoshop (*.psd)")
        if files:
            _settings.setValue("last_dir", os.path.dirname(files[0]))
        for path in files:
            r = self.table.rowCount()
            self.table.insertRow(r)
            item = QtWidgets.QTableWidgetItem(os.path.basename(path))
            item.setData(QtCore.Qt.UserRole, path)
            item.setToolTip(path)
            item.setFlags(item.flags() & ~QtCore.Qt.ItemIsEditable)
            self.table.setItem(r, 0, item)
            combo = QtWidgets.QComboBox()
            combo.addItem("(skip)")
            combo.addItems(self.sets)
            guess = guess_texture_set(path, self.sets)
            if guess:
                combo.setCurrentText(guess)
            elif len(self.sets) == 1:
                combo.setCurrentIndex(1)
            self.table.setCellWidget(r, 1, combo)

    def remove_rows(self):
        for r in sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True):
            self.table.removeRow(r)

    def jobs(self):
        out = []
        for r in range(self.table.rowCount()):
            target = self.table.cellWidget(r, 1).currentText()
            if target != "(skip)":
                out.append((self.table.item(r, 0).data(QtCore.Qt.UserRole), target))
        return out


class _Progress:
    def __init__(self, parent):
        self.dlg = QtWidgets.QProgressDialog("", None, 0, 0, parent)
        self.dlg.setWindowTitle(TITLE)
        self.dlg.setWindowModality(QtCore.Qt.WindowModal)
        self.dlg.setMinimumDuration(0)
        self.dlg.setMinimumWidth(420)
        self.dlg.show()

    def __call__(self, text):
        self.dlg.setLabelText(text)
        QtWidgets.QApplication.processEvents()

    def close(self):
        self.dlg.close()


def _ensure_numpy(parent):
    if deps.have_numpy():
        return True
    size = deps.wheel_size_hint()
    mb = f"about {size // 1_000_000} MB" if size else "a few MB"
    answer = QtWidgets.QMessageBox.question(
        parent, TITLE,
        "This plugin needs numpy, which Painter's Python doesn't include.\n\n"
        f"Download the official numpy wheel from pypi.org ({mb}, one time only)?\n"
        f"It goes into:\n{deps.DEPS_DIR}")
    if answer != QtWidgets.QMessageBox.Yes:
        return False
    progress = _Progress(parent)
    try:
        progress("Downloading numpy...")
        deps.install(progress)
    finally:
        progress.close()
    return deps.have_numpy()


def _apply_direct_masks(progress, report):
    from . import direct_masks
    path = project.file_path()
    progress("Saving the project...")
    project.save(project.ProjectSaveMode.Full)
    path = project.file_path()
    progress("Closing the project...")
    project.close()
    backup = path + ".bak"
    shutil.copy2(path, backup)
    try:
        progress("Moving the masks into their layers...")
        masks, pixels = direct_masks.apply(path)
        line = f"Moved into their layers: {masks} masks"
        if pixels:
            line += f", {pixels} paint layer contents"
        report.append(f"{line} (backup: {os.path.basename(backup)})")
    except Exception as e:
        shutil.copy2(backup, path)
        report.append(f"Masks/pixels left as fill effects, couldn't rewrite the file: {e}")
        log.error(traceback.format_exc())
    progress("Reopening the project...")
    try:
        project.open(path)
    except Exception:
        shutil.copy2(backup, path)
        project.open(path)
        report.append("Painter refused the rewritten file, so the backup was restored "
                      "(masks/pixels stay as fill effects).")


def run():
    parent = QtWidgets.QApplication.activeWindow()
    if not project.is_open():
        QtWidgets.QMessageBox.information(parent, TITLE, "Open a project first.")
        return
    dlg = ImportDialog(parent)
    if dlg.exec() != QtWidgets.QDialog.Accepted:
        return
    jobs = dlg.jobs()
    if not jobs:
        return
    _settings.setValue("skip", dlg.skip.text())
    _settings.setValue("tolerance", dlg.tolerance.value())
    direct = dlg.direct.isChecked()
    if direct and not project.file_path():
        QtWidgets.QMessageBox.information(
            parent, TITLE, "Save the project once first (the masks step rewrites the saved file).")
        return
    if not _ensure_numpy(parent):
        return

    from . import builder, extract
    sets = {s.name(): s for s in ts.all_texture_sets()}
    work = tempfile.mkdtemp(prefix="EmilsPsdToSpp_")
    progress = _Progress(parent)
    report = []
    try:
        for psd_path, target in jobs:
            name = os.path.basename(psd_path)
            folder = os.path.join(work, re.sub(r"\W+", "_", name))
            m = extract.extract(psd_path, folder, translate=dlg.translate.isChecked(),
                                skip_pattern=dlg.skip.text(),
                                tolerance=-1 if dlg.all_paint.isChecked() else dlg.tolerance.value(),
                                progress=lambda l, n=name: progress(f"Reading {n}\n{l}"))
            count = builder.build(m, sets[target], replace=dlg.replace.isChecked(),
                                  progress=lambda l, n=name: progress(f"Building {n}\n{l}"))
            line = f"{name} -> {target}: {count} layers"
            if m["dropped"]:
                line += f", skipped {len(m['dropped'])} ({', '.join(m['dropped'][:5])}{'...' if len(m['dropped']) > 5 else ''})"
            report.append(line)
        if direct:
            _apply_direct_masks(progress, report)
    except Exception as e:
        log.error(traceback.format_exc())
        report.append(f"Failed: {e!r}")
    finally:
        progress.close()
        if direct:
            shutil.rmtree(work, ignore_errors=True)
    msg = "\n".join(report)
    log.info(f"{TITLE}\n{msg}")
    QtWidgets.QMessageBox.information(parent, TITLE, msg)


def _run_safe():
    try:
        run()
    except Exception as e:
        log.error(traceback.format_exc())
        QtWidgets.QMessageBox.critical(None, TITLE, repr(e))


def start_plugin():
    action = QtGui.QAction("Import PSD layers (Emil's)...")
    action.triggered.connect(_run_safe)
    ui.add_action(ui.ApplicationMenu.File, action)
    _widgets.append(action)


def close_plugin():
    for w in _widgets:
        ui.delete_ui_element(w)
    _widgets.clear()
