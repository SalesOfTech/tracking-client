from __future__ import annotations

import abc
from dataclasses import dataclass


@dataclass
class ActiveApplication:
    pid: int
    process_name: str | None
    title: str | None = None
    bundle_id: str | None = None
    executable: str | None = None


class PlatformAdapter(abc.ABC):
    def is_session_active(self) -> bool:
        return True

    @abc.abstractmethod
    def platform_name(self) -> str:
        raise NotImplementedError

    @abc.abstractmethod
    def get_active_application(self) -> ActiveApplication | None:
        raise NotImplementedError

    @abc.abstractmethod
    def get_idle_duration_ms(self) -> int:
        raise NotImplementedError

    @abc.abstractmethod
    def ensure_autostart(self, binary_path: str, enable: bool = True) -> None:
        raise NotImplementedError

    @abc.abstractmethod
    def remove_autostart(self) -> None:
        raise NotImplementedError
