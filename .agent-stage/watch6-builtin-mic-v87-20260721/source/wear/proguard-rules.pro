# Manifest-declared receivers and services are retained by the Android Gradle Plugin.
# Keep the listener callbacks explicit as a defensive guard for OEM Wear builds.
-keep class com.shushunya.m.wear.** extends android.app.Service { *; }
-keep class com.shushunya.m.wear.** extends android.content.BroadcastReceiver { *; }
