"""Shared onedir engine reached through the native macOS role wrappers."""
import runpy
import sys
from pathlib import Path


def main():
    args = sys.argv[1:]
    roles = {'app': ('runtime_entry', 'soft-tracking-app'),
             'native': ('native_entry', 'soft-tracking-host-app'),
             'bootstrap': ('bootstrap_entry', 'soft-tracking'),
             'bootstrap-native': ('bootstrap_native_entry', 'soft-tracking-host'),
             'setup': ('setup_entry', 'SOFT-Tracking-Setup')}
    if (len(args) < 5 or args[0] != '--tracking-role' or args[2] != '--tracking-wrapper'
            or args[4] != '--' or args[1] not in roles):
        raise ValueError('Invalid native engine invocation')
    module, name = roles[args[1]]
    wrapper = Path(args[3])
    if not wrapper.is_absolute() or wrapper.name != name or not wrapper.is_file():
        raise ValueError('Invalid native engine wrapper')
    # Existing subprocess and install-root discovery use the public wrapper path.
    # Keep _MEIPASS untouched: imports still belong to the sealed onedir bundle.
    sys.executable = str(wrapper)
    sys.argv = [str(wrapper)] + args[5:]
    runpy.run_module(module, run_name='__main__')


if __name__ == '__main__':
    main()
