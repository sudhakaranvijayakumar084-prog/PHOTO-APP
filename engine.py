"""Deluxe Digital Studio - image engine (ported from the Windows studio.py).
Same face detection, passport crop, blue background, enhancement and sheet layouts.
Windows-only parts (Tkinter window, win32 printing, drive scanning) were removed;
the Android screen is in main.py.
"""
import os, io, datetime, traceback, time, functools, base64, json
import numpy as np, cv2
from PIL import Image, ImageOps, ImageFilter, ImageDraw
try:
    from PIL import ImageCms
except Exception:                    # colour-management module missing: photos are used as they are
    ImageCms = None

Image.MAX_IMAGE_PIXELS = None
HERE = os.path.dirname(os.path.abspath(__file__))
SAVE_DIR = os.path.join(HERE, "out")          # main.py points this at the app's private folder
LOG_FILE = os.path.join(SAVE_DIR, "studio_errors.log")
MAX_SIDE = 4096                    # phone photos (up to 100 MP) are reduced to this long side: saves memory
DPI = 600                          # sheet resolution (phone-friendly; the PC version used 1200)
FULL_DPI = 600
def mm(v): return round(v / 25.4 * DPI)
PH_W, PH_H = mm(35), mm(45)        # 3.5 x 4.5 cm photo, at DPI resolution
SH_W, SH_H = mm(152.4), mm(101.6)  # 6 x 4 inch print sheet, at DPI resolution

# ---- look of the photo (all easy to tweak) ----
BG_COLOR = (30, 144, 255)
CHANGE_BG = True
EXPOSURE = 1.03
SHADOW_LIFT = 0.10
AUTO_EXPOSURE = True
CONTRAST = 1.0
CLARITY = 0.18
COLOR = 1.04
SHARPEN = True
SHARP_AMOUNT = 0.8
DENOISE = True
CROP_H_FACTOR = 3.25
CROP_FACE_Y = 0.42
BORDER = mm(0.4)


def _cascade(name):
    """Load a Haar cascade: first from the bundled 'cascades' folder, else from OpenCV's own data."""
    dirs = [os.path.join(HERE, "cascades")]
    data = getattr(cv2, "data", None)
    if data is not None:
        dirs.append(data.haarcascades)
    for d in dirs:
        p = os.path.join(d, name)
        if os.path.isfile(p):
            c = cv2.CascadeClassifier(p)
            if not c.empty():
                return c
    raise RuntimeError(f"face detector file missing: {name}")


FACE = _cascade("haarcascade_frontalface_default.xml")
EYE = _cascade("haarcascade_eye.xml")
FACE2 = _cascade("haarcascade_frontalface_alt2.xml")


def _log(text=None):
    """Append an error to the log file (never raises)."""
    try:
        os.makedirs(SAVE_DIR, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"\n[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}]\n{text or traceback.format_exc()}")
    except Exception:
        pass



def _to_srgb(im):
    """Convert the photo's embedded colour profile (Display P3, AdobeRGB, CMYK...) to sRGB, so colours
    and skin tones print the way they look on the camera."""
    global _SRGB
    if im.mode in ("RGBA", "LA", "PA"):
        im = im.convert("RGBA")
        bg = Image.new("RGB", im.size, "white")
        bg.paste(im, mask=im.getchannel("A"))
        return bg
    icc = im.info.get("icc_profile")
    if icc and ImageCms is not None and im.mode in ("RGB", "CMYK"):
        try:
            src = ImageCms.ImageCmsProfile(io.BytesIO(icc))
            if "srgb" not in ImageCms.getProfileDescription(src).lower() or im.mode == "CMYK":
                if _SRGB is None:
                    _SRGB = ImageCms.createProfile("sRGB")
                return ImageCms.profileToProfile(im, src, _SRGB, renderingIntent=1, outputMode="RGB")
        except Exception:
            pass
    return im.convert("RGB")


