"""The same QML interface supports the modern and legacy Qt runtimes."""
try:
    from PySide6.QtCore import QObject, Property, Signal, Slot, QTimer, QUrl, Qt, QCoreApplication, QEvent
    from PySide6.QtGui import QDesktopServices, QIcon, QFont, QFontDatabase
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtQuick import QQuickWindow
    from PySide6.QtWidgets import QApplication, QSystemTrayIcon, QMenu
except ImportError:
    from PySide2.QtCore import QObject, Property, Signal, Slot, QTimer, QUrl, Qt, QCoreApplication, QEvent
    from PySide2.QtGui import QDesktopServices, QIcon, QFont, QFontDatabase
    from PySide2.QtQml import QQmlApplicationEngine
    from PySide2.QtQuick import QQuickWindow
    from PySide2.QtWidgets import QApplication, QSystemTrayIcon, QMenu
