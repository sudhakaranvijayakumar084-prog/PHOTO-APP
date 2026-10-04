# Build Deluxe Digital Studio APK

## GitHub Actions (recommended)

1. Create a GitHub repository and upload the **contents** of this folder.
2. Keep `.github/workflows/build-apk.yml` in place.
3. Open **Actions → Build APK → Run workflow**.
4. When it finishes, open the run and download the **DeluxeStudio-apk** artifact.
5. Unzip it and install the `.apk` on an Android phone.

The workflow uses Ubuntu, Python 3.11, Java 17 and Buildozer. The APK is built for `arm64-v8a`.

## Linux / WSL2

Install the required Linux packages, then:

```bash
python3 -m pip install --user --upgrade buildozer 'cython<3.4'
cd studio_apk
buildozer -v android debug
```

The APK will be in `bin/`.

If you change `buildozer.spec` or dependencies after a build, run:

```bash
buildozer android clean
```

before rebuilding.

## What is included

- `main.py` — Kivy touch interface.
- `engine.py` — face detection, crop, blue-background replacement, enhancement and 6x4 sheet generation.
- `android_io.py` — Android photo selection, MediaStore saving, sharing and Android printing.
- `cascades/` — bundled OpenCV Haar cascade XML files, so face detection does not depend on a phone's filesystem.
- `buildozer.spec` — Android packaging configuration.
- `.github/workflows/build-apk.yml` — one-click GitHub Actions build.

## Android behavior

The app uses the system photo chooser rather than direct filesystem access. Finished JPEG sheets are saved through MediaStore under `Pictures/StudioPrints`, and the Print button opens Android's print dialog. The Python code already contains Android-specific handling for these operations.