def open_photo(path):
    """Read a photo (rotated upright, sRGB). A photo that is still being written, or a card that is slow,
    is retried a few times before giving up - and the file is always closed again (safe to eject the card)."""
    last = None
    for attempt in range(4):
        try:
            with Image.open(path) as im:
                im.load()
                im = _to_srgb(ImageOps.exif_transpose(im))
                if max(im.size) > MAX_SIDE:
                    im.thumbnail((MAX_SIDE, MAX_SIDE), Image.BILINEAR, reducing_gap=2.0)
                return im
        except Exception as e:
            last = e
            if isinstance(e, FileNotFoundError):
                break
            time.sleep(0.7)
    name = os.path.basename(path)
    if isinstance(last, FileNotFoundError):
        raise RuntimeError(f"{name} is no longer there (card removed?)")
    raise RuntimeError(f"{name} cannot be read ({last}). It may still be saving - wait a moment and tap it again")


# ======================================================================================
#  Image quality: develop (tone, clarity, sharpness)
# ======================================================================================
def _luma(a):
    return a[..., 0] * 0.299 + a[..., 1] * 0.587 + a[..., 2] * 0.114


def _noise_sigma(gray):
    """Estimate the noise level of a photo (grey levels). Robust against edges (median based)."""
    k = np.array([[1, -2, 1], [-2, 4, -2], [1, -2, 1]], np.float32)
    c = cv2.filter2D(gray.astype(np.float32), -1, k)[2:-2, 2:-2]
    return float(np.median(np.abs(c)) / 4.047) if c.size else 0.0


def _denoise(img):
    """Luminance noise reduction + chroma smoothing, only used when a photo is genuinely noisy."""
    arr = np.asarray(img)
    ycc = cv2.cvtColor(arr, cv2.COLOR_RGB2YCrCb)
    sig = _noise_sigma(ycc[..., 0])
    if sig < 2.2:
        return img
    h = float(np.clip(sig * 1.2, 2.5, 8.0))
    ycc[..., 0] = cv2.fastNlMeansDenoising(ycc[..., 0], None, h, 7, 15)
    u = max(0.6, img.width / PH_W)
    ycc[..., 1] = cv2.GaussianBlur(ycc[..., 1], (0, 0), 1.2 * u)
    ycc[..., 2] = cv2.GaussianBlur(ycc[..., 2], (0, 0), 1.2 * u)
    return Image.fromarray(cv2.cvtColor(ycc, cv2.COLOR_YCrCb2RGB))


def _soft(d, t):
    """Noise guard for sharpening: tiny differences (noise) are left alone, real edges get the full boost."""
    ad = np.abs(d)
    return d * ad / (ad + t)


