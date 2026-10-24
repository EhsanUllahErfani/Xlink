package dev.tablink

import android.content.Context
import android.os.Build
import dev.tablink.tablinknet.Node
import dev.tablink.tablinknet.Store
import dev.tablink.tablinknet.Tablinknet
import java.io.File

/**
 * TabLink's built-in Tailscale node: joins the user's tailnet without the Tailscale app or a
 * VPN. The laptop reaches the tablet through it from far away; the node forwards those
 * connections to the tablet's adb on 127.0.0.1:5555. Tailscale's keys are stored encrypted with
 * the app's Android Keystore key.
 */
object Tailnet {
    private const val PREFS = "tailnet"
    private const val ADB_PORT = 5555L

    @Volatile private var node: Node? = null

    private class KeystoreStore(context: Context) : Store {
        private val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

        override fun read(id: String): ByteArray? =
            prefs.getString(id, null)?.let {
                try {
                    Secrets.decryptBytes(it)
                } catch (e: Exception) {
                    null
                }
            }

        override fun write(id: String, data: ByteArray?) {
            prefs.edit().putString(id, Secrets.encryptBytes(data ?: ByteArray(0))).apply()
        }
    }

    fun hostname(): String =
        "tablink-" + (Build.MODEL ?: "tablet").lowercase().replace(Regex("[^a-z0-9]+"), "-").trim('-')

    /** Start the node (idempotent). Call from the foreground LinkService. */
    @Synchronized
    fun start(context: Context) {
        if (node != null) return
        val dir = File(context.filesDir, "tsnet").apply { mkdirs() }
        val n = Tablinknet.newNode(dir.path, hostname(), KeystoreStore(context.applicationContext))
        n.start()
        n.forwardToLocal(ADB_PORT)
        node = n
    }

    @Synchronized
    fun stop() {
        node?.close()
        node = null
    }

    data class Status(val state: String, val ip: String, val authUrl: String)

    fun status(): Status {
        val n = node ?: return Status("Off", "", "")
        val state = n.state()
        return Status(state, if (state == "Running") n.iPv4() else "", if (state == "NeedsLogin") n.authURL() else "")
    }

    fun signOut() {
        node?.logout()
    }
}
