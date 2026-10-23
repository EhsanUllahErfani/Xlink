package dev.tablink

import android.app.Activity
import android.os.Bundle
import android.view.WindowManager
import com.freerdp.freerdpcore.application.GlobalApp
import com.freerdp.freerdpcore.presentation.SessionActivity

/**
 * TabLink's application. It extends FreeRDP's GlobalApp, which owns the remote-desktop
 * sessions (the RDP engine built into this app; see android/scripts/build-freerdp.sh).
 */
class TabLinkApp : GlobalApp() {
    override fun onCreate() {
        super.onCreate()
        // While the laptop desktop is on screen the user is working, not idle: keep the
        // tablet's screen on so it does not time out and lock in the middle of a session.
        registerActivityLifecycleCallbacks(object : ActivityLifecycleCallbacks {
            override fun onActivityCreated(activity: Activity, state: Bundle?) {
                if (activity is SessionActivity) {
                    activity.window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
                }
            }

            override fun onActivityStarted(activity: Activity) = Unit
            override fun onActivityResumed(activity: Activity) = Unit
            override fun onActivityPaused(activity: Activity) = Unit
            override fun onActivityStopped(activity: Activity) = Unit
            override fun onActivitySaveInstanceState(activity: Activity, outState: Bundle) = Unit
            override fun onActivityDestroyed(activity: Activity) = Unit
        })
    }
}
