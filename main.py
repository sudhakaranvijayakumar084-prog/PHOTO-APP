__version__ = "1.0.0"

"""Deluxe Digital Studio - Android app (Kivy).
Choose photo -> choose layout -> Print.  Passport 3.5 x 4.5 cm, blue background, 6 x 4 in sheets."""
import os, threading, traceback, datetime
from kivy.config import Config
Config.set("kivy", "exit_on_escape", "1")
from kivy.app import App
from kivy.clock import Clock
from kivy.graphics import Color, RoundedRectangle
from kivy.graphics.texture import Texture
from kivy.metrics import dp, sp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.image import Image as KImage
from kivy.uix.label import Label
from kivy.utils import get_color_from_hex as hx
from PIL import Image, ImageOps

import engine as E
import android_io as A

BG, INK, MUTED, ACCENT = "#f3f4f8", "#111827", "#6b7280", "#6d28d9"
KINDS = {"info": ("#e8eeff", "#2b3a8f"), "ok": ("#e3f8ec", "#14633d"), "warn": ("#fff4d6", "#8a4b08"),
         "busy": ("#f1eaff", "#5b21b6"), "bad": ("#fde8e8", "#9b1c1c")}
PSCALE = 0.5            # quick on-screen preview size (the print version is built at full size)


def to_texture(im):
    im = im.convert("RGB")
    tex = Texture.create(size=im.size, colorfmt="rgb")
    tex.blit_buffer(im.tobytes(), colorfmt="rgb", bufferfmt="ubyte")
    tex.flip_vertical()
    return tex


def fit_preview(im, max_side=1100):
    s = min(1.0, max_side / max(im.size))
    if s < 1:
        im = im.resize((max(1, round(im.width * s)), max(1, round(im.height * s))), Image.LANCZOS)
    return im


class RBtn(Button):
    """Rounded, coloured button that greys out when disabled."""
    def __init__(self, color, **kw):
        super().__init__(background_normal="", background_down="", background_color=(0, 0, 0, 0),
                         bold=True, **kw)
        self.base = hx(color)
        self.color = (1, 1, 1, 1)
        with self.canvas.before:
            self._c = Color(*self.base)
            self._r = RoundedRectangle(pos=self.pos, size=self.size, radius=[dp(14)])
        self.bind(pos=self._upd, size=self._upd, state=self._upd, disabled=self._upd)

    def _upd(self, *a):
        self._r.pos, self._r.size = self.pos, self.size
        if self.disabled:
            self._c.rgba = hx("#e6e8ee")
            self.color = hx("#9aa1af")
        else:
            b = self.base
            self._c.rgba = (b[0] * .8, b[1] * .8, b[2] * .8, 1) if self.state == "down" else b
            self.color = (1, 1, 1, 1)


class Banner(Label):
    def __init__(self, **kw):
        super().__init__(halign="center", valign="middle", bold=True, font_size=sp(14), **kw)
        with self.canvas.before:
            self._c = Color(1, 1, 1, 1)
            self._r = RoundedRectangle(pos=self.pos, size=self.size, radius=[dp(12)])
        self.bind(pos=self._upd, size=self._upd, width=self._wrap)

    def _upd(self, *a):
        self._r.pos, self._r.size = self.pos, self.size

    def _wrap(self, *a):
        self.text_size = (self.width - dp(20), None)
        self.texture_update()

    def set(self, text, kind):
        bg, fg = KINDS[kind]
        self._c.rgba = hx(bg)
        self.color = hx(fg)
        self.text = text
        self.text_size = (self.width - dp(20), None)
        self.texture_update()
        self.height = max(dp(52), self.texture_size[1] + dp(18))


