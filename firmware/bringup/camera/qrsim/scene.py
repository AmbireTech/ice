"""A dense Ambire request QR code on a phone screen, as the device's camera
sees it, and the ways the camera can turn the sensor's pixels into the frame
the decoder reads.

Model (the assumptions behind every number the sweep prints):

- The code: a UR part like the app's (200-byte fragments, upper case:
  "UR:ETH-SIGN-REQUEST/123-45/" + 438 bytewords letters), QR alphanumeric
  mode, ECC L, as react-native-qrcode-svg draws it in the app: version 11
  (61 modules). Longer texts give versions 12-14 for the margin study.
- The phone: 400-460 ppi, 9-14 screen pixels per module (300 dp at 3x), the
  app's 2-module white quiet zone, then the app's background (light or dark).
  Screen pixels light 70 % of their pitch (dark gaps), modelled as its first
  harmonic, attenuated by the sensor pixel's area and the optics, so it
  aliases (moire) as the sampling allows.
- The camera: a 1600x1200 sensor behind a lens of 55 deg horizontal field
  (focal 1537 px), the plane tilted up to 15 deg, rotated up to 30 deg.
  Optical blur (lens + demosaic) a Gaussian of 0.6-1.4 sensor pixels; motion
  blur 0-2 px half the time. Exposure: the screen's white at 0.5-1.25 of the
  clipping level, its black at 4-20 % of white (screen black + reflections);
  shot and read noise; gamma 2.2; 8 bits out.
- "uneven": vignetting, a brightness gradient across the screen and half the
  time a glare spot (a lamp reflected in the glass).
"""

import math
import random

import numpy as np
import cv2
import segno

FOCAL = 800 / math.tan(math.radians(27.5))
LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
# Alphanumeric capacity at ECC L, by version
ALNUM_L = {10: 395, 11: 468, 12: 535, 13: 619, 14: 667}


def ur_text(rng, version=11):
    prefix = "UR:ETH-SIGN-REQUEST/%d-%d/" % (rng.randint(100, 400), rng.randint(20, 80))
    n = 438 if version == 11 else ALNUM_L[version] - len(prefix) - 4
    return prefix + "".join(rng.choice(LETTERS) for _ in range(n))


def qr_matrix(text):
    q = segno.make_qr(text, error="L", mode="alphanumeric", boost_error=False)
    m = np.array([[bool(c) for c in row] for row in q.matrix], dtype=bool)
    return m, q.version


def scene_params(rng, uneven=False):
    p = dict(
        ppi=rng.uniform(400, 460),
        msp=rng.uniform(9, 14),
        theta=rng.uniform(-30, 30),
        tilt=rng.uniform(0, 15),
        tilt_axis=rng.uniform(0, 180),
        sigma=rng.uniform(0.6, 1.4),
        motion=rng.uniform(0, 2) if rng.random() < 0.5 else 0.0,
        motion_dir=rng.uniform(0, 180),
        white=rng.uniform(0.5, 1.25),
        black=rng.uniform(0.04, 0.2),
        bg=rng.choice([1.0, 0.08]),
        pos=(rng.random(), rng.random()),
        phase=(rng.random(), rng.random()),
        seed=rng.randrange(1 << 30),
        uneven=uneven,
    )
    if uneven:
        p.update(
            vignette=rng.uniform(0.15, 0.4),
            grad=rng.uniform(-0.35, 0.35),
            grad_dir=rng.uniform(0, 360),
            glare=rng.uniform(0.2, 0.6) if rng.random() < 0.5 else 0.0,
            glare_pos=(rng.random(), rng.random()),
            glare_r=rng.uniform(0.1, 0.3),
        )
    return p


