package dev.tablink

import java.io.BufferedReader
import java.io.InputStreamReader
import java.net.InetSocketAddress
import java.net.Socket
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

/**
 * Talks to the laptop daemon on the control port. Over USB the laptop is at 127.0.0.1 through
 * `adb reverse`, so the app never needs to know an IP address.
 */
class ControlClient(
    private val host: String = "127.0.0.1",
    private val port: Int = Ports.CONTROL,
) {
    suspend fun send(type: String, readTimeoutMs: Int = 45_000): Protocol.Reply =
        withContext(Dispatchers.IO) {
            Socket().use { s ->
                s.connect(InetSocketAddress(host, port), 1_500)
                s.soTimeout = readTimeoutMs
                s.getOutputStream().write(Protocol.request(type).toByteArray())
                val line = BufferedReader(InputStreamReader(s.getInputStream())).readLine()
                    ?: return@use Protocol.Reply(type, false, "The laptop closed the connection.")
                Protocol.parse(line)
            }
        }

    /** The laptop's active link mode ("usb", "lan", ...) or null if unreachable. */
    suspend fun linkMode(): String? =
        try {
            val r = send("hello", readTimeoutMs = 2_000)
            if (r.type == "hello") r.mode else null
        } catch (e: Exception) {
            null
        }
}
