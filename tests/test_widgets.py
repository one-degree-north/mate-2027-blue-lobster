from __future__ import annotations

import pytest

from rovkit import Button, Module, Number, Plot, Progress, Readout, Select, Slider, Status, Toggle


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


def test_number_clamps_to_its_bounds_but_does_not_snap() -> None:
    number = Number(0, 10, step=0.5)
    assert number.coerce(3.14159) == 3.14159
    assert number.coerce(99) == 10
    assert number.coerce(-1) == 0
    assert Number().coerce(-1e9) == -1e9  # no bounds, no clamping


@pytest.mark.parametrize("bad", ["5", None, True, float("nan"), float("inf")])
def test_number_rejects_non_numbers(bad: object) -> None:
    with pytest.raises(ValueError):
        Number().coerce(bad)


def test_number_validates_its_definition_and_schema() -> None:
    with pytest.raises(ValueError):
        Number(5, 5)
    with pytest.raises(ValueError):
        Number(step=0)
    schema = Number(0, 1, unit="m", default=2).schema()
    assert schema["kind"] == "number" and schema["unit"] == "m"
    assert Number(0, 1, default=2).initial() == 1.0  # the default is clamped too


def test_select_takes_ids_or_an_id_to_label_mapping() -> None:
    plain = Select(["a", "b"])
    assert plain.initial() == "a"
    assert plain.schema()["options"] == [
        {"value": "a", "label": "a"},
        {"value": "b", "label": "b"},
    ]
    labelled = Select({"lo": "Low", "hi": "High"}, default="hi")
    assert labelled.initial() == "hi"
    assert labelled.schema()["options"][0] == {"value": "lo", "label": "Low"}


def test_select_only_accepts_its_options() -> None:
    select = Select(["a", "b"])
    assert select.coerce("b") == "b"
    for bad in ("c", 1, None, ["a"]):
        with pytest.raises(ValueError):
            select.coerce(bad)
    with pytest.raises(ValueError):
        Select([])
    with pytest.raises(ValueError):
        Select(["a"], default="z")


def test_status_accepts_text_or_text_and_tone() -> None:
    status = Status(default="Idle")
    assert status.initial() == {"text": "Idle", "tone": "info"}
    assert status.coerce(("Failed", "error")) == {"text": "Failed", "tone": "error"}
    assert status.coerce({"text": "Done", "tone": "ok"}) == {"text": "Done", "tone": "ok"}
    for bad in (("x", "loud"), ("x",), 5, None, {"text": "x"}, (1, "ok")):
        with pytest.raises(ValueError):
            status.coerce(bad)


def test_new_input_widgets_get_handlers_checked_like_the_others() -> None:
    with pytest.raises(TypeError, match=r"\(self, value\)"):

        class A(Module):
            gain = Number()

            async def on_gain(self) -> None: ...

    with pytest.raises(TypeError, match="output-only"):

        class B(Module):
            note = Status()

            async def on_note(self, value: str) -> None: ...
