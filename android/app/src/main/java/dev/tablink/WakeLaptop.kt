package dev.tablink

import android.content.Context
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetAddress
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

/**
 * "Wake laptop": turn the laptop's screen on when it is reachable (nearby or over Tailscale);
 * when it is asleep, send a Wake-on-Wi-Fi magic packet on the local network instead.
 * The laptop's Wi-Fi card address is learned from its hello while linked (it is not secret).
 */
object WakeLaptop {
    private const val PREFS = "tablink"
    private const val MAC = "laptop_mac"
    private const val IP = "laptop_ip"

    fun remember(context: Context, hello: Protocol.Reply) {
        if (hello.wakeMac.isEmpty()) return
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit()
            .putString(MAC, hello.wakeMac).putString(IP, hello.wakeIp).apply()
    }

    /** 6 x 0xFF then the MAC 16 times: the standard Wake-on-LAN "magic packet". */
    fun magicPacket(mac: String): ByteArray {
        val bytes = mac.split(":").map { it.toInt(16).toByte() }
        require(bytes.size == 6) { "bad MAC" }
        return ByteArray(6) { 0xFF.toByte() } + List(16) { bytes }.flatten()
    }

    suspend fun wake(context: Context, client: ControlClient): String {
        try {
            val r = client.send("wake_laptop", readTimeoutMs = 5_000)
            if (r.ok) return "The laptop screen is on. Tap \u201cShow laptop here\u201d to use it."
        } catch (e: ControlClient.NotPaired) {
            return e.message ?: "Not paired"
        } catch (e: Exception) {
            // Not reachable: the laptop is probably asleep. Try a wake packet.
        }
        val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        val mac = prefs.getString(MAC, null)
            ?: return "Can't reach the laptop, and it hasn't been linked on Wi-Fi yet."
        val ip = prefs.getString(IP, null)
        withContext(Dispatchers.IO) {
            val packet = magicPacket(mac)
            DatagramSocket().use { s ->
                s.broadcast = true
                val targets = listOfNotNull(
                    InetAddress.getByName("255.255.255.255"),
                    ip?.let { InetAddress.getByName(it) },
                )
                repeat(3) {
                    for (t in targets) s.send(DatagramPacket(packet, packet.size, t, 9))
                }
            }
        }
        return "Sent a wake signal. If the laptop is asleep on this Wi-Fi, it wakes in a few " +
            "seconds. From far away, a sleeping laptop can't be woken."
    }
}
