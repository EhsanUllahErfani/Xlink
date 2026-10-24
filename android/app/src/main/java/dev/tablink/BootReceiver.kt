package dev.tablink

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.provider.Settings

/**
 * After a restart: start TabLink's link (tablet -> laptop works at once over its encrypted
 * network) and remind the user to turn Wireless debugging back on, which Android switches off
 * at every restart and which the laptop needs to show and control this tablet.
 */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != Intent.ACTION_BOOT_COMPLETED) return
        runCatching { LinkService.start(context) }
        val nm = context.getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(
            NotificationChannel("setup", "Setup reminders", NotificationManager.IMPORTANCE_DEFAULT),
        )
        val open = PendingIntent.getActivity(
            context, 1,
            Intent(Settings.ACTION_APPLICATION_DEVELOPMENT_SETTINGS)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
            PendingIntent.FLAG_IMMUTABLE,
        )
        nm.notify(
            2,
            android.app.Notification.Builder(context, "setup")
                .setSmallIcon(android.R.drawable.stat_sys_data_bluetooth)
                .setContentTitle("TabLink: turn on Wireless debugging")
                .setContentText("So your laptop can show this tablet again (Android turns it off at every restart).")
                .setContentIntent(open)
                .setAutoCancel(true)
                .build(),
        )
    }
}
