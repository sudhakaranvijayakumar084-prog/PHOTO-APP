[app]
# Deluxe Digital Studio - Android build configuration
title = Deluxe Digital Studio
package.name = deluxestudio
package.domain = com.deluxestudio
source.dir = .
source.include_exts = py,xml,png,jpg,jpeg,txt
source.include_patterns = cascades/*.xml
version = 1.0.0
requirements = python3,kivy==2.3.0,numpy,opencv,pillow
orientation = portrait
fullscreen = 0

# Android 10+ MediaStore is used for saving images; no legacy storage permission is required.
android.api = 34
android.minapi = 29
android.archs = arm64-v8a
android.accept_sdk_license = True
android.enable_androidx = True
android.gradle_dependencies = androidx.print:print:1.0.0
android.allow_backup = True

[buildozer]
log_level = 2
warn_on_root = 1
