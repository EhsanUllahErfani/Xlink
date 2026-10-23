package dev.tablink

import android.app.Activity
import android.widget.Toast

/** See-through activity: gains focus, reads the clipboard, sends it, and closes. */
class ClipSendActivity : Activity() {
    override fun onWindowFocusChanged(hasFocus: Boolean) {
        super.onWindowFocusChanged(hasFocus)
        if (!hasFocus) return
        if (!LinkService.running) LinkService.start(this)
        val sent = LinkService.sendClipboard(this)
        Toast.makeText(
            this,
            if (sent) "Clipboard sent to laptop" else "Nothing new to send",
            Toast.LENGTH_SHORT,
        ).show()
        finish()
    }
}
