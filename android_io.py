"""Android glue: photo picker, save-to-gallery, share sheet and system printing.
Everything here is a no-op / raises on a PC, so main.py can still be tried on a desktop."""
import os, shutil, threading, datetime
from kivy.utils import platform

IS_ANDROID = platform == "android"
_REQ_PICK = 4711

if IS_ANDROID:
    from jnius import autoclass, cast
    from android import activity
    from android.runnable import run_on_ui_thread

    PythonActivity = autoclass("org.kivy.android.PythonActivity")
    Intent = autoclass("android.content.Intent")

    def _act():
        return PythonActivity.mActivity

    def _resolver():
        return _act().getContentResolver()


# ---------------------------------------------------------------- picking a photo
_pick_cb = None
_cache_dir = None


def _copy_uri(uri, dest):
    """Copy a content:// photo into the app's own folder (works for every gallery / cloud picker)."""
    pfd = _resolver().openFileDescriptor(uri, "r")
    fd = pfd.detachFd()
    with os.fdopen(fd, "rb") as src, open(dest, "wb") as dst:
        shutil.copyfileobj(src, dst, 1 << 20)


def _decode_with_android(uri, dest):
    """Fallback for formats Pillow cannot read (e.g. .heic): let Android decode and re-save as JPEG."""
    ImageDecoder = autoclass("android.graphics.ImageDecoder")
    Config = autoclass("android.graphics.Bitmap$Config")
    Fmt = autoclass("android.graphics.Bitmap$CompressFormat")
    FOS = autoclass("java.io.FileOutputStream")
    bmp = ImageDecoder.decodeBitmap(ImageDecoder.createSource(_resolver(), uri))
    bmp = bmp.copy(Config.ARGB_8888, False)
    fos = FOS(dest)
    bmp.compress(Fmt.JPEG, 96, fos)
    fos.close()


def _readable(path):
    try:
        from PIL import Image
        with Image.open(path) as im:
            im.verify()
        return True
    except Exception:
        return False


def _on_result(request, result, intent):
    global _pick_cb
    if request != _REQ_PICK:
        return
    cb, _pick_cb = _pick_cb, None
    if cb is None:
        return
    if result != -1 or intent is None or intent.getData() is None:      # -1 = RESULT_OK
        cb(None, None)
        return
    uri = intent.getData()

    def work():
        try:
            dest = os.path.join(_cache_dir, datetime.datetime.now().strftime("pick_%H%M%S_%f") + ".jpg")
            _copy_uri(uri, dest)
            if not _readable(dest):
                _decode_with_android(uri, dest)
            cb(dest, None)
        except Exception as e:
            cb(None, str(e))

    threading.Thread(target=work, daemon=True).start()


def pick_photo(cache_dir, callback):
    """Open the system photo picker. callback(path_or_None, error_or_None) - called from a worker thread."""
    global _pick_cb, _cache_dir
    os.makedirs(cache_dir, exist_ok=True)
    if not IS_ANDROID:
        try:                                    # desktop testing only
            import tkinter as tk
            from tkinter import filedialog
            r = tk.Tk(); r.withdraw()
            p = filedialog.askopenfilename(filetypes=[("Photos", "*.jpg *.jpeg *.png *.webp *.bmp *.tif *.tiff")])
            r.destroy()
            callback(p or None, None)
        except Exception as e:
            callback(None, str(e))
        return
    _cache_dir, _pick_cb = cache_dir, callback
    activity.unbind(on_activity_result=_on_result)
    activity.bind(on_activity_result=_on_result)
    it = Intent(Intent.ACTION_GET_CONTENT)
    it.setType("image/*")
    it.addCategory(Intent.CATEGORY_OPENABLE)
    _act().startActivityForResult(Intent.createChooser(it, "Choose a photo"), _REQ_PICK)


# ---------------------------------------------------------------- gallery + share
def save_to_gallery(jpg_path, name):
    """Copy the sheet into Pictures/StudioPrints (shows up in Gallery / Google Photos). Returns a content Uri."""
    if not IS_ANDROID:
        return None
    ContentValues = autoclass("android.content.ContentValues")
    Media = autoclass("android.provider.MediaStore$Images$Media")
    cv = ContentValues()
    cv.put("_display_name", name)
    cv.put("mime_type", "image/jpeg")
    cv.put("relative_path", "Pictures/StudioPrints")
    cv.put("is_pending", 1)
    uri = _resolver().insert(Media.EXTERNAL_CONTENT_URI, cv)
    if uri is None:
        raise RuntimeError("Could not create the gallery file")
    pfd = _resolver().openFileDescriptor(uri, "w")
    fd = pfd.detachFd()
    with open(jpg_path, "rb") as src, os.fdopen(fd, "wb") as dst:
        shutil.copyfileobj(src, dst, 1 << 20)
    cv2 = ContentValues()
    cv2.put("is_pending", 0)
    _resolver().update(uri, cv2, None, None)
    return uri


def share(uri):
    """Android share sheet: pick Epson iPrint / Smart Panel / WhatsApp / Drive ..."""
    if not IS_ANDROID or uri is None:
        return
    it = Intent(Intent.ACTION_SEND)
    it.setType("image/jpeg")
    it.putExtra(Intent.EXTRA_STREAM, cast("android.os.Parcelable", uri))
    it.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
    _act().startActivity(Intent.createChooser(it, "Send / print sheet"))


# ---------------------------------------------------------------- system print dialog
if IS_ANDROID:
    @run_on_ui_thread
    def _print_ui(jpg_path, on_error):
        try:
            PrintHelper = autoclass("androidx.print.PrintHelper")
            BitmapFactory = autoclass("android.graphics.BitmapFactory")
            bmp = BitmapFactory.decodeFile(jpg_path)
            ph = PrintHelper(_act())
            ph.setScaleMode(PrintHelper.SCALE_MODE_FIT)
            ph.setColorMode(PrintHelper.COLOR_MODE_COLOR)
            ph.setOrientation(PrintHelper.ORIENTATION_LANDSCAPE)
            ph.printBitmap("Deluxe Studio print", bmp)
        except Exception as e:
            on_error(str(e))


def print_image(jpg_path, on_error):
    """Open Android's print dialog (choose printer, paper 4x6 in, glossy, quality ...)."""
    if not IS_ANDROID:
        raise RuntimeError("Printing is only available on the phone")
    _print_ui(jpg_path, on_error)
