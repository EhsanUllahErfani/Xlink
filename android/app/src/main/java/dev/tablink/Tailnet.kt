package dev.tablink

import android.content.Context
import android.os.Build
import dev.tablink.tablinknet.InterfaceSource
import dev.tablink.tablinknet.Node
import dev.tablink.tablinknet.Router
import dev.tablink.tablinknet.Store
import dev.tablink.tablinknet.Tablinknet
import java.io.File
import java.net.NetworkInterface
import org.json.JSONObject

/**
 * TabLink's encrypted networks, built in (no Tailscale app, no VPN):
 * - "private": TabLink's own network, served by the laptop (Headscale). Just your devices.
 * - "tailscale": fallback through Tailscale's service, for campus / far away.
 *
 * A [Router] carries the app's connections to 127.0.0.1:47800 (control) and :3389 (remote
 * desktop) to the laptop over whichever network reaches it, private first. Both nodes forward
 * tailnet port 5555 to the tablet's adb so the laptop can show and wake the tablet.
 * Keys and join keys are stored encrypted with Android Keystore.
 */
object Tailnet {
    private const val ADB_PORT = 5555L
    const val PENDING_PRIVATE_FILE = "private-network.pending" // written by `tablink pair`
    private const val PREFS = "tablink"
    private const val SERVER_URL = "private_server_url"
    private const val JOIN_KEY = "private_join_key"

    @Volatile private var privateNode: Node? = null
    @Volatile private var tailscaleNode: Node? = null
    @Volatile private var router: Router? = null

    /** Why internet access is not running, if it failed to start. */
    @Volatile var lastError: String? = null

    private class KeystoreStore(context: Context, private val prefix: String) : Store {
        private val prefs = context.getSharedPreferences("tailnet", Context.MODE_PRIVATE)

        override fun read(id: String): ByteArray? =
            prefs.getString(prefix + id, null)?.let {
                try {
                    Secrets.decryptBytes(it)
                } catch (e: Exception) {
                    null
                }
            }

        override fun write(id: String, data: ByteArray?) {
            prefs.edit().putString(prefix + id, Secrets.encryptBytes(data ?: ByteArray(0))).apply()
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

    private fun prefs(context: Context) =
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    /** Take the private network's server address and join key delivered by `tablink pair`. */
    fun absorbPending(context: Context) {
        val file = File(context.filesDir, PENDING_PRIVATE_FILE)
        if (!file.exists()) return
        val text = file.readText()
        file.delete()
        val json = try { JSONObject(text) } catch (e: Exception) { return }
        val url = json.optString("server_url")
        val key = json.optString("join_key")
        val edit = prefs(context).edit()
        if (url.startsWith("http://") || url.startsWith("https://")) edit.putString(SERVER_URL, url)
        if (key.isNotEmpty()) edit.putString(JOIN_KEY, Secrets.encryptBytes(key.toByteArray()))
        edit.apply()
    }

    /** The laptop announces its server address in its hello; follow it when it changes. */
    fun updateServerUrl(context: Context, url: String) {
        if (!(url.startsWith("http://") || url.startsWith("https://"))) return
        val p = prefs(context)
        if (p.getString(SERVER_URL, null) == url) return
        p.edit().putString(SERVER_URL, url).apply()
        // Restart the private node on the new address (keys are kept).
        synchronized(this) {
            privateNode?.close()
            privateNode = null
        }
        start(context)
    }

    /**
     * Start both nodes and the router (idempotent). Call from the foreground LinkService.
     * A fatal error inside Go would kill the app, so a marker skips internet access after a
     * crash instead of crash-looping (a new install retries). Report: files/tsnet-crash.txt.
     */
    @Synchronized
    fun start(context: Context) {
        absorbPending(context)
        val marker = File(context.filesDir, "tsnet-starting")
        val build = context.packageManager.getPackageInfo(context.packageName, 0).lastUpdateTime.toString()
        if (marker.exists() && marker.readText() == build) {
            lastError = "it stopped the app last time (report saved); a TabLink update retries"
            return
        }
        marker.writeText(build)
        Tablinknet.setCrashFile(File(context.filesDir, "tsnet-crash.txt").path)
        Tablinknet.setInterfaceSource(JavaInterfaces)
        val r = router ?: Tablinknet.newRouter().also { router = it }

        val url = prefs(context).getString(SERVER_URL, null)
        if (privateNode == null && url != null) {
            val dir = File(context.filesDir, "tsnet-private").apply { mkdirs() }
            val key = prefs(context).getString(JOIN_KEY, null)
                ?.let { String(Secrets.decryptBytes(it)) } ?: ""
            val n = Tablinknet.newNodeAt(dir.path, hostname(), KeystoreStore(context, "p:"), url, key)
            n.start()
            n.forwardToLocal(ADB_PORT)
            privateNode = n
            prefs(context).edit().remove(JOIN_KEY).apply() // one-time: forget it once used
        }
        if (tailscaleNode == null) {
            val dir = File(context.filesDir, "tsnet").apply { mkdirs() }
            val n = Tablinknet.newNode(dir.path, hostname(), KeystoreStore(context, ""))
            n.start()
            n.forwardToLocal(ADB_PORT)
            tailscaleNode = n
        }
        // Preference order: TabLink's own network first, then Tailscale.
        r.reset()
        privateNode?.let { r.add(it) }
        tailscaleNode?.let { r.add(it) }
        if (!routerStarted) {
            r.listenLocal(Ports.CONTROL.toLong(), Ports.CONTROL.toLong())
            r.listenLocal(3389, 3389)
            routerStarted = true
        }
        lastError = null
        Thread {
            Thread.sleep(15_000)
            marker.delete()
        }.start()
    }

    @Volatile private var routerStarted = false

    @Synchronized
    fun stop() {
        router?.close()
        router = null
        routerStarted = false
        privateNode?.close()
        privateNode = null
        tailscaleNode?.close()
        tailscaleNode = null
    }

    data class Status(val state: String, val ip: String, val authUrl: String)

    /** The private network's status (what the main screen shows first). */
    fun privateStatus(): Status {
        val n = privateNode ?: return Status(if (lastError != null) "Error" else "Off", lastError ?: "", "")
        val state = n.state()
        return Status(state, if (state == "Running") n.iPv4() else "", "")
    }

    /** The Tailscale fallback's status (sign-in link when it needs one). */
    fun status(): Status {
        val n = tailscaleNode ?: return Status(if (lastError != null) "Error" else "Off", lastError ?: "", "")
        val state = n.state()
        return Status(state, if (state == "Running") n.iPv4() else "", if (state == "NeedsLogin") n.authURL() else "")
    }

    /** "private", "tailscale" or "" (laptop not reachable). */
    fun laptopVia(): String = when (router?.laptopVia()) {
        "0" -> if (privateNode != null) "private" else "tailscale"
        "1" -> "tailscale"
        else -> ""
    }

    fun signOut() {
        tailscaleNode?.logout()
    }
}