def enhance(img, scale=1.0, face=None, low_res=False):
    """Professional 'studio' finish, done in floating point so there is no banding:
      1 noise clean-up (only if the photo is noisy)   2 tiny white-balance fix
      3 exposure rescue (only if the face is too dark / too bright)   4 gentle film contrast curve
      5 clarity (local contrast, gives the face depth)   6 colour   7 print sharpening - extra on the face,
      limited to the neighbourhood of each pixel so it can never create halos.
    face = (cx, cy, face_height) in img pixels, or None."""
    if DENOISE:
        img = _denoise(img)
    a = np.asarray(img, dtype=np.float32)
    H, W = a.shape[:2]
    u = max(0.45, min(1.0, W / PH_W))                  # sharpening radius follows the picture size

    # 2) white balance (gentle)
    s = a[::max(1, H // 256), ::max(1, W // 256)]
    m = s.reshape(-1, 3).mean(0)
    a *= np.clip(m.mean() / np.maximum(m, 1e-3), 0.96, 1.04)

    # 3) exposure: only rescue a face that is clearly under/over exposed (skin tones are never "normalised")
    if AUTO_EXPOSURE:
        y = _luma(a) / 255.0
        if face is not None:
            fx, fy, fh = face
            x0, x1 = int(max(0, fx - 0.25 * fh)), int(min(W, fx + 0.25 * fh))
            y0, y1 = int(max(0, fy - 0.15 * fh)), int(min(H, fy + 0.30 * fh))
            reg = y[y0:y1, x0:x1]
            lo, hi = 0.47, 0.72
        else:
            reg, lo, hi = y[::4, ::4], 0.45, 0.62
        if reg.size > 50:
            med = float(np.clip(np.median(reg), 0.05, 0.95))
            tgt = min(max(med, lo), hi)
            g = float(np.clip(np.log(tgt) / np.log(med), 0.66, 1.3))
            if abs(g - 1) > 0.02:
                a = 255.0 * np.power(np.clip(a, 0, 255) / 255.0, g)
        del y
    if EXPOSURE != 1.0:
        a *= EXPOSURE
    a = np.clip(a, 0, 255)

    # 3b) open up the shadows a little (dark clothes / dark hair keep their depth, dim photos get lighter)
    if SHADOW_LIFT > 0:
        y = _luma(a) / 255.0
        y2 = y + SHADOW_LIFT * np.sqrt(y) * (1 - y) ** 2
        a *= ((y2 + 1e-3) / (y + 1e-3))[..., None]
        a = np.clip(a, 0, 255)

    # 4) gentle S-curve on brightness only (colours keep their hue)
    if CONTRAST > 0:
        y = _luma(a) / 255.0
        y2 = y - 0.14 * CONTRAST * np.sin(2 * np.pi * y) / (2 * np.pi)
        a *= ((y2 + 1e-3) / (y + 1e-3))[..., None]
        a = np.clip(a, 0, 255)

    # 5) clarity: local contrast at a large radius, strongest in the mid-tones
    if CLARITY > 0:
        y = _luma(a) / 255.0
        sm = cv2.resize(y, (max(8, W // 4), max(8, H // 4)), interpolation=cv2.INTER_AREA)
        sm = cv2.GaussianBlur(sm, (0, 0), 0.025 * W / 4)
        yb = cv2.resize(sm, (W, H), interpolation=cv2.INTER_LINEAR)
        y3 = y + CLARITY * (y - yb) * (4 * y * (1 - y))
        a *= ((y3 + 1e-3) / (y + 1e-3))[..., None]
        a = np.clip(a, 0, 255)

    # 6) colour
    if COLOR != 1.0:
        g = _luma(a)[..., None]
        a = np.clip(g + COLOR * (a - g), 0, 255)

    # 7) print sharpening on brightness only
    if SHARPEN:
        y = _luma(a)
        amt = SHARP_AMOUNT * (1.3 if low_res else 1.0)
        if face is not None:
            fx, fy, fh = face
            sx, sy = max(1.0, 0.9 * fh), max(1.0, 1.1 * fh)
            wx = np.exp(-0.5 * ((np.arange(W) - fx) / sx) ** 2)
            wy = np.exp(-0.5 * ((np.arange(H) - fy) / sy) ** 2)
            wgt = (0.6 + 0.4 * np.outer(wy, wx)).astype(np.float32)      # face region gets full strength
        else:
            wgt = 1.0
        d1 = y - cv2.GaussianBlur(y, (0, 0), 1.1 * u + 0.2)              # medium detail (eyelashes, hair, edges)
        d2 = y - cv2.GaussianBlur(y, (0, 0), 0.55 * u + 0.15)            # fine detail
        ys = y + amt * wgt * _soft(d1, 2.0) + 0.35 * amt * wgt * _soft(d2, 1.5)
        k3 = np.ones((3, 3), np.uint8)
        ys = np.clip(ys, cv2.erode(y, k3) - 8, cv2.dilate(y, k3) + 8)    # no halos / ringing
        a = np.clip(a + (ys - y)[..., None], 0, 255)
    return Image.fromarray((a + 0.5).astype(np.uint8))


# ======================================================================================
#  Background replacement
# ======================================================================================
def guided_filter(I, p, r, eps):
    """Colour guided filter (He et al.): makes the soft mask p snap onto the real edges of the photo I
    (hair strands, shoulders). I = HxWx3 float 0..1, p = HxW float 0..1."""
    def box(x):
        return cv2.boxFilter(x, -1, (2 * r + 1, 2 * r + 1), borderType=cv2.BORDER_REFLECT)
    ch = [I[..., c] for c in range(3)]
    mI = [box(c) for c in ch]
    mp = box(p)
    cov = [box(ch[c] * p) - mI[c] * mp for c in range(3)]
    v = {}
    for i in range(3):
        for j in range(i, 3):
            v[i, j] = box(ch[i] * ch[j]) - mI[i] * mI[j]
    s00, s01, s02 = v[0, 0] + eps, v[0, 1], v[0, 2]
    s11, s12, s22 = v[1, 1] + eps, v[1, 2], v[2, 2] + eps
    c00, c01, c02 = s11 * s22 - s12 * s12, s02 * s12 - s01 * s22, s01 * s12 - s02 * s11
    c11, c12, c22 = s00 * s22 - s02 * s02, s01 * s02 - s00 * s12, s00 * s11 - s01 * s01
    det = s00 * c00 + s01 * c01 + s02 * c02
    det = np.where(np.abs(det) < 1e-12, 1e-12, det)
    a0 = (cov[0] * c00 + cov[1] * c01 + cov[2] * c02) / det
    a1 = (cov[0] * c01 + cov[1] * c11 + cov[2] * c12) / det
    a2 = (cov[0] * c02 + cov[1] * c12 + cov[2] * c22) / det
    b = mp - a0 * mI[0] - a1 * mI[1] - a2 * mI[2]
    return box(a0) * ch[0] + box(a1) * ch[1] + box(a2) * ch[2] + box(b)


def blue_background(img, face):
    """Replace the studio backdrop with plain BG_COLOR.
    The backdrop colour is learned from the photo's own edges (so uneven lighting,
    vignetting and shadows on the backdrop are handled), GrabCut finds the outline, and a guided filter
    then snaps that outline onto the real edge in the full-size photo (clean hair, no cut-out look).
    img = final-size photo, face = (cx, cy, face_height) in img pixels. Returns (image, ok)."""
    sw = 512
    sh = round(sw * img.height / img.width)
    k = sw / img.width
    small = cv2.cvtColor(np.asarray(img.resize((sw, sh), Image.LANCZOS)), cv2.COLOR_RGB2BGR)
    lab = cv2.cvtColor(small, cv2.COLOR_BGR2LAB).astype(np.float32)
    cx, cy, fh = face[0] * k, face[1] * k, face[2] * k
    yy, xx = np.mgrid[0:sh, 0:sw]

    # where the person certainly / probably is
    head = ((xx - cx) / (0.9 * fh)) ** 2 + ((yy - (cy - 0.2 * fh)) / (1.2 * fh)) ** 2 < 1
    zone = head.astype(np.uint8)
    body = np.array([[cx - 1.7 * fh, sh], [cx + 1.7 * fh, sh], [cx + 0.95 * fh, cy + 0.55 * fh],
                     [cx - 0.95 * fh, cy + 0.55 * fh]], np.int32)
    cv2.fillPoly(zone, [body], 1)
    fgseed = np.zeros((sh, sw), np.uint8)
    cv2.ellipse(fgseed, (int(cx), int(cy + 0.05 * fh)), (int(0.32 * fh), int(0.5 * fh)), 0, 0, 360, 1, -1)
    cv2.rectangle(fgseed, (int(cx - 0.4 * fh), int(cy + 0.75 * fh)), (int(cx + 0.4 * fh), sh), 1, -1)

    # 1) backdrop colour model: samples from the left / right / top margins of the upper photo
    samp = np.zeros((sh, sw), bool)
    mx, my = int(0.10 * sw), int(0.06 * sh)
    samp[:int(0.6 * sh), :mx] = True
    samp[:int(0.6 * sh), sw - mx:] = True
    samp[:my, :] = True
    samp &= ~head
    pts = lab[samp]
    if len(pts) < 300:
        return img, False
    K = 4
    _, lbl, cen = cv2.kmeans(pts, K, None, (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 20, 1.0),
                             3, cv2.KMEANS_PP_CENTERS)
    lbl = lbl.ravel()
    wts = np.array([0.5, 1.0, 1.0], np.float32)       # lighting changes (L) count less than colour (a, b)
    dmin = np.full((sh, sw), 1e9, np.float32)
    for i in range(K):
        mem = pts[lbl == i]
        if len(mem) < 0.08 * len(pts):                # ignore stray clusters (hair, shoulder at the margin)
            continue
        di = np.sqrt((((mem - cen[i]) * wts) ** 2).sum(1))
        thr = max(9.0, float(di.mean() + 3 * di.std()))
        dmin = np.minimum(dmin, np.sqrt((((lab - cen[i]) * wts) ** 2).sum(2)) / thr)
    cand = (dmin < 1.0).astype(np.uint8)
    cand = cv2.morphologyEx(cand, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))

    # 2) sure backdrop = backdrop-coloured areas outside the person zone that reach the picture edge
    outside = cand.copy()
    outside[zone > 0] = 0
    n, cc = cv2.connectedComponents(outside)
    edge = set(np.unique(np.concatenate([cc[0, :], cc[:, 0], cc[:, -1], cc[-1, :]]))) - {0}
    sure_bg = np.isin(cc, list(edge)).astype(np.uint8)
    if sure_bg.mean() < 0.08:
        return img, False
    sure_bg_in = cv2.erode(sure_bg, np.ones((5, 5), np.uint8))

    # 3) GrabCut starting from those seeds
    m = np.full((sh, sw), cv2.GC_PR_FGD, np.uint8)
    m[cand > 0] = cv2.GC_PR_BGD
    m[sure_bg_in > 0] = cv2.GC_BGD
    m[fgseed > 0] = cv2.GC_FGD
    dark = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)[..., 2] < 70
    m[head & dark & (cand == 0)] = cv2.GC_FGD         # dark hair
    try:
        bgd, fgd = np.zeros((1, 65), np.float64), np.zeros((1, 65), np.float64)
        cv2.grabCut(small, m, None, bgd, fgd, 4, cv2.GC_INIT_WITH_MASK)
    except cv2.error:
        return img, False
    fg = ((m == cv2.GC_FGD) | (m == cv2.GC_PR_FGD)).astype(np.uint8)

    # 4) tidy: keep the blob with the face, fill holes, smooth the outline
    n, lab_cc = cv2.connectedComponents(fg)
    lbl0 = lab_cc[min(max(int(cy), 0), sh - 1), min(max(int(cx), 0), sw - 1)]
    if lbl0 == 0:
        return img, False
    fg = (lab_cc == lbl0).astype(np.uint8)
    cnts, _ = cv2.findContours(fg, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    fg = np.zeros_like(fg)
    cv2.drawContours(fg, cnts, -1, 1, -1)
    ker = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    fg = cv2.morphologyEx(fg, cv2.MORPH_OPEN, ker)
    fg = cv2.morphologyEx(fg, cv2.MORPH_CLOSE, ker)
    # neat hair trim: remove stray wisps / specks and smooth the jagged outline into one clean curve
    big = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))
    fg = cv2.morphologyEx(fg, cv2.MORPH_OPEN, big)
    fg = cv2.morphologyEx(fg, cv2.MORPH_CLOSE, big)
    fg = (cv2.GaussianBlur(fg.astype("float32"), (0, 0), 2.2) > 0.5).astype(np.uint8)
    n, lab_cc = cv2.connectedComponents(fg)                 # smoothing may split off a speck: keep the person only
    lbl0 = lab_cc[min(max(int(cy), 0), sh - 1), min(max(int(cx), 0), sw - 1)]
    if lbl0 == 0:
        return img, False
    fg = (lab_cc == lbl0).astype(np.uint8)

    # 5) sanity checks: if the cut-out looks wrong, keep the original instead of a half-changed photo
    c = max(4, int(0.06 * sw))
    corners = np.concatenate([fg[:c, :c].ravel(), fg[:c, -c:].ravel()])
    leak = (fg & sure_bg_in).sum() / max(1, sure_bg_in.sum())
    if not (0.12 < fg.mean() < 0.9) or corners.mean() > 0.2 or leak > 0.1:
        return img, False

    # 6) full-size matte: snap the outline onto the real edge of the photo
    fg = cv2.erode(fg, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)))
    W, H = img.size
    a = np.asarray(img).astype("float32")
    hard = cv2.resize(fg.astype("float32"), (W, H), interpolation=cv2.INTER_LINEAR)
    hard = cv2.GaussianBlur(hard, (0, 0), max(0.8, 0.0025 * W))
    r = max(2, int(round(0.005 * W)))
    refined = guided_filter(a / 255.0, hard, r, 1.5e-3)    # larger eps = smoother, tidier edge (no frizz)
    refined = cv2.GaussianBlur(refined, (0, 0), max(0.6, 0.0012 * W))
    bw = max(5, int(0.014 * W))
    band = cv2.dilate(((hard > 0.02) & (hard < 0.98)).astype(np.uint8), np.ones((bw, bw), np.uint8))
    alpha = np.where(band > 0, refined, (hard >= 0.5).astype("float32")).astype("float32")
    alpha = np.clip((alpha - 0.6) * 2.2 + 0.5, 0, 1)          # tighter, crisper edge = no old-backdrop fringe
    del refined, hard, band

    # edge colours: rebuild them from the solid inside of the person so no old-backdrop tint bleeds in
    q = 4
    size = (max(8, W // q), max(8, H // q))
    kk = max(3, int(0.006 * W) | 1)
    core = cv2.erode((alpha > 0.98).astype("uint8"), np.ones((kk, kk), np.uint8)).astype("float32")
    core_s = (cv2.resize(core, size, interpolation=cv2.INTER_AREA) > 0.99).astype("float32")
    sm = cv2.resize(a, size, interpolation=cv2.INTER_AREA)
    sig = max(0.6, 3.0 * W / PH_W)
    num = cv2.GaussianBlur(sm * core_s[..., None], (0, 0), sig)
    den = cv2.GaussianBlur(core_s, (0, 0), sig)[..., None]
    fgc = cv2.resize(num / np.maximum(den, 1e-3), (W, H), interpolation=cv2.INTER_CUBIC)
    alpha_f = alpha[..., None]
    t = np.clip((alpha_f - 0.88) / 0.12, 0, 1)             # edge pixels take the person's own colour
    solid = a * t + fgc * (1 - t)
    out = solid * alpha_f + np.array(BG_COLOR, "float32") * (1 - alpha_f)
    return Image.fromarray(out.clip(0, 255).astype("uint8")), True


# ======================================================================================
#  Face finding and passport crop
# ======================================================================================
MIN_CROP_W = round(35 / 25.4 * 300)  # = 413 px (3.5 cm at 300 dpi): below this the face crop is too small for a sharp print.


def _find_face(gray, rgb=None):
    """Pick the real face in a small grayscale photo. Several detectors vote; a box needs
    eyes in the right place and a plausible position/size, so things like a checked shirt
    (which fools a single detector) lose against the real face. -> ((x, y, w, h), eyes) or None"""
    H, W = gray.shape
    g = cv2.equalizeHist(gray)
    ms = max(40, int(0.09 * H))
    skin = None
    if rgb is not None:                      # skin-coloured pixels (YCrCb range)
        ycc = cv2.cvtColor(rgb, cv2.COLOR_RGB2YCrCb)
        skin = (ycc[..., 1] >= 133) & (ycc[..., 1] <= 173) & (ycc[..., 2] >= 77) & (ycc[..., 2] <= 127)
    boxes = []
    for casc, nb, wgt in ((FACE, 6, 1.0), (FACE2, 4, 1.0), (FACE, 4, 0.8), (FACE2, 3, 0.7)):
        for b in casc.detectMultiScale(g, 1.1, nb, minSize=(ms, ms)):
            boxes.append((tuple(int(v) for v in b), wgt))
    best, best_s = None, 0.0
    for (x, y, w, h), wgt in boxes:
        cxn, cyn, hn = (x + w / 2) / W, (y + h / 2) / H, h / H
        if cyn > 0.72 or hn > 0.75:
            continue
        eyes = EYE.detectMultiScale(g[y:y + int(h * 0.62), x:x + w], 1.1, 4, minSize=(max(8, w // 9),) * 2)
        eyes = [e for e in eyes if e[1] + e[3] / 2 < h * 0.55]
        if not eyes and skin is not None:    # no eyes AND not skin-coloured = a shirt, a poster, a curtain...
            patch = skin[y + int(0.2 * h):y + int(0.8 * h), x + int(0.2 * w):x + int(0.8 * w)]
            if patch.size and patch.mean() < 0.25:
                continue
        votes = sum(1 for (x2, y2, w2, h2), _ in boxes
                    if abs((x2 + w2 / 2) - (x + w / 2)) < 0.3 * w and abs((y2 + h2 / 2) - (y + h / 2)) < 0.3 * h)
        prior = np.exp(-((cxn - 0.5) / 0.28) ** 2) * np.exp(-((cyn - 0.38) / 0.25) ** 2)
        sc = hn * prior * wgt * (1 + min(len(eyes), 2)) * (1 + 0.25 * votes)
        if best is None or sc > best_s:
            best, best_s = ((x, y, w, h), eyes), sc
    return best


def _grab(img, x0, y0, x1, y1):
    """Crop a box out of the photo as an RGB array. Any part outside the photo is filled by
    mirroring the picture (no stretched streaks, no white/black bars)."""
    a = np.asarray(img)
    H, W = a.shape[:2]
    cx0, cy0, cx1, cy1 = max(x0, 0), max(y0, 0), min(x1, W), min(y1, H)
    if cx1 <= cx0 or cy1 <= cy0:
        return np.full((y1 - y0, x1 - x0, 3), 255, np.uint8)
    return cv2.copyMakeBorder(a[cy0:cy1, cx0:cx1], cy0 - y0, y1 - cy1, cx0 - x0, x1 - cx1, cv2.BORDER_REFLECT_101)


def locate_face(img):
    """Run the face detectors once, on a small copy of the photo.
    Returns ((x, y, w, h), eyes, scale) in small-copy pixels, or None if there is no face."""
    s = min(1.0, 900 / max(img.size))
    small = img.resize((max(1, int(img.width * s)), max(1, int(img.height * s))), Image.BILINEAR, reducing_gap=2.0)
    found = _find_face(np.asarray(small.convert("L")), np.asarray(small))
    return None if found is None else (found[0], found[1], s)


def passport_crop(img, loc="auto", scale=1.0):
    """Find the face, level the eyes, crop 3.5x4.5 cm (head + shoulders), plain blue background.
    loc   = result of locate_face() (pass it in so the face is only searched once; None = no face).
    scale = 1.0 for the print version, smaller for the quick on-screen preview.
    Returns (image, face_found, low_res, bg_ok)."""
    if isinstance(loc, str):
        loc = locate_face(img)
    pw, ph = round(PH_W * scale), round(PH_H * scale)
    if loc is None:
        return enhance(ImageOps.fit(img, (pw, ph), Image.LANCZOS, centering=(0.5, 0.4)), scale), False, False, False
    (x, y, w, h), eyes, s = loc
    cx, cy, fh = (x + w / 2) / s, (y + h / 2) / s, h / s
    ang = 0.0
    eyes = sorted(eyes, key=lambda e: -e[2])[:2]
    if len(eyes) == 2:
        (x1, y1, w1, h1), (x2, y2, w2, h2) = sorted(eyes, key=lambda e: e[0])
        a = float(np.degrees(np.arctan2((y2 + h2 / 2) - (y1 + h1 / 2), (x2 + w2 / 2) - (x1 + w1 / 2))))
        if 0.7 < abs(a) < 12:
            ang = a

    ch = fh * CROP_H_FACTOR
    cw = ch * PH_W / PH_H
    if cw > img.width:                    # shoulders wider than the photo: zoom in a little (max 15%)
        f = max(0.85, img.width / cw)
        ch, cw = ch * f, cw * f
    top, left = cy - CROP_FACE_Y * ch, cx - cw / 2
    head_top = cy - 0.95 * fh
    # slide the crop back inside the photo when there is room, so no edge has to be invented
    if top + ch > img.height:
        top -= min(top + ch - img.height, max(0.0, head_top - 0.04 * ch - top))
    elif top < 0:
        top += min(-top, max(0.0, img.height - (top + ch)), max(0.0, head_top - 0.04 * ch - top))
    if cw <= img.width:
        left = min(max(left, 0.0), img.width - cw)
    bw, bh = max(8, int(round(cw))), max(8, int(round(ch)))
    x0, y0 = int(round(left)), int(round(top))

    pad = int(0.2 * ch) if ang else 0
    arr = _grab(img, x0 - pad, y0 - pad, x0 + bw + pad, y0 + bh + pad)
    ds = min(1.0, 2.0 * pw / bw) if (ang and scale < 1) else 1.0   # preview: shrink first, then level (4x faster)
    if ds < 0.8:
        arr = cv2.resize(arr, (max(1, round(arr.shape[1] * ds)), max(1, round(arr.shape[0] * ds))),
                         interpolation=cv2.INTER_AREA)
    else:
        ds = 1.0
    if ang:
        M = cv2.getRotationMatrix2D(((cx - (x0 - pad)) * ds, (cy - (y0 - pad)) * ds), ang, 1.0)
        arr = cv2.warpAffine(arr, M, (arr.shape[1], arr.shape[0]), flags=cv2.INTER_LANCZOS4,
                             borderMode=cv2.BORDER_REFLECT_101)
    p2 = round(pad * ds)
    arr = arr[p2:p2 + round(bh * ds), p2:p2 + round(bw * ds)]
    low_res = bw < MIN_CROP_W    # the source didn't have enough detail for a sharp print
    result = Image.fromarray(arr).resize((pw, ph), Image.LANCZOS)
    face_out = ((cx - x0) / bw * pw, (cy - y0) / bh * ph, fh / bh * ph)
    result = enhance(result, scale, face_out, low_res)
    bg_ok = False
    if CHANGE_BG:
        result, bg_ok = blue_background(result, face_out)
    return result, True, low_res, bg_ok


# ======================================================================================
#  Sheets
# ======================================================================================
# Every sheet is 6x4 inch, LANDSCAPE, with the photos standing upright - exactly what you see in the preview.
# (When it is printed, the sheet is turned 90 degrees to feed the portrait 4x6 paper; the photos are cut out
#  one by one, so that does not matter.)  3.5 x 4.5 cm photos, a thin black border drawn inside each photo's edge:
#   4 copies = 4 across, one row
#   8 copies = 4 across, 2 rows - the largest photos that still fit on the sheet with ~2 mm cutting gaps
#              (about 3 mm of white at the left/right paper edges, 5 mm at top/bottom)
# copies: (columns, rows, gap between columns in mm, gap between rows in mm)
SHEETS = {4: (4, 1, 2, 2), 8: (4, 2, 2, 2)}
# Prefer 4 copies as a 2 x 2 block? Use  4: (2, 2, 4, 4)


def make_sheet(photo, copies):
    """Lay out the copies on the 6x4 inch sheet, centred, each framed by a thin black border."""
    cols, rows, cgap_mm, rgap_mm = SHEETS[copies]
    sheet = Image.new("RGB", (SH_W, SH_H), "white")
    cgap, rgap = mm(cgap_mm), mm(rgap_mm)
    cell_w, cell_h = photo.width, photo.height
    x0 = (SH_W - (cols * cell_w + (cols - 1) * cgap)) // 2
    y0 = (SH_H - (rows * cell_h + (rows - 1) * rgap)) // 2
    d = ImageDraw.Draw(sheet)
    for r in range(rows):
        for c in range(cols):
            x, y = x0 + c * (cell_w + cgap), y0 + r * (cell_h + rgap)
            sheet.paste(photo, (x, y))
            d.rectangle((x, y, x + cell_w - 1, y + cell_h - 1), outline=(0, 0, 0), width=BORDER)
    return sheet


FULL_MARGIN = mm(4)  # equal white margin on all 4 sides for the Full 6x4 layout


def make_full(img):
    """Full-sheet layout: the photo gets a thin black border, then an equal
    white margin on all four sides, so nothing prints edge-to-edge.
    The picture is developed at FULL_DPI (plenty for a 6x4 print), then enlarged to the sheet with Lanczos."""
    if img.height > img.width:
        img = img.rotate(90, expand=True)
    inner_w, inner_h = SH_W - 2 * FULL_MARGIN, SH_H - 2 * FULL_MARGIN
    pw_, ph_ = inner_w - 2 * BORDER, inner_h - 2 * BORDER
    wk = min(1.0, FULL_DPI / DPI)
    work = ImageOps.fit(img, (max(8, round(pw_ * wk)), max(8, round(ph_ * wk))), Image.LANCZOS)
    work = enhance(work, 1.0)
    photo = work if work.size == (pw_, ph_) else work.resize((pw_, ph_), Image.LANCZOS)
    if photo is not work:
        photo = photo.filter(ImageFilter.UnsharpMask(1.4, 35, 2))
    sheet = Image.new("RGB", (SH_W, SH_H), "white")
    x, y = FULL_MARGIN, FULL_MARGIN
    sheet.paste(photo, (x + BORDER, y + BORDER))
    ImageDraw.Draw(sheet).rectangle((x, y, x + inner_w - 1, y + inner_h - 1), outline=(0, 0, 0), width=BORDER)
    return sheet

