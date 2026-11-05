"""Widgets a module declares as class attributes; the UI renders them automatically.

    class Vision(Module):
        threshold = Slider(0, 255, default=128)
        detect = Button("Run detection")
        crabs = Readout("Crabs found")

        async def on_detect(self): ...          # Button -> no argument
        async def on_threshold(self, value): ...  # Slider/Toggle -> the new value

Reading ``self.threshold`` gives the current value; assigning to a Slider, Toggle,
Readout or Plot publishes the new value to the UI.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any, ClassVar, Self, overload

if TYPE_CHECKING:
    from .module import Module


class Widget[T]:
    """Base descriptor. ``accepts_input`` widgets receive commands from the UI."""

    kind: ClassVar[str]
    accepts_input: ClassVar[bool] = False

    def __init__(self, label: str | None = None) -> None:
        self.name = ""
        self.label = label

    def __set_name__(self, owner: type, name: str) -> None:
        self.name = name
        if self.label is None:
            self.label = name.replace("_", " ").capitalize()

    @overload
    def __get__(self, obj: None, owner: type) -> Self: ...
    @overload
    def __get__(self, obj: Module, owner: type) -> T: ...
    def __get__(self, obj: Module | None, owner: type) -> Self | T:
        if obj is None:
            return self
        value: T = obj._widget_values[self.name]
        return value

    def __set__(self, obj: Module, value: T) -> None:
        obj._set_widget(self, self.coerce(value))

    def initial(self) -> T:
        raise NotImplementedError

    def coerce(self, value: Any) -> T:
        """Validate a value from module code or the UI; raise ``ValueError`` if unacceptable."""
        return value

    def schema(self) -> dict[str, Any]:
        return {"id": self.name, "kind": self.kind, "label": self.label}


class Button(Widget[None]):
    kind = "button"
    accepts_input = True

    def initial(self) -> None:
        return None

    def __set__(self, obj: Module, value: None) -> None:
        raise AttributeError(f"button {self.name!r} cannot be assigned")


class Slider(Widget[float]):
    kind = "slider"
    accepts_input = True

    def __init__(
        self,
        min: float,
        max: float,
        *,
        step: float | None = None,
        default: float | None = None,
        label: str | None = None,
        unit: str | None = None,
    ) -> None:
        super().__init__(label)
        if not min < max:
            raise ValueError(f"Slider needs min < max, got {min} and {max}")
        if step is not None and step <= 0:
            raise ValueError(f"Slider step must be positive, got {step}")
        self.min, self.max, self.step, self.unit = min, max, step, unit
        self._default = min if default is None else default

    def initial(self) -> float:
        return self.coerce(self._default)

    def coerce(self, value: Any) -> float:
        if (
            isinstance(value, bool)
            or not isinstance(value, int | float)
            or not math.isfinite(value)
        ):
            raise ValueError(f"slider {self.name!r} needs a finite number, got {value!r}")
        number = min(max(float(value), self.min), self.max)
        if self.step is not None:
            number = min(self.min + round((number - self.min) / self.step) * self.step, self.max)
        return number

    def schema(self) -> dict[str, Any]:
        return super().schema() | {
            "min": self.min,
            "max": self.max,
            "step": self.step,
            "unit": self.unit,
        }


class Number(Widget[float]):
    """A number the user types in. Out-of-range values are clamped; nothing snaps to ``step``."""

    kind = "number"
    accepts_input = True

    def __init__(
        self,
        min: float | None = None,
        max: float | None = None,
        *,
        step: float | None = None,
        default: float = 0.0,
        label: str | None = None,
        unit: str | None = None,
    ) -> None:
        super().__init__(label)
        if min is not None and max is not None and not min < max:
            raise ValueError(f"Number needs min < max, got {min} and {max}")
        if step is not None and step <= 0:
            raise ValueError(f"Number step must be positive, got {step}")
        self.min, self.max, self.step, self.unit = min, max, step, unit
        self._default = default

    def initial(self) -> float:
        return self.coerce(self._default)

    def coerce(self, value: Any) -> float:
        if (
            isinstance(value, bool)
            or not isinstance(value, int | float)
            or not math.isfinite(value)
        ):
            raise ValueError(f"number {self.name!r} needs a finite number, got {value!r}")
        number = float(value)
        if self.min is not None:
            number = max(number, self.min)
        if self.max is not None:
            number = min(number, self.max)
        return number

    def schema(self) -> dict[str, Any]:
        return super().schema() | {
            "min": self.min,
            "max": self.max,
            "step": self.step,
            "unit": self.unit,
        }


class Select(Widget[str]):
    """Pick one of several options. Options are ids, or a mapping of id to the label shown."""

    kind = "select"
    accepts_input = True

    def __init__(
        self,
        options: Sequence[str] | Mapping[str, str],
        *,
        default: str | None = None,
        label: str | None = None,
    ) -> None:
        super().__init__(label)
        self.options: dict[str, str] = (
            dict(options) if isinstance(options, Mapping) else {o: o for o in options}
        )
        if not self.options:
            raise ValueError("Select needs at least one option")
        if default is not None and default not in self.options:
            raise ValueError(f"Select default {default!r} is not one of {list(self.options)}")
        self._default = default if default is not None else next(iter(self.options))

    def initial(self) -> str:
        return self._default

    def coerce(self, value: Any) -> str:
        if not isinstance(value, str) or value not in self.options:
            raise ValueError(
                f"select {self.name!r} needs one of {list(self.options)}, got {value!r}"
            )
        return value

    def schema(self) -> dict[str, Any]:
        return super().schema() | {
            "options": [{"value": v, "label": text} for v, text in self.options.items()]
        }


class Toggle(Widget[bool]):
    kind = "toggle"
    accepts_input = True

    def __init__(self, *, default: bool = False, label: str | None = None) -> None:
        super().__init__(label)
        self._default = default

    def initial(self) -> bool:
        return self._default

    def coerce(self, value: Any) -> bool:
        if not isinstance(value, bool):
            raise ValueError(f"toggle {self.name!r} needs true/false, got {value!r}")
        return value


class Readout(Widget[Any]):
    """Shows any JSON value (number, string, ...). Output only."""

    kind = "readout"

    def __init__(
        self, label: str | None = None, *, unit: str | None = None, default: Any = None
    ) -> None:
        super().__init__(label)
        self.unit = unit
        self._default = default

    def initial(self) -> Any:
        return self._default

    def schema(self) -> dict[str, Any]:
        return super().schema() | {"unit": self.unit}


TONES = ("info", "ok", "warn", "error")


class Status(Widget[dict[str, str]]):
    """A line of text whose colour shows how things are going. Output only.

    self.status = "Idle"                          # tone "info"
    self.status = ("Reconstruction failed", "error")   # tone is info, ok, warn or error
    """

    kind = "status"

    def __init__(self, label: str | None = None, *, default: str = "") -> None:
        super().__init__(label)
        self._default = default

    def initial(self) -> dict[str, str]:
        return self.coerce(self._default)

    def __set__(self, obj: Module, value: str | tuple[str, str] | dict[str, str]) -> None:
        obj._set_widget(self, self.coerce(value))

    def coerce(self, value: Any) -> dict[str, str]:
        if isinstance(value, str):
            text, tone = value, "info"
        elif isinstance(value, tuple) and len(value) == 2:
            text, tone = value
        elif isinstance(value, dict) and set(value) == {"text", "tone"}:
            text, tone = value["text"], value["tone"]
        else:
            raise ValueError(f"status {self.name!r} needs text or (text, tone), got {value!r}")
        if not isinstance(text, str) or tone not in TONES:
            raise ValueError(f"status {self.name!r} needs a text and a tone from {TONES}")
        return {"text": text, "tone": tone}


class Progress(Widget[float | None]):
    """Progress bar: assign a fraction from 0 to 1, or ``None`` for unknown. Output only."""

    kind = "progress"

    def __init__(self, label: str | None = None, *, default: float | None = 0.0) -> None:
        super().__init__(label)
        self._default = default

    def initial(self) -> float | None:
        return self.coerce(self._default)

    def coerce(self, value: Any) -> float | None:
        if value is None:
            return None
        if (
            isinstance(value, bool)
            or not isinstance(value, int | float)
            or not math.isfinite(value)
        ):
            raise ValueError(f"progress {self.name!r} needs a number from 0 to 1, got {value!r}")
        return min(max(float(value), 0.0), 1.0)


class Plot(Widget[float | None]):
    """Scrolling time-series of the values you assign. The UI keeps the last ``window`` points."""

    kind = "plot"

    def __init__(
        self,
        label: str | None = None,
        *,
        unit: str | None = None,
        window: int = 200,
        y_min: float | None = None,
        y_max: float | None = None,
    ) -> None:
        super().__init__(label)
        if window < 2:
            raise ValueError("Plot window must be at least 2 points")
        self.unit, self.window, self.y_min, self.y_max = unit, window, y_min, y_max

    def initial(self) -> float | None:
        return None

    def coerce(self, value: Any) -> float | None:
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ValueError(f"plot {self.name!r} needs a number, got {value!r}")
        return float(value)

    def schema(self) -> dict[str, Any]:
        return super().schema() | {
            "unit": self.unit,
            "window": self.window,
            "yMin": self.y_min,
            "yMax": self.y_max,
        }
