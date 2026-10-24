package dev.tablink

import android.content.Context
import android.os.Build
import dev.tablink.tablinknet.InterfaceSource
import dev.tablink.tablinknet.Node
import dev.tablink.tablinknet.Store
import dev.tablink.tablinknet.Tablinknet
import java.io.File
import java.net.NetworkInterface

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

    /** Why the node is not running, if it failed to start. */
    @Volatile var lastError: String? = null

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

    /** Android blocks Go's way of listing network interfaces; Java's way is allowed. */
    private object JavaInterfaces : InterfaceSource {
        override fun interfaces(): String {
            val lines = StringBuilder()
            val all = try {
                NetworkInterface.getNetworkInterfaces()?.toList().orEmpty()
            } catch (e: Exception) {
                emptyList()
            }
            for (ni in all) {
                val flags = buildList {
                    if (ni.isUp) add("up")
                    if (ni.isLoopback) add("loopback")
                    if (ni.isPointToPoint) add("pointtopoint")
                    if (ni.supportsMulticast()) add("multicast")
                    if (!ni.isLoopback && !ni.isPointToPoint) add("broadcast")
                }.joinToString(",").ifEmpty { "-" }
                val addrs = ni.interfaceAddresses.mapNotNull { ia ->
                    val host = ia.address?.hostAddress?.substringBefore('%') ?: return@mapNotNull null
                    "$host/${ia.networkPrefixLength}"
                }
                val mtu = try { ni.mtu } catch (e: Exception) { 1500 }
                lines.append(ni.name).append(' ').append(ni.index).append(' ').append(mtu)
                    .append(' ').append(flags)
                addrs.forEach { lines.append(' ').append(it) }
                lines.append('\n')
            }
            return lines.toString()
        }
    }

    fun hostname(): String =
        "tablink-" + (Build.MODEL ?: "tablet").lowercase().replace(Regex("[^a-z0-9]+"), "-").trim('-')

    /**
     * Start the node (idempotent). Call from the foreground LinkService.
     *
     * A fatal error inside Tailscale's Go code would kill the whole app, so a marker file is
     * written before starting and removed after; if the app finds it on the next start, the
     * previous attempt crashed and internet access is skipped instead of crashing again.
     * The Go crash report goes to files/tsnet-crash.txt.
     */
    @Synchronized
    fun start(context: Context) {
        if (node != null) return
        val dir = File(context.filesDir, "tsnet").apply { mkdirs() }
        val marker = File(context.filesDir, "tsnet-starting")
        val build = context.packageManager.getPackageInfo(context.packageName, 0).lastUpdateTime.toString()
        if (marker.exists() && marker.readText() == build) {
            lastError = "it stopped the app last time (report saved); a TabLink update retries"
            return
        }
        marker.writeText(build)  // a new install of TabLink tries again
        Tablinknet.setCrashFile(File(context.filesDir, "tsnet-crash.txt").path)
        Tablinknet.setInterfaceSource(JavaInterfaces)
        val n = Tablinknet.newNode(dir.path, hostname(), KeystoreStore(context.applicationContext))
        n.start()
        n.forwardToLocal(ADB_PORT)
        node = n
        lastError = null
        // Running for a few seconds without dying counts as a good start.
        Thread {
            Thread.sleep(15_000)
            marker.delete()
        }.start()
    }

    @Synchronized
    fun stop() {
        node?.close()
        node = null
    }

    data class Status(val state: String, val ip: String, val authUrl: String)

    fun status(): Status {
        val n = node ?: return Status(if (lastError != null) "Error" else "Off", lastError ?: "", "")
        val state = n.state()
        return Status(state, if (state == "Running") n.iPv4() else "", if (state == "NeedsLogin") n.authURL() else "")
    }

    fun signOut() {
        node?.logout()
    }
}
