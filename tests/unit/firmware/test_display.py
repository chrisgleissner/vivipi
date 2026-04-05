from types import SimpleNamespace

from firmware.display import SH1107Display, _pin_number


class FakeFramebuffer:
    def __init__(self):
        self.calls = []

    def fill(self, value):
        self.calls.append(("fill", value))

    def fill_rect(self, x, y, width, height, color):
        self.calls.append(("fill_rect", x, y, width, height, color))

    def text(self, value, x, y, color):
        self.calls.append(("text", value, x, y, color))


def test_pin_number_parses_gpio_names():
    assert _pin_number("GP14") == 14


def test_draw_frame_renders_rows_and_selected_inversion():
    framebuffer = FakeFramebuffer()
    display = SH1107Display.__new__(SH1107Display)
    display.width = 128
    display.height = 64
    display.framebuffer = framebuffer
    marker = {"shown": False}
    display._show = lambda: marker.__setitem__("shown", True)

    display.draw_frame(
        SimpleNamespace(
            rows=("Alpha           ", "Bravo           ") + (" " * 16,) * 6,
            inverted_row=1,
            shift_offset=(1, 0),
        )
    )

    assert framebuffer.calls[0] == ("fill", 0)
    assert ("fill_rect", 0, 8, 128, 8, 1) in framebuffer.calls
    assert ("text", "Alpha           ", 1, 0, 1) in framebuffer.calls
    assert ("text", "Bravo           ", 1, 8, 0) in framebuffer.calls
    assert marker["shown"] is True