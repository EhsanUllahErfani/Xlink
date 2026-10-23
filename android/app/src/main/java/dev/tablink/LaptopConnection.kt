package dev.tablink

import android.content.Context
import android.content.Intent
import com.freerdp.freerdpcore.data.AppDatabase
import com.freerdp.freerdpcore.domain.BookmarkBase
import com.freerdp.freerdpcore.presentation.SessionActivity
import com.freerdp.freerdpcore.services.ManualBookmarkGateway
import java.io.File
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

/**
 * The laptop's remote-desktop connection, kept in FreeRDP's database (encrypted with a key from
 * Android Keystore). Over every link the laptop is at 127.0.0.1:3389 through the adb tunnel.
 *
 * The password arrives once, at pairing: the laptop writes it to [PENDING_FILE] in this app's
 * private storage; it is moved into the encrypted database on the next start and the file is
 * deleted. It is never shown, logged, or put in an Intent.
 */
object LaptopConnection {
    const val LABEL = "Laptop"
    const val HOST = "127.0.0.1"
    const val PORT = 3389
    const val USER = "tablink"
    const val PENDING_FILE = "rdp-password.pending"

    private fun gateway(context: Context) =
        ManualBookmarkGateway(AppDatabase.getInstance(context).bookmarkDao())

    /** Take a password delivered by `tablink pair`, if any. Returns true if one was stored. */
    suspend fun absorbPendingPassword(context: Context): Boolean = withContext(Dispatchers.IO) {
        val file = File(context.filesDir, PENDING_FILE)
        if (!file.exists()) return@withContext false
        val password = file.readText().trim()
        file.delete()
        if (password.isEmpty()) return@withContext false
        val gw = gateway(context)
        val existing = gw.findAll().firstOrNull { it.label == LABEL }
        val bookmark = existing ?: BookmarkBase()
        bookmark.label = LABEL
        bookmark.hostname = HOST
        bookmark.port = PORT
        bookmark.username = USER
        bookmark.password = password
        bookmark.activeScreenSettings.setResolution(BookmarkBase.ScreenSettings.FITSCREEN)
        if (existing == null) gw.insert(bookmark) else gw.update(bookmark)
        true
    }

    /** id of the saved laptop connection, or null before pairing. */
    suspend fun bookmarkId(context: Context): Long? = withContext(Dispatchers.IO) {
        gateway(context).findAll().firstOrNull { it.label == LABEL }?.id
    }

    /** Open the laptop desktop full screen inside TabLink. */
    fun open(context: Context, bookmarkId: Long) {
        context.startActivity(
            Intent(context, SessionActivity::class.java)
                .putExtra(SessionActivity.PARAM_CONNECTION_REFERENCE, "MBMID/$bookmarkId"),
        )
    }
}
