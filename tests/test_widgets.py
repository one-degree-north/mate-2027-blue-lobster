from __future__ import annotations

import pytest

from rovkit import Button, Module, Plot, Progress, Readout, Slider, Toggle


def test_slider_clamps_and_snaps_to_step() -> None:
    slider = Slider(0, 10, step=2)
    assert slider.coerce(11) == 10
    assert slider.coerce(-5) == 0
    assert slider.coerce(3.1) == 4
    assert slider.coerce(9.9) == 10


@pytest.mark.parametrize("bad", ["5", None, True, float("nan"), float("inf"), [1]])
def test_slider_rejects_non_numbers(bad: object) -> None:
    with pytest.raises(ValueError):
        Slider(0, 10).coerce(bad)


def test_slider_validates_its_definition() -> None:
    with pytest.raises(ValueError):
        Slider(5, 5)
    with pytest.raises(ValueError):
        Slider(0, 1, step=0)


def test_toggle_requires_a_real_bool() -> None:
    toggle = Toggle()
    assert toggle.coerce(True) is True
    with pytest.raises(ValueError):
        toggle.coerce(1)


def test_plot_accepts_numbers_and_none() -> None:
    plot = Plot()
    assert plot.coerce(3) == 3.0
    assert plot.coerce(None) is None
    with pytest.raises(ValueError):
        plot.coerce("x")


def test_labels_default_from_attribute_names() -> None:
    class M(Module):
        water_depth = Readout()
        go = Button("Launch")

    assert [w.label for w in M._widgets.values()] == ["Water depth", "Launch"]


def test_handler_for_missing_widget_fails_at_class_definition() -> None:
    with pytest.raises(TypeError, match=r"no widget 'typo'"):

        class M(Module):
            async def on_typo(self) -> None: ...


def test_handler_for_output_widget_fails() -> None:
    with pytest.raises(TypeError, match="output-only"):

        class M(Module):
            reading = Readout()

            async def on_reading(self, value: float) -> None: ...


def test_handler_signature_is_checked() -> None:
    with pytest.raises(TypeError, match=r"\(self, value\)"):

        class A(Module):
            gain = Slider(0, 1)

            async def on_gain(self) -> None: ...

    with pytest.raises(TypeError, match=r"\(self\)"):

        class B(Module):
            go = Button()

            async def on_go(self, value: int) -> None: ...


def test_widgets_are_inherited() -> None:
    class Base(Module):
        gain = Slider(0, 1)

    class Child(Base):
        extra = Toggle()

    assert list(Child._widgets) == ["gain", "extra"]


def test_progress_clamps_and_allows_unknown() -> None:
    progress = Progress()
    assert progress.initial() == 0.0
    assert progress.coerce(0.25) == 0.25
    assert progress.coerce(7) == 1.0
    assert progress.coerce(-3) == 0.0
    assert progress.coerce(None) is None
    assert progress.schema()["kind"] == "progress"


@pytest.mark.parametrize("bad", ["50%", True, float("nan"), float("inf"), [0.5]])
def test_progress_rejects_non_numbers(bad: object) -> None:
    with pytest.raises(ValueError):
        Progress().coerce(bad)


def test_progress_is_output_only() -> None:
    with pytest.raises(TypeError, match="output-only"):

        class M(Module):
            done = Progress()

            async def on_done(self, value: float) -> None: ...
