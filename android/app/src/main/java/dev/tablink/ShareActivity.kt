package dev.tablink

import android.app.Activity
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.provider.OpenableColumns
import android.widget.Toast
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/** "Send to laptop" in Android's share menu: files land in ~/Downloads/TabLink on the laptop. */
class ShareActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val uris = when (intent.action) {
            Intent.ACTION_SEND -> listOfNotNull(intent.parcelable(Intent.EXTRA_STREAM))
            Intent.ACTION_SEND_MULTIPLE -> intent.parcelableList(Intent.EXTRA_STREAM)
            else -> emptyList()
        }
        val text = intent.getStringExtra(Intent.EXTRA_TEXT)
        if (uris.isEmpty() && text != null) {
            LinkService.enqueue(Protocol.clip(text))
            if (!LinkService.running) LinkService.start(this)
            Toast.makeText(this, "Sent to the laptop's clipboard", Toast.LENGTH_SHORT).show()
            finish()
            return
        }
        Toast.makeText(this, "Sending ${uris.size} file(s) to the laptop…", Toast.LENGTH_SHORT).show()
        val app = applicationContext
        CoroutineScope(Dispatchers.IO).launch {
            val result = try {
                upload(app, uris)
                "Sent ${uris.size} file(s) to Downloads/TabLink on the laptop"
            } catch (e: ControlClient.NotPaired) {
                e.message ?: "Not paired"
            } catch (e: Exception) {
                "Could not send: is the laptop connected?"
            }
            withContext(Dispatchers.Main) { Toast.makeText(app, result, Toast.LENGTH_LONG).show() }
        }
        finish()
    }

    private fun upload(app: android.content.Context, uris: List<Uri>) {
        val conn = ControlClient({ Secrets.token(app) }).connectBlocking(readTimeoutMs = 120_000)
        conn.socket.use {
            for (uri in uris) {
                val (name, size) = describe(app, uri)
                conn.write(Protocol.fileHeader(name, size))
                app.contentResolver.openInputStream(uri)!!.use { input ->
                    input.copyTo(conn.output, 1 shl 16)
                }
                conn.output.flush()
                val reply = conn.read()
                if (!reply.ok) throw java.io.IOException(reply.message)
            }
        }
    }

    private fun describe(app: android.content.Context, uri: Uri): Pair<String, Long> {
        app.contentResolver.query(uri, null, null, null, null)?.use { c ->
            if (c.moveToFirst()) {
                val name = c.getString(c.getColumnIndexOrThrow(OpenableColumns.DISPLAY_NAME))
                val sizeIdx = c.getColumnIndex(OpenableColumns.SIZE)
                if (sizeIdx >= 0 && !c.isNull(sizeIdx)) return name to c.getLong(sizeIdx)
            }
        }
        // Unknown size: count it first (content streams can be read twice).
        val size = app.contentResolver.openInputStream(uri)!!.use { s ->
            var n = 0L
            val buf = ByteArray(1 shl 16)
            while (true) {
                val r = s.read(buf)
                if (r < 0) break
                n += r
            }
            n
        }
        return (uri.lastPathSegment ?: "file") to size
    }
}

@Suppress("DEPRECATION")
private fun Intent.parcelable(key: String): Uri? =
    if (android.os.Build.VERSION.SDK_INT >= 33) getParcelableExtra(key, Uri::class.java)
    else getParcelableExtra(key)

@Suppress("DEPRECATION")
private fun Intent.parcelableList(key: String): List<Uri> =
    (if (android.os.Build.VERSION.SDK_INT >= 33) getParcelableArrayListExtra(key, Uri::class.java)
    else getParcelableArrayListExtra(key)) ?: emptyList()