def render_sensor(matrix, p, region, code_px):
    """Linear sensor values (0..1, before noise) over a region x region window
    of sensor pixels, the code's side spanning code_px sensor pixels. region
    may be a (w, h) pair."""
    rw, rh = (region, region) if np.isscalar(region) else region
    size = matrix.shape[0]
    pitch = 25.4 / p["ppi"]  # mm per screen pixel
    mod_mm = p["msp"] * pitch
    z = FOCAL * size * mod_mm / code_px
    # Plane rotation: in-plane theta, then a tilt about an in-plane axis
    th = math.radians(p["theta"])
    rz = np.array([[math.cos(th), -math.sin(th), 0], [math.sin(th), math.cos(th), 0], [0, 0, 1]])
    a = math.radians(p["tilt_axis"])
    axis = np.array([math.cos(a), math.sin(a), 0.0])
    t = math.radians(p["tilt"])
    kx = np.array([[0, -axis[2], axis[1]], [axis[2], 0, -axis[0]], [-axis[1], axis[0], 0]])
    rt = np.eye(3) + math.sin(t) * kx + (1 - math.cos(t)) * kx @ kx
    r = rt @ rz
    kmat = np.array([[FOCAL, 0, rw / 2], [0, FOCAL, rh / 2], [0, 0, 1]])

    def homography(cx, cy):
        trans = np.array([cx * z / FOCAL, cy * z / FOCAL, z])
        return kmat @ np.column_stack([r[:, 0], r[:, 1], trans])

    # The code's centre: anywhere that keeps it and one module around it in
    # view; centred (and flagged) when it does not fit
    e = (size / 2 + 1) * mod_mm
    corners = np.array([[-e, -e, 1], [e, -e, 1], [e, e, 1], [-e, e, 1]]).T
    pc = homography(0, 0) @ corners
    pc = pc[:2] / pc[2]
    lo, hi = pc.min(axis=1), pc.max(axis=1)
    room_x = rw - (hi[0] - lo[0])
    room_y = rh - (hi[1] - lo[1])
    p["fits"] = bool(room_x >= 0 and room_y >= 0)
    cx = rw / 2 - (lo[0] + hi[0]) / 2 + (p["pos"][0] - 0.5) * max(0.0, room_x)
    cy = rh / 2 - (lo[1] + hi[1]) / 2 + (p["pos"][1] - 0.5) * max(0.0, room_y)
    h = homography(cx, cy)
    hinv = np.linalg.inv(h)

    ss = 2  # samples per sensor pixel per axis: the pixel's area, roughly
    ys, xs = np.mgrid[0:rh * ss, 0:rw * ss].astype(np.float64)
    xs = (xs + 0.5) / ss
    ys = (ys + 0.5) / ss
    den = hinv[2, 0] * xs + hinv[2, 1] * ys + hinv[2, 2]
    pxm = (hinv[0, 0] * xs + hinv[0, 1] * ys + hinv[0, 2]) / den  # mm on the plane
    pym = (hinv[1, 0] * xs + hinv[1, 1] * ys + hinv[1, 2]) / den
    # Screen pixels (centred code), then the module each screen pixel shows
    spx = pxm / pitch + size * p["msp"] / 2
    spy = pym / pitch + size * p["msp"] / 2
    mx = np.floor((np.floor(spx) + 0.5) / p["msp"]).astype(np.int64)
    my = np.floor((np.floor(spy) + 0.5) / p["msp"]).astype(np.int64)
    inside = (mx >= 0) & (mx < size) & (my >= 0) & (my < size)
    quiet = (mx >= -2) & (mx < size + 2) & (my >= -2) & (my < size + 2)
    val = np.where(quiet, 1.0, p["bg"])
    dark = np.zeros_like(inside)
    dark[inside] = matrix[my[inside], mx[inside]]
    val = np.where(dark, p["black"], val)
    img = val.reshape(rh, ss, rw, ss).mean(axis=(1, 3))

    # Screen pixel grid, its fundamental after the sensor pixel and the optics
    sig = p["sigma"]
    ps = pitch * FOCAL / z  # screen pitch in sensor pixels
    f = 1.0 / ps
    ff = 0.7
    amp = 2 * math.sin(math.pi * ff) / (math.pi * ff)
    amp *= abs(np.sinc(f)) * math.exp(-2 * math.pi ** 2 * sig ** 2 * f ** 2)
    cyc, cxc = np.mgrid[0:rh, 0:rw].astype(np.float64)
    den = hinv[2, 0] * (cxc + 0.5) + hinv[2, 1] * (cyc + 0.5) + hinv[2, 2]
    gx = (hinv[0, 0] * (cxc + 0.5) + hinv[0, 1] * (cyc + 0.5) + hinv[0, 2]) / den / pitch
    gy = (hinv[1, 0] * (cxc + 0.5) + hinv[1, 1] * (cyc + 0.5) + hinv[1, 2]) / den / pitch

    img = cv2.GaussianBlur(img, (0, 0), sig)
    img *= (1 + amp * np.cos(2 * math.pi * (gx + p["phase"][0]))) * (
        1 + amp * np.cos(2 * math.pi * (gy + p["phase"][1])))
    if p["motion"] > 0.3:
        n = int(math.ceil(p["motion"])) * 2 + 1
        k = np.zeros((n, n))
        d = math.radians(p["motion_dir"])
        for s in np.linspace(-p["motion"] / 2, p["motion"] / 2, 4 * n):
            k[int(round(n // 2 + s * math.sin(d))), int(round(n // 2 + s * math.cos(d)))] += 1
        img = cv2.filter2D(img, -1, k / k.sum())

    img = img * p["white"]
    if p["uneven"]:
        u = (cxc + 0.5) / rw - 0.5
        v = (cyc + 0.5) / rh - 0.5
        gd = math.radians(p["grad_dir"])
        img *= (1 - p["vignette"] * (u * u + v * v) * 2) * (
            1 + p["grad"] * (u * math.cos(gd) + v * math.sin(gd)) * 2)
        if p["glare"] > 0:
            gu, gv = p["glare_pos"][0] - 0.5, p["glare_pos"][1] - 0.5
            img += p["glare"] * np.exp(-((u - gu) ** 2 + (v - gv) ** 2) / (2 * p["glare_r"] ** 2))
    return img


def add_noise(img, p):
    rng = np.random.default_rng(p["seed"])
    sd = np.sqrt(0.0008 * np.clip(img, 0, None) + 0.003 ** 2)
    return np.clip(img + rng.standard_normal(img.shape) * sd, 0, 1)


def to_y8(lin):
    return np.clip(np.round(255 * lin ** (1 / 2.2)), 0, 255).astype(np.uint8)


# Methods: name -> (sensor window per output pixel, how the frame is made)
def sample(method, n):
    """(sensor region, function linear sensor image -> linear frame)"""
    if method == "native":
        return n, lambda s: s
    if method == "vga":
        return (640, 480), lambda s: s
    if method == "bin2":
        return 2 * n, lambda s: s.reshape(n, 2, n, 2).mean(axis=(1, 3))
    if method == "skip2":
        return 2 * n, lambda s: s[::2, ::2]
    if method == "skip3":
        return 3 * n, lambda s: s[::3, ::3]
    if method.startswith("area"):
        # area<R>: an R x R window averaged down to n (a box-filter scaler)
        r = int(method[4:])
        return r, lambda s: cv2.resize(s, (n, n), interpolation=cv2.INTER_AREA)
    raise ValueError("unknown method " + method)


def otsu(img):
    """quirc's otsu() on a Y8 frame"""
    hist = np.bincount(img.ravel(), minlength=256).astype(np.float64)
    n = img.size
    total = (np.arange(256) * hist).sum()
    q1 = np.cumsum(hist)
    sumb = np.cumsum(np.arange(256) * hist)
    q2 = n - q1
    ok = (q1 > 0) & (q2 > 0)
    m1 = np.where(ok, sumb / np.maximum(q1, 1), 0)
    m2 = np.where(ok, (total - sumb) / np.maximum(q2, 1), 0)
    var = np.where(ok, (m1 - m2) ** 2 * q1 * q2, -1)
    return int(len(var) - 1 - np.argmax(var[::-1]))


def frame(matrix, p, method, n, ppm_out):
    """The Y8 frame the decoder reads, the code at ppm_out output pixels per
    module (measured along its side, before perspective)"""
    region, fn = sample(method, n)
    rw = region if np.isscalar(region) else region[0]
    out_w = n if method != "vga" else 640
    scale = rw / out_w  # sensor pixels per output pixel
    code_px = ppm_out * matrix.shape[0] * scale
    lin = render_sensor(matrix, p, region, code_px)
    return to_y8(fn(add_noise(lin, p)))
