from pathlib import PurePosixPath
from PyInstaller.utils.hooks.qt import add_qt5_dependencies, pyside2_library_info


def needed(entry):
    parts = PurePosixPath(entry[1].replace('\\', '/')).parts
    path = parts[parts.index('qml')+1:]
    return not path or path[0] in ('QtQml', 'QtQuick.2', 'QtGraphicalEffects') or (path[0] == 'QtQuick' and (len(path) == 1 or path[1] in ('Controls.2', 'Templates.2', 'Layouts', 'Window.2'))) or path[:2] == ('Qt', 'labs')


hiddenimports, binaries, datas = add_qt5_dependencies(__file__)
qml_binaries, qml_datas = pyside2_library_info.collect_qtqml_files()
hiddenimports += ['PySide2.QtGui']
binaries += [entry for entry in qml_binaries if needed(entry)]
datas += [entry for entry in qml_datas if needed(entry)]
