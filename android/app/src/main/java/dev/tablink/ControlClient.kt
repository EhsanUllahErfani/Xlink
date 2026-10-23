package dev.tablink

import java.io.BufferedReader
import java.io.InputStream
import java.io.InputStreamReader
import java.io.OutputStream
import java.net.InetSocketAddress
import java.net.Socket
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

/**
 * Talks to the laptop daemon on the control port. On every link the laptop is at 127.0.0.1
 * through the adb tunnel. Each connection proves pairing by answering the laptop's challenge
 * with HMAC-SHA256(token, challenge); the token itself never crosses the link.
 */
class ControlClient(
    private val token: () -> String?,
    private val host: String = "127.0.0.1",
    private val port: Int = Ports.CONTROL,
) {
    class NotPaired : Exception("Not paired yet. On the laptop, run: tablink pair")

    /** An authenticated connection. */
    class Conn(val socket: Socket) {
        val input: BufferedReader = BufferedReader(InputStreamReader(socket.getInputStream()))
        val output: OutputStream = socket.getOutputStream()
        val raw: InputStream get() = socket.getInputStream()

        fun write(line: String) {
            output.write(line.toByteArray())
            output.flush()
        }

        fun read(): Protocol.Reply =
            input.readLine()?.let(Protocol::parse) ?: throw java.io.EOFException("laptop closed")

        fun readLine(): String? = input.readLine()
    }

    fun connectBlocking(readTimeoutMs: Int = 45_000): Conn {
        val s = Socket()
        s.connect(InetSocketAddress(host, port), 1_500)
        s.soTimeout = readTimeoutMs
        val conn = Conn(s)
        val challenge = conn.readLine() ?: throw java.io.EOFException("laptop closed")
        val nonce = Protocol.stringField(challenge, "nonce") ?: throw java.io.IOException("bad challenge")
        val t = token() ?: run {
            s.close()
            throw NotPaired()
        }
        conn.write(Protocol.auth(hmac(t, nonce)))
        if (!conn.read().ok) {
            s.close()
            throw NotPaired()
        }
        return conn
    }

    suspend fun send(type: String, readTimeoutMs: Int = 45_000): Protocol.Reply =
        withContext(Dispatchers.IO) {
            connectBlocking(readTimeoutMs).socket.use { s ->
                val c = Conn(s)
                c.write(Protocol.request(type))
                c.read()
            }
        }

    /** The laptop's active link mode ("usb", "lan", ...) or null if unreachable. No pairing needed. */
    suspend fun linkMode(): String? = withContext(Dispatchers.IO) {
        try {
            Socket().use { s ->
                s.connect(InetSocketAddress(host, port), 1_500)
                s.soTimeout = 2_000
                val c = Conn(s)
                c.readLine() // challenge
                c.write(Protocol.request("hello"))
                val r = c.read()
                if (r.type == "hello") r.mode else null
            }
        } catch (e: Exception) {
            null
        }
    }

    companion object {
        fun hmac(token: String, nonce: String): String {
            val m = Mac.getInstance("HmacSHA256")
            m.init(SecretKeySpec(token.toByteArray(), "HmacSHA256"))
            return m.doFinal(nonce.toByteArray()).joinToString("") { "%02x".format(it) }
        }
    }
}
