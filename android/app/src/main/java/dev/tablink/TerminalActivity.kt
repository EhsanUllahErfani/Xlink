package dev.tablink

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import java.io.OutputStream
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.withContext

/** F6: a shell on the laptop. Run scripts, training jobs or builds and watch them live. */
class TerminalActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent {
            MaterialTheme {
                Surface(Modifier.fillMaxSize(), color = Color(0xFF101418)) { TerminalScreen() }
            }
        }
    }
}

@Composable
private fun TerminalScreen() {
    val context = androidx.compose.ui.platform.LocalContext.current
    val term = remember { TerminalText() }
    var shown by remember { mutableStateOf("Connecting to the laptop…") }
    var input by remember { mutableStateOf("") }
    var out by remember { mutableStateOf<OutputStream?>(null) }
    val history = remember { ArrayList<String>() }
    val scroll = rememberScrollState()

    LaunchedEffect(Unit) {
        withContext(Dispatchers.IO) {
            try {
                val conn = ControlClient({ Secrets.token(context) }).connectBlocking(readTimeoutMs = 0)
                conn.socket.use {
                    conn.write("{\"type\":\"terminal\",\"rows\":40,\"cols\":100}\n")
                    conn.read()
                    out = conn.output
                    val buf = CharArray(8192)
                    while (isActive) {
                        val n = conn.input.read(buf)
                        if (n < 0) break
                        val text = String(buf, 0, n)
                        withContext(Dispatchers.Main) {
                            term.feed(text)
                            shown = term.toString()
                        }
                    }
                }
                withContext(Dispatchers.Main) { shown = term.toString() + "\n[session ended]" }
            } catch (e: ControlClient.NotPaired) {
                withContext(Dispatchers.Main) { shown = e.message ?: "Not paired" }
            } catch (e: Exception) {
                withContext(Dispatchers.Main) {
                    shown = "Can't reach the laptop. Is it on and linked (cable, Wi-Fi or internet)?"
                }
            }
            out = null
        }
    }
    LaunchedEffect(shown) {
        delay(16)
        scroll.scrollTo(scroll.maxValue)
    }

    fun send(bytes: String) {
        val o = out ?: return
        Thread { runCatching { o.write(bytes.toByteArray()); o.flush() } }.start()
    }

    Column(Modifier.fillMaxSize().safeDrawingPadding().imePadding().padding(8.dp)) {
        Text(
            shown,
            color = Color(0xFFD6E2EC),
            fontFamily = FontFamily.Monospace,
            fontSize = 13.sp,
            modifier = Modifier.weight(1f).fillMaxWidth().verticalScroll(scroll),
        )
        Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            OutlinedButton(onClick = { send("\u0003") }) { Text("Ctrl-C") }
            OutlinedButton(onClick = { send("\t") }) { Text("Tab") }
            OutlinedButton(onClick = { history.lastOrNull()?.let { input = it } }) { Text("↑") }
        }
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            OutlinedTextField(
                value = input,
                onValueChange = { input = it },
                modifier = Modifier.weight(1f),
                singleLine = true,
                textStyle = androidx.compose.ui.text.TextStyle(fontFamily = FontFamily.Monospace),
                placeholder = { Text("command, e.g. python3 train.py") },
                keyboardOptions = KeyboardOptions(imeAction = ImeAction.Send),
                keyboardActions = KeyboardActions(onSend = {
                    send(input + "\n"); if (input.isNotBlank()) history.add(input); input = ""
                }),
            )
            Button(onClick = {
                send(input + "\n"); if (input.isNotBlank()) history.add(input); input = ""
            }) { Text("Send") }
        }
    }
}