class Studio(BoxLayout):
    def __init__(self, data_dir, **kw):
        super().__init__(orientation="vertical", padding=dp(12), spacing=dp(8), **kw)
        self.data_dir = data_dir
        E.SAVE_DIR = data_dir
        E.LOG_FILE = os.path.join(data_dir, "studio_errors.log")
        self.out_dir = os.path.join(data_dir, "prints")
        os.makedirs(self.out_dir, exist_ok=True)
        # state
        self.busy, self.sel, self.full, self.loc = False, None, None, None
        self.sheet, self.hi, self.hi_lock = None, None, threading.Lock()
        self.last_uri = None
        from kivy.core.window import Window
        Window.clearcolor = hx(BG)

        head = BoxLayout(orientation="vertical", size_hint_y=None, height=dp(56))
        head.add_widget(Label(text="DELUXE", bold=True, font_size=sp(26), color=hx(ACCENT)))
        head.add_widget(Label(text="DIGITAL STUDIO", bold=True, font_size=sp(12), color=hx(INK)))
        self.add_widget(head)

        self.banner = Banner(size_hint_y=None, height=dp(52))
        self.add_widget(self.banner)

        self.prev = KImage(fit_mode="contain")
        self.add_widget(self.prev)

        self.b_choose = RBtn("#6d28d9", text="1   CHOOSE PHOTO", font_size=sp(18), size_hint_y=None, height=dp(56))
        self.b_choose.bind(on_release=lambda *_: self.choose())
        self.add_widget(self.b_choose)

        row = BoxLayout(spacing=dp(8), size_hint_y=None, height=dp(56))
        self.b_lay = []
        for text, n in (("2   4 COPIES", 4), ("8 COPIES", 8), ("FULL 6x4", 0)):
            b = RBtn("#0ea5e9", text=text, font_size=sp(15))
            b.bind(on_release=lambda _b, n=n: self.pick_layout(n))
            row.add_widget(b)
            self.b_lay.append(b)
        self.add_widget(row)

        row2 = BoxLayout(spacing=dp(8), size_hint_y=None, height=dp(60))
        self.b_print = RBtn("#16a34a", text="3   PRINT", font_size=sp(18))
        self.b_print.bind(on_release=lambda *_: self.go())
        self.b_save = RBtn("#f59e0b", text="SAVE / SHARE", font_size=sp(15))
        self.b_save.bind(on_release=lambda *_: self.save_share())
        row2.add_widget(self.b_print)
        row2.add_widget(self.b_save)
        self.add_widget(row2)

        self.enable_layouts(False)
        self.enable_print(False)
        self.say("Tap CHOOSE PHOTO to start", "info")

    # ---- small helpers (all UI changes must run on the Kivy thread) ----
    def post(self, fn):
        Clock.schedule_once(lambda dt: fn(), 0)

    def say(self, text, kind="info"):
        self.banner.set(text, kind)

    def enable_layouts(self, on):
        for b in self.b_lay:
            b.disabled = not on

    def enable_print(self, on):
        self.b_print.disabled = self.b_save.disabled = not on

    def show(self, pil_img):
        self.prev.texture = to_texture(pil_img)

    # ---- 1. choose a photo ----
    def choose(self):
        if self.busy:
            return
        A.pick_photo(os.path.join(self.data_dir, "picked"), self._picked)

    def _picked(self, path, err):                      # worker thread
        if err:
            E._log(f"pick: {err}")
            self.post(lambda: self.say(f"Could not open that photo: {err[:80]}", "bad"))
        elif path:
            self.post(lambda: self.select(path))

    def select(self, path):
        if self.busy:
            return
        self._drop_hi()
        self.sel, self.sheet = path, None
        self.busy = True
        self.enable_layouts(False)
        self.enable_print(False)
        self.say("Checking photo, please wait...", "busy")
        pw, ph = round(E.PH_W * PSCALE), round(E.PH_H * PSCALE)

        def work():
            try:
                full = E.open_photo(path)
                err, loc = None, None
                try:
                    loc = E.locate_face(full)
                    passport, found, low_res, bg_ok = E.passport_crop(full, loc, PSCALE)
                except Exception as e:
                    E._log(f"preview of {path}\n{traceback.format_exc()}")
                    err = str(e)
                    passport = E.enhance(ImageOps.fit(full, (pw, ph), Image.LANCZOS, centering=(0.5, 0.4)), PSCALE)
                    found = low_res = bg_ok = False
                pv = fit_preview(passport)
            except Exception as e:
                E._log(f"select {path}\n{traceback.format_exc()}")
                msg = str(e) or "Something went wrong with this photo"
                self.post(lambda: self._select_failed(path, msg))
                return
            self.post(lambda: self._select_done(path, full, loc, pv, found, low_res, bg_ok, err))

        threading.Thread(target=work, daemon=True).start()

    def _select_failed(self, path, msg):
        if self.sel == path:
            self.busy, self.sel = False, None
            self.say(msg, "bad")

    def _select_done(self, path, full, loc, pv, found, low_res, bg_ok, err):
        if self.sel != path:
            return
        self.full, self.loc = full, loc
        self.busy = False
        self.show(pv)
        if err:
            self.say(f"Could not fully process this photo ({err[:60]}). Centred crop shown", "warn")
        elif not found:
            self.say("No face found - showing a centred crop. Choose a layout", "warn")
        elif low_res:
            self.say("Face aligned, but the face is small in this shot, so the print may look soft. "
                     "A closer photo prints sharper", "warn")
        elif E.CHANGE_BG and not bg_ok:
            self.say("Face aligned, but the background could not be replaced cleanly (original kept). "
                     "Choose a layout", "warn")
        else:
            self.say("Face aligned, blue background applied. Now choose a layout", "ok")
        self.enable_layouts(True)
        self._start_hi(path, full, loc)

    def _start_hi(self, path, full, loc):
        """While you check the preview, build the full print-quality photo in the background."""
        job = {"cancel": False, "done": threading.Event(), "img": None}
        self.hi = job

        def work():
            with self.hi_lock:
                if not job["cancel"]:
                    try:
                        job["img"] = E.passport_crop(full, loc)[0]
                    except Exception:
                        E._log(f"print version of {path}\n{traceback.format_exc()}")
            job["done"].set()

        threading.Thread(target=work, daemon=True).start()

    def _drop_hi(self):
        if self.hi is not None:
            self.hi["cancel"] = True
            self.hi = None

    # ---- 2. layout ----
    def pick_layout(self, copies):
        if self.busy or not self.sel:
            self.say("Choose a photo first", "warn")
            return
        self.busy = True
        self.enable_layouts(False)
        self.enable_print(False)
        label = "full photo" if copies == 0 else f"{copies}-copy sheet"
        self.say(f"Building the {label} at full quality, please wait...", "busy")
        full, loc, job = self.full, self.loc, self.hi

        def work():
            try:
                if copies == 0:
                    sheet = E.make_full(full)
                else:
                    passport = None
                    if job is not None:
                        job["done"].wait()
                        passport = job["img"]
                    if passport is None:
                        passport = E.passport_crop(full, loc)[0]
                    sheet = E.make_sheet(passport, copies)
                pv = ImageOps.expand(fit_preview(sheet), border=2, fill=(156, 163, 175))
            except Exception as e:
                E._log(f"layout {copies}\n{traceback.format_exc()}")
                msg = f"Could not build the layout: {e}"
                self.post(lambda: self._layout_failed(msg))
                return
            self.post(lambda: self._layout_done(sheet, pv, label))

        threading.Thread(target=work, daemon=True).start()

    def _layout_failed(self, msg):
        self.busy = False
        self.say(msg, "bad")
        self.enable_layouts(True)

    def _layout_done(self, sheet, pv, label):
        self.busy = False
        self.sheet = sheet
        self.last_uri = None
        self.show(pv)
        self.say(f"Ready: {label}. Check the preview, then tap PRINT", "ok")
        self.enable_layouts(True)
        self.enable_print(True)

    # ---- 3. print / save ----
    def _write_sheet(self):
        path = os.path.join(self.out_dir, datetime.datetime.now().strftime("%Y%m%d_%H%M%S") + ".jpg")
        self.sheet.save(path, quality=96, subsampling=0, dpi=(E.DPI, E.DPI))
        return path

    def go(self):
        """PRINT: opens Android's print dialog with the finished 6x4 sheet."""
        if self.busy or self.sheet is None:
            return
        self.busy = True
        self.enable_print(False)
        self.enable_layouts(False)
        self.say("Preparing the print...", "busy")

        def work():
            try:
                path = self._write_sheet()
                A.print_image(path, lambda m: self.post(lambda: self._print_done(f"Print error: {m}", "bad")))
                self.post(lambda: self._print_done("Print dialog opened. Choose your printer and paper 4x6 in "
                                                   "(10x15 cm), glossy, then tap Print", "ok"))
            except Exception as e:
                E._log()
                self.post(lambda: self._print_done(f"Print error: {e}", "bad"))

        threading.Thread(target=work, daemon=True).start()

    def _print_done(self, msg, kind):
        self.say(msg, kind)
        self._drop_hi()
        self.busy, self.sel, self.sheet = False, None, None
        self.full = self.loc = None
        self.enable_layouts(False)
        self.enable_print(False)

    def save_share(self):
        """SAVE / SHARE: puts the sheet in Gallery (Pictures/StudioPrints) and opens the share sheet."""
        if self.busy or self.sheet is None:
            return
        self.busy = True
        self.say("Saving...", "busy")

        def work():
            try:
                path = self._write_sheet()
                uri = A.save_to_gallery(path, os.path.basename(path))
                self.post(lambda: self._saved(path, uri, None))
            except Exception as e:
                E._log()
                self.post(lambda: self._saved(None, None, str(e)))

        threading.Thread(target=work, daemon=True).start()

    def _saved(self, path, uri, err):
        self.busy = False
        if err:
            self.say(f"Could not save: {err}", "bad")
            return
        if uri is None:
            self.say(f"Saved: {path}", "ok")
        else:
            self.say("Saved to Gallery > StudioPrints. Choose where to send it", "ok")
            A.share(uri)


class StudioApp(App):
    title = "Deluxe Digital Studio"

    def build(self):
        return Studio(self.user_data_dir)


if __name__ == "__main__":
    StudioApp().run()
