package dev.tablink

import android.app.PendingIntent
import android.content.Intent
import android.service.quicksettings.TileService

/**
 * Quick Settings tile: send the tablet clipboard to the laptop with one tap. Android only lets
 * the focused app read the clipboard, so the tile opens a see-through activity that reads it.
 */
class ClipboardTile : TileService() {
    override fun onClick() {
        val intent = Intent(this, ClipSendActivity::class.java)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        startActivityAndCollapse(
            PendingIntent.getActivity(this, 0, intent, PendingIntent.FLAG_IMMUTABLE),
        )
    }
}
