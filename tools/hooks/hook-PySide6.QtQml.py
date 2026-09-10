from pathlib import PurePosixPath
from PyInstaller.utils.hooks.qt import add_qt6_dependencies, pyside6_library_info


def needed(entry):
    parts = PurePosixPath(entry[1].replace('\\', '/')).parts
    path = parts[parts.index('qml')+1:]
    return not path or path[0] == 'QtQml' or (path[0] == 'QtQuick' and (len(path) == 1 or path[1] in ('Controls', 'Templates', 'Layouts', 'Window')))


hiddenimports, binaries, datas = add_qt6_dependencies(__file__)
qml_binaries, qml_datas = pyside6_library_info.collect_qtqml_files()
binaries += [entry for entry in qml_binaries if needed(entry)]
datas += [entry for entry in qml_datas if needed(entry)]
