package dev.tablink

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.foundation.background
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

private val Blue = Color(0xFF1E5AA8)

/** One screen: connection status and the two one-tap actions. */
class MainActivity : ComponentActivity() {
    private val client = ControlClient()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent {
            val dark = isSystemInDarkTheme()
            MaterialTheme(
                colorScheme = if (dark) darkColorScheme(primary = Color(0xFF8DB8F2))
                else lightColorScheme(primary = Blue),
            ) {
                Surface(modifier = Modifier.fillMaxSize()) { TabLinkScreen(client) }
            }
        }
    }
}

@Composable
private fun TabLinkScreen(client: ControlClient) {
    val scope = rememberCoroutineScope()
    var connected by remember { mutableStateOf<Boolean?>(null) }
    var busy by remember { mutableStateOf(false) }
    var message by remember { mutableStateOf("") }

    LaunchedEffect(Unit) {
        while (true) {
            if (!busy) connected = client.reachable()
            delay(3_000)
        }
    }

    fun run(type: String, starting: String) {
        if (busy) return
        busy = true
        message = starting
        scope.launch {
            message = try {
                val reply = client.send(type)
                connected = true
                reply.message.ifEmpty { if (reply.ok) "Done." else "That didn't work." }
            } catch (e: Exception) {
                connected = false
                "Can't reach the laptop. Plug in the USB cable and make sure TabLink is " +
                    "running on the laptop."
            }
            busy = false
        }
    }

    Box(Modifier.fillMaxSize().safeDrawingPadding().padding(24.dp), Alignment.Center) {
        Column(
            Modifier.widthIn(max = 520.dp).fillMaxWidth(),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.spacedBy(16.dp),
        ) {
            Text("TabLink", fontSize = 40.sp, fontWeight = FontWeight.Bold)
            StatusLine(connected)
            Spacer(Modifier.height(8.dp))
            Button(
                onClick = { run("remote_ready", "Opening the laptop…") },
                enabled = !busy && connected == true,
                modifier = Modifier.fillMaxWidth().height(96.dp),
            ) {
                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                    Text("Show laptop here", fontSize = 22.sp, fontWeight = FontWeight.SemiBold)
                    Text("Control the laptop with touch, keyboard or mouse", fontSize = 13.sp)
                }
            }
            FilledTonalButton(
                onClick = { run("screen", "Opening this tablet on the laptop…") },
                enabled = !busy && connected == true,
                modifier = Modifier.fillMaxWidth().height(80.dp),
            ) {
                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                    Text("Show tablet on laptop", fontSize = 19.sp, fontWeight = FontWeight.SemiBold)
                    Text("Control this tablet from the laptop", fontSize = 13.sp)
                }
            }
            Box(Modifier.height(64.dp), Alignment.Center) {
                if (busy) {
                    CircularProgressIndicator(Modifier.size(32.dp))
                } else if (message.isNotEmpty()) {
                    Text(message, textAlign = TextAlign.Center, fontSize = 15.sp)
                }
            }
            if (connected == false) {
                TextButton(onClick = { scope.launch { connected = client.reachable() } }) {
                    Text("Try again")
                }
            }
        }
    }
}

@Composable
private fun StatusLine(connected: Boolean?) {
    val (color, text) = when (connected) {
        true -> Color(0xFF2E9D4E) to "Laptop connected · USB"
        false -> Color(0xFFC0392B) to "Laptop not found · plug in the USB cable"
        null -> Color.Gray to "Looking for the laptop…"
    }
    Row(verticalAlignment = Alignment.CenterVertically) {
        Box(Modifier.size(10.dp).clip(CircleShape).background(color))
        Spacer(Modifier.size(8.dp))
        Text(text, fontSize = 16.sp)
    }
}
