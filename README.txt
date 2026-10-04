DELUXE DIGITAL STUDIO - Android APK project
============================================

This folder is ready to build with Buildozer.

Contents
--------
main.py                         Kivy touch-screen application
engine.py                       image processing: face detection, crop, blue background, enhancement, sheets
android_io.py                   Android photo picker, Gallery/MediaStore saving, share sheet, print dialog
cascades/                       bundled OpenCV Haar cascade XML files
buildozer.spec                  Android/Buildozer configuration
.github/workflows/build-apk.yml GitHub Actions APK builder
BUILD_APK.md                    build instructions

BUILD THE APK WITH GITHUB (recommended)
----------------------------------------
1. Create a GitHub repository (private is fine).
2. Upload the CONTENTS of this folder, including .github/workflows/build-apk.yml.
3. Open Actions -> Build APK -> Run workflow.
4. Wait for the build to finish.
5. Open the completed workflow run -> Artifacts -> DeluxeStudio-apk.
6. Unzip the artifact and install the APK on an Android phone.

BUILD LOCALLY ON LINUX / WSL2
-----------------------------
Install the Linux prerequisites, then:
  python3 -m pip install --user --upgrade buildozer 'cython<3.4'
  cd studio_apk
  buildozer -v android debug

The APK is written to bin/.

If buildozer.spec or dependencies change, clean before rebuilding:
  buildozer android clean

APP FLOW
--------
1 CHOOSE PHOTO -> 2 choose 4 COPIES / 8 COPIES / FULL 6x4 -> 3 PRINT.

PRINT opens Android's print dialog. Select the printer, 4x6 in (10x15 cm)
paper and the desired quality/settings supported by the printer service.

SAVE / SHARE saves the JPEG through Android MediaStore under
Pictures/StudioPrints and opens Android's share sheet.

The app uses Android's system photo chooser and does not require legacy
storage permissions on Android 10+.
