package dev.tablink

import android.app.Notification
import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification

/**
 * Mirrors tablet notifications to the laptop (only after the user grants Notification access).
 * Ongoing ones (music, downloads, TabLink itself) are skipped. Nothing is stored or logged.
 */
class NotificationMirror : NotificationListenerService() {
    override fun onNotificationPosted(sbn: StatusBarNotification) {
        val n = sbn.notification
        if (sbn.packageName == packageName || sbn.isOngoing) return
        if (n.flags and Notification.FLAG_GROUP_SUMMARY != 0) return
        val extras = n.extras
        val title = extras.getCharSequence(Notification.EXTRA_TITLE)?.toString().orEmpty()
        val text = extras.getCharSequence(Notification.EXTRA_TEXT)?.toString().orEmpty()
        if (title.isEmpty() && text.isEmpty()) return
        val app = try {
            packageManager.getApplicationLabel(
                packageManager.getApplicationInfo(sbn.packageName, 0),
            ).toString()
        } catch (e: Exception) {
            sbn.packageName
        }
        LinkService.enqueue(Protocol.notification(app, title.take(300), text.take(1000)))
        if (!LinkService.running) {
            try {
                LinkService.start(this)
            } catch (e: Exception) {
                // Background start refused; queued items go out when TabLink next opens.
            }
        }
    }
}
