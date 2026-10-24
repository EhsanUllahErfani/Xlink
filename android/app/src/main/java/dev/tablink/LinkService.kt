package dev.tablink

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.net.nsd.NsdManager
import android.net.nsd.NsdServiceInfo
import android.net.wifi.WifiManager
import android.net.wifi.WifiNetworkSuggestion
import android.os.IBinder
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch

/**
 * Keeps one authenticated connection to the laptop while TabLink runs (a foreground service,
 * so HyperOS does not kill it). Applies clipboard text pushed by the laptop and sends what the
 * tablet queues (clipboard, notifications). Reconnects every few seconds when the link drops.
 */
class LinkService : Service() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        running = true
        startForeground(
            NOTIFICATION_ID,
            notification(),
            ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE,
        )
        scope.launch { loop() }
        announce()
        scope.launch {
            try {
                Tailnet.start(this@LinkService)
            } catch (e: Exception) {
                // Internet access stays off; the local link still works.
            }
        }
    }

    override fun onDestroy() {
        running = false
        nsdListener?.let { runCatching { getSystemService(NsdManager::class.java).unregisterService(it) } }
        Tailnet.stop()
        scope.cancel()
        super.onDestroy()
    }

    private suspend fun loop() {
        val client = ControlClient({ Secrets.token(this) })
        while (scope.isActive) {
            try {
                val conn = client.connectBlocking(readTimeoutMs = 0)
                conn.write("{\"type\":\"subscribe\"}\n")
                conn.read()
                connected = true
                val sender = scope.launch {
                    while (isActive) {
                        val line = outbox.poll(1, TimeUnit.SECONDS) ?: continue
                        synchronized(conn) { conn.write(line) }
                    }
                }
                try {
                    while (true) {
                        val line = conn.readLine() ?: break
                        when (Protocol.parse(line).type) {
                            "clip" -> applyClip(Protocol.parse(line).message)
                            "wifi" -> rememberHotspot(line)
                        }
                    }
                } finally {
                    sender.cancel()
                    conn.socket.close()
                }
            } catch (e: Exception) {
                // Laptop unreachable or not paired yet: try again shortly.
            }
            connected = false
            delay(3_000)
        }
    }

    private var nsdListener: NsdManager.RegistrationListener? = null

    /**
     * Announce TabLink on the local network (mDNS `_tablink._tcp`), so the laptop finds the
     * tablet even after its Wi-Fi address changed. Port = the tablet's adb over Wi-Fi.
     */
    private fun announce() {
        val info = NsdServiceInfo().apply {
            serviceName = Tailnet.hostname()
            serviceType = "_tablink._tcp"
            port = 5555
        }
        val listener = object : NsdManager.RegistrationListener {
            override fun onServiceRegistered(i: NsdServiceInfo) = Unit
            override fun onRegistrationFailed(i: NsdServiceInfo, code: Int) = Unit
            override fun onServiceUnregistered(i: NsdServiceInfo) = Unit
            override fun onUnregistrationFailed(i: NsdServiceInfo, code: Int) = Unit
        }
        runCatching {
            getSystemService(NsdManager::class.java)
                .registerService(info, NsdManager.PROTOCOL_DNS_SD, listener)
            nsdListener = listener
        }
    }

    private fun applyClip(text: String) {
        lastFromLaptop = text
        val cm = getSystemService(ClipboardManager::class.java)
        android.os.Handler(mainLooper).post {
            cm.setPrimaryClip(ClipData.newPlainText("TabLink", text))
        }
    }

    /**
     * The laptop is about to start its hotspot: offer it to Android as a suggested network, so
     * the tablet joins by itself when no other known Wi-Fi is around. Android keeps the
     * password; TabLink does not store it.
     */
    private fun rememberHotspot(line: String) {
        val ssid = Protocol.stringField(line, "ssid") ?: return
        val psk = Protocol.stringField(line, "psk") ?: return
        if (ssid.isEmpty() || psk.length < 8) return
        val wifi = getSystemService(WifiManager::class.java)
        val suggestion = WifiNetworkSuggestion.Builder()
            .setSsid(ssid)
            .setWpa2Passphrase(psk)
            .setIsAppInteractionRequired(false)
            .build()
        wifi.removeNetworkSuggestions(listOf(suggestion))
        wifi.addNetworkSuggestions(listOf(suggestion))
    }

    private fun notification(): Notification {
        val nm = getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(
            NotificationChannel(CHANNEL, "Link to laptop", NotificationManager.IMPORTANCE_MIN),
        )
        val open = PendingIntent.getActivity(
            this, 0, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE,
        )
        return Notification.Builder(this, CHANNEL)
            .setSmallIcon(android.R.drawable.stat_sys_data_bluetooth)
            .setContentTitle("TabLink")
            .setContentText("Linked with your laptop: clipboard, files and notifications")
            .setContentIntent(open)
            .setOngoing(true)
            .build()
    }

    companion object {
        private const val CHANNEL = "link"
        private const val NOTIFICATION_ID = 1
        private val outbox = LinkedBlockingQueue<String>(200)

        @Volatile var running = false
        @Volatile var connected = false

        /** Last text the laptop put on our clipboard, so it is not sent straight back. */
        @Volatile var lastFromLaptop: String? = null

        fun start(context: Context) {
            context.startForegroundService(Intent(context, LinkService::class.java))
        }

        /** Queue a line for the laptop; dropped if the queue is full (never blocks the UI). */
        fun enqueue(line: String) {
            outbox.offer(line)
        }

        /** Send the tablet clipboard to the laptop if it is new. Call while in the foreground. */
        fun sendClipboard(context: Context): Boolean {
            val cm = context.getSystemService(ClipboardManager::class.java)
            val text = cm.primaryClip?.takeIf { it.itemCount > 0 }
                ?.getItemAt(0)?.coerceToText(context)?.toString() ?: return false
            if (text.isEmpty() || text == lastFromLaptop || text == lastSent) return false
            lastSent = text
            enqueue(Protocol.clip(text.take(512 * 1024)))
            return true
        }

        @Volatile private var lastSent: String? = null
    }
}
