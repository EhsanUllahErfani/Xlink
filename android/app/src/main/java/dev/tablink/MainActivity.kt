package dev.tablink

import android.Manifest
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.PowerManager
import android.util.Log
import android.provider.Settings
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
import androidx.compose.ui.platform.LocalContext
import kotlinx.coroutines.delay
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.launch

private val Blue = Color(0xFF1E5AA8)



/** One screen: connection status and the two one-tap actions. */
class MainActivity : ComponentActivity() {
    companion object {
        const val EXTRA_OPEN_LAPTOP = "open_laptop"
    }

    private val client by lazy { ControlClient({ Secrets.token(this) }) }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        Secrets.absorbPendingToken(this)
        if (Build.VERSION.SDK_INT >= 33 &&
            checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) {
            requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 1)
        }
        try {
            LinkService.start(this)
        } catch (e: Exception) {
            // Started again from onResume once in the foreground.
        }
        enableEdgeToEdge()
        openLaptopIfAsked(intent)
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

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        openLaptopIfAsked(intent)
    }

    /** The laptop's "Laptop on tab" button (and `tablink remote`) open the session this way. */
    private fun openLaptopIfAsked(intent: Intent?) {
        if (intent?.getBooleanExtra(EXTRA_OPEN_LAPTOP, false) != true) return
        intent.removeExtra(EXTRA_OPEN_LAPTOP)
        lifecycleScope.launch {
            LaptopConnection.absorbPendingPassword(this@MainActivity)
            val id = LaptopConnection.bookmarkId(this@MainActivity)
            Log.i("TabLink", "open laptop requested; saved connection ${if (id != null) "found" else "missing"}")
            if (id != null) LaptopConnection.open(this@MainActivity, id)
        }
    }

    override fun onResume() {
        super.onResume()
        if (!LinkService.running) LinkService.start(this)
    }

    override fun onWindowFocusChanged(hasFocus: Boolean) {
        super.onWindowFocusChanged(hasFocus)
        // Android lets only the focused app read the clipboard: send it now if it is new.
        if (hasFocus) LinkService.sendClipboard(this)
    }
}

@Composable
private fun TabLinkScreen(client: ControlClient) {
    val scope = rememberCoroutineScope()
    val context = LocalContext.current
    var connected by remember { mutableStateOf<Boolean?>(null) }
    var mode by remember { mutableStateOf("") }
    var busy by remember { mutableStateOf(false) }
    var message by remember { mutableStateOf("") }

    LaunchedEffect(Unit) {
        LaptopConnection.absorbPendingPassword(context)
        while (true) {
            if (!busy) {
                val m = client.linkMode()
                connected = m != null
                mode = m ?: ""
            }
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
                if (type == "remote_ready" && reply.ok) {
                    // The laptop made sure remote desktop and the tunnel are up; open it here.
                    val id = LaptopConnection.bookmarkId(context)
                    if (id == null) {
                        "Not paired yet. On the laptop, run: tablink pair"
                    } else {
                        LaptopConnection.open(context, id)
                        ""
                    }
                } else {
                    reply.message.ifEmpty { if (reply.ok) "Done." else "That didn't work." }
                }
            } catch (e: Exception) {
                connected = false
                "Can't reach the laptop. Plug in the USB cable, or put both devices on the " +
                    "same Wi-Fi, and make sure the laptop is on."
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
            StatusLine(connected, mode)
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
            Checklist()
            if (connected == false) {
                TextButton(onClick = {
                    scope.launch {
                        val m = client.linkMode()
                        connected = m != null
                        mode = m ?: ""
                    }
                }) {
                    Text("Try again")
                }
            }
        }
    }
}

@Composable
private fun StatusLine(connected: Boolean?, mode: String) {
    val (color, text) = when (connected) {
        true -> Color(0xFF2E9D4E) to "Laptop connected · ${Protocol.modeLabel(mode)}"
        false -> Color(0xFFC0392B) to "Laptop not found · plug in USB or join the laptop's Wi-Fi"
        null -> Color.Gray to "Looking for the laptop…"
    }
    Row(verticalAlignment = Alignment.CenterVertically) {
        Box(Modifier.size(10.dp).clip(CircleShape).background(color))
        Spacer(Modifier.size(8.dp))
        Text(text, fontSize = 16.sp)
    }
}


/** HyperOS stops background apps unless allowed; these three switches keep TabLink linked. */
@Composable
private fun Checklist() {
    val context = LocalContext.current
    var tick by remember { mutableStateOf(0) }
    LaunchedEffect(Unit) {
        while (true) {
            delay(2_000)
            tick++
        }
    }
    @Suppress("UNUSED_EXPRESSION") tick
    val pm = context.getSystemService(PowerManager::class.java)
    val battery = pm.isIgnoringBatteryOptimizations(context.packageName)
    val notifAccess = Settings.Secure.getString(
        context.contentResolver, "enabled_notification_listeners",
    )?.contains(context.packageName) == true
    if (battery && notifAccess) return
    Column(
        Modifier.fillMaxWidth().padding(top = 8.dp),
        verticalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        Text("Finish setting up", fontWeight = FontWeight.SemiBold, fontSize = 16.sp)
        if (!battery) {
            TextButton(onClick = { openBatterySettings(context) }) {
                Text("1. Battery: set TabLink to \u201cNo restrictions\u201d")
            }
            TextButton(onClick = { openAutostart(context) }) {
                Text("2. Turn on Autostart for TabLink")
            }
        }
        if (!notifAccess) {
            TextButton(onClick = {
                context.startActivity(Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS))
            }) {
                Text("3. Allow notification access (shows tablet notifications on the laptop)")
            }
        }
    }
}

private fun openBatterySettings(context: Context) {
    val intent = Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS)
        .setData(Uri.parse("package:${context.packageName}"))
    try {
        context.startActivity(intent)
    } catch (e: Exception) {
        context.startActivity(Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS))
    }
}

private fun openAutostart(context: Context) {
    val miui = Intent().setComponent(
        ComponentName(
            "com.miui.securitycenter",
            "com.miui.permcenter.autostart.AutoStartManagementActivity",
        ),
    )
    try {
        context.startActivity(miui)
    } catch (e: Exception) {
        context.startActivity(
            Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS)
                .setData(Uri.parse("package:${context.packageName}")),
        )
    }
}
